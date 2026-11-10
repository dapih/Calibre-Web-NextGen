# Windows portable installation specification

Implemented and verified on Windows. The portable setup runs the existing project without Docker or WSL.

- Setup provisions stripped Python, Calibre, and supporting tools locally and refreshes them when pins change. It builds the frontend and keeps the runtime separate from local data.
- `cwng-installer.exe` installs, reinstalls, or removes the adjacent portable app. `CWNG.exe` checks the installed files and provides tray controls for opening, restarting, and quitting CWNG.
- First-run library setup offers a default sibling folder, a custom location, and selection of an existing library. Existing libraries are reused at their selected paths.
- Installed runtime, configuration, and default ingest travel with the app folder. A moved installation reuses its local runtime, and external libraries keep their configured paths.
- Preserve existing data. Legacy Windows profile configuration and ingest require an explicit copy; originals remain and existing local state is never merged or overwritten.
- Remove setup-created build downloads/directories and obsolete proof/installer material. Retain dependencies and source needed by the existing application, plus pre-existing developer directories.
- Keep Linux and container defaults unchanged. Uninstall the portable app by stopping it and removing its folder after preserving local data.

The move check used disposable data, the real launcher, and Calibre import. Windows adapts long library roots with short names or directory junctions and retains real paths in settings. The adapter applies to CWNG's calls to Calibre. The launcher can be built from the source checkout.

See [instructions](windows-source.md), [verification and cleanup](windows-m0-progress.md) and [handoff](windows-m0-handoff.md).
