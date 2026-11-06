"""Conservative M0 dispatch for a whole-library conversion request."""
from contextlib import closing
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import uuid
import zipfile

from windows_m0_dispatch import _digest


_SOURCES = {'acsm', 'azw', 'azw3', 'azw4', 'cbz', 'cbr', 'cb7', 'cbc', 'chm',
            'djvu', 'docx', 'epub', 'fb2', 'fbz', 'html', 'htmlz', 'lit', 'lrf',
            'mobi', 'odt', 'pdf', 'prc', 'pdb', 'pml', 'rb', 'rtf', 'snb', 'tcr',
            'txt', 'txtz', 'kfx', 'kfx-zip'}


class LibraryConversionDispatcher:
    """Contain conversion and verify its intended additions after quiescence."""

    def __init__(self, ingest_dispatcher):
        self.ingest = ingest_dispatcher
        self.owner = ingest_dispatcher.owner

    def _path(self, book_path, name, fmt):
        parts = PurePosixPath(book_path)
        if (parts.is_absolute() or not parts.parts or
                any(part in ('', '.', '..') or ':' in part or '\\' in part for part in parts.parts) or
                not name or any(char in name for char in '/\\:') or
                not fmt or any(char in fmt for char in '/\\:')):
            raise ValueError('unsafe book format path')
        path = self.ingest.library.joinpath(*parts.parts, name + '.' + fmt.lower()).resolve(strict=True)
        if not path.is_relative_to(self.ingest.library) or not path.is_file():
            raise ValueError('book format is outside the verified library')
        return path

    def prepare(self):
        self.ingest._check_settings()
        with closing(sqlite3.connect(self.ingest.cwa_db.as_uri() + '?mode=ro', uri=True)) as db:
            settings = db.execute('SELECT auto_convert_target_format, auto_convert_ignored_formats '
                                  'FROM cwa_settings').fetchall()
        if len(settings) != 1:
            raise ValueError('conversion settings are ambiguous')
        target, ignored = settings[0]
        if target not in ('epub', 'kepub') or not isinstance(ignored, str):
            raise ValueError('M0 conversion supports EPUB and KEPUB targets')
        ignored = set(ignored.split(',')) if ignored else set()
        with closing(sqlite3.connect(self.ingest.database.as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute('SELECT books.id,books.path,data.name,data.format FROM books '
                              'JOIN data ON data.book=books.id ORDER BY books.id,data.format').fetchall()
        books = {}
        for book_id, book_path, name, fmt in rows:
            books.setdefault(book_id, []).append((book_path, name, fmt))
        sources, expected = [], []
        for book_id, formats in books.items():
            if any(fmt.lower() == target for _, _, fmt in formats):
                continue
            eligible = [(path, name, fmt) for path, name, fmt in formats
                        if fmt.lower() in _SOURCES and fmt.lower() not in ignored]
            if not eligible:
                continue
            expected.append(book_id)
            for book_path, name, fmt in eligible:
                path = self._path(book_path, name, fmt)
                sources.append({'path': str(path.relative_to(self.ingest.library)), 'sha256': _digest(path)})
        return {'target': target, 'expected': expected, 'sources': sources}

    def start(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'pending' or record['operation'] != 'convert-library':
            raise ValueError('job is not pending conversion')
        if self.prepare() != record['intent']:
            raise ValueError('library or conversion settings changed before conversion')
        if self.ingest._verify_endpoint(self.ingest.port) is not True:
            raise OSError('application endpoint ownership changed')
        if uuid.UUID(hex=job_id).hex != job_id:
            raise ValueError('invalid conversion job id')
        attempt = self.ingest.work_root / ('convert-library-' + job_id + '-' + str(record['attempts'] + 1))
        attempt.mkdir()
        script = Path(__file__).with_name('convert_library.py').resolve(strict=True)
        worker = Path(__file__).with_name('windows_m0_conversion_worker.py').resolve(strict=True)
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')")
        environment = dict(self.ingest.environment, CWNG_NATIVE_CANCEL_FILE=str(attempt / 'cancel'))
        try:
            return self.owner.start(job_id, self.ingest.python,
                                    ['-I', '-B', '-c', bootstrap, str(script.parent), str(worker), str(script), job_id,
                                     str(record['attempts'] + 1), str(attempt / 'worker.log')],
                                    cwd=self.ingest.state, environment=environment,
                                    writer_lock_dir=self.ingest.writer_lock_dir,
                                    preflight=lambda: self.prepare() == record['intent'])
        except BaseException:
            current = self.owner.ledger.get(job_id)
            if current and current['state'] in ('pending', 'completed'):
                shutil.rmtree(attempt)
            raise

    def request_cancel(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'running' or record['attempts'] < 1:
            raise ValueError('conversion is not running')
        attempt = self.ingest.work_root / ('convert-library-' + job_id + '-' + str(record['attempts']))
        (attempt / 'cancel').touch(exist_ok=True)

    def verify(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['operation'] != 'convert-library':
            return False
        intent = record['intent']
        self.ingest._check_settings()
        for source in intent['sources']:
            path = (self.ingest.library / source['path']).resolve(strict=True)
            if not path.is_relative_to(self.ingest.library) or _digest(path) != source['sha256']:
                return False
        with closing(sqlite3.connect(self.ingest.database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                return False
            for book_id in intent['expected']:
                rows = db.execute('SELECT books.path,data.name,data.format FROM books JOIN data ON data.book=books.id '
                                  'WHERE books.id=? AND LOWER(data.format)=?', (book_id, intent['target'])).fetchall()
                if len(rows) != 1:
                    return False
                path = self._path(*rows[0])
                with zipfile.ZipFile(path) as archive:
                    if (archive.testzip() is not None or
                            archive.read('mimetype') != b'application/epub+zip' or
                            'META-INF/container.xml' not in archive.namelist()):
                        return False
        return True

    def finish(self, job_id):
        self.owner.finish(job_id, evidence='source formats retained and target formats verified',
                          verify=lambda: self.verify(job_id))
