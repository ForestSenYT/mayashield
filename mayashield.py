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
    """Make ``import mayashield_core`` find the engine that came with this very file.

    Two copies of MayaShield can be on one machine (one for everybody in Maya's own
    folder, one in a user's).  Maya then puts the scripts folder of one on sys.path
    and may load the plug-in file of the other; an engine of a different version
    than the plug-in file fails in ways nobody can make sense of.  So the engine
    next to this file goes first, whatever else is on the path.
    """
    # Maya's plug-in loader doesn't define __file__, but the code object knows.
    here = os.path.dirname(os.path.abspath(_ensure_engine_importable.__code__.co_filename))
    for folder in (os.path.join(os.path.dirname(here), "scripts"), here):
        if os.path.isdir(os.path.join(folder, "mayashield_core")):
            loaded = sys.modules.get("mayashield_core")
            origin = os.path.dirname(os.path.dirname(os.path.abspath(getattr(loaded, "__file__", "") or ""))) if loaded else ""
            if loaded is not None and os.path.normcase(origin) != os.path.normcase(folder):
                # Another copy's engine is already imported (its userSetup guard, say).  Drop it,
                # so that what gets imported from here on is all of one version.
                for name in [name for name in sys.modules if name == "mayashield_core" or name.startswith("mayashield_core.")]:
                    del sys.modules[name]
            while folder in sys.path:
                sys.path.remove(folder)
            sys.path.insert(0, folder)
            return
    try:
        import mayashield_core  # noqa: F401
    except ImportError:
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
