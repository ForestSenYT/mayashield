# -*- coding: utf-8 -*-
"""File operations that cope with Windows holding a file for a moment.

Antivirus and the search indexer open a freshly written file as soon as it is
closed.  A rename or delete that lands in that moment fails with PermissionError
and works a few milliseconds later, which is why pip, npm and Go all retry.  The
cleaner writes, closes and renames within about a millisecond, so it has to as well.
"""
import os
import shutil
import stat
import time

_ATTEMPTS = 10
_FIRST_PAUSE = 0.04
_LONGEST_PAUSE = 0.5


def _retry(action):
    pause = _FIRST_PAUSE
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return action()
        except PermissionError:
            if attempt == _ATTEMPTS:
                raise
        time.sleep(pause)
        pause = min(_LONGEST_PAUSE, pause * 2)


def replace(source, destination):
    """os.replace, tried again while another program has either file open."""
    _retry(lambda: os.replace(source, destination))


def remove(path):
    """Delete a file if it exists, read-only or not.  Tried again while it is busy."""
    def action():
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        except PermissionError:
            make_writable(path)
            os.remove(path)
    _retry(action)


def remove_quietly(path):
    """Delete a file if that is possible.  Never raises: for tidying up after a failure."""
    try:
        remove(path)
        return True
    except OSError:
        return False


def make_writable(path):
    """Clear the read-only bit.  Returns the mode the file had, or None if nothing changed."""
    try:
        mode = os.stat(path).st_mode
        if mode & stat.S_IWRITE:
            return None
        os.chmod(path, mode | stat.S_IWRITE)
        return mode
    except OSError:
        return None


def restore_mode(path, mode):
    """Undo make_writable, when it reported a change."""
    if mode is None:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def rmtree(path):
    """Remove a folder tree, read-only files included.  Never raises."""
    def retry_writable(function, target, _error):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            function(target)
        except OSError:
            pass

    for _attempt in range(3):
        shutil.rmtree(path, onerror=retry_writable)
        if not os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def signature(path):
    """What identifies a file's current contents cheaply: (size, modification time in ns)."""
    status = os.stat(path)
    return status.st_size, status.st_mtime_ns
