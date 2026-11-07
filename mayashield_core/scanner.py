# -*- coding: utf-8 -*-
"""Static scene scanning: file -> parser -> analyzer -> scored report.

Scenes are never opened or executed here.  The same assessment code serves .ma,
.mb and (through ``assess_node``) a scene already open in Maya, so every path
applies identical rules.
"""
import datetime
import json
import os
import time

from . import __version__, analyzer, rules
from .ma_parser import line_numbers, parse_ma
from .mb_parser import is_mb_header, parse_mb
from .scene_model import base_name

SCENE_EXTENSIONS = (".ma", ".mb")
_SCRIPT_ATTRS = ("b", "before", "beforeScript", "a", "after", "afterScript")
_EXPRESSION_ATTRS = ("ixp", "exp", "expression", "internalExpression")
_SCRIPT_TYPES = ("script", "scriptNode")
_EXPRESSION_TYPES = ("expression",)
_NETWORK_TYPES = ("network",)
_PY_SOURCE_VALUES = (1,)
_MAX_REPORTED_DETAIL = 260


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_json(path, payload):
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return target


def _script_language(node):
    source = node.numbers.get("stp", node.numbers.get("sourceType"))
    if source is None:
        return None
    return "python" if source in _PY_SOURCE_VALUES else "mel"


def _name_evidence(node):
    """Evidence from a node's name.  Only executable or carrier types count."""
    name = base_name(node.name)
    items = []
    if node.type in _SCRIPT_TYPES or node.type in _EXPRESSION_TYPES:
        for pattern, family in rules.SCRIPT_NODE_NAME_IOCS:
            if pattern.search(name):
                items.append(("name_family", "family", 100, "Script node named '%s'" % name, family))
                break
        else:
            if rules.WEAK_SCRIPT_NAME_RE.search(name) and name.lower() not in rules.OFFICIAL_SCRIPT_NODES:
                items.append(("name_weak", "name", 25, "Script node named '%s'" % name, None))
    elif node.type in _NETWORK_TYPES:
        for pattern, family in rules.NETWORK_NODE_NAME_IOCS:
            if pattern.match(name):
                items.append(("name_family", "family", 85, "Network node named '%s'" % name, family))
                break
    return items


def assess_node(node):
    """Score one NodeRecord.  Returns (analysis, kind, flagged attribute names)."""
    results = []
    flagged = []
    is_script = node.type in _SCRIPT_TYPES
    is_expression = node.type in _EXPRESSION_TYPES

    for rule, category, weight, detail, family in _name_evidence(node):
        results.append({
            "score": weight,
            "evidence": [{"rule": rule, "category": category, "weight": weight, "detail": detail}],
            "families": [family] if family else [],
        })

    for attr, value in node.strings.items():
        if not value:
            continue
        if is_script and attr in _SCRIPT_ATTRS:
            result = analyzer.analyze_text(value, lang=_script_language(node), context=analyzer.CONTEXT_SCRIPT)
        elif is_expression and attr in _EXPRESSION_ATTRS:
            result = analyzer.analyze_text(value, lang="mel", context=analyzer.CONTEXT_SCRIPT)
        else:
            result = analyzer.analyze_attr(value)
        if result["evidence"]:
            results.append(result)
            if result["score"] >= rules.RISK_LOW:
                flagged.append(attr)

    if not results:
        return None, None, []

    combined = analyzer.merge_analyses(results)

    behaviours = [item for item in combined["evidence"] if item["weight"] >= rules.RISK_LOW]
    extra = []
    if is_script and behaviours:
        script_type = node.numbers.get("st", node.numbers.get("scriptType", 0))
        runs_by_itself = script_type in rules.AUTO_EXEC_SCRIPT_TYPES
        if runs_by_itself:
            extra.append({"rule": "auto_exec", "category": "trigger", "weight": 15,
                          "detail": "Runs automatically (scriptType %s)" % script_type})
        if base_name(node.name).lower() in rules.OFFICIAL_SCRIPT_NODES:
            extra.append({"rule": "official_name_abuse", "category": "evasion", "weight": 40,
                          "detail": "Uses the name of a built-in Maya node to look harmless"})
    else:
        runs_by_itself = is_expression  # expressions evaluate without being asked
    if runs_by_itself and any(item["category"] in rules.AUTO_RUN_ESCALATED_CATEGORIES for item in behaviours):
        extra.append({"rule": "auto_run_process", "category": "execution", "weight": 50,
                      "detail": "Starts a process or goes online as soon as the scene opens"})
    if extra:
        combined = analyzer.merge_analyses([combined, {"score": 0, "evidence": extra, "families": []}])

    kind = "scriptAttr" if is_script else ("expression" if is_expression else "node")
    return combined, kind, flagged


