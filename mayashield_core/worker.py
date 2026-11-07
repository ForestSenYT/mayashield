# -*- coding: utf-8 -*-
"""Scan worker, run as a plain script by pool.py: one JSON job per stdin line.

Started in isolated mode (``-I``) so a planted module in the working folder or
on PYTHONPATH can't run inside the scanner.  ``maya`` is never imported here.
"""
import json
import os
import sys


def main():
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, package_parent)

    # Anything printed by library code must not corrupt the JSON channel.
    channel = sys.stdout
    sys.stdout = sys.stderr

    from mayashield_core.pool import execute_job

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            result = execute_job(json.loads(line))
        except Exception as error:  # Report the failure, keep serving jobs.
            result = {"worker_error": "%s: %s" % (type(error).__name__, error)}
        channel.write(json.dumps(result, ensure_ascii=True) + "\n")
        channel.flush()


if __name__ == "__main__":
    main()
