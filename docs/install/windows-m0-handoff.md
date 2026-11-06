# Windows M0 handoff — 2026-11-06

M0's six native proof groups pass under the agreed local scope. The next implementation milestone is M1: integrate the proven components into the normal application launcher and complete shared supervision, scheduling and lifecycle controls. M0 is a disposable proof, not a finished installer or a release.

## Scope

Validate the supported per-user installation and ordinary duplicate launches using the available Windows account. Do not require another account, session, machine or VM, or installing or enabling Docker or WSL. The native proof uses disposable libraries and inputs; it does not exercise the user's collection.

The experimental work is in branch `codex/windows-m0-proof`. The local worktree used for the proof was `C:\Works\AI works & experiments\Calibre-Web-NextGen\.worktrees\windows-m0-proof`. The root checkout has separate agent-setup changes; preserve them. Read the repository's applicable agent instructions before continuing.

## Evidence and limits

- [The native specification](windows-native-specification.md) records N1–N6 as PASS for M0 and preserves the later installer and release gates. [The detailed progress record](windows-m0-progress.md) records commands, fixture locations, results and historical failures; its older entries describe their status at the time and are superseded by the final pass sections.
- The real Windows application pipeline passed at `.m0/dfd6429`. It exercised owner-before-web startup, metadata and cover edits, ingest, staged and whole-library conversion, automatic and manual enforcement, EPUB repair, refresh, cancellation, retry, restart and retained inputs. The controller-crash and caller-death proof passed at `.m0/wdb0fab`; lifecycle and recovery checks passed at `.m0/native-helper-ed7e257ed1ec469981e67fbb71ef0601`.
- The default experimental 150-wheel lock includes reproducibly built Faust and locally repaired MIME, OAuth and PyDrive2 wheels. The private runtime, primary tools, staged assets and scoped host settings were checked; N1 does not establish exhaustive optional-feature coverage or release redistribution approval.
- [The writer inventory](windows-m0-writer-inventory.md) describes the supported in-app coordination, child lifetimes, lock order and source-audit blind spots. Native database reconstruction is explicitly unavailable pending coordinated maintenance and recovery. Manual all-book enforcement sends formats outside its verified EPUB/KEPUB scope to review. External applications and third-party plugins are outside the per-user coordination boundary.
- Power and device notifications were injected into the actual Windows message loop, and storage loss was simulated. This is not evidence of a physical suspend or unplug. Five additional live-sync replacement tests failed because Windows refused to replace or remove an open SQLite database; their pre-change baseline was not established, and external live synchronization is outside the local M0 storage proof.
- The ignored `.m0/` tree contains the local private runtime, wheelhouse, build tools, fixtures and evidence; it is not included in Git. Recreate the pinned inputs using the checked-in scripts when needed. Do not treat local hashes or traces as a package-wide redistribution or legal review.

## Next work

1. Implement M1 and preserve or rerun the M0 evidence when affected. Complete normal launcher integration and application-wide lifecycle and recovery controls.
2. Implement safe native maintenance and database recovery only with the required quiescence and coherent checkpoints. Finish the installer, setup and release acceptance milestones in the specification.
3. Review the exact release payload, optional features, provenance, notices and servicing before publication. Do not add cross-account or separate-machine validation requirements, or Docker/WSL setup.

The user explicitly authorized committing and pushing this M0 branch. That authorization does not itself publish an installer, binary wheel or release. The `.m0/` build and evidence outputs remain local and ignored.
