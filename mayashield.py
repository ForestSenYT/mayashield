# -*- coding: utf-8 -*-
"""MayaShield plug-in entry point.

Registers the commands and starts protection.  The engine lives in the
``mayashield_core`` package.  This file avoids Python-3-only syntax so that Maya
2022 started in its optional Python 2 mode gets a clear message, not a SyntaxError.
"""
import os
import sys

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.utils


def maya_useNewAPI():
    """Tells Maya this plug-in uses Python API 2.0."""


def _ensure_engine_importable():
    try:
        import mayashield_core  # noqa: F401
        return
    except ImportError:
        pass
    # Maya's plug-in loader doesn't define __file__, but the code object knows.
    here = os.path.dirname(os.path.abspath(_ensure_engine_importable.__code__.co_filename))
    for folder in (here, os.path.join(os.path.dirname(here), "scripts")):
        if os.path.isdir(os.path.join(folder, "mayashield_core")):
            sys.path.insert(0, folder)
            return
    raise RuntimeError("MayaShield: the mayashield_core package was not found. Please reinstall MayaShield.")


def initializePlugin(mobject):
    if sys.version_info < (3, 7):
        raise RuntimeError("MayaShield 4 needs Python 3.7 or newer (Maya 2022 or later in Python 3 mode).")
    _ensure_engine_importable()

    from mayashield_core import VENDOR, __version__, command, guard, i18n, integration

    i18n.language()   # asks Maya for its interface language: here, on the main thread, once
    plugin = om.MFnPlugin(mobject, VENDOR, __version__, "Any")
    plugin.registerCommand(command.COMMAND, command.MayaShieldCmd.creator)
    plugin.registerCommand(command.UI_COMMAND, command.MayaShieldUICmd.creator)
    guard.install()
    if not cmds.about(batch=True):
        maya.utils.executeDeferred(integration.install_all)
        maya.utils.executeDeferred(guard.startup_check)
        maya.utils.executeDeferred(guard.after_scene_check)


def uninitializePlugin(mobject):
    from mayashield_core import command, guard, integration

    guard.uninstall()
    if not cmds.about(batch=True):
        integration.uninstall_all()
    plugin = om.MFnPlugin(mobject)
    plugin.deregisterCommand(command.UI_COMMAND)
    plugin.deregisterCommand(command.COMMAND)
