// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class WindowsInstaller
{
    private const string Title = "CWNG Installer";
    private const string Data = ".cwng-data";
    private const string Retained = ".cwng-retained";
    private const string Installed = ".cwng-installed";
    private const string ReleaseApi = "https://api.github.com/repos/dapih/Calibre-Web-NextGen/releases?per_page=100";

    private sealed class RecoveryRequired : IOException
    {
        internal RecoveryRequired(string message, Exception error) : base(message, error) { }
    }

    [STAThread]
    private static void Main()
    {
        try
        {
            WindowsInstallation.EnableLongPaths();
            Application.EnableVisualStyles();
            string parent = Path.GetDirectoryName(Application.ExecutablePath);
            if (Path.GetFileName(parent).Equals("CWNG", StringComparison.OrdinalIgnoreCase) && HasSource(parent))
                throw new IOException("Move cwng-installer.exe to the folder that contains CWNG before running it.");
            string root = Path.Combine(parent, "CWNG");
            CheckDestination(root);
            bool installed = HasSource(root);
            bool running = installed && WindowsInstallation.IsRunning(root);
            string action = installed
                ? Choose("CWNG is already installed" + (running ? " and running." : "."),
                         running ? new[] { "Open CWNG", "Reinstall", "Uninstall", "Cancel" }
                                 : new[] { "Reinstall", "Uninstall", "Cancel" })
                : Choose("Install CWNG in " + root + "?", new[] { "Install", "Cancel" });
            if (action == "Cancel") return;
            if (action == "Open CWNG")
            {
                Open(root);
                return;
            }

            bool uninstall = action == "Uninstall";
            bool keepData = !installed || !Directory.Exists(Path.Combine(root, Data));
            if (!keepData)
            {
                DialogResult choice = MessageBox.Show(
                    "Keep the existing settings, accounts, reading progress, pending ingest and Calibre settings?\r\n\r\n" +
                    (uninstall ? "Choose No to remove them." : "Choose No to start with fresh settings."),
                    Title, MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button1);
                if (choice == DialogResult.Cancel) return;
                keepData = choice == DialogResult.Yes;
            }

            string selectedLibrary = null;
            var libraries = FindLibraries(root);
            var keptLibraries = new HashSet<string>(libraries, StringComparer.OrdinalIgnoreCase);
            foreach (string library in libraries)
            {
                if (!uninstall && (selectedLibrary != null ||
                                   !File.Exists(Path.Combine(root, library, "metadata.db"))))
                    continue;
                DialogResult choice = MessageBox.Show(
                    uninstall
                        ? "CWNG contains this library or book folder:\r\n\r\n" + Path.Combine(root, library) +
                          "\r\n\r\nKeep it? Choosing No deletes the folder and all its contents."
                        : "Use this internal Calibre library after reinstalling?\r\n\r\n" + Path.Combine(root, library) +
                          "\r\n\r\nChoosing No keeps its files without changing your saved library selection.",
                    Title, MessageBoxButtons.YesNoCancel,
                    uninstall ? MessageBoxIcon.Warning : MessageBoxIcon.Question, MessageBoxDefaultButton.Button1);
                if (choice == DialogResult.Cancel) return;
                if (uninstall && choice == DialogResult.No) keptLibraries.Remove(library);
                if (!uninstall && choice == DialogResult.Yes) selectedLibrary = library;
            }

            using (var window = new WindowsProgress(Title, true))
            {
                Exception failure = null;
                window.Shown += async (sender, args) =>
                {
                    var progress = new Progress<string>(message => { if (!window.IsDisposed) window.Report(message); });
                    try
                    {
                        await Task.Run(() => ChangeInstallation(root, uninstall, keepData, selectedLibrary,
                            libraries, keptLibraries, progress));
                    }
                    catch (Exception error) { failure = error; }
                    finally { window.Finish(); }
                };
                window.SetMessage(uninstall ? "Preparing to uninstall CWNG…" : "Preparing the installation…");
                Application.Run(window);
                if (failure != null) throw failure;
            }
            if (!uninstall) Open(root);
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            Environment.ExitCode = 1;
        }
    }

    private static string Choose(string text, string[] options)
    {
        using (var form = new Form { Text = Title, StartPosition = FormStartPosition.CenterScreen,
            FormBorderStyle = FormBorderStyle.FixedDialog, MinimizeBox = false, MaximizeBox = false,
            ClientSize = new Size(480, 126) })
        {
            form.Controls.Add(new Label { Text = text, Left = 18, Top = 18, Width = 444, Height = 44 });
            var buttons = new FlowLayoutPanel { Left = 12, Top = 73, Width = 456, Height = 38 };
            form.Controls.Add(buttons);
            string result = "Cancel";
            foreach (string option in options)
            {
                string label = option;
                var button = new Button { Text = label, AutoSize = true, MinimumSize = new Size(95, 29) };
                button.Click += (sender, args) => { result = label; form.Close(); };
                buttons.Controls.Add(button);
                if (label == "Cancel") form.CancelButton = button;
            }
            form.ShowDialog();
            return result;
        }
    }

    private static void Open(string root)
    {
        string executable = Path.Combine(root, "CWNG.exe");
        if (!File.Exists(executable))
            throw new FileNotFoundException("CWNG.exe is missing. Reinstall CWNG to restore it.");
        Process.Start(new ProcessStartInfo(executable) { WorkingDirectory = root, UseShellExecute = true });
    }

    private static bool HasSource(string root)
    {
        return File.Exists(Path.Combine(root, Installed)) ||
               (File.Exists(Path.Combine(root, "VERSION")) &&
                (File.Exists(Path.Combine(root, @"scripts\windows-install.ps1")) ||
                 File.Exists(Path.Combine(root, "CWNG.exe"))));
    }

    private static void CheckDestination(string root)
    {
        if (File.Exists(root)) throw new IOException(root + " is a file. Choose another installer location.");
        if (!Directory.Exists(root)) return;
        if (IsLink(root) || Directory.Exists(Path.Combine(root, ".git")) || File.Exists(Path.Combine(root, ".git")))
            throw new IOException("Use a dedicated CWNG installation folder. The installer will not replace a junction or a Git checkout.");
        if (File.Exists(Path.Combine(root, "metadata.db")))
            throw new IOException("The CWNG folder is itself a Calibre library. Move that library to its own folder before continuing.");
        if (!HasSource(root) && !File.Exists(Path.Combine(root, Retained)) && Directory.EnumerateFileSystemEntries(root).Any())
            throw new IOException(root + " contains other files. Move the installer to another writable folder.");
    }

    private static bool IsLink(string path)
    {
        return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
    }

    private static bool Inside(string relative, string folder)
    {
        return relative.Equals(folder, StringComparison.OrdinalIgnoreCase) ||
               relative.StartsWith(folder + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    internal static List<string> FindLibraries(string root)
    {
        var result = new List<string>();
        if (!Directory.Exists(root)) return result;
        var pending = new Stack<string>();
        pending.Push(root);
        while (pending.Count != 0)
        {
            string folder = pending.Pop();
            foreach (string child in Directory.EnumerateDirectories(folder))
            {
                if (IsLink(child)) continue;
                string relative = child.Substring(root.Length + 1);
                bool legacy = relative.Equals("library", StringComparison.OrdinalIgnoreCase) ||
                              relative.Equals("calibre-library", StringComparison.OrdinalIgnoreCase) ||
                              relative.Equals(Path.Combine(Data, "library"), StringComparison.OrdinalIgnoreCase);
                if (File.Exists(Path.Combine(child, "metadata.db")) ||
                    (legacy && Directory.EnumerateFileSystemEntries(child).Any()))
                    result.Add(relative);
                else
                    pending.Push(child);
            }
        }
        result.Sort(StringComparer.OrdinalIgnoreCase);
        return result;
    }

    private static void StopInstallation(string root, IProgress<string> progress)
    {
        if (!WindowsInstallation.IsRunning(root)) return;
        progress.Report("Waiting for CWNG and its background workers to stop…");
        string signal = WindowsInstallation.Prefix(root) + "-quit";
        for (int attempt = 0; !WindowsInstallation.Signal(signal); attempt++)
        {
            if (!WindowsInstallation.IsRunning(root)) return;
            if (attempt == 12)
                throw new IOException("CWNG is running from a console or another session. Shut it down before continuing.");
            Thread.Sleep(250);
        }
        var timer = Stopwatch.StartNew();
        while (WindowsInstallation.IsRunning(root))
        {
            if (timer.Elapsed > TimeSpan.FromMinutes(2))
                throw new IOException("CWNG is still stopping. Wait for its workers to finish, then retry.");
            Thread.Sleep(250);
        }
    }

    private static void ChangeInstallation(string root, bool uninstall, bool keepData, string selectedLibrary,
        List<string> reviewedLibraries, HashSet<string> keptLibraries, IProgress<string> progress)
    {
        using (var maintenance = new Mutex(false, WindowsInstallation.Prefix(root) + "-maintenance"))
        {
            if (!WindowsInstallation.TryAcquire(maintenance))
                throw new IOException("Another CWNG installer is already working in this folder.");
            try
            {
                CheckDestination(root);
                string stage = Path.Combine(Path.GetDirectoryName(root), ".cwng-stage-" + Path.GetRandomFileName());
                if (Directory.Exists(stage) || File.Exists(stage)) throw new IOException("The staging path is already in use.");
                bool keepRecovery = false;
                try
                {
                    if (!uninstall)
                    {
                        Directory.CreateDirectory(stage);
                        DownloadRelease(stage, progress);
                        progress.Report("Installing the bundled tools and building the browser interface…");
                        Run(WindowsInstallation.Script(stage, "windows-install.ps1", ""), "Windows setup failed.");
                        string missing = WindowsInstallation.MissingFile(stage);
                        if (missing != null) throw new IOException("Windows setup did not create " + missing + ".");
                        File.WriteAllText(Path.Combine(stage, Installed), "");
                    }

                    StopInstallation(root, progress);
                    CheckDestination(root);
                    var libraries = FindLibraries(root);
                    if (uninstall && !libraries.SequenceEqual(reviewedLibraries, StringComparer.OrdinalIgnoreCase))
                        throw new IOException("The internal library folders changed. Restart the installer and review them again.");
                    if (!uninstall) keptLibraries = new HashSet<string>(libraries, StringComparer.OrdinalIgnoreCase);
                    progress.Report(uninstall ? "Removing CWNG and keeping the selected data…" : "Applying the installation…");
                    ReplaceInstallation(root, stage, uninstall, keepData, libraries, keptLibraries, selectedLibrary);
                }
                catch (RecoveryRequired)
                {
                    keepRecovery = true;
                    throw;
                }
                finally
                {
                    if (!keepRecovery && Directory.Exists(stage)) DeleteTree(stage);
                }
            }
            finally { maintenance.ReleaseMutex(); }
        }
    }

    internal static void ReplaceInstallation(string root, string stage, bool uninstall, bool keepData,
        List<string> libraries, HashSet<string> keptLibraries, string selectedLibrary)
    {
        string backup = Path.Combine(Path.GetDirectoryName(root), ".cwng-old-" + Path.GetRandomFileName());
        var moved = new List<Tuple<string, string>>();
        bool hasOld = Directory.Exists(root);
        if (hasOld) Directory.Move(root, backup);
        try
        {
            if (keepData && Directory.Exists(Path.Combine(backup, Data)))
            {
                string data = Path.Combine(backup, Data);
                if (IsLink(data)) throw new IOException("CWNG's data folder is a junction. Move it to a normal folder before continuing.");
                foreach (string library in libraries.Where(path => Inside(path, Data) && !keptLibraries.Contains(path)))
                    Move(Path.Combine(backup, library), Path.Combine(backup, Guid.NewGuid().ToString("N")), moved);
                string destination = Path.Combine(stage, Data);
                if (Directory.Exists(destination)) DeleteTree(destination);
                Move(data, destination, moved);
            }
            foreach (string library in keptLibraries)
            {
                string original = Path.Combine(backup, library);
                if (Directory.Exists(original)) Move(original, Path.Combine(stage, library), moved);
            }
            if (selectedLibrary != null)
                SelectLibrary(stage, root, selectedLibrary);
            if (Directory.Exists(stage))
            {
                if (uninstall) File.WriteAllText(Path.Combine(stage, Retained), "");
                Directory.Move(stage, root);
            }
        }
        catch
        {
            try
            {
                for (int i = moved.Count - 1; i >= 0; i--)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(moved[i].Item1));
                    Directory.Move(moved[i].Item2, moved[i].Item1);
                }
                if (hasOld) Directory.Move(backup, root);
            }
            catch (Exception error)
            {
                throw new RecoveryRequired("CWNG could not restore the previous folder automatically. Check:\r\n" +
                    backup + "\r\n" + stage, error);
            }
            throw;
        }
        if (hasOld)
        {
            try { DeleteTree(backup); }
            catch (Exception error)
            {
                throw new IOException("CWNG processed your choices, but some old files could not be removed from:\r\n" + backup, error);
            }
        }
    }

    private static void Move(string source, string destination, List<Tuple<string, string>> moves)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destination));
        Directory.Move(source, destination);
        moves.Add(Tuple.Create(source, destination));
    }

    private static void DeleteTree(string path)
    {
        if (!IsLink(path))
        {
            foreach (string file in Directory.EnumerateFiles(path))
                File.Delete(file);
            foreach (string directory in Directory.EnumerateDirectories(path)) DeleteTree(directory);
        }
        Directory.Delete(path);
    }

    private static void SelectLibrary(string stage, string root, string library)
    {
        string script = Path.Combine(stage, "scripts", "windows-select-library.py");
        ExtractResource("CWNG.SelectLibrary", script);
        var info = new ProcessStartInfo {
            FileName = Path.Combine(stage, @".cwng-runtime\python\python.exe"),
            Arguments = "-I -B -X utf8 \"" + script + "\" \"" + Path.Combine(stage, library) +
                        "\" \"" + Path.Combine(root, library) + "\"",
            WorkingDirectory = stage,
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        Run(info, "CWNG could not select the requested library.");
    }

    private static void Run(ProcessStartInfo info, string error)
    {
        using (Process process = Process.Start(info))
        {
            Task<string> output = process.StandardOutput.ReadToEndAsync();
            Task<string> stderr = process.StandardError.ReadToEndAsync();
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                string detail = stderr.Result.Trim();
                if (detail.Length == 0) detail = output.Result.Trim();
                throw new IOException(error + "\r\n\r\n" + detail.Substring(Math.Max(0, detail.Length - 1200)));
            }
        }
    }

    private static void DownloadRelease(string stage, IProgress<string> progress)
    {
        progress.Report("Checking GitHub for the latest Windows release…");
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        using (var web = new WebClient())
        {
            web.Headers[HttpRequestHeader.UserAgent] = "CWNG-Windows-Installer";
            var serializer = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
            var releases = serializer.Deserialize<Dictionary<string, object>[]>(web.DownloadString(ReleaseApi));
            var release = releases.Where(item => !(bool)item["draft"] && !(bool)item["prerelease"] &&
                        ((string)item["tag_name"]).StartsWith("windows-v", StringComparison.Ordinal) &&
                        Assets(item).Any(candidate => IsPayload(candidate, "cwng-windows.zip") || IsPayload(candidate, "CWNG.exe")))
                        .OrderByDescending(item => (string)item["published_at"], StringComparer.Ordinal).FirstOrDefault();
            if (release == null) throw new IOException("GitHub has no published Windows release for CWNG.");
            var asset = Assets(release).FirstOrDefault(item => IsPayload(item, "cwng-windows.zip")) ??
                        Assets(release).First(item => IsPayload(item, "CWNG.exe"));
            string url = (string)asset["browser_download_url"];
            var uri = new Uri(url);
            if (uri.Scheme != "https" || uri.Host != "github.com" ||
                !uri.AbsolutePath.StartsWith("/dapih/Calibre-Web-NextGen/releases/download/", StringComparison.Ordinal))
                throw new IOException("GitHub returned an unexpected download URL.");
            string archive = Path.Combine(stage, ".download");
            progress.Report("Downloading " + release["tag_name"] + " from GitHub…");
            try
            {
                DownloadAsset(web, uri, archive, Convert.ToInt64(asset["size"]));
            }
            catch (Exception error)
            {
                if (!(error is WebException) && !(error is IOException)) throw;
                progress.Report("Retrying the download with Windows PowerShell…");
                DownloadWithPowerShell(uri, archive);
            }
            VerifyAsset(archive, asset);
            progress.Report("Extracting the verified Windows release…");
            ExtractRelease(archive, stage, IsPayload(asset, "cwng-windows.zip"), (string)release["tag_name"]);
            File.Delete(archive);
        }
    }

    private static void ExtractRelease(string archive, string stage, bool splitPackage, string tag)
    {
        if (splitPackage)
        {
            using (ZipArchive zip = ZipFile.OpenRead(archive)) Extract(zip, stage);
            return;
        }

        // Read the original launcher's verified ZIP resource without running it.
        Assembly legacy = Assembly.ReflectionOnlyLoad(File.ReadAllBytes(archive));
        using (Stream resource = legacy.GetManifestResourceStream("CWNG.Source"))
        {
            if (resource == null) throw new IOException("The Windows release has no portable source payload.");
            using (var zip = new ZipArchive(resource, ZipArchiveMode.Read)) Extract(zip, stage);
        }
        ExtractResource("CWNG.Launcher", Path.Combine(stage, "CWNG.exe"));
        ExtractResource("CWNG.TrayControl", Path.Combine(stage, @"scripts\windows_tray_control.py"));
        if (tag == "windows-v4.1.62")
        {
            // The original release has the panel labels but predates their dark styles.
            using (var style = new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("CWNG.LibrarySetupStyle")))
                File.AppendAllText(Path.Combine(stage, @"cps\static\css\caliBlur_override.css"), Environment.NewLine + style.ReadToEnd());
        }
    }

    private static IEnumerable<Dictionary<string, object>> Assets(Dictionary<string, object> release)
    {
        return ((System.Collections.IEnumerable)release["assets"]).Cast<Dictionary<string, object>>();
    }

    private static bool IsPayload(Dictionary<string, object> asset, string name)
    {
        return (string)asset["name"] == name && (string)asset["state"] == "uploaded";
    }

    private static void DownloadAsset(WebClient web, Uri url, string file, long remaining)
    {
        using (Stream input = web.OpenRead(url))
        using (Stream output = File.Create(file))
        {
            var buffer = new byte[65536];
            // GitHub supplies the asset length and digest. Some network stacks
            // report a TLS EOF when reading past the last byte of the asset.
            while (remaining > 0)
            {
                int count = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                if (count == 0) throw new IOException("GitHub ended the download before the complete asset arrived.");
                output.Write(buffer, 0, count);
                remaining -= count;
            }
        }
    }

    private static void DownloadWithPowerShell(Uri url, string file)
    {
        var info = new ProcessStartInfo {
            FileName = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
            Arguments = "-NoProfile -NonInteractive -Command \"$ErrorActionPreference = 'Stop'; " +
                        "$ProgressPreference = 'SilentlyContinue'; " +
                        "for ($attempt = 1; $attempt -le 3; $attempt++) { try { " +
                        "$url = [UriBuilder]$env:CWNG_DOWNLOAD_URL; " +
                        "$url.Query = 'cwng=' + [Guid]::NewGuid().ToString('N'); " +
                        "Invoke-WebRequest -UseBasicParsing -Uri $url.Uri " +
                        "-OutFile $env:CWNG_DOWNLOAD_PATH -TimeoutSec 90; break " +
                        "} catch { if ($attempt -eq 3) { throw }; Start-Sleep -Milliseconds (500 * $attempt) } }\"",
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        info.EnvironmentVariables["CWNG_DOWNLOAD_URL"] = url.AbsoluteUri;
        info.EnvironmentVariables["CWNG_DOWNLOAD_PATH"] = file;
        Run(info, "The Windows release download failed.");
    }

    internal static void VerifyAsset(string archive, Dictionary<string, object> asset)
    {
        object expected;
        if (!asset.TryGetValue("digest", out expected) || !(expected is string) ||
            new FileInfo(archive).Length != Convert.ToInt64(asset["size"]))
            throw new IOException("GitHub did not provide matching size and checksum information.");
        using (var hash = SHA256.Create())
        using (var data = File.OpenRead(archive))
        {
            string actual = "sha256:" + BitConverter.ToString(hash.ComputeHash(data)).Replace("-", "");
            if (!actual.Equals((string)expected, StringComparison.OrdinalIgnoreCase))
                throw new IOException("The downloaded CWNG package did not match its GitHub SHA-256 checksum.");
        }
    }

    internal static void Extract(ZipArchive zip, string stage)
    {
        stage = Path.GetFullPath(stage);
        string prefix = stage + Path.DirectorySeparatorChar;
        foreach (ZipArchiveEntry entry in zip.Entries)
        {
            string name = entry.FullName.Replace('\\', '/');
            string[] parts = name.TrimEnd('/').Split('/');
            if (parts.Any(part => part == "" || part.EndsWith(".", StringComparison.Ordinal) ||
                                 part.EndsWith(" ", StringComparison.Ordinal) || part.Contains(":")) ||
                parts[0].StartsWith(".", StringComparison.Ordinal) ||
                parts[0].Equals("library", StringComparison.OrdinalIgnoreCase) ||
                parts[0].Equals("calibre-library", StringComparison.OrdinalIgnoreCase))
                throw new IOException("Unexpected file in the Windows package: " + name);
            string destination = Path.GetFullPath(Path.Combine(stage, name.Replace('/', Path.DirectorySeparatorChar)));
            if (!destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                throw new IOException("File is outside the Windows package directory: " + name);
            if (name.EndsWith("/", StringComparison.Ordinal)) Directory.CreateDirectory(destination);
            else
            {
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                entry.ExtractToFile(destination);
            }
        }
    }

    private static void ExtractResource(string resourceName, string file)
    {
        using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName))
        using (Stream output = File.Create(file))
            resource.CopyTo(output);
    }
}
