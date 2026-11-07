# -*- coding: utf-8 -*-
"""Engine tests.  Standard library only, no Maya: runs on every Python Maya ships.

    python -m unittest discover -s tests
"""
import ast
import base64
import glob
import json
import os
import re
import shutil
import struct
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
from mayashield_core.pool import run_jobs  # noqa: E402


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mayashield_test_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, data):
        path = os.path.normpath(os.path.join(self.dir, name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()


def rule_ids(result):
    return [item["rule"] for item in result["evidence"]]


class AnalyzerBehaviour(unittest.TestCase):
    def assertFlagged(self, result, level=rules.RISK_HIGH):
        self.assertGreaterEqual(result["score"], level, result)

    def assertQuiet(self, result):
        self.assertLess(result["score"], rules.RISK_LOW, result)

    def test_plain_expression_is_quiet(self):
        self.assertQuiet(analyzer.analyze_text("pCube1.tx = sin(time) * 2;", lang="mel"))

    def test_evaldeferred_alone_is_quiet(self):
        # v3 flagged and quarantined any userSetup that used evalDeferred.
        code = "import maya.cmds as cmds\ncmds.evalDeferred('print(1)')\n"
        self.assertQuiet(analyzer.analyze_text(code, lang="python", context=analyzer.CONTEXT_STARTUP))

    def test_confirm_dialog_alone_is_quiet(self):
        self.assertQuiet(analyzer.analyze_text('confirmDialog -m "Save?";', lang="mel"))

    def test_family_marker_is_decisive(self):
        result = analyzer.analyze_text("python(\"import vaccine\");", lang="mel")
        self.assertFlagged(result)
        self.assertIn("Vaccine (PuTianTongQi)", result["families"])

    def test_melui_header_vs_benign_header(self):
        infected = "// Maya Mel UI Configuration File.Maya Mel UI Configuration File..\n"
        benign = "// Maya Mel UI Configuration File.\n// No UI generated in batch mode.\n"
        self.assertFlagged(analyzer.analyze_text(infected, lang="mel"))
        self.assertQuiet(analyzer.analyze_text(benign, lang="mel"))

    def test_genuine_autoupdateattred_is_quiet(self):
        # autoUpdateAttrEd is a real Maya procedure; only the misspelled ones are not.
        self.assertQuiet(analyzer.analyze_text("autoUpdateAttrEd;", lang="mel"))
        self.assertFlagged(analyzer.analyze_text("autoUpdatoAttrEnd;", lang="mel"))

    def test_import_alias_resolves(self):
        code = "import base64 as b\nexec(b.b64decode('%s'))\n" % fixtures.B64_INERT
        result = analyzer.analyze_text(code, lang="python")
        self.assertIn("py_exec_decoded", rule_ids(result))
        self.assertFlagged(result)

    def test_from_import_alias_resolves(self):
        code = "from base64 import b64decode as d\nexec(d('%s'))\n" % fixtures.B64_INERT
        self.assertIn("py_exec_decoded", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_dunder_import_and_built_string(self):
        code = "__import__('o' + 's').system('echo inert')\n"
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_getattr_with_built_name(self):
        code = "import os\ngetattr(os, 'sys' + 'tem')('echo inert')\n"
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_reversed_string_family_marker(self):
        code = "name = 'eneg_eniccav'[::-1]\nprint(name)\n"
        self.assertIn("ioc_vaccine_gene", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_concatenated_family_marker(self):
        code = "print('vaccine' + '_gene')\n"
        self.assertIn("ioc_vaccine_gene", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_chr_join_family_marker(self):
        word = "breed_gene"
        code = "x = ''.join([%s])\n" % ", ".join("chr(%d)" % ord(c) for c in word)
        self.assertIn("ioc_breed_gene", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_very_long_concatenation_is_bounded(self):
        code = "x = " + " + ".join(["'a'"] * 3000) + "\n"
        analyzer.analyze_text(code, lang="python")

    def test_marker_inside_two_encoding_layers(self):
        packed = fixtures.packed_python("import vaccine\n" + fixtures.INERT)
        result = analyzer.analyze_text("data = '%s'\n" % packed, lang="python")
        self.assertIn("Vaccine (PuTianTongQi)", result["families"])
        self.assertIn("encoded_payload", rule_ids(result))

    def test_hex_blob_is_decoded(self):
        blob = ("import maya_secure_system  # " + "x" * 40).encode().hex()
        self.assertIn("maya_secure_system", analyzer.analyze_text("d = '%s'" % blob, lang="python")["families"])

    def test_mel_python_call_is_analysed_as_python(self):
        code = 'python("import base64 as b; exec(b.b64decode(\\"%s\\"))");' % fixtures.B64_INERT
        self.assertIn("py_exec_decoded", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_python2_source_still_gets_rules(self):
        code = "print 'hi'\nimport os\nos.system('echo inert')\n"
        self.assertIn("py_process", rule_ids(analyzer.analyze_text(code, lang="python")))

    def test_running_an_existing_script_node_is_not_replication(self):
        # Pattern used throughout Maya's interactive tutorial scenes.
        code = ("import maya.cmds as cmds\n"
                "cmds.scriptNode('populateText', executeBefore=True)\n"
                "job = cmds.scriptJob(event=['SelectionChanged', check])\n")
        result = analyzer.analyze_text(code, lang="python")
        self.assertNotIn("self_replication", rule_ids(result))
        self.assertLess(result["score"], rules.RISK_MEDIUM, result)

    def test_creating_a_script_node_with_code_is_replication(self):
        code = ("import maya.cmds as cmds\n"
                "cmds.scriptNode(name='copy', beforeScript=payload, scriptType=1)\n"
                "cmds.scriptJob(event=['SceneSaved', save])\n")
        self.assertIn("self_replication", rule_ids(analyzer.analyze_text(code, lang="python")))
        self.assertIn("py_scriptnode", rule_ids(analyzer.analyze_text(
            "import maya.cmds as cmds\ncmds.createNode('script')\n", lang="python")))

    def test_self_replication_combo(self):
        code = ('scriptNode -n "copy" -bs $payload;\n'
                'scriptJob -e "SceneSaved" "file -save";\n')
        self.assertIn("self_replication", rule_ids(analyzer.analyze_text(code, lang="mel")))

    def test_persistence_write_combo(self):
        code = '$f = fopen($dir + "/scripts/userSetup.mel", "a"); fprint $f $code;'
        result = analyzer.analyze_text(code, lang="mel")
        self.assertIn("persist_write", rule_ids(result))
        self.assertFlagged(result, rules.RISK_MEDIUM)

    def test_ordinary_pipeline_tool_is_not_high(self):
        # Studio tools call subprocess and urllib all the time.
        code = ("import subprocess, urllib.request\n"
                "subprocess.call(['git', 'pull'])\n"
                "urllib.request.urlopen('https://example.com/version.json')\n")
        result = analyzer.analyze_text(code, lang="python", context=analyzer.CONTEXT_TOOL)
        self.assertLess(result["score"], rules.RISK_HIGH, result)

    def test_data_attribute_base64_alone_is_quiet(self):
        # Many tools keep base64 data in attributes (skin weights, thumbnails).
        blob = base64.b64encode(os.urandom(2000)).decode()
        self.assertQuiet(analyzer.analyze_attr(blob))

    def test_data_attribute_holding_code_is_flagged(self):
        payload = "import os\nos.system('echo inert')\n"
        result = analyzer.analyze_attr(payload)
        self.assertIn("hidden_payload", rule_ids(result))
        self.assertFlagged(result, rules.RISK_MEDIUM)

    def test_zip_bomb_is_bounded(self):
        bomb = base64.b64encode(__import__("zlib").compress(b"\x00" * (64 * 1024 * 1024))).decode()
        analyzer.analyze_text("x = '%s'" % bomb, lang="python")  # must return promptly

    def test_deep_nesting_does_not_crash(self):
        analyzer.analyze_text("x = " + "(" * 5000 + "1" + ")" * 5000, lang="python")
        analyzer.analyze_text("exec('" + "\\'" * 100 + "')", lang="python")


class AsciiScenes(Sandbox):
    def scan(self, data, name="scene.ma"):
        return scanner.scan_file(self.write(name, data))

    def test_clean_scene_has_no_hits(self):
        report = self.scan(fixtures.ma_scene())
        self.assertEqual(report["hits"], [])
        self.assertTrue(report["parse_ok"])

    def test_v3_false_positives_are_gone(self):
        # v3: "high" for any expression or unknown node, and it deleted nodes
        # that merely had one of these words in their name.
        nodes = (b'createNode transform -n "vaccine_bottle_geo";\n'
                 b'createNode transform -n "river_diversion_channel";\n'
                 b'createNode mesh -n "leukocyte_cellShape" -p "vaccine_bottle_geo";\n'
                 b'createNode unknown -n "fromMissingPlugin";\n'
                 b'\tsetAttr ".ufd" -type "string" "someData";\n'
                 b'createNode unknownDag -n "alsoMissing";\n')
        self.assertEqual(self.scan(fixtures.ma_scene(nodes))["hits"], [])

    def test_vaccine_script_node_found(self):
        report = self.scan(fixtures.ma_scene(fixtures.script_node("vaccine_gene", fixtures.INERT, python=True)))
        self.assertEqual(report["risk"], "high")
        self.assertIn("Vaccine (PuTianTongQi)", report["families"])
        self.assertEqual(report["hits"][0]["node"], "vaccine_gene")

    def test_payload_split_across_lines_is_reassembled(self):
        # Maya wraps long strings as ("..." + "..."), which hid strings from line scans.
        node = (b'createNode script -n "harmless_name";\n'
                b'\tsetAttr ".b" -type "string" (\n'
                b'\t\t"import vacc"\n'
                b'\t\t+ "ine\\nprint(1)");\n'
                b'\tsetAttr ".st" 1;\n\tsetAttr ".stp" 1;\n')
        report = self.scan(fixtures.ma_scene(node))
        self.assertEqual(report["risk"], "high", report)

    def test_payload_in_official_node_name_is_not_exempt(self):
        # v3 skipped these nodes by name, so hiding here beat it completely.
        report = self.scan(fixtures.ma_scene(fixtures.script_node(
            "uiConfigurationScriptNode1", 'python("import os; os.system(\\"echo inert\\")");', script_type=3)))
        self.assertGreaterEqual(report["score"], rules.RISK_MEDIUM, report)

        node = fixtures.script_node("sceneConfigurationScriptNode", 'system("echo inert");', script_type=6)
        report = self.scan(fixtures.ma_scene(node))
        self.assertIn("official_name_abuse", [item["rule"] for item in report["hits"][0]["evidence"]])

    def test_injected_top_level_statement(self):
        report = self.scan(fixtures.ma_scene(before=b'python("print(1)");\n'))
        self.assertTrue(any(hit["reason"] == "ma_unexpected_statement" for hit in report["hits"]), report)

    def test_statement_hidden_after_semicolon(self):
        node = b'createNode transform -n "x"; system("echo inert");\n'
        report = self.scan(fixtures.ma_scene(node))
        self.assertGreaterEqual(report["score"], rules.RISK_MEDIUM, report)

    def test_backtick_inside_allowed_statement(self):
        node = (b'createNode transform -n "x";\n'
                b'\tsetAttr ".notes" -type "string" `system "echo inert"`;\n')
        report = self.scan(fixtures.ma_scene(node))
        self.assertTrue(any(hit["reason"] == "ma_inline_code" or "ma_inline_code" in
                            [item["rule"] for item in hit["evidence"]] for hit in report["hits"]), report)

    def test_call_inside_allowed_statement(self):
        node = (b'createNode transform -n "x";\n'
                b'\tsetAttr ".notes" -type "string" (python("print(1)"));\n')
        report = self.scan(fixtures.ma_scene(node))
        self.assertGreaterEqual(report["score"], rules.RISK_MEDIUM, report)

    def test_comments_do_not_trigger(self):
        report = self.scan(fixtures.ma_scene(before=b'// python("import vaccine");\n/* system("x"); */\n'))
        self.assertEqual(report["hits"], [])

    def test_semicolon_and_quotes_inside_string(self):
        node = (b'createNode transform -n "x";\n'
                b'\tsetAttr ".notes" -type "string" "a; b \\"c\\"; python(\\"d\\")";\n')
        doc_path = self.write("q.ma", fixtures.ma_scene(node))
        doc = parse_ma(doc_path)
        self.assertEqual(doc.anomalies, [])
        self.assertEqual([n for n in doc.nodes if n.name == "x"][0].strings["notes"], 'a; b "c"; python("d")')

    def test_hidden_payload_on_any_node(self):
        node = (b'createNode network -n "settings";\n'
                b'\taddAttr -ci true -sn "data" -ln "data" -dt "string";\n'
                b'\tsetAttr ".data" -type "string" "import os\\nos.system(\'echo inert\')";\n')
        report = self.scan(fixtures.ma_scene(node))
        self.assertGreaterEqual(report["score"], rules.RISK_MEDIUM, report)
        self.assertEqual(report["hits"][0]["removable"], "attr")

    def test_network_chunk_nodes_by_type_only(self):
        as_network = b'createNode network -n "codeChunk0";\n\taddAttr -ci true -sn "code" -ln "code" -dt "string";\n'
        as_mesh = b'createNode transform -n "codeChunk0";\n'
        self.assertEqual(self.scan(fixtures.ma_scene(as_network))["risk"], "high")
        self.assertEqual(self.scan(fixtures.ma_scene(as_mesh))["hits"], [])

    def test_unusual_file_command(self):
        report = self.scan(fixtures.ma_scene(before=b'file -f -o "C:/other/scene.ma";\n'))
        self.assertTrue(report["hits"], report)

    def test_reference_is_recorded_not_flagged(self):
        data = fixtures.ma_scene(before=b'file -rdi 1 -ns "R" -rfn "RRN" -typ "mayaAscii" "C:/a/b.ma";\n'
                                        b'file -r -ns "R" -dr 1 -rfn "RRN" -typ "mayaAscii" "C:/a/b.ma";\n')
        report = self.scan(data)
        self.assertEqual(report["hits"], [])
        self.assertIn("C:/a/b.ma", report["references"])

    def test_binary_content_with_ma_extension_is_detected(self):
        iff = fixtures.Iff()
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "vaccine_gene", fixtures.INERT, python=True), iff=iff)
        report = self.scan(data, name="disguised.ma")
        self.assertEqual(report["type"], "mb")
        self.assertEqual(report["risk"], "high")

    def test_unterminated_string_does_not_hide_rest(self):
        report = self.scan(fixtures.HEADER + b'createNode transform -n "x;\npython("print(1)");\n')
        self.assertTrue(report["parse_ok"])

    def test_empty_and_garbage_files(self):
        self.assertEqual(self.scan(b"")["hits"], [])
        scanner.scan_file(self.write("garbage.ma", os.urandom(4096)))

    def test_non_scene_and_missing_file(self):
        self.assertEqual(scanner.scan_file(self.write("a.txt", b"x"))["note"], "Not a Maya scene (.ma/.mb).")
        self.assertEqual(scanner.scan_file(os.path.join(self.dir, "none.ma"))["note"], "File does not exist.")


class AsciiCleaning(Sandbox):
    def test_removing_last_node_keeps_all_connections(self):
        # v3 treated everything up to the next createNode as part of the node, so
        # a malicious LAST node took every connectAttr in the file with it.
        data = fixtures.ma_scene(fixtures.script_node("vaccine_gene", fixtures.INERT, python=True))
        source = self.write("scene.ma", data)
        report = cleaner.clean_file(source)
        self.assertTrue(report["cleaned"], report)
        cleaned = self.read(report["output_path"])
        self.assertNotIn(b"vaccine_gene", cleaned)
        self.assertEqual(cleaned, fixtures.ma_scene())
        self.assertEqual(report["remaining_hits_count"], 0)

    def test_gbk_bytes_survive_byte_for_byte(self):
        # v3 decoded as UTF-8 and dropped what didn't fit, corrupting Chinese text.
        chinese = u'createNode transform -n "x";\n\tsetAttr ".notes" -type "string" "中文备注";\n'.encode("gbk")
        infected = fixtures.ma_scene(chinese, fixtures.script_node("breed_gene", fixtures.INERT))
        expected = fixtures.ma_scene(chinese)
        report = cleaner.clean_file(self.write("gbk.ma", infected))
        self.assertEqual(self.read(report["output_path"]), expected)

    def test_crlf_line_endings_preserved(self):
        data = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT)).replace(b"\n", b"\r\n")
        expected = fixtures.ma_scene().replace(b"\n", b"\r\n")
        report = cleaner.clean_file(self.write("crlf.ma", data))
        self.assertEqual(self.read(report["output_path"]), expected)

    def test_connections_to_removed_node_are_removed(self):
        node = fixtures.script_node("breed_gene", fixtures.INERT)
        data = fixtures.ma_scene(node).replace(
            b'connectAttr "polyCube1.out"', b'connectAttr "breed_gene.msg" "box.nts";\nconnectAttr "polyCube1.out"')
        report = cleaner.clean_file(self.write("links.ma", data))
        self.assertEqual(report["links_removed"], 1)
        self.assertEqual(self.read(report["output_path"]), fixtures.ma_scene())

    def test_shared_node_loses_only_the_bad_attribute(self):
        block = (b'select -ne :defaultRenderGlobals;\n'
                 b'\tsetAttr ".ren" -type "string" "arnold";\n'
                 b'\tsetAttr ".preMel" -type "string" "import vaccine";\n')
        source = self.write("attr.ma", fixtures.HEADER + block + fixtures.FOOTER)
        report = cleaner.clean_file(source)
        cleaned = self.read(report["output_path"])
        self.assertIn(b'"arnold"', cleaned)
        self.assertNotIn(b"vaccine", cleaned)

    def test_injected_statement_removed(self):
        data = fixtures.ma_scene(before=b'system("echo inert");\npython("import vaccine");\n')
        report = cleaner.clean_file(self.write("stmt.ma", data))
        self.assertEqual(self.read(report["output_path"]), fixtures.ma_scene())

    def test_default_threshold_leaves_medium_risk_alone(self):
        node = fixtures.script_node("batch_tool", "import subprocess\nsubprocess.call(['x'])\n",
                                    script_type=0, python=True)
        source = self.write("medium.ma", fixtures.ma_scene(node))
        report = cleaner.clean_file(source)
        self.assertFalse(report["cleaned"], report)
        report = cleaner.clean_file(source, threshold=rules.RISK_LOW)
        self.assertTrue(report["cleaned"], report)

    def test_in_place_keeps_backup(self):
        data = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT))
        source = self.write("inplace.ma", data)
        report = cleaner.clean_file(source, in_place=True)
        self.assertEqual(report["output_path"], source)
        self.assertEqual(self.read(source), fixtures.ma_scene())
        self.assertEqual(self.read(report["backup_path"]), data)

    def test_second_in_place_clean_keeps_both_backups(self):
        data = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT))
        source = self.write("twice.ma", data)
        first = cleaner.clean_file(source, in_place=True)["backup_path"]
        with open(source, "wb") as handle:
            handle.write(data)
        second = cleaner.clean_file(source, in_place=True)["backup_path"]
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(first) and os.path.isfile(second))

    def test_dry_run_writes_nothing(self):
        data = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT))
        source = self.write("dry.ma", data)
        report = cleaner.clean_file(source, dry_run=True)
        self.assertFalse(report["cleaned"])
        self.assertEqual(len(report["removed"]), 1)
        self.assertEqual(os.listdir(self.dir), ["dry.ma"])

    def test_clean_file_is_untouched_and_no_output(self):
        source = self.write("fine.ma", fixtures.ma_scene())
        report = cleaner.clean_file(source)
        self.assertFalse(report["cleaned"])
        self.assertEqual(os.listdir(self.dir), ["fine.ma"])

    def test_no_temp_files_left_behind(self):
        source = self.write("t.ma", fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT)))
        cleaner.clean_file(source)
        self.assertEqual(sorted(os.listdir(self.dir)), ["t.cleaned.ma", "t.ma"])


