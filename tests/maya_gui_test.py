# -*- coding: utf-8 -*-
"""End-to-end test in a real interactive Maya, driven by tests/run_gui_test.ps1.

Maya starts from a sandbox MAYA_APP_DIR containing an installed-style module and a
pluginPrefs.mel entry, so this proves the installer's layout: does Maya really
autoload the plug-in, build the menu, shelf and panel, and handle real scriptJobs?
"""
import json
import os
import sys
import tempfile
import threading
import time
import traceback

import maya.cmds as cmds
import maya.utils

RESULT_PATH = os.environ["MAYASHIELD_GUI_RESULT"]
SHOT_DIR = os.environ.get("MAYASHIELD_GUI_SHOTS", "")
results = []


def check(name, condition, detail=""):
    results.append({"name": name, "ok": bool(condition), "detail": str(detail)[:500]})


def flush():
    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"maya": cmds.about(version=True), "python": sys.version, "results": results}, handle, indent=1)


_started = time.time()


def run():
    # Autoload entries load one per idle tick, so wait for ours instead of guessing.
    # The "off" case must wait the whole time, to prove nothing turned it back on.
    elapsed = time.time() - _started
    if os.environ.get("MAYASHIELD_GUI_EXPECT_OFF"):
        keep_waiting = elapsed < 40
    else:
        keep_waiting = not cmds.pluginInfo("mayashield", q=True, loaded=True) and elapsed < 180
    if keep_waiting and not os.environ.get("MAYASHIELD_GUI_CMDLINE"):
        retry = threading.Timer(2.0, lambda: maya.utils.executeDeferred(run))
        retry.daemon = True
        retry.start()
        return
    try:
        if os.environ.get("MAYASHIELD_GUI_CMDLINE"):
            command_line_steps()
        else:
            steps()
    except Exception:
        check("no exception in test body", False, traceback.format_exc())
    flush()
    # A normal quit, so we can inspect what Maya writes to pluginPrefs.mel on exit.
    maya.utils.executeDeferred(cmds.quit, force=True)


def command_line_steps():
    """Maya was started with an infected scene as its argument, like a double-click."""
    answered = os.path.exists(os.environ["MAYASHIELD_GUI_DIALOG_MARK"])
    ran = os.path.exists(os.environ["MAYASHIELD_GUI_PAYLOAD_MARK"])
    check("guard was active before the command-line scene opened", answered)
    check("payload did not run from a double-clicked scene", not ran)
    check("scene was refused", (cmds.file(q=True, sceneName=True) or "") == "", cmds.file(q=True, sceneName=True))
    check("plug-in is loaded", cmds.pluginInfo("mayashield", q=True, loaded=True))