def _hit(kind, analysis, node=None, line=None, attrs=None, removable=None):
    top = analysis["evidence"][0] if analysis["evidence"] else {"rule": kind, "detail": ""}
    return {
        "kind": kind,
        "reason": top["rule"],
        "detail": str(top.get("detail", ""))[:_MAX_REPORTED_DETAIL],
        "line": line,
        "confidence": rules.risk_band(analysis["score"]),
        "score": analysis["score"],
        "node": node,
        "attrs": attrs or [],
        "families": analysis.get("families", []),
        "evidence": [
            {"rule": item["rule"], "weight": item["weight"], "detail": str(item.get("detail", ""))[:160]}
            for item in analysis["evidence"][:12]
        ],
        "removable": removable,
    }


_ANOMALY_WEIGHT = {
    "statement": ("ma_unexpected_statement", 45, "Statement Maya never writes in a scene: %s"),
    "inline_code": ("ma_inline_code", 60, "Code embedded in a %s statement"),
    "unusual_file_command": ("ma_unusual_file_command", 45, "file command with unusual flags: %s"),
    "malformed_iff": ("mb_malformed", 20, "Damaged or crafted binary structure: %s"),
    "trailing_data": ("mb_trailing_data", 25, "Unexpected data after the scene: %s"),
}


def _assess_anomaly(anomaly):
    rule, weight, template = _ANOMALY_WEIGHT.get(anomaly.kind, ("anomaly", 30, "%s"))
    label = anomaly.command if anomaly.kind in ("statement", "inline_code", "unusual_file_command") else anomaly.text
    base = {"rule": rule, "category": "structure", "weight": weight, "detail": template % label}
    results = [{"score": weight, "evidence": [base], "families": []}]
    if anomaly.kind in ("statement", "inline_code", "unusual_file_command") and anomaly.text:
        results.append(analyzer.analyze_text(anomaly.text, lang="mel", context=analyzer.CONTEXT_SCRIPT))
    return analyzer.merge_analyses(results)


def build_report(doc):
    """Turn a parsed SceneDoc into hits.  Also returns the removal plan."""
    hits = []
    offsets = []
    plan = {"nodes": [], "attrs": [], "extents": []}

    # In a .mb, an SLCT group may aim at a script node from earlier in the file.
    executable = {}
    for node in doc.nodes:
        if node.type in _SCRIPT_TYPES or node.type in _EXPRESSION_TYPES:
            executable[base_name(node.name)] = node.type
    for node in doc.nodes:
        if node.type == "<selected>" and base_name(node.name) in executable:
            node.type = executable[base_name(node.name)]

    if not doc.parse_ok:
        analysis = {"score": 45, "families": [], "evidence": [
            {"rule": "not_fully_analyzed", "category": "structure", "weight": 45,
             "detail": "Could not be fully analyzed: %s" % doc.parse_note}]}
        hits.append(_hit("structure", analysis))

    for node in doc.nodes:
        analysis, kind, flagged = assess_node(node)
        if analysis is None or analysis["score"] < rules.RISK_LOW:
            continue
        whole = (node.type in _SCRIPT_TYPES or node.type in _EXPRESSION_TYPES
                 or any(item["rule"] == "name_family" for item in analysis["evidence"]))
        hit = _hit(kind, analysis, node=node.name, attrs=flagged, removable="node" if whole else "attr")
        hits.append(hit)
        offsets.append((hit, node.offset))
        plan["nodes" if whole else "attrs"].append((hit, node))

    for anomaly in doc.anomalies:
        analysis = _assess_anomaly(anomaly)
        if analysis["score"] < rules.RISK_LOW:
            continue
        hit = _hit("structure", analysis, removable="statement" if anomaly.extent else None)
        hit["detail"] = str(analysis["evidence"][0].get("detail", ""))[:_MAX_REPORTED_DETAIL]
        hits.append(hit)
        if anomaly.extent:
            offsets.append((hit, anomaly.extent[0]))
            plan["extents"].append((hit, anomaly.extent))

    if doc.type == "ma" and offsets:
        lines = line_numbers(doc.path, [offset for _hit_item, offset in offsets])
        for hit, offset in offsets:
            hit["line"] = lines.get(offset)

    for label, value in doc.info_strings:
        analysis = analyzer.analyze_attr(value)
        if analysis["score"] >= rules.RISK_MEDIUM:
            hits.append(_hit("fileInfo", analysis, node="fileInfo:%s" % label))

    parsed_strings = None
    for raw in doc.raw_hits:
        # Family string in bytes: reported only if nothing structured explains it.
        needle = raw.lower()
        if parsed_strings is None:
            parsed_strings = _collect_parsed_text(hits)
        if needle not in parsed_strings:
            analysis = {"score": 90, "families": [],
                        "evidence": [{"rule": "raw_family_string", "category": "family", "weight": 90,
                                      "detail": "Family marker outside any parsed attribute: %s" % raw[:80]}]}
            hits.append(_hit("raw", analysis))

    hits.sort(key=lambda item: -item["score"])
    return hits, plan