class BinaryScenes(Sandbox):
    def build(self, *groups, **kwargs):
        iff = kwargs.pop("iff", None) or fixtures.Iff()
        return iff, fixtures.mb_scene(*groups, iff=iff, **kwargs)

    def test_clean_scene_has_no_hits(self):
        _iff, data = self.build()
        report = scanner.scan_file(self.write("a.mb", data))
        self.assertEqual(report["hits"], [], report)
        self.assertTrue(report["parse_ok"])

    def test_script_node_found_without_maya(self):
        iff = fixtures.Iff()
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "innocent", "import vaccine", python=True), iff=iff)
        report = scanner.scan_file(self.write("b.mb", data))
        self.assertEqual(report["risk"], "high", report)
        self.assertEqual(report["hits"][0]["node"], "innocent")

    def test_old_32bit_iff_layout(self):
        iff = fixtures.Iff(wide=False)
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "breed_gene", fixtures.INERT), iff=iff)
        report = scanner.scan_file(self.write("c.mb", data))
        self.assertEqual(report["risk"], "high", report)

    def test_script_language_and_type_read_from_doubles(self):
        iff = fixtures.Iff()
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "n", "print(1)", script_type=7, python=True), iff=iff)
        node = [n for n in parse_mb(self.write("d.mb", data)).nodes if n.name == "n"][0]
        self.assertEqual(node.numbers, {"st": 7, "stp": 1})

    def test_marker_in_unparsed_chunk_is_caught_by_raw_sweep(self):
        iff = fixtures.Iff()
        odd = iff.group(b"XFRM", iff.crea("mesh1"), iff.leaf(b"ZZZZ", b"maya_secure_system"))
        data = fixtures.mb_scene(odd, iff=iff)
        report = scanner.scan_file(self.write("e.mb", data))
        self.assertTrue(any(hit["reason"] == "raw_family_string" for hit in report["hits"]), report)

    def test_truncated_file_reports_and_still_sweeps(self):
        iff = fixtures.Iff()
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "vaccine_gene", fixtures.INERT), iff=iff)
        report = scanner.scan_file(self.write("f.mb", data[:-40]))
        self.assertFalse(report["parse_ok"])
        self.assertGreaterEqual(report["score"], rules.RISK_HIGH, report)

    def test_crafted_sizes_do_not_crash(self):
        iff = fixtures.Iff()
        data = bytearray(fixtures.mb_scene(iff=iff))
        struct.pack_into(">Q", data, 8 + 20, 2 ** 62)
        report = scanner.scan_file(self.write("g.mb", bytes(data)))
        self.assertFalse(report["parse_ok"])

    def test_tiny_and_empty_binary(self):
        self.assertFalse(scanner.scan_file(self.write("h.mb", b"FOR8"))["parse_ok"])
        scanner.scan_file(self.write("i.mb", b""))

    def test_surgical_removal_recomputes_sizes(self):
        iff = fixtures.Iff()
        bad = fixtures.mb_script_node(iff, "breed_gene", fixtures.INERT)
        infected = fixtures.mb_scene(bad, iff=iff, connections=[("benign_expr.out[0]", "box.tx"),
                                                                ("breed_gene.msg", "box.nts")])
        report = cleaner.clean_file(self.write("clean.mb", infected))
        self.assertTrue(report["cleaned"], report)
        self.assertEqual(report["links_removed"], 1)
        expected = fixtures.mb_scene(iff=iff)
        self.assertEqual(self.read(report["output_path"]), expected)

    def test_surgical_removal_32bit(self):
        iff = fixtures.Iff(wide=False)
        bad = fixtures.mb_script_node(iff, "breed_gene", fixtures.INERT)
        report = cleaner.clean_file(self.write("clean32.mb", fixtures.mb_scene(bad, iff=iff)))
        self.assertEqual(self.read(report["output_path"]), fixtures.mb_scene(iff=iff))

    def test_attribute_removal_keeps_node(self):
        iff = fixtures.Iff()
        holder = iff.group(b"NTWK", iff.crea("settings"), iff.string("keep", "ok"),
                           iff.string("data", "import vaccine"))
        report = cleaner.clean_file(self.write("attr.mb", fixtures.mb_scene(holder, iff=iff)))
        self.assertTrue(report["cleaned"], report)
        doc = parse_mb(report["output_path"])
        self.assertTrue(doc.parse_ok)
        node = [n for n in doc.nodes if n.name == "settings"][0]
        self.assertEqual(node.strings, {"keep": "ok"})

    def test_refuses_to_clean_what_it_cannot_parse(self):
        iff = fixtures.Iff()
        data = fixtures.mb_scene(fixtures.mb_script_node(iff, "vaccine_gene", fixtures.INERT), iff=iff)
        source = self.write("broken.mb", data[:-40])
        report = cleaner.clean_file(source)
        self.assertFalse(report["cleaned"])
        self.assertEqual(self.read(source), data[:-40])


