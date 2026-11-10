// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class WindowsLauncher
{
    internal const string Title = "Calibre-Web NextGen";

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            string executable = Application.ExecutablePath;
            string folder = Path.GetDirectoryName(executable);
            string root = HasSource(folder) ? folder : Path.Combine(folder, "CWNG");
            bool openOnStart = Array.IndexOf(args, "--background") < 0;
            string prefix;
            SecurityIdentifier user = WindowsIdentity.GetCurrent().User;
            using (SHA256 hash = SHA256.Create())
            {
                byte[] identity = Encoding.UTF8.GetBytes(user.Value + "|" + root.ToUpperInvariant());
                prefix = @"Local\CWNG-" + BitConverter.ToString(hash.ComputeHash(identity), 0, 16).Replace("-", "");
            }

            Application.EnableVisualStyles();
            using (Mutex instance = new Mutex(false, prefix))
            {
                bool owns;
                try { owns = instance.WaitOne(0); }
                catch (AbandonedMutexException) { owns = true; }
                if (!owns)
                {
                    if (!openOnStart) return;
                    // The first click may still be setting up the tray.
                    for (int i = 0; i < 30; i++)
                    {
                        EventWaitHandle open;
                        if (EventWaitHandle.TryOpenExisting(prefix + "-open", out open))
                        {
                            using (open) open.Set();
                            return;
                        }
                        Thread.Sleep(100);
                    }
                    throw new InvalidOperationException("The existing CWNG launcher is still starting.");
                }
                try
                {
                    using (TrayApplication tray = new TrayApplication(root, executable, prefix, user, openOnStart))
                        Application.Run(tray);
                }
                finally { instance.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    internal static bool HasSource(string root)
    {
        return File.Exists(Path.Combine(root, @"scripts\windows-install.ps1"));
    }
}

internal sealed class TrayApplication : ApplicationContext
{
    private readonly string root;
    private readonly string prefix;
    private readonly string logPath;
    private readonly object logLock = new object();
    private readonly Icon icon;
    private readonly NotifyIcon tray;
    private readonly ContextMenuStrip menu;
    private readonly ToolStripMenuItem status;
    private readonly ToolStripMenuItem openMenu;
    private readonly ToolStripMenuItem restartMenu;
    private readonly ToolStripMenuItem quitMenu;
    private readonly EventWaitHandle open;
    private readonly EventWaitHandle restart;
    private readonly EventWaitHandle quit;
    private readonly System.Windows.Forms.Timer timer;
    private Task preparation;
    private Process server;
    private volatile string readyUrl;
    private string activeUrl;
    private bool openRequested;

    internal TrayApplication(string root, string executable, string prefix, SecurityIdentifier user, bool openOnStart)
    {
        this.root = root;
        this.prefix = prefix;
        openRequested = openOnStart;
        logPath = Path.Combine(root, @".cwng-data\windows-launcher.log");
        EventWaitHandleSecurity security = new EventWaitHandleSecurity();
        security.AddAccessRule(new EventWaitHandleAccessRule(user, EventWaitHandleRights.FullControl, AccessControlType.Allow));
        open = CreateEvent("open", security);
        restart = CreateEvent("restart", security);
        quit = CreateEvent("quit", security);

        menu = new ContextMenuStrip();
        status = new ToolStripMenuItem { Enabled = false };
        menu.Items.Add(status);
        openMenu = new ToolStripMenuItem("Open CWNG", null, (sender, e) => OpenBrowser());
        restartMenu = new ToolStripMenuItem("Restart", null, (sender, e) => RequestStop(true));
        quitMenu = new ToolStripMenuItem("Quit", null, (sender, e) => RequestStop(false));
        menu.Items.AddRange(new ToolStripItem[] { openMenu, restartMenu, quitMenu });
        icon = Icon.ExtractAssociatedIcon(executable);
        tray = new NotifyIcon { Icon = icon, ContextMenuStrip = menu, Visible = true };
        tray.DoubleClick += (sender, e) => OpenBrowser();
        SetStatus("Preparing installation", false);

        preparation = Task.Run(() => Prepare(executable));
        timer = new System.Windows.Forms.Timer { Interval = 200 };
        timer.Tick += Tick;
        timer.Start();
    }

    private EventWaitHandle CreateEvent(string action, EventWaitHandleSecurity security)
    {
        bool created;
        return new EventWaitHandle(false, EventResetMode.AutoReset, prefix + "-" + action, out created, security);
    }

    private void Prepare(string executable)
    {
        if (!WindowsLauncher.HasSource(root))
        {
            if (Directory.Exists(root))
                throw new IOException("The CWNG destination already exists. Move CWNG.exe to a writable folder with a free CWNG destination.");
            string stage = root + "-setup-" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(stage);
            try
            {
                using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("CWNG.Source"))
                using (ZipArchive zip = new ZipArchive(payload, ZipArchiveMode.Read))
                    zip.ExtractToDirectory(stage);
                File.Copy(executable, Path.Combine(stage, "CWNG.exe"));
                Directory.Move(stage, root);
            }
            finally
            {
                if (Directory.Exists(stage)) Directory.Delete(stage, true);
            }
        }

        if (!File.Exists(Path.Combine(root, @"scripts\windows_tray_control.py")))
            throw new InvalidOperationException("Update this installation's source before launching it with CWNG.exe.");

        Directory.CreateDirectory(Path.GetDirectoryName(logPath));
        File.WriteAllText(logPath, "");
        string[] required = {
            @".cwng-runtime\python\python.exe", @".cwng-runtime\calibre\calibredb.exe",
            @".cwng-runtime\imagemagick\CORE_RL_MagickWand_.dll", @".cwng-runtime\kepubify\kepubify.exe",
            @"cps\static\app\index.html"
        };
        if (Array.TrueForAll(required, file => File.Exists(Path.Combine(root, file)))) return;
        using (Process setup = StartScript("windows-install.ps1", "", false))
        {
            setup.WaitForExit();
            if (setup.ExitCode != 0 || !Array.TrueForAll(required, file => File.Exists(Path.Combine(root, file))))
                throw new InvalidOperationException("CWNG setup could not prepare the application.");
        }
    }

    private Process StartScript(string script, string arguments, bool trayManaged)
    {
        ProcessStartInfo info = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
            Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" +
                        Path.Combine(root, "scripts", script) + "\" " + arguments,
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        if (trayManaged) info.EnvironmentVariables["CWNG_TRAY_CONTROL"] = prefix;
        Process process = Process.Start(info);
        string ready = "CWNG_TRAY_READY " + prefix + " ";
        process.OutputDataReceived += (sender, e) =>
        {
            Uri uri;
            if (trayManaged && e.Data != null && e.Data.StartsWith(ready, StringComparison.Ordinal) &&
                Uri.TryCreate(e.Data.Substring(ready.Length), UriKind.Absolute, out uri) &&
                uri.Host == "127.0.0.1" && (uri.Scheme == "http" || uri.Scheme == "https"))
                readyUrl = uri.AbsoluteUri;
            else if (!trayManaged || readyUrl == null)
                Log(e.Data);
        };
        process.ErrorDataReceived += (sender, e) => Log(e.Data);
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return process;
    }

    private void Log(string line)
    {
        if (line == null) return;
        try
        {
            lock (logLock) File.AppendAllText(logPath, line + Environment.NewLine);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void StartServer()
    {
        readyUrl = null;
        activeUrl = null;
        restart.Reset();
        quit.Reset();
        SetStatus("Starting", false);
        server = StartScript("windows-start.ps1", "-TrayManaged", true);
    }

    private void Tick(object sender, EventArgs args)
    {
        try
        {
            if (open.WaitOne(0)) OpenBrowser();
            if (preparation != null)
            {
                if (!preparation.IsCompleted) return;
                preparation.GetAwaiter().GetResult();
                preparation = null;
                StartServer();
            }
            if (server.HasExited)
            {
                server.WaitForExit();
                int code = server.ExitCode;
                server.Dispose();
                server = null;
                if (code == 75) StartServer();
                else if (code == 0 && readyUrl != null) ExitThread();
                else throw new InvalidOperationException("The CWNG server stopped before it could finish normally.");
                return;
            }
            if (activeUrl == null && readyUrl != null)
            {
                activeUrl = readyUrl;
                SetStatus("Running", true);
                if (openRequested) OpenBrowser();
            }
        }
        catch (Exception error)
        {
            timer.Stop();
            MessageBox.Show(error.Message + (File.Exists(logPath) ? "\r\n\r\nLog: " + logPath : ""),
                WindowsLauncher.Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            ExitThread();
        }
    }

    private void OpenBrowser()
    {
        openRequested = activeUrl == null;
        if (openRequested) return;
        try { Process.Start(new ProcessStartInfo(activeUrl) { UseShellExecute = true }); }
        catch (System.ComponentModel.Win32Exception error)
        {
            MessageBox.Show(error.Message + "\r\n\r\n" + activeUrl, WindowsLauncher.Title);
        }
    }

    private void RequestStop(bool again)
    {
        if (!quitMenu.Enabled) return;
        SetStatus(again ? "Restarting" : "Stopping", false);
        (again ? restart : quit).Set();
    }

    private void SetStatus(string text, bool running)
    {
        status.Text = text;
        tray.Text = WindowsLauncher.Title + " - " + text;
        openMenu.Enabled = restartMenu.Enabled = quitMenu.Enabled = running;
    }

    protected override void ExitThreadCore()
    {
        timer.Stop();
        tray.Visible = false;
        base.ExitThreadCore();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            timer.Dispose();
            tray.Dispose();
            icon.Dispose();
            menu.Dispose();
            open.Dispose();
            restart.Dispose();
            quit.Dispose();
            if (server != null) server.Dispose();
        }
        base.Dispose(disposing);
    }
}
