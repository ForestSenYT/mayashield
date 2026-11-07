# -*- coding: utf-8 -*-
"""Real-time protection: scenes are checked before Maya opens them.

``kBeforeOpenCheck`` and its siblings run before any of the file is read and can
refuse it, so a script node never gets the chance to run.  v3 tried to turn script
nodes off with ``file -q -executeScriptNodes``, a flag Maya rejects in query mode,
so its pre-open protection never engaged.  A live scan after opening stays as a
second layer for anything that arrives another way.

Batch sessions (render farms) are only warned: no dialog, no refused files.
"""
import os
import threading
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.utils

from . import cleaner, live, mayapaths, persistence, rules, scanner

OPT_STARTUP = "mayashield_startupGuardEnabled"
OPT_RUNTIME = "mayashield_runtimeSceneGuardEnabled"
OPT_MAX_MB = "mayashield_guardMaxMB"
DEFAULT_MAX_MB = 4096

_DEBOUNCE_SECONDS = 3.0
_callbacks = []
_verdicts = {}             # (path, size, mtime) -> "allow" | "block"
_last_scene = ("", 0.0)
_startup_checked = False


def option_bool(name, default=True):
    try:
        if cmds.optionVar(exists=name):
            return bool(cmds.optionVar(q=name))
    except RuntimeError:
        pass
    return bool(default)


def set_option_bool(name, value):
    cmds.optionVar(iv=(name, 1 if value else 0))


def _max_bytes():
    try:
        if cmds.optionVar(exists=OPT_MAX_MB):
            return int(cmds.optionVar(q=OPT_MAX_MB)) * 1024 * 1024
    except (RuntimeError, ValueError):
        pass
    return DEFAULT_MAX_MB * 1024 * 1024


def _batch():
    try:
        return bool(cmds.about(batch=True))
    except RuntimeError:
        return True


def _ask(title, message, buttons, default, cancel):
    """Separate function so tests can answer the dialog."""
    return cmds.confirmDialog(title=title, message=message, button=buttons, defaultButton=default,
                              cancelButton=cancel, dismissString=cancel)


def _summary(report, limit=6):
    lines = []
    for hit in report.get("hits", [])[:limit]:
        label = hit.get("node") or hit.get("detail") or hit.get("reason")
        lines.append("- %s  (%s, score %s)" % (label, hit.get("reason"), hit.get("score")))
    extra = len(report.get("hits", [])) - limit
    if extra > 0:
        lines.append("...and %d more." % extra)
    return "\n".join(lines)


def _file_path(file_object):
    path = file_object.resolvedFullName() or file_object.rawFullName()
    return path.split("{", 1)[0] if path.endswith("}") else path


_OPERATIONS = {
    "kBeforeOpenCheck": "open", "kBeforeImportCheck": "import",
    "kBeforeCreateReferenceCheck": "reference", "kBeforeLoadReferenceCheck": "reference",
}


def _check_file(file_object, message_name):
    """Returns False to make Maya refuse the file.  Must never raise."""
    try:
        return _decide(_file_path(file_object), _OPERATIONS.get(message_name, "open"))
    except Exception as error:
        om.MGlobal.displayWarning("MayaShield: pre-open check failed, allowing the file: %s" % error)
        return True


