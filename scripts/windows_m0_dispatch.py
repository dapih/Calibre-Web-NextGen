"""Experimental controller dispatch for admitted local ingest work.

The caller owns private runtime/environment policy and profile ACL validation.
No source is removed here; ambiguous outcomes remain in review.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import uuid
import xml.etree.ElementTree as ET
import zipfile

from windows_m0_inbox import _flush_directory, _volume_identity


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class IngestDispatcher:
    """Run the real ingest entrypoint as a contained, verified writer."""

    def __init__(self, coordinator, runtime, library, environment, *, verify_endpoint):
        self.owner = coordinator
        self.state = Path(coordinator.state_dir).resolve(strict=True)
        self.runtime = Path(runtime).resolve(strict=True)
        self.python = (self.runtime / 'Scripts' / 'python.exe').resolve(strict=True)
        self.script = Path(__file__).with_name('ingest_processor.py').resolve(strict=True)
        self.library = Path(library).resolve(strict=True)
        self.database = self.library / 'metadata.db'
        if not self.database.is_file():
            raise ValueError('an existing Calibre database is required')
        database_info = self.database.stat()
        if not database_info.st_ino:
            raise ValueError('Calibre database identity is unavailable')
        self.database_identity = database_info.st_dev, database_info.st_ino
        self.library_volume = _volume_identity(self.library)
        self.environment = dict(environment)
        configured_lock = self.environment.get('CWA_METADATA_LOCK_DIR')
        if not configured_lock:
            raise ValueError('an explicit private writer lock directory is required')
        self.writer_lock_dir = Path(configured_lock).resolve(strict=True)
        if not self.writer_lock_dir.is_dir():
            raise ValueError('writer lock directory must exist')
        raw_port = self.environment.get('CWA_PORT_OVERRIDE', '')
        if not raw_port.isascii() or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
            raise ValueError('a verified private application port is required')
        self.port = int(raw_port)
        if not callable(verify_endpoint) or verify_endpoint(self.port) is not True:
            raise ValueError('application endpoint ownership could not be verified')
        self._verify_endpoint = verify_endpoint
        configured = self.environment.get('CWA_CALIBRE_LIBRARY_DIR')
        if not configured or Path(configured).resolve(strict=True) != self.library:
            raise ValueError('worker library does not match the verified library')
        app_db = self.environment.get('CWA_APP_DB_PATH')
        if not app_db or not Path(app_db).is_file():
            raise ValueError('an existing app settings database is required')
        with sqlite3.connect(Path(app_db).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as connection:
            rows = connection.execute('SELECT config_calibre_dir, config_calibre_split FROM settings').fetchall()
        if len(rows) != 1 or not rows[0][0] or Path(rows[0][0]).resolve(strict=True) != self.library or rows[0][1]:
            raise ValueError('app settings do not name the verified unsplit library')
        self.app_db = Path(app_db).resolve(strict=True)
        cwa_dir = self.environment.get('CWA_DB_PATH')
        cwa_db = Path(cwa_dir or '') / 'cwa.db'
        if not cwa_dir or not cwa_db.is_file():
            raise ValueError('an existing ingest settings database is required')
        with sqlite3.connect(cwa_db.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as connection:
            rows = connection.execute('SELECT auto_ingest_folder_label_target FROM cwa_settings').fetchall()
        if len(rows) != 1 or rows[0][0] not in ('', 'disabled', None):
            raise ValueError('folder-label ingest is not yet supported by this dispatcher')
        self.cwa_db = cwa_db.resolve(strict=True)
        self.source_dir = Path(self.environment.get('CWA_INGEST_FOLDER', '')).resolve(strict=True)
        if not self.source_dir.is_dir():
            raise ValueError('an existing ingest directory is required')
        self.owner.ledger.bind_profile('library-storage', {
            'library': os.path.normcase(str(self.library)), 'volume': self.library_volume,
            'database_device': self.database_identity[0], 'database_inode': self.database_identity[1],
        })
        self.work_root = self.state / 'ingest-work'
        self.work_root.mkdir(exist_ok=True)
        if not self.work_root.resolve(strict=True).is_relative_to(self.state):
            raise ValueError('worker directory escapes controller state')

    def _check_settings(self):
        with sqlite3.connect(self.app_db.as_uri() + '?mode=ro', uri=True) as connection:
            rows = connection.execute('SELECT config_calibre_dir, config_calibre_split FROM settings').fetchall()
        if len(rows) != 1 or not rows[0][0] or Path(rows[0][0]).resolve(strict=True) != self.library or rows[0][1]:
            raise ValueError('app library settings changed')
        with sqlite3.connect(self.cwa_db.as_uri() + '?mode=ro', uri=True) as connection:
            rows = connection.execute('SELECT auto_ingest_folder_label_target FROM cwa_settings').fetchall()
        if len(rows) != 1 or rows[0][0] not in ('', 'disabled', None):
            raise ValueError('folder-label ingest is not yet supported by this dispatcher')

    def start(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'pending' or record['operation'] != 'ingest':
            raise ValueError('job is not pending ingest')
        intent = record['intent']
        if set(intent) != {'source', 'staged', 'sha256', 'size'}:
            raise ValueError('unexpected ingest intent')
        staged = Path(intent['staged']).resolve(strict=True)
        if not staged.is_relative_to(self.state / 'staged') or not staged.is_file():
            raise ValueError('staged input is outside private staging')
        if _digest(staged) != intent['sha256'] or staged.stat().st_size != intent['size']:
            raise ValueError('staged input changed')
        source = Path(intent['source']).resolve(strict=True)
        if not source.is_relative_to(self.source_dir) or not source.is_file() or _digest(source) != intent['sha256']:
            raise ValueError('source input changed or escaped the ingest directory')
        if any(Path(str(source) + suffix).exists() for suffix in ('.cwa.json', '.cwa.failed.json')):
            raise ValueError('sidecar ingest is not yet supported by this dispatcher')
        if _volume_identity(self.library) != self.library_volume:
            raise OSError('library volume changed')
        database_info = self.database.stat()
        if (database_info.st_dev, database_info.st_ino) != self.database_identity:
            raise OSError('library database identity changed')
        if self._verify_endpoint(self.port) is not True:
            raise OSError('application endpoint ownership changed')
        attempt = self.work_root / uuid.uuid4().hex
        attempt.mkdir()
        _flush_directory(self.work_root)
        work_file = attempt / ('input' + staged.suffix)
        with staged.open('rb') as source, work_file.open('xb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if _digest(work_file) != intent['sha256']:
            raise OSError('working input copy changed')
        _flush_directory(attempt)
        if self._verify_endpoint(self.port) is not True:
            raise OSError('application endpoint ownership changed before launch')
        self._check_settings()
        def preflight():
            if (_volume_identity(self.library) != self.library_volume or
                    (self.database.stat().st_dev, self.database.stat().st_ino) != self.database_identity):
                raise OSError('library identity changed before ingest')
            if _digest(staged) != intent['sha256'] or _digest(work_file) != intent['sha256']:
                raise ValueError('ingest input changed before launch')
            original = Path(intent['source']).resolve(strict=True)
            if not original.is_relative_to(self.source_dir) or _digest(original) != intent['sha256']:
                raise ValueError('original input changed before launch')
            if self._verify_endpoint(self.port) is not True:
                raise OSError('application endpoint ownership changed before launch')
            self._check_settings()
            return True
        # Isolated mode ignores PYTHONPATH, so explicitly add only this source tree.
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "__import__('windows_m0_origin').install(sys.argv[2]); "
                     "sys.stdout=open(sys.argv[4],'a',encoding='utf-8',buffering=1); sys.stderr=sys.stdout; "
                     "sys.argv=[sys.argv[2],sys.argv[3]]; runpy.run_path(sys.argv[0],run_name='__main__')")
        return self.owner.start(job_id, self.python,
                                ['-I', '-c', bootstrap, str(self.script.parent), str(self.script), str(work_file),
                                 str(attempt / 'worker.log')],
                                cwd=self.state, environment=self.environment,
                                writer_lock_dir=self.writer_lock_dir, preflight=preflight)

    def verify(self, job_id):
        """Verify source, retained stage, committed marker and stored format."""
        record = self.owner.ledger.get(job_id)
        if not record or record['operation'] != 'ingest':
            return False
        intent = record['intent']
        if _volume_identity(self.library) != self.library_volume or not self.database.is_file():
            return False
        database_info = self.database.stat()
        if (database_info.st_dev, database_info.st_ino) != self.database_identity:
            return False
        source, staged = Path(intent['source']), Path(intent['staged'])
        if not source.is_file() or not staged.is_file() or _digest(source) != intent['sha256'] or _digest(staged) != intent['sha256']:
            return False
        with sqlite3.connect(self.database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('BEGIN')
            rows = connection.execute(
                'SELECT books.path, data.name, data.format FROM identifiers '
                'JOIN books ON books.id=identifiers.book JOIN data ON data.book=books.id '
                'WHERE identifiers.type=? AND identifiers.val=?',
                ('cwng_ingest_sha256_' + intent['sha256'], intent['sha256']),
            ).fetchall()
        for book_path, name, extension in rows:
            parts = PurePosixPath(book_path)
            if parts.is_absolute() or not parts.parts or any(part in ('', '.', '..') or ':' in part or '\\' in part for part in parts.parts):
                continue
            if not name or any(char in name for char in '/\\:') or not extension or not extension.isalnum():
                continue
            candidate = self.library.joinpath(*parts.parts, name + '.' + extension.lower())
            try:
                resolved = candidate.resolve(strict=True)
                if resolved.is_relative_to(self.library) and resolved.is_file():
                    return True
            except OSError:
                continue
        return False

    def finish(self, job_id):
        self.owner.finish(job_id, evidence='source, stage, Calibre marker and format verified',
                          verify=lambda: self.verify(job_id))


class EnforcementDispatcher(IngestDispatcher):
    """Run the actual metadata enforcer for one verified EPUB book."""

    def _book(self, book_id):
        if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0:
            raise ValueError('invalid book id')
        if _volume_identity(self.library) != self.library_volume:
            raise OSError('library volume changed')
        info = self.database.stat()
        if (info.st_dev, info.st_ino) != self.database_identity:
            raise OSError('library database identity changed')
        with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True) as connection:
            rows = connection.execute(
                "SELECT books.path, books.title, data.name FROM books JOIN data ON data.book=books.id "
                "WHERE books.id=? AND UPPER(data.format)='EPUB'", (book_id,),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError('book must have exactly one EPUB format')
        book_path, title, name = rows[0]
        parts = PurePosixPath(book_path)
        if (parts.is_absolute() or not parts.parts or
                any(part in ('', '.', '..') or ':' in part or '\\' in part for part in parts.parts) or
                not name or any(char in name for char in '/\\:')):
            raise ValueError('book path is unsafe')
        directory = self.library.joinpath(*parts.parts).resolve(strict=True)
        book = (directory / (name + '.epub')).resolve(strict=True)
        if not directory.is_relative_to(self.library) or not book.is_relative_to(directory) or not book.is_file():
            raise ValueError('EPUB is outside the library')
        return directory, book, title

    def prepare(self, book_id):
        _, book, title = self._book(book_id)
        return {'book_id': book_id, 'title': title, 'before_sha256': _digest(book)}

    def enqueue(self, event_id, book_id):
        return self.owner.enqueue(event_id, 'enforce', self.prepare(book_id))

    def start(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'pending' or record['operation'] != 'enforce':
            raise ValueError('job is not pending enforcement')
        intent = record['intent']
        if set(intent) != {'book_id', 'title', 'before_sha256'}:
            raise ValueError('unexpected enforcement intent')
        directory, book, title = self._book(intent['book_id'])
        def preflight():
            _directory, current_book, current_title = self._book(intent['book_id'])
            if _directory != directory or current_title != intent['title'] or _digest(current_book) != intent['before_sha256']:
                raise ValueError('book changed before enforcement')
            if self._verify_endpoint(self.port) is not True:
                raise OSError('application endpoint ownership changed')
            self._check_settings()
            return True
        attempt = self.work_root / uuid.uuid4().hex
        attempt.mkdir()
        _flush_directory(self.work_root)
        script = Path(__file__).with_name('cover_enforcer.py').resolve(strict=True)
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "__import__('windows_m0_origin').install(sys.argv[2]); "
                     "sys.stdout=open(sys.argv[4],'a',encoding='utf-8',buffering=1); sys.stderr=sys.stdout; "
                     "sys.argv=[sys.argv[2],'--dir',sys.argv[3]]; runpy.run_path(sys.argv[0],run_name='__main__')")
        return self.owner.start(job_id, self.python,
                                ['-I', '-c', bootstrap, str(script.parent), str(script), str(directory),
                                 str(attempt / 'worker.log')], cwd=self.state, environment=self.environment,
                                writer_lock_dir=self.writer_lock_dir, preflight=preflight)

    def verify(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['operation'] != 'enforce':
            return False
        _, book, title = self._book(record['intent']['book_id'])
        if title != record['intent']['title']:
            return False
        with zipfile.ZipFile(book) as archive:
            def read_unique(name):
                matches = [item for item in archive.infolist() if item.filename == name]
                if len(matches) != 1 or matches[0].file_size > 2 * 1024 * 1024:
                    raise ValueError('missing, ambiguous or oversized EPUB metadata')
                return archive.read(matches[0])
            container = ET.fromstring(read_unique('META-INF/container.xml'))
            roots = container.findall('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile')
            if len(roots) != 1:
                return False
            package = roots[0].get('full-path', '')
            parts = PurePosixPath(package)
            if parts.is_absolute() or not parts.parts or any(p in ('', '.', '..') or '\\' in p or ':' in p for p in parts.parts):
                return False
            metadata = ET.fromstring(read_unique(package))
            titles = metadata.findall('.//{http://purl.org/dc/elements/1.1/}title')
            return bool(titles and ''.join(titles[0].itertext()).strip() == title)

    def finish(self, job_id):
        self.owner.finish(job_id, evidence='library metadata and EPUB title verified',
                          verify=lambda: self.verify(job_id))
