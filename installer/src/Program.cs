using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
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
        public string ElevatedLog;
        public readonly List<KeyValuePair<int, string>> Targets = new List<KeyValuePair<int, string>>();

        public bool Machine { get { return Scope == "machine"; } }

        public static Options Parse(string[] args, out string error)
        {
            var o = new Options();
            error = null;
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                string next = i + 1 < args.Length ? args[i + 1] : null;
                switch (arg.ToLowerInvariant())
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
                switch (arg.ToLowerInvariant())
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
                    case "--elevated-log": o.ElevatedLog = next; break;
                    case "--maya":
                    case "--target":
                        KeyValuePair<int, string> pair;
                        if (!TryPair(next, out pair)) { error = arg + " " + next; return null; }
                        (arg.ToLowerInvariant() == "--maya" ? o.Maya : o.Targets).Add(pair);
                        break;
                    default:
                        error = arg;
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
        [STAThread]
        static int Main(string[] args)
        {
            string error;
            Options o = Options.Parse(args, out error);
            if (o == null)
            {
                L.Init(null);
                bool silent = Array.Exists(args, a => a.ToLowerInvariant() == "--silent" || a.StartsWith("--elevated"));
                if (!silent)
                    MessageBox.Show(L.T("badArgs", error), "MayaShield", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return Exit.Error;
            }
            L.Init(o.Lang);

            if (o.ElevatedAction != null)
                return Engine.RunElevated(o);

            if (o.List)
            {
                string json = Maya.ToJson(Maya.Detect(o));
                if (o.LogPath != null)
                    File.WriteAllText(o.LogPath, json, new UTF8Encoding(false));
                return Exit.Ok;
            }

            if (o.Silent)
            {
                using (var report = new Reporter(o.LogPath))
                    return RunSilent(o, report);
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var report = new Reporter(o.LogPath))
            {
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
                List<MayaInstall> all = Maya.Detect(o);
                foreach (MayaInstall maya in all)
                    report.Log(L.T("found", maya.Version, maya.Path));
                if (!o.Force && Maya.IsRunning())
                {
                    report.Log(L.T("mayaRunningLog"));
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
}
