"""Windows controller admission checks against real profile locks and jobs."""

import json
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import zipfile
from unittest.mock import patch

import pytest

if os.name != 'nt':
    pytest.skip('Windows native controller', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_m0_coordinator import CoordinatorBusy
from windows_m0_activation import open_or_activate
from windows_m0_job import Job
from windows_m0_inbox import _volume_identity
from windows_m0_ledger import LedgerStateError
from windows_native_controller import NativeController
from windows_native_launcher import (_review_profile, _retry_unstarted_metadata, _reset_restart_budget,
                                     _confirm_epub_repair)


@pytest.fixture
def controller_profile(tmp_path):
    helper = os.environ.get('CWNG_NATIVE_TEST_HELPER')
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not helper or not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_HELPER and CWNG_NATIVE_TEST_RUNTIME for native proof')
    from werkzeug.security import generate_password_hash

    state, library, ingest = (tmp_path / name for name in ('state', 'library', 'ingest'))
    for directory in (state, library, ingest):
        directory.mkdir()
    (library / 'metadata.db').write_bytes(b'fixture library')
    app_db = state / 'app.db'
    db = sqlite3.connect(app_db)
    try:
        db.execute('CREATE TABLE settings (config_calibre_dir TEXT, config_calibre_split INTEGER)')
        db.execute('INSERT INTO settings VALUES (?, 0)', (str(library.resolve()),))
        db.execute('CREATE TABLE user (role INTEGER, password TEXT)')
        db.execute('INSERT INTO user VALUES (1, ?)', (generate_password_hash('admin123'),))
        db.commit()
    finally:
        db.close()
    controller = NativeController(helper=helper, state=state, runtime=runtime, library=library,
                                  ingest=ingest, port=18083, environment={'CWA_APP_DB_PATH': str(app_db)},
                                  web_entrypoint=Path(__file__).resolve().parents[2] / 'scripts/windows_native_web.py')
    return controller, app_db


@pytest.mark.parametrize('entry', ['file', 'directory'])
def test_maintenance_journal_blocks_start_before_web(controller_profile, entry):
    controller, app_db = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    if entry == 'file':
        journal.write_text('{incomplete', encoding='utf-8')
    else:
        journal.mkdir()
    before = app_db.read_bytes()
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        controller.start()
    assert controller.process is None
    assert not controller.generation_file.exists()
    assert app_db.read_bytes() == before
    assert journal.exists()


def test_inaccessible_maintenance_journal_fails_closed(controller_profile, monkeypatch):
    controller, app_db = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    original_stat = os.stat
    def stat_with_denial(path, *args, **kwargs):
        if os.fspath(path) == str(journal):
            raise PermissionError('journal status unavailable')
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', stat_with_denial)
    with pytest.raises(PermissionError, match='journal status unavailable'):
        controller.start()
    assert controller.process is None
    assert not controller.generation_file.exists()


def test_dangling_maintenance_journal_reparse_point_blocks_start(controller_profile):
    controller, _ = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    try:
        journal.symlink_to(controller.state / 'missing-target')
    except OSError as error:
        pytest.skip(f'creating a Windows symlink is unavailable: {error}')
    try:
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            controller.start()
        assert controller.process is None
    finally:
        journal.unlink()


def test_maintenance_journal_probe_does_not_follow_reparse_points(controller_profile, monkeypatch):
    controller, _ = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    original_stat = os.stat
    probes = []
    def stat_fake_entry(path, *args, **kwargs):
        if os.fspath(path) == str(journal):
            probes.append(kwargs.get('follow_symlinks'))
            return original_stat(controller.state)
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', stat_fake_entry)
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        controller.start()
    assert probes == [False]


def test_start_rechecks_after_maintenance_journal_is_resolved(controller_profile):
    controller, _ = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    journal.write_text('{}', encoding='utf-8')
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        controller.start()
    journal.unlink()
    with pytest.raises(ValueError, match='setup has not been completed'):
        controller.start()
    assert controller.process is None


def test_journal_appearing_during_owner_acquisition_blocks_start(controller_profile, monkeypatch):
    import windows_native_controller
    controller, _ = controller_profile
    journal = controller.state / 'maintenance-journal.json'
    activate = windows_native_controller.open_or_activate
    def activate_then_record(*args, **kwargs):
        local, existing = activate(*args, **kwargs)
        journal.write_text('{}', encoding='utf-8')
        return local, existing
    monkeypatch.setattr(windows_native_controller, 'open_or_activate', activate_then_record)
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        controller.start()
    assert controller.local is None
    assert controller.process is None
    assert journal.is_file()
    journal.unlink()
    owner, duplicate = activate(controller.helper, controller.state)
    try:
        assert duplicate is None
    finally:
        owner.close()


@pytest.mark.parametrize('existing_ledger', [False, True])
@pytest.mark.parametrize('existing_key', [False, True])
def test_maintenance_journal_after_lock_does_not_initialize_ledger_or_touch_control_key(
        controller_profile, monkeypatch, existing_ledger, existing_key):
    import windows_m0_coordinator
    from windows_m0_ledger import Ledger
    controller, _ = controller_profile
    state = controller.state
    journal = state / 'maintenance-journal.json'
    ledger = state / 'runtime-ledger.sqlite'
    if existing_ledger:
        Ledger(ledger)
    before = ledger.read_bytes() if existing_ledger else None
    key = state / 'control.key'
    if existing_key:
        key.write_bytes(b'unchanged fixture key')
    lock_type = windows_m0_coordinator.ProfileLock
    def acquire_then_create_journal(*args, **kwargs):
        lock = lock_type(*args, **kwargs)
        journal.write_bytes(b'pending maintenance')
        return lock
    monkeypatch.setattr(windows_m0_coordinator, 'ProfileLock', acquire_then_create_journal)
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        controller.start()
    assert ledger.exists() is existing_ledger
    if existing_ledger:
        assert ledger.read_bytes() == before
    assert key.exists() is existing_key
    if existing_key:
        assert key.read_bytes() == b'unchanged fixture key'
    assert controller.local is None
    assert controller.process is None
    assert journal.read_bytes() == b'pending maintenance'


def test_unreadable_journal_after_lock_does_not_open_ledger_or_touch_control_key(controller_profile, monkeypatch):
    import windows_m0_coordinator
    controller, _ = controller_profile
    state = controller.state
    journal = state / 'maintenance-journal.json'
    key = state / 'control.key'
    key.write_bytes(b'unchanged fixture key')
    lock_type = windows_m0_coordinator.ProfileLock
    original_stat = os.stat
    def deny_journal(path, *args, **kwargs):
        if os.fspath(path) == str(journal):
            raise PermissionError('journal status unavailable')
        return original_stat(path, *args, **kwargs)
    def acquire_then_deny(*args, **kwargs):
        lock = lock_type(*args, **kwargs)
        monkeypatch.setattr(os, 'stat', deny_journal)
        return lock
    monkeypatch.setattr(windows_m0_coordinator, 'ProfileLock', acquire_then_deny)
    with pytest.raises(PermissionError, match='journal status unavailable'):
        controller.start()
    assert not (state / 'runtime-ledger.sqlite').exists()
    assert key.read_bytes() == b'unchanged fixture key'
    assert controller.local is None
    assert controller.process is None


def test_maintenance_journal_blocks_restart_while_ownership_is_retained(controller_profile):
    controller, _ = controller_profile
    local, duplicate = open_or_activate(controller.helper, controller.state)
    assert duplicate is None
    controller.local = local
    journal = controller.state / 'maintenance-journal.json'
    journal.write_text('{}', encoding='utf-8')
    try:
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            controller.start()
        assert controller.local is local
        assert controller.process is None
    finally:
        journal.unlink()
        controller.close()


def test_maintenance_journal_blocks_duplicate_activation(controller_profile):
    controller, _ = controller_profile
    local, duplicate = open_or_activate(controller.helper, controller.state)
    assert duplicate is None
    journal = controller.state / 'maintenance-journal.json'
    journal.write_text('{}', encoding='utf-8')
    try:
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            controller.start()
    finally:
        journal.unlink()
        local.close()


def test_maintenance_journal_blocks_local_reconciliation(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(job, 'needs review')
    finally:
        owner.close()
    journal = controller.state / 'maintenance-journal.json'
    journal.write_text('{}', encoding='utf-8')
    try:
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            _retry_unstarted_metadata(controller, job)
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            _reset_restart_budget(controller, 'web')
        with pytest.raises(CoordinatorBusy, match='maintenance'):
            _review_profile(controller)
    finally:
        journal.unlink()
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_journal_appearing_during_offline_recovery_acquisition_is_not_ignored(controller_profile, monkeypatch):
    import windows_native_launcher
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(job, 'needs review')
    finally:
        owner.close()
    journal = controller.state / 'maintenance-journal.json'
    coordinator = windows_native_launcher.Coordinator
    def acquire_then_record(*args, **kwargs):
        acquired = coordinator(*args, **kwargs)
        journal.write_text('{}', encoding='utf-8')
        return acquired
    monkeypatch.setattr(windows_native_launcher, 'Coordinator', acquire_then_record)
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        _retry_unstarted_metadata(controller, job)
    assert journal.is_file()
    journal.unlink()
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_maintenance_journal_blocks_fresh_setup_ownership(controller_profile, monkeypatch):
    import windows_native_setup_db
    controller, _ = controller_profile
    monkeypatch.setattr(windows_native_setup_db, 'require_private_directory', lambda path: Path(path).resolve(strict=True))
    journal = controller.state / 'maintenance-journal.json'
    journal.write_text('{}', encoding='utf-8')
    with pytest.raises(CoordinatorBusy, match='maintenance'):
        with windows_native_setup_db.setup_ownership(controller.state, controller.helper):
            pytest.fail('setup ownership must not be granted')
    assert journal.is_file()


def test_incomplete_setup_and_default_admin_never_start_web(controller_profile):
    controller, app_db = controller_profile
    with pytest.raises(ValueError, match='setup has not been completed'):
        controller.start()
    marker = {'schema': 1, 'library': str(controller.library), 'ingest': str(controller.ingest),
              'app_db': str(app_db.resolve()),
              'library_volume': list(_volume_identity(controller.library)),
              'ingest_volume': list(_volume_identity(controller.ingest)),
              'state_volume': list(_volume_identity(controller.state)),
              'library_database': [(controller.library / 'metadata.db').stat().st_dev,
                                   (controller.library / 'metadata.db').stat().st_ino]}
    (controller.state / 'setup-complete.json').write_text(json.dumps(marker), encoding='utf-8')
    with pytest.raises(ValueError, match='administrator credentials'):
        controller.start()
    assert controller.process is None


def test_replaced_storage_is_rejected_before_web_start(controller_profile):
    controller, app_db = controller_profile
    info = (controller.library / 'metadata.db').stat()
    marker = {'schema': 1, 'library': str(controller.library), 'ingest': str(controller.ingest),
              'app_db': str(app_db.resolve()), 'library_volume': ['replacement', 123],
              'ingest_volume': list(_volume_identity(controller.ingest)),
              'state_volume': list(_volume_identity(controller.state)),
              'library_database': [info.st_dev, info.st_ino]}
    (controller.state / 'setup-complete.json').write_text(json.dumps(marker), encoding='utf-8')
    before = (controller.library / 'metadata.db').read_bytes()
    with pytest.raises(ValueError, match='storage or library identity changed'):
        controller.start()
    assert controller.process is None
    assert (controller.library / 'metadata.db').read_bytes() == before


def test_ledger_storage_mismatch_is_rejected_before_web_start(controller_profile):
    from werkzeug.security import generate_password_hash
    controller, app_db = controller_profile
    db = sqlite3.connect(app_db)
    try:
        db.execute('UPDATE user SET password=?', (generate_password_hash('new-secret'),))
        db.commit()
    finally:
        db.close()
    info = (controller.library / 'metadata.db').stat()
    marker = {'schema': 1, 'library': str(controller.library), 'ingest': str(controller.ingest),
              'app_db': str(app_db.resolve()), 'library_volume': list(_volume_identity(controller.library)),
              'ingest_volume': list(_volume_identity(controller.ingest)),
              'state_volume': list(_volume_identity(controller.state)),
              'library_database': [info.st_dev, info.st_ino]}
    (controller.state / 'setup-complete.json').write_text(json.dumps(marker), encoding='utf-8')
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    try:
        assert duplicate is None
        owner.owner.ledger.bind_profile('library-storage', {'different': 'previous volume'})
    finally:
        owner.close()
    before = app_db.read_bytes()
    with pytest.raises(LedgerStateError, match='storage identity changed'):
        controller.start()
    assert controller.process is None
    assert app_db.read_bytes() == before


def test_previous_live_web_generation_blocks_relaunch(controller_profile):
    controller, _ = controller_profile
    generation = 'nj1-' + 'b' * 32
    job = Job(controller.helper, generation=generation)
    try:
        (controller.state / 'web-generation').write_text(generation, encoding='ascii')
        with pytest.raises(CoordinatorBusy, match='previous web process'):
            controller.start()
    finally:
        job.close()
    with pytest.raises(ValueError, match='setup has not been completed'):
        controller.start()
    assert not (controller.state / 'web-generation').exists()


def test_control_thread_failure_stops_work_and_blocks_restart():
    controller = NativeController.__new__(NativeController)
    stopped = []
    controller.scheduler = SimpleNamespace(stop_safely=lambda: stopped.append(True))
    controller.local = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: False))
    with pytest.raises(CoordinatorBusy, match='local control stopped'):
        controller.step(timeout=0)
    assert stopped
    controller.scheduler = None
    with pytest.raises(CoordinatorBusy, match='local control stopped'):
        controller.start()


