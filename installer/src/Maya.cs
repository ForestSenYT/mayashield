using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace MayaShieldSetup
{
    class MayaInstall
    {
        public int Version;
        public string Path;
        public bool Supported;
        public bool OldCopy;     // a v3 mayashield.py in bin/plug-ins, which outranks modules
        public string Installed; // MayaShield version already in this Maya's modules folder, or null

        public string OldCopyPath { get { return System.IO.Path.Combine(Path, "bin", "plug-ins", "mayashield.py"); } }
    }

    enum MayaProcessKind
    {
        Open,       // shows a window on this desktop: somebody is using it
        Starting,   // no window yet, but it has only just started
        Batch,      // mayabatch: never has a window, and may be in the middle of a render
        Elsewhere,  // another Windows session, whose windows cannot be seen from here
        Leftover,   // lost its window and never exited
    }

    class MayaProcess
    {
        public int Id;
        public string Name;         // "maya" or "mayabatch"
        public string Title = "";   // of the window it shows, when it shows one
        public DateTime? Started;
        public MayaProcessKind Kind;

        /// <summary>Only a process with nothing on screen, and nothing on the way, is passed over.</summary>
        public bool Blocks { get { return Kind != MayaProcessKind.Leftover; } }

        public string Describe()
        {
            switch (Kind)
            {
                case MayaProcessKind.Open: return L.T("procOpen", Title.Length > 0 ? Title : Name + ".exe", Id);
                case MayaProcessKind.Starting: return L.T("procStarting", Id);
                case MayaProcessKind.Batch: return L.T("procBatch", Name + ".exe", Id);
                case MayaProcessKind.Elsewhere: return L.T("procElsewhere", Id);
                default: return L.T("procLeftover", Id, Started == null ? "?" : Started.Value.ToString("yyyy-MM-dd HH:mm"));
            }
        }
    }

    /// <summary>What is on screen, asked of Windows directly.</summary>
    static class Desktop
    {
        delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

        [DllImport("user32.dll")]
        static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool IsWindowVisible(IntPtr window);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);

        /// <summary>
        /// Process id to the title of a window that process shows, for every process showing
        /// one.  A minimised window counts as shown; a window that exists but was never put on
        /// screen does not.  Null when the desktop cannot be looked at, as for a service.
        /// </summary>
        public static Dictionary<int, string> VisibleWindows()
        {
            var found = new Dictionary<int, string>();
            EnumWindowsProc each = delegate(IntPtr window, IntPtr unused)
            {
                if (!IsWindowVisible(window))
                    return true;
                uint id;
                GetWindowThreadProcessId(window, out id);
                var text = new StringBuilder(256);
                GetWindowText(window, text, text.Capacity);
                string title = text.ToString();
                string known;
                // Maya shows several windows; the one with its name in the title is the one to quote.
                if (!found.TryGetValue((int)id, out known) || known.Length == 0
                    || (title.IndexOf("maya", StringComparison.OrdinalIgnoreCase) >= 0
                        && known.IndexOf("maya", StringComparison.OrdinalIgnoreCase) < 0))
                    found[(int)id] = title;
                return true;
            };
            bool listed;
            try
            {
                listed = EnumWindows(each, IntPtr.Zero);
            }
            catch (Exception)
            {
                listed = false;
            }
            GC.KeepAlive(each);
            return listed && found.Count > 0 ? found : null;
        }
    }

    static class Maya
    {
        public const int FirstSupported = 2022;
        static readonly Regex ModLine = new Regex(@"^\+\s+mayashield\s+(\S+)", RegexOptions.IgnoreCase | RegexOptions.Multiline);

        /// <summary>Installed Mayas, oldest first.  Registry entries left behind by an uninstall are dropped.</summary>
        public static List<MayaInstall> Detect(Options o)
        {
            return Candidates(o).Where(pair => IsMaya(pair.Value)).Select(pair => Describe(pair.Key, pair.Value)).ToList();
        }

        /// <summary>
        /// Where MayaShield may have been installed: detected Mayas, plus folders whose Maya has
        /// since been removed but which still hold our module.  For the uninstaller.
        /// </summary>
        public static List<MayaInstall> WithModule(Options o)
        {
            return Candidates(o).Select(pair => Describe(pair.Key, pair.Value))
                .Where(maya => maya.Installed != null || Directory.Exists(System.IO.Path.Combine(maya.Path, "modules", "MayaShield")))
                .ToList();
        }

        static MayaInstall Describe(int version, string path)
        {
            return new MayaInstall
            {
                Version = version,
                Path = path,
                Supported = version >= FirstSupported,
                OldCopy = File.Exists(System.IO.Path.Combine(path, "bin", "plug-ins", "mayashield.py")),
                Installed = InstalledVersion(System.IO.Path.Combine(path, "modules")),
            };
        }

        /// <summary>Version named in a modules folder's mayashield.mod, or null.</summary>
        public static string InstalledVersion(string modules)
        {
            try
            {
                string mod = System.IO.Path.Combine(modules, "mayashield.mod");
                if (!File.Exists(mod))
                    return null;
                Match match = ModLine.Match(File.ReadAllText(mod, Encoding.ASCII));
                return match.Success ? match.Groups[1].Value : "?";
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Every folder that is, or was, a Maya: version -> path.  --maya replaces detection.</summary>
        static SortedDictionary<int, string> Candidates(Options o)
        {
            var found = new SortedDictionary<int, string>();
            if (o.Maya.Count > 0)
            {
                foreach (var pair in o.Maya)
                    if (IsFolder(pair.Value))
                        found[pair.Key] = pair.Value;
                return found;
            }
            FromRegistry(found);
            try
            {
                FromDisk(found);
            }
            catch (Exception)
            {
                // An unreadable Program Files must not throw away what the registry gave.
            }
            return found;
        }

        static bool IsFolder(string folder)
        {
            try
            {
                return !string.IsNullOrEmpty(folder) && Directory.Exists(folder);
            }
            catch (ArgumentException)
            {
                return false;
            }
        }

        public static bool IsMaya(string folder)
        {
            try
            {
                return !string.IsNullOrEmpty(folder) && File.Exists(System.IO.Path.Combine(folder, "bin", "maya.exe"));
            }
            catch (ArgumentException)
            {
                return false;
            }
        }

        /// <summary>True for a folder this program's own detection reports as a Maya.  The elevated copy trusts nothing else.</summary>
        public static bool IsDetected(string folder)
        {
            var found = new SortedDictionary<int, string>();
            FromRegistry(found);
            try { FromDisk(found); } catch (Exception) { }
            string wanted = Normalize(folder);
            return found.Values.Any(path => Normalize(path) == wanted && IsMaya(path));
        }

        static string Normalize(string path)
        {
            try
            {
                return System.IO.Path.GetFullPath(path).TrimEnd('\\', '/').ToLowerInvariant();
            }
            catch (Exception)
            {
                return "";
            }
        }

        /// <summary>
        /// Every maya.exe and mayabatch.exe on this computer, and what each one is.
        ///
        /// A process by that name does not by itself mean Maya is open.  A Maya that hangs while
        /// closing keeps its process for days with nothing on screen, and asking someone to close
        /// what they cannot see leaves them stuck.  So a Maya counts as open when it shows a
        /// window; without one it is passed over, unless it started so recently that its window
        /// is still on the way.  A batch job never has a window and always counts.
        /// </summary>
        public static List<MayaProcess> Running(int startupGraceSeconds)
        {
            var result = new List<MayaProcess>();
            Dictionary<int, string> windows = Desktop.VisibleWindows();
            int session = -1;
            try
            {
                using (Process self = Process.GetCurrentProcess())
                    session = self.SessionId;
            }
            catch (Exception) { }

            foreach (string name in new[] { "maya", "mayabatch" })
            {
                Process[] processes;
                try
                {
                    processes = Process.GetProcessesByName(name);
                }
                catch (Exception)
                {
                    continue;
                }
                foreach (Process process in processes)
                {
                    using (process)
                    {
                        // Each of these can be refused for a process of another account: what
                        // cannot be learnt is left at the value that holds Setup back.
                        bool gone = false;
                        try { gone = process.HasExited; } catch (Exception) { }
                        if (gone)
                            continue;
                        var found = new MayaProcess { Name = name };
                        try { found.Id = process.Id; } catch (Exception) { }
                        try { found.Started = process.StartTime; } catch (Exception) { }
                        int itsSession = -2;
                        try { itsSession = process.SessionId; } catch (Exception) { }

                        string title;
                        if (name == "mayabatch")
                            found.Kind = MayaProcessKind.Batch;
                        else if (itsSession != session)
                            found.Kind = MayaProcessKind.Elsewhere;
                        else if (windows == null)
                            found.Kind = MayaProcessKind.Open;      // cannot look, so assume it is in use
                        else if (windows.TryGetValue(found.Id, out title))
                        {
                            found.Kind = MayaProcessKind.Open;
                            found.Title = title;
                        }
                        else if (found.Started != null && (DateTime.Now - found.Started.Value).TotalSeconds < startupGraceSeconds)
                            found.Kind = MayaProcessKind.Starting;
                        else
                            found.Kind = MayaProcessKind.Leftover;
                        result.Add(found);
                    }
                }
            }
            return result;
        }

        /// <summary>The processes Setup has to wait for.  Everything found goes into the log, passed over or not.</summary>
        public static List<MayaProcess> Blocking(Options o, Reporter report)
        {
            List<MayaProcess> running = Running(o.StartupGrace);
            foreach (MayaProcess process in running)
                report.Log(process.Blocks ? L.T("mayaFound", process.Describe()) : process.Describe());
            return running.Where(process => process.Blocks).ToList();
        }

        /// <summary>First come, first kept, except that a real Maya replaces a leftover folder.</summary>
        static void Add(SortedDictionary<int, string> found, int version, string path)
        {
            if (!IsFolder(path))
                return;
            string known;
            if (!found.TryGetValue(version, out known) || (!IsMaya(known) && IsMaya(path)))
                found[version] = path;
        }

        static void FromRegistry(SortedDictionary<int, string> found)
        {
            foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                try
                {
                    using (RegistryKey root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view))
                    using (RegistryKey maya = root.OpenSubKey(@"SOFTWARE\Autodesk\Maya"))
                    {
                        if (maya == null)
                            continue;
                        foreach (string name in maya.GetSubKeyNames())
                        {
                            Match match = Regex.Match(name, @"^(\d{4})");
                            if (!match.Success)
                                continue;
                            int version = int.Parse(match.Groups[1].Value);
                            using (RegistryKey setup = maya.OpenSubKey(name + @"\Setup\InstallPath"))
                            {
                                string path = setup == null ? null : setup.GetValue("MAYA_INSTALL_LOCATION") as string;
                                if (path == null)
                                    continue;
                                Add(found, version, path.Trim().TrimEnd('\\', '/'));
                            }
                        }
                    }
                }
                catch (Exception)
                {
                    // An unreadable registry falls back to the disk search.
                }
            }
        }

        static void FromDisk(SortedDictionary<int, string> found)
        {
            var roots = new List<string>();
            foreach (string variable in new[] { "ProgramW6432", "ProgramFiles" })
            {
                string value = Environment.GetEnvironmentVariable(variable);
                if (!string.IsNullOrEmpty(value) && !roots.Contains(value, StringComparer.OrdinalIgnoreCase))
                    roots.Add(value);
            }
            foreach (string root in roots)
            {
                string autodesk = System.IO.Path.Combine(root, "Autodesk");
                if (!Directory.Exists(autodesk))
                    continue;
                foreach (string folder in Directory.GetDirectories(autodesk, "Maya*"))
                {
                    Match match = Regex.Match(System.IO.Path.GetFileName(folder), @"^Maya(\d{4})$", RegexOptions.IgnoreCase);
                    if (!match.Success)
                        continue;
                    Add(found, int.Parse(match.Groups[1].Value), folder);
                }
            }
        }

        public static string ToJson(List<MayaInstall> list)
        {
            var text = new StringBuilder("[");
            for (int i = 0; i < list.Count; i++)
            {
                MayaInstall maya = list[i];
                if (i > 0)
                    text.Append(", ");
                text.AppendFormat("{{\"version\": {0}, \"path\": {1}, \"supported\": {2}, \"oldCopy\": {3}, \"installed\": {4}}}",
                                  maya.Version, Quote(maya.Path), maya.Supported ? "true" : "false",
                                  maya.OldCopy ? "true" : "false", maya.Installed == null ? "null" : Quote(maya.Installed));
            }
            return text.Append("]").ToString();
        }

        static string Quote(string value)
        {
            var text = new StringBuilder("\"");
            foreach (char c in value)
            {
                if (c == '"' || c == '\\')
                    text.Append('\\').Append(c);
                else if (c < 0x20)
                    text.AppendFormat("\\u{0:x4}", (int)c);
                else
                    text.Append(c);
            }
            return text.Append('"').ToString();
        }
    }
}