class Pool(Sandbox):
    def make_files(self, count):
        bad = fixtures.ma_scene(fixtures.script_node("breed_gene", fixtures.INERT))
        paths = []
        for index in range(count):
            paths.append(self.write("sub/s%02d.ma" % index, bad if index % 2 else fixtures.ma_scene()))
        return paths

    def test_folder_scan_through_worker_processes(self):
        self.make_files(6)
        payload = scanner.scan_folder(self.dir, workers=2, interpreter=sys.executable)
        self.assertEqual(payload["summary"], {"files": 6, "flagged": 3})
        self.assertEqual(payload["files_with_hits"], 3)
        self.assertEqual(len(payload["results"]), 6)

    def test_results_keep_job_order(self):
        paths = self.make_files(5)
        jobs = [{"op": "scan", "path": path} for path in paths]
        results, canceled = run_jobs(jobs, workers=3, interpreter=sys.executable)
        self.assertFalse(canceled)
        self.assertEqual([result["path"] for result in results], paths)

    def test_non_ascii_paths_cross_the_worker_channel(self):
        paths = [self.write(u"场景/文件%d.ma" % index, fixtures.ma_scene()) for index in range(3)]
        results, _canceled = run_jobs([{"op": "scan", "path": p} for p in paths], interpreter=sys.executable)
        self.assertEqual([result["path"] for result in results], paths)

    def test_cancel_stops_early(self):
        paths = self.make_files(12)
        results, canceled = run_jobs([{"op": "scan", "path": p} for p in paths], workers=1,
                                     interpreter=sys.executable, cancel_cb=lambda: True)
        self.assertTrue(canceled)
        self.assertLess(len(results), 12)

    def test_unusable_interpreter_falls_back(self):
        paths = self.make_files(4)
        results, _canceled = run_jobs([{"op": "scan", "path": p} for p in paths], workers=1,
                                      interpreter=os.path.join(self.dir, "missing.exe"))
        self.assertEqual(len(results), 4)
        self.assertFalse(any(result.get("risk") == "error" for result in results))

    def test_folder_clean_mirrors_layout(self):
        self.make_files(4)
        out = os.path.join(self.dir, "out")
        payload = cleaner.clean_folder(self.dir, output_root=out, workers=2, interpreter=sys.executable)
        self.assertEqual(payload["summary"]["cleaned"], 2)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "sub", "s01.ma")))
        self.assertFalse(os.path.exists(os.path.join(out, "sub", "s00.ma")))

    def test_folder_clean_skips_its_own_output(self):
        self.make_files(3)
        cleaner.clean_folder(self.dir, workers=1, interpreter=sys.executable)
        again = cleaner.clean_folder(self.dir, workers=1, interpreter=sys.executable)
        self.assertEqual(again["summary"]["files"], 3)


