# Windows portable installation verification

Updated 2026-11-08. The existing application runs from an app-local standalone Python runtime, with configuration and default ingest in `.cwng-data`. No virtual environment, editable source import or global Python/Calibre is needed. See [instructions](windows-source.md).

## Changes

- Install the existing project and dependencies normally into `.cwng-runtime\python`. Launch with isolated Python, explicitly load the adjacent source, and enable UTF-8 for launch and worker output.
- Keep application config, default ingest, conversion temp and Calibre tool configuration inside the installation. Record the old root and update known internal filesystem settings after a move. External library paths remain unchanged.
- Detect legacy Windows profile data before a fresh start. `-ImportLegacyData` explicitly copies it; original files remain and existing local state is never overwritten or merged. A per-install lock prevents simultaneous launches from rebasing or opening the same state.
- Remove setup-created Python build metadata in addition to frontend/Node build dependencies. Preserve pre-existing developer directories.

## Executed checks

The disposable check used the real `windows-start.ps1`, with global Python, Calibre and Node excluded from PATH. It initialized an application database, retained a custom title and a configured internal library/tool path, and left an EPUB pending in the default ingest folder. The runtime and state were moved into an application-only folder with the required source; the previous runtime location was absent. That complete folder was then physically moved to a shorter path containing spaces, `&` and Unicode. Setup was not rerun.

After the move the application served HTTP and the built frontend, retained its title, rebased its internal library and tool paths, and imported the pending EPUB through the existing processor and included Calibre. The original proof library's file hashes were unchanged. Legacy-copy checks also confirmed detection, copied settings/ingest, unchanged original hashes, external-library preservation and refusal to overwrite existing local data. `pip check` reported no broken requirements.

The check exposed and fixed a Unicode output encoding failure. It also demonstrated an existing Calibre limit: a 100-character Windows library path was rejected with a requirement for fewer than 89 characters. The original input and working copy were retained. Moving the stopped installation to a shorter path and clearing the resolved disposable recovery marker allowed the import to finish. Keep this limit in the user instructions.

The updated setup then completed end to end using only the Windows system directories on PATH. It installed the application normally, downloaded local build-only Node, type-checked and rebuilt the frontend, and removed its Python build directory/metadata, Node and frontend dependency directory. No `.venv` remained. PowerShell parsing and Git whitespace checks passed. No broad application test suite was added or rerun.

Windows refused to rename the development worktree root. That is distinct from the application payload: the standalone application folder, runtime and state were physically moved and verified. The test configured its library directly in the disposable application database, not through a full first-run UI walkthrough. This verifies portability and import, not every application feature or a binary release. Disposable test files and data were removed afterward.

## Retained footprint

Measured before the final setup rerun; source and user data are separate:

| Component | Approximate size |
| --- | ---: |
| Python and installed application dependencies | 539 MiB |
| Calibre | 625 MiB |
| ImageMagick | 61 MiB |
| kepubify | 4 MiB |
| Built frontend | 2 MiB |

The dependencies come from the existing project; this work does not audit or remove optional application features. Node and frontend dependencies are build-only and are removed when setup creates them.

## Repository cleanup

The active Windows checkout is `.worktrees/windows-portable`, branch `codex/windows-portable`. Five obsolete worktrees and their local branches were removed. Removed the duplicate comparison snapshot after comparing every source file with newline normalization, the approved disposable Sherlock Holmes library, empty proof directories, obsolete planning drafts and 125 retired installer/proof files. Application-referenced Windows helpers remain.

The root checkout's edits and agent instructions were preserved; the instructions were also brought into the fresh checkout. The only unique old specification edit is retained as a 26 KiB recovery patch at `.git/cleanup-recovery/windows-native-specification.patch` in the primary repository. Prior committed experiments remain recoverable through existing remote branches; no shared history was rewritten.
