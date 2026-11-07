# Windows installation progress

Updated 2026-11-07. The scope is installing and running the existing project on Windows without Docker or WSL, with a small, portable and movable installation. Reviewing the application's architecture or features is outside scope. The installation is **not yet portable**.

## Current implementation

`install-windows.cmd` provisions Python, Calibre, ImageMagick and kepubify in `.cwng-runtime`, installs the project's existing Python dependencies into `.venv` and builds the frontend. Node is obtained locally only when needed to build the frontend. `start-windows.cmd` uses the local runtime without changing the system-wide PATH. An internet connection is required for setup.

The combined setup ran without global Python or Node on PATH; Calibre had been provisioned into the same runtime immediately beforehand. The application started and imported a disposable EPUB using the local Calibre. The test library was configured directly in the disposable application database; it was not a full first-run UI or relocation check.

## Footprint and cleanup

The following are measured on this checkout after cleanup; source files and user data are separate:

| Installed component | Approximate size |
| --- | ---: |
| Calibre | 625 MiB |
| Python runtime | 146 MiB |
| ImageMagick | 61 MiB |
| kepubify | 4 MiB |
| Python virtual environment and application dependencies | 395 MiB |
| Built frontend | 2 MiB |

The virtual environment contains dependencies from the existing project, including approximately 100 MiB for the Google API client, 32 MiB for Selenium and 18 MiB for Sphinx. Their presence does not establish that they can safely be removed; no existing feature or dependency audit was requested.

Removed the locally generated Node runtime, `frontend/node_modules` and stale test/build caches, freeing approximately 228 MiB plus small caches. The built frontend was retained. Setup now removes the Node runtime and frontend dependency directory it creates for a build; it does not remove pre-existing directories as post-build cleanup. npm may refresh existing frontend dependencies when the lockfile changes. A future frontend rebuild may download them again. Historical `.m0` proof outputs and the old bundled installer had already been removed.

## Remaining work

1. Make the application movable. The current `.venv/pyvenv.cfg` and editable installation contain absolute paths; configuration is under `%LOCALAPPDATA%\Calibre-Web-NextGen` and the default ingest folder is under `%USERPROFILE%\CWNG-Ingest`, so moving the project folder does not move all application state. Preserve existing user data when changing this.
2. Keep setup and the retained runtime as small as practical without deleting application dependencies or changing existing features. Confirm temporary build artifacts are cleaned without deleting pre-existing files.
3. Verify one real move and subsequent start/import with disposable data. Do not repeat unrelated feature or architecture investigations.

Earlier M0/M1/M2/M3 experiments are superseded by this scope. Historical records remain in Git history at `3510d9e75` and earlier.
