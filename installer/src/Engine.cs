using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MayaShieldSetup
{
    /// <summary>
    /// Install and uninstall.  Work in a Maya program folder may need administrator rights and
    /// then runs in an elevated copy of this exe (--elevated).  Everything in the user's own
    /// folders runs here, as the user, because an elevated process may belong to another account.
    /// </summary>
    class Engine
    {
        const string ModuleName = "MayaShield";
        const string ModFile = "mayashield.mod";
        const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\MayaShield";
        static readonly Regex VersionFolder = new Regex(@"^(\d{4})(-x64)?$");
        static readonly Regex LanguageFolder = new Regex(@"^[a-z]{2}_[A-Z]{2}$");

        readonly Options o;
        readonly Reporter report;

        public Engine(Options options, Reporter reporter)
        {
            o = options;
            report = reporter;
        }

        string AppDir
        {
            get
            {
                if (o.AppDir != null)
                    return o.AppDir;
                string env = Environment.GetEnvironmentVariable("MAYA_APP_DIR");
                if (!string.IsNullOrEmpty(env))
                    return env;
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "maya");
            }
        }

        public List<MayaInstall> SelectTargets(List<MayaInstall> all)
        {
            if (o.Versions != null)
            {
                foreach (int version in o.Versions)
                    if (!all.Any(m => m.Version == version && m.Supported))
                        report.Log(L.T("versionNotFound", version));
            }
            return all.Where(m => m.Supported && (o.Versions == null || o.Versions.Contains(m.Version))).ToList();
        }

        // ================================================================ install
        public int Install(List<MayaInstall> targets)
        {
            report.Log(L.T("logStart", BuildInfo.Version, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")));
            report.Begin(targets.Count * (o.NoVerify ? 2 : 3) + (o.NoRegister ? 0 : 1));

            if (!o.Machine)
            {
                List<MayaInstall> blocked = targets.Where(m => m.OldCopy).ToList();
                foreach (MayaInstall maya in blocked)
                    report.Log(L.T("oldNeedsAdmin", maya.Version));
                if (blocked.Count > 0)
                    return Exit.OldNeedsAdmin;
            }

            // 1. Module files
            if (o.Machine && NeedsAdmin(targets))
            {
                int code = RunElevatedCopy("install", targets);
                if (code != Exit.Ok)
                    return code;
                foreach (MayaInstall maya in targets)
                    report.Advance();
            }
            else
            {
                foreach (MayaInstall maya in targets)
                {
                    if (o.Machine)
                        DeployMachine(maya, report);
                    else
                        DeployModule(maya.Version, Path.Combine(AppDir, maya.Version.ToString(), "modules"), report);
                    report.Advance();
                }
            }

            // 2. This user's preferences
            foreach (MayaInstall maya in targets)
            {
                ConfigureUser(maya);
                report.Advance();
            }

            // 3. Self-test with each Maya's own Python
            int result = Exit.Ok;
            if (!o.NoVerify)
            {
                foreach (MayaInstall maya in targets)
                {
                    if (!SelfTest(maya))
                        result = Exit.SelfTestFailed;
                    report.Advance();
                }
            }

            // 4. Apps & features
            if (!o.NoRegister)
            {
                report.Status(L.T("registering"));
                try { Register(); }
                catch (Exception ex) { report.Log(L.T("warning", ex.Message)); }
                report.Advance();
            }
            return result;
        }

        bool NeedsAdmin(IEnumerable<MayaInstall> targets)
        {
            if (IsAdmin())
                return false;
            foreach (MayaInstall maya in targets)
            {
                if (!FileOps.CanWriteTo(Path.Combine(maya.Path, "modules")))
                    return true;
                if (maya.OldCopy && !FileOps.CanWriteTo(Path.GetDirectoryName(maya.OldCopyPath)))
                    return true;
            }
            return false;
        }

        static bool IsAdmin()
        {
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
        }

        /// <summary>Installs into the Maya program folder.  Runs here or in the elevated copy.</summary>
        static void DeployMachine(MayaInstall maya, Reporter report)
        {
            string modules = Path.Combine(maya.Path, "modules");
            Directory.CreateDirectory(modules);
            // A v3 copy in bin/plug-ins would load instead of the module.
            if (File.Exists(maya.OldCopyPath))
            {
                string moved = FileOps.MoveAside(maya.OldCopyPath, Path.Combine(modules, ModuleName + ".legacy-backup"));
                report.Log(L.T("movedOld", maya.Version, moved));
            }
            DeployModule(maya.Version, modules, report);
        }

        /// <summary>Writes modules/MayaShield and modules/mayashield.mod.</summary>
        static void DeployModule(int version, string modules, Reporter report)
        {
            string target = Path.Combine(modules, ModuleName);
            report.Status(L.T("copying", version, target));
            Directory.CreateDirectory(modules);
            if (FileOps.IsLink(target))
            {
                // Someone linked their own folder here: update it in place, never delete through the link.
                Payload.ExtractTo(target);
            }
            else
            {
                string staging = Path.Combine(modules, "." + ModuleName + ".new");
                FileOps.DeleteTree(staging);
                Payload.ExtractTo(staging);
                FileOps.DeleteTree(target);
                Directory.Move(staging, target);
            }
            string mod = "+ mayashield " + BuildInfo.Version + " ./" + ModuleName + "\r\n";
            FileOps.WriteAtomic(Path.Combine(modules, ModFile), Encoding.ASCII.GetBytes(mod));
        }

        void ConfigureUser(MayaInstall maya)
        {
            report.Status(L.T("configuring", maya.Version));
            List<string> versionDirs = VersionDirs(maya.Version);

            foreach (string versionDir in versionDirs)
            {
                // An all-users install replaces this user's own copy, which would otherwise win.
                if (o.Machine)
                    RemoveModule(maya.Version, Path.Combine(versionDir, "modules"), "removedUserCopy");
                // Old single-file copies in the user's plug-in folder load before any module.
                foreach (string name in new[] { "mayashield.py", "mayashield.pyc" })
                {
                    string old = Path.Combine(versionDir, "plug-ins", name);
                    if (File.Exists(old))
                    {
                        string moved = FileOps.MoveAside(old, Path.Combine(AppDir, "mayashield", "legacy-backup", maya.Version.ToString()));
                        report.Log(L.T("movedOld", maya.Version, moved));
                    }
                }
            }

            // Maya ignores pluginPrefs.mel in brand new preferences, so only prefs Maya has
            // really used get the line.  The module's bootstrap covers the rest.
            bool any = false;
            foreach (string versionDir in versionDirs)
            {
                foreach (string prefs in PrefsDirs(versionDir))
                {
                    if (!File.Exists(Path.Combine(prefs, "userPrefs.mel")))
                        continue;
                    any = true;
                    string pluginPrefs = Path.Combine(prefs, "pluginPrefs.mel");
                    try
                    {
                        Prefs.EnsureAutoload(pluginPrefs);
                        report.Log(L.T("autoloadOn", maya.Version, pluginPrefs));
                    }
                    catch (Exception ex)
                    {
                        report.Log(L.T("warning", pluginPrefs + ": " + ex.Message));
                    }
                }
            }
            if (!any)
                report.Log(L.T("firstStart", maya.Version));
        }

        List<string> VersionDirs(int version)
        {
            var found = new List<string>();
            foreach (string name in new[] { version.ToString(), version + "-x64" })
            {
                string path = Path.Combine(AppDir, name);
                if (Directory.Exists(path))
                    found.Add(path);
            }
            return found;
        }

        static List<string> PrefsDirs(string versionDir)
        {
            var found = new List<string> { Path.Combine(versionDir, "prefs") };
            try
            {
                foreach (string folder in Directory.GetDirectories(versionDir))
                    if (LanguageFolder.IsMatch(Path.GetFileName(folder)))
                        found.Add(Path.Combine(folder, "prefs"));
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return found.Where(Directory.Exists).ToList();
        }

        bool SelfTest(MayaInstall maya)
        {
            string mayapy = Path.Combine(maya.Path, "bin", "mayapy.exe");
            if (!File.Exists(mayapy))
            {
                report.Log(L.T("selfTestSkipped", maya.Version));
                return true;
            }
            report.Status(L.T("selfTesting", maya.Version));
            string modules = o.Machine ? Path.Combine(maya.Path, "modules") : Path.Combine(AppDir, maya.Version.ToString(), "modules");
            var start = new ProcessStartInfo(mayapy)
            {
                // -I keeps the user's environment and site-packages out; -B leaves no .pyc in the install.
                Arguments = "-I -B -c \"import os, sys; sys.path.insert(0, os.environ['MAYASHIELD_SELFTEST_PATH']); "
                          + "from mayashield_core import selftest; sys.exit(selftest.main())\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Path.GetTempPath(),
            };
            start.EnvironmentVariables["MAYASHIELD_SELFTEST_PATH"] = Path.Combine(modules, ModuleName, "scripts");
            var output = new StringBuilder();
            int exitCode = -1;
            try
            {
                using (Process process = new Process { StartInfo = start })
                {
                    DataReceivedEventHandler collect = (sender, e) =>
                    {
                        if (e.Data != null)
                            lock (output) output.AppendLine(e.Data);
                    };
                    process.OutputDataReceived += collect;
                    process.ErrorDataReceived += collect;
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    if (!process.WaitForExit(180000))
                    {
                        try { process.Kill(); } catch (Exception) { }
                        lock (output) output.AppendLine("timed out after 180 s");
                    }
                    else
                    {
                        process.WaitForExit();   // flushes the asynchronous readers
                        exitCode = process.ExitCode;
                    }
                }
            }
            catch (Exception ex)
            {
                output.AppendLine(ex.Message);
            }
            string text = output.ToString().Trim();
            if (text.Length > 0)
                report.Log(text);
            bool ok = exitCode == 0 && text.Contains("\"ok\": true");
            report.Log(ok ? L.T("selfTestOk", maya.Version) : L.T("selfTestFailed", maya.Version));
            return ok;
        }

        // ================================================================ uninstall
        public int Uninstall(List<MayaInstall> all)
        {
            report.Log(L.T("logStart", BuildInfo.Version, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")));
            List<MayaInstall> machine = all.Where(m => (o.Versions == null || o.Versions.Contains(m.Version))
                && (File.Exists(Path.Combine(m.Path, "modules", ModFile)) || Directory.Exists(Path.Combine(m.Path, "modules", ModuleName))))
                .ToList();
            List<string> userDirs = AllVersionDirs();
            bool unregister = !o.NoRegister && o.Versions == null;
            report.Begin(machine.Count + userDirs.Count + (unregister ? 1 : 0));

            if (machine.Count == 0)
                report.Log(L.T("nothingInstalled"));
            else if (NeedsAdmin(machine))
            {
                int code = RunElevatedCopy("uninstall", machine);
                if (code != Exit.Ok)
                    return code;
                foreach (MayaInstall maya in machine)
                    report.Advance();
            }
            else
            {
                foreach (MayaInstall maya in machine)
                {
                    RemoveMachine(maya, report);
                    report.Advance();
                }
            }

            foreach (string versionDir in userDirs)
            {
                CleanUser(versionDir);
                report.Advance();
            }

            if (unregister)
            {
                report.Status(L.T("unregistering"));
                try { Unregister(); }
                catch (Exception ex) { report.Log(L.T("warning", ex.Message)); }
                report.Advance();
            }
            return Exit.Ok;
        }

        List<string> AllVersionDirs()
        {
            var found = new List<string>();
            try
            {
                foreach (string folder in Directory.GetDirectories(AppDir))
                {
                    Match match = VersionFolder.Match(Path.GetFileName(folder));
                    if (match.Success && (o.Versions == null || o.Versions.Contains(int.Parse(match.Groups[1].Value))))
                        found.Add(folder);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            found.Sort(StringComparer.OrdinalIgnoreCase);
            return found;
        }

        static void RemoveMachine(MayaInstall maya, Reporter report)
        {
            RemoveModule(maya.Version, Path.Combine(maya.Path, "modules"), "removing", report);
        }

        void RemoveModule(int version, string modules, string message)
        {
            RemoveModule(version, modules, message, report);
        }

        static void RemoveModule(int version, string modules, string message, Reporter report)
        {
            string mod = Path.Combine(modules, ModFile);
            string folder = Path.Combine(modules, ModuleName);
            if (!File.Exists(mod) && !Directory.Exists(folder))
                return;
            report.Status(L.T(message, version, folder));
            if (File.Exists(mod))
            {
                File.SetAttributes(mod, FileAttributes.Normal);
                File.Delete(mod);
            }
            FileOps.DeleteTree(folder);
            FileOps.DeleteTree(Path.Combine(modules, "." + ModuleName + ".new"));
        }

        void CleanUser(string versionDir)
        {
            report.Status(L.T("cleaningPrefs", versionDir));
            int version = int.Parse(VersionFolder.Match(Path.GetFileName(versionDir)).Groups[1].Value);
            RemoveModule(version, Path.Combine(versionDir, "modules"), "removing");
            foreach (string prefs in PrefsDirs(versionDir))
            {
                try
                {
                    Prefs.RemoveAutoload(Path.Combine(prefs, "pluginPrefs.mel"));
                    Prefs.RemoveOptionVar(Path.Combine(prefs, "userPrefs.mel"));
                    string shelf = Path.Combine(prefs, "shelves", "shelf_MayaShield.mel");
                    if (File.Exists(shelf))
                    {
                        File.SetAttributes(shelf, FileAttributes.Normal);
                        File.Delete(shelf);
                    }
                }
                catch (Exception ex)
                {
                    report.Log(L.T("warning", prefs + ": " + ex.Message));
                }
            }
        }

        // ================================================================ Apps & features
        static string RegisteredFolder
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MayaShield"); }
        }

        static void Register()
        {
            string folder = RegisteredFolder;
            Directory.CreateDirectory(folder);
            string exe = Path.Combine(folder, "MayaShield-Setup.exe");
            if (!SamePath(Application.ExecutablePath, exe))
                File.Copy(Application.ExecutablePath, exe, true);
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                key.SetValue("DisplayName", "MayaShield");
                key.SetValue("DisplayVersion", BuildInfo.Version);
                key.SetValue("Publisher", "4rest");
                key.SetValue("DisplayIcon", exe + ",0");
                key.SetValue("InstallLocation", folder);
                key.SetValue("UninstallString", "\"" + exe + "\" --uninstall");
                key.SetValue("QuietUninstallString", "\"" + exe + "\" --uninstall --silent");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", 1024, RegistryValueKind.DWord);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
            }
        }

        static void Unregister()
        {
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            string folder = RegisteredFolder;
            if (!Directory.Exists(folder))
                return;
            string self = Path.GetDirectoryName(Application.ExecutablePath);
            if (SamePath(self, folder))
            {
                // The running exe can't delete itself: let a hidden cmd do it once we've exited.
                Process.Start(new ProcessStartInfo("cmd.exe", "/c ping 127.0.0.1 -n 4 > nul & rd /s /q \"" + folder + "\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                });
            }
            else
            {
                FileOps.DeleteTree(folder);
            }
        }

        static bool SamePath(string a, string b)
        {
            return string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }

        // ================================================================ elevation
        int RunElevatedCopy(string action, List<MayaInstall> targets)
        {
            report.Status(L.T("waitingAdmin"));
            string log = Path.Combine(Path.GetTempPath(), "mayashield_elevated_" + Guid.NewGuid().ToString("N") + ".log");
            var arguments = new StringBuilder();
            arguments.Append("--elevated ").Append(action).Append(" --lang ").Append(L.Code);
            arguments.Append(" --elevated-log ").Append(QuoteArgument(log));
            foreach (MayaInstall maya in targets)
                arguments.Append(" --target ").Append(QuoteArgument(maya.Version + "=" + maya.Path));
            var start = new ProcessStartInfo(Application.ExecutablePath, arguments.ToString())
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            int code;
            try
            {
                using (Process process = Process.Start(start))
                {
                    process.WaitForExit();
                    code = process.ExitCode;
                }
            }
            catch (Win32Exception ex)
            {
                if (ex.NativeErrorCode == 1223)   // ERROR_CANCELLED: the UAC prompt was declined
                {
                    report.Log(L.T("uacDenied"));
                    return Exit.UacDenied;
                }
                throw;
            }
            try
            {
                if (File.Exists(log))
                {
                    foreach (string line in File.ReadAllLines(log, Encoding.UTF8))
                        report.Log(line);
                    File.Delete(log);
                }
            }
            catch (Exception) { }
            return code == Exit.Ok ? Exit.Ok : Exit.Error;
        }

        /// <summary>The elevated copy: touches only the Maya program folders it was given.</summary>
        public static int RunElevated(Options o)
        {
            using (var report = new Reporter(o.ElevatedLog))
            {
                try
                {
                    var targets = new List<MayaInstall>();
                    foreach (var pair in o.Targets)
                    {
                        // Never write into a folder that isn't a Maya, whatever the command line says.
                        if (!Maya.IsMaya(pair.Value))
                        {
                            report.Log(L.T("notMaya", pair.Value));
                            return Exit.Error;
                        }
                        targets.Add(new MayaInstall { Version = pair.Key, Path = pair.Value, Supported = true });
                    }
                    if (targets.Count == 0)
                        return Exit.Error;
                    foreach (MayaInstall maya in targets)
                    {
                        if (o.ElevatedAction == "install")
                            DeployMachine(maya, report);
                        else if (o.ElevatedAction == "uninstall")
                            RemoveMachine(maya, report);
                        else
                            return Exit.Error;
                    }
                    return Exit.Ok;
                }
                catch (Exception ex)
                {
                    report.Log(L.T("failed", ex.Message));
                    return Exit.Error;
                }
            }
        }

        /// <summary>Quotes one argument the way CommandLineToArgvW reads it back.</summary>
        static string QuoteArgument(string value)
        {
            var text = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\')
                {
                    slashes++;
                    continue;
                }
                if (c == '"')
                    text.Append('\\', slashes * 2 + 1).Append('"');
                else
                    text.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return text.Append('\\', slashes * 2).Append('"').ToString();
        }
    }
}