def _collect_parsed_text(hits):
    text = []
    for hit in hits:
        text.append(str(hit.get("node") or ""))
        text.append(str(hit.get("detail") or ""))
        for item in hit.get("evidence", ()):
            text.append(str(item.get("detail", "")))
    return "\n".join(text).lower()


def parse_scene(path):
    """Pick the parser by content, not extension: Maya sniffs the header too."""
    with open(path, "rb") as handle:
        prefix = handle.read(16)
    return parse_mb(path) if is_mb_header(prefix) else parse_ma(path)


def looks_like_scene(path):
    """True for a .ma or .mb, or any file whose content says it's a Maya scene."""
    if path.lower().endswith(SCENE_EXTENSIONS):
        return True
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(32)
    except (IOError, OSError):
        return False
    return is_mb_header(prefix) or prefix.lstrip(b"\xef\xbb\xbf").startswith(b"//Maya ASCII")


def scan_script_file(path):
    """Scan a MEL file about to be imported.  Importing one runs it."""
    report = {"path": os.path.abspath(path), "type": "mel", "hits": [], "score": 0, "risk": "info",
              "families": [], "note": "", "parse_ok": True}
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (IOError, OSError) as error:
        report["parse_ok"] = False
        report["note"] = "Could not read file: %s" % error
        return report
    analysis = analyzer.analyze_text(data.decode("utf-8", "replace"), lang="mel", context=analyzer.CONTEXT_SCRIPT)
    if analysis["score"] >= rules.RISK_LOW:
        report["hits"].append(_hit("script", analysis))
    _summarize(report)
    return report


def scan_file(path, report_path=None, deep=False, timeout_sec=120, mayapy=None):
    """Scan one scene.  Returns the report dict (see the README for the schema)."""
    started = time.time()
    target = os.path.abspath(path)
    report = {
        "path": target, "type": "other", "size": 0, "engine": "static", "version": __version__,
        "hits": [], "score": 0, "risk": "info", "families": [], "note": "", "parse_ok": True,
    }
    if not os.path.isfile(target):
        report["note"] = "File does not exist."
        return report
    report["size"] = os.path.getsize(target)
    report["type"] = os.path.splitext(target)[1].lstrip(".").lower()
    if not looks_like_scene(target):
        report["note"] = "Not a Maya scene (.ma/.mb)."
        return report

    try:
        doc = parse_scene(target)
        report["type"] = doc.type
        report["parse_ok"] = doc.parse_ok
        report["references"] = doc.references[:200]
        report["hits"], _plan = build_report(doc)
        if not doc.parse_ok:
            report["note"] = doc.parse_note
    except Exception as error:  # A crafted file must never take the scanner down.
        report["parse_ok"] = False
        report["hits"].append(_error_hit("scanException", "%s: %s" % (type(error).__name__, error)))
        report["note"] = "Static scan failed."

    needs_deep = deep or not report["parse_ok"]
    if needs_deep and mayapy:
        from . import headless  # Only needed when a real Maya session is wanted.
        deep_report = headless.deep_scan(target, mayapy, timeout_sec)
        report["engine"] = "static+mayapy"
        known = set(hit.get("node") for hit in report["hits"] if hit.get("node"))
        report["hits"].extend(hit for hit in deep_report.get("hits", []) if hit.get("node") not in known or not hit.get("node"))
        report["deep_note"] = deep_report.get("note", "")
    elif deep and not mayapy:
        report["deep_note"] = "Deep scan skipped: mayapy was not found."

    _summarize(report)
    report["elapsed"] = round(time.time() - started, 4)
    if not report["note"]:
        report["note"] = ("Static scan complete." if not report["hits"]
                          else "Flagged: %s risk (score %d)." % (report["risk"], report["score"]))
    if report_path:
        try:
            report["report_path"] = write_json(report_path, report)
        except (IOError, OSError):
            pass
    return report


