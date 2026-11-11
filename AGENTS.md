# Calibre-Web NextGen for Windows

This fork, `dapih/Calibre-Web-NextGen`, packages the self-hosted library server from `new-usemame/Calibre-Web-NextGen` for native Windows x64. Fork work covers Windows setup, launch, portability, and releases. Preserve upstream application behavior outside the requested change.

## Project guidance

- **Setup, updates, moves, or recovery.** Read the [Windows installation guide](docs/install/windows-source.md). Preserve `.cwng-data/`, the installed `.cwng-runtime/`, pending ingest, and user-selected libraries, including libraries outside the checkout. Use a disposable installation for verification.
- **Windows packaging, upstream sync, or release preparation.** Read the [maintainer instructions](docs/install/windows-source.md#building-and-publishing-windows-releases). Use the existing Windows smoke check for package changes. Ask before expanding into unrelated upstream code or tests.
- **Changes users will notice in the Windows distribution.** Follow the [Windows changelog instructions](changelog.d/README.md#windows-release-fragments).
- **Python changes under `cps/`.** Before changing a Python file, symbol, or Flask route, follow the [impact-map skill](.agents/skills/cwng-impact-map/SKILL.md).

### Issue tracker

Before reading, creating, or updating issues or specs, read `docs/agents/issue-tracker.md`. This repo uses GitHub Issues in `dapih/Calibre-Web-NextGen`.

### Triage labels

Before triaging or applying triage labels, read `docs/agents/triage-labels.md`. Use the five default canonical label names.

### Domain docs

Before exploring domain concepts or proposing architecture changes, read `docs/agents/domain.md`. This repo uses a single-context layout: root `GLOSSARY.md` and `docs/adr/`.
