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

### First-run browser redirect correction

A browser navigation with `Accept: text/html` exposed a cycle before library setup: `/login` → `/app/` → `/admin/dbconfig` → `/login`. Generic HTTP requests with `Accept: */*` returned 200 and missed the failure. The login route now keeps an unconfigured installation on the existing classic login/setup flow; configured installations retain the new UI default. The regression failed before the fix, then passed with the existing login/UI-preference and OAuth route checks (80 passed). Browser-style live HTTP checks cover the full first-run login-to-database-configuration chain.

Before this Python change, the impact map was refreshed against `4f2134175` and cps tree `3a98fbda6a1b43d051e4282954a75d821438ea74`. `/login` reaches `web.login` with high-confidence `route_reconciled` evidence, and the handler reaches SPA preference and URL helpers with high-confidence `exact_import_module_attribute` links. Guessed callers from unrelated mail/client classes were not treated as actual login consumers. The module has 784 blind spots, chiefly unknown receivers (551), imported attributes (88), ambiguous names (75) and guessed receivers (38). The map covers Python only; templates and frontend are absent. Its measured recall spans roughly 12.5–80% depending on the target; the refreshed curated set was 8/10, with the frontend Shelf case absent and an instance-method library-filter case unresolved. The real HTTP chain and source inspection supply the evidence for this fix. Generated map/check artifacts are disposable and are not committed.

Windows refused to rename the development worktree root. That is distinct from the application payload: the standalone application folder, runtime and state were physically moved and verified. The test configured its library directly in the disposable application database, not through a full first-run UI walkthrough. This verifies portability and import, not every application feature or a binary release. Disposable test files and data were removed afterward.

### Long library path correction

The user requested a fix without drive letters. Calibre's installed tweak settings expose no library-root length option; its database code applies fixed limits and reserves room for generated book paths. The fork's shared Calibre addressing policy now uses a short Windows directory junction for long local library roots. App config and UI paths stay real, vendor binaries stay unchanged, and Linux paths and remote content-server URLs retain their behavior. Content-server addressing uses the same ID that Calibre derives from the alias. Aliases are generated cache entries in `%LOCALAPPDATA%\CWNG-Paths`, not copied books. A new creation helper supports empty libraries in deep folders and refuses existing/nonempty destinations. Upstream application comparisons used `original-repo-read-only/`, without modifying it.

The original failure was reproduced with a 164-character library root. The fix then created a new library at a 177-character root, imported an EPUB using the real `calibre_ingest_transaction.py` helper, opened a stored book with a 325-character physical path, exported it, and reopened the library after a physical move with unchanged book hashes. A conflicting alias-cache entry was rejected without overwrite. After a graceful app restart, the live library API returned the existing book and the browser route returned HTTP 200; the real saved library path and all four existing associated-file hashes were unchanged. The six added command-boundary regression checks passed. Of 97 related existing checks, Windows had 19 failures and one skip; comparison against the unchanged implementation reproduced the same 19 failures (POSIX path assertions and unsupported Windows content-server ownership), with no added failures. These unrelated baseline limitations were not expanded into this task.

The pre-change impact map was refreshed at `f21163639` / cps tree `dd917470148e6adcd4f4bf2e2ec0be3f535d4f24`. High-confidence `exact_import_symbol` links identify metadata export, conversion and managed-server consumers of the shared addressing policy; `exact_local_symbol` links server startup to argument construction. The policy module has 33 unresolved calls (17 unknown receivers, 7 callbacks, 5 unbound names, 4 ambiguous attributes); the server module has 64 (33 unknown receivers, 18 receiver guesses, 7 ambiguities, 6 callbacks). Python-only coverage omits templates/frontend; measured recall ranges roughly 12.5–80%, and the refreshed curated result remained 8/10 with the same two misses recorded above. Standalone scripts and vendor execution were inspected and verified directly.

## Windows lifecycle verification

The real admin Restart control returned the app, but originally dropped `-I -X utf8` and bypassed the Windows worker/lock cleanup path. Restart now returns to the launcher only after both background workers join and the portable context releases `running.lock`; PowerShell relaunches with the original interpreter options and listen address. The one-time legacy-copy flag is consumed before any restart. The focused cleanup-order test failed on the original implementation and all three cases passed after the fix, including shutdown and nonzero failure propagation. Real authenticated checks confirmed an old PID exited, exactly one replacement PID belonged to the same PowerShell launcher, its command retained `-I -X utf8`, and the existing book was served. Shutdown stopped HTTP and released the instance lock.

The pre-change impact refresh checked `ff3a6052b` / cps tree `c0d9945e77517397c81157c577f521acf0b02a4b`. `cps.__init__` constructs `WebServer` through a high-confidence `exact_import_symbol` edge; the class reaches content-server exit cleanup through `exact_import_module_attribute`. Its 64 unresolved calls comprise 51 unknown receivers, 6 receiver guesses, 5 dynamic names, 1 ambiguous attribute and 1 callback. The map names no live route reaching the class, so the admin endpoint and standalone launcher were inspected directly. The map omits frontend/templates, has a roughly 12.5–80% recall range, and the refreshed curated result remains 8/10 with the same two misses listed above.

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
