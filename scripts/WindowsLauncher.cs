// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;
using System.Windows.Forms;

internal static class WindowsLauncher
{
    internal const string Title = "Calibre-Web NextGen";

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            WindowsInstallation.EnableLongPaths();
            string executable = Application.ExecutablePath;
            string root = Path.GetDirectoryName(executable);
            string prefix = WindowsInstallation.Prefix(root);
            bool openOnStart = Array.IndexOf(args, "--background") < 0;
            Application.EnableVisualStyles();
            using (Mutex maintenance = new Mutex(false, prefix + "-maintenance"))
            using (Mutex instance = new Mutex(false, prefix))
            {
                if (!WindowsInstallation.TryAcquire(maintenance))
                    throw new InvalidOperationException("CWNG maintenance is in progress. Try again when the installer closes.");
                bool owns;
                try
                {
                    string missing = WindowsInstallation.MissingFile(root);
                    if (missing != null)
                        throw new FileNotFoundException("CWNG is missing " + missing +
                            ". Reinstall with cwng-installer.exe or download it from:\r\n" + WindowsInstallation.InstallerUrl);
                    owns = WindowsInstallation.TryAcquire(instance);
                }
                finally { maintenance.ReleaseMutex(); }
                if (!owns)
                {
                    if (!openOnStart) return;
                    for (int i = 0; i < 30; i++)
                    {
                        if (WindowsInstallation.Signal(prefix + "-open")) return;
                        Thread.Sleep(100);
                    }
                    throw new InvalidOperationException("The existing CWNG launcher is still starting.");
                }
                try
                {
                    if (WindowsInstallation.IsRunning(root))
                    {
                        if (!openOnStart) return;
                        string address = Path.Combine(root, @".cwng-data\launcher-url");
                        Uri url = File.Exists(address) ? WindowsInstallation.LoopbackUrl(File.ReadAllText(address)) : null;
                        if (url == null)
                            throw new IOException("CWNG is running from a console. Open the address shown in that console.");
                        WindowsInstallation.OpenBrowser(url);
                        return;
                    }
                    using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                    using (TrayApplication tray = new TrayApplication(root, executable, prefix, identity.User, openOnStart))
                        Application.Run(tray);
                }
                finally { instance.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            Environment.ExitCode = 1;
        }
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
    private readonly WindowsProgress progress;
    private Process server;
    private volatile Uri readyUrl;
    private Uri activeUrl;
    private bool openRequested;

    internal TrayApplication(string root, string executable, string prefix, SecurityIdentifier user, bool openOnStart)
    {
        this.root = root;
        this.prefix = prefix;
        openRequested = openOnStart;
        logPath = Path.Combine(root, @".cwng-data\windows-launcher.log");
        Directory.CreateDirectory(Path.GetDirectoryName(logPath));
        File.WriteAllText(logPath, "");
        progress = new WindowsProgress(WindowsLauncher.Title, false);
        EventWaitHandleSecurity security = new EventWaitHandleSecurity();
        security.AddAccessRule(new EventWaitHandleAccessRule(user, EventWaitHandleRights.FullControl, AccessControlType.Allow));
        open = CreateEvent("open", security);
        restart = CreateEvent("restart", security);
        quit = CreateEvent("quit", security);

        menu = new ContextMenuStrip();
        status = new ToolStripMenuItem { Enabled = false };
        menu.Items.Add(status);
        openMenu = new ToolStripMenuItem("Open CWNG", null, (sender, e) => OpenOrStart());
        restartMenu = new ToolStripMenuItem("Restart", null, (sender, e) => RequestStop(true));
        quitMenu = new ToolStripMenuItem("Quit", null, (sender, e) => RequestStop(false));
        menu.Items.AddRange(new ToolStripMenuItem[] { openMenu, restartMenu, quitMenu });
        icon = Icon.ExtractAssociatedIcon(executable);
        tray = new NotifyIcon { Icon = icon, ContextMenuStrip = menu, Visible = true };
        tray.MouseClick += (sender, e) => { if (e.Button == MouseButtons.Left) OpenOrStart(); };
        timer = new System.Windows.Forms.Timer { Interval = 200 };
        timer.Tick += Tick;
        SetStatus("Starting CWNG", false);
        timer.Start();
    }

    private EventWaitHandle CreateEvent(string action, EventWaitHandleSecurity security)
    {
        bool created;
        return new EventWaitHandle(false, EventResetMode.AutoReset, prefix + "-" + action, out created, security);
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
        using (Mutex maintenance = new Mutex(false, prefix + "-maintenance"))
        {
            if (!WindowsInstallation.TryAcquire(maintenance))
            {
                ExitThread();
                return;
            }
            try
            {
                readyUrl = null;
                activeUrl = null;
                restart.Reset();
                SetStatus("Starting CWNG", false);
                progress.Report("Starting CWNG…");
                var info = WindowsInstallation.Script(root, "windows-start.ps1", "-TrayManaged");
                info.EnvironmentVariables["CWNG_TRAY_CONTROL"] = prefix;
                server = Process.Start(info);
                string ready = "CWNG_TRAY_READY " + prefix + " ";
                server.OutputDataReceived += (sender, e) =>
                {
                    if (e.Data != null && e.Data.StartsWith(ready, StringComparison.Ordinal))
                        readyUrl = WindowsInstallation.LoopbackUrl(e.Data.Substring(ready.Length));
                    else if (readyUrl == null)
                        Log(e.Data);
                };
                server.ErrorDataReceived += (sender, e) => Log(e.Data);
                server.BeginOutputReadLine();
                server.BeginErrorReadLine();
            }
            finally { maintenance.ReleaseMutex(); }
        }
    }

    private bool firstStart = true;

    private void Tick(object sender, EventArgs args)
    {
        try
        {
            if (firstStart)
            {
                firstStart = false;
                StartServer();
            }
            if (open.WaitOne(0)) OpenOrStart();
            if (server == null)
            {
                if (quit.WaitOne(0)) ExitThread();
                return;
            }
            if (server.HasExited)
            {
                server.WaitForExit();
                int code = server.ExitCode;
                server.Dispose();
                server = null;
                if (code == 75)
                {
                    openRequested = true;
                    StartServer();
                }
                else if (code == 0) ExitThread();
                else throw new InvalidOperationException("The CWNG server stopped with exit code " + code + ".");
                return;
            }
            if (activeUrl == null && readyUrl != null)
            {
                activeUrl = readyUrl;
                progress.Hide();
                SetStatus("Running", true);
                if (openRequested) OpenBrowser();
            }
        }
        catch (Exception error) { ShowError(error); }
    }

    private void OpenOrStart()
    {
        try
        {
            if (server == null)
            {
                firstStart = false;
                openRequested = true;
                StartServer();
            }
            else OpenBrowser();
        }
        catch (Exception error) { ShowError(error); }
    }

    private void OpenBrowser()
    {
        openRequested = activeUrl == null;
        if (!openRequested) WindowsInstallation.OpenBrowser(activeUrl);
    }

    private void RequestStop(bool again)
    {
        try
        {
            if (server == null)
            {
                if (again) OpenOrStart();
                else ExitThread();
                return;
            }
            SetStatus(again ? "Restarting" : "Stopping", false);
            progress.Report(again ? "Restarting CWNG…" : "Stopping CWNG…");
            (again ? restart : quit).Set();
        }
        catch (Exception error) { ShowError(error); }
    }

    private void ShowError(Exception error)
    {
        progress.Hide();
        if (server == null)
        {
            activeUrl = null;
            SetStatus("Stopped", false);
        }
        MessageBox.Show(error.Message + "\r\n\r\nLog: " + logPath,
            WindowsLauncher.Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private void SetStatus(string text, bool running)
    {
        status.Text = text;
        tray.Text = WindowsLauncher.Title + " - " + text;
        openMenu.Enabled = restartMenu.Enabled = quitMenu.Enabled = running || text == "Stopped";
    }

    protected override void ExitThreadCore()
    {
        timer.Stop();
        progress.Hide();
        tray.Visible = false;
        base.ExitThreadCore();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            timer.Dispose();
            progress.Dispose();
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