def _decide(path, operation):
    if not option_bool(OPT_RUNTIME) or not os.path.isfile(path):
        return True
    # By content as well as name: Maya will open a renamed scene if told its type.
    # Importing a .mel file runs it.  Anything else (.fbx, .obj...) isn't ours to judge.
    is_script = path.lower().endswith(".mel")
    if not is_script and not scanner.looks_like_scene(path):
        return True
    stat = os.stat(path)
    if stat.st_size > _max_bytes():
        om.MGlobal.displayWarning("MayaShield: %s is above the pre-open scan limit; only the after-open "
                                  "check will run." % os.path.basename(path))
        return True
    key = (os.path.normcase(path), stat.st_size, stat.st_mtime)
    if key in _verdicts:
        return _verdicts[key] == "allow"

    report = scanner.scan_script_file(path) if is_script else scanner.scan_file(path)
    score = report.get("score", 0)
    # A file that defeats the scanner has not been shown to be safe.
    unverified = not report.get("parse_ok", True) or any(
        hit.get("confidence") == "error" for hit in report.get("hits", ()))
    if score < rules.RISK_HIGH and not unverified:
        if score >= rules.RISK_MEDIUM:
            om.MGlobal.displayWarning("MayaShield: review %s (risk score %d). Open the MayaShield panel for details."
                                      % (os.path.basename(path), score))
        _verdicts[key] = "allow"
        return True

    if _batch():
        om.MGlobal.displayWarning("MayaShield: %s scene %s (score %d). Batch mode does not block it.\n%s"
                                  % ("UNVERIFIED" if score < rules.RISK_HIGH else "HIGH RISK", path, score,
                                     _summary(report)))
        return True

    if score >= rules.RISK_HIGH:
        families = ", ".join(report.get("families", [])) or "unknown family"
        message = ("MayaShield stopped this file before it could run anything.\n\n%s\nRisk score: %d (%s)\n\n%s\n\n"
                   % (path, score, families, _summary(report)))
        buttons = ["Block", "Clean Copy", "Open Anyway"] if not is_script and report.get("parse_ok", True) \
            else ["Block", "Open Anyway"]
        choice = _ask("MayaShield: dangerous file", message, buttons, "Block", "Block")
    else:
        message = ("MayaShield could not fully analyze this file, so it can't say the file is safe. "
                   "It may be damaged, or built to confuse scanners.\n\n%s\n\n%s\n\n"
                   % (path, report.get("note") or _summary(report)))
        choice = _ask("MayaShield: file not verified", message, ["Block", "Open Anyway"], "Block", "Block")

    if choice == "Open Anyway":
        _verdicts[key] = "allow"
        om.MGlobal.displayWarning("MayaShield: %s opened at your request, despite the warning." % path)
        return True
    if choice == "Clean Copy":
        result = cleaner.clean_file(path)
        if result.get("cleaned"):
            target = result["output_path"]
            om.MGlobal.displayWarning("MayaShield: clean copy written to %s" % target)
            if operation == "open":
                maya.utils.executeDeferred(_open_scene, target)
        else:
            om.MGlobal.displayError("MayaShield: could not clean %s: %s" % (path, result.get("note")))
    else:
        om.MGlobal.displayWarning("MayaShield blocked %s." % path)
    return False


def _open_scene(path):
    # Never at the cost of unsaved work: if the open that got refused had already asked
    # about saving, the answer no longer applies to this one.
    if cmds.file(q=True, modified=True):
        om.MGlobal.displayWarning("MayaShield: this scene has unsaved changes, so the clean copy "
                                  "was not opened for you: %s" % path)
        return
    cmds.file(path, open=True, force=True, prompt=False)


def _after_scene_change(*_args):
    maya.utils.executeDeferred(after_scene_check)


def after_scene_check():
    """Second layer: look at what is actually in the session now."""
    global _last_scene
    if not option_bool(OPT_RUNTIME):
        return
    try:
        scene = cmds.file(q=True, sceneName=True) or "<untitled>"
        now = time.time()
        if scene == _last_scene[0] and now - _last_scene[1] < _DEBOUNCE_SECONDS:
            return
        _last_scene = (scene, now)

        scene_hits = [hit for hit in live.scene_hits() if hit["score"] >= rules.RISK_HIGH]
        job_hits = live.job_hits()
        runtime_hits = live.runtime_hits()
        if not scene_hits and not job_hits and not runtime_hits:
            return

        lines = ["- Node: %s (%s)" % (hit["node"], hit["reason"]) for hit in scene_hits[:6]]
        lines += ["- Job #%s: %s" % (hit.get("id"), str(hit.get("desc"))[:100]) for hit in job_hits[:6]]
        lines += ["- %s: %s" % (hit["name"], hit["reason"]) for hit in runtime_hits[:6]]
        text = "\n".join(lines)

        if _batch():
            om.MGlobal.displayWarning("MayaShield: suspicious content in this session:\n%s" % text)
            return

        choice = _ask("MayaShield Runtime Guard",
                      "Suspicious content is active in this session:\n\n%s\n\nClean it now?" % text,
                      ["Terminate + Clean", "Open Panel", "Ignore Once", "Disable Runtime Guard"],
                      "Terminate + Clean", "Ignore Once")
        if choice == "Terminate + Clean":
            jobs = live.clean_jobs()
            nodes = live.clean_scene()
            om.MGlobal.displayWarning("MayaShield: killed %d job(s), deleted %d node(s)." % (
                sum(1 for item in jobs["killed"] if item["status"] == "killed"),
                sum(1 for item in nodes["deleted"] if item["status"] == "deleted")))
        elif choice == "Open Panel":
            from . import ui
            ui.show_ui(workspace=True)
        elif choice == "Disable Runtime Guard":
            set_option_bool(OPT_RUNTIME, False)
            om.MGlobal.displayWarning("MayaShield: Runtime Guard disabled (MayaShield > Preferences).")
    except Exception as error:
        om.MGlobal.displayWarning("MayaShield Runtime Guard error: %s" % error)


