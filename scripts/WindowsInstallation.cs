// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class WindowsInstallation
{
    internal const string InstallerUrl = "https://github.com/dapih/Calibre-Web-NextGen/releases";
    internal const string InstalledReceipt = ".cwng-installed";
    internal const string UpdateHelper = @"scripts\cwng-updater.exe";

    internal static readonly string[] RequiredFiles = {
        "VERSION", @"cps\main.py", @"scripts\windows-start.ps1", @"scripts\windows-run.py",
        @"scripts\windows_tray_control.py", @".cwng-runtime\python\python.exe",
        @".cwng-runtime\calibre\calibredb.exe", @".cwng-runtime\imagemagick\CORE_RL_MagickWand_.dll",
        @".cwng-runtime\kepubify\kepubify.exe", @"cps\static\app\index.html"
    };

    internal static void EnableLongPaths()
    {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
    }

    internal static string MissingFile(string root)
    {
        return Array.Find(RequiredFiles, file => !File.Exists(Path.Combine(root, file)));
    }

    internal static string Prefix(string root)
    {
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
        using (SHA256 hash = SHA256.Create())
        {
            byte[] key = Encoding.UTF8.GetBytes(identity.User.Value + "|" + root.ToUpperInvariant());
            return @"Local\CWNG-" + BitConverter.ToString(hash.ComputeHash(key), 0, 16).Replace("-", "");
        }
    }

    internal static bool TryAcquire(Mutex mutex)
    {
        try { return mutex.WaitOne(0); }
        catch (AbandonedMutexException) { return true; }
    }

    internal static bool Signal(string name)
    {
        EventWaitHandle signal;
        if (!EventWaitHandle.TryOpenExisting(name, out signal)) return false;
        using (signal) signal.Set();
        return true;
    }

    internal static bool IsRunning(string root)
    {
        using (Mutex instance = new Mutex(false, Prefix(root)))
        {
            if (!TryAcquire(instance)) return true;
            instance.ReleaseMutex();
        }
        try
        {
            using (FileStream file = File.Open(Path.Combine(root, @".cwng-data\running.lock"),
                                              FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite))
            {
                file.Lock(0, 1);
                file.Unlock(0, 1);
            }
        }
        catch (FileNotFoundException) { }
        catch (DirectoryNotFoundException) { }
        catch (IOException) { return true; }
        return false;
    }

    internal static Uri LoopbackUrl(string value)
    {
        Uri url;
        return Uri.TryCreate(value.Trim(), UriKind.Absolute, out url) && url.Host == "127.0.0.1" &&
               (url.Scheme == "http" || url.Scheme == "https") ? url : null;
    }

    internal static void OpenBrowser(Uri url)
    {
        Process.Start(new ProcessStartInfo(url.AbsoluteUri) { UseShellExecute = true });
    }

    internal static ProcessStartInfo Script(string root, string name, string arguments)
    {
        return new ProcessStartInfo {
            FileName = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"),
            Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" +
                        Path.Combine(root, "scripts", name) + "\" " + arguments,
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
    }
}

internal sealed class WindowsProgress : Form
{
    private readonly Label status;
    private bool completed;

    internal WindowsProgress(string title, bool showInTaskbar)
    {
        Text = title;
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        ControlBox = false;
        ShowInTaskbar = showInTaskbar;
        ClientSize = new Size(420, 110);
        status = new Label { Left = 20, Top = 16, Width = 380, Height = 32 };
        Controls.Add(status);
        Controls.Add(new ProgressBar { Left = 20, Top = 53, Width = 380, Height = 14,
                                      Style = ProgressBarStyle.Marquee });
        Controls.Add(new Label { Left = 20, Top = 78, Width = 380, Text = "Please wait…" });
    }

    internal void Report(string text)
    {
        SetMessage(text);
        if (!Visible) Show();
    }

    internal void SetMessage(string text)
    {
        status.Text = text;
    }

    internal void Finish()
    {
        completed = true;
        Close();
    }

    protected override void OnFormClosing(FormClosingEventArgs args)
    {
        if (!completed && args.CloseReason == CloseReason.UserClosing) args.Cancel = true;
        base.OnFormClosing(args);
    }
}
