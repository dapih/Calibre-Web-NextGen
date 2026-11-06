# Windows native preview

The Windows installer is a development preview, not a published release. Run setup as the Windows user who will use CWNG. It installs under `%LOCALAPPDATA%\Programs\CWNG-dapih`; the application keeps its profile under `%LOCALAPPDATA%\CWNG` and uses the library and ingest folders selected during setup. The preview installer is unsigned.

Close CWNG before running setup or uninstalling. Uninstall retains the profile and books. Keep a copy of your library and other important data before maintenance. The preview currently supports only the installer operations shown in its setup wizard; do not use it to downgrade or migrate an existing installation.

To remove retained application settings after uninstall, inspect `%LOCALAPPDATA%\CWNG` in File Explorer and remove only data you no longer want. Your selected library, ingest directory, backups or custom assets may be elsewhere or inside that folder: move or keep anything you need before deleting it. Removing the profile also removes application accounts, settings and progress stored there.

The tray menu provides status, recovery actions, diagnostics and manual update checks. LAN access is opt-in; native HTTPS, OAuth and LDAP are not currently supported.
