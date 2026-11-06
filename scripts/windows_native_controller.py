"""Connect a configured native profile to the proven M0 runtime components.

The setup layer must validate the profile ACL, paths and private runtime before
calling this module. A profile awaiting setup must not call this entry point.
"""

from pathlib import Path
from contextlib import closing
import json
import os
import secrets
import sqlite3
import time
import uuid

from windows_m0_activation import open_or_activate
from windows_m0_coordinator import CoordinatorBusy, require_no_maintenance_journal
from windows_m0_dispatch import EnforcementDispatcher, IngestDispatcher
from windows_m0_inbox import Inbox, _flush_directory, _volume_identity
from windows_m0_job import Job, generation_exists
from windows_m0_readiness import OwnedReadiness
from windows_m0_scheduler import IngestScheduler
from windows_native_http import validate_http_settings


class NativeController:
    """Own the web process and scheduler for one already-configured profile."""

    def __init__(self, *, helper, state, runtime, library, ingest, port,
                 environment, web_entrypoint, ready_timeout=60):
        self.helper = Path(helper).resolve(strict=True)
        self.state = Path(state).resolve(strict=True)
        self.runtime = Path(runtime).resolve(strict=True)
        self.library = Path(library).resolve(strict=True)
        self.ingest = Path(ingest).resolve(strict=True)
        self.web_entrypoint = Path(web_entrypoint).resolve(strict=True)
        self.python = (self.runtime / 'Scripts' / 'python.exe').resolve(strict=True)
        self.environment = dict(environment)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid configured port')
        if not 0 < ready_timeout <= 300:
            raise ValueError('invalid readiness timeout')
        self.port = port
        self.ready_timeout = ready_timeout
        self.local = self.job = self.process = self.scheduler = self.readiness = None
        self.web_generation = None
        self.generation_file = self.state / 'web-generation'

    @staticmethod
    def require_no_maintenance_journal(state):
        """Block on any journal entry or inability to establish its absence."""
        require_no_maintenance_journal(state)

    def _require_no_maintenance_journal(self):
        self.require_no_maintenance_journal(self.state)

    def start(self):
        """Start once; return the authenticated existing status on a duplicate."""
        if self.local is not None and self.scheduler is not None:
            raise RuntimeError('controller is already started')
        if self.local is not None and not self.local.thread.is_alive():
            raise CoordinatorBusy('local control stopped; recovery is required')
        # Check before activation as well as under ownership: even a duplicate
        # launch must not report an instance ready during maintenance recovery.
        self._require_no_maintenance_journal()
        already_owned = self.local is not None
        if self.local is None:
            local, existing = open_or_activate(self.helper, self.state)
            if existing is not None:
                self._require_no_maintenance_journal()
                return existing
            self.local = local
        try:
            # Maintenance acquires this same profile lock before writing the
            # journal. Recheck under that lock to close the acquisition race.
            self._require_no_maintenance_journal()
        except BaseException:
            if not already_owned:
                self.local.close()
                self.local = None
            raise
        try:
            owner = self.local.owner
            owner.fence_interrupted()
            if owner.ledger.list_jobs('review'):
                raise CoordinatorBusy('unfinished work must be reviewed before startup; launch with --review')
            if self.generation_file.exists():
                previous = self.generation_file.read_text(encoding='ascii').strip()
                if generation_exists(self.helper, previous):
                    raise CoordinatorBusy('previous web process has not been fenced')
                self.generation_file.unlink()
                _flush_directory(self.state)
            app_db = Path(self.environment.get('CWA_APP_DB_PATH', ''))
            if not app_db.is_absolute() or not app_db.is_file():
                raise ValueError('an existing application settings database is required')
            marker = self.state / 'setup-complete.json'
            try:
                setup = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                raise ValueError('validated profile setup has not been completed') from error
            if (not isinstance(setup, dict) or setup.get('schema') != 1 or
                    setup.get('library') != str(self.library) or
                    setup.get('ingest') != str(self.ingest) or
                    setup.get('app_db') != str(app_db.resolve(strict=True))):
                raise ValueError('setup completion does not match the configured profile')
            database = self.library / 'metadata.db'
            info = database.stat()
            if not info.st_ino:
                raise ValueError('library database identity is unavailable')
            library_volume = _volume_identity(self.library)
            if (setup.get('library_volume') != list(library_volume) or
                    setup.get('ingest_volume') != list(_volume_identity(self.ingest)) or
                    setup.get('state_volume') != list(_volume_identity(self.state)) or
                    setup.get('library_database') != [info.st_dev, info.st_ino]):
                raise ValueError('configured storage or library identity changed; work remains stopped')
            with closing(sqlite3.connect(app_db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                validate_http_settings(db)
                settings = db.execute('SELECT config_calibre_dir, config_calibre_split FROM settings').fetchall()
                admins = db.execute('SELECT password FROM user WHERE role & 1 = 1').fetchall()
            from werkzeug.security import check_password_hash
            if not admins or any(not row[0] or check_password_hash(row[0], 'admin123') for row in admins):
                raise ValueError('administrator credentials require setup')
            if (len(settings) != 1 or not settings[0][0] or settings[0][1] or
                    Path(settings[0][0]).resolve(strict=True) != self.library):
                raise ValueError('profile setup is incomplete or library configuration does not match')
            # Keep profile discovery and maintenance recovery read-only. Only
            # an owner that passed the journal and profile checks may create
            # the private runtime's temporary directory.
            temporary = self.state / 'tmp'
            temporary.mkdir(exist_ok=True)
            temp_info = temporary.stat(follow_symlinks=False)
            if (getattr(temp_info, 'st_file_attributes', 0) & 0x400 or
                    not temporary.is_dir() or temporary.resolve(strict=True).parent != self.state):
                raise ValueError('temporary directory must be inside private state')
            # Bind the setup-verified storage before any web process can open
            # databases or run startup jobs. A changed ledger binding fails
            # here, not after the web server has already begun mutating data.
            owner.ledger.bind_profile('library-storage', {
                'library': os.path.normcase(str(self.library)), 'volume': library_volume,
                'database_device': info.st_dev, 'database_inode': info.st_ino,
            })
            stage = self.state / 'staged'
            stage.mkdir(exist_ok=True)
            inbox = Inbox(owner, self.ingest, stage)
            identity = f'{info.st_dev}:{info.st_ino}'
            key = secrets.token_bytes(32)
            generation = 'nj1-' + uuid.uuid4().hex
            with self.generation_file.open('x', encoding='ascii') as stream:
                stream.write(generation)
                stream.flush()
                os.fsync(stream.fileno())
            _flush_directory(self.state)
            self.web_generation = generation
            web_env = dict(self.environment)
            web_env.update({
                'CWNG_NATIVE_MODE': '1', 'CWNG_NATIVE_STATE_DIR': str(self.state),
                'CWNG_NATIVE_READY_KEY': key.hex(),
                'CWNG_NATIVE_WEB_GENERATION': generation, 'CWNG_NATIVE_HELPER': str(self.helper),
                'CWA_CALIBRE_LIBRARY_DIR': str(self.library),
                'CWA_PORT_OVERRIDE': str(self.port),
            })
            self.job = Job(self.helper, generation=generation)
            self.process = self.job.spawn(self.python, ['-I', '-B', str(self.web_entrypoint)],
                                          cwd=self.state, environment=web_env)
            self.readiness = OwnedReadiness(self.job, self.process, self.port, key, identity)
            deadline = time.monotonic() + self.ready_timeout
            while not self.readiness.verify():
                if self.process.wait(0) is not None:
                    raise RuntimeError('native web process exited before readiness')
                if time.monotonic() >= deadline:
                    raise TimeoutError('native web process did not become ready')
                time.sleep(.1)
            worker_env = dict(self.environment)
            worker_env.update({'CWNG_NATIVE_MODE': '1', 'CWNG_NATIVE_STATE_DIR': str(self.state),
                               'CWA_CALIBRE_LIBRARY_DIR': str(self.library),
                               'CWA_INGEST_FOLDER': str(self.ingest),
                               'CWA_PORT_OVERRIDE': str(self.port)})
            verify = lambda candidate: candidate == self.port and self.readiness.verify()
            dispatcher = IngestDispatcher(owner, self.runtime, self.library, worker_env,
                                          verify_endpoint=verify)
            enforcer = EnforcementDispatcher(owner, self.runtime, self.library, worker_env,
                                             verify_endpoint=verify)
            self.scheduler = IngestScheduler(owner, inbox, dispatcher, enforcer=enforcer)
            self.scheduler.start()
            return None
        except BaseException:
            if already_owned:
                self.pause()
            else:
                self.close()
            raise

    def step(self, timeout=1.0):
        if self.scheduler is None:
            raise RuntimeError('controller is not running')
        if not self.local.thread.is_alive():
            self.scheduler.stop_safely()
            raise CoordinatorBusy('local control stopped; recovery is required')
        if not self.readiness.verify():
            self.scheduler.stop_safely()
            raise RuntimeError('owned web process is unavailable; work was stopped')
        return self.scheduler.step(timeout=timeout)

    def finish_current(self):
        if self.scheduler is None:
            raise RuntimeError('controller is not running')
        self.scheduler.finish_current()

    def stop_safely(self):
        if self.scheduler is None:
            raise RuntimeError('controller is not running')
        return self.scheduler.stop_safely()

    def pause(self):
        """Stop the web process and scheduler while retaining profile ownership."""
        if self.scheduler is not None:
            try:
                if self.scheduler.active is not None:
                    self.scheduler.stop_safely()
            finally:
                self.scheduler.close()
                self.scheduler = None
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process is not None:
            self.process.close()
            self.process = None
        if self.web_generation is not None:
            deadline = time.monotonic() + 5
            while generation_exists(self.helper, self.web_generation):
                if time.monotonic() >= deadline:
                    raise CoordinatorBusy('web job is still alive; profile ownership retained')
                time.sleep(.05)
            self.generation_file.unlink(missing_ok=True)
            _flush_directory(self.state)
            self.web_generation = None
        if self.local is not None:
            # A stopped writer can outlive its caller if another process still
            # holds its job handle. Never relinquish profile ownership until the
            # durable generation has been fenced.
            self.local.owner.fence_interrupted()

    def close(self):
        """Close owned processes; interrupted jobs remain durable for review."""
        self.pause()
        if self.local is not None:
            self.local.close()
            self.local = None
