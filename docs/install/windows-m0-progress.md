# Windows implementation progress

Updated 2026-11-07. The current work is a **source-based Windows installation**, not the earlier bundled installer. See [windows-native-specification.md](windows-native-specification.md) for the current scope and [windows-source.md](windows-source.md) for the development instructions. This is not yet a finished Windows release.

## Implemented

- PowerShell scripts create a Python 3.13 virtual environment, install the application and launch it on localhost by default. Python and Calibre are installed separately; the source path does not bundle them. Configuration is stored under `%LOCALAPPDATA%\Calibre-Web-NextGen`, and the launch script displays the ingest folder.
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

- Direct OPDS acquisition uses the Windows ingest-worker status and Windows-compatible private key, capability and publication operations. A real Windows check covered repository records, key creation, capability and book/sidecar publication, ingest-intent verification, and restricted credential-file permissions. Full network download through Calibre import remains unverified because Calibre is not installed on this host. Newznab and external download-client adapters remain unavailable on Windows and are hidden or rejected.
- The source installer builds the SPA when Node.js and npm are available; otherwise the classic interface remains available. The updated frontend build passed in a checkout path containing an ampersand. Setup refreshes frontend dependencies when the lockfile changes and reports if a failed build leaves an existing bundle.
- Verify the complete installed workflow and resolve remaining Linux-specific service/status behavior. The current worker's child can outlive an abrupt parent termination; the review marker blocks automatic ingest retry, but does not provide process-tree fencing. Some existing enforcer failures may not produce a nonzero exit code.
- Earlier experimental supervisor, native installer and proof code remains in the branch; remove or simplify it only after confirming the current source path no longer depends on it. No claim is made that this cleanup is complete.

## Historical artifacts

The earlier M0 proof and experimental installer produced extensive temporary `.m0/` build and test copies. Those files and the proof installer were removed at the user's request; historical paths in earlier versions of this record are no longer available. The previous full staged payload used an older dependency baseline and must not be treated as release evidence. Detailed historical records remain in Git history at commit `3510d9e75` and earlier.
