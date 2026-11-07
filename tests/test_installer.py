# -*- coding: utf-8 -*-
"""Tests the built Setup.exe against sandbox folders, using its silent mode.

Run after installer/build.py.  Uses --app-dir and --maya so the real Maya and your
real preferences stay untouched, and --no-register to leave the registry alone.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = glob.glob(os.path.join(ROOT, "dist", "MayaShield-*-Setup.exe"))
AUTOLOAD = b'evalDeferred("autoLoadPlugin(\\"\\", \\"mayashield.py\\", \\"mayashield\\")");'
GBK_LINE = u'// 中文注释 keep me'.encode("gbk")


@unittest.skipUnless(os.name == "nt" and SETUP, "needs the built installer on Windows")
class SilentInstall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mayashield_setup_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.app = os.path.join(self.dir, "docs", "maya")
        os.makedirs(self.app)
        self.log = os.path.join(self.dir, "setup.log")

    def fake_maya(self, version, old_copy=False, python=False):
        root = os.path.join(self.dir, "Program Files", "Maya%d" % version)
        os.makedirs(os.path.join(root, "bin", "plug-ins"))
        open(os.path.join(root, "bin", "maya.exe"), "wb").close()
        if old_copy:
            with open(os.path.join(root, "bin", "plug-ins", "mayashield.py"), "wb") as handle:
                handle.write(b"# v3.1.0\n")
        if python:
            self.install_python_as_mayapy(os.path.join(root, "bin"))
        return root

    def install_python_as_mayapy(self, folder):
        """A real interpreter under mayapy's name, so the self-test truly runs."""
        try:
            listing = subprocess.check_output(["py", "-0p"], stderr=subprocess.STDOUT).decode("utf-8", "replace")
        except (OSError, subprocess.CalledProcessError):
            listing = ""
        found = [line.split()[-1] for line in listing.splitlines() if line.strip().lower().endswith("python.exe")]
        found = [path for path in found if os.path.isfile(path) and "WindowsApps" not in path]
        if not found:
            self.skipTest("no copyable Python on this machine")
        home = os.path.dirname(found[0])
        shutil.copy2(found[0], os.path.join(folder, "mayapy.exe"))
        for pattern in ("python3*.dll", "vcruntime*.dll"):
            for library in glob.glob(os.path.join(home, pattern)):
                shutil.copy2(library, folder)
        with open(os.path.join(folder, "pyvenv.cfg"), "w") as handle:
            handle.write("home = %s\n" % home)

    def prefs(self, version, content=None, language=None, ran=True):
        parts = [self.app, str(version)] + ([language] if language else []) + ["prefs"]
        folder = os.path.join(*parts)
        os.makedirs(folder, exist_ok=True)
        if ran:
            with open(os.path.join(folder, "userPrefs.mel"), "wb") as handle:
                handle.write(b"// prefs\n")
        path = os.path.join(folder, "pluginPrefs.mel")
        if content is not None:
            with open(path, "wb") as handle:
                handle.write(content)
        return path

    def run_setup(self, *arguments, **options):
        command = [SETUP[0], "--silent", "--force", "--no-register", "--app-dir", self.app, "--log", self.log]
        command += list(arguments)
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, **options).returncode

    def maya_args(self, *pairs):
        result = []
        for version, path in pairs:
            result += ["--maya", "%d=%s" % (version, path)]
        return result

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def log_text(self):
        return self.read(self.log).decode("utf-8") if os.path.exists(self.log) else ""

    # ---------------------------------------------------------------- detection
    def test_list_filters_stale_entries_and_marks_support(self):
        real = self.fake_maya(2024, old_copy=True)
        old = self.fake_maya(2019)
        stale = os.path.join(self.dir, "uninstalled_maya")
        code = self.run_setup("--list", *self.maya_args((2024, real), (2019, old), (2026, stale)))
        self.assertEqual(code, 0)
        found = json.loads(self.read(self.log).decode("utf-8"))
        self.assertEqual([item["version"] for item in found], [2019, 2024])
        self.assertEqual(found[0]["supported"], False)
        self.assertEqual(found[1]["supported"], True)
        self.assertEqual(found[1]["oldCopy"], True)

    def test_no_supported_maya(self):
        old = self.fake_maya(2020)
        self.assertEqual(self.run_setup(*self.maya_args((2020, old))), 2)

    # ---------------------------------------------------------------- all-users install
    def test_machine_install_layout_and_old_copy_replaced(self):
        maya = self.fake_maya(2024, old_copy=True)
        pref = self.prefs(2024, b"//Maya Preference 2024\n" + GBK_LINE + b'\nevalDeferred("autoLoadPlugin(\\"\\", \\"fbxmaya\\", \\"fbxmaya\\")");\n')
        self.assertEqual(self.run_setup("--no-verify", *self.maya_args((2024, maya))), 0, self.log_text())

        modules = os.path.join(maya, "modules")
        self.assertEqual(self.read(os.path.join(modules, "mayashield.mod")).strip(), b"+ mayashield 4.0.0 ./MayaShield")
        for relative in ("plug-ins/mayashield.py", "scripts/userSetup.py", "scripts/mayashield_core/__init__.py",
                         "scripts/mayashield_core/manifest.json", "icons/mayashield.png"):
            self.assertTrue(os.path.isfile(os.path.join(modules, "MayaShield", relative)), relative)

        self.assertFalse(os.path.exists(os.path.join(maya, "bin", "plug-ins", "mayashield.py")))
        self.assertEqual(self.read(os.path.join(modules, "MayaShield.legacy-backup", "mayashield.py")), b"# v3.1.0\n")

        text = self.read(pref)
        self.assertIn(AUTOLOAD, text)
        self.assertIn(GBK_LINE, text, "existing non-UTF-8 bytes must survive")
        self.assertIn(b"fbxmaya", text)

    def test_every_language_prefs_folder_gets_the_line(self):
        maya = self.fake_maya(2023)
        plain = self.prefs(2023, b"// a\n")
        chinese = self.prefs(2023, b"// b\n", language="zh_CN")
        self.run_setup("--no-verify", *self.maya_args((2023, maya)))
        self.assertIn(AUTOLOAD, self.read(plain))
        self.assertIn(AUTOLOAD, self.read(chinese))

    def test_never_run_version_is_left_to_the_bootstrap(self):
        # Maya ignores pluginPrefs.mel in brand new preferences, as tested in real Maya.
        maya = self.fake_maya(2026)
        self.assertEqual(self.run_setup("--no-verify", *self.maya_args((2026, maya))), 0)
        self.assertFalse(os.path.exists(os.path.join(self.app, "2026", "prefs", "pluginPrefs.mel")))
        self.assertTrue(os.path.isfile(os.path.join(maya, "modules", "MayaShield", "scripts", "userSetup.py")))
        self.assertIn("first start" if "first start" in self.log_text() else u"首次启动", self.log_text())

    def test_reinstall_is_idempotent(self):
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b"// keep\n")
        for _attempt in range(3):
            self.assertEqual(self.run_setup("--no-verify", *self.maya_args((2024, maya))), 0)
        self.assertEqual(self.read(pref).count(b"mayashield"), 2)   # file name + plug-in name, in one line
        self.assertEqual(self.read(pref).count(b"autoLoadPlugin"), 1)

    def test_old_format_entry_is_replaced(self):
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b'evalDeferred("autoLoadPlugin(\\"\\", \\"D:/old/mayashield.py\\", \\"mayashield\\")");\n')
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        self.assertNotIn(b"D:/old", self.read(pref))
        self.assertEqual(self.read(pref).count(b"autoLoadPlugin"), 1)

    def test_other_plugins_with_similar_names_are_left_alone(self):
        maya = self.fake_maya(2024)
        other = b'evalDeferred("autoLoadPlugin(\\"\\", \\"mayashield_pro.py\\", \\"mayashield_pro\\")");\n'
        pref = self.prefs(2024, other)
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        self.assertIn(other, self.read(pref))
        self.run_setup("--uninstall", *self.maya_args((2024, maya)))
        self.assertIn(other, self.read(pref))
        self.assertNotIn(AUTOLOAD, self.read(pref))

    def test_prefs_with_a_bom_keep_their_exact_bytes(self):
        maya = self.fake_maya(2024)
        start = b"\xef\xbb\xbf// a\n" + u"// 中文\n".encode("utf-8")
        pref = self.prefs(2024, start)
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        self.assertTrue(self.read(pref).startswith(start))

    def test_junction_in_place_of_the_module_folder_keeps_its_target(self):
        maya = self.fake_maya(2024)
        target = os.path.join(self.dir, "working_copy")
        os.makedirs(target)
        with open(os.path.join(target, "precious.txt"), "w") as handle:
            handle.write("keep")
        modules = os.path.join(maya, "modules")
        os.makedirs(modules)
        link = os.path.join(modules, "MayaShield")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if made.returncode != 0:
            self.skipTest("could not create a junction")
        self.assertEqual(self.run_setup("--no-verify", *self.maya_args((2024, maya))), 0, self.log_text())
        self.assertTrue(os.path.isfile(os.path.join(target, "precious.txt")))
        self.assertTrue(os.path.isfile(os.path.join(link, "plug-ins", "mayashield.py")))

    def test_crlf_prefs_stay_crlf(self):
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b"// a\r\n// b\r\n")
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        text = self.read(pref)
        self.assertEqual(text, b"// a\r\n// b\r\n" + AUTOLOAD + b"\r\n")

    def test_old_user_copy_is_moved_because_it_would_load_first(self):
        maya = self.fake_maya(2024)
        old = os.path.join(self.app, "2024", "plug-ins", "mayashield.py")
        os.makedirs(os.path.dirname(old))
        with open(old, "w") as handle:
            handle.write("# old")
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(os.path.join(self.app, "mayashield", "legacy-backup", "2024", "mayashield.py")))

    def test_versions_flag_limits_the_install(self):
        a, b = self.fake_maya(2024), self.fake_maya(2025)
        self.run_setup("--no-verify", "--versions", "2025", *self.maya_args((2024, a), (2025, b)))
        self.assertFalse(os.path.exists(os.path.join(a, "modules", "mayashield.mod")))
        self.assertTrue(os.path.exists(os.path.join(b, "modules", "mayashield.mod")))

    def test_self_test_runs_with_the_mayas_own_python(self):
        maya = self.fake_maya(2027, python=True)
        code = self.run_setup(*self.maya_args((2027, maya)))
        self.assertEqual(code, 0, self.log_text())
        self.assertIn('"ok": true', self.log_text())

    def test_broken_payload_fails_the_self_test(self):
        # Self-test failure must be reported, not swallowed: point at a Python that can't run it.
        maya = self.fake_maya(2027)
        bad = os.path.join(maya, "bin", "mayapy.exe")
        shutil.copy2(os.path.join(os.environ["WINDIR"], "System32", "where.exe"), bad)
        self.assertEqual(self.run_setup(*self.maya_args((2027, maya))), 6, self.log_text())

    # ---------------------------------------------------------------- current-user install
    def test_user_install_touches_only_the_user_folder(self):
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b"// a\n")
        self.assertEqual(self.run_setup("--no-verify", "--scope", "user", *self.maya_args((2024, maya))), 0, self.log_text())
        self.assertFalse(os.path.exists(os.path.join(maya, "modules")))
        modules = os.path.join(self.app, "2024", "modules")
        self.assertTrue(os.path.isfile(os.path.join(modules, "mayashield.mod")))
        self.assertTrue(os.path.isfile(os.path.join(modules, "MayaShield", "plug-ins", "mayashield.py")))
        self.assertIn(AUTOLOAD, self.read(pref))

    def test_user_install_refuses_when_old_copy_would_win(self):
        maya = self.fake_maya(2024, old_copy=True)
        self.assertEqual(self.run_setup("--no-verify", "--scope", "user", *self.maya_args((2024, maya))), 5)
        self.assertTrue(os.path.exists(os.path.join(maya, "bin", "plug-ins", "mayashield.py")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "2024", "modules", "mayashield.mod")))

    def test_machine_install_removes_leftover_user_install(self):
        maya = self.fake_maya(2024)
        self.run_setup("--no-verify", "--scope", "user", *self.maya_args((2024, maya)))
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        self.assertFalse(os.path.exists(os.path.join(self.app, "2024", "modules", "mayashield.mod")))
        self.assertTrue(os.path.exists(os.path.join(maya, "modules", "mayashield.mod")))

    def test_non_ascii_user_folder(self):
        self.app = os.path.join(self.dir, u"用户", "maya")
        os.makedirs(self.app)
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b"// a\n")
        self.assertEqual(self.run_setup("--no-verify", "--scope", "user", *self.maya_args((2024, maya))), 0, self.log_text())
        self.assertIn(AUTOLOAD, self.read(pref))
        mod = self.read(os.path.join(self.app, "2024", "modules", "mayashield.mod"))
        self.assertEqual(mod.decode("ascii").strip(), "+ mayashield 4.0.0 ./MayaShield")

    # ---------------------------------------------------------------- uninstall
    def test_uninstall_removes_everything_and_keeps_user_data(self):
        maya = self.fake_maya(2024)
        pref = self.prefs(2024, b"// keep me\n")
        shelf = os.path.join(self.app, "2024", "prefs", "shelves", "shelf_MayaShield.mel")
        os.makedirs(os.path.dirname(shelf))
        with open(shelf, "w") as handle:
            handle.write("// shelf")
        user_prefs = os.path.join(self.app, "2024", "prefs", "userPrefs.mel")
        with open(user_prefs, "ab") as handle:
            handle.write(b'optionVar -iv "mayashield_autoloadConfigured" 1;\n')
        quarantine = os.path.join(self.app, "mayashield", "quarantine", "x.quarantine")
        os.makedirs(os.path.dirname(quarantine))
        with open(quarantine, "w") as handle:
            handle.write("kept")

        self.assertEqual(self.run_setup("--no-verify", *self.maya_args((2024, maya))), 0)
        self.assertEqual(self.run_setup("--uninstall", *self.maya_args((2024, maya))), 0, self.log_text())

        self.assertFalse(os.path.exists(os.path.join(maya, "modules", "MayaShield")))
        self.assertFalse(os.path.exists(os.path.join(maya, "modules", "mayashield.mod")))
        self.assertEqual(self.read(pref), b"// keep me\n")
        self.assertFalse(os.path.exists(shelf))
        self.assertNotIn(b"mayashield", self.read(user_prefs), "a later reinstall must be able to bootstrap again")
        self.assertTrue(os.path.exists(quarantine), "quarantine holds the user's files and must survive")

    def test_uninstall_of_user_scope(self):
        maya = self.fake_maya(2024)
        self.run_setup("--no-verify", "--scope", "user", *self.maya_args((2024, maya)))
        self.assertEqual(self.run_setup("--uninstall", *self.maya_args((2024, maya))), 0, self.log_text())
        self.assertFalse(os.path.exists(os.path.join(self.app, "2024", "modules", "MayaShield")))

    # ---------------------------------------------------------------- safety
    def test_elevated_step_rejects_a_folder_without_maya(self):
        target = os.path.join(self.dir, "not_maya")
        os.makedirs(target)
        code = subprocess.run([SETUP[0], "--elevated", "install", "--target", "2024=%s" % target,
                               "--elevated-log", self.log], timeout=60).returncode
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(target, "modules")))

    def test_installed_manifest_matches_installed_files(self):
        import hashlib
        maya = self.fake_maya(2024)
        self.run_setup("--no-verify", *self.maya_args((2024, maya)))
        package = os.path.join(maya, "modules", "MayaShield", "scripts", "mayashield_core")
        with open(os.path.join(package, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["version"], "4.0.0")
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256(self.read(os.path.join(package, name))).hexdigest(), digest, name)


if __name__ == "__main__":
    sys.exit(unittest.main())
