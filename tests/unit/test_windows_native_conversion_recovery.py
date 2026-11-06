"""Recovery of an interrupted whole-library conversion on Windows."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest

if os.name != 'nt':
    pytest.skip('Windows native conversion recovery', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_job import Job
from windows_native_conversion_recovery import confirm_conversion


def _epub():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container/>')
        archive.writestr('book.xhtml', '<html>converted</html>')
    return data.getvalue()


@pytest.fixture
def conversion(tmp_path):
    helper = os.environ.get('CWNG_NATIVE_TEST_HELPER')
    if not helper:
        pytest.skip('set CWNG_NATIVE_TEST_HELPER to the native helper DLL')
    state, library = tmp_path / 'state', tmp_path / 'library'
    state.mkdir()
    library.mkdir()
    work = state / 'ingest-work'
    work.mkdir()
    book = library / 'Author' / 'Title (1)'
    book.mkdir(parents=True)
    source = book / 'Book.pdf'
    target = book / 'Book.epub'
    source.write_bytes(b'original PDF bytes')
    target.write_bytes(_epub())
    database = library / 'metadata.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT)')
        db.execute('CREATE TABLE data (book INTEGER, name TEXT, format TEXT, uncompressed_size INTEGER)')
        db.execute('INSERT INTO books VALUES (1, ?)', ('Author/Title (1)',))
        db.execute('INSERT INTO data VALUES (1, ?, ?, ?)', ('Book', 'PDF', source.stat().st_size))
        db.execute('INSERT INTO data VALUES (1, ?, ?, ?)', ('Book', 'EPUB', target.stat().st_size))
    app_db = state / 'app.db'
    with sqlite3.connect(app_db) as db:
        db.execute('CREATE TABLE settings (config_calibre_dir TEXT, config_calibre_split INTEGER)')
        db.execute('INSERT INTO settings VALUES (?, 0)', (str(library),))
    cwa_db = state / 'cwa.db'
    with sqlite3.connect(cwa_db) as db:
        db.execute('CREATE TABLE cwa_settings (auto_ingest_folder_label_target TEXT)')
        db.execute("INSERT INTO cwa_settings VALUES ('disabled')")
    controller = SimpleNamespace(helper=Path(helper), state=state, library=library,
                                 environment={'CWA_APP_DB_PATH': str(app_db), 'CWA_DB_PATH': str(state)})
    owner = Coordinator(helper, state)
    intent = {'target': 'epub', 'expected': [1], 'sources': [
        {'path': 'Author/Title (1)/Book.pdf', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
    job_id = owner.ledger.enqueue('conversion-fixture', 'convert-library', intent)
    owner.ledger.start(job_id, owner.generation)
    owner.fence_interrupted()
    attempt = work / ('convert-library-' + job_id + '-1')
    attempt.mkdir()
    (attempt / 'conversion-complete.json').write_text(json.dumps({
        'schema': 1, 'job_id': job_id, 'generation': owner.generation, 'attempt': 1}), encoding='utf-8')
    (attempt / 'worker.log').write_text(
        '[convert-library]: Library conversion complete! 1 books converted! Exiting now...\n'
        'NextGen Convert Library Service - Run Ended: 2026-11-06 12:15:55\n', encoding='utf-8')
    try:
        yield SimpleNamespace(controller=controller, owner=owner, database=database, job_id=job_id,
                              source=source, target=target, attempt=attempt)
    finally:
        owner.close()


def test_verified_conversion_completes_without_changing_source(conversion):
    previous = conversion.source.read_bytes()
    confirm_conversion(conversion.controller, conversion.owner, conversion.database, conversion.job_id,
                       lambda: conversion.database)
    assert conversion.owner.ledger.get(conversion.job_id)['state'] == 'completed'
    assert conversion.source.read_bytes() == previous


@pytest.mark.parametrize('failure', ['log', 'partial-summary', 'backup-error', 'missing-marker', 'wrong-marker', 'boolean-marker',
                                     'cancel', 'durable-cancel', 'source', 'archive', 'size', 'flush', 'storage'])
def test_ambiguous_conversion_stays_in_review(conversion, failure, monkeypatch):
    if failure == 'log':
        (conversion.attempt / 'worker.log').write_text('Conversion started', encoding='utf-8')
    elif failure == 'partial-summary':
        (conversion.attempt / 'worker.log').write_text(
            '[convert-library]: Library conversion complete! 1 books converted! Exiting now...\n'
            '[convert-library]: Library conversion complete. 0 of 1 books converted; 1 failed, see the errors above. Exiting now...\n'
            'NextGen Convert Library Service - Run Ended: 2026-11-06 12:15:55\n', encoding='utf-8')
    elif failure == 'backup-error':
        log = conversion.attempt / 'worker.log'
        log.write_text('[convert-library]: ERROR - The following error occurred when trying to copy backup\n' +
                       log.read_text(encoding='utf-8'), encoding='utf-8')
    elif failure == 'missing-marker':
        (conversion.attempt / 'conversion-complete.json').unlink()
    elif failure == 'wrong-marker':
        (conversion.attempt / 'conversion-complete.json').write_text('{}', encoding='utf-8')
    elif failure == 'boolean-marker':
        (conversion.attempt / 'conversion-complete.json').write_text(json.dumps({
            'schema': True, 'job_id': conversion.job_id,
            'generation': conversion.owner.generation, 'attempt': True}), encoding='utf-8')
    elif failure == 'cancel':
        (conversion.attempt / 'cancel').touch()
    elif failure == 'durable-cancel':
        with sqlite3.connect(conversion.owner.ledger.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS cancel_requests (job_id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO cancel_requests VALUES (?)', (conversion.job_id,))
    elif failure == 'source':
        conversion.source.write_bytes(b'changed source')
    elif failure == 'archive':
        conversion.target.write_bytes(b'bad archive')
    elif failure == 'size':
        with sqlite3.connect(conversion.database) as db:
            db.execute("UPDATE data SET uncompressed_size=0 WHERE format='EPUB'")
    elif failure == 'flush':
        def failed_flush(_directory):
            raise OSError('injected flush failure')
        monkeypatch.setattr('windows_native_conversion_recovery._flush_directory', failed_flush)
    with pytest.raises((CoordinatorBusy, OSError, zipfile.BadZipFile)):
        confirm_conversion(conversion.controller, conversion.owner, conversion.database, conversion.job_id,
                           lambda: conversion.controller.state / 'app.db' if failure == 'storage' else conversion.database)
    assert conversion.owner.ledger.get(conversion.job_id)['state'] == 'review'


def test_live_old_job_cannot_be_completed(conversion):
    generation = conversion.owner.ledger.get(conversion.job_id)['generation']
    old_job = Job(conversion.controller.helper, generation=generation)
    try:
        with pytest.raises(CoordinatorBusy):
            confirm_conversion(conversion.controller, conversion.owner, conversion.database, conversion.job_id,
                               lambda: conversion.database)
    finally:
        old_job.close()
    assert conversion.owner.ledger.get(conversion.job_id)['state'] == 'review'


@pytest.mark.parametrize('exit_code', [0, 1])
def test_contained_wrapper_records_only_actual_success(conversion, tmp_path, exit_code):
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_RUNTIME to the private runtime')
    # A child may emit convincing-looking converter output before it fails.
    # Only the containing wrapper can publish the generation-bound marker.
    converter = tmp_path / 'fake-converter.py'
    converter.write_text(
        "print('[convert-library]: Library conversion complete! 1 books converted! Exiting now...')\n"
        "print('NextGen Convert Library Service - Run Ended: 2026-11-06 12:15:55')\n"
        f'raise SystemExit({exit_code})\n', encoding='utf-8')
    owner = conversion.owner
    owner.ledger.reconcile(conversion.job_id, outcome='retry', evidence='fixture retry')
    attempt = conversion.controller.state / 'ingest-work' / ('convert-library-' + conversion.job_id + '-2')
    attempt.mkdir()
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    worker = scripts / 'windows_m0_conversion_worker.py'
    bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                 "sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')")
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP'}}
    environment['CWA_METADATA_LOCK_DIR'] = str(conversion.controller.state)
    owner.start(conversion.job_id, Path(runtime) / 'Scripts' / 'python.exe',
                ['-I', '-c', bootstrap, str(scripts), str(worker), str(converter), conversion.job_id,
                 '2', str(attempt / 'worker.log')], cwd=conversion.controller.state,
                environment=environment, writer_lock_dir=conversion.controller.state)
    deadline = time.monotonic() + 30
    while True:
        try:
            owner.finish(conversion.job_id, evidence='fixture worker finished', verify=lambda: True)
            break
        except CoordinatorBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)
    assert 'Library conversion complete!' in (attempt / 'worker.log').read_text(encoding='utf-8')
    marker = attempt / 'conversion-complete.json'
    assert marker.exists() is (exit_code == 0)
    if marker.exists():
        assert json.loads(marker.read_text(encoding='utf-8')) == {
            'schema': 1, 'job_id': conversion.job_id, 'generation': owner.generation, 'attempt': 2}
    else:
        assert owner.ledger.get(conversion.job_id)['state'] == 'review'


def test_real_m0_conversion_backup_failure_stays_in_review(tmp_path):
    fixture = os.environ.get('CWNG_M0_CONVERSION_FIXTURE')
    helper = os.environ.get('CWNG_NATIVE_TEST_HELPER')
    if not fixture or not helper:
        pytest.skip('set CWNG_M0_CONVERSION_FIXTURE and CWNG_NATIVE_TEST_HELPER')
    fixture = Path(fixture).resolve(strict=True)
    with sqlite3.connect(fixture / 'state' / 'runtime-ledger.sqlite') as db:
        rows = db.execute("SELECT id,intent,attempts FROM jobs WHERE operation='convert-library' "
                          "AND state='completed'").fetchall()
    assert len(rows) == 1
    original_job, encoded, attempts = rows[0]
    intent = json.loads(encoded)
    assert attempts == 1 and len(intent['sources']) == 1 and len(intent['expected']) == 1
    state, library = tmp_path / 'state', tmp_path / 'library'
    state.mkdir()
    library.mkdir()
    with sqlite3.connect(fixture / 'lib' / 'metadata.db') as src, sqlite3.connect(library / 'metadata.db') as dst:
        src.backup(dst)
    with sqlite3.connect(library / 'metadata.db') as db:
        row = db.execute('SELECT books.path,data.name,data.format FROM books JOIN data ON books.id=data.book '
                         'WHERE books.id=? AND LOWER(data.format)=?', (intent['expected'][0], intent['target'])).fetchone()
    book_path, name, fmt = row
    target_relative = Path(book_path) / (name + '.' + fmt.lower())
    target = library / target_relative
    target.parent.mkdir(parents=True)
    shutil.copyfile(fixture / 'lib' / target_relative, target)
    original_hash = intent['sources'][0]['sha256']
    backups = [p for p in (fixture / 'state' / 'ingest-work').rglob('*-original.epub')
               if hashlib.sha256(p.read_bytes()).hexdigest() == original_hash]
    assert backups
    source = library / intent['sources'][0]['path']
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backups[0], source)
    app_db = state / 'app.db'
    with sqlite3.connect(app_db) as db:
        db.execute('CREATE TABLE settings (config_calibre_dir TEXT, config_calibre_split INTEGER)')
        db.execute('INSERT INTO settings VALUES (?, 0)', (str(library),))
    with sqlite3.connect(state / 'cwa.db') as db:
        db.execute('CREATE TABLE cwa_settings (auto_ingest_folder_label_target TEXT)')
        db.execute("INSERT INTO cwa_settings VALUES ('disabled')")
    controller = SimpleNamespace(helper=Path(helper), state=state, library=library,
                                 environment={'CWA_APP_DB_PATH': str(app_db), 'CWA_DB_PATH': str(state)})
    owner = Coordinator(helper, state)
    try:
        job_id = owner.ledger.enqueue('real-conversion-fixture', 'convert-library', intent)
        owner.ledger.start(job_id, owner.generation)
        owner.fence_interrupted()
        attempt = state / 'ingest-work' / ('convert-library-' + job_id + '-1')
        attempt.mkdir(parents=True)
        log = fixture / 'state' / 'ingest-work' / ('convert-library-' + original_job + '-1') / 'worker.log'
        shutil.copyfile(log, attempt / 'worker.log')
        # Inject a correctly-bound test marker to reach the independent real
        # log check: historical M0 runs intentionally had no such marker.
        (attempt / 'conversion-complete.json').write_text(json.dumps({
            'schema': 1, 'job_id': job_id, 'generation': owner.generation, 'attempt': 1}), encoding='utf-8')
        assert '[convert-library]: ERROR -' in log.read_text(encoding='utf-8', errors='replace')
        with pytest.raises(CoordinatorBusy):
            confirm_conversion(controller, owner, library / 'metadata.db', job_id, lambda: library / 'metadata.db')
        assert owner.ledger.get(job_id)['state'] == 'review'
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    finally:
        owner.close()
