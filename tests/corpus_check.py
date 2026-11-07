# -*- coding: utf-8 -*-
"""False-positive and speed check over real scenes.  Only reads the files.

    python tests/corpus_check.py <folder> [<folder>...] [--json out.json]

Every scene shipped with Maya is clean, so anything flagged here is a false
positive.  Also runs v3.1's text scanner on the same files for comparison.
"""
import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mayashield_core import rules, scanner  # noqa: E402


def load_legacy_scanner():
    """Import v3.1.0 with a stub for maya, to compare its .ma verdicts."""
    fake = types.ModuleType("maya")
    for name in ("cmds", "mel", "OpenMaya", "OpenMayaMPx"):
        module = types.ModuleType("maya." + name)
        setattr(fake, name, module)
        sys.modules["maya." + name] = module
    sys.modules["maya.OpenMayaMPx"].MPxCommand = object
    sys.modules["maya"] = fake
    path = os.path.join(ROOT, "archive", "mayashield_3.1.0.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    namespace = {"__name__": "mayashield_legacy"}
    exec(compile(source, path, "exec"), namespace)
    return namespace


def main():
    arguments = sys.argv[1:]
    out_path = None
    if "--json" in arguments:
        index = arguments.index("--json")
        out_path = arguments[index + 1]
        del arguments[index:index + 2]

    files = []
    for root in arguments:
        if os.path.isfile(root):
            files.append(root)
        else:
            files.extend(scanner.list_scene_files(root))

    legacy = load_legacy_scanner()
    total_bytes = sum(os.path.getsize(path) for path in files)
    started = time.time()
    results = [scanner.scan_file(path) for path in files]
    elapsed = time.time() - started

    flagged = [result for result in results if scanner.is_flagged(result)]
    low = [result for result in results if result["hits"] and not scanner.is_flagged(result)]
    broken = [result for result in results if not result["parse_ok"]]

    legacy_started = time.time()
    legacy_flagged = legacy_high = legacy_ma = 0
    for path in files:
        if path.lower().endswith(".ma"):
            legacy_ma += 1
            report = legacy["scan_scene_file"](path)
            hits = report.get("hits") or []
            if hits:
                legacy_flagged += 1
            if any(hit.get("confidence") == "high" for hit in hits):
                legacy_high += 1
    legacy_elapsed = time.time() - legacy_started

    print("files: %d  size: %.1f MB" % (len(files), total_bytes / 1048576.0))
    print("v4: %.2f s (%.0f MB/s)  flagged(>=medium): %d  low-only: %d  parse failures: %d" % (
        elapsed, total_bytes / 1048576.0 / max(elapsed, 1e-6), len(flagged), len(low), len(broken)))
    print("v3 on the %d .ma files: %.2f s  flagged: %d  'high': %d" % (
        legacy_ma, legacy_elapsed, legacy_flagged, legacy_high))
    for result in sorted(flagged, key=lambda r: -r["score"])[:40]:
        top = result["hits"][0]
        print("  FLAGGED %3d %s\n      node=%s reason=%s | %s" % (
            result["score"], result["path"], top.get("node"), top.get("reason"),
            [(e["rule"], e["weight"]) for e in top.get("evidence", [])][:5]))
    for result in low[:25]:
        top = result["hits"][0]
        print("  low %3d %s node=%s %s" % (
            result["score"], os.path.basename(result["path"]), top.get("node"),
            [(e["rule"], e["weight"]) for e in top.get("evidence", [])][:3]))
    for result in broken[:15]:
        print("  BROKEN %s: %s" % (result["path"], result["note"]))
    slowest = sorted(results, key=lambda r: -r.get("elapsed", 0))[:3]
    for result in slowest:
        print("  slowest %.3fs %.1f MB %s" % (
            result.get("elapsed", 0), result.get("size", 0) / 1048576.0, os.path.basename(result["path"])))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
