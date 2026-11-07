# -*- coding: ascii -*-
# MayaShield module bootstrap.  Maya runs every userSetup.py on its script path, so
# this one runs for every user of a Maya that has the MayaShield module installed.
#
# 1. The first time it runs for a user, it loads MayaShield and switches its
#    autoload on.  That covers users and Maya versions the installer never saw,
#    since Maya ignores pluginPrefs.mel in brand new preferences.
# 2. After that it respects the user's choice: if autoload is still on it loads the
#    plug-in now, at userSetup time, so protection is up before Maya opens a scene
#    given on the command line (a double-click).  Normal autoload comes too late.
#
# Keep this file ASCII and valid Python 2: Maya 2022 can start in Python 2 mode.
import re as _mayashield_re

_MAYASHIELD_FLAG = "mayashield_autoloadConfigured"
_MAYASHIELD_ENTRY = _mayashield_re.compile(r'autoLoadPlugin\(.*,\s*\\"mayashield\\"\s*\)')


def _mayashield_autoload_on(cmds):
    import os
    try:
        path = os.path.join(cmds.internalVar(userPrefDir=True), "pluginPrefs.mel")
        with open(path, "rb") as handle:
            text = handle.read().decode("latin-1")
    except Exception:
        return False
    return bool(_MAYASHIELD_ENTRY.search(text))


def _mayashield_bootstrap():
    import sys
    if sys.version_info < (3, 7):
        return
    import maya.cmds as cmds
    first_time = not cmds.optionVar(exists=_MAYASHIELD_FLAG)
    if not first_time and not _mayashield_autoload_on(cmds):
        return  # the user switched autoload off
    try:
        if not cmds.pluginInfo("mayashield", query=True, loaded=True):
            cmds.loadPlugin("mayashield", quiet=True)
        if first_time:
            cmds.pluginInfo("mayashield", edit=True, autoload=True)
            cmds.optionVar(intValue=(_MAYASHIELD_FLAG, 1))
    except Exception as error:
        sys.stderr.write("MayaShield could not start: %s\n" % (error,))


_mayashield_bootstrap()
