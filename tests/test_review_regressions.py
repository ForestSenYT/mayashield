# -*- coding: utf-8 -*-
"""Bypass and data-loss cases found by an independent code review of 4.0.0."""
import base64
import os
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from mayashield_core import analyzer, cleaner, persistence, rules, scanner  # noqa: E402
from mayashield_core.ma_parser import parse_ma  # noqa: E402
from mayashield_core.mb_parser import parse_mb  # noqa: E402


def rule_ids(result):
    return [item["rule"] for item in result["evidence"]]


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mayashield_review_")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for folder, _subfolders, names in os.walk(self.dir):
            for name in names:
                os.chmod(os.path.join(folder, name), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.dir, True)

    def write(self, name, data):
        path = os.path.normpath(os.path.join(self.dir, name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def node(self, doc, name):
        return [node for node in doc.nodes if node.name == name][0]


class CodeHiddenFromTheAnalyzer(unittest.TestCase):
    def test_mel_python_through_a_variable(self):
        code = 'string $c = "import os; os.system(\'echo inert\')"; python($c);'
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_mel_python_inside_evaldeferred(self):
        code = 'evalDeferred("python(\\"import os; os.system(\'echo inert\')\\")");'
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_mel_python_built_by_concatenation(self):
        code = 'python("import o" + "s; os.system(\'echo inert\')");'
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_mel_system_inside_evaldeferred(self):
        code = 'evalDeferred("system(\\"echo inert\\")");'
        self.assertIn("mel_system", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_python_code_in_evaldeferred_string(self):
        code = "import maya.cmds as c\nc.evalDeferred(\"import os; os.system('echo inert')\")\n"
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_python_code_in_scriptjob_string(self):
        code = ("import maya.cmds as c\n"
                "c.scriptJob(event=['timeChanged', \"import os; os.system('echo inert')\"])\n")
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_docstrings_are_not_treated_as_code(self):
        code = '"""Example:\n    import os\n    os.system("x")\n"""\nx = 1\n'
        self.assertNotIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_deep_expression_cannot_hide_the_statements_after_it(self):
        code = "x = " + " + ".join(["'a'"] * 1500) + "\nimport os as o\no.system('echo inert')\n"
        result = analyzer.analyze_text(code, lang="python")
        self.assertIn("py_process", rule_ids(result), result)

    def test_deep_expression_in_the_same_statement_is_flagged(self):
        code = "import os\ny = (" + " + ".join(["'a'"] * 1500) + ") and os.system('echo inert')\n"
        result = analyzer.analyze_text(code, lang="python", context=analyzer.CONTEXT_SCRIPT)
        self.assertGreaterEqual(result["score"], rules.RISK_MEDIUM, result)

    def test_spawn_and_exec_family(self):
        for call in ("os.spawnl(os.P_NOWAIT, 'x')", "os.execv('x', [])", "os.spawnv(os.P_WAIT, 'x', [])"):
            result = analyzer.analyze_text("import os\n%s\n" % call, lang="python")
            self.assertIn("py_process", rule_ids(result), call)

    def test_exec_of_json_is_not_called_decoded(self):
        code = "import json\neval(json.loads(s))\n"
        self.assertNotIn("py_exec_decoded", rule_ids(analyzer.analyze_text(code, lang="python")))
        code = "import marshal\nexec(marshal.loads(s))\n"
        self.assertIn("py_exec_decoded", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_download_and_run_is_high_on_its_own(self):
        code = "import urllib.request as u\nexec(u.urlopen('http://x/y').read())\n"
        self.assertGreaterEqual(analyzer.analyze_text(code, lang="python")["score"], rules.RISK_HIGH)

    def test_decoy_blobs_cannot_crowd_out_a_payload(self):
        decoys = " ".join(base64.b64encode(os.urandom(150 + index)).decode() for index in range(40))
        payload = base64.b64encode(b"import vaccine\n" + b"#" * 40).decode()
        result = analyzer.analyze_text("x = '%s %s'" % (decoys, payload), lang="python")
        self.assertIn("Vaccine (PuTianTongQi)", result["families"])

    def test_family_marker_in_the_middle_of_a_huge_script(self):
        text = "#" * (5 * 1024 * 1024) + "\nimport vaccine\n" + "#" * (5 * 1024 * 1024)
        self.assertGreaterEqual(analyzer.analyze_text(text, lang="python")["score"], rules.RISK_HIGH)

    def test_huge_script_is_never_silently_fine(self):
        text = "print(1)\n" * (1024 * 1024)
        result = analyzer.analyze_text(text, lang="python", context=analyzer.CONTEXT_SCRIPT)
        self.assertGreaterEqual(result["score"], rules.RISK_LOW, result)


class AutoRunScriptNodes(Sandbox):
    def score(self, payload, script_type=1):
        node = fixtures.script_node("batch_tool", payload, script_type=script_type, python=True)
        return scanner.scan_file(self.write("s.ma", fixtures.ma_scene(node)))["score"]

    def test_auto_run_process_is_high(self):
        self.assertGreaterEqual(self.score("import os\nos.system('echo inert')\n"), rules.RISK_HIGH)

    def test_auto_run_network_is_high(self):
        self.assertGreaterEqual(self.score("import urllib.request as u\nu.urlopen('http://x')\n"), rules.RISK_HIGH)

    def test_on_demand_process_is_only_medium(self):
        score = self.score("import os\nos.system('echo inert')\n", script_type=0)
        self.assertLess(score, rules.RISK_HIGH)
        self.assertGreaterEqual(score, rules.RISK_MEDIUM)

    def test_config_script_types_count_as_auto_run(self):
        self.assertGreaterEqual(self.score("import os\nos.system('echo inert')\n", script_type=3), rules.RISK_HIGH)

    def test_expression_running_a_process_is_high(self):
        node = (b'createNode expression -n "e";\n'
                b'\tsetAttr ".ixp" -type "string" "system(\\"echo inert\\");";\n')
        report = scanner.scan_file(self.write("e.ma", fixtures.ma_scene(node)))
        self.assertGreaterEqual(report["score"], rules.RISK_HIGH, report)


class AsciiParserBypasses(Sandbox):
    BAD = b'"import vaccine"'

    def scan(self, data):
        return scanner.scan_file(self.write("s.ma", fixtures.HEADER + data))

    def test_infinite_number_does_not_abort_the_scan(self):
        data = (b'createNode script -n "x";\n\tsetAttr ".st" 1e999;\n'
                b'createNode script -n "y";\n\tsetAttr ".b" -type "string" ' + self.BAD + b';\n')
        report = self.scan(data)
        self.assertTrue(report["parse_ok"], report)
        self.assertEqual(report["risk"], "high", report)

    def test_empty_statement_does_not_swallow_the_next_one(self):
        data = (b'createNode script -n "x";\n;\n;;\n\tsetAttr ".b" -type "string" ' + self.BAD
                + b';\n\tsetAttr ".stp" 1;\n')
        doc = parse_ma(self.write("s.ma", fixtures.HEADER + data))
        self.assertEqual(doc.anomalies, [])
        self.assertEqual(self.node(doc, "x").strings["b"], "import vaccine")
        self.assertEqual(self.node(doc, "x").numbers["stp"], 1)

    def test_quoted_node_type(self):
        doc = parse_ma(self.write("s.ma", b'createNode "script" -n "x";\n'))
        self.assertEqual(self.node(doc, "x").type, "script")

    def test_flags_before_node_type(self):
        doc = parse_ma(self.write("s.ma", b'createNode -n "x" script;\n'))
        self.assertEqual(self.node(doc, "x").type, "script")
        doc = parse_ma(self.write("s.ma", b'createNode -p "grp" -n "y" -s transform;\n'))
        self.assertEqual(self.node(doc, "y").type, "transform")
        self.assertTrue(self.node(doc, "y").shared)

    def test_select_back_to_a_script_node(self):
        data = b'createNode script -n "x";\ncreateNode transform -n "t";\nselect -ne "x";\n\tsetAttr ".b" -type "string" "system(\\"echo inert\\");";\n'
        doc = parse_ma(self.write("s.ma", data))
        self.assertIn("b", self.node(doc, "x").strings)
        self.assertEqual(self.scan(data)["hits"][0]["kind"], "scriptAttr")

    def test_attribute_after_a_connection_stays_on_the_node(self):
        # connectAttr doesn't change the selection, so this .b still belongs to x.
        data = b'createNode script -n "x";\nconnectAttr "a.b" "c.d";\n\tsetAttr ".b" -type "string" "payload";\n'
        doc = parse_ma(self.write("s.ma", data))
        self.assertEqual(self.node(doc, "x").strings.get("b"), "payload")

    def test_type_flag_before_the_attribute(self):
        data = b'createNode script -n "x";\n\tsetAttr -type "string" ".b" "payload";\n'
        doc = parse_ma(self.write("s.ma", data))
        self.assertEqual(self.node(doc, "x").strings.get("b"), "payload")

    def test_short_type_flag(self):
        data = b'createNode script -n "x";\n\tsetAttr -typ "string" ".b" "payload";\n'
        doc = parse_ma(self.write("s.ma", data))
        self.assertEqual(self.node(doc, "x").strings.get("b"), "payload")

    def test_plug_with_node_name(self):
        data = b'createNode script -n "x";\ncreateNode transform -n "t";\nsetAttr "x.b" -type "string" "payload";\n'
        doc = parse_ma(self.write("s.ma", data))
        self.assertEqual(self.node(doc, "x").strings.get("b"), "payload")

    def test_attribute_on_unknown_node_still_gets_analysed(self):
        for code in (b'system \\"cmd /c x\\";', b'sysFile -delete \\"x\\";', b'quit -f;'):
            data = b'setAttr "external.notes" -type "string" "' + code + b'";\n'
            self.assertGreaterEqual(self.scan(data)["score"], rules.RISK_LOW, code)

    def test_removal_covers_statements_spread_through_the_file(self):
        data = (fixtures.HEADER + b'createNode script -n "breed_gene";\n'
                b'createNode transform -n "keep";\n'
                b'\trename -uid "00000000-0000-0000-0000-000000000009";\n'
                b'select -ne "breed_gene";\n\tsetAttr ".b" -type "string" "x";\n'
                b'createNode transform -n "keep2";\n')
        report = cleaner.clean_file(self.write("spread.ma", data))
        cleaned = self.read(report["output_path"])
        self.assertNotIn(b"breed_gene", cleaned)
        self.assertIn(b'"keep"', cleaned)
        self.assertIn(b'"keep2"', cleaned)
        self.assertEqual(parse_ma(report["output_path"]).anomalies, [])

    def test_statements_between_a_nodes_attributes_survive_removal(self):
        data = (fixtures.HEADER + b'createNode script -n "breed_gene";\n'
                b'connectAttr "keep.a" "keep.b";\n'
                b'\tsetAttr ".b" -type "string" "x";\n')
        report = cleaner.clean_file(self.write("gap.ma", data))
        cleaned = self.read(report["output_path"])
        self.assertNotIn(b"breed_gene", cleaned)
        self.assertIn(b'connectAttr "keep.a" "keep.b"', cleaned)
        self.assertNotIn(b'setAttr ".b"', cleaned)

    def test_scene_with_another_extension_is_recognised(self):
        path = self.write("renamed.txt", fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT)))
        self.assertTrue(scanner.looks_like_scene(path))
        self.assertEqual(scanner.scan_file(path)["risk"], "high")
        self.assertFalse(scanner.looks_like_scene(self.write("note.txt", b"hello")))

    def test_mel_script_file_can_be_scanned(self):
        path = self.write("tool.mel", b'python("import vaccine");\n')
        self.assertEqual(scanner.scan_script_file(path)["risk"], "high")


class BinaryParserBypasses(Sandbox):
    def test_select_group_on_a_script_node(self):
        iff = fixtures.Iff()
        node = iff.group(b"SCRP", iff.crea("x"))
        select = iff.group(b"SLCT", iff.leaf(b"SLCT", b"x\x00"), iff.string("b", "system(\"echo inert\");"))
        report = scanner.scan_file(self.write("a.mb", fixtures.mb_scene(node, select, iff=iff)))
        self.assertEqual(report["hits"][0]["kind"], "scriptAttr", report)

    def test_select_group_is_removed_with_its_node(self):
        iff = fixtures.Iff()
        node = iff.group(b"SCRP", iff.crea("breed_gene"))
        select = iff.group(b"SLCT", iff.leaf(b"SLCT", b"breed_gene\x00"), iff.string("b", "x"))
        report = cleaner.clean_file(self.write("a.mb", fixtures.mb_scene(node, select, iff=iff)))
        self.assertEqual(self.read(report["output_path"]), fixtures.mb_scene(iff=iff))

    def test_strings_in_unknown_groups_are_analysed(self):
        iff = fixtures.Iff()
        odd = iff.group(b"ZZZZ", iff.string("data", "import os\nos.system('echo inert')"))
        report = scanner.scan_file(self.write("b.mb", fixtures.mb_scene(odd, iff=iff)))
        self.assertGreaterEqual(report["score"], rules.RISK_MEDIUM, report)

    def test_root_larger_than_the_file_is_flagged_not_clean(self):
        iff = fixtures.Iff()
        data = bytearray(fixtures.mb_scene(iff=iff))
        data[8:16] = (len(data) * 4).to_bytes(8, "big")
        report = scanner.scan_file(self.write("c.mb", bytes(data)))
        self.assertFalse(report["parse_ok"])
        self.assertTrue(scanner.is_flagged(report), report)

    def test_nothing_removed_is_not_reported_as_cleaned(self):
        # A string nested below a node's top level has no range that can be cut.
        iff = fixtures.Iff()
        nested = iff.group(b"XXXX", iff.string("data", "import vaccine"))
        holder = iff.group(b"NTWK", iff.crea("settings"), nested)
        source = self.write("d.mb", fixtures.mb_scene(holder, iff=iff))
        report = cleaner.clean_file(source)
        self.assertFalse(report["cleaned"], report)
        self.assertEqual(report["removed"], [])
        self.assertGreater(report["remaining_hits_count"], 0)
        self.assertEqual(sorted(os.listdir(self.dir)), ["d.mb"])


class CleanerSafety(Sandbox):
    INFECTED = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT))

    def test_default_copy_never_overwrites_an_earlier_copy(self):
        # The user may have opened the clean copy and kept working in it.
        source = self.write("s.ma", self.INFECTED)
        first = cleaner.clean_file(source)["output_path"]
        with open(first, "ab") as handle:
            handle.write(b"// the user's later work\n")
        edited = self.read(first)
        second = cleaner.clean_file(source)["output_path"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.read(first), edited)

    def test_replacing_a_chosen_output_keeps_a_backup(self):
        source = self.write("s.ma", self.INFECTED)
        target = self.write("out.ma", b"// precious\n")
        report = cleaner.clean_file(source, output_path=target)
        self.assertEqual(self.read(report["replaced_backup"]), b"// precious\n")
        self.assertEqual(self.read(target), fixtures.ma_scene())

    def test_read_only_flag_survives_an_in_place_clean(self):
        source = self.write("ro.ma", self.INFECTED)
        os.chmod(source, stat.S_IREAD)
        report = cleaner.clean_file(source, in_place=True)
        self.assertTrue(report["cleaned"], report)
        self.assertFalse(os.stat(source).st_mode & stat.S_IWRITE, "read-only flag was lost")

    def test_unparsable_failure_leaves_no_temp_file(self):
        source = self.write("t.ma", self.INFECTED)
        original = cleaner.rewrite_ma

        def explode(*_args, **_kwargs):
            raise ValueError("boom")

        cleaner.rewrite_ma = explode
        try:
            report = cleaner.clean_file(source)
        finally:
            cleaner.rewrite_ma = original
        self.assertFalse(report["cleaned"])
        self.assertEqual(os.listdir(self.dir), ["t.ma"])
        self.assertEqual(self.read(source), self.INFECTED)

    def test_parser_exception_is_reported_not_raised(self):
        source = self.write("u.ma", self.INFECTED)
        original = cleaner.parse_scene

        def explode(_path):
            raise OverflowError("boom")

        cleaner.parse_scene = explode
        try:
            report = cleaner.clean_file(source)
        finally:
            cleaner.parse_scene = original
        self.assertFalse(report["cleaned"])
        self.assertIn("boom", report["note"])

    def test_folder_listing_ignores_interrupted_temp_files(self):
        self.write("a.ma", fixtures.ma_scene())
        self.write(".a.mayashield_tmp_123.ma", self.INFECTED)
        self.assertEqual([os.path.basename(p) for p in scanner.list_scene_files(self.dir)], ["a.ma"])


