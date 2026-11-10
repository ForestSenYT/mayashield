# -*- coding: utf-8 -*-
"""Optional deep scan: open a scene in an isolated mayapy, with script nodes off.

The static scan needs neither Maya nor a licence and handles normal work.  This
is for a second opinion and costs a Maya start.  The sandbox matters: v3 set
``MAYA_SKIP_USERSETUP``, a variable Maya does not read, and inherited PYTHONPATH,
so a userSetup.py found there still ran inside its "isolated" scanner.
"""
import json
import os
import shutil
import subprocess
import tempfile


def sandbox_environment(app_dir):
    """Environment with no route for user code to get into the scanning process."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "MAYA_", "XBMLANGPATH"))}
    environment.update({
        "MAYA_APP_DIR": app_dir,
        "MAYA_SKIP_USERSETUP_PY": "1",
        "MAYA_DISABLE_CIP": "1",
        "MAYA_DISABLE_CER": "1",
        "MAYA_DISABLE_ADP": "1",
        "MAYA_NO_HOME": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


def _error_report(path, reason, detail):
    return {"path": path, "note": detail, "hits": [{
        "kind": "error", "reason": reason, "detail": str(detail)[:260], "line": None, "confidence": "error",
        "score": 0, "node": None, "attrs": [], "families": [], "evidence": [], "removable": None}]}


def deep_scan(path, mayapy, timeout_sec=120):
    """Run the scan in mayapy and return {hits, note}."""
    target = os.path.abspath(path)
    if not mayapy:
        return _error_report(target, "mayapyNotFound", "Deep scan unavailable: mayapy not found.")
    folder = tempfile.mkdtemp(prefix="mayashield_deep_")
    output = os.path.join(folder, "report.json")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "headless_worker.py")
    app_dir = os.path.join(folder, "appdir")
    os.makedirs(app_dir, exist_ok=True)
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [mayapy, "-I", script, target, output], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=timeout_sec, env=sandbox_environment(app_dir), cwd=folder, creationflags=flags)
        if os.path.isfile(output):
            with open(output, "r", encoding="utf-8") as handle:
                return json.load(handle)
        detail = completed.stderr.decode("utf-8", "replace").strip()[-500:] or "No report was written."
        return _error_report(target, "noReport", "Deep scan failed: %s" % detail)
    except subprocess.TimeoutExpired:
        # Maya is slow to leave.  If it had already written its findings, they count.
        try:
            with open(output, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (IOError, OSError, ValueError):
            return _error_report(target, "timeout", "Deep scan timed out after %ss." % timeout_sec)
    except (OSError, ValueError) as error:
        return _error_report(target, "exception", "Deep scan failed: %s" % error)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
