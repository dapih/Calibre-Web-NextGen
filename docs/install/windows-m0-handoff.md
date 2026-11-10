# Windows installation handoff

Updated 2026-11-10. The fork runs the existing project on Windows from a portable installation without Docker or WSL. The [Windows guide](windows-source.md) describes source setup and the buildable `CWNG.exe` launcher.

## Current state

- `install-windows.cmd` provisions stripped standalone Python, Calibre, and supporting tools locally. It installs existing dependencies, builds the frontend, and cleans its own build dependencies. Setup stages replacement tools when their pins change.
- `start-windows.cmd` uses that runtime with isolated imports and UTF-8. Configuration, default ingest, temp, and Calibre tool settings travel in `.cwng-data`. CWNG adjusts known internal paths after a move. External libraries, including the default sibling `calibre-library`, retain their saved paths.
- First-time sign-in opens **Set up your Calibre library**. Create a library at the default or a custom location, or select an existing library. Either creation choice uses an existing `metadata.db` at its destination. A failed mount of a previously configured library opens troubleshooting.
- `CWNG.exe` can provision a fresh `CWNG` folder and launch it in the tray. The tray offers **Open CWNG**, **Restart**, and **Quit**. Admin restart and shutdown are also available. Restart waits for Windows workers and the instance lock to finish before relaunching.
- `start-windows.cmd -ImportLegacyData` explicitly copies old profile config/ingest, retaining originals and refusing to overwrite local state. A normal fresh launch reports existing legacy data instead of silently starting a separate account database.
- Long Windows library roots use a short Windows name when available, with directory junctions as a fallback. Saved paths stay real. The `create-windows-library.cmd` helper uses the same sibling default as the web setup page.
- To uninstall, stop CWNG and remove its folder after backing up local state and any library stored inside it. See [uninstall and optional cache cleanup](windows-source.md#uninstall-on-windows).

Disposable Windows checks covered tool pin refresh, the launcher and its tray controls, first-run library creation and reuse, relocation, and EPUB ingest. The [verification record](windows-m0-progress.md) has the test scope and results.