class StartupFileSafety(Sandbox):
    def env(self):
        return {"app_dir": os.path.join(self.dir, "maya"), "maya_location": "", "sys_path": [], "script_path": []}

    def setUp(self):
        super(StartupFileSafety, self).setUp()
        self.saved = os.environ.get("MAYA_APP_DIR")
        os.environ["MAYA_APP_DIR"] = os.path.join(self.dir, "maya")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.saved is None:
            del os.environ["MAYA_APP_DIR"]
        else:
            os.environ["MAYA_APP_DIR"] = self.saved

    def test_repair_keeps_gbk_bytes_and_line_endings(self):
        keep = u"# coding: gbk\r\n# 中文注释\r\nimport mytools\r\n".encode("gbk")
        path = self.write("maya/scripts/userSetup.py", keep + b"import vaccine\r\n")
        report = persistence.clean_disk(self.env())
        self.assertEqual(report["actions"][0]["status"], "repaired", report["actions"])
        self.assertEqual(self.read(path), keep)

    def test_restore_after_a_repair_will_not_discard_later_edits(self):
        path = self.write("maya/scripts/userSetup.py", b"import mine\nimport vaccine\n")
        persistence.clean_disk(self.env())
        with open(path, "ab") as handle:
            handle.write(b"mine.new_work()\n")
        edited = self.read(path)
        record = persistence.list_quarantine()[0]
        result = persistence.restore(record["id"])
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(self.read(path), edited)
        self.assertEqual(len(persistence.list_quarantine()), 1)

    def test_restore_right_after_a_repair_works(self):
        original = b"import mine\nimport vaccine\n"
        path = self.write("maya/scripts/userSetup.py", original)
        persistence.clean_disk(self.env())
        record = persistence.list_quarantine()[0]
        self.assertEqual(persistence.restore(record["id"])["status"], "restored")
        self.assertEqual(self.read(path), original)

    def test_restore_refuses_a_tampered_quarantine_file(self):
        path = self.write("maya/scripts/vaccine.py", fixtures.INERT)
        persistence.clean_disk(self.env())
        record = persistence.list_quarantine()[0]
        with open(record["stored"], "ab") as handle:
            handle.write(b"# changed\n")
        self.assertEqual(persistence.restore(record["id"])["status"], "failed")
        self.assertFalse(os.path.exists(path))

    def test_guard_block_keeps_bytes_and_crlf_and_backs_up(self):
        original = u"# coding: gbk\r\n# 中文\r\nimport mine\r\n".encode("gbk")
        path = self.write("maya/2024/scripts/userSetup.py", original)
        result = persistence.write_guard_block(os.path.dirname(path))
        self.assertEqual(result["status"], "appended")
        text = self.read(path)
        self.assertTrue(text.startswith(original), text[:60])
        self.assertIn(b"# >>> MAYASHIELD GUARD >>>\r\n", text)
        self.assertEqual(self.read(result["backup"]), original)
        self.assertEqual(persistence.write_guard_block(os.path.dirname(path))["status"], "already_immunized")

    def test_guard_block_in_a_crlf_file_is_still_recognised_by_the_scan(self):
        path = self.write("maya/2024/scripts/userSetup.py", b"import mine\r\n")
        persistence.write_guard_block(os.path.dirname(path))
        self.assertEqual(persistence.scan_disk(self.env())["hits"], [])

    def test_payload_after_padding_in_a_startup_file(self):
        padding = b"# padding\n" * (1024 * 1024)
        self.write("maya/scripts/userSetup.py", padding + b"import vaccine\n")
        self.assertEqual(len(persistence.scan_disk(self.env())["hits"]), 1)

    def test_oversized_startup_file_is_flagged(self):
        self.write("maya/scripts/userSetup.py", b"# padding\n" * (7 * 1024 * 1024))
        report = persistence.scan_disk(self.env())
        self.assertEqual(len(report["hits"]), 1)
        self.assertIn("oversized_startup_file", report["hits"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
