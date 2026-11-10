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
        public string Installed; // MayaShield version already in this Maya's modules folder, or null

        public string OldCopyPath { get { return System.IO.Path.Combine(Path, "bin", "plug-ins", "mayashield.py"); } }
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

        /// <summary>A Maya with a window, or a batch render: either may have our files open.</summary>
        public static bool IsRunning()
        {
            foreach (string name in new[] { "maya", "mayabatch" })
            {
                Process[] running = Process.GetProcessesByName(name);
                foreach (Process process in running)
                    process.Dispose();
                if (running.Length > 0)
                    return true;
            }
            return false;
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
