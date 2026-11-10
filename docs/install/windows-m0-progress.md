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

The reviewed Windows work was fast-forwarded into root `main`. With the app stopped and its lock released, `.cwng-runtime`, `.cwng-data`, the existing `library` and `cps/static/app` were moved from `.worktrees/windows-portable` into the root checkout. Frozen manifests matched all 33 state files, 5 library files and 7 frontend files before startup. After rebasing internal paths, all settings, both accounts, the existing book record and all associated-file hashes were preserved. Real admin Restart and Shutdown passed again from `main`: a single replacement process retained its launcher and interpreter options, and shutdown released the lock. The obsolete worktree and local branch were removed. Git encountered long source paths during removal; 1,204 residual source files were compared with `main` before deleting the remainder, alongside disposable bytecode. The old library junction was removed as a junction entry only, without traversing its target.

The earlier cleanup removed five obsolete worktrees and their local branches, the duplicate comparison snapshot after comparing every source file with newline normalization, the approved disposable Sherlock Holmes library, empty proof directories, obsolete planning drafts and 125 retired installer/proof files. Application-referenced Windows helpers remain. The newly supplied `original-repo-read-only/` folder was left untouched.

The root checkout's ignore-rule edit was preserved; its matching agent instructions are now tracked in `main`. The only unique old specification edit is retained as a 26 KiB recovery patch at `.git/cleanup-recovery/windows-native-specification.patch`. Prior committed experiments remain recoverable through existing remote branches; no shared history was rewritten.

## Stripped Python and tool refresh verification

On 2026-11-10, a disposable copy of the tracked source installed with only the Windows system directories on PATH. Setup downloaded and verified the stripped CPython 3.13.14 archive and the native tools, passed `pip check`, built the frontend and removed its build-only Node files. The runtime measured about 1,147 MiB, including 457 MiB for Python and its dependencies. The Python tree contained no `.pdb` files.

The disposable server accepted a library configured through the authenticated Database Configuration form. Setup rejected a concurrent update while the server held its lock. After shutdown, the folder moved to a path containing spaces, an ampersand and a non-ASCII character. The launcher adjusted the library path, served the built frontend and imported a pending copy of the *Pride and Prejudice* EPUB. The original fixture hash matched after import.

Changing the disposable Dockerfile pin from kepubify v4.0.4 to v4.0.3 first failed with a missing Windows checksum. With the checksum present, setup downloaded and ran v4.0.3, then restored v4.0.4 when the pin changed back. The Python and Calibre receipts and disposable data hashes matched throughout. A separate staging check covered reuse, a simulated extraction failure and recovery after an interrupted swap. A deep-path library creation probe hit Calibre's `.calnotes` error with the same Calibre binaries as the existing installation. The passing relocation check used a shorter library path.

## Direct short paths and the Windows launcher

A disposable 178-character library reproduced Calibre's `.calnotes` failure. Both the Python and Windows command implementations of directory junctions reproduced error 183 when creating a child directory on this machine. Creating the same child at the real path succeeded. Preferring a verified Windows short name let Calibre create a database and notes in a 184-character library, and the combined launcher test created and imported into a 241-character library. The library path stored in app settings stays the real path.

The impact map refreshed from `8f00912e2` and cps tree `bf6b680127c7943fd5ada2643178a1f134f5c6b5`. It identifies `calibredb_command` and `library_id` as exact local callers and `content_server.server_arguments` as an exact import caller. The 51 reconciled route paths include database configuration and conversion. The downstream `encode` match is an attribute-name guess. Source inspection confirms the call encodes the junction cache key. The module has 43 unresolved calls, led by 25 unknown receivers and 8 calls through local values or parameters. The map covers only Python under `cps`. Recall ranges from about 12.5% to 80%, with 8 of 10 curated cases matched on this refresh. The standalone creation helper and background ingest were verified directly.

The disposable `CWNG.exe` installed from an empty folder, built the frontend, and started on loopback. A second background launch reused the instance. Sending the same local Windows events that the tray uses restarted the server and later stopped it. After importing a disposable EPUB, the installed folder moved to a path with spaces, an ampersand and a non-ASCII character. Starting the relocated copy with the final launcher rebased the library setting and retained the book. The desktop inspection tool exposed regular windows, so verification exercised tray controls at their event boundary.

## First-run library setup

Disposable Windows runs used the bundled Calibre and separate app databases. They covered default creation, custom creation with missing parents and an apostrophe in the path, reuse through either creation option, selection by `metadata.db`, and both picker filters. Existing UUIDs and sentinel files survived reuse. Missing, corrupt and nonempty destinations produced targeted errors, and removing a previously configured database showed the troubleshooting page. Twenty targeted tests passed.

The refreshed map for `8f00912e2` reports both database configuration routes as `route_reconciled`. Their handlers reach `_db_configuration_update_helper` via `exact_local_symbol`. The helper reaches `_db_simulate_change` and `_db_configuration_result` via `exact_local_symbol`, and the result reaches `render_title_template` via `exact_import_symbol`. At startup, `cps/__init__.py` reaches `ConfigSQL` and `CalibreDB` via `exact_import_module_attribute`. `ConfigSQL` reaches binary autodetection via `exact_local_symbol`. `CalibreDB` reaches `_register_sqlite_udfs` via `exact_local_symbol` and `metadata_db_write_lock` via `exact_import_symbol`.

The map reports 798 unresolved calls in `admin.py` (516 unknown receivers, 132 ambiguous attributes, 90 imported module attributes), 183 in `config_sql.py` (157 unknown receivers, 16 guessed attributes), and 582 in `db.py` (496 unknown receivers, 33 folded class members). Examples from the baseline include `cps/admin.py:3065` (`to_save.get`), `cps/config_sql.py:395` (`log.info`) and `cps/db.py:1333` (`connection.execute`). The map covers only Python under `cps`. Historical recall ranges from about 12.5% to 80%, and the fresh run matched 8 of 10 curated cases. The misses were a frontend Shelf consumer and an instance call to `public_shelf_book_filter`.

The sibling default uses the same path helper for the setup page, its POST handler and the CLI. A second refresh confirmed the two `route_reconciled` routes and their `exact_local_symbol` calls into the update helper, plus its `exact_local_symbol` calls to the first-run guard and result renderer. The renderer reaches `render_title_template` via `exact_import_symbol`. The updated admin module has 801 blind spots, led by 517 unknown receivers, 133 ambiguous attributes and 90 imported module attributes. Examples include `to_save.get` and the subsequent `.strip()` at `cps/admin.py:3081`. The shared helper and Jinja are outside the map. The Python-only recall limits and the two curated misses are unchanged.

The sibling location passed disposable first-run creation and reuse, and the CLI created the same layout. Moving only the app folder preserved its connection to the sibling library at the saved path. All 20 targeted checks passed. The [uninstall guide](windows-source.md#uninstall-on-windows) records how to preserve local data and libraries during removal. A disposable junction check confirmed that deleting the junction entry left the target library and its files intact.
