using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace MayaShieldSetup
{
    static class Exit
    {
        public const int Ok = 0;
        public const int Error = 1;
        public const int NoMaya = 2;
        public const int MayaRunning = 3;
        public const int UacDenied = 4;
        public const int OldNeedsAdmin = 5;
        public const int SelfTestFailed = 6;
        public const int Busy = 7;
    }

    class Options
    {
        public bool Silent, Uninstall, List, Force, NoRegister, NoVerify;
        public string Scope = "machine";
        public List<int> Versions;                  // null: every supported version
        public string LogPath;
        public string AppDir;
        public string Lang;
        public readonly List<KeyValuePair<int, string>> Maya = new List<KeyValuePair<int, string>>();   // --maya overrides detection
        public string ElevatedAction;               // internal: "install" or "uninstall", run as administrator
        public string ElevatedPipe;                 // internal: where the elevated copy sends its log
        public readonly List<KeyValuePair<int, string>> Targets = new List<KeyValuePair<int, string>>();
        public string TestRoot;                     // tests only: see Engine.TestRoot
        public int StartupGrace = 120;              // seconds a Maya with no window yet is taken to be starting; tests shorten it
        public string RelayFile;                    // internal: this is the temporary copy doing an uninstall
        public int WaitPid;                         // internal: the registered copy that started it

        public bool Machine { get { return Scope == "machine"; } }

        /// <summary>Asked for no windows?  Decided before parsing, so a bad option still shows none.</summary>
        public static bool LooksSilent(string[] args)
        {
            foreach (string arg in args)
            {
                string lowered = arg.ToLowerInvariant();
                if (lowered == "--silent" || lowered == "/s" || lowered == "/silent" || lowered == "--list" || lowered.StartsWith("--elevated"))
                    return true;
            }
            return false;
        }

        public static Options Parse(string[] args, out string error)
        {
            var o = new Options();
            error = null;
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                string flag = arg.ToLowerInvariant();
                string next = i + 1 < args.Length ? args[i + 1] : null;
                switch (flag)
                {
                    case "--silent": case "/s": case "/silent": o.Silent = true; continue;
                    case "--uninstall": o.Uninstall = true; continue;
                    case "--list": o.List = true; continue;
                    case "--force": o.Force = true; continue;
                    case "--no-register": o.NoRegister = true; continue;
                    case "--no-verify": o.NoVerify = true; continue;
                }
                if (next == null) { error = arg; return null; }
                i++;
                try
                {
                    switch (flag)
                    {
                        case "--scope":
                            o.Scope = next.ToLowerInvariant();
                            if (o.Scope != "machine" && o.Scope != "user") { error = arg + " " + next; return null; }
                            break;
                        case "--versions":
                            o.Versions = new List<int>();
                            foreach (string part in next.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
                            {
                                int version;
                                if (!int.TryParse(part, out version)) { error = arg + " " + next; return null; }
                                o.Versions.Add(version);
                            }
                            break;
                        case "--log": o.LogPath = Path.GetFullPath(next); break;
                        case "--app-dir": o.AppDir = Path.GetFullPath(next); break;
                        case "--lang": o.Lang = next; break;
                        case "--elevated": o.ElevatedAction = next.ToLowerInvariant(); break;
                        case "--elevated-pipe": o.ElevatedPipe = next; break;
                        case "--relay": o.RelayFile = Path.GetFullPath(next); break;
                        case "--test-root": o.TestRoot = Path.GetFullPath(next); break;
                        case "--wait-pid":
                            if (!int.TryParse(next, out o.WaitPid)) { error = arg + " " + next; return null; }
                            break;
                        case "--startup-grace":
                            if (!int.TryParse(next, out o.StartupGrace) || o.StartupGrace < 0) { error = arg + " " + next; return null; }
                            break;
                        case "--maya":
                        case "--target":
                            KeyValuePair<int, string> pair;
                            if (!TryPair(next, out pair)) { error = arg + " " + next; return null; }
                            (flag == "--maya" ? o.Maya : o.Targets).Add(pair);
                            break;
                        default:
                            error = arg;
                            return null;
                    }
                }
                catch (Exception)   // a path Windows cannot make sense of
                {
                    error = arg + " " + next;
                    return null;
                }
            }
            return o;
        }

        static bool TryPair(string text, out KeyValuePair<int, string> pair)
        {
            pair = new KeyValuePair<int, string>();
            int split = text.IndexOf('=');
            int version;
            if (split < 1 || !int.TryParse(text.Substring(0, split), out version))
                return false;
            pair = new KeyValuePair<int, string>(version, text.Substring(split + 1).Trim().TrimEnd('\\', '/'));
            return pair.Value.Length > 0;
        }
    }

    static class Program
    {
        // One Setup at a time: two copying into the same Maya leave it with half of each.
        const string MutexName = @"Global\MayaShieldSetup-6c0d1f6e5b0a4a57";

        [STAThread]
        static int Main(string[] args)
        {
            string error;
            Options o = Options.Parse(args, out error);
            if (o == null)
            {
                L.Init(null);
                if (!Options.LooksSilent(args))
                    MessageBox.Show(L.T("badArgs", error), "MayaShield", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return Exit.Error;
            }
            L.Init(o.Lang);
            Engine.TestRoot = o.TestRoot;

            if (o.ElevatedAction != null)
                return Engine.RunElevated(o);

            if (o.List)
            {
                string json = Maya.ToJson(Maya.Detect(o));
                if (o.LogPath != null)
                    File.WriteAllText(o.LogPath, json, new UTF8Encoding(false));
                return Exit.Ok;
            }

            // Uninstalling from Apps & features starts the copy kept for that purpose.  It cannot
            // delete its own folder while it runs, so the work is done by a temporary copy.
            if (o.Uninstall && o.RelayFile == null && !o.NoRegister && Engine.RunsFromRegisteredFolder)
            {
                int? relayed = Relay.Start(args);
                if (relayed != null)
                    return relayed.Value;
            }

            Mutex single = null;
            try
            {
                bool mine;
                try
                {
                    single = new Mutex(true, MutexName, out mine);
                }
                catch (Exception)   // owned by another account on this computer
                {
                    mine = false;
                }
                if (!mine)
                {
                    if (!o.Silent)
                        MessageBox.Show(L.T("anotherRunning"), "MayaShield", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return Relay.Finish(o, Exit.Busy);
                }
                return Relay.Finish(o, Run(o));
            }
            finally
            {
                if (single != null)
                {
                    try { single.ReleaseMutex(); } catch (Exception) { }
                    single.Dispose();
                }
            }
        }

        static int Run(Options o)
        {
            using (var report = new Reporter(o.LogPath))
            {
                if (o.Silent)
                    return RunSilent(o, report);
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                var form = new SetupForm(o, report);
                Application.Run(form);
                return form.ExitCode;
            }
        }

        static int RunSilent(Options o, Reporter report)
        {
            try
            {
                var engine = new Engine(o, report);
                List<MayaInstall> all = o.Uninstall ? Maya.WithModule(o) : Maya.Detect(o);
                foreach (MayaInstall maya in all)
                    report.Log(L.T("found", maya.Version, maya.Path));
                if (!o.Force && Maya.Blocking(o, report).Count > 0)
                {
                    report.Log(L.T("mayaRunningLog"));
                    report.Log(L.T("mayaRunningHint"));
                    return Exit.MayaRunning;
                }
                if (o.Uninstall)
                    return engine.Uninstall(all);
                List<MayaInstall> targets = engine.SelectTargets(all);
                if (targets.Count == 0)
                {
                    report.Log(L.T("noMaya"));
                    return Exit.NoMaya;
                }
                return engine.Install(targets);
            }
            catch (Exception ex)
            {
                report.Log(L.T("failed", ex.Message));
                return Exit.Error;
            }
        }
    }

    /// <summary>
    /// Uninstalling through a temporary copy.  The registered copy starts it, waits for the exit
    /// code it reports in a small file, and leaves; the temporary copy then removes the folder
    /// the registered copy lived in.  No command prompt is involved.
    /// </summary>
    static class Relay
    {
        /// <summary>Set once the Apps & features entry is gone: only then may the registered folder go too.</summary>
        public static bool Unregistered;

        /// <summary>Runs the uninstall from a temporary copy.  Null if that could not be arranged.</summary>
        public static int? Start(string[] args)
        {
            try
            {
                string id = Guid.NewGuid().ToString("N");
                string copy = Path.Combine(Path.GetTempPath(), "MayaShield-Uninstall-" + id + ".exe");
                string answer = Path.Combine(Path.GetTempPath(), "MayaShield-Uninstall-" + id + ".code");
                File.Copy(Engine.ExePath, copy, true);

                var line = new StringBuilder();
                foreach (string arg in args)
                    line.Append(Engine.QuoteArgument(arg)).Append(' ');
                int self;
                using (Process me = Process.GetCurrentProcess())
                    self = me.Id;
                line.Append("--relay ").Append(Engine.QuoteArgument(answer)).Append(" --wait-pid ").Append(self);

                using (Process child = Process.Start(new ProcessStartInfo(copy, line.ToString()) { UseShellExecute = false }))
                {
                    while (!File.Exists(answer) && !child.WaitForExit(100)) { }
                    int? code = ReadCode(answer);
                    if (code == null && child.HasExited)
                        code = child.ExitCode;
                    return code ?? Exit.Error;
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        static int? ReadCode(string path)
        {
            for (int attempt = 0; attempt < 20; attempt++)
            {
                try
                {
                    if (File.Exists(path))
                    {
                        int code = int.Parse(File.ReadAllText(path).Trim());
                        File.Delete(path);
                        return code;
                    }
                }
                catch (Exception) { }
                Thread.Sleep(50);
            }
            return null;
        }

        /// <summary>In the temporary copy: report the result, then tidy up after the copy that started us.</summary>
        public static int Finish(Options o, int code)
        {
            if (o.RelayFile == null)
                return code;
            try
            {
                string partial = o.RelayFile + ".tmp";
                File.WriteAllText(partial, code.ToString());
                File.Move(partial, o.RelayFile);
            }
            catch (Exception) { }
            if (Unregistered)
            {
                try
                {
                    using (Process parent = Process.GetProcessById(o.WaitPid))
                        parent.WaitForExit(15000);
                }
                catch (Exception) { }   // already gone
                FileOps.TryDeleteTree(Engine.RegisteredFolder);
            }
            return code;
        }
    }
}
