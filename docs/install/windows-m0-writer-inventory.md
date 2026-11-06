# M0 native library writer inventory

This working inventory covers the supported per-user Windows proof. It is not
evidence that every application feature is integrated with the native owner.
The static map covers only `cps/*.py` and has query-dependent recall; direct
source inspection is also required for scripts, subprocesses and file writes.
The audit searched `cps/` and `scripts/` for Calibre session commits and
mutations, SQL writes to library tables, file publication/deletion, and child
process launches. It inspected metadata/cover editors, deletion and duplicate
handling, import, metadata autofetch, conversion, repair and maintenance,
reading-state columns and checksums, admin changes and the corresponding script
entrypoints. The static map and text searches cannot prove the absence of
indirect calls, dynamically loaded plugins or undocumented external writers.
Only the supported application and its own children are within this per-user
proof; third-party plugins and other applications editing the library are not.

| Writer or child | Current coordination |
| --- | --- |
| Ingest and its raw Calibre transaction | Native dispatcher claims durable intent, acquires the controller writer gate, and starts a named job; the transaction verifies job membership. The gate is held until descendants are quiescent and verified, or through review. New attempts can emit a job-bound completion receipt only for an unchanged, already-target EPUB with fixer, acquisition and import backup disabled. Offline confirmation additionally verifies the retained input, exact stored bytes and DB markers; all other started cases remain in review. |
| Metadata/cover enforcement | Native scheduler accepts existing application change logs into durable per-book debounce jobs; manual all-library requests use the same controller-owned job and gate. The real proof verifies EPUB and KEPUB titles; formats outside that verified set remain unsupported. |
| Classic and API metadata edits, title/author swap, cover upload/picker | Cooperating metadata lock takes the native gate and checks recovery state before the metadata lock. Classic merge holds it across copies and deletions. API identifier updates and Hardcover review acceptance also acquire it. |
| Whole-book/format deletion and duplicate resolution | Shared delete core acquires the metadata lock; classic merge invokes its already-locked core. Automatic duplicate resolution holds its local nonblocking resolution lock, then the shared writer lock, across merge, database deletion and files-last cleanup. |
| Single-book conversion, metadata backup, KEPUB repair, automatic Hardcover ID | Task paths acquire the shared writer lock. Native conversion runs the child against a private source copy outside the library and only the live lock-holding parent publishes a checked output. If the parent dies, a surviving converter can only touch staging; it cannot publish to the library. Nested metadata export does not reacquire the same lock. |
| Read-status custom columns and checksums | Website, Kobo, KOReader and progress-sync metadata-column writes, read/download checksum registration and KOReader-enable table initialization use the shared metadata lock. Calls already under the inline metadata editor's lock use its existing ownership to avoid nesting. User-only app database state is separate. |
| Database setup and reconnect | Native setup, reconnect and session recovery acquire the writer lock before the reconnect lock; same-execution nested acquisition is supported. The proof starts the owner and ledger before the web app. |
| Whole-library conversion | The native web start/cancel/status actions use the authenticated owner and durable ledger. The scheduler snapshots eligible source formats, revalidates after taking the gate, starts the converter inside a controller job and checks retained sources, database rows and EPUB/KEPUB ZIP integrity. Cancellation asks it to finish its current operation, then forcibly stops it after a bounded wait; partial or interrupted outcomes enter review. This M0 dispatcher supports unsplit libraries and EPUB/KEPUB targets; other targets enter review. |
| EPUB fixer | Native full-library and single-book UI requests, status and cancellation use the durable scheduler and controller-owned job. Each original is retained in a deterministic private job/attempt directory, a same-directory staged copy is fixed and checked, and a live contained worker publishes it atomically. Archive integrity and database sizes are verified before completion. The real single-book UI path passed; full-library multi-book behavior has not been independently demonstrated. Ingest's direct fixer invocation operates on its staged input within the managed job. |
| Library refresh | The native refresh action queues a durable watcher-rescan request and completes it only after the rescan succeeds; it does not start an unmanaged ingest process. |
| Admin Calibre database reconstruction | The `restore_calibre_db` route invokes `calibredb restore_database` on supported non-native deployments. Native mode explicitly refuses it before mutation; reconstruction remains deferred. M3 checkpoint restore is a separate operation and does not require enabling this route. |
| Legacy metadata-change service | A native standalone enforcer cannot write outside a controller job and gate. The native scheduler scans the application's existing JSON change logs into its durable queue instead of launching this service's unmanaged child. Logs are retained. |
| Calibre content server and other external writers | The managed Calibre content server is refused on Windows by `content_server.platform_supported()` and is outside this native proof. Plugin-provided writers and other external applications are outside the supported per-user application scope. |

## Pending integration scope — reviewed 2026-11-06

M1 integrates this existing coordination with the normal launcher; it updates
this inventory for new or changed writers and child launches rather than
repeating the audit from zero. Synchronous edits need no separate ledger job
when the shared gate covers commit/publication and no surviving child can
mutate the library; existing transaction/staging recovery still applies.
Queued/retryable work and raw mutating children keep durable
ownership and outcome reconciliation. Preserve the staging-only conversion
rule above; do not require containment solely to kill an orphan that cannot
publish library changes.

The M0 ingest dispatcher rejects split libraries, folder-label ingest and
sidecars; bulk conversion is limited to unsplit libraries and EPUB/KEPUB
targets, and manual enforcement to its verified EPUB/KEPUB scope. Full-library
multi-book repair also needs integration evidence. Record how each limit is
resolved or exposed before advertising the capability; this inventory does not
make those paths production-ready. New supported mutation paths need gate and
child-lifetime coverage. Third-party writers remain outside this contract.

Use the [specification's evidence rules](windows-native-specification.md#9-confirmed-release-policy-and-evidence-handling)
to reuse unchanged source-audit and fault results. Normal-launcher/installed
integration, changed writer paths and checkpoint maintenance still require
their own affected checks. M3 recovery must maintain exclusive ownership and
restore a coherent payload/state/library set; enabling database reconstruction
is not an M1 or M3 prerequisite.

## Lock order and recovery

For an ordinary cooperating native web writer, acquire the native writer gate,
check for running/review ledger entries while holding it, then acquire the
metadata lock. For a managed worker, the controller acquires the native gate
before recording the running claim and launching its Job Object. The worker
proves membership and acquires the metadata lock when needed; the controller
releases the gate only after the whole job is quiescent and its outcome is
verified. Unresolved outcomes stay in review. A crash releases the operating
system gate, but durable running/review state causes cooperating web writers to
refuse admission until recovery fences the old job and resolves the record.

The duplicate resolver takes its own nonblocking in-process resolution lock
before the shared writer lock. The maintenance ownership lock and child process
lifetimes require special care: do not add a reverse acquisition order or assume
an inherited Windows byte-range lock stays held after its owner dies. These
rules cover cooperating code only. Application startup and reconnect schema
initialization acquire the writer lock before the reconnect lock; a nested
acquisition in the same execution context uses the already-held writer lock.

Impact-map queries for the changes reached the reconciled merge, cover-picker,
API metadata, Hardcover review and conversion/fixer control routes; duplicate
resolution was also reached from scheduled scans and review routes. Exact
local/import and route-reconciled hops are high confidence, whereas class and
attribute guesses are leads only. The queried module blind spots were 445 for
editbooks, 288 for duplicate resolution, 113 for cover-picker, 82 for API edit,
798 for admin, 578 for CWA service controls, 513 for helper and 31 for checksum
management. The snapshot's curated recall was 8/10; it excludes scripts and
frontend and cannot rule out callers missed by dynamic dispatch.