def test_local_review_reports_unresolved_work_without_replaying_it(controller_profile):
    controller, _ = controller_profile
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    assert duplicate is None
    job = owner.owner.enqueue('review-fixture', 'ingest', {'source': 'retained.epub'})
    owner.owner.ledger.review_pending(job, 'fixture requires verification')
    owner.close()
    result = _review_profile(controller)
    assert job in result and 'fixture requires verification' in result
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    try:
        assert duplicate is None
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def _prepare_recovery(controller, app_db):
    database = controller.library / 'metadata.db'
    database.unlink()
    db = sqlite3.connect(database)
    try:
        db.execute('CREATE TABLE books(id INTEGER PRIMARY KEY, path TEXT, title TEXT)')
        db.execute("INSERT INTO books VALUES (1, 'Book (1)', 'Fixture title')")
        db.execute('CREATE TABLE data(book INTEGER, format TEXT, name TEXT, uncompressed_size INTEGER)')
        db.commit()
    finally:
        db.close()
    info = database.stat()
    marker = {'schema': 1, 'library': str(controller.library), 'ingest': str(controller.ingest),
              'app_db': str(app_db.resolve()), 'library_volume': list(_volume_identity(controller.library)),
              'ingest_volume': list(_volume_identity(controller.ingest)),
              'state_volume': list(_volume_identity(controller.state)),
              'library_database': [info.st_dev, info.st_ino]}
    (controller.state / 'setup-complete.json').write_text(json.dumps(marker), encoding='utf-8')


