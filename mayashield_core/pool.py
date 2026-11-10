# -*- coding: utf-8 -*-
"""Runs scan and clean jobs across a pool of worker processes.

Worker processes give real parallelism on large folders, keep Maya's UI thread
free, and survive a crafted file: a crash or hang costs one job, not the session.
Falls back to running in-process if no interpreter can be started.
"""
import json
import os
import queue
import subprocess
import sys
import threading

IN_PROCESS_LIMIT = 2
MAX_WORKERS = 8
_POLL_SECONDS = 0.2
_HELLO_SECONDS = 30.0


def execute_job(job):
    """Run one job in this process."""
    if job.get("op") == "ping":
        return {"pong": True}
    if job.get("op") == "clean":
        from .cleaner import clean_file
        kwargs = {"output_path": job.get("output"), "in_place": bool(job.get("in_place")),
                  "dry_run": bool(job.get("dry_run"))}
        if job.get("threshold") is not None:
            kwargs["threshold"] = job["threshold"]
        return clean_file(job["path"], **kwargs)
    from .scanner import scan_file
    return scan_file(job["path"], deep=bool(job.get("deep")), timeout_sec=job.get("timeout") or 120,
                     mayapy=job.get("mayapy"))


def _failed_result(job, message):
    return {
        "path": job.get("path", ""), "type": "other", "score": 0, "risk": "error", "families": [],
        "cleaned": False, "removed": [], "note": message, "parse_ok": False,
        "hits": [{"kind": "error", "reason": "workerFailure", "detail": message[:260], "line": None,
                  "confidence": "error", "score": 0, "node": None, "attrs": [], "families": [],
                  "evidence": [], "removable": None}],
    }


def default_interpreter():
    """Interpreter for workers: this Python if that is what is running, else mayapy.

    Inside Maya sys.executable is Maya itself (maya.exe, mayabatch.exe, "Maya" on
    macOS).  Starting that as a worker would open eight more Mayas, none of which
    speaks the job protocol.
    """
    executable = os.path.basename(sys.executable or "").lower()
    if executable.startswith(("python", "mayapy")):
        return sys.executable
    from .mayapaths import find_mayapy
    return find_mayapy()


def worker_environment():
    """Environment for workers, without anything that could inject code."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "MAYA_"))}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


class _Worker(object):
    def __init__(self, interpreter):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            [interpreter, "-I", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=worker_environment(), creationflags=flags)
        self.responses = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(line)
        except (OSError, ValueError):
            pass
        self.responses.put(None)

    def send(self, job):
        self.process.stdin.write((json.dumps(job) + "\n").encode("ascii"))
        self.process.stdin.flush()

    def answers(self):
        """Does this interpreter start, find the engine and speak the protocol at all?

        Asked before the first real job.  A worker that dies on a scene is that
        scene's doing; one that cannot even say hello is a broken interpreter, and
        blaming every file in the folder for it would report a clean folder as
        full of threats.
        """
        try:
            self.send({"op": "ping"})
            line = self.responses.get(timeout=_HELLO_SECONDS)
            return line is not None and bool(json.loads(line.decode("ascii")).get("pong"))
        except (OSError, ValueError, UnicodeDecodeError, queue.Empty):
            return False

    def stop(self):
        # A worker in the middle of a deep scan has a mayapy of its own running: take
        # the whole tree, or that Maya outlives the scan it was started for.
        if os.name == "nt" and self.process.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=15, creationflags=0x08000000)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            self.process.kill()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _run_in_process(jobs, progress_cb, cancel_cb):
    results = []
    canceled = False
    for index, job in enumerate(jobs, 1):
        if callable(cancel_cb) and cancel_cb():
            canceled = True
            break
        _notify(progress_cb, job.get("path", ""), index, len(jobs))
        try:
            results.append(execute_job(job))
        except Exception as error:
            results.append(_failed_result(job, "%s: %s" % (type(error).__name__, error)))
    return results, canceled


def _notify(progress_cb, path, index, total):
    if callable(progress_cb):
        try:
            progress_cb(path, index, total)
        except Exception:
            pass


def run_jobs(jobs, workers=None, timeout_sec=None, progress_cb=None, cancel_cb=None, interpreter=None):
    """Run jobs and return (results in job order, canceled flag)."""
    if not jobs:
        return [], False
    if len(jobs) <= IN_PROCESS_LIMIT:
        return _run_in_process(jobs, progress_cb, cancel_cb)

    interpreter = interpreter or default_interpreter()
    if not interpreter:
        return _run_in_process(jobs, progress_cb, cancel_cb)

    count = workers or min(MAX_WORKERS, max(1, (os.cpu_count() or 2) - 1))
    count = max(1, min(count, len(jobs)))

    pending = queue.Queue()
    for index, job in enumerate(jobs):
        pending.put((index, job))
    results = [None] * len(jobs)
    lock = threading.Lock()
    state = {"done": 0, "canceled": False, "unusable": False}

    def canceled():
        if state["canceled"]:
            return True
        if callable(cancel_cb):
            try:
                if cancel_cb():
                    state["canceled"] = True
                    return True
            except Exception:
                pass
        return False

    def finish(index, job, result):
        with lock:
            results[index] = result
            state["done"] += 1
            done = state["done"]
        _notify(progress_cb, job.get("path", ""), done, len(jobs))

    def drive():
        worker = None
        try:
            while not canceled():
                try:
                    index, job = pending.get_nowait()
                except queue.Empty:
                    return
                if state["unusable"]:
                    finish(index, job, execute_job_safely(job, "the worker interpreter does not start"))
                    continue
                if worker is None or worker.process.poll() is not None:
                    try:
                        worker = _Worker(interpreter)
                        if not worker.answers():
                            raise OSError("no answer from %s" % interpreter)
                    except (OSError, ValueError) as error:
                        # Not this file's fault: do the work here, and stop trying to start workers.
                        if worker is not None:
                            worker.stop()
                        worker = None
                        state["unusable"] = True
                        finish(index, job, execute_job_safely(job, error))
                        continue
                try:
                    worker.send(job)
                except (OSError, ValueError) as error:
                    worker.stop()
                    worker = None
                    finish(index, job, _failed_result(job, "Worker stopped accepting jobs: %s" % error))
                    continue

                waited = 0.0
                response = "pending"
                while response == "pending":
                    if canceled():
                        return
                    try:
                        response = worker.responses.get(timeout=_POLL_SECONDS)
                    except queue.Empty:
                        waited += _POLL_SECONDS
                        if timeout_sec and waited >= timeout_sec:
                            response = "timeout"

                if response == "timeout":
                    worker.stop()
                    worker = None
                    finish(index, job, _failed_result(job, "Timed out after %ss." % timeout_sec))
                elif response is None:
                    worker.stop()
                    worker = None
                    finish(index, job, _failed_result(job, "The worker process crashed on this file."))
                else:
                    try:
                        result = json.loads(response.decode("ascii"))
                    except (ValueError, UnicodeDecodeError) as error:
                        result = {"worker_error": str(error)}
                    if "worker_error" in result:
                        result = _failed_result(job, result["worker_error"])
                    finish(index, job, result)
        finally:
            if worker is not None:
                worker.stop()

    def execute_job_safely(job, spawn_error):
        try:
            return execute_job(job)
        except Exception as error:
            return _failed_result(job, "Could not start a worker (%s), and in-process failed: %s"
                                  % (spawn_error, error))

    threads = [threading.Thread(target=drive, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ordered = [result for result in results if result is not None]
    return ordered, state["canceled"]
