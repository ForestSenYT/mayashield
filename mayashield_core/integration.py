# -*- coding: utf-8 -*-
"""Menu, shelf and status-line button.  All of it is removed again on unload."""
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

from . import __version__
from .i18n import tr

MENU_NAME = "MayaShieldMenu"
STATUS_BUTTON = "MayaShieldStatusBtn"
SHELF_NAME = "MayaShield"
PREFS_WINDOW = "MayaShieldPrefsWin"

OPT_DRYRUN = "mayashield_dryRun"
OPT_VERBOSE = "mayashield_verbose"
OPT_REPORT = "mayashield_reportPath"

ICON = "mayashield.png"
_FALLBACK_ICON = "menuIconWindow.png"

# Shelf buttons are recognised by this tag, so their visible text can follow the
# interface language.  Buttons saved by 4.0 and 3.x only had an English annotation.
_SHELF_BUTTONS = (
    ("mayashield.open", "shelf.open", "shelf.open.label", "mayashieldUI -workspace;", ("MayaShield: Open",)),
    ("mayashield.checkup", "shelf.checkup", "shelf.checkup.label", "mayashieldUI -workspace -checkup;",
     ("MayaShield: Scan",)),
)
# 4.0 also had a "Clean" button that worked on files without showing what it found.
# The panel's "Fix Now" replaces it.
_RETIRED_SHELF_ANNOTATIONS = ("MayaShield: Clean",)


def _icon():
    """The bundled icon when the module provides it, else a built-in one."""
    for folder in (os.environ.get("XBMLANGPATH") or "").split(os.pathsep):
        folder = folder.replace("%B", "").rstrip("/\\")
        if folder and os.path.isfile(os.path.join(folder, ICON)):
            return ICON
    return _FALLBACK_ICON


def _show_panel(workspace, checkup=False):
    try:
        from . import ui
        ui.show_ui(workspace=workspace, checkup=checkup)
    except Exception as error:
        om.MGlobal.displayError(tr("ui.failed", error))


def install_menu():
    main_window = mel.eval("$tmp=$gMainWindow")
    if not main_window:
        return False
    uninstall_menu()
    menu = cmds.menu(MENU_NAME, label="MayaShield", parent=main_window, tearOff=True)
    cmds.menuItem(label=tr("menu.open"), parent=menu, command=lambda *_: _show_panel(True))
    cmds.menuItem(label=tr("menu.checkup"), parent=menu, command=lambda *_: _show_panel(True, checkup=True))
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label=tr("menu.open.float"), parent=menu, command=lambda *_: _show_panel(False))
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label=tr("menu.about"), parent=menu, command=lambda *_: show_about())
    return True


def uninstall_menu():
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME)


def install_status_button():
    parent = mel.eval("$tmp=$gStatusLine")
    if not parent or cmds.control(STATUS_BUTTON, exists=True):
        return bool(parent)
    try:
        cmds.iconTextButton(STATUS_BUTTON, parent=parent, style="iconOnly", image1=_icon(),
                            annotation=tr("status.button"), command=lambda *_: _show_panel(True))
        return True
    except RuntimeError:
        return False


def uninstall_status_button():
    if cmds.control(STATUS_BUTTON, exists=True):
        cmds.deleteUI(STATUS_BUTTON)


def install_shelf():
    top = mel.eval("$tmp=$gShelfTopLevel")
    if not top:
        return False
    if not cmds.shelfLayout(SHELF_NAME, exists=True):
        cmds.shelfLayout(SHELF_NAME, parent=top)
    by_tag = {}
    by_annotation = {}
    for child in cmds.shelfLayout(SHELF_NAME, q=True, childArray=True) or ():
        if cmds.objectTypeUI(child) != "shelfButton":
            continue
        tag = cmds.shelfButton(child, q=True, docTag=True) or ""
        annotation = cmds.shelfButton(child, q=True, annotation=True) or ""
        if annotation in _RETIRED_SHELF_ANNOTATIONS and not tag:
            cmds.deleteUI(child)
            continue
        by_tag[tag] = child
        by_annotation[annotation] = child
    icon = _icon()
    for tag, annotation_key, label_key, command, old_annotations in _SHELF_BUTTONS:
        label = tr(label_key)
        settings = {"label": label, "imageOverlayLabel": label, "image": icon, "image1": icon, "command": command,
                    "sourceType": "mel", "annotation": tr(annotation_key), "docTag": tag}
        existing = by_tag.get(tag)
        if existing is None:
            # Maya restores saved shelves, so this also updates buttons from older versions.
            existing = next((by_annotation[name] for name in old_annotations if name in by_annotation), None)
        if existing is not None:
            cmds.shelfButton(existing, edit=True, **settings)
        else:
            cmds.shelfButton(parent=SHELF_NAME, **settings)
    return True


def show_about():
    cmds.confirmDialog(title=tr("about.title"), message=tr("about.text", __version__), button=[tr("ok")])


def show_preferences():
    """Settings live in the panel, under Advanced."""
    _show_panel(True)


def install_all():
    """Deferred at plug-in load, once Maya's UI exists.  Failures never stop loading."""
    for step in (install_menu, install_status_button, install_shelf):
        try:
            step()
        except Exception as error:
            om.MGlobal.displayWarning(tr("integration.failed", step.__name__, error))


def uninstall_all():
    for step in (uninstall_menu, uninstall_status_button):
        try:
            step()
        except RuntimeError:
            pass
    if cmds.window(PREFS_WINDOW, exists=True):
        cmds.deleteUI(PREFS_WINDOW)
    try:
        from . import ui
        ui.close_ui()
    except Exception:
        pass
