# Windows M0 handoff — 2026-11-06

M0's six native proof groups pass under the agreed local scope. M1 integration is in progress: a configured-profile controller and experimental launcher now run the real web process and scheduler, and first-run setup has an experimental implementation. See the [progress record](windows-m0-progress.md#m1-integration-in-progress--2026-11-06) for evidence and remaining gaps. The [specification's 2026-11-06 review](windows-native-specification.md#review-decisions--2026-11-06) describes the milestones and acceptance coverage. This is not a finished installer or release.

## Scope

Validate the supported per-user installation and ordinary duplicate launches using the available Windows account. Do not require another account, session, machine or VM, or installing or enabling Docker or WSL. The native proof uses disposable libraries and inputs; it does not exercise the user's collection.

The experimental work is in branch `codex/windows-m0-proof`. The local worktree used for the proof was `C:\Works\AI works & experiments\Calibre-Web-NextGen\.worktrees\windows-m0-proof`. The root checkout has separate agent-setup changes; preserve them. Read the repository's applicable agent instructions before continuing.

## Evidence and limits

- [The native specification](windows-native-specification.md) records N1–N6 as PASS for M0 and preserves the later installer and release gates. [The detailed progress record](windows-m0-progress.md) records commands, fixture locations, results and historical failures; its older entries describe their status at the time and are superseded by the final pass sections.
- The real Windows application pipeline passed at `.m0/dfd6429`. It exercised owner-before-web startup, metadata and cover edits, ingest, staged and whole-library conversion, automatic and manual enforcement, EPUB repair, refresh, cancellation, retry, restart and retained inputs. The controller-crash and caller-death proof passed at `.m0/wdb0fab`; lifecycle and recovery checks passed at `.m0/native-helper-ed7e257ed1ec469981e67fbb71ef0601`.
- The default experimental 150-wheel lock includes reproducibly built Faust and locally repaired MIME, OAuth and PyDrive2 wheels. The private runtime, primary tools, staged assets and scoped host settings were checked; N1 does not establish exhaustive optional-feature coverage or release redistribution approval.
- [The writer inventory](windows-m0-writer-inventory.md) describes the supported in-app coordination, child lifetimes, lock order and source-audit blind spots. Synchronous edits use the gate through commit/publication; queued jobs and surviving library-mutating children need durable ownership. Native Calibre database reconstruction remains disabled/deferred; coherent checkpoint restore is separate M3 work. Manual all-book enforcement sends formats outside its verified EPUB/KEPUB scope to review. The M0 ingest dispatcher also rejects split libraries, folder-label ingest and sidecars; these limits need an explicit capability disposition before candidate acceptance. External applications and third-party plugins are outside the per-user coordination boundary.
- Power and device notifications were injected into the actual Windows message loop, and storage loss was simulated. This is not evidence of a physical suspend or unplug. Five additional live-sync replacement tests failed because Windows refused to replace or remove an open SQLite database; their pre-change baseline was not established, and external live synchronization is outside the local M0 storage proof.
- The ignored `.m0/` tree contains the local private runtime, wheelhouse, build tools, fixtures and evidence; it is not included in Git. Recreate the pinned inputs using the checked-in scripts when needed. Do not treat local hashes or traces as a package-wide redistribution or legal review.

## Next work

1. Continue M1 operation-specific reconciliation and normal-launcher lifecycle verification. Never infer a failed transaction from a missing receipt. The current narrow recovery actions and conversion completion marker are documented in the progress record.
2. Continue M2 installed setup/tray, LAN and update integration. Confirm a real Private-profile device, firewall behavior and the GUI; the available host's Public profile only proves denial. Native HTTPS and OAuth login are currently unavailable as described in the specification.
3. Continue M3 with candidate identity and compatibility verification, journal recovery reachable through the installer/bootstrap, migrations, activation, rollback, retention and real uninstall/reset. Current checkpoints and prepared cancellation are internal groundwork; the fixed-target proof EXE is not a product installer. Unsigned stable releases remain allowed.
4. Complete the pending I/R coverage against the installed candidate. Reuse a result across gates and carry forward unaffected evidence with a change-impact explanation. Review the exact payload, provenance, notices and servicing. Physical Kobo/KOReader and unsigned-package policy checks remain required; do not add cross-account or separate-machine requirements, or Docker/WSL setup.

For storage/lifecycle acceptance, inject unavailable/replaced storage and
logoff/suspend notifications through the installed handlers. A physical drive
removal, logoff or sleep is not required. Registered cloud-sync roots are
detectable; users must confirm other sync software because exhaustive detection
is not possible. A new installation has no prior state to checkpoint.

The bootstrap holds an installation-wide lifetime gate. The first-install
preview has uninstall disabled until independent maintenance and recovery join
that gate. Upgrade, repair and reinstall over a retained profile are also pending.

The user explicitly authorized committing and pushing this M0 branch. That authorization does not itself publish an installer, binary wheel or release. The `.m0/` build and evidence outputs remain local and ignored.