def steps():
    from mayashield_core import qt_compat
    from mayashield_core.qt_compat import QT_API, QtCore, QtWidgets, is_alive

    # ---- autoload, from pluginPrefs.mel, through the module
    loaded = cmds.pluginInfo("mayashield", q=True, loaded=True)
    if os.environ.get("MAYASHIELD_GUI_EXPECT_OFF"):
        check("bootstrap respects autoload turned off by the user", not loaded)
        return
    check("plug-in autoloaded at startup", loaded)
    if not loaded:
        return
    check("autoload flag set", cmds.pluginInfo("mayashield", q=True, autoload=True))
    check("version 4.0.0", cmds.pluginInfo("mayashield", q=True, version=True) == "4.0.0")
    path = cmds.pluginInfo("mayashield", q=True, path=True)
    check("loaded from the sandbox install, not the old copy", "itest_gui" in path.replace("\\", "/"), path)
    check("module registered with Maya", "mayashield" in (cmds.moduleInfo(listModules=True) or []))
    check("running in GUI mode", not cmds.about(batch=True))
    check("Qt binding", True, QT_API)
    if os.environ.get("MAYASHIELD_GUI_QUICK"):
        return

    # ---- integration
    check("menu exists", cmds.menu("MayaShieldMenu", exists=True))
    check("menu has items", (cmds.menu("MayaShieldMenu", q=True, numberOfItems=True) or 0) >= 8)
    check("status button exists", cmds.control("MayaShieldStatusBtn", exists=True))
    check("shelf exists", cmds.shelfLayout("MayaShield", exists=True))
    buttons = cmds.shelfLayout("MayaShield", q=True, childArray=True) or []
    check("shelf has 3 buttons", len(buttons) == 3, buttons)
    sources = [cmds.shelfButton(b, q=True, sourceType=True) for b in buttons]
    check("shelf buttons are MEL", all(s == "mel" for s in sources), sources)
    check("bundled icon resolved", cmds.shelfButton(buttons[0], q=True, image=True) == "mayashield.png",
          cmds.shelfButton(buttons[0], q=True, image=True))

    # ---- panel
    from mayashield_core import guard, live, ui

    def pump(predicate, seconds=60):
        deadline = time.time() + seconds
        while time.time() < deadline:
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)
            if predicate():
                return True
            time.sleep(0.02)
        return False

    window = ui.show_ui(workspace=True)
    check("workspace panel shown", is_alive(window) and window.isVisible())
    check("workspace control exists", cmds.workspaceControl(ui.WORKSPACE_CONTROL, exists=True))
    pump(lambda: False, 1)
    if SHOT_DIR:
        window.grab().save(os.path.join(SHOT_DIR, "panel_workspace.png"))
    again = ui.show_ui(workspace=True)
    check("panel is a singleton", again is window)

    # ---- scanning through the panel, on its background thread
    work = tempfile.mkdtemp(prefix="mayashield_gui_")
    cmds.file(new=True, force=True)
    cmds.polyCube(name="hero_geo")
    cmds.scriptNode(name="vaccine_gene", beforeScript="print('inert')", scriptType=1, sourceType="python")
    bad = os.path.join(work, "bad.ma").replace("\\", "/")
    cmds.file(rename=bad)
    cmds.file(save=True, type="mayaAscii", force=True)
    bad_mb = os.path.join(work, "bad.mb").replace("\\", "/")
    cmds.file(rename=bad_mb)
    cmds.file(save=True, type="mayaBinary", force=True)
    cmds.delete("vaccine_gene")
    good = os.path.join(work, "good.ma").replace("\\", "/")
    cmds.file(rename=good)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)

    window._scan_paths_job(bad, is_folder=False)
    check("file scan runs off the UI thread", window.worker is not None)
    finished = pump(lambda: window.worker is None)
    check("file scan finishes", finished)
    check("results reach the tree", window.tree.topLevelItemCount() == 1)
    check("flagged file remembered", window.last_hit_files == [os.path.abspath(bad)], window.last_hit_files)
    check("clean-last button enabled", window.btn_cleanlast.isEnabled())
    pump(lambda: False, 1)
    if SHOT_DIR:
        window.grab().save(os.path.join(SHOT_DIR, "panel_hits.png"))

    folder = os.path.join(work, "folder")
    os.makedirs(folder)
    for index in range(6):
        with open(good if index % 2 else bad, "rb") as source:
            with open(os.path.join(folder, "s%d.ma" % index), "wb") as target:
                target.write(source.read())
    window._scan_paths_job(folder.replace("\\", "/"), is_folder=True)
    finished = pump(lambda: window.worker is None, 120)
    check("folder scan through mayapy workers finishes", finished)
    check("folder scan flags the right count", len(window.last_hit_files) == 3, window.last_hit_files)

    # A long job can be canceled and leaves the panel usable.
    for index in range(40):
        with open(bad, "rb") as source:
            with open(os.path.join(folder, "more%d.ma" % index), "wb") as target:
                target.write(source.read())
    window._scan_paths_job(folder.replace("\\", "/"), is_folder=True)
    window._cancel_job()
    check("cancel returns control", pump(lambda: window.worker is None, 60))
    check("buttons re-enabled after cancel", window.btn_scan.isEnabled())

    window._scan_disk(False)
    check("disk scan through the panel finishes", pump(lambda: window.worker is None, 60))

    # ---- modal dialogs and menus, on Maya's own Qt
    def click_message_box(text):
        def answer():
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible():
                    for button in widget.buttons():
                        if text in button.text():
                            button.click()
                            return
            QtCore.QTimer.singleShot(50, answer)
        QtCore.QTimer.singleShot(50, answer)

    click_message_box("Create Clean Copy")
    check("clean-mode dialog returns copy", window._ask_clean_mode() == "copy")
    click_message_box("Overwrite Original")
    check("clean-mode dialog returns overwrite", window._ask_clean_mode() == "inplace")
    click_message_box("Cancel")
    check("clean-mode dialog returns cancel", window._ask_clean_mode() is None)

    def close_quarantine():
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, QtWidgets.QDialog) and widget.isVisible() and widget.windowTitle() == "MayaShield Quarantine":
                widget.accept()
                return
        QtCore.QTimer.singleShot(50, close_quarantine)

    QtCore.QTimer.singleShot(50, close_quarantine)
    window._show_quarantine()
    check("quarantine dialog opens and closes", True)

    menu = QtWidgets.QMenu(window)
    menu.addAction("x")
    QtCore.QTimer.singleShot(100, menu.close)
    qt_compat.exec_menu(menu, QtCore.QPoint(0, 0))
    check("menu helper works", True)

    # ---- log links must not navigate away and blank the log
    text_before = window.log.toPlainText()
    window._on_log_link(QtCore.QUrl(ui._link("selectNode", "time1")))
    check("clicking a link keeps the log", window.log.toPlainText() == text_before)
    check("link round-trips a non-ascii path", ui._parse_link(ui._link("reveal", u"D:/场景/a b.ma"))[1] == u"D:/场景/a b.ma")

    # ---- floating mode
    floating = ui.show_ui(workspace=False)
    check("floating panel shown", is_alive(floating) and floating.isVisible())
    ui.close_ui()
    check("panel closes cleanly", not cmds.workspaceControl(ui.WORKSPACE_CONTROL, exists=True))

    # ---- real scriptJobs (batch mode has none)
    job = cmds.scriptJob(event=["timeChanged", "import vaccine"])
    check("suspicious job detected", any(hit.get("id") == job for hit in live.job_hits()), live.job_hits()[:2])
    normal = cmds.scriptJob(event=["timeChanged", "print('normal tool')"])
    flagged = [hit.get("id") for hit in live.job_hits()]
    check("ordinary job not flagged", normal not in flagged, flagged)
    live.clean_jobs()
    check("suspicious job killed", not cmds.scriptJob(exists=job))
    check("ordinary job survives", cmds.scriptJob(exists=normal))
    cmds.scriptJob(kill=normal, force=True)
    stock = live.job_hits()
    check("no stock Maya job flagged in a live session", not stock, stock[:3])

    # ---- guard in a real session, answering its dialog
    calls = []

    def answer(choice):
        def fake(_title, message, _buttons, _default, _cancel):
            calls.append(message)
            return choice
        return fake

    original = guard._ask
    try:
        guard._ask = answer("Block")
        guard._verdicts.clear()
        cmds.file(bad, open=True, force=True, prompt=False)
        check("guard blocks in GUI", len(calls) == 1 and (cmds.file(q=True, sceneName=True) or "") == "",
              cmds.file(q=True, sceneName=True))

        guard._ask = answer("Clean Copy")
        guard._verdicts.clear()
        cmds.file(bad_mb, open=True, force=True, prompt=False)
        opened = pump(lambda: bool(cmds.file(q=True, sceneName=True)), 20)
        check("Clean Copy opens the cleaned scene", opened and cmds.file(q=True, sceneName=True).endswith("bad.cleaned.mb"),
              cmds.file(q=True, sceneName=True))
        check("cleaned scene content is intact", cmds.objExists("hero_geo") and not cmds.objExists("vaccine_gene"))
    finally:
        guard._ask = original
    cmds.file(new=True, force=True)

    # ---- the real dialog must not fire for ordinary scenes
    guard._verdicts.clear()
    cmds.file(good, open=True, force=True, prompt=False)
    check("clean scene opens", cmds.objExists("hero_geo"))
    cmds.file(new=True, force=True)

    # ---- unload removes everything, reload restores it
    cmds.unloadPlugin("mayashield")
    check("unload removes the menu", not cmds.menu("MayaShieldMenu", exists=True))
    check("unload removes the status button", not cmds.control("MayaShieldStatusBtn", exists=True))
    check("unload removes the callbacks", guard._callbacks == [])
    cmds.loadPlugin("mayashield")
    pump(lambda: cmds.menu("MayaShieldMenu", exists=True), 10)
    check("reload restores the menu", cmds.menu("MayaShieldMenu", exists=True))
    check("reload does not duplicate shelf buttons",
          len(cmds.shelfLayout("MayaShield", q=True, childArray=True) or []) == 3)

    # A button exactly as v3 saved it, as restored from an existing shelf file.
    from mayashield_core import integration
    old = cmds.shelfLayout("MayaShield", q=True, childArray=True)[0]
    cmds.shelfButton(old, edit=True, annotation="MayaShield: Open", image="menuIconWindow.png", sourceType="python",
                     command="import maya.mel as mel; mel.eval('mayashieldUI -workspace;')")
    integration.install_shelf()
    check("button saved by v3 gets upgraded",
          cmds.shelfButton(old, q=True, image=True) == "mayashield.png"
          and cmds.shelfButton(old, q=True, sourceType=True) == "mel"
          and cmds.shelfButton(old, q=True, command=True) == "mayashieldUI -workspace;",
          cmds.shelfButton(old, q=True, command=True))
    check("upgrade does not add a button", len(cmds.shelfLayout("MayaShield", q=True, childArray=True) or []) == 3)
    cmds.pluginInfo("mayashield", edit=True, autoload=True)
    flush()


# Give Maya's deferred startup (autoload) time to finish, then run on the main thread.
_timer = threading.Timer(float(os.environ.get("MAYASHIELD_GUI_DELAY", "15")), lambda: maya.utils.executeDeferred(run))
_timer.daemon = True
_timer.start()
