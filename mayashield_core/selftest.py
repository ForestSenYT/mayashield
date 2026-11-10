# -*- coding: utf-8 -*-
"""Quick end-to-end check of the engine, used by the installer on each Maya's mayapy.

Needs no Maya licence and no ``maya`` import.  The result is one JSON line, written
to the file named by MAYASHIELD_SELFTEST_RESULT (when set) and to standard output.

The sample carries a marker made up for this purpose, not a real virus signature:
a file that looks like a known virus, written to the temp folder during an install,
is exactly what other antivirus products are there to remove.
"""
import json
import os
import sys
import tempfile
import time
import traceback

MARKER = "MAYASHIELD-SELFTEST-4F3A9C2E"
SAMPLE = (
    b'//Maya ASCII 2024 scene\nrequires maya "2024";\n'
    b'createNode transform -n "box";\n'
    b'createNode script -n "selftest_sample";\n'
    b'\tsetAttr ".b" -type "string" "print \\"' + MARKER.encode("ascii") + b'\\";";\n\tsetAttr ".st" 1;\n'
    b'// End of selftest.ma\n'
)
EXPECTED = b'//Maya ASCII 2024 scene\nrequires maya "2024";\ncreateNode transform -n "box";\n// End of selftest.ma\n'
_FOLDER_PREFIX = "mayashield_selftest_"
_STALE_SECONDS = 3600


def _purge_stale():
    """Folders an earlier run could not remove (a scanner had a file open) go now."""
    from . import fileops
    base = tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.startswith(_FOLDER_PREFIX):
            continue
        path = os.path.join(base, name)
        try:
            if time.time() - os.path.getmtime(path) > _STALE_SECONDS:
                fileops.rmtree(path)
        except OSError:
            pass


def run():
    from . import __version__, cleaner, fileops, scanner
    _purge_stale()
    step = "create the sample"
    folder = tempfile.mkdtemp(prefix=_FOLDER_PREFIX)
    try:
        path = os.path.join(folder, "selftest.ma")
        with open(path, "wb") as handle:
            handle.write(SAMPLE)
        step = "scan the sample"
        report = scanner.scan_file(path)
        if report["risk"] != "high":
            raise AssertionError("sample not detected: risk %s, score %s, note %s"
                                 % (report.get("risk"), report.get("score"), report.get("note")))
        step = "clean the sample"
        cleaned = cleaner.clean_file(path)
        if not cleaned.get("cleaned"):
            raise AssertionError("sample not cleaned: %s" % cleaned.get("note"))
        step = "compare the cleaned file"
        with open(cleaned["output_path"], "rb") as handle:
            if handle.read() != EXPECTED:
                raise AssertionError("cleaned output differs from what was expected")
        return {"ok": True, "version": __version__, "python": "%d.%d.%d" % tuple(sys.version_info[:3])}
    except Exception as error:
        return {"ok": False, "step": step, "error": "%s: %s" % (type(error).__name__, error),
                "trace": traceback.format_exc()[-1500:], "python": "%d.%d.%d" % tuple(sys.version_info[:3])}
    finally:
        fileops.rmtree(folder)


def main():
    """Run, report, and return the exit code.  The report is flushed before returning,

    so the caller may leave with os._exit and skip an interpreter shutdown that mayapy
    is known to get wrong.
    """
    try:
        result = run()
    except Exception as error:   # the engine would not even import
        result = {"ok": False, "step": "load the engine", "error": "%s: %s" % (type(error).__name__, error),
                  "trace": traceback.format_exc()[-1500:]}
    line = json.dumps(result)
    target = os.environ.get("MAYASHIELD_SELFTEST_RESULT")
    if target:
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
