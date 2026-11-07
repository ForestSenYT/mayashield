using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
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

        public string OldCopyPath { get { return System.IO.Path.Combine(Path, "bin", "plug-ins", "mayashield.py"); } }
    }

    static class Maya
    {
        public const int FirstSupported = 2022;

        /// <summary>Installed Mayas, oldest first.  Registry entries left behind by an uninstall are dropped.</summary>
        public static List<MayaInstall> Detect(Options o)
        {
            var found = new SortedDictionary<int, string>();
            if (o.Maya.Count > 0)
            {
                foreach (var pair in o.Maya)
                    if (IsMaya(pair.Value))
                        found[pair.Key] = pair.Value;
            }
            else
            {
                FromRegistry(found);
                FromDisk(found);
            }
            return found.Select(pair => new MayaInstall
            {
                Version = pair.Key,
                Path = pair.Value,
                Supported = pair.Key >= FirstSupported,
                OldCopy = File.Exists(System.IO.Path.Combine(pair.Value, "bin", "plug-ins", "mayashield.py")),
            }).ToList();
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

        public static bool IsRunning()
        {
            foreach (Process process in Process.GetProcessesByName("maya"))
            {
                process.Dispose();
                return true;
            }
            return false;
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
                                path = path.Trim().TrimEnd('\\', '/');
                                if (!found.ContainsKey(version) && IsMaya(path))
                                    found[version] = path;
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
                    int version = int.Parse(match.Groups[1].Value);
                    if (!found.ContainsKey(version) && IsMaya(folder))
                        found[version] = folder;
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
                text.AppendFormat("{{\"version\": {0}, \"path\": {1}, \"supported\": {2}, \"oldCopy\": {3}}}",
                                  maya.Version, Quote(maya.Path), maya.Supported ? "true" : "false", maya.OldCopy ? "true" : "false");
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
