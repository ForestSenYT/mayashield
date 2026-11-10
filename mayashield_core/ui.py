# -*- coding: utf-8 -*-
"""The MayaShield panel.

Built for people who are not programmers: one status line, one big button, and
results in plain words.  Everything else lives under "Advanced".

File work runs on a background thread (which mostly waits on worker processes),
so Maya stays usable during a long folder scan.  Anything calling ``maya.cmds``
stays on the main thread.
"""
import html
import os
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.OpenMayaUI as omui

from . import __version__, cleaner, guard, integration, live, mayapaths, persistence, rules, scanner
from .i18n import explain_hit, explain_rule, level_name, status_name, tr
from .qt_compat import QtCore, QtGui, QtWidgets, exec_dialog, exec_menu, is_alive, wrap_pointer

WORKSPACE_CONTROL = "MayaShieldWorkspaceControl"
OBJECT_NAME = "MayaShieldWindow_V4"
_UI_SCRIPT = "import mayashield_core.ui as _ms_ui; _ms_ui.restore_workspace()"
_LINK_SCHEME = "ms://"
_RISK_COLORS = {"high": (224, 82, 82), "medium": (230, 160, 60), "low": (90, 160, 225), "error": (200, 110, 220)}
_BANNER_COLORS = {"good": "#2e7d4f", "bad": "#b23b3b", "review": "#a8741a", "idle": "#4a4f57", "error": "#7a4a8c"}
_MAX_RENDERED = 300
_LEVEL_ROLE = QtCore.Qt.UserRole + 1

_window = None
_threads = set()   # running jobs: they belong to no window, so closing one cannot destroy a running thread


def _link(action, value):
    return "%s%s?v=%s" % (_LINK_SCHEME, action, quote(str(value), safe=""))


def _parse_link(url):
    """(action, value) of a link made by _link.  The action comes back lower-case:

    Qt treats it as a host name and lower-cases it on the way through a QUrl.
    """
    text = str(url)
    if not text.lower().startswith(_LINK_SCHEME) or "?" not in text:
        return None, None
    action, query = text[len(_LINK_SCHEME):].split("?", 1)
    return action.strip("/").lower(), parse_qs(query, keep_blank_values=True).get("v", [""])[0]


def _follow_link(link):
    action, value = _parse_link(link)
    if action == "reveal":
        reveal_path(value)
    elif action == "selectnode":
        select_node(value)
    return action


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
        om.MGlobal.displayWarning(tr("reveal.failed", target, error))


def select_node(node):
    if cmds.objExists(node):
        cmds.select(node, replace=True)
    else:
        om.MGlobal.displayWarning(tr("node.missing", node))


