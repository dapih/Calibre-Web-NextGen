"""Offline recovery of a single, job-bound native ingest result."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

if os.name != 'nt':
    pytest.skip('Windows native ingest recovery', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_job import Job
from windows_native_ingest_recovery import confirm_ingest


@pytest.fixture
def ingest(tmp_path):
    helper = os.environ.get('CWNG_NATIVE_TEST_HELPER')
    if not helper:
        pytest.skip('set CWNG_NATIVE_TEST_HELPER to the native helper DLL')
    state, library, source_dir = (tmp_path / name for name in ('state', 'library', 'incoming'))
    for directory in (state, library, source_dir):
        directory.mkdir()
    stage = state / 'staged'
    stage.mkdir()
    work = state / 'ingest-work'
    work.mkdir()
    source = source_dir / 'input.epub'
    source.write_bytes(b'input fixture')
    staged = stage / 'input.epub'
    staged.write_bytes(source.read_bytes())
    book_dir = library / 'Author' / 'Book (1)'
    book_dir.mkdir(parents=True)
    output = book_dir / 'Book.epub'
    output.write_bytes(source.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = library / 'metadata.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT)')
        db.execute('CREATE TABLE data (book INTEGER, name TEXT, format TEXT, uncompressed_size INTEGER)')
        db.execute('CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT)')
        db.execute("INSERT INTO books VALUES (1, 'Author/Book (1)')")
        db.execute("INSERT INTO data VALUES (1, 'Book', 'EPUB', ?)", (output.stat().st_size,))
        db.execute('INSERT INTO identifiers VALUES (1, ?, ?)', ('cwng_ingest_sha256_' + digest, digest))
    owner = Coordinator(helper, state)
    job_id = owner.ledger.enqueue('fixture', 'ingest', {'source': str(source), 'staged': str(staged),
                                                       'sha256': digest, 'size': source.stat().st_size})
    owner.ledger.start(job_id, owner.generation)
    owner.fence_interrupted()
    attempt = work / ('ingest-' + job_id + '-1-' + 'a' * 32)
    attempt.mkdir()
    receipt = {'schema': 1, 'job_id': job_id, 'generation': owner.generation,
               'attempt': 1, 'sha256': digest, 'size': source.stat().st_size, 'book_ids': [1],
               'mode': 'original-epub-no-backup'}
    (attempt / 'ingest-complete.json').write_text(json.dumps(receipt), encoding='utf-8')
    controller = SimpleNamespace(helper=Path(helper), state=state, library=library, ingest=source_dir)
    try:
        yield SimpleNamespace(controller=controller, owner=owner, database=database, job_id=job_id,
                              source=source, staged=staged, output=output, attempt=attempt, receipt=receipt)
    finally:
        owner.close()


def test_confirm_started_ingest_requires_job_receipt_and_exact_stored_bytes(ingest):
    confirm_ingest(ingest.controller, ingest.owner, ingest.database, ingest.job_id, lambda: ingest.database)
    assert ingest.owner.ledger.get(ingest.job_id)['state'] == 'completed'
    assert ingest.source.read_bytes() == ingest.staged.read_bytes() == b'input fixture'


def test_confirm_ingest_waits_until_the_old_named_job_is_gone(ingest):
    live_job = Job(ingest.controller.helper, generation=ingest.owner.generation)
    try:
        with pytest.raises(CoordinatorBusy, match='could not be verified'):
            confirm_ingest(ingest.controller, ingest.owner, ingest.database, ingest.job_id, lambda: ingest.database)
        assert ingest.owner.ledger.get(ingest.job_id)['state'] == 'review'
    finally:
        live_job.close()


@pytest.mark.parametrize('fault', ['missing_receipt', 'wrong_job', 'wrong_generation', 'wrong_attempt',
                                   'boolean_attempt', 'missing_source', 'changed_stage', 'changed_output',
                                   'wrong_size', 'wrong_format', 'converted_receipt', 'missing_marker',
                                   'duplicate_marker', 'cancelled', 'flush', 'storage'])
def test_ambiguous_ingest_stays_in_review(ingest, fault, monkeypatch):
    receipt = ingest.attempt / 'ingest-complete.json'
    if fault == 'missing_receipt':
        receipt.unlink()
    elif fault in ('wrong_job', 'wrong_generation', 'wrong_attempt', 'boolean_attempt'):
        data = dict(ingest.receipt)
        key, value = {'wrong_job': ('job_id', 'b' * 32), 'wrong_generation': ('generation', 'nj1-' + 'c' * 32),
                      'wrong_attempt': ('attempt', 2), 'boolean_attempt': ('attempt', True)}[fault]
        data[key] = value
        receipt.write_text(json.dumps(data), encoding='utf-8')
    elif fault == 'missing_source':
        ingest.source.unlink()
    elif fault == 'changed_stage':
        ingest.staged.write_bytes(b'changed stage')
    elif fault == 'changed_output':
        ingest.output.write_bytes(b'changed book')
    elif fault == 'wrong_size':
        with sqlite3.connect(ingest.database) as db:
            db.execute('UPDATE data SET uncompressed_size=1')
    elif fault == 'wrong_format':
        with sqlite3.connect(ingest.database) as db:
            db.execute("UPDATE data SET format='PDF'")
    elif fault == 'converted_receipt':
        data = dict(ingest.receipt, mode='converted-with-retained-original')
        receipt.write_text(json.dumps(data), encoding='utf-8')
    elif fault == 'missing_marker':
        with sqlite3.connect(ingest.database) as db:
            db.execute('DELETE FROM identifiers')
    elif fault == 'duplicate_marker':
        with sqlite3.connect(ingest.database) as db:
            db.execute('INSERT INTO identifiers SELECT * FROM identifiers')
    elif fault == 'cancelled':
        with sqlite3.connect(ingest.owner.ledger.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO cancel_requests VALUES (?)', (ingest.job_id,))
    elif fault == 'flush':
        monkeypatch.setattr('windows_native_ingest_recovery._flush_directory',
                            lambda _: (_ for _ in ()).throw(OSError('injected flush failure')))
    verify_storage = (lambda: ingest.database.parent / 'other.db') if fault == 'storage' else (lambda: ingest.database)
    with pytest.raises((CoordinatorBusy, OSError, ValueError)):
        confirm_ingest(ingest.controller, ingest.owner, ingest.database, ingest.job_id, verify_storage)
    assert ingest.owner.ledger.get(ingest.job_id)['state'] == 'review'


@pytest.mark.parametrize('mode', ['plain', 'converted_retaining_original', 'backup_configured', 'fixer_enabled'])
def test_processor_reports_only_untransformed_epub_without_backup(monkeypatch, tmp_path, mode):
    import ingest_processor
    source = tmp_path / 'book.epub'
    source.write_bytes(b'original book')
    converted = tmp_path / 'converted.kepub'
    converted.write_bytes(b'converted book')
    class Processor:
        def __init__(self, filepath):
            self.filepath, self.filename = filepath, Path(filepath).name
            self.input_format = 'epub'
            self.target_format = 'kepub' if mode == 'converted_retaining_original' else 'epub'
            self.is_target_format = self.target_format == 'epub'
            self.is_kindle_epub_fixer = mode == 'fixer_enabled'
            self.auto_convert_on = True
            self.can_convert = True
            self.ingest_ignored_formats = []
            self.convert_ignored_formats = []
            self.convert_retained_formats = ['epub']
            self.cwa_settings = {'ingest_timeout_minutes': 1, 'auto_backup_imports': mode == 'backup_configured'}
            self.tmp_conversion_dir = str(tmp_path / 'conversion')
            self.last_added_book_ids = []
            self.last_added_book_id = None
            self.retained = []
        def is_file_in_use(self): return True
        def is_supported_audiobook(self): return False
        def convert_to_kepub(self): return True, str(converted)
        def add_book_to_library(self, *args, **kwargs):
            self.last_added_book_ids = [1]
            self.last_added_book_id = 1
        def add_format_to_book(self, book_id, path): self.retained.append((book_id, path))
        def set_library_permissions(self): pass
        def delete_current_file(self): pass
    processor = Processor(str(source))
    monkeypatch.setattr(ingest_processor, 'NewBookProcessor', lambda _: processor)
    monkeypatch.setattr(ingest_processor, 'require_native_writer_lease', lambda: None)
    monkeypatch.setattr(ingest_processor, '_acquire_process_lock_or_exit', lambda: None)
    monkeypatch.setattr(ingest_processor, 'check_maintenance', lambda: None)
    monkeypatch.setattr(ingest_processor, 'initialize_runtime', lambda: True)
    monkeypatch.setattr(ingest_processor, 'empty_tmp_conversion_dir', lambda _: None)
    receipts = []
    assert ingest_processor.main(str(source), native_completion=receipts.append) == 0
    assert receipts == ([[1]] if mode == 'plain' else [])
    if mode == 'converted_retaining_original':
        assert processor.retained == [(1, str(source))]


def test_missing_ingest_target_never_reports_completion(monkeypatch, tmp_path):
    import ingest_processor
    monkeypatch.setattr(ingest_processor, 'require_native_writer_lease', lambda: None)
    receipts = []
    assert ingest_processor.main(str(tmp_path / 'absent.epub'), native_completion=receipts.append) == 0
    assert receipts == []


@pytest.mark.parametrize('case', ['committed', 'skipped', 'failed_after_callback'])
def test_ingest_worker_receipt_requires_successful_main_and_book_ids(monkeypatch, tmp_path, case):
    import windows_m0_ingest_worker as worker
    job_id, generation, attempt = 'a' * 32, 'nj1-' + 'b' * 32, '1'
    work = tmp_path / ('ingest-' + job_id + '-1-' + 'c' * 32)
    work.mkdir()
    source = work / 'input.epub'
    source.write_bytes(b'input fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    script = tmp_path / 'ingest_processor.py'
    if case == 'skipped':
        body = '    return 0\n'
    else:
        body = '    native_completion([1])\n    return ' + ('1' if case == 'failed_after_callback' else '0') + '\n'
    script.write_text('def main(filepath, *, native_completion):\n' + body, encoding='utf-8')
    monkeypatch.setenv('CWNG_NATIVE_JOB_GENERATION', generation)
    monkeypatch.setattr(worker, '__file__', str(tmp_path / 'windows_m0_ingest_worker.py'))
    monkeypatch.setattr(worker, 'require_native_writer_lease', lambda: None)
    monkeypatch.setattr(worker, 'install_origin_trace', lambda _: None)
    args = (str(script), str(source), job_id, attempt, digest, str(source.stat().st_size), str(work / 'worker.log'))
    if case == 'failed_after_callback':
        with pytest.raises(SystemExit):
            worker.main(*args)
    else:
        worker.main(*args)
    marker = work / 'ingest-complete.json'
    assert marker.exists() == (case == 'committed')
    if marker.exists():
        assert json.loads(marker.read_text(encoding='utf-8')) == {
            'schema': 1, 'job_id': job_id, 'generation': generation, 'attempt': 1,
            'sha256': digest, 'size': source.stat().st_size, 'book_ids': [1],
            'mode': 'original-epub-no-backup',
        }
