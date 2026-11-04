# Windows interaction review prototype

**Status: approved by the human on 2026-11-04. Throwaway planning artifact; never merge this branch.**

The [authoritative interaction resolution](https://github.com/dapih/Calibre-Web-NextGen/issues/7#issuecomment-6628686215) records the approval and implementation limits. The exact reviewed HTML is at commit `fcc602c213a2f4bab5fb30333ac2e4d1d14a4ceb`; the later documentation update only records the verdict.

Question: do the approved per-user installer, first-run, tray, update and uninstall behaviors remain understandable when setup or background work fails? This reviews interaction and state transitions, not visual branding or native runtime feasibility.

Open [the self-contained HTML file](windows-native-interaction.prototype.html) in a browser by double-clicking it. It needs no build, server or dependencies. The temporary localhost preview used during review is optional. All state is fictional and in memory; refreshing resets it. No accounts are created, files changed, software installed, external requests made or Windows permissions granted.

Choose a scenario on the left, press **Next**, and inspect the simulated product and the live state panel. Each scenario starts from known sample data. Use **Free play** to try a different order. The sample account fields are read-only; do not enter real credentials. Versions, addresses, paths and book names are examples.

## Review coverage

1. Offline, current-user installation; unsigned distribution disclosure; private runtime; administrator creation; separate library/ingest paths; login startup and LAN initially off; empty new library; close/relaunch existing instance.
2. Interrupted copy import without changing the source or treating a partial destination as ready.
3. Rejected overlapping folders.
4. Occupied port and explicit alternative without adopting or terminating another program.
5. Declined LAN permission and Public-network pause while local use remains available.
6. Missing/replaced external drive without creating a replacement library or consuming queued inputs.
7. Three bounded worker restart attempts, then paused work and diagnostics.
8. Busy Exit with finish-current-book, safe-stop/retry and keep-running choices.
9. Controller crash and relaunch with prior-run verification before recovery.
10. Update disk-space/checkpoint failure, validation failure and covered recovery before normal work resumes.
11. File-changing update requiring a full snapshot; blocked direct downgrade; later restore with explicit consequence confirmation and retained-input reconciliation.
12. Unsigned stable releases, failed package verification and explicit prerelease selection.
13. Uninstall retaining all state and a compatible reinstall.
14. Initially unchecked app-state reset, separate confirmation, preserved book/ingest content and fresh setup without overwriting retained books.
15. Local password recovery preserving books and history while LAN is paused.

Data locations and diagnostics are available from the tray. The proposed private log location is shown explicitly. Explorer actions are simulated. First-run setup can be resumed after its window closes. The first Windows edition's three deferred integrations are disclosed during installation.

## Fixed planning inputs

- [Review the Windows installer, onboarding and tray interaction](https://github.com/dapih/Calibre-Web-NextGen/issues/7)
- [Approved first-run contract](https://github.com/dapih/Calibre-Web-NextGen/issues/4#issuecomment-6622647229)
- [Approved startup and supervision architecture](https://github.com/dapih/Calibre-Web-NextGen/issues/5#issuecomment-6623576805)
- [Approved installer, upgrade and uninstall contract](https://github.com/dapih/Calibre-Web-NextGen/issues/6#issuecomment-6624310208)

## Verification and limits

Agent walkthrough on 2026-11-04 completed all 15 guided scenarios in the browser without console errors. After revisions, focused walkthroughs confirmed empty-library display, resuming incomplete setup, and restoring only actual remaining ingest inputs before queue reconciliation. Data-location/log controls were also checked. Inline JavaScript passed a syntax check. No test suite or production code was added.

The reducer is a conceptual interaction model. Its counts, simulated jobs, restart budget, copy completion, hashes, checkpoint success, safe stops and recovery verification stand in for work the implementation must actually prove. It neither implements nor validates Windows process ownership, ACLs, paths, NTFS/cloud detection, credentials, firewall rules, real ingest, migrations, backup consistency or recovery. Opening the HTML is not an installation acceptance test.

The human approved the running artifact without requesting revisions; the ticket is resolved. Native proof and clean-machine/offline acceptance remain future release gates. The final implementation specification must carry the accepted interaction and pending evidence requirements forward.
