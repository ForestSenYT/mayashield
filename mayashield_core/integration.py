# -*- coding: utf-8 -*-
"""Menu, shelf and status-line button.  All of it is removed again on unload."""
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

from . import __version__, guard

MENU_NAME = "MayaShieldMenu"
STATUS_BUTTON = "MayaShieldStatusBtn"
SHELF_NAME = "MayaShield"
PREFS_WINDOW = "MayaShieldPrefsWin"

OPT_DRYRUN = "mayashield_dryRun"
OPT_VERBOSE = "mayashield_verbose"
OPT_REPORT = "mayashield_reportPath"

ICON = "mayashield.png"
_FALLBACK_ICON = "menuIconWindow.png"


def _icon():
    """The bundled icon when the module provides it, else a built-in one."""
    for folder in (os.environ.get("XBMLANGPATH") or "").split(os.pathsep):
        folder = folder.replace("%B", "").rstrip("/\\")
        if folder and os.path.isfile(os.path.join(folder, ICON)):
            return ICON
    return _FALLBACK_ICON


def _run_mel(command):
    try:
        mel.eval(command)
    except RuntimeError as error:
        om.MGlobal.displayError(str(error))


def _show_panel(workspace):
    from . import ui
    ui.show_ui(workspace=workspace)


def install_menu():
    main_window = mel.eval("$tmp=$gMainWindow")
    if not main_window:
        return False
    uninstall_menu()
    menu = cmds.menu(MENU_NAME, label="MayaShield", parent=main_window, tearOff=True)
    cmds.menuItem(label="Open Panel (Workspace)", parent=menu, command=lambda *_: _show_panel(True))
    cmds.menuItem(label="Open Panel (Floating)", parent=menu, command=lambda *_: _show_panel(False))
    cmds.menuItem(divider=True, parent=menu)
    for label, command in (
        ("Scan", "mayashield -scan;"),
        ("Scan All", "mayashield -scanAll;"),
        ("Clean (Repair / Quarantine)", "mayashield -clean;"),
        ("Clean Scene Nodes", "mayashield -cleanScene;"),
        ("Clean Suspicious Jobs", "mayashield -cleanJobs;"),
        ("Immunize (Startup Guard)", "mayashield -immunize;"),
    ):
        cmds.menuItem(label=label, parent=menu, command=lambda _checked=False, c=command: _run_mel(c))
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Preferences...", parent=menu, command=lambda *_: show_preferences())
    cmds.menuItem(label="About MayaShield...", parent=menu, command=lambda *_: show_about())
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
                            annotation="MayaShield: open panel", command=lambda *_: _show_panel(True))
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
    existing = {}
    for child in cmds.shelfLayout(SHELF_NAME, q=True, childArray=True) or ():
        if cmds.objectTypeUI(child) == "shelfButton":
            existing[cmds.shelfButton(child, q=True, annotation=True)] = child
    icon = _icon()
    for annotation, label, command in (
        ("MayaShield: Open", "Shield", "mayashieldUI -workspace;"),
        ("MayaShield: Scan", "Scan", "mayashield -scan;"),
        ("MayaShield: Clean", "Clean", "mayashield -clean;"),
    ):
        settings = {"label": label, "imageOverlayLabel": label, "image": icon, "image1": icon,
                    "command": command, "sourceType": "mel"}
        if annotation in existing:
            # Maya restores saved shelves, so this also updates buttons from older versions.
            cmds.shelfButton(existing[annotation], edit=True, **settings)
        else:
            cmds.shelfButton(parent=SHELF_NAME, annotation=annotation, **settings)
    return True


def show_about():
    message = ("MayaShield %s\n"
               "Detects and removes Maya scene malware and startup persistence.\n\n"
               "Commands:\n  mayashield   (scan, clean, immunize)\n  mayashieldUI (panel)\n\n"
               "Tip: start with Dry Run." % __version__)
    cmds.confirmDialog(title="About MayaShield", message=message, button=["OK"])


def show_preferences():
    if cmds.window(PREFS_WINDOW, exists=True):
        cmds.deleteUI(PREFS_WINDOW)
    cmds.window(PREFS_WINDOW, title="MayaShield Preferences", sizeable=False)
    cmds.columnLayout(adj=True, rowSpacing=8, columnAlign="left")
    dry = cmds.checkBox(label="Default: Dry Run", value=guard.option_bool(OPT_DRYRUN, True))
    verbose = cmds.checkBox(label="Default: Verbose", value=guard.option_bool(OPT_VERBOSE, False))
    startup = cmds.checkBox(label="Scan startup files when Maya starts", value=guard.option_bool(guard.OPT_STARTUP))
    runtime = cmds.checkBox(label="Check scenes before opening them", value=guard.option_bool(guard.OPT_RUNTIME))
    cmds.text(label="Default report path (optional):", align="left")
    report_value = cmds.optionVar(q=OPT_REPORT) if cmds.optionVar(exists=OPT_REPORT) else ""
    report = cmds.textField(text=str(report_value or ""))

    def save(*_):
        guard.set_option_bool(OPT_DRYRUN, cmds.checkBox(dry, q=True, value=True))
        guard.set_option_bool(OPT_VERBOSE, cmds.checkBox(verbose, q=True, value=True))
        guard.set_option_bool(guard.OPT_STARTUP, cmds.checkBox(startup, q=True, value=True))
        guard.set_option_bool(guard.OPT_RUNTIME, cmds.checkBox(runtime, q=True, value=True))
        cmds.optionVar(sv=(OPT_REPORT, cmds.textField(report, q=True, text=True).strip()))
        cmds.deleteUI(PREFS_WINDOW)

    cmds.rowLayout(nc=2, adjustableColumn=1, columnAttach=[(1, "both", 0), (2, "both", 6)])
    cmds.button(label="Save", command=save)
    cmds.button(label="Cancel", command=lambda *_: cmds.deleteUI(PREFS_WINDOW))
    cmds.setParent("..")
    cmds.showWindow(PREFS_WINDOW)


def install_all():
    """Deferred at plug-in load, once Maya's UI exists.  Failures never stop loading."""
    for step in (install_menu, install_status_button, install_shelf):
        try:
            step()
        except Exception as error:
            om.MGlobal.displayWarning("MayaShield: %s failed: %s" % (step.__name__, error))


def uninstall_all():
    for step in (uninstall_menu, uninstall_status_button):
        try:
            step()
        except RuntimeError:
            pass
    try:
        from . import ui
        ui.close_ui()
    except Exception:
        pass
