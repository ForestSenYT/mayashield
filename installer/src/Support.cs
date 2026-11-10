using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace MayaShieldSetup
{
    /// <summary>Log file plus progress for the window.  Callbacks run on the worker thread.</summary>
    class Reporter : IDisposable
    {
        const int LogsToKeep = 10;

        readonly StreamWriter file;
        readonly object gate = new object();
        int done, total;

        public Action<string> OnLog;
        public Action<string> OnStatus;
        public Action<int, int> OnProgress;

        /// <summary>Where the log is being written, or null when it could not be opened.</summary>
        public string LogPath { get; private set; }

        /// <summary>Things that went wrong without stopping Setup.  The window stays open when there are any.</summary>
        public int Warnings { get; private set; }

        public Reporter(string path) : this(path, true)
        {
        }

        /// <summary>
        /// With keep, a log is written even when no path was asked for: a failure in the field is
        /// no use without one.  The elevated copy passes false and sends its lines to its parent.
        /// </summary>
        public Reporter(string path, bool keep)
        {
            bool chosen = path != null;
            if (!chosen && !keep)
                return;
            try
            {
                if (!chosen)
                    path = Path.Combine(DefaultFolder, "setup-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + ".log");
                string folder = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(folder))
                    Directory.CreateDirectory(folder);
                if (!chosen)
                    Prune(folder);
                file = new StreamWriter(path, false, new UTF8Encoding(false)) { AutoFlush = true };
                LogPath = path;
            }
            catch (Exception)
            {
                file = null;   // logging must never stop the install
            }
        }

        /// <summary>Outside the folder the uninstaller removes, so the log of an uninstall survives it.</summary>
        public static string DefaultFolder
        {
            get
            {
                if (Engine.TestRoot != null)
                    return Path.Combine(Engine.TestRoot, "logs");
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MayaShield-Logs");
            }
        }

        static void Prune(string folder)
        {
            try
            {
                string[] old = Directory.GetFiles(folder, "setup-*.log");
                Array.Sort(old, StringComparer.OrdinalIgnoreCase);
                for (int i = 0; i < old.Length - (LogsToKeep - 1); i++)
                    File.Delete(old[i]);
            }
            catch (Exception) { }
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

        public void Warn(string line)
        {
            Warnings++;
            Log(L.T("warning", line));
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
                byte[] data = item.Value;
                FileOps.Retry(() =>
                {
                    if (File.Exists(path))
                        File.SetAttributes(path, FileAttributes.Normal);
                    File.WriteAllBytes(path, data);
                });
            }
        }
    }

    static class FileOps
    {
        /// <summary>
        /// Runs a file operation, trying again when Windows says the file is busy.  Antivirus and
        /// the search indexer open freshly written files for a moment; a rename or delete that
        /// lands in that moment fails, and succeeds a few milliseconds later.
        /// </summary>
        public static void Retry(Action action)
        {
            int pause = 40;
            for (int attempt = 1; ; attempt++)
            {
                try
                {
                    action();
                    return;
                }
                catch (IOException)
                {
                    if (attempt >= 10)
                        throw;
                }
                catch (UnauthorizedAccessException)
                {
                    if (attempt >= 10)
                        throw;
                }
                Thread.Sleep(pause);
                pause = Math.Min(500, pause * 2);
            }
        }

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
                Retry(() => Directory.Delete(path, false));
                return;
            }
            foreach (string file in Directory.GetFiles(path))
            {
                string target = file;
                Retry(() =>
                {
                    File.SetAttributes(target, FileAttributes.Normal);
                    File.Delete(target);
                });
            }
            foreach (string folder in Directory.GetDirectories(path))
                DeleteTree(folder);
            Retry(() =>
            {
                File.SetAttributes(path, FileAttributes.Directory);
                Directory.Delete(path, false);
            });
        }

        /// <summary>Deletes a folder if it can; never throws.  True when it is gone.</summary>
        public static bool TryDeleteTree(string path)
        {
            try
            {
                DeleteTree(path);
                return !Directory.Exists(path);
            }
            catch (Exception)
            {
                return false;
            }
        }

        public static void DeleteFile(string path)
        {
            if (!File.Exists(path))
                return;
            Retry(() =>
            {
                File.SetAttributes(path, FileAttributes.Normal);
                File.Delete(path);
            });
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
            Retry(() => File.Move(file, target));
            return target;
        }

        /// <summary>
        /// Replaces a file's bytes in one step.  The original is never deleted first: if the
        /// replacement cannot be put in place, the file is still exactly what it was.
        /// </summary>
        public static void WriteAtomic(string path, byte[] data)
        {
            string temporary = path + ".mayashield_tmp";
            try
            {
                Retry(() =>
                {
                    if (File.Exists(temporary))
                        File.SetAttributes(temporary, FileAttributes.Normal);
                    File.WriteAllBytes(temporary, data);
                });
                if (!File.Exists(path))
                {
                    Retry(() => File.Move(temporary, path));
                    return;
                }
                FileAttributes attributes = File.GetAttributes(path);
                bool locked = (attributes & FileAttributes.ReadOnly) != 0;
                if (locked)
                    File.SetAttributes(path, attributes & ~FileAttributes.ReadOnly);
                try
                {
                    Retry(() => File.Replace(temporary, path, null, true));
                }
                finally
                {
                    if (locked && File.Exists(path))
                        File.SetAttributes(path, attributes);
                }
            }
            finally
            {
                try
                {
                    if (File.Exists(temporary))
                    {
                        File.SetAttributes(temporary, FileAttributes.Normal);
                        File.Delete(temporary);
                    }
                }
                catch (Exception) { }
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
    /// Edits Maya's .mel preference files.  Bytes are mapped one to one through Latin-1, so GBK
    /// comments, a BOM and the file's own line endings all survive untouched, and only our own
    /// statement is ever taken out of a line.
    /// </summary>
    static class Prefs
    {
        public const string AutoloadLine = "evalDeferred(\"autoLoadPlugin(\\\"\\\", \\\"mayashield.py\\\", \\\"mayashield\\\")\");";
        static readonly Encoding Raw = Encoding.GetEncoding(28591);
        // One autoload statement for the plug-in named exactly "mayashield", whatever path it was loaded from.
        static readonly Regex OurAutoload = new Regex(
            @"evalDeferred\(\s*""autoLoadPlugin\((?:[^""\\()]|\\.)*,\s*\\""mayashield\\""\s*\)""\s*\)\s*;?");
        // One flag of an optionVar statement that belongs to MayaShield: -iv "mayashield_x" 1 / -sv "mayashield_x" "..."
        static readonly Regex OurOptionVar = new Regex(
            @"^(?<lead>\s*)(?<statement>optionVar\s+)?-(?:iv|fv|sv)\s+""mayashield_\w+""\s+(?:""(?:[^""\\]|\\.)*""|[-+.\w]+)\s*(?<end>;?)\s*$");
        static readonly Regex EmptyOptionVar = new Regex(@"(?<![\w$.])optionVar\s*;[ \t]*(?:\r\n|\r|\n)?");

        /// <summary>Lines with their endings kept.  A lone CR ends a line too (old Mac files).</summary>
        static List<string> Lines(string text)
        {
            var lines = new List<string>();
            int start = 0;
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '\n' || (c == '\r' && !(i + 1 < text.Length && text[i + 1] == '\n')))
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

        static string Newline(string text)
        {
            if (text.Contains("\r\n"))
                return "\r\n";
            if (text.Contains("\n"))
                return "\n";
            return text.Contains("\r") ? "\r" : "\r\n";
        }

        /// <summary>The text without our autoload statements.  A line left empty by that goes too.</summary>
        static string WithoutAutoload(string text)
        {
            var kept = new StringBuilder();
            foreach (string line in Lines(text))
            {
                if (!OurAutoload.IsMatch(line))
                {
                    kept.Append(line);
                    continue;
                }
                string rest = OurAutoload.Replace(Body(line), "");
                if (rest.Trim().Length > 0)
                    kept.Append(rest).Append(Ending(line));
            }
            return kept.ToString();
        }

        /// <summary>Adds the autoload line once, replacing older entries for the plug-in.  True if the file changed.</summary>
        public static bool EnsureAutoload(string path)
        {
            byte[] before = File.Exists(path) ? File.ReadAllBytes(path) : new byte[0];
            string text = Raw.GetString(before);
            string newline = Newline(text);
            var kept = new StringBuilder(WithoutAutoload(text));
            if (kept.Length > 0 && kept[kept.Length - 1] != '\n' && kept[kept.Length - 1] != '\r')
                kept.Append(newline);
            kept.Append(AutoloadLine).Append(newline);
            return Save(path, before, kept.ToString());
        }

        public static bool RemoveAutoload(string path)
        {
            if (!File.Exists(path))
                return false;
            byte[] before = File.ReadAllBytes(path);
            return Save(path, before, WithoutAutoload(Raw.GetString(before)));
        }

        /// <summary>
        /// Drops MayaShield's optionVars: the bootstrap's "already configured" flag, so a later
        /// reinstall configures again, and the panel's own settings.
        /// </summary>
        public static bool RemoveOptionVars(string path)
        {
            if (!File.Exists(path))
                return false;
            byte[] before = File.ReadAllBytes(path);
            var result = new StringBuilder();
            bool changed = false;
            foreach (string line in Lines(Raw.GetString(before)))
            {
                Match match = OurOptionVar.Match(Body(line));
                if (!match.Success)
                {
                    result.Append(line);
                    continue;
                }
                changed = true;
                bool statement = match.Groups["statement"].Success, ends = match.Groups["end"].Value == ";";
                if (statement && ends)
                    continue;                                                    // optionVar -iv "x" 1;
                if (statement)
                    result.Append(match.Groups["lead"].Value).Append("optionVar").Append(Ending(line));   // first flag of several
                else if (ends)
                    result.Append(";").Append(Ending(line));                     // last flag: keep the terminator
            }
            if (!changed)
                return false;
            // If ours were a statement's only flags, a bare "optionVar ;" is left, which MEL rejects.
            return Save(path, before, EmptyOptionVar.Replace(result.ToString(), ""));
        }

        static bool Save(string path, byte[] before, string text)
        {
            byte[] after = Raw.GetBytes(text);
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
