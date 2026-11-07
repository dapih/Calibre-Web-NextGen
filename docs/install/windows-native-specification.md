# Windows portable installation specification

Status: **in progress**, 2026-11-07. The goal is to install and run the existing project on Windows without Docker or WSL, with the smallest practical installation. Evaluating, redesigning or changing the existing application's architecture and features is outside this work.

## Scope

- Provide a straightforward Windows setup and start method. Setup obtains the required Python runtime, Calibre and supporting tools locally; users do not install them globally or configure a system-wide PATH. Use existing project dependencies and workflows.
- The installation must be **portable and movable**: moving the application folder must not leave the Python runtime or application configuration tied to its previous location. Configuration and the default ingest location need to travel with the application. User-selected external libraries remain the user's data and must never be moved or deleted automatically.
- Keep the installed footprint as small as practical without stripping dependencies or functionality that the existing application needs. Build-only downloads and temporary files created by setup should not remain afterward; do not remove pre-existing developer directories as cleanup.
- Keep Linux/container behavior unchanged. Do not introduce a separate installer framework, unnecessary checks or unrelated feature changes.

## Current state and next work

The source-based setup downloads app-local Python, Calibre and tools and has run a real disposable EPUB import. It is **not yet portable**: `.venv` stores absolute paths, and application configuration and the default ingest folder are stored under the Windows user profile. Moving the application folder alone does not move those files or guarantee a working launch.

The next implementation step is to make the installed runtime and its application-owned state move with the application folder, while preserving any existing configuration, libraries and ingest data. After that, verify one actual move-and-run scenario and document installation, update and removal. Do not add broad test matrices or evaluate unrelated application features.

See [windows-source.md](windows-source.md) for current instructions and [windows-m0-progress.md](windows-m0-progress.md) and [windows-m0-handoff.md](windows-m0-handoff.md) for evidence and handoff.
