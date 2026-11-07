# -*- coding: utf-8 -*-
"""The `mayashield` and `mayashieldUI` MEL commands."""
import json

import maya.api.OpenMaya as om

from . import __version__, cleaner, live, mayapaths, persistence, rules, scanner

COMMAND = "mayashield"
UI_COMMAND = "mayashieldUI"

USAGE = (
    "MayaShield %s\n"
    "  mayashield -scan | -scanAll | -clean | -cleanScene | -cleanJobs | -immunize\n"
    "  mayashield -scanFile \"scene.ma|mb\" [-deep]\n"
    "  mayashield -scanFolder \"folder\" [-deep]\n"
    "  mayashield -offlineCleanFile \"scene.ma|mb\" [-inPlace] [-offlineOut \"path\"]\n"
    "  mayashield -offlineCleanFolder \"folder\" [-inPlace] [-offlineOutDir \"path\"]\n"
    "  mayashield -listQuarantine | -restore \"id\" | -version\n"
    "Options:\n"
    "  -dryRun  -verbose  -includeMedium  -timeout 120  -report \"path/to/report.json\"\n"
    "  -clean -scanAll also acts on files found outside the startup locations.\n"
) % __version__


class _Args(object):
    def __init__(self, values):
        self.values = values
        self.lowered = [value.lower() for value in values]

    def has(self, *flags):
        return any(flag.lower() in self.lowered for flag in flags)

    def value(self, *flags):
        for flag in flags:
            lowered = flag.lower()
            if lowered in self.lowered:
                index = self.lowered.index(lowered)
                if index + 1 < len(self.values):
                    return self.values[index + 1]
        return None


def _write_report(path, payload):
    if not path:
        return
    try:
        scanner.write_json(path, payload)
        om.MGlobal.displayInfo("[mayashield] report -> %s" % path)
    except (IOError, OSError) as error:
        om.MGlobal.displayWarning("[mayashield] failed to write report: %s" % error)


def _show_notes(report, verbose):
    if verbose:
        for note in (report.get("notes") or [])[:500]:
            om.MGlobal.displayInfo("[mayashield][verbose] %s" % note)


def _show_file_report(report):
    if report.get("hits"):
        om.MGlobal.displayWarning("%s: %s risk, score %d" % (report["path"], report["risk"], report["score"]))
        for hit in report["hits"][:50]:
            om.MGlobal.displayWarning("  HIT: %s | %s | %s" % (hit.get("node") or "-", hit.get("reason"), hit.get("confidence")))
    else:
        om.MGlobal.displayInfo("%s: %s" % (report["path"], report.get("note") or "No suspicious content."))


