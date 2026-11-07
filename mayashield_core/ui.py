# -*- coding: utf-8 -*-
"""The MayaShield panel.

File work runs on a background thread (which mostly waits on worker processes),
so Maya stays usable during a long folder scan.  Anything calling ``maya.cmds``
stays on the main thread.
"""
import html
import os
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, unquote

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.OpenMayaUI as omui

from . import __version__, cleaner, guard, integration, live, mayapaths, persistence, rules, scanner
from .qt_compat import QtCore, QtGui, QtWidgets, exec_dialog, exec_menu, is_alive, wrap_pointer

WORKSPACE_CONTROL = "MayaShieldWorkspaceControl"
OBJECT_NAME = "MayaShieldWindow_V4"
_UI_SCRIPT = "import mayashield_core.ui as _ms_ui; _ms_ui.restore_workspace()"
_LINK_SCHEME = "ms://"
_RISK_COLORS = {"high": (210, 60, 60), "medium": (220, 140, 40), "low": (40, 140, 220), "error": (200, 40, 200)}
_MAX_RENDERED = 300

_window = None


def _link(action, value):
    return "%s%s?v=%s" % (_LINK_SCHEME, action, quote(str(value)))


def _parse_link(url):
    text = str(url)
    if not text.startswith(_LINK_SCHEME) or "?" not in text:
        return None, None
    action, query = text[len(_LINK_SCHEME):].split("?", 1)
    return action, unquote(parse_qs(query).get("v", [""])[0])


def reveal_path(path):
    """Show a file or folder in the system file browser."""
    target = os.path.normpath(path)
    try:
        if os.name == "nt":
            if os.path.isfile(target):
                subprocess.Popen(["explorer", "/select,", target])
            else:
                subprocess.Popen(["explorer", target])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target] if os.path.isfile(target) else ["open", target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target) if os.path.isfile(target) else target])
    except OSError as error:
        om.MGlobal.displayWarning("MayaShield: could not reveal %s: %s" % (target, error))


def select_node(node):
    if cmds.objExists(node):
        cmds.select(node, replace=True)
    else:
        om.MGlobal.displayWarning("MayaShield: node not found: %s" % node)


class _Worker(QtCore.QThread):
    """Runs one function off the UI thread.  Results come back as signals."""
    progressed = QtCore.Signal(str, int, int)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function, parent=None):
        super(_Worker, self).__init__(parent)
        self.function = function
        self.canceled = False

    def cancel(self):
        self.canceled = True

    def is_canceled(self):
        return self.canceled

    def report_progress(self, path, index, total):
        self.progressed.emit(os.path.basename(path), int(index), int(total or 0))

    def run(self):
        try:
            self.done.emit(self.function(self))
        except Exception as error:
            self.failed.emit("%s: %s" % (type(error).__name__, error))


