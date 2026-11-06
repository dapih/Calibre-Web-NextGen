"""Managed native EPUB fixes with staged output and retained originals."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid
import zipfile

from windows_m0_conversion import LibraryConversionDispatcher
from windows_m0_dispatch import _digest


def _valid_epub(path):
    with zipfile.ZipFile(path) as archive:
        return (archive.testzip() is None and archive.read('mimetype') == b'application/epub+zip'
                and 'META-INF/container.xml' in archive.namelist())


class EpubFixerDispatcher(LibraryConversionDispatcher):
    def prepare(self, book_id):
        self.ingest._check_settings()
        if book_id is not None and (not isinstance(book_id, int) or isinstance(book_id, bool) or book_id <= 0):
            raise ValueError('invalid book id')
        with closing(sqlite3.connect(self.ingest.database.as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute('SELECT books.id,books.path,data.name,data.format FROM books '
                              "JOIN data ON data.book=books.id WHERE UPPER(data.format) IN ('EPUB','EPUB3') "
                              'AND (? IS NULL OR books.id=?) ORDER BY books.id,data.format',
                              (book_id, book_id)).fetchall()
        if book_id is not None and not rows:
            raise ValueError('selected book has no EPUB')
        files = []
        for identity, book_path, name, fmt in rows:
            path = self._path(book_path, name, fmt)
            if not _valid_epub(path):
                raise ValueError('source EPUB is invalid')
            files.append({'book_id': identity, 'format': fmt, 'path': str(path.relative_to(self.ingest.library)),
                          'sha256': _digest(path)})
        return {'book_id': book_id, 'files': files}

    def start(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['state'] != 'pending' or record['operation'] != 'epub-fixer':
            raise ValueError('job is not pending EPUB repair')
        if self.prepare(record['intent']['book_id']) != record['intent']:
            raise ValueError('library changed before EPUB repair')
        if self.ingest._verify_endpoint(self.ingest.port) is not True:
            raise OSError('application endpoint ownership changed')
        if uuid.UUID(hex=job_id).hex != job_id:
            raise ValueError('invalid repair job id')
        attempt = self.ingest.work_root / ('epub-fixer-' + job_id + '-' + str(record['attempts'] + 1))
        attempt.mkdir()
        manifest = attempt / 'manifest.json'
        worker = Path(__file__).resolve(strict=True)
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "sys.stdout=open(sys.argv[7],'a',encoding='utf-8',buffering=1); sys.stderr=sys.stdout; "
                     "sys.argv=[sys.argv[2]]+sys.argv[3:7]; runpy.run_path(sys.argv[0],run_name='__main__')")
        try:
            manifest.write_text(json.dumps(record['intent']), encoding='utf-8')
            return self.owner.start(job_id, self.ingest.python,
                                    ['-I', '-c', bootstrap, str(worker.parent), str(worker), str(self.ingest.library), str(self.ingest.database),
                                     str(manifest), str(attempt), str(attempt / 'worker.log')], cwd=self.ingest.state,
                                    environment=self.ingest.environment, writer_lock_dir=self.ingest.writer_lock_dir,
                                    preflight=lambda: self.prepare(record['intent']['book_id']) == record['intent'])
        except BaseException:
            current = self.owner.ledger.get(job_id)
            if current and current['state'] in ('pending', 'completed'):
                shutil.rmtree(attempt)
            raise

    def verify(self, job_id):
        record = self.owner.ledger.get(job_id)
        if not record or record['operation'] != 'epub-fixer':
            return False
        self.ingest._check_settings()
        with closing(sqlite3.connect(self.ingest.database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                return False
            if uuid.UUID(hex=job_id).hex != job_id or record['attempts'] < 1:
                return False
            attempt = self.ingest.work_root / ('epub-fixer-' + job_id + '-' + str(record['attempts']))
            for index, item in enumerate(record['intent']['files']):
                backup = attempt / (str(index) + '-original.epub')
                if not backup.is_file() or _digest(backup) != item['sha256']:
                    return False
                path = (self.ingest.library / item['path']).resolve(strict=True)
                if not path.is_relative_to(self.ingest.library) or not _valid_epub(path):
                    return False
                rows = db.execute('SELECT uncompressed_size FROM data WHERE book=? AND format=?',
                                  (item['book_id'], item['format'])).fetchall()
                if len(rows) != 1 or rows[0][0] != path.stat().st_size:
                    return False
        return True

    def finish(self, job_id):
        self.owner.finish(job_id, evidence='EPUB archives and library sizes verified',
                          verify=lambda: self.verify(job_id))


def _worker(library, database, manifest, attempt):
    from calibre_library_target import require_native_writer_lease
    require_native_writer_lease()
    library, database, attempt = Path(library).resolve(strict=True), Path(database).resolve(strict=True), Path(attempt)
    records = json.loads(Path(manifest).read_text(encoding='utf-8'))['files']
    fixer = Path(__file__).with_name('kindle_epub_fixer.py').resolve(strict=True)
    for index, item in enumerate(records):
        original = (library / item['path']).resolve(strict=True)
        if not original.is_relative_to(library) or _digest(original) != item['sha256']:
            raise ValueError('source EPUB changed before repair')
        backup = attempt / (str(index) + '-original.epub')
        shutil.copy2(original, backup)
        with backup.open('r+b') as handle:
            handle.flush(); os.fsync(handle.fileno())
        stage = original.with_name('.cwng-fixer-' + uuid.uuid4().hex + '.epub')
        shutil.copy2(original, stage)
        bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                     "sys.argv=[sys.argv[2]]+sys.argv[3:]; runpy.run_path(sys.argv[0],run_name='__main__')")
        completed = subprocess.run([sys.executable, '-I', '-c', bootstrap, str(fixer.parent), str(fixer),
                                    '--input_file', str(stage)],
                                   cwd=attempt, env=os.environ.copy(), timeout=1800)
        if completed.returncode or not _valid_epub(stage):
            raise RuntimeError('EPUB repair failed or produced an invalid archive')
        if _digest(original) != item['sha256']:
            raise ValueError('source EPUB changed during repair')
        if _digest(stage) == item['sha256']:
            stage.unlink()
        else:
            os.replace(stage, original)
        with closing(sqlite3.connect(database, timeout=30)) as db:
            result = db.execute('UPDATE data SET uncompressed_size=? WHERE book=? AND format=?',
                                (original.stat().st_size, item['book_id'], item['format']))
            if result.rowcount != 1:
                raise ValueError('book format changed during repair')
            db.commit()


if __name__ == '__main__':
    _worker(*sys.argv[1:])
