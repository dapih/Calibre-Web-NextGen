"""M0 single-writer coordination; not the complete native controller.

The caller must supply a private, ACL-protected state directory and verified
private executable paths/environments. Previous running work blocks admission:
old-generation fencing and authenticated IPC are deliberately not assumed here.
"""

from pathlib import Path
import importlib.util
import os
import threading
import uuid

from windows_m0_job import Job, ProfileLock, generation_exists
from windows_m0_ledger import Ledger


class CoordinatorBusy(RuntimeError):
    """A writer is active or the previous run still needs recovery."""


def require_no_maintenance_journal(state_dir):
    """Refuse normal ownership when maintenance state exists or is unreadable."""
    try:
        os.stat(Path(state_dir) / 'maintenance-journal.json', follow_symlinks=False)
    except FileNotFoundError:
        return
    raise CoordinatorBusy('maintenance recovery is required before startup')


class Coordinator:
    """Own one profile and retain a writer job until its descendants exit."""

    def __init__(self, helper_path, state_dir):
        self._helper_path = Path(helper_path)
        self._state_dir = Path(state_dir)
        if not self._state_dir.is_absolute() or not self._state_dir.is_dir():
            raise ValueError("state_dir must be an existing private directory")
        self._guard = threading.RLock()
        self._owner = ProfileLock(self._helper_path, self._state_dir / "controller.lock")
        try:
            # Maintenance uses this same profile lock while recording its
            # journal. Check before opening or creating the ledger so racing a
            # newly journaled update cannot mutate the data it protects.
            require_no_maintenance_journal(self._state_dir)
            self.ledger = Ledger(self._state_dir / "runtime-ledger.sqlite")
        except BaseException:
            self._owner.close()
            raise
        self.generation = 'nj1-' + uuid.uuid4().hex
        self._active = None
        self._gate_fd = None
        self._gate_api = None
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise RuntimeError("coordinator is closed")

    @property
    def state_dir(self):
        return self._state_dir

    def _check_recovery(self):
        # Do not infer that an old process is gone from a PID or from lock release.
        if self.ledger.list_jobs("review") or (self._active is None and self.ledger.list_jobs("running")):
            raise CoordinatorBusy("previous work requires fencing and reconciliation")

    def enqueue(self, dedup_key, operation, intent):
        with self._guard:
            self._check_open()
            self._check_recovery()
            return self.ledger.enqueue(dedup_key, operation, intent)

    def _acquire_writer_gate(self, directory):
        directory = Path(directory).resolve(strict=True)
        if not directory.is_dir() or not directory.drive or directory.drive.startswith("\\\\"):
            raise ValueError("writer gate requires an existing local directory")
        path = Path(__file__).resolve().parents[1] / "cps" / "services" / "file_lock.py"
        spec = importlib.util.spec_from_file_location("_cwng_controller_file_lock", path)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        if api.msvcrt is None:
            raise RuntimeError("native writer locking is unavailable")
        fd = api.open_lock(str(directory / ".cwng-native-writer.lock"))
        try:
            if not api.acquire(fd, blocking=False):
                raise CoordinatorBusy("another application writer is active")
        except BaseException:
            os.close(fd)
            raise
        self._gate_api, self._gate_fd = api, fd
        return directory

    def _release_writer_gate(self):
        if self._gate_fd is not None:
            fd, api = self._gate_fd, self._gate_api
            api.release(fd)
            os.close(fd)
            self._gate_fd = self._gate_api = None

    def start(self, job_id, executable, arguments, *, cwd, environment, writer_lock_dir=None,
              allow_ungated_fixture=False, preflight=None):
        """Commit running ownership before creating the contained worker.

        A gate is mandatory for application writers. The explicit exception is
        for control or disposable fixture jobs that do not write the library.
        """
        with self._guard:
            self._check_open()
            self._check_recovery()
            if self._active is not None:
                raise CoordinatorBusy("another writer is still active")
            if not isinstance(allow_ungated_fixture, bool):
                raise TypeError("allow_ungated_fixture must be a bool")
            if writer_lock_dir is None and not allow_ungated_fixture:
                raise ValueError("a writer gate is required unless explicitly running a non-writer fixture")
            if writer_lock_dir is not None and allow_ungated_fixture:
                raise ValueError("a gated writer cannot be marked as an ungated fixture")
            gate_dir = self._acquire_writer_gate(writer_lock_dir) if writer_lock_dir is not None else None
            try:
                if preflight is not None and (not callable(preflight) or preflight() is not True):
                    raise ValueError('writer preflight failed')
                self.ledger.start(job_id, self.generation)
            except BaseException:
                self._release_writer_gate()
                raise
            job = None
            try:
                job = Job(self._helper_path, generation=self.generation)
                # Native writer checks bind descendants to this generation.
                # Remove case variants before adding authoritative values.
                managed = {key: value for key, value in environment.items()
                           if key.upper() not in {"CWNG_NATIVE_MODE", "CWNG_NATIVE_JOB_GENERATION", "CWNG_NATIVE_HELPER",
                                                  "CWNG_NATIVE_WRITER_GATE", "CWNG_NATIVE_GATE_DIR", "CWNG_NATIVE_STATE_DIR"}}
                managed.update({"CWNG_NATIVE_MODE": "1", "CWNG_NATIVE_JOB_GENERATION": self.generation,
                                "CWNG_NATIVE_HELPER": str(self._helper_path.resolve()),
                                "CWNG_NATIVE_STATE_DIR": str(self._state_dir.resolve())})
                if gate_dir is not None:
                    managed.update({"CWNG_NATIVE_WRITER_GATE": "1", "CWNG_NATIVE_GATE_DIR": str(gate_dir)})
                process = job.spawn(executable, arguments, cwd=cwd, environment=managed)
            except BaseException:
                if job is not None:
                    job.close()
                # Leave the durable record running: next admission is fenced
                # until the caller can establish quiescence and reconcile it.
                raise
            self._active = {"id": job_id, "job": job, "process": process, "finalized": False}
            return process.pid

    def fence_interrupted(self):
        """Move old intents to review only after their named jobs disappear.

        This never terminates an object by name or retries an ambiguous write.
        Profile ownership and private state-directory ACLs are prerequisites.
        """
        with self._guard:
            self._check_open()
            if self._active is not None:
                raise CoordinatorBusy("a writer is still active")
            running = self.ledger.list_jobs("running")
            for record in running:
                try:
                    present = generation_exists(self._helper_path, record["generation"])
                except (ValueError, OSError) as error:
                    raise CoordinatorBusy("old generation cannot be verified") from error
                if present:
                    raise CoordinatorBusy("old generation still has a job object")
            count = self.ledger.review_interrupted() if running else 0
            if not self.ledger.list_jobs("review"):
                self._release_writer_gate()
            return count

    def serve_control_once(self, pipe):
        """Serve authenticated local status and durable conversion requests."""
        def handle(request):
            with self._guard:
                self._check_open()
                if request == {"operation": "status"}:
                    return {
                        "active": self._active is not None,
                        "running": len(self.ledger.list_jobs("running")),
                        "review": len(self.ledger.list_jobs("review")),
                    }
                if request == {"operation": "convert-library"}:
                    return {"job_id": self.ledger.request_conversion()}
                if request == {"operation": "cancel-convert-library"}:
                    return {"cancelled": self.ledger.cancel_conversion()}
                if request == {"operation": "conversion-status"}:
                    return {"job": self.ledger.conversion_status()}
                if request == {"operation": "refresh-ingest"}:
                    return {"job_id": self.ledger.enqueue('refresh:' + uuid.uuid4().hex, 'refresh-ingest', {})}
                if request == {"operation": "manual-enforce"}:
                    return {"job_id": self.ledger.request_manual_enforcement()}
                if request == {"operation": "cancel-manual-enforce"}:
                    return {"cancelled": self.ledger.cancel_manual_enforcement()}
                if request == {"operation": "manual-enforce-status"}:
                    return {"job": self.ledger.manual_enforcement_status()}
                if set(request) == {"operation", "book_id"} and request['operation'] == 'epub-fixer':
                    try:
                        return {"job_id": self.ledger.request_epub_fixer(request['book_id'])}
                    except ValueError:
                        return {"error": "invalid book id"}
                if request == {"operation": "cancel-epub-fixer"}:
                    return {"cancelled": self.ledger.cancel_epub_fixer()}
                if request == {"operation": "epub-fixer-status"}:
                    return {"job": self.ledger.epub_fixer_status()}
                if set(request) == {"operation", "job_id"} and request['operation'] == 'job-status':
                    if not isinstance(request['job_id'], str) or len(request['job_id']) > 64:
                        return {"error": "invalid job id"}
                    job = self.ledger.get(request['job_id'])
                    return {"job": ({key: job[key] for key in ('id', 'state', 'evidence')} if job else None)}
                return {"error": "unsupported operation"}
        pipe.serve_one(handle)

    def reconcile(self, job_id, *, outcome, evidence, verify):
        """Resolve reviewed intent only after a trusted caller verifies state.

        A retry requires proof that retrying is safe; a completed outcome
        requires proof of the committed result. This method does not infer
        either condition from a process exit or a user-supplied label.
        """
        with self._guard:
            self._check_open()
            if self._active is not None or self.ledger.list_jobs("running"):
                raise CoordinatorBusy("running work must be fenced before reconciliation")
            record = self.ledger.get(job_id)
            if record is None or record["state"] != "review":
                raise CoordinatorBusy("requested work is not awaiting review")
            if outcome not in ("retry", "completed"):
                raise ValueError("unknown reconciliation outcome")
            if not callable(verify) or verify(record, outcome) is not True:
                raise CoordinatorBusy("reconciliation could not be verified")
            self.ledger.reconcile(job_id, outcome=outcome, evidence=evidence)
            if not self.ledger.list_jobs("review"):
                self._release_writer_gate()

    def finish(self, job_id, *, evidence, verify=None):
        """Finalize after quiescence and independent outcome verification.

        Missing, failing or negative verification retains the durable intent for
        review. The verifier runs while the coordinator holds writer admission.
        """
        with self._guard:
            self._check_open()
            active = self._active
            if active is None or active["id"] != job_id:
                raise CoordinatorBusy("the requested writer is not active")
            if not active["finalized"]:
                code = active["process"].wait(0)
                if code is None or active["job"].active_count() != 0:
                    raise CoordinatorBusy("writer or descendant is still active")
                if code == 0:
                    try:
                        verified = verify is not None and verify() is True
                    except Exception:
                        verified = False
                    if verified:
                        self.ledger.complete(job_id, self.generation, evidence)
                    else:
                        self.ledger.require_review(job_id, self.generation, "worker exited; outcome not verified")
                else:
                    self.ledger.require_review(job_id, self.generation, f"worker exited {code}: {evidence}")
                active["finalized"] = True
            active["process"].close()
            active["job"].close()
            self._active = None
            if not self.ledger.list_jobs("review"):
                self._release_writer_gate()

    def stop_current(self, job_id):
        """Stop a contained job; leave its intent running until fenced and reviewed."""
        with self._guard:
            self._check_open()
            active = self._active
            if active is None or active['id'] != job_id:
                raise CoordinatorBusy('the requested writer is not active')
            # Closing the last job handle requests tree termination. A leaked
            # handle can keep it alive, so recovery still has to fence the name.
            active['job'].close()
            active['process'].close()
            self._active = None

    def close(self):
        with self._guard:
            if self._closed:
                return
            if self._active is not None:
                # A cancelled/aborted run remains running in the ledger. A
                # later controller must fence and reconcile before retrying.
                self._active["job"].close()
                self._active["process"].close()
                self._active = None
            self._release_writer_gate()
            self._owner.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
