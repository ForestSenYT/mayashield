# -*- coding: utf-8 -*-
"""Real-time protection: scenes are checked before Maya opens them.

``kBeforeOpenCheck`` and its siblings run before any of the file is read and can
refuse it, so a script node never gets the chance to run.  v3 tried to turn script
nodes off with ``file -q -executeScriptNodes``, a flag Maya rejects in query mode,
so its pre-open protection never engaged.  A live scan after opening stays as a
second layer for anything that arrives another way.

How loudly it speaks depends on how sure it is.  A dangerous file stops with a
dialog.  A file that is merely unusual opens, with a short note the user can
follow up in the panel: interrupting people over maybes teaches them to click
through the dialogs that matter.

Batch sessions (render farms) are only warned: no dialog, no refused files.
"""
import os
import threading
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.utils

from . import cleaner, live, mayapaths, persistence, rules, scanner
from .i18n import explain_hit, tr

OPT_STARTUP = "mayashield_startupGuardEnabled"
OPT_RUNTIME = "mayashield_runtimeSceneGuardEnabled"
OPT_MAX_MB = "mayashield_guardMaxMB"
DEFAULT_MAX_MB = 4096

_DEBOUNCE_SECONDS = 3.0
_MAX_REVIEWS = 10
_callbacks = []
_verdicts = {}             # (path, size, mtime) -> "allow" | "block"
_reviews = []              # reports of files let through with a note, newest last
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
    """Ask the user.  ``buttons`` are keys such as "block"; the chosen key comes back.

    Separate function so tests can answer the dialog.
    """
    labels = [tr("btn." + key) for key in buttons]
    by_label = dict(zip(labels, buttons))
    chosen = cmds.confirmDialog(title=title, message=message, button=labels, defaultButton=tr("btn." + default),
                                cancelButton=tr("btn." + cancel), dismissString=tr("btn." + cancel))
    return by_label.get(chosen, cancel)


def _why(report):
    """One plain sentence on why a file was flagged."""
    hits = report.get("hits") or [{}]
    return explain_hit(dict(hits[0], families=report.get("families") or hits[0].get("families")))


def _summary(report, limit=6):
    lines = []
    for hit in report.get("hits", [])[:limit]:
        label = hit.get("node") or hit.get("kind") or "-"
        lines.append(tr("guard.line.item", label, explain_hit(hit)))
    extra = len(report.get("hits", [])) - limit
    if extra > 0:
        lines.append(tr("guard.more", extra))
    return "\n".join(lines)


def take_reviews():
    """Reports of files that opened with a note.  Handing them over clears the list."""
    reports = list(_reviews)
    del _reviews[:]
    return reports


def _toast(text):
    try:
        cmds.inViewMessage(assistMessage=text, position="topCenter", fade=True, fadeStayTime=6000)
    except Exception:
        pass   # a hint only: no viewport, no hint


def _note_review(path, report):
    """Unusual but not clearly malicious: let it open and say so without interrupting."""
    name = os.path.basename(path)
    om.MGlobal.displayWarning(tr("guard.review.warn", name, report.get("score", 0)))
    _reviews.append(report)
    del _reviews[:-_MAX_REVIEWS]
    if not _batch():
        safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        maya.utils.executeDeferred(_toast, tr("guard.review.toast", safe))


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
        om.MGlobal.displayWarning(tr("guard.check.failed", error))
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
        om.MGlobal.displayWarning(tr("guard.toolarge", os.path.basename(path)))
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
            _note_review(path, report)
        _verdicts[key] = "allow"
        return True

    if _batch():
        om.MGlobal.displayWarning(tr("guard.batch", tr("guard.batch.unverified" if score < rules.RISK_HIGH
                                                         else "guard.batch.high"), path, score, _summary(report)))
        return True

    if score >= rules.RISK_HIGH:
        can_clean = not is_script and report.get("parse_ok", True)
        message = tr("guard.block.text" if can_clean else "guard.block.text.plain", path, _why(report), _summary(report))
        buttons = ["block", "clean_copy", "open_anyway"] if can_clean else ["block", "open_anyway"]
        choice = _ask(tr("guard.block.title"), message, buttons, "block", "block")
    else:
        message = tr("guard.unverified.text", path, report.get("note") or _summary(report))
        choice = _ask(tr("guard.unverified.title"), message, ["block", "open_anyway"], "block", "block")

    if choice == "open_anyway":
        _verdicts[key] = "allow"
        om.MGlobal.displayWarning(tr("guard.opened", path))
        return True
    if choice == "clean_copy":
        result = cleaner.clean_file(path)
        if result.get("cleaned"):
            target = result["output_path"]
            om.MGlobal.displayWarning(tr("guard.cleancopy", target))
            if operation == "open":
                maya.utils.executeDeferred(_open_scene, target)
        else:
            om.MGlobal.displayError(tr("guard.cleancopy.failed", path, result.get("note")))
    else:
        om.MGlobal.displayWarning(tr("guard.blocked", path))
    return False


