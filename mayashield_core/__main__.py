# -*- coding: utf-8 -*-
"""Command line, no Maya needed:  python -m mayashield_core scan <file-or-folder>

Exit code: 0 nothing flagged, 1 threats found, 2 bad usage.
"""
import argparse
import json
import os
import sys

from . import __version__, cleaner, rules, scanner


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mayashield_core", description="MayaShield %s scene scanner" % __version__)
    commands = parser.add_subparsers(dest="command")

    scan = commands.add_parser("scan", help="Scan a scene or a folder of scenes")
    scan.add_argument("path")
    scan.add_argument("--json", help="Write the full report to this file")

    clean = commands.add_parser("clean", help="Remove threats from a scene or a folder of scenes")
    clean.add_argument("path")
    clean.add_argument("--in-place", action="store_true", help="Overwrite, keeping a *.mayashield_bak copy")
    clean.add_argument("--output", help="Output file, or output folder when cleaning a folder")
    clean.add_argument("--threshold", type=int, default=rules.RISK_HIGH, help="Lowest risk score to remove (default 70)")
    clean.add_argument("--dry-run", action="store_true")
    clean.add_argument("--json", help="Write the full report to this file")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    if not os.path.exists(args.path):
        print("No such file or folder: %s" % args.path)
        return 2
    try:
        # A path or node name the console's code page cannot show must not stop the listing.
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    is_folder = os.path.isdir(args.path)
    if args.command == "scan":
        payload = scanner.scan_folder(args.path, report_path=args.json) if is_folder \
            else scanner.scan_file(args.path, report_path=args.json)
    elif is_folder:
        payload = cleaner.clean_folder(args.path, output_root=args.output, in_place=args.in_place,
                                       threshold=args.threshold, dry_run=args.dry_run, report_path=args.json)
    else:
        payload = cleaner.clean_file(args.path, output_path=args.output, in_place=args.in_place,
                                     threshold=args.threshold, dry_run=args.dry_run, report_path=args.json)

    results = payload.get("results") if is_folder else [payload]
    flagged = 0
    for result in results:
        if scanner.is_flagged(result):
            flagged += 1
            print("%s  %s  score=%s  %s" % (result.get("risk", "").upper(), result["path"], result.get("score"),
                                            ", ".join(result.get("families", [])) or ""))
            for hit in (result.get("hits") or [])[:20]:
                print("    %s: %s" % (hit.get("node") or hit.get("kind"), hit.get("reason")))
        if result.get("cleaned"):
            print("    cleaned -> %s" % result.get("output_path"))
    print(json.dumps({"files": len(results), "flagged": flagged}))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
