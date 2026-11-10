using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
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
        const string StagingName = ".MayaShield.new";
        const string RetiredName = ".MayaShield.old";
        const string RealUninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\MayaShield";
        const int SelfTestAttempts = 3;
        const int SelfTestTimeoutMs = 120000;
        static readonly Regex VersionFolder = new Regex(@"^(\d{4})(-x64)?$");
        static readonly Regex LanguageFolder = new Regex(@"^[a-z]{2}_[A-Z]{2}$");
        static readonly Regex PipeToken = new Regex(@"^[0-9a-f]{32}$");
        static string exePath;

        /// <summary>
        /// Set by --test-root, for the test-suite only: the Apps & features entry, the registered
        /// copy and the default log folder go under this folder (and a registry key of its own),
        /// so a test run never touches a real installation's.
        /// </summary>
        public static string TestRoot;

        static string UninstallKey
        {
            get
            {
                if (TestRoot == null)
                    return RealUninstallKey;
                return @"Software\MayaShield-SetupTest\" + Path.GetFileName(TestRoot.TrimEnd('\\', '/'));
            }
        }

        readonly Options o;
        readonly Reporter report;

        public Engine(Options options, Reporter reporter)
        {
            o = options;
            report = reporter;
        }

        /// <summary>This program's own file.  Application.ExecutablePath URL-decodes it, so "%20" in a name breaks.</summary>
        public static string ExePath
        {
            get
            {
                if (exePath == null)
                {
                    using (Process self = Process.GetCurrentProcess())
                        exePath = self.MainModule.FileName;
                }
                return exePath;
            }
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
                string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                return string.IsNullOrEmpty(documents) ? "" : Path.Combine(documents, "maya");
            }
        }

        /// <summary>The user's Maya folder must be a full path: a relative one would land wherever Setup was started.</summary>
        bool AppDirUsable()
        {
            string dir = AppDir;
            if (!string.IsNullOrEmpty(dir) && Path.IsPathRooted(dir))
                return true;
            report.Log(L.T("noAppDir"));
            return false;
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

        string ModulesFolder(MayaInstall maya)
        {
            return o.Machine ? Path.Combine(maya.Path, "modules") : Path.Combine(AppDir, maya.Version.ToString(), "modules");
        }

        // ================================================================ install
        public int Install(List<MayaInstall> targets)
        {
            report.Log(L.T("logStart", BuildInfo.Version, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")));
            report.Begin(targets.Count * (o.NoVerify ? 2 : 3) + (o.NoRegister ? 0 : 1));
            if (!AppDirUsable())
                return Exit.Error;

            if (!o.Machine)
            {
                List<MayaInstall> blocked = targets.Where(m => m.OldCopy).ToList();
                foreach (MayaInstall maya in blocked)
                    report.Log(L.T("oldNeedsAdmin", maya.Version));
                if (blocked.Count > 0)
                    return Exit.OldNeedsAdmin;
            }

            // 1. Module files.  One Maya failing does not stop the others.
            bool failed = false;
            if (o.Machine && NeedsAdmin(targets))
            {
                int code = RunElevatedCopy("install", targets);
                if (code == Exit.UacDenied)
                    return code;
                failed = code != Exit.Ok;
                foreach (MayaInstall maya in targets)
                    report.Advance();
            }
            else
            {
                foreach (MayaInstall maya in targets)
                {
                    try
                    {
                        if (o.Machine)
                            DeployMachine(maya, report);
                        else
                            DeployModule(maya.Version, ModulesFolder(maya), report);
                    }
                    catch (Exception ex)
                    {
                        failed = true;
                        report.Log(L.T("targetFailed", maya.Version, ex.Message));
                    }
                    report.Advance();
                }
            }
            // What is really on disk decides what happens next, whoever did the copying.
            List<MayaInstall> installed = targets.Where(m => Maya.InstalledVersion(ModulesFolder(m)) == BuildInfo.Version).ToList();
            failed = failed || installed.Count < targets.Count;

            // 2. This user's preferences
            foreach (MayaInstall maya in targets)
            {
                if (installed.Contains(maya))
                {
                    try { ConfigureUser(maya); }
                    catch (Exception ex) { report.Warn(L.T("prefsFailed", maya.Version, ex.Message)); }
                }
                report.Advance();
            }

            // 3. Apps & features, before the check below: whatever that says, there is now something to uninstall.
            if (!o.NoRegister)
            {
                if (installed.Count > 0)
                {
                    report.Status(L.T("registering"));
                    try { Register(); }
                    catch (Exception ex) { report.Warn(ex.Message); }
                }
                report.Advance();
            }

            // 4. Self-test with each Maya's own Python
            bool verified = true;
            if (!o.NoVerify)
            {
                foreach (MayaInstall maya in targets)
                {
                    if (installed.Contains(maya) && !SelfTest(maya))
                        verified = false;
                    report.Advance();
                }
            }
            if (failed)
                return Exit.Error;
            return verified ? Exit.Ok : Exit.SelfTestFailed;
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

        /// <summary>
        /// Writes modules/MayaShield and modules/mayashield.mod.  The new folder is built beside the
        /// old one and the two change places by renaming, so a file that is busy leaves the old
        /// install whole instead of half deleted.
        /// </summary>
        static void DeployModule(int version, string modules, Reporter report)
        {
            string target = Path.Combine(modules, ModuleName);
            string staging = Path.Combine(modules, StagingName);
            string retired = Path.Combine(modules, RetiredName);
            report.Status(L.T("copying", version, target));
            Directory.CreateDirectory(modules);
            Recover(modules);

            if (FileOps.IsLink(target))
            {
                // Someone linked their own folder here: update it in place, never delete through the link.
                Payload.ExtractTo(target);
            }
            else
            {
                FileOps.DeleteTree(staging);
                Payload.ExtractTo(staging);
                bool replacing = Directory.Exists(target);
                if (replacing)
                {
                    try
                    {
                        FileOps.Retry(() => Directory.Move(target, retired));
                    }
                    catch (Exception)
                    {
                        FileOps.TryDeleteTree(staging);
                        throw new IOException(L.T("inUse", target));
                    }
                }
                try
                {
                    FileOps.Retry(() => Directory.Move(staging, target));
                }
                catch (Exception)
                {
                    if (replacing)
                    {
                        try { FileOps.Retry(() => Directory.Move(retired, target)); }
                        catch (Exception) { }
                    }
                    FileOps.TryDeleteTree(staging);
                    throw;
                }
                if (replacing && !FileOps.TryDeleteTree(retired))
                    report.Log(L.T("leftover", retired));
            }
            string mod = "+ mayashield " + BuildInfo.Version + " ./" + ModuleName + "\r\n";
            FileOps.WriteAtomic(Path.Combine(modules, ModFile), Encoding.ASCII.GetBytes(mod));
        }

        /// <summary>Puts right what an interrupted earlier run left: the old folder moved aside and nothing in its place.</summary>
        static void Recover(string modules)
        {
            string target = Path.Combine(modules, ModuleName);
            string retired = Path.Combine(modules, RetiredName);
            if (!Directory.Exists(retired))
                return;
            if (Directory.Exists(target))
                FileOps.TryDeleteTree(retired);
            else
                FileOps.Retry(() => Directory.Move(retired, target));
        }

        void ConfigureUser(MayaInstall maya)
        {
            report.Status(L.T("configuring", maya.Version));
            List<string> versionDirs = VersionDirs(maya.Version);

            // Old single-file copies in the user's plug-in folders load before any module.
            var oldCopies = new List<KeyValuePair<string, string>>();
            foreach (string versionDir in versionDirs)
                oldCopies.Add(new KeyValuePair<string, string>(Path.Combine(versionDir, "plug-ins"), maya.Version.ToString()));
            oldCopies.Add(new KeyValuePair<string, string>(Path.Combine(AppDir, "plug-ins"), "shared"));
            foreach (var place in oldCopies)
            {
                foreach (string name in new[] { "mayashield.py", "mayashield.pyc" })
                {
                    string old = Path.Combine(place.Key, name);
                    if (File.Exists(old))
                    {
                        string moved = FileOps.MoveAside(old, Path.Combine(AppDir, "mayashield", "legacy-backup", place.Value));
                        report.Log(L.T("movedOld", maya.Version, moved));
                    }
                }
            }

            // An all-users install replaces this user's own copy, which would otherwise win.
            if (o.Machine)
            {
                foreach (string versionDir in versionDirs)
                    RemoveModule(maya.Version, Path.Combine(versionDir, "modules"), "removedUserCopy", report);
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
                        report.Warn(pluginPrefs + ": " + ex.Message);
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

        // ================================================================ self-test
        /// <summary>
        /// Runs the engine's own check on this Maya's Python.  Tried up to three times: an antivirus
        /// looking at the files just written can make one run fail for no reason of ours.
        /// </summary>
        bool SelfTest(MayaInstall maya)
        {
            string mayapy = Path.Combine(maya.Path, "bin", "mayapy.exe");
            if (!File.Exists(mayapy))
            {
                report.Log(L.T("selfTestSkipped", maya.Version));
                return true;
            }
            report.Status(L.T("selfTesting", maya.Version));
            string scripts = Path.Combine(ModulesFolder(maya), ModuleName, "scripts");
            for (int attempt = 1; attempt <= SelfTestAttempts; attempt++)
            {
                if (attempt > 1)
                {
                    report.Log(L.T("selfTestRetry", maya.Version, attempt, SelfTestAttempts));
                    Thread.Sleep(attempt == 2 ? 1000 : 3000);
                }
                if (RunSelfTest(mayapy, scripts))
                {
                    report.Log(L.T("selfTestOk", maya.Version));
                    return true;
                }
            }
            report.Log(L.T("selfTestFailed", maya.Version));
            return false;
        }

        /// <summary>
        /// One run.  The verdict is what the test itself reported, in a result file and on its
        /// output; the exit code is logged but does not decide, because mayapy is known to exit
        /// badly after doing its work.
        /// </summary>
        bool RunSelfTest(string mayapy, string scripts)
        {
            string resultFile = Path.Combine(Path.GetTempPath(), "mayashield_selftest_" + Guid.NewGuid().ToString("N") + ".json");
            var start = new ProcessStartInfo(mayapy)
            {
                // -I keeps the user's environment and site-packages out; -B leaves no .pyc in the install.
                // main() flushes its report itself, so os._exit can skip the interpreter's shutdown.
                Arguments = "-X utf8 -I -B -c \"import os, sys; sys.path.insert(0, os.environ['MAYASHIELD_SELFTEST_PATH']); "
                          + "from mayashield_core import selftest; os._exit(selftest.main())\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Path.GetTempPath(),
            };
            start.EnvironmentVariables["MAYASHIELD_SELFTEST_PATH"] = scripts;
            start.EnvironmentVariables["MAYASHIELD_SELFTEST_RESULT"] = resultFile;

            var output = new StringBuilder();
            var outputClosed = new ManualResetEvent(false);
            var errorClosed = new ManualResetEvent(false);
            var clock = Stopwatch.StartNew();
            string exit = "none";
            bool timedOut = false;
            try
            {
                using (Process process = new Process { StartInfo = start })
                {
                    process.OutputDataReceived += (sender, e) => Collect(output, outputClosed, e.Data);
                    process.ErrorDataReceived += (sender, e) => Collect(output, errorClosed, e.Data);
                    process.Start();
                    try { process.StandardInput.Close(); } catch (Exception) { }
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    if (!process.WaitForExit(SelfTestTimeoutMs))
                    {
                        timedOut = true;
                        try { process.Kill(); } catch (Exception) { }
                        process.WaitForExit(5000);
                    }
                    // The pipes close when their last holder exits.  Wait for that, but not for ever:
                    // a process the test never started could be holding them.
                    outputClosed.WaitOne(5000);
                    errorClosed.WaitOne(5000);
                    if (process.HasExited)
                        exit = string.Format("{0} (0x{0:X8})", process.ExitCode);
                }
            }
            catch (Exception ex)
            {
                lock (output) output.AppendLine(ex.Message);
            }
            clock.Stop();

            string text;
            lock (output) text = output.ToString().Trim();
            string verdict = ReadResult(resultFile) ?? text;
            bool ok = verdict.Contains("\"ok\": true");

            report.Log(L.T("selfTestRun", mayapy, exit, clock.ElapsedMilliseconds));
            if (timedOut)
                report.Log(L.T("selfTestTimeout", SelfTestTimeoutMs / 1000));
            if (text.Length > 0)
                report.Log(text);
            else if (verdict.Length > 0)
                report.Log(verdict);
            return ok;
        }

        static void Collect(StringBuilder output, ManualResetEvent closed, string line)
        {
            if (line == null)
            {
                try { closed.Set(); } catch (ObjectDisposedException) { }
                return;
            }
            lock (output) output.AppendLine(line);
        }

        static string ReadResult(string path)
        {
            try
            {
                if (!File.Exists(path))
                    return null;
                string text = File.ReadAllText(path, Encoding.UTF8).Trim();
                File.Delete(path);
                return text.Length > 0 ? text : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        // ================================================================ uninstall
        public int Uninstall(List<MayaInstall> all)
        {
            report.Log(L.T("logStart", BuildInfo.Version, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")));
            if (!AppDirUsable())
                return Exit.Error;
            List<MayaInstall> machine = all.Where(m => (o.Versions == null || o.Versions.Contains(m.Version))
                && (File.Exists(Path.Combine(m.Path, "modules", ModFile)) || Directory.Exists(Path.Combine(m.Path, "modules", ModuleName))))
                .ToList();
            List<string> userDirs = AllVersionDirs();
            bool unregister = !o.NoRegister && o.Versions == null;
            report.Begin(machine.Count + userDirs.Count + (unregister ? 1 : 0));

            bool failed = false;
            if (machine.Count == 0)
                report.Log(L.T("nothingInstalled"));
            else if (NeedsAdmin(machine))
            {
                int code = RunElevatedCopy("uninstall", machine);
                if (code == Exit.UacDenied)
                    return code;
                failed = code != Exit.Ok;
                foreach (MayaInstall maya in machine)
                    report.Advance();
            }
            else
            {
                foreach (MayaInstall maya in machine)
                {
                    try
                    {
                        RemoveMachine(maya, report);
                    }
                    catch (Exception ex)
                    {
                        failed = true;
                        report.Log(L.T("targetFailed", maya.Version, ex.Message));
                    }
                    report.Advance();
                }
            }

            foreach (string versionDir in userDirs)
            {
                try { CleanUser(versionDir); }
                catch (Exception ex) { report.Warn(versionDir + ": " + ex.Message); }
                report.Advance();
            }

            if (unregister)
            {
                report.Status(L.T("unregistering"));
                try { Unregister(); }
                catch (Exception ex) { report.Warn(ex.Message); }
                report.Advance();
            }
            return failed ? Exit.Error : Exit.Ok;
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

        /// <summary>Removes the .mod first, so Maya stops loading the module even if a busy file outlives it.</summary>
        static void RemoveModule(int version, string modules, string message, Reporter report)
        {
            string mod = Path.Combine(modules, ModFile);
            string folder = Path.Combine(modules, ModuleName);
            if (!File.Exists(mod) && !Directory.Exists(folder))
                return;
            report.Status(L.T(message, version, folder));
            FileOps.DeleteFile(mod);
            foreach (string name in new[] { ModuleName, StagingName, RetiredName })
            {
                string path = Path.Combine(modules, name);
                if (!FileOps.TryDeleteTree(path))
                    report.Warn(L.T("leftover", path));
            }
        }

        void CleanUser(string versionDir)
        {
            report.Status(L.T("cleaningPrefs", versionDir));
            int version = int.Parse(VersionFolder.Match(Path.GetFileName(versionDir)).Groups[1].Value);
            RemoveModule(version, Path.Combine(versionDir, "modules"), "removing", report);
            foreach (string prefs in PrefsDirs(versionDir))
            {
                try
                {
                    Prefs.RemoveAutoload(Path.Combine(prefs, "pluginPrefs.mel"));
                    Prefs.RemoveOptionVars(Path.Combine(prefs, "userPrefs.mel"));
                    FileOps.DeleteFile(Path.Combine(prefs, "shelves", "shelf_MayaShield.mel"));
                    foreach (string name in new[] { "pluginPrefs.mel", "userPrefs.mel" })
                        FileOps.DeleteFile(Path.Combine(prefs, name + ".mayashield_tmp"));
                }
                catch (Exception ex)
                {
                    report.Warn(prefs + ": " + ex.Message);
                }
            }
        }

        // ================================================================ Apps & features
        public static string RegisteredFolder
        {
            get
            {
                if (TestRoot != null)
                    return Path.Combine(TestRoot, "registered");
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MayaShield");
            }
        }

        /// <summary>True when this is the copy Apps & features starts, which cannot delete its own folder.</summary>
        public static bool RunsFromRegisteredFolder
        {
            get { return SamePath(Path.GetDirectoryName(ExePath), RegisteredFolder); }
        }

        static void Register()
        {
            string folder = RegisteredFolder;
            Directory.CreateDirectory(folder);
            string exe = Path.Combine(folder, "MayaShield-Setup.exe");
            if (!SamePath(ExePath, exe))
                FileOps.Retry(() => File.Copy(ExePath, exe, true));
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

        /// <summary>
        /// Removes the Apps & features entry.  The folder holding the registered copy goes too,
        /// unless that copy is still running: Program removes it then, once that copy has exited.
        /// </summary>
        void Unregister()
        {
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            Relay.Unregistered = true;
            if (o.RelayFile == null && !RunsFromRegisteredFolder)
                FileOps.TryDeleteTree(RegisteredFolder);
        }

        /// <summary>Is there an "only me" install for any of these Mayas?  The window offers Uninstall if so.</summary>
        public bool UserCopyExists(IEnumerable<MayaInstall> mayas)
        {
            try
            {
                string app = AppDir;
                if (string.IsNullOrEmpty(app) || !Path.IsPathRooted(app))
                    return false;
                return mayas.Any(m => VersionDirs(m.Version).Any(dir => File.Exists(Path.Combine(dir, "modules", ModFile))));
            }
            catch (Exception)
            {
                return false;
            }
        }

        static bool SamePath(string a, string b)
        {
            return string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }

        // ================================================================ elevation
        /// <summary>
        /// Runs the copying in an elevated copy of this program.  What it logs comes back through
        /// a pipe this side owns, so the elevated copy is never told where to write a file.
        /// </summary>
        int RunElevatedCopy(string action, List<MayaInstall> targets)
        {
            report.Status(L.T("waitingAdmin"));
            string token = Guid.NewGuid().ToString("N");
            var arguments = new StringBuilder();
            arguments.Append("--elevated ").Append(action).Append(" --lang ").Append(L.Code);
            arguments.Append(" --elevated-pipe ").Append(token);
            foreach (MayaInstall maya in targets)
                arguments.Append(" --target ").Append(QuoteArgument(maya.Version + "=" + maya.Path));
            var start = new ProcessStartInfo(ExePath, arguments.ToString())
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            };

            NamedPipeServerStream pipe = null;
            Thread reader = null;
            try
            {
                try
                {
                    pipe = new NamedPipeServerStream(PipeName(token), PipeDirection.In, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
                    NamedPipeServerStream server = pipe;
                    reader = new Thread(() => RelayLog(server)) { IsBackground = true };
                    reader.Start();
                }
                catch (Exception)
                {
                    pipe = null;   // no log from the helper, but the work can still be done
                }

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
                if (reader != null)
                    reader.Join(3000);
                return code == Exit.Ok ? Exit.Ok : Exit.Error;
            }
            finally
            {
                if (pipe != null)
                {
                    try { pipe.Dispose(); } catch (Exception) { }
                }
            }
        }

        static string PipeName(string token)
        {
            return "MayaShieldSetup-" + token;
        }

        void RelayLog(NamedPipeServerStream server)
        {
            try
            {
                server.WaitForConnection();
                using (var lines = new StreamReader(server, Encoding.UTF8))
                {
                    string line;
                    while ((line = lines.ReadLine()) != null)
                        report.Log(line);
                }
            }
            catch (Exception)
            {
                // The helper never connected (UAC declined), or went away: nothing more to relay.
            }
        }

        /// <summary>The elevated copy: touches only the Maya program folders it was given, and only real ones.</summary>
        public static int RunElevated(Options o)
        {
            using (var report = new Reporter(null, false))
            {
                Stream pipe = ConnectToParent(o.ElevatedPipe);
                StreamWriter writer = pipe == null ? null : new StreamWriter(pipe, new UTF8Encoding(false)) { AutoFlush = true };
                if (writer != null)
                    report.OnLog = line => { try { writer.WriteLine(line); } catch (Exception) { } };
                try
                {
                    // With administrator rights, a command line is not to be believed: only folders this
                    // program itself finds to be a Maya are written to.  Without them it can do no more
                    // than whoever started it, which is how the tests drive it.
                    bool elevated = IsAdmin();
                    var targets = new List<MayaInstall>();
                    foreach (var pair in o.Targets)
                    {
                        if (!Maya.IsMaya(pair.Value) || (elevated && !Maya.IsDetected(pair.Value)))
                        {
                            report.Log(L.T("notMaya", pair.Value));
                            return Exit.Error;
                        }
                        targets.Add(new MayaInstall { Version = pair.Key, Path = pair.Value, Supported = true });
                    }
                    if (targets.Count == 0 || (o.ElevatedAction != "install" && o.ElevatedAction != "uninstall"))
                        return Exit.Error;
                    bool failed = false;
                    foreach (MayaInstall maya in targets)
                    {
                        try
                        {
                            if (o.ElevatedAction == "install")
                                DeployMachine(maya, report);
                            else
                                RemoveMachine(maya, report);
                        }
                        catch (Exception ex)
                        {
                            failed = true;
                            report.Log(L.T("targetFailed", maya.Version, ex.Message));
                        }
                    }
                    return failed || report.Warnings > 0 ? Exit.Error : Exit.Ok;
                }
                catch (Exception ex)
                {
                    report.Log(L.T("failed", ex.Message));
                    return Exit.Error;
                }
                finally
                {
                    if (writer != null)
                    {
                        try { writer.Dispose(); } catch (Exception) { }
                    }
                }
            }
        }

        static Stream ConnectToParent(string token)
        {
            if (token == null || !PipeToken.IsMatch(token))
                return null;
            try
            {
                var client = new NamedPipeClientStream(".", PipeName(token), PipeDirection.Out);
                client.Connect(5000);
                return client;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Quotes one argument the way CommandLineToArgvW reads it back.</summary>
        public static string QuoteArgument(string value)
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
