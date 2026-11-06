"""Controller-owned manual metadata enforcement for supported EPUB libraries."""
import sqlite3
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from windows_m0_dispatch import EnforcementDispatcher, _digest
from windows_m0_inbox import _flush_directory


class ManualEnforcementDispatcher(EnforcementDispatcher):
    """Run the real all-books enforcer and independently verify its EPUB output."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logs = {}

    def prepare(self):
        with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT books.id,data.format,data.name FROM books LEFT JOIN data '
                              'ON data.book=books.id ORDER BY books.id,data.format').fetchall()
        if not rows:
            raise ValueError('library contains no supported EPUB books')
        books = {}
        for book_id, fmt, name in rows:
            fmt = (fmt or '').upper()
            if fmt not in ('EPUB', 'KEPUB') or not name or any(c in name for c in '/\\:'):
                raise ValueError('manual enforcement supports EPUB and KEPUB books only')
            directory, _epub, title = self._book(book_id)
            file = (directory / (name + '.' + fmt.lower())).resolve(strict=True)
            if not file.is_relative_to(directory) or not file.is_file():
                raise ValueError('book format is outside its directory')
            books.setdefault(book_id, {'book_id': book_id, 'title': title, 'files': []})['files'].append(
                {'format': fmt, 'sha256': _digest(file)})
        return {'books': list(books.values())}

    @staticmethod
    def _title_matches(file, title):
        with zipfile.ZipFile(file) as archive:
            if archive.testzip() is not None:
                return False
            def unique(name):
                entries = [entry for entry in archive.infolist() if entry.filename == name]
                if len(entries) != 1 or entries[0].file_size > 2 * 1024 * 1024:
                    raise ValueError('invalid EPUB metadata')
                return archive.read(entries[0])
            root = ET.fromstring(unique('META-INF/container.xml'))
            paths = root.findall('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile')
            if len(paths) != 1:
                return False
            name = PurePosixPath(paths[0].get('full-path', ''))
            if name.is_absolute() or not name.parts or any(p in ('', '.', '..') or '\\' in p or ':' in p for p in name.parts):
                return False
            package = ET.fromstring(unique(str(name)))
            titles = package.findall('.//{http://purl.org/dc/elements/1.1/}title')
            return bool(titles and ''.join(titles[0].itertext()).strip() == title)

    def start(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'pending' or record['operation'] != 'manual-enforce':
            raise ValueError('manual enforcement is not pending')
        def preflight():
            if self.prepare() != record['intent']:
                raise ValueError('library changed before manual enforcement')
            if self._verify_endpoint(self.port) is not True:
                raise OSError('application endpoint ownership changed')
            self._check_settings()
            return True
        attempt = self.work_root / uuid.uuid4().hex
        attempt.mkdir()
        _flush_directory(self.work_root)
        log = attempt / 'worker.log'
        script = Path(__file__).with_name('cover_enforcer.py').resolve(strict=True)
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "sys.stdout=open(sys.argv[3],'a',encoding='utf-8',buffering=1); sys.stderr=sys.stdout; "
                     "sys.argv=[sys.argv[2],'-all']; runpy.run_path(sys.argv[0],run_name='__main__')")
        pid = self.owner.start(job_id, self.python,
                               ['-I', '-c', bootstrap, str(script.parent), str(script), str(log)],
                               cwd=self.state, environment=self.environment, writer_lock_dir=self.writer_lock_dir,
                               preflight=preflight)
        self._logs[job_id] = log
        return pid

    def verify(self, job_id):
        record = self.owner.ledger.get(job_id)
        log = self._logs.get(job_id)
        if (not record or record['operation'] != 'manual-enforce' or not log or
                'SUCCESS: All covers & metadata successfully updated' not in log.read_text(encoding='utf-8', errors='replace')):
            return False
        expected = record['intent']['books']
        current = self.prepare()['books']
        if [(b['book_id'], b['title'], [f['format'] for f in b['files']]) for b in current] != [
                (b['book_id'], b['title'], [f['format'] for f in b['files']]) for b in expected]:
            return False
        with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True) as db:
            for book in expected:
                directory, _, title = self._book(book['book_id'])
                rows = db.execute('SELECT format,name FROM data WHERE book=?', (book['book_id'],)).fetchall()
                for fmt, name in rows:
                    if not self._title_matches(directory / (name + '.' + fmt.lower()), title):
                        return False
        return True

    def finish(self, job_id):
        self.owner.finish(job_id, evidence='all supported EPUB titles verified', verify=lambda: self.verify(job_id))
