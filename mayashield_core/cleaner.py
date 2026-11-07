# -*- coding: utf-8 -*-
"""Surgical scene cleaning, without opening the scene in Maya.

Only what the scan attributed to a threat is cut out.  Everything else stays
byte-identical, the result is re-scanned before it replaces anything, and any
file that gets replaced is kept as a backup first.
"""
import datetime
import os
import shutil

from . import rules
from .ma_parser import rewrite_ma
from .mb_parser import rewrite_mb
from .scanner import build_report, parse_scene, write_json, list_scene_files, summarize_folder

BACKUP_SUFFIX = ".mayashield_bak"


def default_output_path(path):
    """A name for the clean copy that never collides with an existing file.

    An earlier clean copy may be where the user has been working since, so it must
    not be overwritten just because the original got cleaned again.
    """
    stem, extension = os.path.splitext(os.path.abspath(path))
    candidate = "%s.cleaned%s" % (stem, extension)
    index = 1
    while os.path.exists(candidate):
        candidate = "%s.cleaned.%d%s" % (stem, index, extension)
        index += 1
    return candidate


def _temporary_path(destination):
    folder, name = os.path.split(os.path.abspath(destination))
    stem, extension = os.path.splitext(name)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(folder, ".%s.mayashield_tmp_%s%s" % (stem, stamp, extension))


def _unique_backup_path(path):
    candidate = path + BACKUP_SUFFIX
    index = 1
    while os.path.exists(candidate):
        candidate = "%s%s%d" % (path, BACKUP_SUFFIX, index)
        index += 1
    return candidate


def _backup(path):
    """Copy a file aside.  Finished copies only: being killed halfway leaves no backup."""
    backup = _unique_backup_path(path)
    partial = backup + ".partial"
    shutil.copy2(path, partial)
    os.replace(partial, backup)
    return backup


def _make_writable(path):
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _attr_ranges(node, attrs):
    ranges = []
    for attr in attrs:
        ranges.extend(node.attr_extents.get(attr, ()))
    return ranges


def _select_targets(plan, threshold):
    nodes = [(hit, node) for hit, node in plan["nodes"] if hit["score"] >= threshold and node.extents]
    # Only attributes that can really be cut: a claim of "removed" must be true.
    attrs = [(hit, node) for hit, node in plan["attrs"]
             if hit["score"] >= threshold and _attr_ranges(node, hit["attrs"])]
    extents = [(hit, extent) for hit, extent in plan["extents"] if hit["score"] >= threshold]
    return nodes, attrs, extents


def _removal_records(nodes, attrs, extents):
    removed = []
    for hit, node in nodes:
        removed.append({"node": node.name, "type": node.type, "line": hit.get("line"), "status": "deleted",
                        "score": hit["score"], "reasons": [item["rule"] for item in hit["evidence"][:5]]})
    for hit, node in attrs:
        removed.append({"node": node.name, "type": node.type, "line": hit.get("line"), "status": "attribute removed",
                        "attrs": hit["attrs"], "score": hit["score"],
                        "reasons": [item["rule"] for item in hit["evidence"][:5]]})
    for hit, _extent in extents:
        removed.append({"node": "<statement>", "type": "statement", "line": hit.get("line"), "status": "deleted",
                        "score": hit["score"], "reasons": [item["rule"] for item in hit["evidence"][:5]]})
    return removed


