# -*- coding: utf-8 -*-
"""Quick end-to-end check of the engine, used by the installer on each Maya's mayapy.

Needs no Maya licence and no ``maya`` import.  Prints one JSON line and exits 0 on
success.  The sample is inert: it only carries the marker the scanner looks for.
"""
import json
import os
import shutil
import sys
import tempfile

SAMPLE = (
    b'//Maya ASCII 2024 scene\nrequires maya "2024";\n'
    b'createNode transform -n "box";\n'
    b'createNode script -n "selftest_sample";\n'
    b'\tsetAttr ".b" -type "string" "import vaccine";\n\tsetAttr ".st" 1;\n\tsetAttr ".stp" 1;\n'
    b'// End of selftest.ma\n'
)
EXPECTED = b'//Maya ASCII 2024 scene\nrequires maya "2024";\ncreateNode transform -n "box";\n// End of selftest.ma\n'


def run():
    from . import __version__, cleaner, scanner
    folder = tempfile.mkdtemp(prefix="mayashield_selftest_")
    try:
        path = os.path.join(folder, "selftest.ma")
        with open(path, "wb") as handle:
            handle.write(SAMPLE)
        report = scanner.scan_file(path)
        if report["risk"] != "high":
            raise AssertionError("sample not detected: %r" % (report,))
        cleaned = cleaner.clean_file(path)
        if not cleaned.get("cleaned"):
            raise AssertionError("sample not cleaned: %s" % cleaned.get("note"))
        with open(cleaned["output_path"], "rb") as handle:
            if handle.read() != EXPECTED:
                raise AssertionError("cleaned output differs from what was expected")
        return {"ok": True, "version": __version__, "python": "%d.%d.%d" % tuple(sys.version_info[:3])}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    try:
        result = run()
    except Exception as error:
        result = {"ok": False, "error": "%s: %s" % (type(error).__name__, error)}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
