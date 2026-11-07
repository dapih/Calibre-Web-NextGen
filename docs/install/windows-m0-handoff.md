# Windows source-install handoff

Updated 2026-11-07. Continue the simpler [Windows source installation](windows-source.md), using the existing Python application with Python and Calibre installed automatically into the application directory. The [current specification](windows-native-specification.md) replaces the previous M0–M5 bundled-installer plan. Keep the work focused: no new installer framework, bundled Calibre, large proof environments or unnecessary checks.

The worktree is `C:\Works\AI works & experiments\Calibre-Web-NextGen\.worktrees\windows-m0-proof`, on branch `codex/windows-m0-proof`. The root checkout and `original-repo-just-for-comparison` are separate; preserve their changes and data. The prior `.m0/` proof and build directory was removed, so old commands referencing it are not directly runnable.

## Current implementation

- `scripts/windows-install.ps1` provisions Python, Calibre and required tools under `.cwng-runtime`, creates `.venv` and installs the source project. `scripts/windows-start.ps1` sets per-user configuration and ingest locations, uses those local tools and launches `scripts/windows-run.py`.
- The runner starts the ordinary web application and the Windows ingest and metadata workers. They reuse the existing ingest processor, watcher and cover enforcer. Windows web actions use the active Python interpreter and the currently configured library.
- Startup, a real import, nested sidecar/folder-label import and EPUB metadata enforcement have been exercised with disposable Windows data. The combined setup subsequently ran without global Python or Node on PATH, started the app and imported an EPUB using app-local Calibre. Calibre was independently provisioned into the local runtime before that combined setup run; the test configured its disposable library directly in the database. See the [progress record](windows-m0-progress.md) for the precise scope.
- On an uncertain ingest outcome, the source and a review marker remain. After a forced stop, the child process may outlive its parent: ensure it has finished before resolving the marker or restarting. User libraries and configuration must never be removed as a cleanup shortcut.

## Next steps

1. Complete a real direct OPDS acquisition through download and Calibre import on Windows. Key creation, credential-file permissions, capability publication and ingest-intent verification have passed; external download-client integrations remain unavailable on Windows.
2. Verify the source installation and user-facing interface. Setup uses an existing or app-local Node.js to build the SPA; otherwise the classic interface is the fallback.
3. Exercise a normal source installation and representative user workflows without preinstalled Python or Calibre. Check Windows-specific service/status or manual actions only where they affect those workflows.
4. Remove obsolete experimental supervisor and proof code incrementally as the simpler path replaces it. Keep useful Windows compatibility fixes and preserve the container behavior. Run only meaningful affected checks.

The user previously authorized committing and pushing the work branch; that does not authorize publishing a release or binary artifact. The current source path is still in progress and must not be described as a complete Windows release.