class Persistence(Sandbox):
    def env(self, **extra):
        env = {"app_dir": os.path.join(self.dir, "maya"), "maya_location": "", "sys_path": [], "script_path": []}
        env.update(extra)
        return env

    def paths_hit(self, report):
        return [os.path.relpath(hit["path"], self.dir).replace("\\", "/") for hit in report["hits"]]

    def test_shared_scripts_folder_is_covered(self):
        # This is where the Vaccine family writes, and v3 never looked here.
        self.write("maya/scripts/vaccine.py", fixtures.INERT)
        self.write("maya/scripts/userSetup.py", "import vaccine\n")
        report = persistence.scan_disk(self.env())
        self.assertEqual(sorted(self.paths_hit(report)), ["maya/scripts/userSetup.py", "maya/scripts/vaccine.py"])

    def test_every_version_and_locale_is_covered(self):
        self.write("maya/2022/scripts/userSetup.py", "import vaccine\n")
        self.write("maya/2023/zh_CN/prefs/scripts/userSetup.mel", "autoUpdatoAttrEnd;\n")
        self.write("maya/2027/Maya.env", "PYTHONPATH=C:\\Users\\Public\\x\n")
        report = persistence.scan_disk(self.env())
        self.assertEqual(sorted(self.paths_hit(report)), [
            "maya/2022/scripts/userSetup.py", "maya/2023/zh_CN/prefs/scripts/userSetup.mel", "maya/2027/Maya.env"])

    def test_real_sys_path_is_covered(self):
        self.write("pipeline/userSetup.py", "import maya_secure_system\n")
        report = persistence.scan_disk(self.env(sys_path=[os.path.join(self.dir, "pipeline")]))
        self.assertEqual(self.paths_hit(report), ["pipeline/userSetup.py"])

    def test_maya_install_l10n_file(self):
        header = persistence.MEL_INFECTION_HEADER.decode()
        self.write("Maya/resources/l10n/zh_CN/plug-ins/animImportExport.pres.mel", "stock();\n" + header + "x\n")
        self.write("Maya/resources/l10n/ja_JP/plug-ins/animImportExport.pres.mel", "stock();\n")
        report = persistence.scan_disk(self.env(maya_location=os.path.join(self.dir, "Maya")))
        self.assertEqual(self.paths_hit(report), ["Maya/resources/l10n/zh_CN/plug-ins/animImportExport.pres.mel"])

    def test_normal_startup_files_are_quiet(self):
        self.write("maya/2024/scripts/userSetup.py",
                   "import maya.cmds as cmds\ncmds.evalDeferred('import mytools; mytools.setup()')\n")
        self.write("maya/2024/prefs/pluginPrefs.mel", 'evalDeferred("autoLoadPlugin(\\"\\", \\"fbxmaya\\", \\"fbxmaya\\")");\n')
        self.write("maya/2024/Maya.env", "MAYA_SCRIPT_PATH=D:\\tools\\scripts\nPYTHONPATH=\\\\server\\pipeline\n")
        self.assertEqual(persistence.scan_disk(self.env())["hits"], [])

    # What a stock Maya 2022 writes.  Scanned as one script, this looked like a virus.
    STOCK_PREFS = (
        b'optionVar -ca "TrustCenterCommandsList"\n'
        b' -sva "TrustCenterCommandsList" "system" -sva "TrustCenterCommandsList" "fopen"\n'
        b' -sva "TrustCenterCommandsList" "fprint" -sva "TrustCenterCommandsList" "exec"\n'
        b' -sva "SafeModeUserSetupHash" "C:/Program Files/Autodesk/Maya2022/plug-ins/MASH/scripts/userSetup.py=121"\n'
        b' -iv "SafeModeUserSetupHashOption" 1;\n'
        b'makePaintable -attrType "multiFloat" -sm "deformer" "blendShape" "weights";\n'
        b'keyTangent -global -inTangentType auto;\n'
    )

    def test_stock_prefs_are_quiet(self):
        self.write("maya/2022/prefs/userPrefs.mel", self.STOCK_PREFS)
        self.assertEqual(persistence.scan_disk(self.env())["hits"], [])
        result = persistence.inspect_file(os.path.join(self.dir, "maya", "2022", "prefs", "userPrefs.mel"))
        self.assertLess(result["score"], rules.RISK_LOW, result)

    def test_prefs_with_injected_code_are_flagged(self):
        for injected in (b'system("echo inert");\n', b'python("import os");\n',
                         b'$f = fopen("x", "a"); fprint $f "y";\n', b'scriptJob -e "idle" "x";\n'):
            path = self.write("maya/2022/prefs/userPrefs.mel", self.STOCK_PREFS + injected)
            result = persistence.inspect_file(path)
            self.assertGreaterEqual(result["score"], rules.RISK_MEDIUM, (injected, result))

    def test_prefs_family_marker_in_a_value_is_flagged(self):
        path = self.write("maya/2022/prefs/userPrefs.mel",
                          self.STOCK_PREFS + b'optionVar -sv "x" "import vaccine";\n')
        self.assertGreaterEqual(persistence.inspect_file(path)["score"], rules.RISK_HIGH)

    def test_plugin_prefs_autoload_lines_are_quiet(self):
        path = self.write("maya/2024/prefs/pluginPrefs.mel",
                          b'evalDeferred("autoLoadPlugin(\\"\\", \\"mayashield.py\\", \\"mayashield\\")");\n')
        self.assertLess(persistence.inspect_file(path)["score"], rules.RISK_LOW)

    def test_old_marker_no_longer_exempts_a_file(self):
        # v3 skipped any file containing this comment, so malware only had to add it.
        self.write("maya/scripts/userSetup.py", "# MAYASHIELD_IMMUNIZED\nimport vaccine\n")
        self.assertEqual(len(persistence.scan_disk(self.env())["hits"]), 1)

    def test_tampered_guard_block_is_not_exempt(self):
        evil = persistence.GUARD_BLOCK.replace("guard.startup_check()", "import vaccine")
        self.write("maya/scripts/userSetup.py", evil)
        self.assertEqual(len(persistence.scan_disk(self.env())["hits"]), 1)

    def test_scan_all_finds_tools_but_quick_scan_does_not(self):
        self.write("maya/2024/scripts/tools/deep/thing.py", "import vaccine\n")
        self.assertEqual(persistence.scan_disk(self.env())["hits"], [])
        self.assertEqual(len(persistence.scan_disk(self.env(), scan_all=True)["hits"]), 1)

    def test_pyc_files_are_checked(self):
        self.write("maya/scripts/__pycache__/vaccine.cpython-310.pyc", b"\x00\x01binary")
        self.assertEqual(len(persistence.scan_disk(self.env())["hits"]), 1)

    def test_clean_repairs_python_and_keeps_user_lines(self):
        path = self.write("maya/scripts/userSetup.py",
                          "import mytools\nimport vaccine\ncmds.evalDeferred('leukocyte = vaccine.phage()')\n"
                          "cmds.evalDeferred('leukocyte.occupation()')\nmytools.go()\n")
        with _data_dir(os.path.join(self.dir, "maya")):
            report = persistence.clean_disk(self.env())
            self.assertEqual(report["actions"][0]["status"], "repaired", report["actions"])
            self.assertEqual(self.read(path).decode(), "import mytools\nmytools.go()\n")
            self.assertEqual(len(persistence.list_quarantine()), 1)

    def test_clean_repairs_mel_by_cutting_header_onward(self):
        header = persistence.MEL_INFECTION_HEADER.decode()
        path = self.write("maya/scripts/userSetup.mel", "myStudioSetup();\n" + header + "ration File..\nvirus();\n")
        with _data_dir(os.path.join(self.dir, "maya")):
            persistence.clean_disk(self.env())
            self.assertEqual(self.read(path), b"myStudioSetup();\n")

    def test_clean_quarantines_a_pure_drop_file_and_restores_it(self):
        path = self.write("maya/scripts/vaccine.py", fixtures.INERT)
        with _data_dir(os.path.join(self.dir, "maya")):
            report = persistence.clean_disk(self.env())
            self.assertEqual(report["actions"][0]["status"], "quarantined")
            self.assertFalse(os.path.exists(path))
            record = persistence.list_quarantine()[0]
            self.assertEqual(persistence.restore(record["id"])["status"], "restored")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(persistence.list_quarantine(), [])

    def test_dry_run_touches_nothing(self):
        path = self.write("maya/scripts/vaccine.py", fixtures.INERT)
        with _data_dir(os.path.join(self.dir, "maya")):
            report = persistence.clean_disk(self.env(), dry_run=True)
            self.assertEqual(report["actions"][0]["status"], "would_quarantine")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(persistence.list_quarantine(), [])

    def test_clean_and_restore_keep_the_manifest_consistent(self):
        path = self.write("maya/scripts/userSetup.py", b"import mine\nimport vaccine\n")
        with _data_dir(os.path.join(self.dir, "maya")):
            persistence.clean_disk(self.env())
            record = persistence.list_quarantine()[0]
            self.assertIn("repaired_sha256", record)
            self.assertEqual(persistence.restore(record["id"])["status"], "restored")
            self.assertEqual(self.read(path), b"import mine\nimport vaccine\n")

    def test_guard_block_create_refresh_and_scan_quiet(self):
        folder = os.path.join(self.dir, "maya", "2024", "scripts")
        self.assertEqual(persistence.write_guard_block(folder)["status"], "created")
        self.assertEqual(persistence.write_guard_block(folder)["status"], "already_immunized")
        self.assertEqual(persistence.scan_disk(self.env())["hits"], [])

    def test_guard_block_keeps_user_content(self):
        path = self.write("maya/2024/scripts/userSetup.py", "import mytools\n")
        persistence.write_guard_block(os.path.dirname(path))
        text = self.read(path).decode()
        self.assertTrue(text.startswith("import mytools\n"))
        self.assertIn(persistence.GUARD_BEGIN, text)

    def test_generated_guard_block_compiles(self):
        compile(persistence.GUARD_BLOCK, "guard", "exec")

    def test_legacy_block_replaced_without_losing_user_code(self):
        legacy = _build_legacy_block()
        path = self.write("maya/2024/scripts/userSetup.py", "import mine\n\n\n" + legacy + "\nmine.go()\n")
        result = persistence.write_guard_block(os.path.dirname(path))
        self.assertEqual(result["status"], "replaced_v3_block")
        text = self.read(path).decode()
        self.assertNotIn("MAYASHIELD_IMMUNIZED", text)
        self.assertIn("import mine", text)
        self.assertIn("mine.go()", text)
        compile(text, "userSetup", "exec")


