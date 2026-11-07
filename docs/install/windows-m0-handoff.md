# Windows installation handoff

Updated 2026-11-07. The user's goal is the **smallest practical portable and movable Windows installation** of the existing project, without Docker or WSL. Do not evaluate or redesign existing application architecture or features. The [specification](windows-native-specification.md), [progress record](windows-m0-progress.md) and [current instructions](windows-source.md) describe the installation scope and its limits.

The worktree is `C:\Works\AI works & experiments\Calibre-Web-NextGen\.worktrees\windows-m0-proof` on branch `codex/windows-m0-proof`. The root checkout and `original-repo-just-for-comparison` are separate; preserve their changes and user data.

## Current state

- `install-windows.cmd` provisions Python, Calibre and required tools locally and builds the frontend; `start-windows.cmd` starts the existing application with those local tools. No global Python, Calibre or system PATH change is required.
- Setup and an EPUB import ran with disposable data and local Calibre. The test configured its library directly in the temporary application database, not through the first-run UI.
- Build-only Node and `frontend/node_modules` created by this work were removed after the frontend build; the built frontend remains. Setup now removes build dependencies it creates when finished; pre-existing directories are not removed as post-build cleanup.
- **The installation cannot yet be treated as movable.** `.venv/pyvenv.cfg` and the editable package record contain the original checkout path. Configuration and the default ingest folder are in the Windows user profile, outside the application folder.

## Next step

Make the runtime and application-owned state move with the application folder, without overwriting or deleting an existing user's configuration, library or ingest data. Keep any existing user-selected external libraries external. Then run one meaningful move-and-start/import check with disposable data and update the user instructions. Avoid unrelated feature changes, redundant checks, large proof environments or bundled installer frameworks.

The current installed components and the measured footprint are recorded in [windows-m0-progress.md](windows-m0-progress.md). A previous authorization to push the work branch does not authorize publishing a release or binary artifact.