def clean_file(path, output_path=None, in_place=False, threshold=rules.RISK_HIGH, report_path=None,
               dry_run=False, progress_cb=None):
    """Clean one scene.  ``threshold`` is the lowest score that gets removed."""
    def progress(stage, percent, detail=""):
        if callable(progress_cb):
            try:
                progress_cb(stage, percent, detail)
            except Exception:
                pass

    source = os.path.abspath(path)
    report = {
        "path": source, "type": "other", "hits": [], "removed": [], "cleaned": False, "output_path": "",
        "note": "", "in_place": bool(in_place), "dry_run": bool(dry_run), "primary_engine": "static",
        "remaining_hits_count": 0, "threshold": threshold, "score": 0, "risk": "info", "families": [],
    }

    def finish(note):
        report["note"] = note
        progress("Done", 100, note)
        if report_path:
            try:
                report["report_path"] = write_json(report_path, report)
            except (IOError, OSError):
                pass
        return report

    if not os.path.isfile(source):
        return finish("File does not exist.")
    if not source.lower().endswith((".ma", ".mb")):
        return finish("Not a Maya scene (.ma/.mb).")

    progress("Scanning", 15, os.path.basename(source))
    try:
        doc = parse_scene(source)
        report["type"] = doc.type
        hits, plan = build_report(doc)
    except Exception as error:  # A crafted file must not take the caller down.
        return finish("Not cleaned: the file could not be analyzed (%s: %s)." % (type(error).__name__, error))
    report["hits"] = hits
    report["score"] = max([hit["score"] for hit in hits] or [0])
    report["risk"] = rules.risk_band(report["score"])
    report["families"] = sorted(set(family for hit in hits for family in hit.get("families", ())))

    if not doc.parse_ok:
        report["remaining_hits_count"] = len(hits)
        return finish("Not cleaned: the file structure could not be parsed safely (%s)." % doc.parse_note)

    nodes, attrs, extents = _select_targets(plan, threshold)
    if not nodes and not attrs and not extents:
        unfixable = [hit for hit in hits if hit["score"] >= threshold]
        report["remaining_hits_count"] = len(unfixable)
        if unfixable:
            return finish("Threats were found but none can be removed automatically.")
        return finish("Nothing at or above risk score %d to remove." % threshold)

    removed = _removal_records(nodes, attrs, extents)
    report["removed"] = removed
    if dry_run:
        return finish("Dry run: %d item(s) would be removed." % len(removed))

    if in_place:
        destination = source
    elif output_path and os.path.normcase(os.path.abspath(output_path)) != os.path.normcase(source):
        destination = os.path.abspath(output_path)
    else:
        destination = default_output_path(source)

    temporary = _temporary_path(destination)
    try:
        parent = os.path.dirname(destination)
        if parent:
            os.makedirs(parent, exist_ok=True)

        progress("Writing", 50, os.path.basename(destination))
        if doc.type == "ma":
            ranges = []
            for _hit, node in nodes:
                ranges.extend(node.extents)
            for hit, node in attrs:
                ranges.extend(_attr_ranges(node, hit["attrs"]))
            ranges.extend(extent for _hit, extent in extents)
            report["links_removed"] = rewrite_ma(source, temporary, ranges, [node.name for _hit, node in nodes])
        else:
            drop_attr = []
            for hit, node in attrs:
                drop_attr.extend((node, extent) for extent in _attr_ranges(node, hit["attrs"]))
            report["links_removed"] = rewrite_mb(source, temporary, doc, [node for _hit, node in nodes], drop_attr)

        progress("Verifying", 80, os.path.basename(destination))
        verify_doc = parse_scene(temporary)
        verify_hits, _plan = build_report(verify_doc)
        remaining = [hit for hit in verify_hits if hit["score"] >= threshold]
        report["remaining_hits_count"] = len(remaining)
        report["post_scan"] = {"hits_count": len(verify_hits), "parse_ok": verify_doc.parse_ok}
        if remaining:
            report["remaining_hits"] = remaining[:100]
        if not verify_doc.parse_ok:
            os.remove(temporary)
            report["removed"] = []
            return finish("Clean aborted: the rewritten file failed validation. Original untouched.")

        progress("Replacing", 92, os.path.basename(destination))
        # Mode first, while the source still has it, so a read-only file stays read-only.
        shutil.copymode(source, temporary)
        if os.path.exists(destination):
            backup = _backup(destination)
            report["backup_path" if in_place else "replaced_backup"] = backup
            _make_writable(destination)
        os.replace(temporary, destination)
    except Exception as error:
        try:
            if os.path.exists(temporary):
                _make_writable(temporary)
                os.remove(temporary)
        except OSError:
            pass
        report["removed"] = []
        return finish("Clean failed, original untouched: %s: %s" % (type(error).__name__, error))

    report["cleaned"] = True
    report["output_path"] = destination
    if remaining:
        return finish("Removed %d item(s), but %d threat(s) remain." % (len(removed), len(remaining)))
    return finish("Removed %d item(s). Post-clean scan passed." % len(removed))


def clean_folder(folder, recursive=True, report_path=None, output_root=None, in_place=False,
                 threshold=rules.RISK_HIGH, workers=None, timeout_sec=120, dry_run=False,
                 progress_cb=None, cancel_cb=None, interpreter=None):
    """Clean every scene under a folder.  Output mirrors the folder layout."""
    from .pool import run_jobs

    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        payload = summarize_folder(root, [], "Folder not found.")
        payload["summary"].update({"cleaned": 0, "failed": 0})
        return payload

    out_root = None if in_place else os.path.abspath(output_root or os.path.join(root, "_MAYASHIELD_CLEANED"))
    files = list_scene_files(root, recursive)
    if out_root:
        marker = os.path.normcase(out_root) + os.sep
        files = [path for path in files if not os.path.normcase(path).startswith(marker)]

    jobs = []
    for path in files:
        output = None if in_place else os.path.join(out_root, os.path.relpath(path, root))
        jobs.append({"op": "clean", "path": path, "output": output, "in_place": in_place,
                     "threshold": threshold, "dry_run": dry_run})
    results, canceled = run_jobs(jobs, workers=workers, timeout_sec=timeout_sec, progress_cb=progress_cb,
                                 cancel_cb=cancel_cb, interpreter=interpreter)

    cleaned = sum(1 for result in results if result.get("cleaned"))
    failed = sum(1 for result in results if result.get("remaining_hits_count", 0) > 0
                 or "failed" in str(result.get("note", "")).lower()
                 or "aborted" in str(result.get("note", "")).lower())
    payload = summarize_folder(root, results, "Canceled by user." if canceled else "")
    payload.update({"output_root": out_root or root, "in_place": bool(in_place),
                    "files_cleaned": cleaned, "files_failed": failed})
    payload["summary"].update({"cleaned": cleaned, "failed": failed})
    if report_path:
        try:
            payload["report_path"] = write_json(report_path, payload)
        except (IOError, OSError):
            pass
    return payload
