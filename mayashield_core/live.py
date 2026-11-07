# -*- coding: utf-8 -*-
"""The scene currently open in Maya, using the same assessment as files on disk."""
import re
import sys

import maya.cmds as cmds

from . import analyzer, persistence, rules
from .scanner import _hit, assess_node, now
from .scene_model import NodeRecord

_CARRIER_TYPES = ("script", "expression", "network", "unknown", "unknownDag")
_SCRIPT_ATTRS = ("before", "after")
_RUNTIME_MODULES = ("vaccine", "maya_secure_system")
_RUNTIME_GLOBALS = ("leukocyte",)


def _get_string(node, attr):
    try:
        if cmds.attributeQuery(attr, node=node, exists=True):
            value = cmds.getAttr("%s.%s" % (node, attr))
            return value if isinstance(value, str) else None
    except (RuntimeError, ValueError, TypeError):
        pass
    return None


def _get_number(node, attr):
    try:
        if cmds.attributeQuery(attr, node=node, exists=True):
            return int(cmds.getAttr("%s.%s" % (node, attr)))
    except (RuntimeError, ValueError, TypeError):
        pass
    return None


def _record(node, node_type):
    record = NodeRecord(node_type, node)
    if node_type == "script":
        for attr in _SCRIPT_ATTRS:
            value = _get_string(node, attr)
            if value:
                record.strings[attr] = value
        for attr, short in (("scriptType", "st"), ("sourceType", "stp")):
            number = _get_number(node, attr)
            if number is not None:
                record.numbers[short] = number
    elif node_type == "expression":
        value = _get_string(node, "expression")
        if value:
            record.strings["expression"] = value
    try:
        for attr in cmds.listAttr(node, userDefined=True) or ():
            if cmds.getAttr("%s.%s" % (node, attr), type=True) == "string":
                value = _get_string(node, attr)
                if value:
                    record.strings[attr] = value
    except (RuntimeError, ValueError, TypeError):
        pass
    return record


def scene_hits():
    """Assess script, expression, network and unknown nodes in the open scene."""
    hits = []
    for node_type in _CARRIER_TYPES:
        for node in cmds.ls(type=node_type) or ():
            # `ls -type` matches subclasses: report each node under its real type.
            try:
                actual = cmds.nodeType(node)
            except RuntimeError:
                actual = node_type
            if actual != node_type:
                continue
            analysis, kind, flagged = assess_node(_record(node, node_type))
            if analysis is None or analysis["score"] < rules.RISK_LOW:
                continue
            whole = node_type in ("script", "expression") or any(
                item["rule"] == "name_family" for item in analysis["evidence"])
            hit = _hit(kind, analysis, node=node, attrs=flagged, removable="node" if whole else "attr")
            hit["type"] = node_type
            hits.append(hit)
    hits.sort(key=lambda item: -item["score"])
    return hits


def scan_scene(verbose=False):
    report = {"time": now(), "sceneHits": scene_hits(), "notes": []}
    if verbose:
        report["notes"].append("Checked node types: %s" % ", ".join(_CARRIER_TYPES))
    return report


def _unlock(node):
    try:
        cmds.lockNode(node, lock=False)
    except RuntimeError:
        pass


def clean_scene(dry_run=False, verbose=False, threshold=rules.RISK_HIGH):
    """Delete high-risk nodes.  Attributes on other nodes are removed one by one."""
    report = scan_scene(verbose=verbose)
    deleted = []
    for hit in report["sceneHits"]:
        if hit["score"] < threshold:
            continue
        node = hit["node"]
        if not node or not cmds.objExists(node):
            continue
        if dry_run:
            deleted.append({"node": node, "status": "would_delete"})
            continue
        try:
            _unlock(node)
            if hit.get("removable") == "node":
                cmds.delete(node)
            else:
                for attr in hit.get("attrs", ()):
                    plug = "%s.%s" % (node, attr)
                    cmds.setAttr(plug, lock=False)
                    if cmds.attributeQuery(attr, node=node, exists=True) and attr in (
                            cmds.listAttr(node, userDefined=True) or ()):
                        cmds.deleteAttr(plug)
                    else:
                        cmds.setAttr(plug, "", type="string")
            deleted.append({"node": node, "status": "deleted"})
        except (RuntimeError, ValueError) as error:
            deleted.append({"node": node, "status": "failed: %s" % error})
    report["deleted"] = deleted
    report["dryRun"] = bool(dry_run)
    return report


def list_script_jobs():
    try:
        return cmds.scriptJob(listJobs=True) or []
    except RuntimeError:
        return []


def _job_entries():
    for line in list_script_jobs():
        match = re.match(r"^\s*(\d+)\s*:\s*(.*)$", line)
        yield (int(match.group(1)) if match else None), (match.group(2) if match else line)


def job_hits():
    hits = []
    for job_id, description in _job_entries():
        analysis = analyzer.analyze_text(description, context=analyzer.CONTEXT_RUNTIME)
        if analysis["score"] >= rules.RISK_MEDIUM:
            hit = _hit("job", analysis, removable="job")
            hit["id"] = job_id
            hit["desc"] = description
            hits.append(hit)
    return hits


def runtime_hits():
    """Malware already running: family modules imported into this session."""
    hits = []
    for name in _RUNTIME_MODULES:
        if name in sys.modules:
            hits.append({"kind": "module", "name": name, "reason": "family module loaded in this session",
                         "score": 100, "confidence": "high"})
    main = sys.modules.get("__main__")
    for name in _RUNTIME_GLOBALS:
        if main is not None and hasattr(main, name):
            hits.append({"kind": "global", "name": name, "reason": "family object in __main__",
                         "score": 100, "confidence": "high"})
    return hits


def scan_jobs(verbose=False):
    return {"time": now(), "jobHits": job_hits(), "runtimeHits": runtime_hits(), "notes": []}


def clean_jobs(dry_run=False, verbose=False):
    report = scan_jobs(verbose=verbose)
    killed = []
    for hit in report["jobHits"]:
        job_id = hit.get("id")
        if job_id is None:
            continue
        if dry_run:
            killed.append({"id": job_id, "status": "would_kill"})
            continue
        try:
            cmds.scriptJob(kill=job_id, force=True)
            killed.append({"id": job_id, "status": "killed"})
        except (RuntimeError, ValueError) as error:
            killed.append({"id": job_id, "status": "failed: %s" % error})
    report["killed"] = killed

    unloaded = []
    for hit in report["runtimeHits"]:
        if dry_run:
            unloaded.append({"name": hit["name"], "status": "would_remove"})
        elif hit["kind"] == "module":
            sys.modules.pop(hit["name"], None)
            unloaded.append({"name": hit["name"], "status": "removed from sys.modules"})
        else:
            main = sys.modules.get("__main__")
            if main is not None and hasattr(main, hit["name"]):
                delattr(main, hit["name"])
            unloaded.append({"name": hit["name"], "status": "removed from __main__"})
    report["unloaded"] = unloaded
    if report["runtimeHits"] and not dry_run:
        report["notes"].append("Malware code was running in this session. Restart Maya after cleaning.")
    report["dryRun"] = bool(dry_run)
    return report


def immunize():
    """Write the startup guard block to this version's userSetup.py."""
    script_dir = cmds.internalVar(userScriptDir=True)
    if not script_dir:
        return {"time": now(), "status": "failed", "reason": "userScriptDir unavailable"}
    result = persistence.write_guard_block(script_dir)
    result["time"] = now()
    return result
