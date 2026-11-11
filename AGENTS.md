# Calibre-Web NextGen for Windows

This fork, `dapih/Calibre-Web-NextGen`, packages the self-hosted library server from `new-usemame/Calibre-Web-NextGen` for native Windows x64. Fork work covers Windows setup, launch, portability, and releases. Preserve upstream application behavior outside the requested change.

## Project guidance

- **Setup, updates, moves, or recovery.** Read the [Windows installation guide](docs/install/windows-source.md). Preserve `.cwng-data/`, the installed `.cwng-runtime/`, pending ingest, and user-selected libraries, including libraries outside the checkout. Use a disposable installation for verification. Treat `.cwng-data/`, `.cwng-runtime/`, and user-selected library folders as local installation data when cleaning or publishing the repository.
- **Windows packaging, upstream sync, or release preparation.** Read the [maintainer instructions](docs/install/windows-source.md#building-and-publishing-windows-releases). The Windows smoke check is optional for updates to `main` and runs as part of the Windows release workflow. Ask before expanding into unrelated upstream code or tests.
- **Changes users will notice in the Windows distribution.** Follow the [Windows changelog instructions](changelog.d/README.md#windows-release-fragments).
- **Python changes under `cps/`.** Before changing a Python file, symbol, or Flask route, follow the [impact-map skill](.agents/skills/cwng-impact-map/SKILL.md).

### Small docs-only changes

Keep small docs-only changes local. Edit the file and check the diff. Skip builds and all tests, including the Windows smoke check. Open or merge a PR only if the user explicitly asks.

### Keep the primary checkout current

The fork's `main` accepts direct fast-forward pushes. Push or merge only when authorized. After updating the fork's `main`, fetch and fast-forward the primary checkout unless the user requested a different revision. Preserve local instructions, ignore edits, staged and unstaged changes, and untracked or ignored files. Verify the primary checkout's HEAD against the fork's live `main` before reporting the update complete. If a safe fast-forward is blocked, report the specific conflict and which checkout is behind.

### Scope and release verification

Keep Windows fork work focused on the requested change. Ask before expanding into unrelated upstream fixes, tests, or workflow changes. Use checks that exercise the behavior being changed and stop once the relevant evidence is in hand.

Treat each release as a separate decision. Docs-only, small, and non-critical changes in a local worktree or branch carry no release approval. Ask the user before starting a release build, creating a tag or draft, or publishing, unless the user has already authorized that specific release.

For releases, inspect the actual ZIP contents and README before publishing. Verify the uploaded names and checksum on GitHub and use the Latest endpoint to confirm the current release. Use the pending Windows changelog for the notes and archive it after publication. Follow the small documentation rule for an archive-only change.

### Issue tracker

Before reading, creating, or updating issues or specs, read `docs/agents/issue-tracker.md`. This repo uses GitHub Issues in `dapih/Calibre-Web-NextGen`.

### Triage labels

Before triaging or applying triage labels, read `docs/agents/triage-labels.md`. Use the five default canonical label names.

### Domain docs

Before exploring domain concepts or proposing architecture changes, read `docs/agents/domain.md`. This repo uses a single-context layout: root `GLOSSARY.md` and `docs/adr/`.