class MayaShieldCmd(om.MPxCommand):
    @staticmethod
    def creator():
        return MayaShieldCmd()

    def doIt(self, arg_list):
        args = _Args([arg_list.asString(index) for index in range(len(arg_list))])
        dry_run = args.has("-dryRun")
        verbose = args.has("-verbose", "-v")
        in_place = args.has("-inPlace")
        threshold = rules.RISK_MEDIUM if args.has("-includeMedium") else rules.RISK_HIGH
        report_path = args.value("-report")
        try:
            timeout = max(5, int(args.value("-timeout", "-timeoutSec", "-offlineTimeout") or 120))
        except ValueError:
            timeout = 120

        if args.has("-version"):
            self.setResult(__version__)
            om.MGlobal.displayInfo("MayaShield %s" % __version__)
            return

        if args.has("-scanFile"):
            path = args.value("-scanFile")
            if not path:
                om.MGlobal.displayError("Usage: mayashield -scanFile \"scene.ma|mb\" [-deep]")
                return
            report = scanner.scan_file(path, deep=args.has("-deep"), timeout_sec=timeout,
                                       mayapy=mayapaths.find_mayapy() if args.has("-deep") else None)
            _show_file_report(report)
            _write_report(report_path, report)
            self.setResult(json.dumps({"score": report["score"], "risk": report["risk"]}))
            return

        if args.has("-scanFolder"):
            folder = args.value("-scanFolder")
            if not folder:
                om.MGlobal.displayError("Usage: mayashield -scanFolder \"folder\" [-deep]")
                return
            payload = scanner.scan_folder(folder, deep=args.has("-deep"), timeout_sec=timeout,
                                          mayapy=mayapaths.find_mayapy() if args.has("-deep") else None)
            summary = payload["summary"]
            om.MGlobal.displayInfo("[mayashield] scanFolder files=%d flagged=%d" % (summary["files"], summary["flagged"]))
            for hit in payload["hits"][:100]:
                om.MGlobal.displayWarning("HIT: %s | %s | %s" % (hit["path"], hit["confidence"], ",".join(hit["reasons"][:4])))
            _write_report(report_path, payload)
            return

        if args.has("-offlineCleanFile"):
            source = args.value("-offlineCleanFile")
            if not source:
                om.MGlobal.displayError("Usage: mayashield -offlineCleanFile \"scene.ma|mb\" [-inPlace] [-offlineOut \"path\"]")
                return
            report = cleaner.clean_file(source, output_path=args.value("-offlineOut", "-out", "-output"),
                                        in_place=in_place, threshold=threshold, dry_run=dry_run)
            if report.get("cleaned"):
                om.MGlobal.displayWarning("CLEANED: %s -> %s" % (report["path"], report["output_path"]))
                if report.get("backup_path"):
                    om.MGlobal.displayInfo("Original kept as %s" % report["backup_path"])
            om.MGlobal.displayInfo(report.get("note", ""))
            _write_report(report_path, report)
            return

        if args.has("-offlineCleanFolder"):
            folder = args.value("-offlineCleanFolder")
            if not folder:
                om.MGlobal.displayError("Usage: mayashield -offlineCleanFolder \"folder\" [-inPlace] [-offlineOutDir \"path\"]")
                return
            payload = cleaner.clean_folder(folder, output_root=args.value("-offlineOutDir", "-outDir"), in_place=in_place,
                                           threshold=threshold, timeout_sec=timeout, dry_run=dry_run)
            summary = payload["summary"]
            om.MGlobal.displayInfo("[mayashield] offlineCleanFolder files=%d flagged=%d cleaned=%d failed=%d" % (
                summary.get("files", 0), summary.get("flagged", 0), summary.get("cleaned", 0), summary.get("failed", 0)))
            _write_report(report_path, payload)
            return

        if args.has("-cleanScene"):
            report = live.clean_scene(dry_run=dry_run, verbose=verbose, threshold=threshold)
            om.MGlobal.displayInfo("[mayashield] cleanScene @ %s%s" % (report["time"], " (dryRun)" if dry_run else ""))
            for item in report["deleted"]:
                om.MGlobal.displayWarning("SCENE: %s | %s" % (item["node"], item["status"]))
            _show_notes(report, verbose)
            _write_report(report_path, report)
            return

        if args.has("-cleanJobs"):
            report = live.clean_jobs(dry_run=dry_run, verbose=verbose)
            om.MGlobal.displayInfo("[mayashield] cleanJobs @ %s%s" % (report["time"], " (dryRun)" if dry_run else ""))
            for item in report["killed"]:
                om.MGlobal.displayWarning("JOB: %s | %s" % (item["id"], item["status"]))
            for item in report["unloaded"]:
                om.MGlobal.displayWarning("RUNTIME: %s | %s" % (item["name"], item["status"]))
            for note in report["notes"]:
                om.MGlobal.displayWarning(note)
            _write_report(report_path, report)
            return

        if args.has("-clean"):
            report = persistence.clean_disk(dry_run=dry_run, scan_all=args.has("-scanAll"), threshold=threshold, verbose=verbose)
            om.MGlobal.displayInfo("[mayashield] clean @ %s%s" % (report["time"], " (dryRun)" if dry_run else ""))
            if not report["actions"]:
                om.MGlobal.displayInfo("No actions.")
            for action in report["actions"]:
                om.MGlobal.displayWarning("%s: %s -> %s | %s" % (
                    action["status"].upper(), action["src"], action["dst"] or "-", ",".join(action["reasons"][:4])))
            _show_notes(report, verbose)
            _write_report(report_path, report)
            return

        if args.has("-scanAll") or args.has("-scan"):
            full = args.has("-scanAll")
            report = persistence.scan_disk(scan_all=full, verbose=verbose)
            om.MGlobal.displayInfo("[mayashield] %s @ %s: %d file(s) checked" % (
                "scanAll" if full else "scan", report["time"], report["filesScanned"]))
            if not report["hits"]:
                om.MGlobal.displayInfo("No hits.")
            for hit in report["hits"]:
                om.MGlobal.displayWarning("HIT: %s | %s | score %d | %s" % (
                    hit["path"], hit["confidence"], hit["score"], ",".join(hit["reasons"][:4])))
            _show_notes(report, verbose)
            _write_report(report_path, report)
            return

        if args.has("-immunize"):
            report = live.immunize()
            om.MGlobal.displayInfo("[mayashield] immunize @ %s | %s" % (report["time"], report["status"]))
            if report.get("userSetup.py"):
                om.MGlobal.displayInfo("userSetup.py: %s" % report["userSetup.py"])
            if report.get("reason"):
                om.MGlobal.displayWarning(report["reason"])
            _write_report(report_path, report)
            return

        if args.has("-listQuarantine"):
            records = persistence.list_quarantine()
            if not records:
                om.MGlobal.displayInfo("Quarantine is empty.")
            for record in records:
                om.MGlobal.displayInfo("%s | %s | %s | %s" % (record["id"], record["time"], record["action"], record["original"]))
            _write_report(report_path, records)
            return

        if args.has("-restore"):
            result = persistence.restore(args.value("-restore") or "")
            if result["status"] == "restored":
                om.MGlobal.displayWarning("Restored %s" % result["path"])
            else:
                om.MGlobal.displayError(result["reason"])
            return

        om.MGlobal.displayInfo(USAGE)


class MayaShieldUICmd(om.MPxCommand):
    @staticmethod
    def creator():
        return MayaShieldUICmd()

    def doIt(self, arg_list):
        values = [arg_list.asString(index) for index in range(len(arg_list))]
        try:
            from . import ui
            ui.show_ui(workspace=("-workspace" in values or "-dock" in values))
        except Exception as error:
            om.MGlobal.displayError("mayashieldUI failed: %s" % error)
