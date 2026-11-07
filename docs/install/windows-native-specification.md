# Windows installation specification

Status: **source installation in progress**, 2026-11-07. This document supersedes the earlier M0–M5 plan and its bundled offline installer, tray and release-gate requirements. Historical experiments and their evidence are available in Git history; they are not the implementation contract.

## Goal and scope

Add a Windows installation method for the existing Python application without Docker or WSL. Reuse the existing web application, dependencies, ingest processor and metadata enforcement. Keep Windows-specific code limited to what is needed for those workflows. A self-contained EXE, bundled Python or Calibre, native tray, generic recovery framework and custom local dependency wheels are not prerequisites.

The current development target is 64-bit Windows with Python 3.13 and Calibre installed separately. Python dependencies come from the project's published requirements. ImageMagick and kepubify are separate tools for features that use them. The source installer creates a local `.venv`; it does not modify the global Python installation.

## Expected behavior

- `scripts/windows-install.ps1` installs the application into the source checkout's virtual environment. `scripts/windows-start.ps1` launches the application and its background workers.
- The existing web interface opens locally, uses its normal first-run configuration, and lets the user configure a Calibre library. The start script listens on localhost by default; network access is an explicit option. Users must change the existing default administrator password before exposing it to a network.
- Stable books placed in the displayed ingest folder are passed to the existing processor, including supported nested paths and sidecars. Ingest and metadata enforcement run serially with respect to one another. The configured library is read from the application settings.
- Preserve user files on failure. A failed or interrupted ingest remains for review instead of being retried automatically when its outcome is uncertain. Normal uninstall or removal of the source checkout must not delete the user's library, ingest folder or configuration.
- Reuse the current application and container behavior; Windows-specific dependency and path changes must not alter Linux defaults. Optional integrations must not be represented as working until their Windows path is actually available.

## Remaining work

- Complete and verify the source installation on a normal Windows machine with the stated prerequisites, including first-run library setup and the usual application workflows.
- Verify direct OPDS Book Sources acquisition through download and Calibre import on Windows. Other download-client integrations remain unavailable on Windows; do not present them as working until supported.
- The source installer can build the newer frontend when Node.js is present and otherwise uses the classic interface. Confirm update and removal instructions without deleting user data.
- Resolve remaining Linux-specific service/status behavior where it affects actual Windows use. Continue simplifying or removing obsolete experimental code when it is no longer needed by the source path.

## Verification

Run only checks that exercise behavior changed or needed for a working installation: an actual install/startup, a representative book import and metadata-enforcement operation, and relevant checks for affected shared code. Use disposable data, retain user data on failure, and record what was and was not exercised. A previous result is not proof for a changed path, and repeated test matrices or artificial failure cases are not required.

The working instructions and current limitations are in [windows-source.md](windows-source.md); evidence and the next steps are in [windows-m0-progress.md](windows-m0-progress.md) and [windows-m0-handoff.md](windows-m0-handoff.md).
