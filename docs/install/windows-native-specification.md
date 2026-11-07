# Windows portable installation specification

Status: implemented and relocation checked, 2026-11-08. Run the existing project on Windows without Docker or WSL, with the smallest practical source installation. Existing application architecture and features are outside this work.

- Setup obtains Python, Calibre and supporting tools locally; no global installation or system PATH change is required.
- Installed runtime, configuration and default ingest travel with the application folder. Moving a stopped installation does not require setup. User-selected external libraries keep their existing locations.
- Preserve existing data. Legacy Windows profile configuration and ingest require an explicit copy; originals remain and existing local state is never merged or overwritten.
- Remove setup-created build downloads/directories and obsolete proof/installer material. Retain dependencies and source needed by the existing application, plus pre-existing developer directories.
- Keep Linux/container defaults unchanged. No bundled installer framework, unrelated feature changes or broad test matrix.

The move check uses disposable data and the real launcher/Calibre import. The Windows integration adapts long library roots through short directory junctions while retaining real paths in settings. The bundled Calibre is unchanged; direct vendor-program launches and ordinary filesystem limits remain outside this adapter. This is a portable source installation, not a published binary release or validation of every application feature.

See [instructions](windows-source.md), [verification and cleanup](windows-m0-progress.md) and [handoff](windows-m0-handoff.md).
