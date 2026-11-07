# -*- coding: utf-8 -*-
"""Exercises the real panel code on any Qt binding, headless, with Maya stubbed out.

Maya 2025+ uses PySide6.  Run this with the same Python and PySide6 as a Maya you
don't have installed to catch Qt API breaks; the real Maya GUI test can't cover it:

    python tests/qt_ui_test.py          (QT_QPA_PLATFORM=offscreen is set for you)
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OPTION_VARS = {}
CONTROLS = {}
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print("%s  %s  %s" % ("PASS" if condition else "FAIL", name, "" if condition else detail), flush=True)


# ------------------------------------------------------------------ stub of Maya
class Cmds(types.ModuleType):
    """Only what the panel needs.  The workspace control is a real QWidget."""

    def about(self, **kwargs):
        return "" if "mayaLocation" in kwargs else False

    def optionVar(self, **kwargs):
        if "exists" in kwargs:
            return kwargs["exists"] in OPTION_VARS
        if "q" in kwargs:
            return OPTION_VARS.get(kwargs["q"], 0)
        for key in ("iv", "sv"):
            if key in kwargs:
                OPTION_VARS[kwargs[key][0]] = kwargs[key][1]

    def internalVar(self, **_kwargs):
        return WORK + "/"

    def workspaceControl(self, name, **kwargs):
        if kwargs.get("exists"):
            return name in CONTROLS
        if kwargs.get("edit"):
            return None
        host = QtWidgets.QWidget()
        host.show()
        CONTROLS[name] = host
        return name

    def deleteUI(self, name):
        widget = CONTROLS.pop(name, None)
        if widget is not None:
            widget.deleteLater()

    def objExists(self, _name):
        return False

    def ls(self, **_kwargs):
        return []

    def scriptJob(self, **_kwargs):
        return []


class MQtUtil(object):
    main = None

    @staticmethod
    def mainWindow():
        return shiboken.getCppPointer(MQtUtil.main)[0]

    @staticmethod
    def findControl(name):
        widget = CONTROLS.get(name)
        return shiboken.getCppPointer(widget)[0] if widget is not None else None


def install_stub():
    maya = types.ModuleType("maya")
    cmds = Cmds("maya.cmds")
    utils = types.ModuleType("maya.utils")
    utils.executeDeferred = lambda function, *args: function(*args)
    api = types.ModuleType("maya.api")
    om2 = types.ModuleType("maya.api.OpenMaya")

    class MGlobal(object):
        displayInfo = displayWarning = displayError = staticmethod(lambda _text: None)

    om2.MGlobal = MGlobal
    api.OpenMaya = om2
    omui = types.ModuleType("maya.OpenMayaUI")
    omui.MQtUtil = MQtUtil
    mel = types.ModuleType("maya.mel")
    maya.cmds, maya.utils, maya.api, maya.OpenMayaUI, maya.mel = cmds, utils, api, omui, mel
    sys.modules.update({"maya": maya, "maya.cmds": cmds, "maya.utils": utils, "maya.api": api,
                        "maya.api.OpenMaya": om2, "maya.OpenMayaUI": omui, "maya.mel": mel})


install_stub()

import fixtures  # noqa: E402
from mayashield_core.qt_compat import QT_API, QtCore, QtWidgets, exec_menu, shiboken  # noqa: E402
from mayashield_core import ui  # noqa: E402

print("Python %s | %s %s" % (sys.version.split()[0], QT_API, getattr(sys.modules[QT_API], "__version__", "?")), flush=True)
WORK = tempfile.mkdtemp(prefix="mayashield_qt_")
os.environ["MAYA_APP_DIR"] = os.path.join(WORK, "appdir")


def pump(predicate, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents(QtCore.QEventLoop.AllEvents, 30)
        if predicate():
            return True
        time.sleep(0.01)
    return False


def click_message_box(button_text):
    """Answer the next modal QMessageBox by clicking one of its buttons."""
    def answer():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible():
                for button in widget.buttons():
                    if button_text in button.text():
                        button.click()
                        return
        QtCore.QTimer.singleShot(20, answer)
    QtCore.QTimer.singleShot(20, answer)


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
MQtUtil.main = QtWidgets.QMainWindow()

bad = os.path.join(WORK, "bad.ma")
with open(bad, "wb") as handle:
    handle.write(fixtures.ma_scene(fixtures.script_node("vaccine_gene", fixtures.INERT, python=True)))
folder = os.path.join(WORK, "scenes")
os.makedirs(folder)
for index in range(6):
    with open(os.path.join(folder, "s%d.ma" % index), "wb") as handle:
        handle.write(fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT)) if index % 2 else fixtures.ma_scene())

# Workspace mode, through the real wrapInstance.
window = ui.show_ui(workspace=True)
check("workspace panel embeds", window is not None and window.isVisible())
check("panel is parented to the workspace control", window.parent() is not None)
check("singleton in workspace mode", ui.show_ui(workspace=True) is window)
ui.close_ui()
check("closing removes the control", ui.WORKSPACE_CONTROL not in CONTROLS)

# Floating mode.
window = ui.show_ui(workspace=False)
check("floating panel shows", window.isVisible())
check("title has the version", "4.0.0" in window.windowTitle())

# File scan on a background thread, delivered through Qt signals.
window._scan_paths_job(bad, is_folder=False)
check("job started", window.worker is not None)
check("file scan finishes", pump(lambda: window.worker is None))
check("tree filled", window.tree.topLevelItemCount() == 1)
top = window.tree.topLevelItem(0)
check("risk shown", top.text(2) == "high", top.text(2))
check("tree stores a link", (top.data(0, QtCore.Qt.UserRole) or "").startswith("ms://reveal"))
check("child and evidence items built", top.childCount() == 1 and top.child(0).childCount() >= 1)
check("flagged file remembered", window.last_hit_files == [os.path.abspath(bad)])
check("clean button updated", window.btn_cleanlast.isEnabled() and "(1)" in window.btn_cleanlast.text())
check("log has content", "vaccine_gene" in window.log.toPlainText())

# Links must keep the log (openLinks off), and html in a node name must not render.
before = window.log.toPlainText()
window.log.anchorClicked.emit(QtCore.QUrl(ui._link("selectNode", "x")))
check("link click keeps the log", window.log.toPlainText() == before)
window._append("safe: " + ui.html.escape("<img src=x>"))
check("markup is escaped", "<img src=x>" in window.log.toPlainText())

# Folder scan through worker processes, with cancel.
window._scan_paths_job(folder, is_folder=True)
check("folder scan finishes", pump(lambda: window.worker is None, 120))
check("folder hits counted", len(window.last_hit_files) == 3, window.last_hit_files)
window._scan_paths_job(folder, is_folder=True)
window._cancel_job()
check("cancel returns control", pump(lambda: window.worker is None, 60))
check("buttons enabled after cancel", window.btn_scan.isEnabled())

# Modal dialogs.  Maya's own PySide2 build crashes in QMessageBox.exec on the offscreen
# platform; tests/maya_gui_test.py covers these on its real display instead.
if QT_API == "PySide6":
    check("dry run skips the clean-mode dialog", window._choose_clean_mode() == "dry")
    window.chk_dry.setChecked(False)
    click_message_box("Create Clean Copy")
    check("clean-mode dialog works (exec)", window._choose_clean_mode() == "copy")
    click_message_box("Overwrite Original")
    check("clean-mode dialog returns overwrite", window._choose_clean_mode() == "inplace")
    click_message_box("Cancel")
    check("clean-mode dialog returns cancel", window._choose_clean_mode() is None)
    window.chk_dry.setChecked(True)

    menu = QtWidgets.QMenu(window)
    menu.addAction("x")
    QtCore.QTimer.singleShot(50, menu.close)
    exec_menu(menu, QtCore.QPoint(0, 0))
    check("menu exec helper works", True)

    def close_quarantine():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QDialog) and widget.isVisible() \
                    and widget.windowTitle() == "MayaShield Quarantine":
                widget.accept()
                return
        QtCore.QTimer.singleShot(20, close_quarantine)

    QtCore.QTimer.singleShot(20, close_quarantine)
    window._show_quarantine()
    check("quarantine dialog opens and closes", True)

# Offline clean through the panel's job path.
window.chk_dry.setChecked(False)
window._start_job("Cleaning...", lambda worker: ui.cleaner.clean_file(bad, progress_cb=lambda s, p, d="": worker.report_progress(s, 0, 0)),
                  window._show_clean_report)
check("clean job finishes", pump(lambda: window.worker is None))
check("clean output written", os.path.isfile(os.path.join(WORK, "bad.cleaned.ma")))

# Disk scan through the panel.
window._scan_disk(True)
check("disk scan finishes", pump(lambda: window.worker is None, 60))

# Report writing.
report = os.path.join(WORK, "report.json")
window.le_report.setText(report)
window._scan_paths_job(folder, is_folder=True)
pump(lambda: window.worker is None, 120)
with open(report, encoding="utf-8") as handle:
    check("report written as JSON", json.load(handle)["summary"]["files"] == 6)

window.grab().save(os.path.join(WORK, "panel.png"))
check("panel can be rendered", os.path.getsize(os.path.join(WORK, "panel.png")) > 1000)

ui.close_ui()
failed = [name for name, ok in results if not ok]
print("\n%d checks, %d failed" % (len(results), len(failed)), flush=True)
shutil.rmtree(WORK, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if failed else 0)
