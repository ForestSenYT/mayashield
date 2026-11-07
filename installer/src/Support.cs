using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;

namespace MayaShieldSetup
{
    /// <summary>Log file plus progress for the window.  Callbacks run on the worker thread.</summary>
    class Reporter : IDisposable
    {
        readonly StreamWriter file;
        readonly object gate = new object();
        int done, total;

        public Action<string> OnLog;
        public Action<string> OnStatus;
        public Action<int, int> OnProgress;

        public Reporter(string path)
        {
            if (path == null)
                return;
            try
            {
                string folder = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(folder))
                    Directory.CreateDirectory(folder);
                file = new StreamWriter(path, false, new UTF8Encoding(false)) { AutoFlush = true };
            }
            catch (Exception)
            {
                file = null;   // logging must never stop the install
            }
        }

        public void Log(string line)
        {
            lock (gate)
            {
                if (file != null)
                    file.WriteLine(line);
            }
            if (OnLog != null)
                OnLog(line);
        }

        public void Status(string line)
        {
            Log(line);
            if (OnStatus != null)
                OnStatus(line);
        }

        public void Begin(int steps)
        {
            done = 0;
            total = Math.Max(1, steps);
            if (OnProgress != null)
                OnProgress(done, total);
        }

        public void Advance()
        {
            done = Math.Min(total, done + 1);
            if (OnProgress != null)
                OnProgress(done, total);
        }