def _open_scene(path):
    # Never at the cost of unsaved work: if the open that got refused had already asked
    # about saving, the answer no longer applies to this one.
    if cmds.file(q=True, modified=True):
        om.MGlobal.displayWarning(tr("guard.cleancopy.unsaved", path))
        return
    cmds.file(path, open=True, force=True, prompt=False)


def _after_scene_change(*_args):
    maya.utils.executeDeferred(after_scene_check)


def _show_panel():
    from . import ui
    ui.show_ui(workspace=True, checkup=True)


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

        lines = [tr("guard.line.node", hit["node"], explain_hit(hit)) for hit in scene_hits[:6]]
        lines += [tr("guard.line.job", hit.get("id"), explain_hit(hit)) for hit in job_hits[:6]]
        lines += [tr("guard.line.item", hit["name"], tr("what.running")) for hit in runtime_hits[:6]]
        text = "\n".join(lines)

        if _batch():
            om.MGlobal.displayWarning(tr("guard.runtime.batch", text))
            return

        choice = _ask(tr("guard.runtime.title"), tr("guard.runtime.text", text),
                      ["clean_now", "open_panel", "ignore", "disable"], "clean_now", "ignore")
        if choice == "clean_now":
            jobs = live.clean_jobs()
            nodes = live.clean_scene()
            om.MGlobal.displayWarning(tr("guard.runtime.done",
                                         sum(1 for item in jobs["killed"] if item["status"] == "killed"),
                                         sum(1 for item in nodes["deleted"] if item["status"] == "deleted")))
        elif choice == "open_panel":
            _show_panel()
        elif choice == "disable":
            set_option_bool(OPT_RUNTIME, False)
            om.MGlobal.displayWarning(tr("guard.runtime.off"))
    except Exception as error:
        om.MGlobal.displayWarning(tr("guard.runtime.error", error))


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
            maya.utils.executeDeferred(om.MGlobal.displayWarning, tr("guard.startup.failed", error))
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
        om.MGlobal.displayInfo(tr("guard.startup.review", len(hits) - len(high)))
    if not high:
        return
    lines = [tr("guard.line.item", hit["path"], explain_hit(hit)) for hit in high[:8]]
    if len(high) > 8:
        lines.append(tr("guard.more", len(high) - 8))
    choice = _ask(tr("guard.startup.title"), tr("guard.startup.text", "\n".join(lines)),
                  ["clean_now", "open_panel", "ignore", "disable"], "clean_now", "ignore")
    if choice == "clean_now":
        result = persistence.clean_disk()
        for action in result.get("actions", []):
            om.MGlobal.displayWarning("MayaShield: %s -> %s" % (action["src"], action["status"]))
        om.MGlobal.displayWarning(tr("guard.startup.restart"))
    elif choice == "open_panel":
        _show_panel()
    elif choice == "disable":
        set_option_bool(OPT_STARTUP, False)
        om.MGlobal.displayWarning(tr("guard.startup.off"))
