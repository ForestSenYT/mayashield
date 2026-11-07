# -*- coding: utf-8 -*-
"""Runs inside an isolated mayapy, started by headless.py.

Script nodes are disabled and references left unloaded, so opening the scene for
inspection doesn't run its contents or pull in other files.
"""
import json
import os
import sys
import traceback


def main():
    scene, output = sys.argv[1], sys.argv[2]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    report = {"path": scene, "hits": [], "note": ""}
    try:
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds

        cmds.file(scene, open=True, force=True, prompt=False, ignoreVersion=True,
                  executeScriptNodes=False, loadReferenceDepth="none")

        from mayashield_core import live
        report["hits"].extend(live.scene_hits())
        report["hits"].extend(live.job_hits())
        report["note"] = "Deep scan complete."
    except Exception:
        report["hits"].append({
            "kind": "error", "reason": "deepScanException", "detail": traceback.format_exc()[-500:],
            "line": None, "confidence": "error", "score": 0, "node": None, "attrs": [], "families": [],
            "evidence": [], "removable": None})
        report["note"] = "Deep scan failed."
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False)
    # Leaving without this crashes Maya's static destructors, which then writes a
    # recovery scene to the temp folder.
    try:
        maya.standalone.uninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    main()