def _create_metadata_epub(controller):
    book_dir = controller.library / 'Book (1)'
    book_dir.mkdir()
    (book_dir / 'book.epub').write_bytes(b'fixture book')
    with sqlite3.connect(controller.library / 'metadata.db') as db:
        db.execute("INSERT INTO data VALUES (1, 'EPUB', 'book', 12)")


def test_retry_only_requeues_unstarted_metadata_and_preserves_other_review(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    _create_metadata_epub(controller)
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    try:
        assert duplicate is None
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(job, 'needs retry')
        other = owner.owner.ledger.enqueue('unsafe', 'ingest', {'source': 'retained.epub'})
        owner.owner.ledger.review_pending(other, 'ambiguous ingestion')
    finally:
        owner.close()
    _retry_unstarted_metadata(controller, job)
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_metadata(controller, other)
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    try:
        assert duplicate is None
        assert owner.owner.ledger.get(job)['state'] == 'pending'
        assert owner.owner.ledger.get(other)['state'] == 'review'
    finally:
        owner.close()


@pytest.mark.parametrize('fault', ['no_epub', 'missing_file', 'unsafe_path'])
def test_retry_unstarted_metadata_rejects_unsupported_or_missing_epub(controller_profile, fault):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    if fault != 'no_epub':
        _create_metadata_epub(controller)
        if fault == 'missing_file':
            (controller.library / 'Book (1)' / 'book.epub').unlink()
        else:
            with sqlite3.connect(controller.library / 'metadata.db') as db:
                db.execute("UPDATE books SET path='../outside' WHERE id=1")
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(job, 'metadata needs review')
    finally:
        owner.close()
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_metadata(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_retry_rejects_duplicate_metadata_notification(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        original = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(original, 'needs retry')
        other = owner.owner.ledger.debounce_metadata(1, 456)
    finally:
        owner.close()
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_metadata(controller, original)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(original)['state'] == 'review'
        assert owner.owner.ledger.get(other)['state'] == 'pending'
    finally:
        owner.close()


def _reviewed_unstarted_ingest(controller):
    source = controller.ingest / 'book.epub'
    source.write_bytes(b'original input')
    stage_dir = controller.state / 'staged'
    stage_dir.mkdir(exist_ok=True)
    staged = stage_dir / 'book.epub'
    staged.write_bytes(source.read_bytes())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.enqueue('ingest-review', 'ingest', {
            'source': str(source), 'staged': str(staged), 'sha256': digest, 'size': source.stat().st_size,
        })
        owner.owner.ledger.review_pending(job, 'input requires review')
    finally:
        owner.close()
    return job, source, staged


def test_retry_unstarted_ingest_preserves_bytes_and_other_review(controller_profile):
    from windows_native_launcher import _retry_unstarted_ingest
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    job, source, staged = _reviewed_unstarted_ingest(controller)
    _retry_unstarted_ingest(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'pending'
        assert source.read_bytes() == staged.read_bytes() == b'original input'
    finally:
        owner.close()


@pytest.mark.parametrize('fault', ['source', 'staged', 'sidecar', 'started', 'duplicate'])
def test_retry_unstarted_ingest_rejects_changed_or_previously_started_input(controller_profile, fault):
    from windows_native_launcher import _retry_unstarted_ingest
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    job, source, staged = _reviewed_unstarted_ingest(controller)
    if fault == 'source':
        source.write_bytes(b'changed input')
    elif fault == 'staged':
        staged.write_bytes(b'changed staging')
    elif fault == 'sidecar':
        Path(str(source) + '.cwa.json').write_text('{}', encoding='utf-8')
    elif fault == 'duplicate':
        owner, _ = open_or_activate(controller.helper, controller.state)
        try:
            intent = owner.owner.ledger.get(job)['intent']
            owner.owner.ledger.enqueue('duplicate-ingest', 'ingest', intent)
        finally:
            owner.close()
    else:
        owner, _ = open_or_activate(controller.helper, controller.state)
        try:
            owner.owner.ledger.reconcile(job, outcome='retry', evidence='test')
            owner.owner.ledger.start(job, 'nj1-' + 'a' * 32)
            owner.owner.ledger.require_review(job, 'nj1-' + 'a' * 32, 'interrupted')
            owner.owner.ledger.reconcile(job, outcome='retry', evidence='test')
            owner.owner.ledger.review_pending(job, 'review again')
        finally:
            owner.close()
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_ingest(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_retry_rejects_metadata_record_with_started_generation(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.start(job, owner.owner.generation)
        owner.owner.fence_interrupted()
    finally:
        owner.close()
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_metadata(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_retry_rejects_metadata_even_if_previous_retry_cleared_generation(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.start(job, owner.owner.generation)
        owner.owner.fence_interrupted()
        owner.owner.ledger.reconcile(job, outcome='retry', evidence='fixture')
        owner.owner.ledger.review_pending(job, 'review again')
    finally:
        owner.close()
    with pytest.raises(CoordinatorBusy, match='could not be verified'):
        _retry_unstarted_metadata(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_retry_refuses_changed_storage_and_keeps_review(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.debounce_metadata(1, 123)
        owner.owner.ledger.review_pending(job, 'needs retry')
    finally:
        owner.close()
    marker_path = controller.state / 'setup-complete.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    marker['library_volume'] = ['changed-volume', 1]
    marker_path.write_text(json.dumps(marker), encoding='utf-8')
    with pytest.raises(ValueError, match='storage identity changed'):
        _retry_unstarted_metadata(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_reset_restart_budget_never_clears_review(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.enqueue('unsafe', 'ingest', {'source': 'retained.epub'})
        owner.owner.ledger.review_pending(job, 'ambiguous ingestion')
        for _ in range(4):
            owner.owner.ledger.restart_state('web', failure=True)
        assert owner.owner.ledger.restart_state('web')[0]
    finally:
        owner.close()
    _reset_restart_budget(controller, 'web')
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert not owner.owner.ledger.restart_state('web')[0]
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_control_restart_budget_can_be_reset_when_activation_is_paused(controller_profile):
    controller, app_db = controller_profile
    _prepare_recovery(controller, app_db)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        for _ in range(4):
            owner.owner.ledger.restart_state('control', failure=True)
        assert owner.owner.ledger.restart_state('control')[0]
    finally:
        owner.close()
    with pytest.raises(RuntimeError, match='control role is paused'):
        open_or_activate(controller.helper, controller.state)
    _reset_restart_budget(controller, 'control')
    owner, duplicate = open_or_activate(controller.helper, controller.state)
    try:
        assert duplicate is None
        assert not owner.owner.ledger.restart_state('control')[0]
    finally:
        owner.close()


def _epub_bytes(content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container/>')
        archive.writestr('content.xhtml', content)
    return output.getvalue()


def _reviewed_epub_repair(controller, app_db, *, changed):
    _prepare_recovery(controller, app_db)
    book_dir = controller.library / 'Book (1)'
    book_dir.mkdir()
    book = book_dir / 'book.epub'
    old, repaired = _epub_bytes('original'), _epub_bytes('repaired')
    book.write_bytes(repaired if changed else old)
    db = sqlite3.connect(controller.library / 'metadata.db')
    try:
        db.execute('INSERT INTO data VALUES (1, ?, ?, ?)', ('EPUB', 'book', book.stat().st_size))
        db.commit()
    finally:
        db.close()
    cwa = sqlite3.connect(controller.state / 'cwa.db')
    try:
        cwa.execute('CREATE TABLE cwa_settings(auto_ingest_folder_label_target TEXT)')
        cwa.execute("INSERT INTO cwa_settings VALUES ('disabled')")
        cwa.commit()
    finally:
        cwa.close()
    controller.environment['CWA_DB_PATH'] = str(controller.state)
    intent = {'book_id': 1, 'files': [{'book_id': 1, 'format': 'EPUB', 'path': 'Book (1)/book.epub',
                                       'sha256': hashlib.sha256(old).hexdigest()}]}
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        job = owner.owner.ledger.enqueue('epub-fixture', 'epub-fixer', intent)
        owner.owner.ledger.start(job, owner.owner.generation)
        owner.owner.fence_interrupted()
    finally:
        owner.close()
    attempt = controller.state / 'ingest-work' / ('epub-fixer-' + job + '-1')
    attempt.mkdir(parents=True)
    backup = attempt / '0-original.epub'
    backup.write_bytes(old)
    return job, book, backup


def test_completed_changed_epub_repair_is_verified_after_fencing(controller_profile):
    controller, app_db = controller_profile
    job, book, backup = _reviewed_epub_repair(controller, app_db, changed=True)
    before, original = book.read_bytes(), backup.read_bytes()
    _confirm_epub_repair(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'completed'
    finally:
        owner.close()
    assert book.read_bytes() == before and backup.read_bytes() == original


@pytest.mark.parametrize('fault', ['unchanged', 'backup', 'database', 'flush'])
def test_ambiguous_epub_repair_is_never_marked_completed(controller_profile, fault):
    controller, app_db = controller_profile
    job, book, backup = _reviewed_epub_repair(controller, app_db, changed=fault != 'unchanged')
    if fault == 'backup':
        backup.write_bytes(b'corrupt backup')
    if fault == 'database':
        db = sqlite3.connect(controller.library / 'metadata.db')
        try:
            db.execute("UPDATE data SET name='different'")
            db.commit()
        finally:
            db.close()
    if fault == 'flush':
        with patch('windows_m0_epub_fixer._flush_directory', side_effect=OSError('injected flush failure')):
            with pytest.raises(OSError, match='injected flush failure'):
                _confirm_epub_repair(controller, job)
    else:
        with pytest.raises((CoordinatorBusy, OSError)):
            _confirm_epub_repair(controller, job)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job)['state'] == 'review'
    finally:
        owner.close()


def test_live_epub_repair_generation_blocks_completion(controller_profile):
    controller, app_db = controller_profile
    job_id, _, _ = _reviewed_epub_repair(controller, app_db, changed=True)
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        generation = owner.owner.ledger.get(job_id)['generation']
    finally:
        owner.close()
    job = Job(controller.helper, generation=generation)
    try:
        with pytest.raises(CoordinatorBusy, match='could not be verified'):
            _confirm_epub_repair(controller, job_id)
    finally:
        job.close()
    owner, _ = open_or_activate(controller.helper, controller.state)
    try:
        assert owner.owner.ledger.get(job_id)['state'] == 'review'
    finally:
        owner.close()
