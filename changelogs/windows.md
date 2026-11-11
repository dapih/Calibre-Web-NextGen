# CWNG for Windows release history

The Windows fork publishes its own installer and application ZIP. The version
in each tag identifies the upstream application. The `rN` suffix identifies
the Windows packaging revision for that application version.

See [pending Windows changes](../changelog.d/windows/) and the
[upstream application changelog](../CHANGELOG.md).

## [cwng-windows-v4.1.66-r3](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/cwng-windows-v4.1.66-r3) · 2026-11-11

### Changed

- **The Windows ZIP includes a Windows-only README.** It explains how to install from the ZIP, open the tray launcher, and update or remove the app. The package omits the Dockerfile, container setup guides, and development files. Windows setup reads its tool versions from a bundled manifest.

## [cwng-windows-v4.1.66-r2](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/cwng-windows-v4.1.66-r2) · 2026-11-11

- The release title is **CWNG for Windows v4.1.66-r2**. The downloads are
  `cwng-windows-v4.1.66-r2-installer.exe` and `cwng-windows-v4.1.66-r2.zip`.
- The versioned installer can find releases tagged `windows-v…` and `cwng-windows-v…`.
  Users with the old `cwng-installer.exe` need to download the versioned installer once.
- This release predates the Windows-only ZIP and README.

## [windows-v4.1.66-r1](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/windows-v4.1.66-r1) · 2026-11-11

- Updated the application to upstream CWNG v4.1.66. The release workflow
  tested a reinstall from the preceding Windows release before publication.

## [windows-v4.1.62-r3](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/windows-v4.1.62-r3) · 2026-11-10

- The installer retries interrupted GitHub downloads using Windows PowerShell
  and verifies the payload against its SHA-256 before using it.

## [windows-v4.1.62-r2](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/windows-v4.1.62-r2) · 2026-11-10

- Separate installer and ZIP downloads. The installer manages installation,
  reinstallation, and removal. The installed `CWNG.exe` opens the app and
  supplies the tray controls.
- The first-run library setup panels use the dark theme.

## [windows-v4.1.62](https://github.com/dapih/Calibre-Web-NextGen/releases/tag/windows-v4.1.62) · 2026-11-10

- The original combined launcher sets up Python, Calibre, and supporting tools.
  The installation can move with its settings and default ingest folder.
  Explicit legacy data copying preserves existing data.
- First-run browser sign-in reaches library setup. Deep Windows library
  folders use short paths for Calibre while CWNG saves the real paths.
- Restart waits for Windows background work and the instance lock before
  starting the app again.
