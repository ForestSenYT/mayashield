# -*- coding: utf-8 -*-
"""Locating Maya's folders, inside or outside a Maya session."""
import os
import re
import shutil
import sys

_VERSION_DIR_RE = re.compile(r"^\d{4}(?:-x64)?$")


def _cmds():
    try:
        import maya.cmds as cmds
        return cmds
    except Exception:
        return None


def maya_location():
    # The environment first: Maya always sets it, and this may run off the main
    # thread, where maya.cmds must not be called.
    location = os.environ.get("MAYA_LOCATION")
    if location:
        return location
    cmds = _cmds()
    if cmds is not None:
        try:
            return cmds.about(mayaLocation=True) or ""
        except Exception:
            pass
    return ""


def find_mayapy():
    """mayapy of the running Maya.  Returns None rather than a different version."""
    location = maya_location()
    candidates = []
    if location:
        for relative in (("bin", "mayapy.exe"), ("bin", "mayapy"), ("Contents", "bin", "mayapy")):
            candidates.append(os.path.join(location, *relative))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    if os.path.basename(sys.executable or "").lower().startswith("mayapy"):
        return sys.executable
    if not location:
        return shutil.which("mayapy")
    return None


def app_dir():
    """The folder holding per-user Maya data (normally Documents/maya)."""
    cmds = _cmds()
    if cmds is not None:
        try:
            value = cmds.internalVar(userAppDir=True)
            if value:
                return os.path.normpath(value)
        except Exception:
            pass
    env = os.environ.get("MAYA_APP_DIR")
    if env:
        return os.path.normpath(env)
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Preferences", "Autodesk", "maya")
    if os.name == "nt":
        return os.path.join(home, "Documents", "maya")
    return os.path.join(home, "maya")


def version_dirs(root=None):
    """Every per-version folder (2022, 2024-x64...) under the app dir."""
    base = root or app_dir()
    found = []
    try:
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if _VERSION_DIR_RE.match(name) and os.path.isdir(path):
                found.append(path)
    except OSError:
        pass
    return found


def locale_dirs(version_dir):
    """Language subfolders with their own prefs, such as 2023/zh_CN."""
    found = []
    try:
        for name in sorted(os.listdir(version_dir)):
            path = os.path.join(version_dir, name)
            if re.match(r"^[a-z]{2}_[A-Z]{2}$", name) and os.path.isdir(path):
                found.append(path)
    except OSError:
        pass
    return found


def snapshot():
    """Paths needed by background scans, gathered once on the main thread."""
    location = maya_location()
    return {
        "app_dir": app_dir(),
        "maya_location": location,
        "sys_path": [path for path in sys.path if path],
        "script_path": [path for path in (os.environ.get("MAYA_SCRIPT_PATH") or "").split(os.pathsep) if path],
    }
