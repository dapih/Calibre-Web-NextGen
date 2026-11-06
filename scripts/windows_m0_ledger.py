"""M0 controller-owned durable intent, without automatic replay or file deletion.

The controller must establish exclusive profile ownership, fence older worker
generations and reconcile actual library/filesystem state before admitting work.
This ledger is not a transaction across the Calibre database and book files.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


_APPLICATION_ID = 0x43574E47  # CWNG
_SCHEMA_VERSION = 1


class LedgerStateError(RuntimeError):
    """The requested transition is not valid for the current durable state."""


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be a non-empty string without NUL")
    return value


class Ledger:
    """Persist intent and conservative job transitions in one private database.

    The containing state directory must already exist and have the controller's
    private access controls. Deduplication keys identify a logical request for the
    lifetime of this ledger; a different future request needs a different key.
    """

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or not self.path.parent.is_dir():
            raise ValueError("ledger requires an absolute path in an existing private directory")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            created = False
        else:
            os.close(fd)
            created = True
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            if created:
                connection.execute("PRAGMA synchronous=FULL")
                if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise LedgerStateError("durable rollback journal is unavailable")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                connection.execute("""CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL UNIQUE,
                    operation TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'review', 'completed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    generation TEXT,
                    evidence TEXT,
                    created_ns INTEGER NOT NULL,
                    updated_ns INTEGER NOT NULL
                )""")
                connection.execute("COMMIT")
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID or
                  connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION):
                raise LedgerStateError("unknown or incomplete ledger; refusing to modify it")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID or
                    connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION):
                raise LedgerStateError("ledger identity changed")
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise LedgerStateError("unexpected journal mode")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def enqueue(self, dedup_key, operation, intent):
        """Commit intent before returning its ID; conflicting duplicates fail."""
        _required_text(dedup_key, "deduplication key")
        _required_text(operation, "operation")
        if not isinstance(intent, dict):
            raise TypeError("intent must be a dictionary")
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        now, job_id = time.time_ns(), uuid.uuid4().hex
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT id, operation, intent FROM jobs WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
            if existing:
                if existing["operation"] != operation or existing["intent"] != encoded:
                    raise LedgerStateError("deduplication key already belongs to different intent")
                return existing["id"]
            connection.execute(
                "INSERT INTO jobs (id, dedup_key, operation, intent, state, created_ns, updated_ns) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (job_id, dedup_key, operation, encoded, now, now),
            )
        return job_id

    def complete_refresh(self, job_id):
        """Complete a read-only refresh only after its watcher scan succeeded."""
        _required_text(job_id, 'job ID')
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='completed',evidence=?,updated_ns=? "
                "WHERE id=? AND operation='refresh-ingest' AND state='pending'",
                ('ingest directory scanned', time.time_ns(), job_id),
            )
            if cursor.rowcount != 1:
                raise LedgerStateError('refresh is not pending')

    def bind_profile(self, name, value):
        """Persist a profile identity once; a changed identity fails closed."""
        _required_text(name, 'binding name')
        if not isinstance(value, dict):
            raise TypeError('profile binding must be a dictionary')
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS profile_bindings (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            previous = connection.execute('SELECT value FROM profile_bindings WHERE name=?', (name,)).fetchone()
            if previous is None:
                connection.execute('INSERT INTO profile_bindings (name,value) VALUES (?,?)', (name,encoded))
            elif previous['value'] != encoded:
                raise LedgerStateError('profile storage identity changed: ' + name)

    def restart_state(self, role, *, failure=False, retryable=True, now_ns=None, reset=False):
        """Persist a role's bounded restart history across controller launches."""
        _required_text(role, 'role')
        now_ns = time.time_ns() if now_ns is None else now_ns
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS restart_budget '
                               '(role TEXT PRIMARY KEY, attempts TEXT NOT NULL, paused INTEGER NOT NULL)')
            row = connection.execute('SELECT attempts,paused FROM restart_budget WHERE role=?', (role,)).fetchone()
            attempts = json.loads(row['attempts']) if row else []
            paused = bool(row['paused']) if row else False
            delay = None
            if reset:
                attempts, paused = [], False
            elif failure and not paused:
                attempts = [stamp for stamp in attempts if now_ns < stamp or now_ns - stamp < 300_000_000_000]
                if not retryable or len(attempts) >= 3:
                    paused = True
                else:
                    delay = (2, 10, 30)[len(attempts)]
                    attempts.append(now_ns)
            connection.execute('INSERT OR REPLACE INTO restart_budget (role,attempts,paused) VALUES (?,?,?)',
                               (role, json.dumps(attempts), int(paused)))
            return paused, delay

    def debounce_metadata(self, book_id, due_ns):
        """Persist and coalesce a book's pending quiet-period notification."""
        if (not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0 or
                not isinstance(due_ns, int) or isinstance(due_ns, bool) or due_ns <= 0):
            raise ValueError('invalid metadata notification')
        with self._transaction() as connection:
            return self._debounce_metadata(connection, book_id, due_ns)

    @staticmethod
    def _debounce_metadata(connection, book_id, due_ns):
        intent = json.dumps({'book_id': book_id, 'due_ns': due_ns}, sort_keys=True, separators=(',', ':'))
        now = time.time_ns()
        rows = connection.execute(
            "SELECT id,intent FROM jobs WHERE operation='metadata-debounce' AND state='pending'"
        ).fetchall()
        matches = [row for row in rows if json.loads(row['intent']).get('book_id') == book_id]
        if len(matches) > 1:
            raise LedgerStateError('duplicate metadata debounce records')
        if matches:
            row = matches[0]
            if due_ns > json.loads(row['intent'])['due_ns']:
                connection.execute('UPDATE jobs SET intent=?,updated_ns=? WHERE id=?', (intent, now, row['id']))
            return row['id']
        job_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO jobs (id,dedup_key,operation,intent,state,created_ns,updated_ns) "
            "VALUES (?,?,'metadata-debounce',?,'pending',?,?)",
            (job_id, 'metadata:' + job_id, intent, now, now),
        )
        return job_id

    def admit_metadata_event(self, event_key, book_id, due_ns):
        """Atomically record a source event and enqueue its debounced work once."""
        _required_text(event_key, 'metadata event key')
        if (not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0 or
                not isinstance(due_ns, int) or isinstance(due_ns, bool) or due_ns <= 0):
            raise ValueError('invalid metadata notification')
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS metadata_events (event_key TEXT PRIMARY KEY)')
            inserted = connection.execute('INSERT OR IGNORE INTO metadata_events (event_key) VALUES (?)',
                                          (event_key,)).rowcount
            if not inserted:
                return None
            return self._debounce_metadata(connection, book_id, due_ns)

    def promote_metadata(self, job_id, observed_intent, enforcement_intent):
        """Promote only if no newer notification changed the pending deadline."""
        old = json.dumps(observed_intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        new = json.dumps(enforcement_intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE jobs SET operation='enforce',intent=?,updated_ns=? WHERE id=? "
                "AND operation='metadata-debounce' AND state='pending' AND intent=?",
                (new, time.time_ns(), job_id, old),
            )
            return result.rowcount == 1

    def prepare_conversion(self, job_id, intent):
        """Replace an unprepared conversion request with its verified snapshot."""
        encoded = json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE jobs SET operation='convert-library',intent=?,updated_ns=? WHERE id=? "
                "AND operation='convert-library-request' AND state='pending' AND intent='{}'",
                (encoded, time.time_ns(), job_id),
            )
            return result.rowcount == 1

    def cancel_pending(self, job_id):
        """Finish a conversion request that has not begun writing."""
        _required_text(job_id, 'job ID')
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='completed',evidence='cancelled before launch',updated_ns=? "
                "WHERE id=? AND operation IN ('convert-library-request','convert-library') AND state='pending'",
                (time.time_ns(), job_id),
            )
            return cursor.rowcount == 1

    def request_conversion(self):
        """Persist one outstanding whole-library conversion request."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT id FROM jobs WHERE operation IN ('convert-library-request','convert-library') "
                "AND state IN ('pending','running') ORDER BY created_ns LIMIT 1"
            ).fetchone()
            if row:
                return row['id']
            job_id, now = uuid.uuid4().hex, time.time_ns()
            connection.execute(
                "INSERT INTO jobs (id,dedup_key,operation,intent,state,created_ns,updated_ns) "
                "VALUES (?,?,'convert-library-request','{}','pending',?,?)",
                (job_id, 'convert-library:' + job_id, now, now),
            )
            return job_id

    def request_manual_enforcement(self):
        with self._transaction() as connection:
            row = connection.execute("SELECT id FROM jobs WHERE operation IN "
                                     "('manual-enforce-request','manual-enforce') "
                                     "AND state IN ('pending','running') ORDER BY created_ns LIMIT 1").fetchone()
            if row:
                return row['id']
            job_id, now = uuid.uuid4().hex, time.time_ns()
            connection.execute("INSERT INTO jobs (id,dedup_key,operation,intent,state,created_ns,updated_ns) "
                               "VALUES (?,?,'manual-enforce-request','{}','pending',?,?)",
                               (job_id, 'manual-enforce:' + job_id, now, now))
            return job_id

    def prepare_manual_enforcement(self, job_id, intent):
        encoded = json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        with self._transaction() as connection:
            cursor = connection.execute("UPDATE jobs SET operation='manual-enforce',intent=?,updated_ns=? "
                                        "WHERE id=? AND operation='manual-enforce-request' AND state='pending'",
                                        (encoded, time.time_ns(), job_id))
            return cursor.rowcount == 1

    def cancel_manual_enforcement(self):
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            rows = connection.execute("SELECT id,state FROM jobs WHERE operation IN "
                                      "('manual-enforce-request','manual-enforce') AND state IN ('pending','running')").fetchall()
            for row in rows:
                if row['state'] == 'pending':
                    connection.execute("UPDATE jobs SET state='completed',evidence='cancelled before launch',updated_ns=? "
                                       "WHERE id=? AND state='pending'", (time.time_ns(), row['id']))
                else:
                    connection.execute('INSERT OR IGNORE INTO cancel_requests (job_id) VALUES (?)', (row['id'],))
            return len(rows)

    def manual_enforcement_status(self):
        with self._transaction() as connection:
            row = connection.execute("SELECT id,state,evidence FROM jobs WHERE operation IN "
                                     "('manual-enforce-request','manual-enforce') "
                                     "ORDER BY created_ns DESC,id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def cancel_conversion(self):
        """Cancel pending conversion or durably request interruption of a running one."""
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            rows = connection.execute(
                "SELECT id,state FROM jobs WHERE operation IN ('convert-library-request','convert-library') "
                "AND state IN ('pending','running')"
            ).fetchall()
            for row in rows:
                if row['state'] == 'pending':
                    connection.execute("UPDATE jobs SET state='completed',evidence='cancelled before launch',"
                                       "updated_ns=? WHERE id=? AND state='pending'", (time.time_ns(), row['id']))
                else:
                    connection.execute('INSERT OR IGNORE INTO cancel_requests (job_id) VALUES (?)', (row['id'],))
            return len(rows)

    def cancellation_requested(self, job_id):
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            return connection.execute('SELECT 1 FROM cancel_requests WHERE job_id=?', (job_id,)).fetchone() is not None

    def conversion_status(self):
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT id,state,evidence FROM jobs WHERE operation IN ('convert-library-request','convert-library') "
                "ORDER BY created_ns DESC,id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def request_epub_fixer(self, book_id):
        if book_id is not None and (not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0):
            raise ValueError('invalid book id')
        intent = json.dumps({'book_id': book_id}, sort_keys=True, separators=(',', ':'))
        with self._transaction() as connection:
            row = connection.execute("SELECT id,intent FROM jobs WHERE operation='epub-fixer-request' "
                                     "AND state='pending' ORDER BY created_ns LIMIT 1").fetchone()
            if row and row['intent'] == intent:
                return row['id']
            job_id, now = uuid.uuid4().hex, time.time_ns()
            connection.execute("INSERT INTO jobs (id,dedup_key,operation,intent,state,created_ns,updated_ns) "
                               "VALUES (?,?,'epub-fixer-request',?,'pending',?,?)",
                               (job_id, 'epub-fixer:' + job_id, intent, now, now))
            return job_id

    def prepare_epub_fixer(self, job_id, old, intent):
        old = json.dumps(old, sort_keys=True, separators=(',', ':'))
        intent = json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        with self._transaction() as connection:
            result = connection.execute("UPDATE jobs SET operation='epub-fixer',intent=?,updated_ns=? "
                                        "WHERE id=? AND operation='epub-fixer-request' AND state='pending' AND intent=?",
                                        (intent, time.time_ns(), job_id, old))
            return result.rowcount == 1

    def cancel_epub_fixer(self):
        with self._transaction() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            rows = connection.execute("SELECT id,state FROM jobs WHERE operation IN ('epub-fixer-request','epub-fixer') "
                                      "AND state IN ('pending','running')").fetchall()
            for row in rows:
                if row['state'] == 'pending':
                    connection.execute("UPDATE jobs SET state='completed',evidence='cancelled before launch',updated_ns=? "
                                       "WHERE id=? AND state='pending'", (time.time_ns(), row['id']))
                else:
                    connection.execute('INSERT OR IGNORE INTO cancel_requests (job_id) VALUES (?)', (row['id'],))
            return len(rows)

    def epub_fixer_status(self):
        with self._transaction() as connection:
            row = connection.execute("SELECT id,state,evidence FROM jobs WHERE operation IN "
                                     "('epub-fixer-request','epub-fixer') ORDER BY created_ns DESC,id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def get(self, job_id):
        _required_text(job_id, "job ID")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["intent"] = json.loads(item["intent"])
            return item

    def list_jobs(self, state):
        """Return durable jobs in a state for dispatch or reconciliation."""
        if state not in ("pending", "running", "review", "completed"):
            raise ValueError("unknown job state")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE state=? ORDER BY created_ns, id", (state,)
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["intent"] = json.loads(item["intent"])
                result.append(item)
            return result

    def review_pending(self, job_id, evidence):
        """Pause invalid pending intent without claiming or executing it."""
        _required_text(job_id, "job ID")
        _required_text(evidence, "review evidence")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='review', evidence=?, updated_ns=? "
                "WHERE id=? AND state='pending'", (evidence, time.time_ns(), job_id)
            )
            if cursor.rowcount != 1:
                raise LedgerStateError("job is not pending")

    def start(self, job_id, generation):
        """Claim pending intent after the controller has fenced earlier runs."""
        _required_text(job_id, "job ID")
        _required_text(generation, "generation")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='running', generation=?, attempts=attempts+1, "
                "evidence=NULL, updated_ns=? WHERE id=? AND state='pending'",
                (generation, time.time_ns(), job_id),
            )
            if cursor.rowcount != 1:
                raise LedgerStateError("job is not pending")

    def complete(self, job_id, generation, evidence):
        """Record a verified outcome; this does not verify or remove an input."""
        _required_text(job_id, "job ID")
        _required_text(generation, "generation")
        _required_text(evidence, "completion evidence")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='completed', evidence=?, updated_ns=? "
                "WHERE id=? AND state='running' AND generation=?",
                (evidence, time.time_ns(), job_id, generation),
            )
            if cursor.rowcount != 1:
                raise LedgerStateError("running job or generation does not match")

    def review_interrupted(self):
        """Pause running intents for reconciliation after old jobs are fenced."""
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='review', evidence='interrupted; reconcile before retry', "
                "updated_ns=? WHERE state='running'", (time.time_ns(),)
            )
            return cursor.rowcount

    def require_review(self, job_id, generation, evidence):
        """Pause one running job after a failed or uncertain outcome."""
        _required_text(job_id, "job ID")
        _required_text(generation, "generation")
        _required_text(evidence, "review evidence")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='review', evidence=?, updated_ns=? "
                "WHERE id=? AND state='running' AND generation=?",
                (evidence, time.time_ns(), job_id, generation),
            )
            if cursor.rowcount != 1:
                raise LedgerStateError("running job or generation does not match")

    def reconcile(self, job_id, *, outcome, evidence):
        """After explicit reconciliation, retry or record a verified outcome."""
        _required_text(job_id, "job ID")
        _required_text(evidence, "reconciliation evidence")
        if outcome not in ("retry", "completed"):
            raise ValueError("outcome must be retry or completed")
        state = "pending" if outcome == "retry" else "completed"
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state=?, generation=NULL, evidence=?, updated_ns=? "
                "WHERE id=? AND state='review'", (state, evidence, time.time_ns(), job_id)
            )
            if cursor.rowcount != 1:
                raise LedgerStateError("job is not awaiting review")
            if outcome == 'retry':
                # A verified retry is a new attempt. Otherwise a cancellation
                # from the interrupted attempt would immediately stop it again.
                connection.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
                connection.execute('DELETE FROM cancel_requests WHERE job_id=?', (job_id,))