class _data_dir(object):
    """Point the quarantine at a sandbox."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.saved = os.environ.get("MAYA_APP_DIR")
        os.environ["MAYA_APP_DIR"] = self.path

    def __exit__(self, *_exc):
        if self.saved is None:
            del os.environ["MAYA_APP_DIR"]
        else:
            os.environ["MAYA_APP_DIR"] = self.saved


def _build_legacy_block():
    """The exact block v3.1.0 wrote, cut from the archived source."""
    with open(os.path.join(ROOT, "archive", "mayashield_3.1.0.py"), encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "immunize":
            for item in node.body:
                if isinstance(item, ast.Assign) and getattr(item.targets[0], "id", "") == "payload":
                    code = compile(ast.Expression(item.value), "payload", "eval")
                    return eval(code, {"IMMUNE_MARKER": "# MAYASHIELD_IMMUNIZED", "_now": lambda: "2026-01-01 00:00:00"})
    raise AssertionError("immunize payload not found")


class LanguageLevel(unittest.TestCase):
    def test_every_source_file_parses_as_python_37(self):
        # Maya 2022 runs Python 3.7.
        files = glob.glob(os.path.join(ROOT, "mayashield_core", "*.py")) + [os.path.join(ROOT, "mayashield.py")]
        self.assertGreater(len(files), 8)
        for path in files:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if sys.version_info >= (3, 8):
                ast.parse(source, filename=path, feature_version=(3, 7))
            else:
                ast.parse(source, filename=path)

    def test_rule_tables_are_consistent(self):
        known = set()
        for table in (rules.TEXT_RULES, rules.MEL_RULES, rules.PY_FALLBACK_RULES):
            known.update(item[0] for item in table)
        known.update(item[0] for item in rules.PY_CALL_RULES.values())
        known.update(item[0] for item in rules.PY_MAYA_LEAF_RULES.values())
        known.update(("py_file_write", "confirm_dialog"))
        for _rule, _category, _weight, slots in rules.COMBO_RULES:
            for slot in slots:
                for option in slot:
                    self.assertIn(option, known)


if __name__ == "__main__":
    unittest.main()
