# Windows installation handoff

Updated 2026-11-08. The goal is the smallest practical **portable and movable Windows source installation** of the existing project, without Docker or WSL. Existing application architecture and features remain outside scope.

The current checkout and installation are the repository root on `main`. The reviewed Windows branch was fast-forwarded into `main`; its runtime, configuration, default ingest, existing `library` and built frontend were moved into this checkout. The obsolete worktree and local branch were removed after verification. The pre-existing local ignore-rule edit and `original-repo-read-only/` comparison folder were retained.

## Current state

- `install-windows.cmd` provisions standalone Python, Calibre and required tools locally, installs existing dependencies without an editable install, and builds the frontend. Setup cleans its own build dependencies.
- `start-windows.cmd` uses that runtime with isolated imports and UTF-8. Configuration, default ingest, temp and Calibre tool settings live under `.cwng-data` and travel with the folder. Known internal config paths are updated after a move; external library paths remain external.
- `start-windows.cmd -ImportLegacyData` explicitly copies old profile config/ingest, retaining originals and refusing to overwrite local state. A normal fresh launch reports existing legacy data instead of silently starting a separate account database.
- Admin **Restart** now drains Windows workers and releases the instance lock before the launcher starts a replacement with `-I -X utf8`. Admin **Shutdown** stops the server and releases the lock. Both controls passed real authenticated checks before and after the move to `main`.
- A real moved-folder start and pending EPUB import passed with disposable data, the built frontend and included Calibre. Legacy data preservation and external-library paths were checked. Long Windows library roots now use directory-junction aliases at Calibre's command boundary; no drive letters are assigned and saved paths remain real. `create-windows-library.cmd` creates an empty library directly in a deep folder. See the [verification record](windows-m0-progress.md).
- Obsolete worktrees, local branches, duplicate comparison source and retired proof/installer material were cleaned up. The unique old specification edit has a small recovery patch in the primary repository's Git metadata.

The requested portability work is complete. Follow [windows-source.md](windows-source.md) for installation, copying legacy data, moving, updating, recovery and removal. The check was not a complete first-run UI or all-feature acceptance test. A binary release or publication remains a separate action.