        public void Dispose()
        {
            lock (gate)
            {
                if (file != null)
                    file.Dispose();
            }
        }
    }

    /// <summary>The module files, packed by build.py into the MayaShield.payload resource.</summary>
    static class Payload
    {
        static List<KeyValuePair<string, byte[]>> files;

        public static List<KeyValuePair<string, byte[]>> Files
        {
            get
            {
                if (files == null)
                    files = Read();
                return files;
            }
        }

        static List<KeyValuePair<string, byte[]>> Read()
        {
            var result = new List<KeyValuePair<string, byte[]>>();
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("MayaShield.payload"))
            {
                if (resource == null)
                    throw new InvalidOperationException("The installer has no payload. Rebuild it with installer/build.py.");
                using (var unzip = new GZipStream(resource, CompressionMode.Decompress))
                using (var reader = new BinaryReader(unzip))
                {
                    if (Encoding.ASCII.GetString(reader.ReadBytes(4)) != "MSPK")
                        throw new InvalidDataException("The installer payload is damaged.");
                    int count = reader.ReadInt32();
                    for (int i = 0; i < count; i++)
                    {
                        string name = Encoding.UTF8.GetString(reader.ReadBytes(reader.ReadInt32()));
                        byte[] data = reader.ReadBytes(reader.ReadInt32());
                        if (name.StartsWith("/") || name.Contains("..") || name.Contains(":"))
                            throw new InvalidDataException("Bad payload path: " + name);
                        result.Add(new KeyValuePair<string, byte[]>(name, data));
                    }
                }
            }
            return result;
        }

        /// <summary>Writes the MayaShield folder's contents into folder, overwriting what's there.</summary>
        public static void ExtractTo(string folder)
        {
            foreach (var item in Files)
            {
                string path = Path.Combine(folder, item.Key.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                if (File.Exists(path))
                    File.SetAttributes(path, FileAttributes.Normal);
                File.WriteAllBytes(path, item.Value);
            }
        }
    }

    static class FileOps
    {
        public static bool IsLink(string path)
        {
            try
            {
                return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
            }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
        }

        /// <summary>Deletes a folder tree.  Junctions and symlinks are unlinked, never followed.</summary>
        public static void DeleteTree(string path)
        {
            if (!Directory.Exists(path))
                return;
            if (IsLink(path))
            {
                Directory.Delete(path, false);
                return;
            }
            foreach (string file in Directory.GetFiles(path))
            {
                File.SetAttributes(file, FileAttributes.Normal);
                File.Delete(file);
            }
            foreach (string folder in Directory.GetDirectories(path))
                DeleteTree(folder);
            File.SetAttributes(path, FileAttributes.Directory);
            Directory.Delete(path, false);
        }

        /// <summary>Moves file into folder, never overwriting: a.py, a.1.py, a.2.py...</summary>
        public static string MoveAside(string file, string folder)
        {
            Directory.CreateDirectory(folder);
            string name = Path.GetFileNameWithoutExtension(file);
            string extension = Path.GetExtension(file);
            string target = Path.Combine(folder, name + extension);
            for (int index = 1; File.Exists(target) || Directory.Exists(target); index++)
                target = Path.Combine(folder, name + "." + index + extension);
            File.Move(file, target);
            return target;
        }

        /// <summary>Replaces a file's bytes through a temporary file, so a crash never leaves it half written.</summary>
        public static void WriteAtomic(string path, byte[] data)
        {
            string temporary = path + ".mayashield_tmp";
            File.WriteAllBytes(temporary, data);
            if (File.Exists(path))
            {
                FileAttributes attributes = File.GetAttributes(path);
                File.SetAttributes(path, FileAttributes.Normal);
                File.Delete(path);
                File.Move(temporary, path);
                File.SetAttributes(path, attributes);
            }
            else
            {
                File.Move(temporary, path);
            }
        }

        public static bool CanWriteTo(string folder)
        {
            try
            {
                Directory.CreateDirectory(folder);
                string probe = Path.Combine(folder, ".mayashield_probe_" + Guid.NewGuid().ToString("N"));
                File.WriteAllBytes(probe, new byte[0]);
                File.Delete(probe);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    /// <summary>
    /// Edits Maya's .mel preference files line by line.  Bytes are mapped one to one through
    /// Latin-1, so GBK comments, a BOM and the file's own line endings all survive untouched.
    /// </summary>
    static class Prefs
    {
        public const string AutoloadLine = "evalDeferred(\"autoLoadPlugin(\\\"\\\", \\\"mayashield.py\\\", \\\"mayashield\\\")\");";
        static readonly Encoding Raw = Encoding.GetEncoding(28591);
        // Any autoload entry for the plug-in named exactly "mayashield", whatever path it was loaded from.
        static readonly Regex OurAutoload = new Regex(@"autoLoadPlugin\(.*,\s*\\""mayashield\\""\s*\)");
        static readonly Regex OurOptionVar = new Regex(@"^\s*(optionVar\s+)?-iv\s+""mayashield_autoloadConfigured""\s+-?\d+\s*(;?)\s*$");

        static List<string> Lines(string text)
        {
            var lines = new List<string>();
            int start = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == '\n')
                {
                    lines.Add(text.Substring(start, i + 1 - start));
                    start = i + 1;
                }
            }
            if (start < text.Length)
                lines.Add(text.Substring(start));
            return lines;
        }

        static string Body(string line)
        {
            return line.TrimEnd('\r', '\n');
        }

        static string Ending(string line)
        {
            return line.Substring(Body(line).Length);
        }

        /// <summary>Adds the autoload line once, replacing older entries for the plug-in.  True if the file changed.</summary>
        public static bool EnsureAutoload(string path)
        {
            byte[] before = File.Exists(path) ? File.ReadAllBytes(path) : new byte[0];
            string text = Raw.GetString(before);
            string newline = text.Contains("\r\n") || !text.Contains("\n") ? "\r\n" : "\n";
            var kept = new StringBuilder();
            foreach (string line in Lines(text))
            {
                if (!OurAutoload.IsMatch(line))
                    kept.Append(line);
            }
            if (kept.Length > 0 && kept[kept.Length - 1] != '\n')
                kept.Append(newline);
            kept.Append(AutoloadLine).Append(newline);
            byte[] after = Raw.GetBytes(kept.ToString());
            if (Same(before, after))
                return false;
            FileOps.WriteAtomic(path, after);
            return true;
        }

        public static bool RemoveAutoload(string path)
        {
            return Rewrite(path, line => OurAutoload.IsMatch(line) ? "" : line);
        }

        /// <summary>Drops the bootstrap's "already configured" flag, so a later reinstall configures again.</summary>
        public static bool RemoveOptionVar(string path)
        {
            return Rewrite(path, line =>
            {
                Match match = OurOptionVar.Match(Body(line));
                if (!match.Success)
                    return line;
                bool statement = match.Groups[1].Success, ends = match.Groups[2].Value == ";";
                if (statement && ends)
                    return "";                                   // optionVar -iv "x" 1;
                if (statement)
                    return "optionVar" + Ending(line);           // first flag of a multi-line optionVar
                return ends ? ";" + Ending(line) : "";           // last flag / a middle flag
            });
        }

        static bool Rewrite(string path, Func<string, string> edit)
        {
            if (!File.Exists(path))
                return false;
            byte[] before = File.ReadAllBytes(path);
            var result = new StringBuilder();
            foreach (string line in Lines(Raw.GetString(before)))
                result.Append(edit(line));
            byte[] after = Raw.GetBytes(result.ToString());
            if (Same(before, after))
                return false;
            FileOps.WriteAtomic(path, after);
            return true;
        }

        static bool Same(byte[] a, byte[] b)
        {
            if (a.Length != b.Length)
                return false;
            for (int i = 0; i < a.Length; i++)
                if (a[i] != b[i])
                    return false;
            return true;
        }
    }
}
