# Windows implementation progress

Updated 2026-11-07. The current work is a **source-based Windows installation**, not the earlier bundled installer. See [windows-native-specification.md](windows-native-specification.md) for the current scope and [windows-source.md](windows-source.md) for the development instructions. This is not yet a finished Windows release.

## Implemented

- PowerShell setup downloads Python 3.13, Calibre and supporting tools into `.cwng-runtime`, creates a local `.venv`, installs the application and launches it on localhost by default. It does not require a global Python or separate Calibre installation. Configuration is stored under `%LOCALAPPDATA%\Calibre-Web-NextGen`, and the launch script displays the ingest folder.
- Windows uses the existing Tornado server. Windows web actions launch child scripts with the running Python interpreter and supply the configured library path. Metadata changes and ingest use the existing application processors through small Windows workers.
- The ingest worker waits for a configured library, processes stable inputs serially and preserves folder paths and supported sidecars. It stages a copy while holding the input, removes the original only after a successful import, and leaves an `unfinished-ingest.json` marker and retained input for review when an outcome is uncertain.
- The Windows requirements use published project dependencies rather than locally patched wheel versions. The optional Faust acceleration is omitted on Windows and the existing chardet fallback remains available. Windows selects bibtexparser 1.x because the existing Scholar provider fails to import with 2.x. The earlier experimental installer and its user guide were removed, along with five obsolete installer-proof scripts.

## Evidence

- A fresh disposable Windows Python 3.13 virtual environment installed the project from public package indexes. A sandbox-specific pip cache permission failure was resolved by disabling the cache for the verification run; rerunning setup after the bibtexparser correction succeeded. This was not an installation on another machine.
- The source application and background workers started on Windows; the local login endpoint returned HTTP 200. The disposable profile did not yet have a configured library at that point.
- A real Windows import used Calibre and the existing processor. A further nested import with an upload sidecar applied its folder label and consumed the source and sidecar. A failed import retained its source and review marker.
- Real EPUB metadata enforcement ran against a disposable library and updated the book; the metadata watcher dispatch was checked separately. The existing conversion pairing checks passed (six tests).

These are development results, not proof that every workflow or an end-user installation is complete. No separate machine, physical reader or full release candidate was tested in this continuation.

## Open items

- Direct OPDS acquisition uses the Windows ingest-worker status and Windows-compatible private key, capability and publication operations. A real Windows check covered repository records, key creation, capability and book/sidecar publication, ingest-intent verification, and restricted credential-file permissions. Full network acquisition through Calibre import remains unverified. Newznab and external download-client adapters remain unavailable on Windows and are hidden or rejected.
- The source installer builds the SPA using existing or automatically provisioned application-local Node.js; if that build fails, the classic interface or an existing bundle remains available. The updated frontend build passed in a checkout path containing an ampersand. Setup refreshes frontend dependencies when the lockfile changes and reports if a failed build leaves an existing bundle.
- Verify the complete installed workflow and resolve remaining Linux-specific service/status behavior. The current worker's child can outlive an abrupt parent termination; the review marker blocks automatic ingest retry, but does not provide process-tree fencing. Some existing enforcer failures may not produce a nonzero exit code.
- Earlier experimental supervisor, native installer and proof code remains in the branch; remove or simplify it only after confirming the current source path no longer depends on it. No claim is made that this cleanup is complete.

## Historical artifacts

The earlier M0 proof and experimental installer produced extensive temporary `.m0/` build and test copies. Those files and the proof installer were removed at the user's request; historical paths in earlier versions of this record are no longer available. The previous full staged payload used an older dependency baseline and must not be treated as release evidence. Detailed historical records remain in Git history at commit `3510d9e75` and earlier.


## App-local prerequisites — 2026-11-07

Setup now automatically provisions private Python, Calibre, ImageMagick, kepubify and, when needed, Node.js under .cwng-runtime; no global Python, Calibre installation or system PATH change is required. Each downloaded executable is verified before use. Calibre is obtained as the official approximately 200 MB portable installer, not bundled in an oversized application EXE.

A combined setup completed with global Python and Node absent from PATH. Calibre was provisioned separately into the same local runtime beforehand using the official installer; the combined run reused that copy. The application started, served its login page and imported a disposable EPUB into an app-local-Calibre-created test library. The test library path was configured directly in the disposable database rather than through the setup UI. Temporary test data and cache were removed. The retained installed runtime, virtual environment, frontend dependencies and built assets occupied approximately 938, 395, 126 and 2 MiB respectively in this checkout.