def _error_hit(reason, detail):
    return {"kind": "error", "reason": reason, "detail": detail[:_MAX_REPORTED_DETAIL], "line": None,
            "confidence": "error", "score": 0, "node": None, "attrs": [], "families": [], "evidence": [],
            "removable": None}


def _summarize(report):
    score = 0
    families = []
    for hit in report["hits"]:
        score = max(score, int(hit.get("score") or 0))
        for family in hit.get("families") or ():
            if family not in families:
                families.append(family)
    report["score"] = score
    report["risk"] = rules.risk_band(score)
    report["families"] = families


def is_flagged(report):
    """Worth acting on: medium risk or above, or the file could not be read."""
    return report.get("score", 0) >= rules.RISK_MEDIUM or any(
        hit.get("confidence") == "error" for hit in report.get("hits") or ())


def summarize_folder(root, results, note=""):
    """Folder payload, keeping the flat keys older report readers rely on."""
    aggregated = []
    for result in results:
        if not is_flagged(result):
            continue
        reasons = []
        worst = "info"
        for hit in result.get("hits") or ():
            label = str(hit.get("reason") or hit.get("kind") or "hit")
            if label not in reasons:
                reasons.append(label)
            level = str(hit.get("confidence") or "info")
            if rules.CONFIDENCE_RANK.get(level, 0) > rules.CONFIDENCE_RANK.get(worst, 0):
                worst = level
        aggregated.append({"path": result.get("path", ""), "reasons": reasons, "confidence": worst,
                           "score": result.get("score", 0), "families": result.get("families", [])})
    payload = {
        "root": root,
        "results": results,
        "summary": {"files": len(results), "flagged": len(aggregated)},
        "files": len(results),
        "files_with_hits": len(aggregated),
        "hits": aggregated,
    }
    if note:
        payload["note"] = note
    return payload


def list_scene_files(root, recursive=True, extensions=SCENE_EXTENSIONS):
    found = []
    wanted = tuple(extension.lower() for extension in extensions)
    for folder, subfolders, names in os.walk(root):
        for name in names:
            # .mayashield_tmp_ files are left behind only by an interrupted clean.
            if name.lower().endswith(wanted) and ".mayashield_tmp_" not in name:
                found.append(os.path.join(folder, name))
        if not recursive:
            subfolders[:] = []
    found.sort()
    return found


def scan_folder(folder, recursive=True, report_path=None, workers=None, timeout_sec=120, deep=False,
                progress_cb=None, cancel_cb=None, interpreter=None, mayapy=None):
    """Scan every scene under a folder, in parallel when more than a few files."""
    from .pool import run_jobs

    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        return dict(summarize_folder(root, []), note="Folder not found.")
    files = list_scene_files(root, recursive)
    jobs = [{"op": "scan", "path": path, "deep": deep, "timeout": timeout_sec, "mayapy": mayapy} for path in files]
    results, canceled = run_jobs(jobs, workers=workers, timeout_sec=None if deep else timeout_sec,
                                 progress_cb=progress_cb, cancel_cb=cancel_cb, interpreter=interpreter)
    payload = summarize_folder(root, results, "Canceled by user." if canceled else "")
    if report_path:
        try:
            payload["report_path"] = write_json(report_path, payload)
        except (IOError, OSError):
            pass
    return payload
