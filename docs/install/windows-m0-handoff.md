# Windows M0 handoff — 2026-11-04

## Resume here

Continue the user's native Windows installer implementation, beginning with M0. Work in `C:\Works\AI works & experiments\Calibre-Web-NextGen\.worktrees\windows-m0-proof`, branch `codex/windows-m0-proof`. Run `git status --short` and `git log -1` first. The root checkout is `main` and has separate, pre-existing uncommitted `.gitignore`, `AGENTS.md` and `docs/agents/` changes; preserve them.

Read [the approved implementation specification](https://github.com/dapih/Calibre-Web-NextGen/blob/bd8cc31f48915f2c346db748d6c1478870257378/docs/install/windows-native-specification.md), especially M0 and gates N1–N6, and [the detailed local proof record](windows-m0-progress.md). The specification is also at `.worktrees/windows-implementation-specification/docs/install/windows-native-specification.md` in the root checkout. The planning map and its decision tickets are closed; that means the plan is approved, not that implementation is complete.

Read the root checkout's `AGENTS.md` and applicable `docs/agents/` instructions. In the session that produced this handoff, the user also supplied repository instructions requiring the `find_skill` MCP search and Context7 CLI for library/tool documentation questions; follow those instructions if still supplied. Before modifying a Python file or symbol, consult the repository's `cwng-impact-map` skill. Keep build requirements and app runtime isolated from global installations, as the user requested.

## Current evidence and limits

- **All N1–N6 are PENDING.** No native installer or release exists. N2–N6 have not been executed.
- The `.m0/` directory in this worktree is ignored and contains the private Python 3.13.14 runtime, downloaded source/compiler archives, private build/test environments, compiler and experimental wheel. It survives a normal shutdown on this PC, but is not included in Git; the scripts can recreate the inputs. Do not assume it exists on another machine.
- Pinned Python and Faust source archive hashes were verified. Experimental build dependencies are listed with hashes in `scripts/windows-m0-faust-build-requirements.txt`. A workspace-local GCC 16.2.0 archive was verified before extraction.
- The Faust wheel builds, and three builds in this same workspace with fixed timestamp settings produced SHA-256 `ce05053b3fb909228ba460dbd78077515124546d4cc39af31b9810d88b83c486`. Cross-machine reproducibility is unproved. The extracted compiler tree is reused and is not independently reverified on each run.
- The raw wheel failed to import until `libgcc_s_seh-1.dll`, `libstdc++-6.dll` and `libwinpthread-1.dll` were copied into the private test package directory. Basic ASCII/UTF-8 checks then passed with poisoned Python environment variables and compiler absent from PATH; loaded DLL paths were verified inside that test directory. This is not a self-contained distributable wheel. DLL redistribution/license obligations, collision risks, wider encoding/metadata tests and complete isolation remain open.
- The public fork and the agreed release policy require incomplete proof/development artifacts to stay internal. Keep these experimental files and binary outputs local; do not push this branch or publish the wheel without a separately agreed public-test policy. The earlier specification and throwaway research/prototype branches were intentionally published as planning artifacts.

## Next work

1. Address the Faust wheel's private DLL closure and redistribution/packaging review; test the resulting artifact without the compiler or global tool paths. Add representative metadata/encoding cases. Independently reproduce the locked build in a clean controlled Windows environment; compare artifact hashes and document the actual toolchain and dependency origins. Reopen the relevant architecture/dependency decision if the approved proof cannot be met.
2. Complete the rest of N1: private Python/package/tool/asset inventory, magic wrapper/DLL/data selection, Calibre/ImageMagick and other tools, frontend/translations/plugin assets, and poisoned-environment origin tracing for all entrypoints/children. A wheel import alone cannot pass N1.
3. Implement and run N2–N6 as specified: per-user/cross-session ownership and private IPC; atomic job containment/crash fencing; all-writer exclusion including child transactions; durable queue/ingest/lifecycle failure handling; Linux/portable regressions and a minimal native book pipeline. Keep release installer/payload commitment blocked until all six groups pass.

For the existing local probe, from the M0 worktree run `./scripts/windows-m0-fetch-mingw.ps1`, then `./scripts/windows-m0-n1.ps1 -BuildFaust -Compiler Mingw`. The scripts download pinned build inputs when missing; the compiler archive is about 108 MB before extraction. Inspect `windows-m0-progress.md` for the evidence and caveats before interpreting results.

## Session boundary

The local proof code is experimental and remains on its own branch. Git commits on that branch can preserve the handoff and source; they are not a backup of ignored `.m0` binaries and do not publish anything. Keep the root checkout's agent-setup changes separate rather than staging everything together. `AGENTS.md` contains lasting repository guidance, so this changing task state is recorded here instead of adding a temporary instruction to it.