def install():
    if _callbacks:
        return
    for name in _OPERATIONS:
        message = getattr(om.MSceneMessage, name, None)
        if message is not None:
            _callbacks.append(om.MSceneMessage.addCheckFileCallback(message, _check_file, name))
    for name in ("kAfterOpen", "kAfterImport", "kAfterCreateReference", "kAfterLoadReference"):
        message = getattr(om.MSceneMessage, name, None)
        if message is not None:
            _callbacks.append(om.MSceneMessage.addCallback(message, _after_scene_change))


def uninstall():
    global _callbacks
    for callback in _callbacks:
        try:
            om.MMessage.removeCallback(callback)
        except RuntimeError:
            pass
    _callbacks = []


# ---------------------------------------------------------------------------
# Startup: scan on a background thread so Maya keeps loading.
# ---------------------------------------------------------------------------
def startup_check():
    global _startup_checked
    if _startup_checked or _batch() or not option_bool(OPT_STARTUP):
        return
    _startup_checked = True
    env = mayapaths.snapshot()

    def work():
        try:
            report = persistence.scan_disk(env=env, scan_all=False)
        except Exception as error:
            maya.utils.executeDeferred(om.MGlobal.displayWarning, "MayaShield startup scan failed: %s" % error)
            return
        maya.utils.executeDeferred(_report_startup, report)

    thread = threading.Thread(target=work, name="MayaShieldStartupScan")
    thread.daemon = True
    thread.start()


def _report_startup(report):
    hits = report.get("hits", [])
    high = [hit for hit in hits if hit["score"] >= rules.RISK_HIGH]
    for note in report.get("notes", []):
        if "immunize block" in note:
            om.MGlobal.displayWarning("MayaShield: %s" % note)
    if len(hits) > len(high):
        om.MGlobal.displayInfo("MayaShield: %d startup file(s) worth reviewing. See the MayaShield panel."
                               % (len(hits) - len(high)))
    if not high:
        return
    lines = ["- %s (%s)" % (hit["path"], ", ".join(hit["reasons"][:3])) for hit in high[:8]]
    if len(high) > 8:
        lines.append("...and %d more." % (len(high) - 8))
    choice = _ask("MayaShield Warning",
                  "Malware indicators in your Maya startup files:\n\n%s\n\n"
                  "Clean Now repairs or quarantines them. Originals are kept and can be restored."
                  % "\n".join(lines),
                  ["Clean Now", "Open Panel", "Ignore Once", "Disable Guard"], "Clean Now", "Ignore Once")
    if choice == "Clean Now":
        result = persistence.clean_disk()
        for action in result.get("actions", []):
            om.MGlobal.displayWarning("MayaShield: %s -> %s" % (action["src"], action["status"]))
        om.MGlobal.displayWarning("MayaShield: restart Maya so nothing already loaded from these files stays active.")
    elif choice == "Open Panel":
        from . import ui
        ui.show_ui(workspace=True)
    elif choice == "Disable Guard":
        set_option_bool(OPT_STARTUP, False)
        om.MGlobal.displayWarning("MayaShield: Startup Guard disabled (MayaShield > Preferences).")