class MayaShieldWindow(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(MayaShieldWindow, self).__init__(parent)
        self.setObjectName(OBJECT_NAME)
        self.setWindowTitle("MayaShield %s" % __version__)
        self.worker = None
        self.started = 0.0
        self.last_hit_files = []
        self.last_scan_source = ""
        self.action_buttons = []
        self._build()
        self._connect()
        self._update_last_scan_button()

    # ------------------------------------------------------------------ layout
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        options = QtWidgets.QGroupBox("Options")
        grid = QtWidgets.QGridLayout(options)
        self.chk_dry = QtWidgets.QCheckBox("Dry Run (report only)")
        self.chk_dry.setChecked(guard.option_bool(integration.OPT_DRYRUN, True))
        self.chk_verbose = QtWidgets.QCheckBox("Verbose")
        self.chk_verbose.setChecked(guard.option_bool(integration.OPT_VERBOSE, False))
        self.chk_medium = QtWidgets.QCheckBox("Clean medium risk too")
        self.chk_medium.setToolTip("Off: only high-risk items are removed. Medium-risk items are listed for review.")
        self.chk_deep = QtWidgets.QCheckBox("Deep scan (mayapy)")
        self.chk_deep.setToolTip("Also open each scene in an isolated mayapy. Much slower; needs a Maya licence.")
        self.le_report = QtWidgets.QLineEdit()
        self.le_report.setPlaceholderText("Optional JSON report path, e.g. C:/temp/mayashield_report.json")
        if cmds.optionVar(exists=integration.OPT_REPORT):
            self.le_report.setText(str(cmds.optionVar(q=integration.OPT_REPORT) or ""))
        self.btn_browse = QtWidgets.QPushButton("Browse...")
        self.sb_timeout = QtWidgets.QSpinBox()
        self.sb_timeout.setRange(10, 3600)
        self.sb_timeout.setValue(120)

        grid.addWidget(self.chk_dry, 0, 0)
        grid.addWidget(self.chk_verbose, 0, 1)
        grid.addWidget(self.chk_medium, 0, 2)
        grid.addWidget(self.chk_deep, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Report:"), 1, 0)
        grid.addWidget(self.le_report, 1, 1, 1, 2)
        grid.addWidget(self.btn_browse, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Timeout per file (s):"), 2, 0)
        grid.addWidget(self.sb_timeout, 2, 1)
        layout.addWidget(options)

        actions = QtWidgets.QGroupBox("Actions")
        buttons = QtWidgets.QGridLayout(actions)
        self.btn_scan = self._action("Scan", self._scan_disk_quick)
        self.btn_scanall = self._action("Scan All", self._scan_disk_all)
        self.btn_clean = self._action("Clean (Repair / Quarantine)", self._clean_disk)
        self.btn_cleanscene = self._action("Clean Scene Nodes", self._clean_scene)
        self.btn_cleanjobs = self._action("Clean Suspicious Jobs", self._clean_jobs)
        self.btn_immunize = self._action("Immunize (Startup Guard)", self._immunize)
        self.btn_listjobs = self._action("List ScriptJobs", self._list_jobs)
        self.btn_scanfile = self._action("Scan Scene File...", self._scan_file)
        self.btn_scanfolder = self._action("Scan Folder...", self._scan_folder)
        self.btn_cleanlast = self._action("Clean Last Scan Hits", self._clean_last_hits)
        self.btn_cleanfile = self._action("Offline Clean File...", self._clean_file)
        self.btn_cleanfolder = self._action("Offline Clean Folder...", self._clean_folder)
        self.btn_quarantine = self._action("Quarantine...", self._show_quarantine)
        for index, button in enumerate(self.action_buttons):
            buttons.addWidget(button, index // 3, index % 3)
        layout.addWidget(actions)

        progress = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QtWidgets.QLabel("")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.setVisible(False)
        progress.addWidget(self.progress_bar, 1)
        progress.addWidget(self.progress_label, 1)
        progress.addWidget(self.btn_cancel)
        layout.addLayout(progress)

        self.log = QtWidgets.QTextBrowser()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        # Without setOpenLinks(False) a click navigates away and blanks the log.
        self.log.setOpenLinks(False)
        self.log.setOpenExternalLinks(False)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Detail", "Risk", "Score"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self.log)
        splitter.addWidget(self.tree)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        self.btn_clearlog = QtWidgets.QPushButton("Clear Log")
        footer.addWidget(self.btn_clearlog)
        footer.addStretch(1)
        footer.addWidget(QtWidgets.QLabel("MayaShield %s" % __version__))
        layout.addLayout(footer)

    def _action(self, label, handler):
        button = QtWidgets.QPushButton(label)
        button.clicked.connect(lambda *_: handler())
        self.action_buttons.append(button)
        return button

    def _connect(self):
        self.btn_clearlog.clicked.connect(lambda *_: self.log.clear())
        self.btn_browse.clicked.connect(lambda *_: self._browse_report())
        self.btn_cancel.clicked.connect(lambda *_: self._cancel_job())
        self.log.anchorClicked.connect(self._on_log_link)
        self.tree.itemDoubleClicked.connect(self._on_tree_item)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)

    # ------------------------------------------------------------------ helpers
    def _append(self, text):
        self.log.append(text)

    def _note(self, text):
        self._append("<span style='color:#888'>%s</span>" % html.escape(str(text)))

    def _threshold(self):
        return rules.RISK_MEDIUM if self.chk_medium.isChecked() else rules.RISK_HIGH

    def _report_path(self):
        return self.le_report.text().strip() or None

    def _save_options(self):
        guard.set_option_bool(integration.OPT_DRYRUN, self.chk_dry.isChecked())
        guard.set_option_bool(integration.OPT_VERBOSE, self.chk_verbose.isChecked())
        cmds.optionVar(sv=(integration.OPT_REPORT, self.le_report.text().strip()))

    def _write_report(self, payload):
        path = self._report_path()
        if path:
            try:
                self._note("Report written: %s" % scanner.write_json(path, payload))
            except (IOError, OSError) as error:
                self._append("[ERROR] Could not write the report: %s" % html.escape(str(error)))

    def _heading(self, text):
        self._append("<br><b>[%s] %s</b>" % (scanner.now(), html.escape(text)))

    def _browse_report(self):
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self, "Save Report JSON", "", "JSON (*.json)")
        if path:
            self.le_report.setText(path)

    def _on_log_link(self, url):
        action, value = _parse_link(url.toString())
        if action == "reveal":
            reveal_path(value)
        elif action == "selectNode":
            select_node(value)

    # ------------------------------------------------------------------ background jobs
    def _start_job(self, label, function, on_done):
        if self.worker is not None:
            self._note("Another job is still running.")
            return
        self._save_options()
        worker = _Worker(function, self)
        self.worker = worker
        self.started = time.time()
        worker.progressed.connect(self._on_progress)
        worker.done.connect(lambda result: self._finish_job(on_done, result))
        worker.failed.connect(self._on_job_failed)
        worker.finished.connect(worker.deleteLater)
        self.progress_label.setText(label)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        for button in self.action_buttons:
            button.setEnabled(False)
        worker.start()

    def _on_progress(self, name, index, total):
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(index)
            self.progress_label.setText("%d / %d  %s" % (index, total, name))
        else:
            self.progress_label.setText(name)

    def _end_job(self):
        self.worker = None
        self.progress_bar.setVisible(False)
        self.btn_cancel.setVisible(False)
        self.progress_label.setText("")
        for button in self.action_buttons:
            button.setEnabled(True)
        self._update_last_scan_button()

    def _finish_job(self, on_done, result):
        elapsed = time.time() - self.started
        self._end_job()
        try:
            on_done(result)
            self._note("Finished in %.1f s." % elapsed)
        except Exception as error:
            self._append("[ERROR] %s" % html.escape(str(error)))

    def _on_job_failed(self, message):
        self._end_job()
        self._append("<span style='color:#c33'>[ERROR] %s</span>" % html.escape(message))

    def _cancel_job(self):
        if self.worker is not None:
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.progress_label.setText("Canceling...")

    # ------------------------------------------------------------------ disk scan
    def _scan_disk_quick(self):
        self._scan_disk(False)

    def _scan_disk_all(self):
        self._scan_disk(True)

    def _scan_disk(self, scan_all):
        verbose = self.chk_verbose.isChecked()
        env = mayapaths.snapshot()
        label = "Scan All" if scan_all else "Scan"
        self._heading(label)
        self._start_job(label + "...", lambda _w: persistence.scan_disk(env=env, scan_all=scan_all, verbose=verbose),
                        self._show_disk_report)

    def _clean_disk(self):
        dry_run = self.chk_dry.isChecked()
        verbose = self.chk_verbose.isChecked()
        threshold = self._threshold()
        env = mayapaths.snapshot()
        self._heading("Clean (dry run)" if dry_run else "Clean")
        # The env gathered here is what lets this run without touching maya.cmds.
        self._start_job("Cleaning...", lambda _w: persistence.clean_disk(
            env=env, dry_run=dry_run, threshold=threshold, verbose=verbose), self._show_disk_report)

    def _show_disk_report(self, report):
        self._note("%d file(s) checked." % report.get("filesScanned", 0))
        hits = report.get("hits", [])
        if not hits:
            self._append("<span style='color:#4a4'>No suspicious files.</span>")
        for hit in hits[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;<span style='color:%s'>%s %d</span> <a href='%s'>%s</a> "
                         "<span style='color:#888'>[%s]</span>" % (
                             self._css_color(hit["confidence"]), hit["confidence"], hit["score"],
                             _link("reveal", hit["path"]), html.escape(hit["path"]),
                             html.escape(", ".join(hit["reasons"][:4]))))
        for action in report.get("actions", [])[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(action["status"]), html.escape(action["src"])))
        for note in report.get("notes", [])[:_MAX_RENDERED]:
            self._note(note)
        self._fill_tree_disk(hits)
        self._write_report(report)

    # ------------------------------------------------------------------ live scene
    def _clean_scene(self):
        self._save_options()
        dry_run = self.chk_dry.isChecked()
        self._heading("Clean Scene Nodes (dry run)" if dry_run else "Clean Scene Nodes")
        report = live.clean_scene(dry_run=dry_run, threshold=self._threshold())
        hits = report["sceneHits"]
        if not hits:
            self._append("<span style='color:#4a4'>No suspicious nodes in the open scene.</span>")
        for hit in hits[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;<span style='color:%s'>%s %d</span> <a href='%s'>%s</a> "
                         "<span style='color:#888'>[%s]</span>" % (
                             self._css_color(hit["confidence"]), hit["confidence"], hit["score"],
                             _link("selectNode", hit["node"]), html.escape(hit["node"]), html.escape(hit["reason"])))
        for item in report["deleted"]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(item["status"]), html.escape(item["node"])))
        self._write_report(report)

    def _clean_jobs(self):
        self._save_options()
        dry_run = self.chk_dry.isChecked()
        self._heading("Clean Suspicious Jobs (dry run)" if dry_run else "Clean Suspicious Jobs")
        report = live.clean_jobs(dry_run=dry_run)
        if not report["jobHits"] and not report["runtimeHits"]:
            self._append("<span style='color:#4a4'>No suspicious jobs or modules.</span>")
        for hit in report["jobHits"][:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;#%s %s" % (hit.get("id"), html.escape(str(hit.get("desc"))[:300])))
        for item in report["killed"]:
            self._append("&nbsp;&nbsp;<b>%s</b> job #%s" % (html.escape(item["status"]), item["id"]))
        for item in report["unloaded"]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(item["status"]), html.escape(item["name"])))
        for note in report["notes"]:
            self._note(note)
        self._write_report(report)

    def _immunize(self):
        self._save_options()
        self._heading("Immunize")
        report = live.immunize()
        self._append(html.escape("%s: %s" % (report["status"], report.get("userSetup.py", report.get("reason", "")))))
        self._note("Maya may ask you to confirm the changed userSetup.py at next start. That prompt is expected.")
        self._write_report(report)

    def _list_jobs(self):
        self._heading("ScriptJobs")
        jobs = live.list_script_jobs()
        if not jobs:
            self._note("(none)")
        for job in jobs:
            self._append(html.escape(job))

    # ------------------------------------------------------------------ offline scan
    def _scan_file(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Select Maya Scene", "", "Maya Scene (*.ma *.mb)")
        if path:
            self._scan_paths_job(path, is_folder=False)

    def _scan_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self._scan_paths_job(folder, is_folder=True)

    def _scan_paths_job(self, target, is_folder):
        deep = self.chk_deep.isChecked()
        timeout = self.sb_timeout.value()
        mayapy = mayapaths.find_mayapy()
        report_path = self._report_path()
        self._heading("Offline Folder Scan: %s" % target if is_folder else "Offline Scan: %s" % target)
        self.last_scan_source = "folder scan" if is_folder else "file scan"

        if is_folder:
            def work(worker):
                return scanner.scan_folder(target, report_path=report_path, deep=deep, timeout_sec=timeout, mayapy=mayapy,
                                           interpreter=mayapy, progress_cb=worker.report_progress,
                                           cancel_cb=worker.is_canceled)
        else:
            def work(_worker):
                return scanner.scan_file(target, report_path=report_path, deep=deep, timeout_sec=timeout, mayapy=mayapy)
        self._start_job("Scanning...", work, self._show_scene_report)

    def _show_scene_report(self, report):
        self._remember_hits(report)
        if "results" in report:
            summary = report["summary"]
            self._append("Files scanned: %d" % summary["files"])
            self._append("Files flagged: %d" % summary["flagged"])
            if report.get("note"):
                self._note(report["note"])
            for item in report["hits"][:_MAX_RENDERED]:
                self._append("&nbsp;&nbsp;<span style='color:%s'>%s %d</span> <a href='%s'>%s</a> "
                             "<span style='color:#888'>[%s]</span>" % (
                                 self._css_color(item["confidence"]), item["confidence"], item.get("score", 0),
                                 _link("reveal", item["path"]), html.escape(item["path"]),
                                 html.escape(", ".join(item["reasons"][:4]))))
            if not report["hits"]:
                self._append("<span style='color:#4a4'>No suspicious scenes.</span>")
            self._fill_tree_scene(report.get("results", []))
            return
        if report.get("hits"):
            self._render_hits(report)
        else:
            self._append("<span style='color:#4a4'>%s</span>" % html.escape(report.get("note") or "No suspicious content."))
        if report.get("deep_note"):
            self._note(report["deep_note"])
        self._fill_tree_scene([report])

    def _render_hits(self, report):
        self._append("Risk: <span style='color:%s'><b>%s</b></span> (score %d)%s" % (
            self._css_color(report["risk"]), report["risk"], report["score"],
            (" | " + html.escape(", ".join(report["families"]))) if report.get("families") else ""))
        for hit in report["hits"][:_MAX_RENDERED]:
            line = " L%s" % hit["line"] if hit.get("line") else ""
            name = hit.get("node") or hit.get("kind")
            self._append("&nbsp;&nbsp;<span style='color:%s'>%s %s</span> %s%s <span style='color:#888'>[%s]</span> %s" % (
                self._css_color(hit.get("confidence")), hit.get("confidence"), hit.get("score", ""),
                html.escape(str(name)), line, html.escape(str(hit.get("reason"))), html.escape(str(hit.get("detail", "")))))
        self._append("&nbsp;&nbsp;<a href='%s'>Reveal file</a>" % _link("reveal", report.get("path", "")))

    def _remember_hits(self, report):
        results = report.get("results") if "results" in report else [report]
        self.last_hit_files = [item["path"] for item in results or [] if scanner.is_flagged(item)]
        self._update_last_scan_button()

    def _update_last_scan_button(self):
        count = len(self.last_hit_files)
        busy = self.worker is not None
        self.btn_cleanlast.setText("Clean Last Scan Hits (%d)" % count if count else "Clean Last Scan Hits")
        self.btn_cleanlast.setEnabled(count > 0 and not busy)

    # ------------------------------------------------------------------ offline clean
    def _choose_clean_mode(self):
        """How to write results.  A dry run writes nothing, so there's nothing to ask."""
        if self.chk_dry.isChecked():
            self._note("Dry Run is on: nothing will be written. Uncheck it to apply the clean.")
            return "dry"
        return self._ask_clean_mode()

    def _ask_clean_mode(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Offline Clean Mode")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText("How should cleaned scenes be written?")
        box.setInformativeText("A clean copy leaves the original alone. "
                               "Overwriting also keeps the original next to it as *.mayashield_bak.")
        copy_button = box.addButton("Create Clean Copy (Recommended)", QtWidgets.QMessageBox.AcceptRole)
        overwrite_button = box.addButton("Overwrite Original", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        exec_dialog(box)
        clicked = box.clickedButton()
        if clicked == copy_button:
            return "copy"
        if clicked == overwrite_button:
            return "inplace"
        return None

    def _clean_file(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Select Maya Scene", "", "Maya Scene (*.ma *.mb)")
        if not path:
            return
        mode = self._choose_clean_mode()
        if not mode:
            return
        output = None
        if mode == "copy":  # a dry run writes nothing, so it needs no output path
            output, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Cleaned Scene As", cleaner.default_output_path(path), "Maya Scene (*.ma *.mb)")
            if not output:
                return
        threshold, dry_run, report_path = self._threshold(), self.chk_dry.isChecked(), self._report_path()
        self._heading("Offline Clean File: %s" % path)

        def work(worker):
            return cleaner.clean_file(path, output_path=output, in_place=mode == "inplace", threshold=threshold,
                                      dry_run=dry_run, report_path=report_path,
                                      progress_cb=lambda stage, _percent, detail="": worker.report_progress(stage, 0, 0))
        self._start_job("Cleaning...", work, self._show_clean_report)

    def _show_clean_report(self, report):
        if report.get("hits"):
            self._render_hits_from_clean(report)
        for item in report.get("removed", [])[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;%s: %s" % (html.escape(item.get("status", "")), html.escape(str(item.get("node")))))
        style = "#4a4" if report.get("cleaned") and not report.get("remaining_hits_count") else "#c93"
        self._append("<span style='color:%s'>%s</span>" % (style, html.escape(report.get("note", ""))))
        if report.get("output_path"):
            self._append("&nbsp;&nbsp;Output: <a href='%s'>%s</a>" % (
                _link("reveal", report["output_path"]), html.escape(report["output_path"])))
        if report.get("backup_path"):
            self._note("Original kept as %s" % report["backup_path"])
        self._fill_tree_scene([report])

    def _render_hits_from_clean(self, report):
        report = dict(report, risk=report.get("risk", "info"), score=report.get("score", 0))
        self._render_hits(report)

    def _clean_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        mode = self._choose_clean_mode()
        if not mode:
            return
        out_root = None
        if mode == "copy":
            out_root = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Output Folder for Cleaned Scenes", os.path.join(folder, "_MAYASHIELD_CLEANED"))
            if not out_root:
                return
        threshold, dry_run, report_path = self._threshold(), self.chk_dry.isChecked(), self._report_path()
        timeout = self.sb_timeout.value()
        mayapy = mayapaths.find_mayapy()
        self._heading("Offline Clean Folder: %s" % folder)

        def work(worker):
            return cleaner.clean_folder(folder, report_path=report_path, output_root=out_root, in_place=mode == "inplace",
                                        threshold=threshold, timeout_sec=timeout, dry_run=dry_run, interpreter=mayapy,
                                        progress_cb=worker.report_progress, cancel_cb=worker.is_canceled)
        self._start_job("Cleaning...", work, self._show_folder_clean_report)

    def _show_folder_clean_report(self, report):
        summary = report["summary"]
        self._append("Files: %d, flagged: %d, cleaned: %d, failed: %d" % (
            summary.get("files", 0), summary.get("flagged", 0), summary.get("cleaned", 0), summary.get("failed", 0)))
        if report.get("note"):
            self._note(report["note"])
        self._fill_tree_scene(report.get("results", []))

    def _clean_last_hits(self):
        files = [path for path in self.last_hit_files if os.path.isfile(path)]
        if not files:
            self._note("No cached scan hits. Scan a file or folder first.")
            return
        mode = self._choose_clean_mode()
        if not mode:
            return
        out_root = None
        if mode == "copy":
            start = os.path.dirname(files[0])
            out_root = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Output Folder", os.path.join(start, "_MAYASHIELD_LASTSCAN_CLEANED"))
            if not out_root:
                return
        threshold, dry_run = self._threshold(), self.chk_dry.isChecked()
        try:
            common = os.path.commonpath(files) if len(files) > 1 else os.path.dirname(files[0])
        except ValueError:
            common = ""
        self._heading("Clean Last Scan Hits (%s): %d file(s)" % (self.last_scan_source, len(files)))

        def work(worker):
            results = []
            for index, path in enumerate(files, 1):
                if worker.is_canceled():
                    break
                worker.report_progress(path, index, len(files))
                output = None
                if out_root:
                    relative = os.path.relpath(path, common) if common else os.path.basename(path)
                    output = os.path.join(out_root, relative)
                results.append(cleaner.clean_file(path, output_path=output, in_place=mode == "inplace",
                                                  threshold=threshold, dry_run=dry_run))
            return {"results": results}

        def done(payload):
            results = payload["results"]
            self._append("Cleaned %d of %d file(s)." % (sum(1 for item in results if item.get("cleaned")), len(results)))
            for item in results:
                self._note("%s: %s" % (item["path"], item.get("note", "")))
            self._fill_tree_scene(results)
            self._write_report(payload)
        self._start_job("Cleaning...", work, done)

    # ------------------------------------------------------------------ quarantine
    def _show_quarantine(self):
        records = persistence.list_quarantine()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("MayaShield Quarantine")
        dialog.resize(760, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTreeWidget()
        table.setHeaderLabels(["Original", "When", "Action", "Why"])
        for record in records:
            item = QtWidgets.QTreeWidgetItem([record["original"], record["time"], record["action"],
                                              ", ".join(record["reasons"][:4])])
            item.setData(0, QtCore.Qt.UserRole, record["id"])
            table.addTopLevelItem(item)
        table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(table)
        row = QtWidgets.QHBoxLayout()
        restore = QtWidgets.QPushButton("Restore Selected")
        close = QtWidgets.QPushButton("Close")
        row.addWidget(restore)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

        def do_restore():
            item = table.currentItem()
            if item is None:
                return
            result = persistence.restore(item.data(0, QtCore.Qt.UserRole))
            if result["status"] == "restored":
                self._note("Restored %s" % result["path"])
                table.takeTopLevelItem(table.indexOfTopLevelItem(item))
            else:
                QtWidgets.QMessageBox.warning(dialog, "MayaShield", result["reason"])

        restore.clicked.connect(lambda *_: do_restore())
        close.clicked.connect(lambda *_: dialog.accept())
        exec_dialog(dialog)

    # ------------------------------------------------------------------ results tree
    def _css_color(self, level):
        red, green, blue = _RISK_COLORS.get(str(level or ""), (140, 140, 140))
        return "#%02x%02x%02x" % (red, green, blue)

    def _color_item(self, item, level):
        red, green, blue = _RISK_COLORS.get(str(level or ""), (140, 140, 140))
        brush = QtGui.QBrush(QtGui.QColor(red, green, blue))
        for column in (0, 2, 3):
            item.setForeground(column, brush)

    def _fill_tree_disk(self, hits):
        self.tree.clear()
        for hit in hits[:_MAX_RENDERED]:
            top = QtWidgets.QTreeWidgetItem([os.path.basename(hit["path"]), hit["path"], hit["confidence"], str(hit["score"])])
            top.setData(0, QtCore.Qt.UserRole, _link("reveal", hit["path"]))
            self._color_item(top, hit["confidence"])
            self.tree.addTopLevelItem(top)
            self._add_evidence(top, hit.get("evidence", []))
        self.tree.expandToDepth(0)

    def _fill_tree_scene(self, reports):
        self.tree.clear()
        for report in reports[:_MAX_RENDERED]:
            hits = report.get("hits") or []
            if not hits:
                continue
            path = report.get("path", "")
            top = QtWidgets.QTreeWidgetItem([os.path.basename(path), path, report.get("risk", ""), str(report.get("score", ""))])
            top.setData(0, QtCore.Qt.UserRole, _link("reveal", path))
            self._color_item(top, report.get("risk"))
            self.tree.addTopLevelItem(top)
            for hit in hits[:_MAX_RENDERED]:
                name = hit.get("node") or hit.get("kind") or "hit"
                line = "[L%s] " % hit["line"] if hit.get("line") else ""
                child = QtWidgets.QTreeWidgetItem([str(name), line + str(hit.get("detail", "")),
                                                   str(hit.get("confidence", "")), str(hit.get("score", ""))])
                self._color_item(child, hit.get("confidence"))
                if hit.get("node"):
                    child.setData(0, QtCore.Qt.UserRole, _link("selectNode", hit["node"]))
                top.addChild(child)
                self._add_evidence(child, hit.get("evidence", []))
        self.tree.expandToDepth(0)

    def _add_evidence(self, parent, evidence):
        for item in evidence:
            parent.addChild(QtWidgets.QTreeWidgetItem([item.get("rule", ""), str(item.get("detail", "")), "", str(item.get("weight", ""))]))

    def _on_tree_item(self, item, _column):
        action, value = _parse_link(item.data(0, QtCore.Qt.UserRole) or "")
        if action == "reveal":
            reveal_path(value)
        elif action == "selectNode":
            select_node(value)

    def _on_tree_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        menu = QtWidgets.QMenu(self.tree)
        expand = menu.addAction("Expand All")
        collapse = menu.addAction("Collapse All")
        menu.addSeparator()
        action, value = _parse_link(item.data(0, QtCore.Qt.UserRole) or "")
        reveal = menu.addAction("Reveal File") if action == "reveal" else None
        select = menu.addAction("Select Node") if action == "selectNode" else None
        copy = menu.addAction("Copy")
        chosen = exec_menu(menu, self.tree.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen == expand:
            self.tree.expandAll()
        elif chosen == collapse:
            self.tree.collapseAll()
        elif chosen == reveal:
            reveal_path(value)
        elif chosen == select:
            select_node(value)
        elif chosen == copy:
            QtWidgets.QApplication.clipboard().setText("%s | %s" % (item.text(0), item.text(1)))

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        super(MayaShieldWindow, self).closeEvent(event)


def _maya_main_window():
    pointer = omui.MQtUtil.mainWindow()
    return wrap_pointer(pointer, QtWidgets.QWidget) if pointer is not None else None


def _embed_in_workspace():
    """Put the panel inside the workspace control.  Safe to call repeatedly."""
    global _window
    pointer = omui.MQtUtil.findControl(WORKSPACE_CONTROL)
    if pointer is None:
        return None
    host = wrap_pointer(pointer, QtWidgets.QWidget)
    if not is_alive(_window):
        _window = MayaShieldWindow(host)
    elif _window.parent() is not host:
        _window.setParent(host)
    _window.setWindowFlags(QtCore.Qt.Widget)
    layout = host.layout()
    if layout is None:
        layout = QtWidgets.QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 2, 2)
    if layout.indexOf(_window) < 0:
        layout.addWidget(_window)
    _window.show()
    return _window


def restore_workspace():
    """Called by the workspace control's uiScript when Maya rebuilds the layout."""
    return _embed_in_workspace()


def show_ui(workspace=True):
    """Show the panel, reusing it if it already exists."""
    global _window
    if workspace:
        if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
            cmds.workspaceControl(WORKSPACE_CONTROL, edit=True, visible=True, restore=True)
        else:
            cmds.workspaceControl(WORKSPACE_CONTROL, label="MayaShield", retain=False, floating=True,
                                  initialWidth=760, initialHeight=720, uiScript=_UI_SCRIPT)
        return _embed_in_workspace()

    if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL)
        _window = None
    if not is_alive(_window):
        _window = MayaShieldWindow(_maya_main_window())
        _window.setWindowFlags(QtCore.Qt.Window)
        _window.resize(760, 720)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def close_ui():
    global _window
    if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL)
    if is_alive(_window):
        _window.close()
        _window.deleteLater()
    _window = None