class _Worker(QtCore.QThread):
    """Runs one function off the UI thread.  Results come back as signals."""
    progressed = QtCore.Signal(str, int, int)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function):
        # No parent: Qt aborts the whole program if a thread is destroyed while it runs,
        # and a panel can be closed, re-docked or deleted at any moment.
        super(_Worker, self).__init__()
        self.function = function
        self.canceled = False
        _threads.add(self)
        self.finished.connect(self._retire)

    def _retire(self):
        _threads.discard(self)
        self.deleteLater()

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
        self.last_kind = None        # what "Fix Now" acts on: "checkup", "files" or None
        self.last_checkup = None
        self.action_buttons = []
        self._rendered = None        # (function, argument) that filled the list, to redo it on a toggle
        self._build()
        self._connect()
        self.refresh_status()
        self._banner("idle", tr("result.idle"), "")
        self._update_last_scan_button()

    # ------------------------------------------------------------------ layout
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        # ---- status
        status = QtWidgets.QHBoxLayout()
        self.status_dot = QtWidgets.QLabel()
        self.status_dot.setFixedSize(12, 12)
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(0)
        self.status_title = QtWidgets.QLabel()
        title_font = self.status_title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        self.status_title.setFont(title_font)
        self.status_detail = QtWidgets.QLabel()
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("color: #9aa0a6;")
        text.addWidget(self.status_title)
        text.addWidget(self.status_detail)
        dot = QtWidgets.QVBoxLayout()
        dot.setContentsMargins(0, 5, 0, 0)
        dot.addWidget(self.status_dot)
        dot.addStretch(1)
        status.addLayout(dot)
        status.addLayout(text, 1)
        layout.addLayout(status)

        # ---- the one button
        self.btn_checkup = self._action(tr("checkup"), self.start_checkup)
        self.btn_checkup.setMinimumHeight(46)
        big = self.btn_checkup.font()
        big.setBold(True)
        big.setPointSize(big.pointSize() + 3)
        self.btn_checkup.setFont(big)
        self.btn_checkup.setStyleSheet(
            "QPushButton { background-color: #3d7edb; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background-color: #4c8dea; }"
            "QPushButton:pressed { background-color: #2f69bd; }"
            "QPushButton:disabled { background-color: #4a4f57; color: #9aa0a6; }")
        layout.addWidget(self.btn_checkup)
        self.caption = QtWidgets.QLabel(tr("checkup.caption"))
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet("color: #9aa0a6;")
        layout.addWidget(self.caption)

        row = QtWidgets.QHBoxLayout()
        self.btn_scanfile = self._action(tr("scan.file"), self._scan_file, tr("scan.file.tip"))
        self.btn_scanfolder = self._action(tr("scan.folder"), self._scan_folder, tr("scan.folder.tip"))
        row.addWidget(self.btn_scanfile)
        row.addWidget(self.btn_scanfolder)
        layout.addLayout(row)

        # ---- progress
        progress = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(10)
        self.progress_label = QtWidgets.QLabel("")
        self.btn_cancel = QtWidgets.QPushButton(tr("cancel"))
        self.btn_cancel.setVisible(False)
        progress.addWidget(self.progress_bar, 1)
        progress.addWidget(self.progress_label, 1)
        progress.addWidget(self.btn_cancel)
        layout.addLayout(progress)

        # ---- result headline
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("MayaShieldBanner")
        banner_layout = QtWidgets.QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_text = QtWidgets.QVBoxLayout()
        banner_text.setSpacing(2)
        self.banner_title = QtWidgets.QLabel()
        headline = self.banner_title.font()
        headline.setBold(True)
        headline.setPointSize(headline.pointSize() + 1)
        self.banner_title.setFont(headline)
        self.banner_title.setWordWrap(True)
        self.banner_detail = QtWidgets.QLabel()
        self.banner_detail.setWordWrap(True)
        self.banner_title.setStyleSheet("color: white; background: transparent;")
        self.banner_detail.setStyleSheet("color: #e8eaed; background: transparent;")
        banner_text.addWidget(self.banner_title)
        banner_text.addWidget(self.banner_detail)
        self.btn_fix = QtWidgets.QPushButton(tr("fix"))
        self.btn_fix.setMinimumHeight(30)
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(lambda *_: self._fix())
        self.action_buttons.append(self.btn_fix)
        banner_layout.addLayout(banner_text, 1)
        banner_layout.addWidget(self.btn_fix, 0, QtCore.Qt.AlignVCenter)
        layout.addWidget(self.banner)

        # ---- results
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels([tr("col.what"), tr("col.where"), tr("col.level")])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.setMinimumHeight(90)
        self.tree.setVisible(False)
        layout.addWidget(self.tree, 3)

        # ---- advanced
        self.btn_advanced = QtWidgets.QToolButton()
        self.btn_advanced.setText(tr("advanced"))
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.btn_advanced.setArrowType(QtCore.Qt.RightArrow)
        self.btn_advanced.setStyleSheet("QToolButton { border: none; }")
        layout.addWidget(self.btn_advanced)

        self.advanced = QtWidgets.QScrollArea()
        self.advanced.setWidgetResizable(True)
        self.advanced.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.advanced.setVisible(False)
        inner = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(inner)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced.setWidget(inner)
        layout.addWidget(self.advanced, 4)

        protect = QtWidgets.QGroupBox(tr("adv.protect"))
        protect_layout = QtWidgets.QVBoxLayout(protect)
        self.chk_guard_open = QtWidgets.QCheckBox(tr("adv.guard.open"))
        self.chk_guard_open.setToolTip(tr("adv.guard.open.tip"))
        self.chk_guard_startup = QtWidgets.QCheckBox(tr("adv.guard.startup"))
        self.chk_guard_startup.setToolTip(tr("adv.guard.startup.tip"))
        protect_layout.addWidget(self.chk_guard_open)
        protect_layout.addWidget(self.chk_guard_startup)
        advanced_layout.addWidget(protect)

        options = QtWidgets.QGroupBox(tr("adv.options"))
        grid = QtWidgets.QGridLayout(options)
        self.chk_dry = QtWidgets.QCheckBox(tr("adv.preview"))
        self.chk_dry.setToolTip(tr("adv.preview.tip"))
        self.chk_dry.setChecked(guard.option_bool(integration.OPT_DRYRUN, False))
        self.chk_medium = QtWidgets.QCheckBox(tr("adv.medium"))
        self.chk_medium.setToolTip(tr("adv.medium.tip"))
        self.chk_deep = QtWidgets.QCheckBox(tr("adv.deep"))
        self.chk_deep.setToolTip(tr("adv.deep.tip"))
        self.chk_verbose = QtWidgets.QCheckBox(tr("adv.details"))
        self.chk_verbose.setToolTip(tr("adv.details.tip"))
        self.chk_verbose.setChecked(guard.option_bool(integration.OPT_VERBOSE, False))
        self.le_report = QtWidgets.QLineEdit()
        self.le_report.setPlaceholderText(tr("adv.report.hint"))
        if cmds.optionVar(exists=integration.OPT_REPORT):
            self.le_report.setText(str(cmds.optionVar(q=integration.OPT_REPORT) or ""))
        self.btn_browse = QtWidgets.QPushButton(tr("adv.browse"))
        self.sb_timeout = QtWidgets.QSpinBox()
        self.sb_timeout.setRange(10, 3600)
        self.sb_timeout.setValue(120)
        grid.addWidget(self.chk_dry, 0, 0)
        grid.addWidget(self.chk_medium, 0, 1)
        grid.addWidget(self.chk_deep, 1, 0)
        grid.addWidget(self.chk_verbose, 1, 1)
        grid.addWidget(QtWidgets.QLabel(tr("adv.timeout")), 2, 0)
        grid.addWidget(self.sb_timeout, 2, 1)
        grid.addWidget(QtWidgets.QLabel(tr("adv.report")), 3, 0)
        report_row = QtWidgets.QHBoxLayout()
        report_row.addWidget(self.le_report, 1)
        report_row.addWidget(self.btn_browse)
        grid.addLayout(report_row, 3, 1)
        advanced_layout.addWidget(options)

        tools = QtWidgets.QGroupBox(tr("adv.tools"))
        tool_grid = QtWidgets.QGridLayout(tools)
        self.btn_scan = self._action(tr("tool.scanall"), self._scan_disk_all, tr("tool.scanall.tip"))
        self.btn_scanall = self.btn_scan
        self.btn_clean = self._action(tr("tool.cleandisk"), self._clean_disk, tr("tool.cleandisk.tip"))
        self.btn_cleanscene = self._action(tr("tool.cleanscene"), self._clean_scene, tr("tool.cleanscene.tip"))
        self.btn_cleanjobs = self._action(tr("tool.cleanjobs"), self._clean_jobs, tr("tool.cleanjobs.tip"))
        self.btn_cleanfile = self._action(tr("tool.cleanfile"), self._clean_file)
        self.btn_cleanfolder = self._action(tr("tool.cleanfolder"), self._clean_folder)
        self.btn_cleanlast = self._action(tr("tool.cleanlast"), self._clean_last_hits)
        self.btn_quarantine = self._action(tr("tool.quarantine"), self._show_quarantine, tr("tool.quarantine.tip"))
        self.btn_listjobs = self._action(tr("tool.listjobs"), self._list_jobs)
        self.btn_immunize = self._action(tr("tool.immunize"), self._immunize, tr("tool.immunize.tip"))
        tool_buttons = (self.btn_scan, self.btn_clean, self.btn_cleanscene, self.btn_cleanjobs, self.btn_cleanfile,
                        self.btn_cleanfolder, self.btn_cleanlast, self.btn_quarantine, self.btn_listjobs,
                        self.btn_immunize)
        for index, button in enumerate(tool_buttons):
            tool_grid.addWidget(button, index // 2, index % 2)
        advanced_layout.addWidget(tools)

        self.log = QtWidgets.QTextBrowser()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        # Without setOpenLinks(False) a click navigates away and blanks the log.
        self.log.setOpenLinks(False)
        self.log.setOpenExternalLinks(False)
        advanced_layout.addWidget(self.log, 1)

        footer = QtWidgets.QHBoxLayout()
        self.btn_clearlog = QtWidgets.QPushButton(tr("log.clear"))
        footer.addWidget(self.btn_clearlog)
        footer.addStretch(1)
        advanced_layout.addLayout(footer)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        version = QtWidgets.QLabel("MayaShield %s" % __version__)
        version.setStyleSheet("color: #80868b;")
        bottom.addWidget(version)
        layout.addLayout(bottom)
        self._filler = QtWidgets.QWidget()
        layout.addWidget(self._filler, 1)

    def _action(self, label, handler, tooltip=""):
        button = QtWidgets.QPushButton(label)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(lambda *_: handler())
        self.action_buttons.append(button)
        return button

    def _connect(self):
        self.btn_clearlog.clicked.connect(lambda *_: self.log.clear())
        self.btn_browse.clicked.connect(lambda *_: self._browse_report())
        self.btn_cancel.clicked.connect(lambda *_: self._cancel_job())
        self.btn_advanced.toggled.connect(self._toggle_advanced)
        self.chk_verbose.toggled.connect(lambda *_: self._rerender())
        self.chk_guard_open.clicked.connect(lambda *_: self._save_guards())
        self.chk_guard_startup.clicked.connect(lambda *_: self._save_guards())
        self.log.anchorClicked.connect(self._on_log_link)
        self.tree.itemDoubleClicked.connect(self._on_tree_item)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)

    def _toggle_advanced(self, shown):
        self.advanced.setVisible(bool(shown))
        self.btn_advanced.setArrowType(QtCore.Qt.DownArrow if shown else QtCore.Qt.RightArrow)
        self._layout_stretch()

    def _layout_stretch(self):
        # Whatever is showing shares the height; the filler takes it when nothing is.
        self._filler.setVisible(not self.tree.isVisibleTo(self) and not self.advanced.isVisibleTo(self))

    # ------------------------------------------------------------------ status
    def refresh_status(self):
        """Show whether protection is on.  Called again whenever a setting changes."""
        on_open = guard.option_bool(guard.OPT_RUNTIME)
        on_start = guard.option_bool(guard.OPT_STARTUP)
        self.chk_guard_open.setChecked(on_open)
        self.chk_guard_startup.setChecked(on_start)
        if on_open and on_start:
            key, color = "status.on", "#35b36b"
        elif on_open or on_start:
            key, color = "status.partial", "#e6a03c"
        else:
            key, color = "status.off", "#e05252"
        self.status_title.setText(tr(key))
        self.status_detail.setText(tr(key + ".detail"))
        self.status_dot.setStyleSheet("background-color: %s; border-radius: 6px;" % color)

    def _save_guards(self):
        guard.set_option_bool(guard.OPT_RUNTIME, self.chk_guard_open.isChecked())
        guard.set_option_bool(guard.OPT_STARTUP, self.chk_guard_startup.isChecked())
        self.refresh_status()

    def _banner(self, kind, title, detail, fix_label=None):
        self.banner.setStyleSheet("QFrame#MayaShieldBanner { background-color: %s; border-radius: 5px; }"
                                  % _BANNER_COLORS.get(kind, _BANNER_COLORS["idle"]))
        self.banner_title.setText(title)
        self.banner_detail.setText(detail)
        self.banner_detail.setVisible(bool(detail))
        self.btn_fix.setVisible(bool(fix_label))
        if fix_label:
            self.btn_fix.setText(fix_label)

    # ------------------------------------------------------------------ helpers
    def _append(self, text):
        self.log.append(text)

    def _note(self, text):
        self._append("<span style='color:#888'>%s</span>" % html.escape(str(text)))

    def _threshold(self):
        return rules.RISK_MEDIUM if self.chk_medium.isChecked() else rules.RISK_HIGH

    def _details(self):
        return self.chk_verbose.isChecked()

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
                self._note(tr("log.report", scanner.write_json(path, payload)))
            except (IOError, OSError) as error:
                self._append("[ERROR] %s" % html.escape(tr("log.report.failed", error)))

    def _heading(self, text):
        self._append("<br><b>[%s] %s</b>" % (scanner.now(), html.escape(text)))

    def _browse_report(self):
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self, tr("adv.report.pick"), "", "JSON (*.json)")
        if path:
            self.le_report.setText(path)

    def _on_log_link(self, url):
        _follow_link(url.toString())

    def _css_color(self, level):
        red, green, blue = _RISK_COLORS.get(str(level or ""), (150, 150, 150))
        return "#%02x%02x%02x" % (red, green, blue)

    def _log_hit(self, level, score, link, label, reasons):
        self._append("&nbsp;&nbsp;<span style='color:%s'>%s %s</span> <a href='%s'>%s</a> "
                     "<span style='color:#888'>[%s]</span>" % (
                         self._css_color(level), html.escape(level_name(level)), score, link, html.escape(str(label)),
                         html.escape(", ".join(str(reason) for reason in reasons))))

    # ------------------------------------------------------------------ background jobs
    def _start_job(self, label, function, on_done, can_cancel=False):
        if self.worker is not None:
            self._note(tr("busy.other"))
            return False
        self._save_options()
        worker = _Worker(function)
        self.worker = worker
        self.started = time.time()
        # The job may outlive this panel.  Its results are then simply dropped.
        worker.progressed.connect(lambda *args: is_alive(self) and self._on_progress(*args))
        worker.done.connect(lambda result: is_alive(self) and self._finish_job(on_done, result))
        worker.failed.connect(lambda message: is_alive(self) and self._on_job_failed(message))
        self.progress_label.setText(label)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.btn_cancel.setVisible(bool(can_cancel))   # only where stopping really stops something
        self.btn_cancel.setEnabled(True)
        for button in self.action_buttons:
            button.setEnabled(False)
        worker.start()
        return True

    def _on_progress(self, name, index, total):
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(index)
            self.progress_label.setText(tr("progress.count", index, total, name))
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
            self._note(tr("done.seconds", elapsed))
        except Exception as error:
            self._append("[ERROR] %s" % html.escape(str(error)))
            self._banner("error", tr("result.error"), str(error))

    def _on_job_failed(self, message):
        self._end_job()
        self._append("<span style='color:#c33'>[ERROR] %s</span>" % html.escape(message))
        self._banner("error", tr("result.error"), message)

    def _cancel_job(self):
        if self.worker is not None:
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.progress_label.setText(tr("busy.cancel"))

    # ------------------------------------------------------------------ one-click check
    def start_checkup(self, fixed=None):
        """Check the open scene, background tasks and startup files.  Changes nothing."""
        if self.worker is not None:
            self._note(tr("busy.other"))
            return
        if fixed is None:
            self._heading(tr("heading.checkup"))
        env = mayapaths.snapshot()
        verbose = self._details()
        # The scene and the job list are Maya's own data: main thread only, and quick.
        scene = live.scan_scene(verbose=verbose)
        jobs = live.scan_jobs(verbose=verbose)

        def work(_worker):
            return {"scene": scene, "jobs": jobs, "disk": persistence.scan_disk(env=env, scan_all=False, verbose=verbose),
                    "fixed": fixed}
        self._start_job(tr("busy.checkup"), work, self._show_checkup)

    def _checkup_problems(self, result):
        """Flatten a check into rows: (level, what, where, link, technical evidence, hit)."""
        rows = []
        for hit in result["scene"].get("sceneHits", ()):
            if hit["score"] < rules.RISK_MEDIUM and not self._details():
                continue
            rows.append({"level": hit.get("confidence"), "what": explain_hit(hit),
                         "where": tr("where.node", hit["node"]), "link": _link("selectNode", hit["node"]),
                         "evidence": hit.get("evidence", ()), "kind": "node", "hit": hit})
        for hit in result["jobs"].get("jobHits", ()):
            what = explain_hit(hit)
            rows.append({"level": hit.get("confidence"), "what": "%s: %s" % (tr("what.job"), what),
                         "where": tr("where.job", hit.get("id")), "link": "", "evidence": hit.get("evidence", ()),
                         "kind": "job", "hit": hit, "tip": str(hit.get("desc", ""))[:600]})
        for hit in result["jobs"].get("runtimeHits", ()):
            rows.append({"level": "high", "what": tr("what.running"), "where": "%s (%s)" % (
                tr("where.session"), hit["name"]), "link": "", "evidence": (), "kind": "runtime", "hit": hit})
        for hit in result["disk"].get("hits", ()):
            rows.append({"level": hit.get("confidence"), "what": explain_hit(hit),
                         "where": os.path.basename(hit["path"]), "link": _link("reveal", hit["path"]),
                         "evidence": hit.get("evidence", ()), "kind": "disk", "hit": hit, "tip": hit["path"]})
        return rows

    def _show_checkup(self, result):
        self.last_checkup = result
        self.last_kind = "checkup"
        rows = self._checkup_problems(result)
        dangerous = [row for row in rows if row["level"] == "high"]
        review = [row for row in rows if row["level"] == "medium"]
        fixed = result.get("fixed")

        for row in rows:
            self._log_hit(row["level"], row["hit"].get("score", ""), row["link"], row["where"],
                          [item.get("rule") for item in list(row["evidence"])[:4]] or [row["what"]])
        for note in list(result["disk"].get("notes", ()))[:_MAX_RENDERED]:
            self._note(note)
        self._note(tr("log.checked", result["disk"].get("filesScanned", 0)))

        fixable = [row for row in rows if row["hit"].get("score", 100) >= self._threshold() or row["kind"] == "runtime"]
        if fixed is not None:
            advice = " ".join(fixed["advice"])
            if dangerous:
                self._banner("bad", tr("result.fixed.partial", fixed["count"], len(dangerous)),
                             (tr("result.fixed.partial.detail") + " " + advice).strip(), tr("fix") if fixable else None)
            else:
                self._banner("good", tr("result.fixed", fixed["count"]),
                             (tr("result.fixed.detail") + " " + advice).strip())
        elif dangerous:
            self._banner("bad", tr("result.bad", len(dangerous)), tr("result.bad.detail"), tr("fix"))
        elif review:
            self._banner("review", tr("result.review", len(review)), tr("result.review.detail"),
                         tr("fix") if self.chk_medium.isChecked() else None)
        else:
            self._banner("good", tr("result.clean"),
                         tr("result.clean.detail", result["disk"].get("filesScanned", 0)))
        self._fill_rows(rows)
        self._rendered = (self._show_checkup_again, result)
        self._write_report({"scene": result["scene"], "jobs": result["jobs"], "disk": result["disk"]})

    def _show_checkup_again(self, result):
        self._fill_rows(self._checkup_problems(result))

    def _fix(self):
        if self.last_kind == "files":
            self._clean_last_hits(preview=False)
        elif self.last_kind == "checkup" and self.last_checkup is not None:
            self._fix_checkup()

    def _fix_checkup(self):
        result = self.last_checkup
        threshold = self._threshold()
        nodes = [hit for hit in result["scene"].get("sceneHits", ()) if hit["score"] >= threshold]
        jobs = list(result["jobs"].get("jobHits", ()))
        running = list(result["jobs"].get("runtimeHits", ()))
        files = [hit for hit in result["disk"].get("hits", ()) if hit["score"] >= threshold]
        lines = []
        if nodes:
            lines.append(tr("fix.nodes", len(nodes)))
        if jobs:
            lines.append(tr("fix.jobs", len(jobs)))
        if running:
            lines.append(tr("fix.runtime"))
        if files:
            lines.append(tr("fix.disk", len(files)))
        if not lines:
            self._note(tr("fix.nothing"))
            return
        if not self._confirm_fix(lines):
            return

        self._heading(tr("heading.fix"))
        advice = []
        count = 0
        job_report = live.clean_jobs()
        count += sum(1 for item in job_report["killed"] if item["status"] == "killed")
        count += len(job_report["unloaded"])
        scene_report = live.clean_scene(threshold=threshold)
        deleted = sum(1 for item in scene_report["deleted"] if item["status"] == "deleted")
        count += deleted
        for item in scene_report["deleted"]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(status_name(item["status"])), html.escape(item["node"])))
        for item in job_report["killed"]:
            self._append("&nbsp;&nbsp;<b>%s</b> #%s" % (html.escape(status_name(item["status"])), item["id"]))
        if deleted:
            advice.append(tr("advice.save"))
        if running:
            advice.append(tr("advice.restart"))
        env = mayapaths.snapshot()

        def work(_worker):
            return persistence.clean_disk(env=env, threshold=threshold) if files else {"actions": []}

        def done(disk_report):
            total = count
            for action in disk_report.get("actions", ()):
                status = str(action.get("status", ""))
                self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(status_name(status)), html.escape(action["src"])))
                if status in ("repaired", "quarantined"):
                    total += 1
                    if tr("advice.restart") not in advice:
                        advice.append(tr("advice.restart"))
                elif status.startswith("needs administrator") and tr("advice.admin") not in advice:
                    advice.append(tr("advice.admin"))
            # Look again, so what the panel says is what is really there now.
            QtCore.QTimer.singleShot(0, lambda: self.start_checkup(fixed={"count": total, "advice": advice}))
        self._start_job(tr("busy.fix"), work, done)

    def _confirm_fix(self, lines):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("MayaShield")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(tr("fix.title"))
        box.setInformativeText(tr("fix.intro") + "\n" + "\n".join(lines))
        go = box.addButton(tr("fix.go"), QtWidgets.QMessageBox.AcceptRole)
        box.addButton(tr("cancel"), QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(go)
        exec_dialog(box)
        return box.clickedButton() == go

    # ------------------------------------------------------------------ advanced: disk
    def _scan_disk_quick(self):
        self._scan_disk(False)

    def _scan_disk_all(self):
        self._scan_disk(True)

    def _scan_disk(self, scan_all):
        verbose = self._details()
        env = mayapaths.snapshot()
        self._heading(tr("heading.scanall") if scan_all else tr("heading.checkup"))
        self._start_job(tr("busy.checkup"),
                        lambda _w: persistence.scan_disk(env=env, scan_all=scan_all, verbose=verbose),
                        self._show_disk_report)

    def _clean_disk(self):
        dry_run = self.chk_dry.isChecked()
        verbose = self._details()
        threshold = self._threshold()
        env = mayapaths.snapshot()
        self._heading(tr("heading.cleandisk") + (tr("heading.preview") if dry_run else ""))
        # The env gathered here is what lets this run without touching maya.cmds.
        self._start_job(tr("busy.fix"), lambda _w: persistence.clean_disk(
            env=env, dry_run=dry_run, threshold=threshold, verbose=verbose), self._show_disk_report)

    def _show_disk_report(self, report):
        self.last_kind = None
        self._note(tr("log.checked", report.get("filesScanned", 0)))
        hits = report.get("hits", [])
        if not hits:
            self._append("<span style='color:#4a4'>%s</span>" % html.escape(tr("log.none.disk")))
        for hit in hits[:_MAX_RENDERED]:
            self._log_hit(hit["confidence"], hit["score"], _link("reveal", hit["path"]), hit["path"], hit["reasons"][:4])
        for action in report.get("actions", [])[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(status_name(action["status"])), html.escape(action["src"])))
        for note in report.get("notes", [])[:_MAX_RENDERED]:
            self._note(note)
        dangerous = [hit for hit in hits if hit["score"] >= rules.RISK_HIGH]
        if dangerous:
            self._banner("bad", tr("result.bad", len(dangerous)), "")
        elif hits:
            self._banner("review", tr("result.review", len(hits)), tr("result.review.detail"))
        else:
            self._banner("good", tr("result.clean"), tr("result.clean.files", report.get("filesScanned", 0)))
        self._fill_tree_disk(hits)
        self._rendered = (self._fill_tree_disk, hits)
        self._write_report(report)

    # ------------------------------------------------------------------ advanced: live scene
    def _clean_scene(self):
        self._save_options()
        dry_run = self.chk_dry.isChecked()
        self._heading(tr("heading.cleanscene") + (tr("heading.preview") if dry_run else ""))
        report = live.clean_scene(dry_run=dry_run, threshold=self._threshold())
        hits = report["sceneHits"]
        if not hits:
            self._append("<span style='color:#4a4'>%s</span>" % html.escape(tr("log.none.scene")))
        for hit in hits[:_MAX_RENDERED]:
            self._log_hit(hit["confidence"], hit["score"], _link("selectNode", hit["node"]), hit["node"], [hit["reason"]])
        for item in report["deleted"]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(status_name(item["status"])), html.escape(item["node"])))
        self._write_report(report)

    def _clean_jobs(self):
        self._save_options()
        dry_run = self.chk_dry.isChecked()
        self._heading(tr("heading.cleanjobs") + (tr("heading.preview") if dry_run else ""))
        report = live.clean_jobs(dry_run=dry_run)
        if not report["jobHits"] and not report["runtimeHits"]:
            self._append("<span style='color:#4a4'>%s</span>" % html.escape(tr("log.none.jobs")))
        for hit in report["jobHits"][:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;#%s %s" % (hit.get("id"), html.escape(str(hit.get("desc"))[:300])))
        for item in report["killed"]:
            self._append("&nbsp;&nbsp;<b>%s</b> #%s" % (html.escape(status_name(item["status"])), item["id"]))
        for item in report["unloaded"]:
            self._append("&nbsp;&nbsp;<b>%s</b> %s" % (html.escape(status_name(item["status"])), html.escape(item["name"])))
        if report["runtimeHits"] and not dry_run:
            self._note(tr("advice.restart"))
        self._write_report(report)

    def _immunize(self):
        self._save_options()
        self._heading(tr("heading.immunize"))
        report = live.immunize()
        self._append(html.escape("%s: %s" % (report["status"], report.get("userSetup.py", report.get("reason", "")))))
        self._note(tr("log.immunize.note"))
        self._write_report(report)

    def _list_jobs(self):
        self._heading(tr("heading.listjobs"))
        jobs = live.list_script_jobs()
        if not jobs:
            self._note(tr("log.none.listjobs"))
        for job in jobs:
            self._append(html.escape(job))

    # ------------------------------------------------------------------ files and folders
    def _scan_file(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, tr("clean.pick.file"), "", tr("clean.filter"))
        if path:
            self._scan_paths_job(path, is_folder=False)

    def _scan_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, tr("clean.pick.folder"))
        if folder:
            self._scan_paths_job(folder, is_folder=True)

    def _scan_paths_job(self, target, is_folder):
        deep = self.chk_deep.isChecked()
        timeout = self.sb_timeout.value()
        interpreter = mayapaths.find_mayapy()
        # A real Maya only ever opens a file when the deep check was asked for.
        mayapy = interpreter if deep else None
        report_path = self._report_path()
        self._heading(tr("heading.scan", target))

        if is_folder:
            def work(worker):
                return scanner.scan_folder(target, report_path=report_path, deep=deep, timeout_sec=timeout, mayapy=mayapy,
                                           interpreter=interpreter, progress_cb=worker.report_progress,
                                           cancel_cb=worker.is_canceled)
        else:
            def work(_worker):
                return scanner.scan_file(target, report_path=report_path, deep=deep, timeout_sec=timeout, mayapy=mayapy)
        self._start_job(tr("busy.scan"), work, self._show_scene_report, can_cancel=is_folder)

    def show_file_report(self, report):
        """Show a report made elsewhere, such as the guard's note about a file it let through."""
        self._heading(tr("heading.scan", report.get("path", "")))
        self._show_scene_report(report)

    def _show_scene_report(self, report):
        self._remember_hits(report)
        self.last_kind = "files"
        results = report.get("results") if "results" in report else [report]
        results = results or []
        for item in results[:_MAX_RENDERED]:
            if not item.get("hits"):
                continue
            self._log_hit(item.get("risk"), item.get("score", 0), _link("reveal", item.get("path", "")),
                          item.get("path", ""), [hit.get("reason") for hit in item["hits"][:4]])
            for hit in item["hits"][:_MAX_RENDERED]:
                line = " L%s" % hit["line"] if hit.get("line") else ""
                self._append("&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:%s'>%s %s</span> %s%s "
                             "<span style='color:#888'>[%s]</span> %s" % (
                                 self._css_color(hit.get("confidence")), html.escape(level_name(hit.get("confidence"))),
                                 hit.get("score", ""), html.escape(str(hit.get("node") or hit.get("kind"))), line,
                                 html.escape(str(hit.get("reason"))), html.escape(str(hit.get("detail", "")))))
        if "results" in report:
            self._append(html.escape(tr("log.files", report["summary"]["files"], report["summary"]["flagged"])))
        if report.get("note") and "results" in report:
            self._note(report["note"])
        if report.get("deep_note"):
            self._note(report["deep_note"])

        dangerous = [item for item in results if item.get("score", 0) >= rules.RISK_HIGH]
        review = [item for item in results if rules.RISK_MEDIUM <= item.get("score", 0) < rules.RISK_HIGH]
        unverified = [item for item in results if item.get("score", 0) < rules.RISK_HIGH and (
            not item.get("parse_ok", True) or any(hit.get("confidence") == "error" for hit in item.get("hits") or ()))]
        canceled = "results" in report and bool(report.get("note"))
        if dangerous:
            self._banner("bad", tr("result.bad.files", len(dangerous), len(results)), tr("result.bad.files.detail"),
                         tr("fix.files"))
        elif unverified:
            self._banner("error", tr("result.unverified", len(unverified)), tr("result.unverified.detail"))
        elif review:
            self._banner("review", tr("result.review", len(review)), tr("result.review.detail"),
                         tr("fix.files") if self.chk_medium.isChecked() else None)
        elif canceled:
            self._banner("idle", tr("result.canceled"), report.get("note", ""))
        else:
            self._banner("good", tr("result.clean"), tr("result.clean.files", len(results)))
        self._fill_tree_scene(results)
        self._rendered = (self._fill_tree_scene, results)

    def _remember_hits(self, report):
        results = report.get("results") if "results" in report else [report]
        self.last_hit_files = [item["path"] for item in results or [] if scanner.is_flagged(item)]
        self._update_last_scan_button()

    def _update_last_scan_button(self):
        count = len(self.last_hit_files)
        busy = self.worker is not None
        self.btn_cleanlast.setText(tr("tool.cleanlast.count", count) if count else tr("tool.cleanlast"))
        self.btn_cleanlast.setEnabled(count > 0 and not busy)

    # ------------------------------------------------------------------ cleaning files
    def _choose_clean_mode(self, preview=None):
        """How to write results.  A preview writes nothing, so there's nothing to ask."""
        if self.chk_dry.isChecked() if preview is None else preview:
            self._note(tr("clean.preview"))
            return "dry"
        return self._ask_clean_mode()

    def _ask_clean_mode(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("MayaShield")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(tr("clean.mode.title"))
        box.setInformativeText(tr("clean.mode.text"))
        copy_button = box.addButton(tr("clean.mode.copy"), QtWidgets.QMessageBox.AcceptRole)
        overwrite_button = box.addButton(tr("clean.mode.inplace"), QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(tr("cancel"), QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(copy_button)
        exec_dialog(box)
        clicked = box.clickedButton()
        if clicked == copy_button:
            return "copy"
        if clicked == overwrite_button:
            return "inplace"
        return None

    def _clean_file(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, tr("clean.pick.file"), "", tr("clean.filter"))
        if not path:
            return
        mode = self._choose_clean_mode()
        if not mode:
            return
        output = None
        if mode == "copy":  # a preview writes nothing, so it needs no output path
            output, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, tr("clean.pick.output"), cleaner.default_output_path(path), tr("clean.filter"))
            if not output:
                return
        threshold, dry_run, report_path = self._threshold(), mode == "dry", self._report_path()
        self._heading(tr("heading.clean", path) + (tr("heading.preview") if dry_run else ""))

        def work(worker):
            return cleaner.clean_file(path, output_path=output, in_place=mode == "inplace", threshold=threshold,
                                      dry_run=dry_run, report_path=report_path,
                                      progress_cb=lambda stage, _percent, detail="": worker.report_progress(stage, 0, 0))
        self._start_job(tr("busy.clean"), work, self._show_clean_report)

    def _show_clean_report(self, report):
        self.last_kind = None
        for item in report.get("removed", [])[:_MAX_RENDERED]:
            self._append("&nbsp;&nbsp;%s: %s" % (html.escape(status_name(item.get("status", ""))),
                                                 html.escape(str(item.get("node")))))
        good = bool(report.get("cleaned")) and not report.get("remaining_hits_count")
        self._append("<span style='color:%s'>%s</span>" % ("#4a4" if good else "#c93", html.escape(report.get("note", ""))))
        detail = []
        if report.get("output_path"):
            self._append("&nbsp;&nbsp;<a href='%s'>%s</a>" % (
                _link("reveal", report["output_path"]), html.escape(tr("clean.output", report["output_path"]))))
            detail.append(tr("clean.output", report["output_path"]))
        if report.get("backup_path"):
            self._note(tr("clean.backup", report["backup_path"]))
            detail.append(tr("clean.backup", report["backup_path"]))
        if good:
            self._banner("good", tr("result.fixed", len(report.get("removed", ()))), " ".join(detail))
        elif report.get("cleaned"):
            self._banner("bad", tr("result.fixed.partial", len(report.get("removed", ())),
                                   report.get("remaining_hits_count", 0)), " ".join(detail))
        else:
            self._banner("idle" if not report.get("hits") or report.get("dry_run") else "review",
                         report.get("note", ""), "")
        self._fill_tree_scene([report])
        self._rendered = (self._fill_tree_scene, [report])

    def _clean_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, tr("clean.pick.folder"))
        if not folder:
            return
        mode = self._choose_clean_mode()
        if not mode:
            return
        out_root = None
        if mode == "copy":
            out_root = QtWidgets.QFileDialog.getExistingDirectory(
                self, tr("clean.pick.outdir"), os.path.join(folder, "_MAYASHIELD_CLEANED"))
            if not out_root:
                return
        threshold, dry_run, report_path = self._threshold(), mode == "dry", self._report_path()
        timeout = self.sb_timeout.value()
        mayapy = mayapaths.find_mayapy()
        self._heading(tr("heading.clean", folder) + (tr("heading.preview") if dry_run else ""))

        def work(worker):
            return cleaner.clean_folder(folder, report_path=report_path, output_root=out_root, in_place=mode == "inplace",
                                        threshold=threshold, timeout_sec=timeout, dry_run=dry_run, interpreter=mayapy,
                                        progress_cb=worker.report_progress, cancel_cb=worker.is_canceled)
        self._start_job(tr("busy.clean"), work, self._show_folder_clean_report, can_cancel=True)

    def _show_folder_clean_report(self, report):
        self.last_kind = None
        summary = report["summary"]
        text = tr("clean.folder.summary", summary.get("files", 0), summary.get("flagged", 0),
                  summary.get("cleaned", 0), summary.get("failed", 0))
        self._append(html.escape(text))
        if report.get("note"):
            self._note(report["note"])
        self._banner("bad" if summary.get("failed") else "good", text, report.get("note", ""))
        self._fill_tree_scene(report.get("results", []))
        self._rendered = (self._fill_tree_scene, report.get("results", []))

    def _clean_last_hits(self, preview=None):
        files = [path for path in self.last_hit_files if os.path.isfile(path)]
        if not files:
            self._note(tr("clean.none"))
            return
        mode = self._choose_clean_mode(preview)
        if not mode:
            return
        out_root = None
        if mode == "copy" and len(files) > 1:
            start = os.path.dirname(files[0])
            out_root = QtWidgets.QFileDialog.getExistingDirectory(
                self, tr("clean.pick.outdir"), os.path.join(start, "_MAYASHIELD_CLEANED"))
            if not out_root:
                return
        threshold, dry_run = self._threshold(), mode == "dry"
        try:
            common = os.path.commonpath(files) if len(files) > 1 else os.path.dirname(files[0])
        except ValueError:
            common = ""
        self._heading(tr("heading.cleanlast", len(files)) + (tr("heading.preview") if dry_run else ""))

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
            cleaned = [item for item in results if item.get("cleaned")]
            text = tr("clean.summary", len(cleaned), len(results))
            self._append(html.escape(text))
            detail = []
            for item in results:
                self._note("%s: %s" % (item["path"], item.get("note", "")))
                if item.get("output_path"):
                    self._append("&nbsp;&nbsp;<a href='%s'>%s</a>" % (
                        _link("reveal", item["output_path"]), html.escape(tr("clean.output", item["output_path"]))))
                    if len(results) == 1:
                        detail.append(tr("clean.output", item["output_path"]))
                if item.get("backup_path") and len(results) == 1:
                    detail.append(tr("clean.backup", item["backup_path"]))
            if dry_run:
                self._banner("idle", text, tr("clean.preview"))
            else:
                self._banner("good" if len(cleaned) == len(results) and results else "bad", text, " ".join(detail))
            self.last_kind = None
            self._fill_tree_scene(results)
            self._rendered = (self._fill_tree_scene, results)
            self._write_report(payload)
        self._start_job(tr("busy.clean"), work, done, can_cancel=True)

    # ------------------------------------------------------------------ quarantine
    def _show_quarantine(self):
        records = persistence.list_quarantine()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(tr("q.title"))
        dialog.resize(760, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        if not records:
            layout.addWidget(QtWidgets.QLabel(tr("q.empty")))
        table = QtWidgets.QTreeWidget()
        table.setHeaderLabels([tr("q.col.original"), tr("q.col.when"), tr("q.col.action"), tr("q.col.why")])
        for record in records:
            action = tr("q.action.repaired") if record.get("action") == "repaired" else tr("q.action.moved")
            why = explain_hit({"reasons": record.get("reasons", [])})
            item = QtWidgets.QTreeWidgetItem([record["original"], record["time"], action, why])
            item.setData(0, QtCore.Qt.UserRole, record["id"])
            table.addTopLevelItem(item)
        table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(table)
        row = QtWidgets.QHBoxLayout()
        restore = QtWidgets.QPushButton(tr("q.restore"))
        close = QtWidgets.QPushButton(tr("close"))
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
                self._note(tr("log.restored", result["path"]))
                table.takeTopLevelItem(table.indexOfTopLevelItem(item))
            else:
                QtWidgets.QMessageBox.warning(dialog, "MayaShield", result["reason"])

        restore.clicked.connect(lambda *_: do_restore())
        close.clicked.connect(lambda *_: dialog.accept())
        exec_dialog(dialog)

    # ------------------------------------------------------------------ results list
    def _show_tree(self, shown):
        self.tree.setVisible(bool(shown))
        self._layout_stretch()

    def _rerender(self):
        if self._rendered is not None:
            function, argument = self._rendered
            function(argument)

    def _color_item(self, item, level):
        red, green, blue = _RISK_COLORS.get(str(level or ""), (150, 150, 150))
        item.setForeground(2, QtGui.QBrush(QtGui.QColor(red, green, blue)))
        item.setData(0, _LEVEL_ROLE, str(level or ""))

    def _add_evidence(self, parent, evidence):
        """Rule names and weights, for people who want them."""
        if not self._details():
            return
        for item in evidence:
            child = QtWidgets.QTreeWidgetItem(["%s  (%s, %s)" % (explain_rule(item.get("rule"), item.get("category")),
                                                                 item.get("rule", ""), item.get("weight", "")),
                                               str(item.get("detail", "")), ""])
            child.setToolTip(1, str(item.get("detail", "")))
            parent.addChild(child)

    def _fill_rows(self, rows):
        self.tree.clear()
        for row in rows[:_MAX_RENDERED]:
            item = QtWidgets.QTreeWidgetItem([row["what"], row["where"], level_name(row["level"])])
            item.setToolTip(0, row["what"])
            item.setToolTip(1, row.get("tip") or row["where"])
            if row["link"]:
                item.setData(0, QtCore.Qt.UserRole, row["link"])
            self._color_item(item, row["level"])
            self.tree.addTopLevelItem(item)
            self._add_evidence(item, row["evidence"])
        if len(rows) > _MAX_RENDERED:
            self.tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([tr("what.more", len(rows) - _MAX_RENDERED), "", ""]))
        self._show_tree(bool(rows))

    def _fill_tree_disk(self, hits):
        self._fill_rows([{"level": hit.get("confidence"), "what": explain_hit(hit), "where": os.path.basename(hit["path"]),
                          "tip": hit["path"], "link": _link("reveal", hit["path"]), "evidence": hit.get("evidence", ())}
                         for hit in hits])

    def _fill_tree_scene(self, reports):
        self.tree.clear()
        shown = 0
        for report in reports[:_MAX_RENDERED]:
            hits = [hit for hit in report.get("hits") or ()
                    if self._details() or hit.get("score", 0) >= rules.RISK_MEDIUM or hit.get("confidence") == "error"]
            if not hits:
                continue
            shown += 1
            path = report.get("path", "")
            level = report.get("risk", "")
            if level in ("info", "low") and any(hit.get("confidence") == "error" for hit in hits):
                level = "error"
            top = QtWidgets.QTreeWidgetItem([explain_hit(dict(hits[0], families=report.get("families") or hits[0].get("families"))),
                                             os.path.basename(path), level_name(level)])
            top.setToolTip(1, path)
            top.setData(0, QtCore.Qt.UserRole, _link("reveal", path))
            self._color_item(top, level)
            self.tree.addTopLevelItem(top)
            for hit in hits[:_MAX_RENDERED]:
                name = hit.get("node") or ""
                where = tr("where.node", name) if name else ""
                if hit.get("line"):
                    where = (where + ", " if where else "") + tr("where.line", hit["line"])
                # The heading names the virus; the line under it says what this item does.
                child = QtWidgets.QTreeWidgetItem([explain_hit(hit, named=False), where,
                                                   level_name(hit.get("confidence"))])
                child.setToolTip(0, str(hit.get("detail", "")))
                self._color_item(child, hit.get("confidence"))
                if hit.get("node"):
                    child.setData(0, QtCore.Qt.UserRole, _link("selectNode", hit["node"]))
                top.addChild(child)
                self._add_evidence(child, hit.get("evidence", []))
        self.tree.expandToDepth(0)
        self._show_tree(shown > 0)

    def _on_tree_item(self, item, _column):
        _follow_link(item.data(0, QtCore.Qt.UserRole) or "")

    def _on_tree_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        menu = QtWidgets.QMenu(self.tree)
        action, value = _parse_link(item.data(0, QtCore.Qt.UserRole) or "")
        reveal = menu.addAction(tr("menu.reveal")) if action == "reveal" else None
        select = menu.addAction(tr("menu.select")) if action == "selectnode" else None
        copy = menu.addAction(tr("menu.copy"))
        menu.addSeparator()
        expand = menu.addAction(tr("menu.expand"))
        collapse = menu.addAction(tr("menu.collapse"))
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
            QtWidgets.QApplication.clipboard().setText("%s | %s" % (item.text(0), item.toolTip(1) or item.text(1)))

    def closeEvent(self, event):
        # The job is told to stop and left to finish by itself; nothing waits on it here.
        if self.worker is not None:
            self.worker.cancel()
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


def _show_pending(window):
    """Files the guard let through with a note: show the newest when the panel opens."""
    reports = guard.take_reviews()
    if reports and is_alive(window) and window.worker is None:
        window.show_file_report(reports[-1])


def show_ui(workspace=True, checkup=False):
    """Show the panel, reusing it if it already exists."""
    global _window
    if workspace:
        if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
            cmds.workspaceControl(WORKSPACE_CONTROL, edit=True, visible=True, restore=True)
        else:
            cmds.workspaceControl(WORKSPACE_CONTROL, label="MayaShield", retain=False, floating=True,
                                  initialWidth=460, initialHeight=640, uiScript=_UI_SCRIPT)
        window = _embed_in_workspace()
    else:
        if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
            cmds.deleteUI(WORKSPACE_CONTROL)
            _window = None
        if not is_alive(_window):
            _window = MayaShieldWindow(_maya_main_window())
            _window.setWindowFlags(QtCore.Qt.Window)
            _window.resize(460, 640)
        _window.show()
        _window.raise_()
        _window.activateWindow()
        window = _window
    if is_alive(window):
        window.refresh_status()
        if checkup:
            window.start_checkup()
        else:
            _show_pending(window)
    return window


def close_ui():
    global _window
    if is_alive(_window) and _window.worker is not None:
        _window.worker.cancel()
    if cmds.workspaceControl(WORKSPACE_CONTROL, exists=True):
        cmds.deleteUI(WORKSPACE_CONTROL)
    if is_alive(_window):
        _window.close()
        _window.deleteLater()
    _window = None


def wait_for_jobs(milliseconds=5000):
    """Before the plug-in unloads or Maya quits: give running jobs a moment to stop."""
    for thread in list(_threads):
        thread.cancel()
    for thread in list(_threads):
        thread.wait(milliseconds)
