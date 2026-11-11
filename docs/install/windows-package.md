# CWNG for Windows

Calibre-Web NextGen runs on Windows x64. Setup needs internet access to download the tools and dependencies into the app folder.

## Install and open CWNG

Download the asset ending in `-installer.exe` from the [release page](https://github.com/dapih/Calibre-Web-NextGen/releases) to install the app into a `CWNG` folder beside the installer. To install manually, extract the ZIP into a new, writable folder named `CWNG`, run `install-windows.cmd`, then open `CWNG.exe`.

On a new installation, sign in with `admin` and `admin123`, then change the password. Create a library or select an existing Calibre library. Put files you want to import in `.cwng-data\ingest` after choosing a library.

To reopen the app in your browser, choose **Open CWNG** from the system tray. Wait for imports and conversions to finish before using **Restart** or **Quit**.

## Update or remove CWNG

If CWNG notifies you about an update, choose **Update to v…** from the tray menu. CWNG verifies the download and restarts with your current settings and library.

Run the Windows installer from the folder that contains `CWNG` and choose **Reinstall** or **Uninstall**. During reinstall, choose to retain `.cwng-data` to keep your accounts, settings, reading progress, and pending imports. Keep a copy of any library you want to retain.

For details on updating, moving an installation, or recovering an interrupted import, see the [Windows installation guide](https://github.com/dapih/Calibre-Web-NextGen/blob/main/docs/install/windows-source.md).
