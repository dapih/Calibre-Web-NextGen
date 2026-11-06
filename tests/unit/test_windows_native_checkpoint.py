"""Checkpoint behavior with real Windows profile locks and SQLite databases."""
import json
import csv
import hashlib
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

if os.name != 'nt':
    pytest.skip('Windows native checkpoint', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_m0_inbox import _volume_identity
from windows_m0_job import ProfileLock
from windows_native_controller import NativeController
from windows_native_checkpoint import (cancel_prepared, create_checkpoint, create_installed_checkpoint,
                                       maintenance_session, prepare_maintenance, verify_checkpoint,
                                       stage_guarded_activation, rollback_guarded_activation,
                                       _versioned_payload_identity)


@pytest.fixture
def checkpoint_profile(tmp_path):
    helper = os.environ.get('CWNG_NATIVE_TEST_HELPER')
    if not helper:
        pytest.skip('set CWNG_NATIVE_TEST_HELPER')
    state, library, ingest, recovery = (tmp_path / n for n in ('state', 'library', 'ingest', 'recovery'))
    for directory in (state, library, ingest, recovery, state / 'config'):
        directory.mkdir()
    app = state / 'config' / 'app.db'
    source = library / 'metadata.db'
    for path, value in ((app, 'private setting'), (state / 'config' / 'cwa.db', 'automation setting'), (source, 'library entry')):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE data (value TEXT)')
            db.execute('INSERT INTO data VALUES (?)', (value,))
    (state / 'config' / '.key').write_bytes(b'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    info = source.stat()
    marker = {'schema': 1, 'library': str(library), 'ingest': str(ingest), 'app_db': str(app),
              'library_volume': list(_volume_identity(library)), 'ingest_volume': list(_volume_identity(ingest)),
              'state_volume': list(_volume_identity(state)), 'library_database': [info.st_dev, info.st_ino]}
    (state / 'setup-complete.json').write_text(json.dumps(marker), encoding='utf-8')
    controller = SimpleNamespace(helper=Path(helper), state=state, library=library, ingest=ingest,
        generation_file=state / 'web-generation', environment={'CWA_APP_DB_PATH': str(app)})
    controller.require_no_maintenance_journal = NativeController.require_no_maintenance_journal
    controller._require_no_maintenance_journal = lambda: NativeController._require_no_maintenance_journal(controller)
    return controller, recovery


def test_checkpoint_captures_coherent_state_and_wal(checkpoint_profile):
    controller, recovery = checkpoint_profile
    library_db = controller.library / 'metadata.db'
    with sqlite3.connect(library_db) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute("INSERT INTO data VALUES ('wal entry')")
        live.commit()
        with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
            checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1')
        assert verify_checkpoint(checkpoint)['payload_identity'] == 'fixture-1'
        with sqlite3.connect(checkpoint / 'library' / 'metadata.db') as copied:
            assert copied.execute('SELECT value FROM data ORDER BY rowid').fetchall() == [('library entry',), ('wal entry',)]
    assert (checkpoint / 'state' / 'config' / '.key').read_bytes() == b'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
    assert not (checkpoint / 'state' / 'controller.lock').exists()


def test_live_controller_blocks_checkpoint_without_changing_user_files(checkpoint_profile):
    controller, recovery = checkpoint_profile
    before = (controller.library / 'metadata.db').read_bytes()
    with ProfileLock(controller.helper, controller.state / 'controller.lock'):
        with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
            with pytest.raises(OSError):
                create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert (controller.library / 'metadata.db').read_bytes() == before
    assert list(recovery.iterdir()) == []


def test_tampering_invalidates_completed_checkpoint(checkpoint_profile):
    controller, recovery = checkpoint_profile
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1')
    (checkpoint / 'state' / 'config' / '.key').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash'):
        verify_checkpoint(checkpoint)


def test_full_checkpoint_includes_books_and_empty_directories(checkpoint_profile):
    controller, recovery = checkpoint_profile
    (controller.library / 'Author').mkdir()
    (controller.library / 'Author' / 'book.epub').write_bytes(b'book fixture')
    (controller.state / 'custom-assets').mkdir()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1', full=True)
    assert (checkpoint / 'library' / 'Author' / 'book.epub').read_bytes() == b'book fixture'
    assert (checkpoint / 'state' / 'custom-assets').is_dir()
    assert verify_checkpoint(checkpoint, expected_payload_identity='fixture-1')['scope'] == 'full'
    with pytest.raises(ValueError, match='payload identity'):
        verify_checkpoint(checkpoint, expected_payload_identity='other')
    (checkpoint / 'state' / 'custom-assets').rmdir()
    with pytest.raises(ValueError, match='missing or unexpected'):
        verify_checkpoint(checkpoint)


def test_failed_copy_retains_originals_and_never_publishes(checkpoint_profile):
    controller, recovery = checkpoint_profile
    before = (controller.library / 'metadata.db').read_bytes()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with patch('windows_native_checkpoint._copy_file', side_effect=OSError('disk full')):
            with pytest.raises(OSError, match='disk full'):
                create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert (controller.library / 'metadata.db').read_bytes() == before
    assert all(path.name.startswith('pending-') for path in recovery.iterdir())
    assert not list(recovery.glob('checkpoint-*'))


def test_maintenance_session_keeps_ownership_after_checkpoint(checkpoint_profile):
    controller, recovery = checkpoint_profile
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1', session=session)
            assert verify_checkpoint(checkpoint, expected_payload_identity='fixture-1')
            with pytest.raises(OSError):
                ProfileLock(controller.helper, controller.state / 'controller.lock')
        with ProfileLock(controller.helper, controller.state / 'controller.lock'):
            pass
        with pytest.raises(ValueError, match='active maintenance'):
            create_checkpoint(controller, recovery, payload_identity='fixture-1', session=session)


def test_unresolved_maintenance_journal_blocks_new_checkpoint(checkpoint_profile):
    controller, recovery = checkpoint_profile
    (controller.state / 'maintenance-journal.json').write_bytes(b'incomplete journal')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with pytest.raises(RuntimeError, match='maintenance'):
            create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert list(recovery.iterdir()) == []


def test_full_checkpoint_preserves_sqlite_shaped_books_and_lock_named_files(checkpoint_profile):
    controller, recovery = checkpoint_profile
    library = controller.library
    book = library / 'book.kfx'
    book.write_bytes(b'SQLite format 3\x00' + b'book bytes, not an application database')
    (library / 'book.kfx-wal').write_bytes(b'book sidecar')
    (library / 'controller.lock').write_bytes(b'book with a lock name')
    (library / '.cwng-native-writer.lock').write_bytes(b'another book with a lock name')
    (controller.state / 'config' / 'custom').mkdir()
    (controller.state / 'config' / 'custom' / '.cwng-native-writer.lock').write_bytes(b'custom asset')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1', full=True)
    for name in ('book.kfx', 'book.kfx-wal', 'controller.lock', '.cwng-native-writer.lock'):
        assert (checkpoint / 'library' / name).read_bytes() == (library / name).read_bytes()
    assert (checkpoint / 'state' / 'config' / 'custom' / '.cwng-native-writer.lock').read_bytes() == b'custom asset'


def test_silent_truncated_copy_never_publishes(checkpoint_profile):
    controller, recovery = checkpoint_profile
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with patch('windows_native_checkpoint.shutil.copyfileobj', side_effect=lambda src, dst, length: dst.write(src.read(1))):
            with pytest.raises(ValueError, match='copy'):
                create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert not list(recovery.glob('checkpoint-*'))


def test_unknown_state_sqlite_with_wal_requires_explicit_coverage(checkpoint_profile):
    controller, recovery = checkpoint_profile
    unknown = controller.state / 'config' / 'unknown.db'
    with sqlite3.connect(unknown) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE data (value TEXT)')
        connection.execute("INSERT INTO data VALUES ('committed in WAL')")
        connection.commit()
        with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
            with pytest.raises(ValueError, match='unknown SQLite'):
                create_checkpoint(controller, recovery, payload_identity='fixture-1')
        assert connection.execute('SELECT value FROM data').fetchone() == ('committed in WAL',)
    assert not list(recovery.glob('checkpoint-*'))


def test_missing_key_never_produces_verified_checkpoint(checkpoint_profile):
    controller, recovery = checkpoint_profile
    (controller.state / 'config' / '.key').unlink()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with pytest.raises(ValueError, match='encryption key'):
            create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert not list(recovery.glob('checkpoint-*'))


def test_invalid_key_never_produces_verified_checkpoint(checkpoint_profile):
    controller, recovery = checkpoint_profile
    (controller.state / 'config' / '.key').write_bytes(b'not-a-valid-key')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with pytest.raises(ValueError, match='encryption key'):
            create_checkpoint(controller, recovery, payload_identity='fixture-1')
    assert not list(recovery.glob('checkpoint-*'))


def test_verifier_rejects_manifest_missing_application_database(checkpoint_profile):
    controller, recovery = checkpoint_profile
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        checkpoint = create_checkpoint(controller, recovery, payload_identity='fixture-1')
    manifest_path = checkpoint / 'checkpoint.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files'] = [entry for entry in manifest['files'] if entry['path'] != 'state/config/app.db']
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    (checkpoint / 'state' / 'config' / 'app.db').unlink()
    with pytest.raises(ValueError, match='missing required data'):
        verify_checkpoint(checkpoint)


def _installed_payload(controller, tmp_path):
    program = tmp_path / 'program'
    payload = program / 'versions' / '1.0.0'
    (payload / 'scripts').mkdir(parents=True)
    (program / 'active-version.txt').write_text('1.0.0\n', encoding='ascii')
    (program / 'CWNG.exe').write_bytes(b'bootstrap-fixture')
    (payload / 'native-package.json').write_text(json.dumps({'schema': 1, 'repository': 'dapih/Calibre-Web-NextGen',
        'version': '1.0.0', 'revision': 0, 'architecture': 'x64'}), encoding='utf-8')
    controller.web_entrypoint = payload / 'scripts' / 'windows_native_web.py'
    controller.web_entrypoint.write_text('# fixture\n', encoding='utf-8')
    for name in ('runtime', 'calibre', 'imagemagick', 'kepubify'):
        (payload / name).mkdir()
    (payload / 'cwng-native.dll').write_bytes(b'fixture helper')
    (payload / 'kepubify' / 'kepubify.exe').write_bytes(b'fixture tool')
    profile = {'schema': 1, 'library': str(controller.library), 'ingest': str(controller.ingest),
        'port': 8083, 'runtime': str(payload / 'runtime'), 'helper': str(payload / 'cwng-native.dll'),
        'calibre': str(payload / 'calibre'), 'kepubify': str(payload / 'kepubify' / 'kepubify.exe'),
        'magick': str(payload / 'imagemagick')}
    (controller.state / 'native-profile.json').write_text(json.dumps(profile), encoding='utf-8')
    return program


def _candidate(controller, program):
    source = program / 'versions' / '1.0.0'
    candidate = program / 'versions' / '1.0.1'
    shutil.copytree(source, candidate)
    package = json.loads((candidate / 'native-package.json').read_text(encoding='utf-8'))
    package['version'] = '1.0.1'
    (candidate / 'native-package.json').write_text(json.dumps(package), encoding='utf-8')
    return _versioned_payload_identity(controller, program, '1.0.1')


@pytest.mark.skipif(not os.environ.get('CWNG_NATIVE_RECOVERY_PROOF'), reason='large private runtime proof')
def test_old_payload_recovery_cli_after_switch_process_death(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    root = Path(__file__).resolve().parents[2]
    program = _installed_payload(controller, tmp_path)
    old = program / 'versions' / '1.0.0'
    shutil.rmtree(old / 'scripts')
    shutil.copytree(root / 'scripts', old / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (old / 'cps' / 'services').mkdir(parents=True)
    shutil.copy2(root / 'cps' / 'services' / 'file_lock.py', old / 'cps' / 'services' / 'file_lock.py')
    shutil.rmtree(old / 'runtime')
    shutil.copytree(Path(os.environ['CWNG_NATIVE_PBS_RUNTIME']), old / 'runtime',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    native_proof_parent = os.environ.get('CWNG_NATIVE_PROOF_PARENT')
    if native_proof_parent:
        shutil.copy2(root / 'tests/fixtures/windows_native_recovery_adoption_proof.py',
                     old / 'runtime/windows_native_recovery_proof_adapter.py')
    shutil.copy2(controller.helper, old / 'cwng-native.dll')
    controller.helper = old / 'cwng-native.dll'
    candidate = program / 'versions' / '1.0.1'
    for directory in ('scripts', 'runtime/Scripts', 'calibre', 'imagemagick', 'kepubify'):
        (candidate / directory).mkdir(parents=True)
    for name in ('scripts/windows_native_web.py', 'runtime/Scripts/python.exe',
                 'cwng-native.dll', 'kepubify/kepubify.exe'):
        (candidate / name).write_bytes(b'candidate must not execute')
    (candidate / 'native-package.json').write_text(json.dumps({'schema': 1, 'repository': 'dapih/Calibre-Web-NextGen',
        'version': '1.0.1', 'revision': 0, 'architecture': 'x64'}), encoding='utf-8')
    python = old / 'runtime' / 'Scripts' / 'python.exe'
    entry = old / 'scripts' / 'windows_native_maintenance_recovery.py'
    for flags in (('-I',), ('-B',)):
        denied = subprocess.run([str(python), *flags, str(entry)], cwd=tmp_path, capture_output=True, text=True)
        assert denied.returncode and 'isolated original private interpreter' in denied.stderr
    venv_config = old / 'runtime' / 'pyvenv.cfg'
    venv_config.write_text('home = invalid\n', encoding='ascii')
    try:
        denied = subprocess.run([str(python), '-I', '-B', str(entry)], cwd=tmp_path, capture_output=True, text=True)
        assert denied.returncode
    finally:
        venv_config.unlink()
    assert not (controller.state / 'maintenance-journal.json').exists()
    expected = _versioned_payload_identity(controller, program, '1.0.1')
    old_profile = (controller.state / 'native-profile.json').read_bytes()
    old_database = (controller.library / 'metadata.db').read_bytes()
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    for directory in (controller.state, recovery):
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
                        '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                        '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    source = r'''
import os, pathlib, sys
root, state, library, ingest, recovery, program, helper = map(pathlib.Path, sys.argv[1:8])
candidate = sys.argv[8]
sys.path.insert(0, str(root / 'scripts'))
from types import SimpleNamespace
from windows_native_checkpoint import maintenance_session, prepare_maintenance, stage_guarded_activation
from windows_native_controller import NativeController
c = SimpleNamespace(helper=helper, state=state, library=library, ingest=ingest,
    generation_file=state / 'web-generation', environment={'CWA_APP_DB_PATH': str(state / 'config/app.db')},
    web_entrypoint=program / 'versions/1.0.0/scripts/windows_native_web.py')
c.require_no_maintenance_journal = NativeController.require_no_maintenance_journal
c._require_no_maintenance_journal = lambda: NativeController._require_no_maintenance_journal(c)
with maintenance_session(c) as session:
    prepare_maintenance(c, recovery, program, session=session)
    stage_guarded_activation(c, program, recovery, '1.0.1', candidate, session=session,
                             _fault=lambda point: os._exit(77) if point == 'pointer' else None)
'''
    result = subprocess.run([str(python), '-I', '-B', '-c', source, str(root), str(controller.state),
        str(controller.library), str(controller.ingest), str(recovery), str(program), str(controller.helper), expected],
        cwd=tmp_path, timeout=300, capture_output=True, text=True)
    assert result.returncode == 77, result.stderr
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.1'
    if native_proof_parent:
        parent = Path(native_proof_parent)
        # The disposable harness is the independent trust root for this test.
        expected_parent = os.environ['CWNG_NATIVE_PROOF_PARENT_SHA256']
        assert hashlib.sha256(parent.read_bytes()).hexdigest() == expected_parent
        journal = json.loads((controller.state / 'maintenance-journal.json').read_text(encoding='utf-8'))
        inventory = recovery / (journal['checkpoint'] + '.inventory')
        correct_inventory = inventory.read_bytes()
        inventory.write_bytes(correct_inventory + b'bad')
        rejected = subprocess.run([str(parent), str(controller.state), str(program), str(recovery)],
                                  cwd=tmp_path, timeout=300, capture_output=True, text=True)
        assert rejected.returncode != 0
        assert (controller.state / 'maintenance-journal.json').exists()
        assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.1'
        inventory.write_bytes(correct_inventory)
        # The native parent must reject a writable anchor before starting any Python.
        subprocess.run(['icacls', str(inventory), '/grant', '*S-1-1-0:(W)'], check=True, capture_output=True)
        try:
            rejected = subprocess.run([str(parent), str(controller.state), str(program), str(recovery)],
                                      cwd=tmp_path, timeout=300, capture_output=True, text=True)
            assert rejected.returncode != 0
            assert (controller.state / 'maintenance-journal.json').exists()
        finally:
            subprocess.run(['icacls', str(inventory), '/remove:g', '*S-1-1-0'], check=True, capture_output=True)
        checked_file = old / 'scripts/windows_native_web.py'
        subprocess.run(['icacls', str(checked_file), '/grant', '*S-1-1-0:(W)'], check=True, capture_output=True)
        try:
            rejected = subprocess.run([str(parent), str(controller.state), str(program), str(recovery)],
                                      cwd=tmp_path, timeout=300, capture_output=True, text=True)
            assert rejected.returncode != 0
            assert (controller.state / 'maintenance-journal.json').exists()
        finally:
            subprocess.run(['icacls', str(checked_file), '/remove:g', '*S-1-1-0'], check=True, capture_output=True)
        # Read-only access by other users does not compromise installed code.
        subprocess.run(['icacls', str(checked_file), '/grant', '*S-1-1-0:(R)'], check=True, capture_output=True)
        poisoned = dict(os.environ, PYTHONPATH=str(tmp_path / 'hostile'), PYTHONHOME=str(tmp_path / 'hostile'),
                        QT_PLUGIN_PATH=str(tmp_path / 'hostile'), MAGICK_HOME=str(tmp_path / 'hostile'), PATH=str(tmp_path))
        result = subprocess.run([str(parent), str(controller.state), str(program), str(recovery)],
                                cwd=tmp_path, timeout=300, capture_output=True, text=True, env=poisoned)
    else:
        result = subprocess.run([str(python), '-I', '-B', str(old / 'scripts/windows_native_maintenance_recovery.py'),
            '--state', str(controller.state), '--program-root', str(program), '--recovery', str(recovery)],
            cwd=tmp_path, timeout=300, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'
    assert (controller.state / 'native-profile.json').read_bytes() == old_profile
    assert (controller.library / 'metadata.db').read_bytes() == old_database
    assert not (controller.state / 'maintenance-journal.json').exists()


def test_guarded_version_switch_can_roll_back_before_any_resume(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    expected = _candidate(controller, program)
    old_profile = (controller.state / 'native-profile.json').read_bytes()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            stage_guarded_activation(controller, program, recovery, '1.0.1', expected, session=session)
            assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.1'
            assert (controller.state / 'maintenance-journal.json').is_file()
        with pytest.raises(RuntimeError, match='maintenance'):
            NativeController.require_no_maintenance_journal(controller.state)
        rollback_guarded_activation(controller, program, recovery)
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'
    assert (controller.state / 'native-profile.json').read_bytes() == old_profile
    assert not (controller.state / 'maintenance-journal.json').exists()


def test_guarded_rollback_adopts_existing_exclusive_gate(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    expected = _candidate(controller, program)
    original = (controller.state / 'native-profile.json').read_bytes()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            stage_guarded_activation(controller, program, recovery, '1.0.1', expected, session=session)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        handle = kernel.CreateFileW(str(controller.state / 'bootstrap-maintenance.lock'), 0xc0000000, 0,
                                    None, 3, 0x00200080, None)
        assert handle != ctypes.c_void_p(-1).value
        rollback_guarded_activation(controller, program, recovery, _inherited_gate=handle)
    assert (controller.state / 'native-profile.json').read_bytes() == original
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'
    assert not (controller.state / 'maintenance-journal.json').exists()


def test_guarded_switch_rejects_candidate_hash_mismatch(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    _candidate(controller, program)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            with pytest.raises(ValueError, match='candidate'):
                stage_guarded_activation(controller, program, recovery, '1.0.1', 'sha256:' + '0' * 64,
                                         session=session)
        cancel_prepared(controller, program, recovery)
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'


@pytest.mark.parametrize('point', ['prepare-temp', 'journal-temp', 'journal', 'profile-temp', 'profile', 'pointer-temp', 'pointer'])
def test_guarded_switch_survives_real_process_death(checkpoint_profile, tmp_path, point):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    candidate = _candidate(controller, program)
    old_profile = (controller.state / 'native-profile.json').read_bytes()
    old_database = (controller.library / 'metadata.db').read_bytes()
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_RUNTIME for interrupted-process proof')
    source = r'''
import os, pathlib, sys
from types import SimpleNamespace
root, state, library, ingest, recovery, program, helper = map(pathlib.Path, sys.argv[1:8])
point, candidate = sys.argv[8:10]
sys.path.insert(0, str(root / 'scripts'))
import windows_native_checkpoint as checkpoints
from windows_native_controller import NativeController
checkpoints.require_private_directory = lambda p: pathlib.Path(p).resolve(strict=True)
controller = SimpleNamespace(helper=helper, state=state, library=library, ingest=ingest,
    generation_file=state / 'web-generation', environment={'CWA_APP_DB_PATH': str(state / 'config/app.db')},
    web_entrypoint=program / 'versions/1.0.0/scripts/windows_native_web.py')
controller.require_no_maintenance_journal = NativeController.require_no_maintenance_journal
controller._require_no_maintenance_journal = lambda: NativeController._require_no_maintenance_journal(controller)
def crash(current):
    if current == point:
        os._exit(77)
with checkpoints.maintenance_session(controller) as session:
    checkpoints.prepare_maintenance(controller, recovery, program, session=session, _fault=crash)
    checkpoints.stage_guarded_activation(controller, program, recovery, '1.0.1', candidate,
                                         session=session, _fault=crash)
raise RuntimeError('fault point was not reached')
'''
    root = Path(__file__).resolve().parents[2]
    python = Path(runtime) / 'Scripts' / 'python.exe'
    result = subprocess.run([str(python), '-I', '-c', source, str(root), str(controller.state),
        str(controller.library), str(controller.ingest), str(recovery), str(program), str(controller.helper),
        point, candidate], cwd=root, timeout=60, capture_output=True, text=True)
    assert result.returncode == 77, result.stderr
    if point == 'prepare-temp':
        assert not (controller.state / 'maintenance-journal.json').exists()
        NativeController.require_no_maintenance_journal(controller.state)
        assert (controller.state / 'native-profile.json').read_bytes() == old_profile
        assert (controller.library / 'metadata.db').read_bytes() == old_database
        return
    assert (controller.state / 'maintenance-journal.json').is_file()
    with pytest.raises(RuntimeError, match='maintenance'):
        NativeController.require_no_maintenance_journal(controller.state)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        if point == 'journal-temp':
            cancel_prepared(controller, program, recovery)
        else:
            rollback_guarded_activation(controller, program, recovery)
    assert not (controller.state / 'maintenance-journal.json').exists()
    assert (controller.state / 'native-profile.json').read_bytes() == old_profile
    assert (controller.library / 'metadata.db').read_bytes() == old_database
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'


def test_guarded_rollback_refuses_unexpected_database_change(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    candidate = _candidate(controller, program)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            stage_guarded_activation(controller, program, recovery, '1.0.1', candidate, session=session)
        with sqlite3.connect(controller.library / 'metadata.db') as db:
            db.execute("INSERT INTO data VALUES ('unexpected later work')")
        with pytest.raises(ValueError, match='state'):
            rollback_guarded_activation(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').is_file()
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.1'


def test_prepared_cancel_preserves_substituted_exact_name_file(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        record = json.loads((controller.state / 'maintenance-journal.json').read_text(encoding='utf-8'))
        reserved = controller.state / ('.cwng-activation-' + record['transaction_id'] + '-profile-new.tmp')
        replacement = tmp_path / 'user-file'
        replacement.write_bytes(b'')
        os.replace(replacement, reserved)
        assert list((reserved.stat().st_dev, reserved.stat().st_ino)) != record['temp_ids']['profile-new']
        with pytest.raises(ValueError, match='temporary file'):
            cancel_prepared(controller, program, recovery)
    assert reserved.read_bytes() == b''
    assert (controller.state / 'maintenance-journal.json').is_file()


@pytest.mark.parametrize('point', ['rollback-cleanup-journal', 'rollback-profile-temp', 'rollback-pointer-temp'])
def test_rollback_reenters_after_real_process_death_inside_replace(checkpoint_profile, tmp_path, point):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    candidate = _candidate(controller, program)
    old_profile = (controller.state / 'native-profile.json').read_bytes()
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_RUNTIME for interrupted-process proof')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            stage_guarded_activation(controller, program, recovery, '1.0.1', candidate, session=session)
        source = r'''
import os, pathlib, sys
from types import SimpleNamespace
root, state, library, ingest, recovery, program, helper = map(pathlib.Path, sys.argv[1:8])
point = sys.argv[8]
sys.path.insert(0, str(root / 'scripts'))
import windows_native_checkpoint as checkpoints
checkpoints.require_private_directory = lambda p: pathlib.Path(p).resolve(strict=True)
controller = SimpleNamespace(helper=helper, state=state, library=library, ingest=ingest,
    environment={'CWA_APP_DB_PATH': str(state / 'config/app.db')},
    web_entrypoint=program / 'versions/1.0.0/scripts/windows_native_web.py')
def crash(current):
    if current == point:
        os._exit(77)
checkpoints.rollback_guarded_activation(controller, program, recovery, _fault=crash)
raise RuntimeError('rollback fault point was not reached')
'''
        root = Path(__file__).resolve().parents[2]
        python = Path(runtime) / 'Scripts' / 'python.exe'
        result = subprocess.run([str(python), '-I', '-c', source, str(root), str(controller.state),
            str(controller.library), str(controller.ingest), str(recovery), str(program), str(controller.helper), point],
            cwd=root, timeout=60, capture_output=True, text=True)
        assert result.returncode == 77, result.stderr
        assert (controller.state / 'maintenance-journal.json').is_file()
        rollback_guarded_activation(controller, program, recovery)
    assert not (controller.state / 'maintenance-journal.json').exists()
    assert (controller.state / 'native-profile.json').read_bytes() == old_profile
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.0'


def test_prepared_cancel_reenters_after_real_process_death_during_cleanup(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_RUNTIME for interrupted-process proof')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        source = r'''
import os, pathlib, sys
from types import SimpleNamespace
root, state, library, ingest, recovery, program, helper = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root / 'scripts'))
import windows_native_checkpoint as checkpoints
checkpoints.require_private_directory = lambda p: pathlib.Path(p).resolve(strict=True)
controller = SimpleNamespace(helper=helper, state=state, library=library, ingest=ingest,
    environment={'CWA_APP_DB_PATH': str(state / 'config/app.db')},
    web_entrypoint=program / 'versions/1.0.0/scripts/windows_native_web.py')
def crash(point):
    if point == 'cleanup-journal':
        os._exit(77)
checkpoints.cancel_prepared(controller, program, recovery, _fault=crash)
raise RuntimeError('cleanup fault point was not reached')
'''
        root = Path(__file__).resolve().parents[2]
        python = Path(runtime) / 'Scripts' / 'python.exe'
        result = subprocess.run([str(python), '-I', '-c', source, str(root), str(controller.state),
            str(controller.library), str(controller.ingest), str(recovery), str(program), str(controller.helper)],
            cwd=root, timeout=60, capture_output=True, text=True)
        assert result.returncode == 77, result.stderr
        assert (controller.state / 'maintenance-journal.json').is_file()
        cancel_prepared(controller, program, recovery)
    assert not (controller.state / 'maintenance-journal.json').exists()


def test_installed_checkpoint_binds_actual_active_payload(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            checkpoint = create_installed_checkpoint(controller, recovery, program, session=session)
    identity = verify_checkpoint(checkpoint)['payload_identity']
    assert identity.startswith('sha256:') and len(identity) == 71
    controller.web_entrypoint.write_text('# changed\n', encoding='utf-8')
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            second = create_installed_checkpoint(controller, recovery, program, session=session)
    assert verify_checkpoint(second)['payload_identity'] != identity


def test_payload_change_during_checkpoint_prevents_publication(checkpoint_profile, tmp_path):
    import windows_native_checkpoint as checkpoints
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    copy_tree = checkpoints._copy_tree
    def change_after_copy(*args, **kwargs):
        result = copy_tree(*args, **kwargs)
        controller.web_entrypoint.write_text('# changed during snapshot\n', encoding='utf-8')
        return result
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with patch('windows_native_checkpoint._copy_tree', side_effect=change_after_copy):
            with maintenance_session(controller) as session:
                with pytest.raises(ValueError, match='payload changed'):
                    create_installed_checkpoint(controller, recovery, program, session=session)
    assert not list(recovery.glob('checkpoint-*'))


def test_installed_identity_includes_bootstrap(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            first = create_installed_checkpoint(controller, recovery, program, session=session)
        (program / 'CWNG.exe').write_bytes(b'changed-bootstrap')
        with maintenance_session(controller) as session:
            second = create_installed_checkpoint(controller, recovery, program, session=session)
    assert verify_checkpoint(first)['payload_identity'] != verify_checkpoint(second)['payload_identity']


def test_prepared_transaction_blocks_until_verified_cancel(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    assert not (controller.state / 'tmp').exists()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            checkpoint = prepare_maintenance(controller, recovery, program, session=session)
            assert checkpoint.is_dir()
            assert (controller.state / 'maintenance-journal.json').is_file()
        with pytest.raises(RuntimeError, match='maintenance'):
            with maintenance_session(controller):
                pass
        cancel_prepared(controller, program, recovery)
    assert not (controller.state / 'maintenance-journal.json').exists()
    assert not (controller.state / 'tmp').exists()
    assert checkpoint.is_dir()


def test_prepared_cancel_rejects_changed_payload_or_state(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        original = controller.web_entrypoint.read_bytes()
        controller.web_entrypoint.write_bytes(b'changed payload')
        with pytest.raises(ValueError, match='payload'):
            cancel_prepared(controller, program, recovery)
        controller.web_entrypoint.write_bytes(original)
        (controller.state / 'new-user-file').write_bytes(b'important')
        with pytest.raises(ValueError, match='state'):
            cancel_prepared(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').is_file()


def test_prepared_inventory_has_native_verifiable_anchor(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    repo = Path(__file__).resolve().parents[2]
    compiler = repo / '.m0/mingw64/bin/gcc.exe'
    if not compiler.is_file():
        pytest.skip('private compiler is unavailable')
    verifier = tmp_path / 'verifier.exe'
    subprocess.run([str(compiler), '-std=c11', '-municode', '-static', '-Wall', '-Wextra', '-Werror',
                    str(repo / 'scripts/windows-native-recovery-inventory.c'), '-lbcrypt', '-o', str(verifier)], check=True)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            checkpoint = prepare_maintenance(controller, recovery, program, session=session)
        record = json.loads((controller.state / 'maintenance-journal.json').read_text(encoding='utf-8'))
        inventory = recovery / (checkpoint.name + '.inventory')
        subprocess.run([str(verifier), str(program / 'versions' / record['inventory_version']), str(inventory),
                        record['inventory_sha256']], check=True, timeout=20)
        original = inventory.read_bytes()
        inventory.write_bytes(original + b'changed')
        with pytest.raises(ValueError, match='inventory changed'):
            cancel_prepared(controller, program, recovery)
        assert (controller.state / 'maintenance-journal.json').exists()
        inventory.write_bytes(original)
        cancel_prepared(controller, program, recovery)


def test_native_recovery_parent_preflight_without_running_candidate(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    root = Path(__file__).resolve().parents[2]
    compiler = root / '.m0/mingw64/bin/gcc.exe'
    if not compiler.is_file():
        pytest.skip('private compiler unavailable')
    program = _installed_payload(controller, tmp_path)
    old = program / 'versions/1.0.0'
    (old / 'runtime/Scripts').mkdir()
    fake_source = tmp_path / 'fake-old.c'
    fake_source.write_text(r'''
#include <windows.h>
#include <stdio.h>
int wmain(int argc, wchar_t **argv) {
    wchar_t ready[32768], release[32768];
    if (argc < 6) return 1;
    if (swprintf(ready, 32768, L"%ls\\proof-child-ready", argv[5]) < 0 ||
        swprintf(release, 32768, L"%ls\\proof-child-release", argv[5]) < 0) return 1;
    HANDLE f = CreateFileW(ready, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return 1;
    CloseHandle(f);
    while (GetFileAttributesW(release) == INVALID_FILE_ATTRIBUTES) Sleep(50);
    return 0;
}
''', encoding='ascii')
    subprocess.run([str(compiler), '-municode', '-static', str(fake_source), '-o', str(old / 'runtime/Scripts/python.exe')], check=True)
    (old / 'runtime/windows_native_recovery_proof_adapter.py').write_text('# disposable\n', encoding='ascii')
    candidate_identity = _candidate(controller, program)
    (program / 'versions/1.0.1/runtime/Scripts/python.exe').write_bytes(b'candidate cannot execute')
    candidate_identity = _versioned_payload_identity(controller, program, '1.0.1')
    parent = tmp_path / 'native-recovery.exe'
    subprocess.run([str(compiler), '-std=c11', '-municode', '-static', '-Wall', '-Wextra', '-Werror',
                    '-DCWNG_RECOVERY_PROOF', str(root / 'scripts/windows-native-recovery-proof.c'),
                    '-lbcrypt', '-ladvapi32', '-o', str(parent)], check=True)
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    for directory in (controller.state, recovery, program):
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
                        '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                        '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
            stage_guarded_activation(controller, program, recovery, '1.0.1', candidate_identity, session=session)
        journal = json.loads((controller.state / 'maintenance-journal.json').read_text(encoding='utf-8'))
        inventory = recovery / (journal['checkpoint'] + '.inventory')
        for target, grant in ((inventory, '*S-1-1-0:(W)'),
                              (old / 'scripts/windows_native_web.py', '*S-1-1-0:(W)'),
                              (old / 'scripts', '*S-1-1-0:(OI)(CI)(IO)(W)')):
            subprocess.run(['icacls', str(target), '/grant', grant], check=True, capture_output=True)
            try:
                result = subprocess.run([str(parent), str(controller.state), str(program), str(recovery)], timeout=40)
                assert result.returncode != 0
                assert not (controller.state / 'proof-child-ready').exists()
            finally:
                subprocess.run(['icacls', str(target), '/remove:g', '*S-1-1-0'], check=True, capture_output=True)
        subprocess.run(['icacls', str(old / 'scripts/windows_native_web.py'), '/grant', '*S-1-1-0:(R)'],
                       check=True, capture_output=True)
        import time
        from windows_native_maintenance_gate import maintenance_gate
        process = subprocess.Popen([str(parent), str(controller.state), str(program), str(recovery)])
        ready = controller.state / 'proof-child-ready'
        release = controller.state / 'proof-child-release'
        try:
            deadline = time.monotonic() + 40
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert ready.exists(), f'native proof stage {process.poll()}'
            process.kill()
            process.wait(timeout=10)
            with pytest.raises(OSError):
                with maintenance_gate(controller.state):
                    pass
            with pytest.raises(OSError):
                program.rename(tmp_path / 'moved-program')
        finally:
            release.touch()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    # Fake old executable does not mutate state; candidate must remain unexecuted.
    assert (controller.state / 'maintenance-journal.json').is_file()
    assert (program / 'active-version.txt').read_text(encoding='ascii').strip() == '1.0.1'


def test_prepared_cancel_rejects_database_mutation(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        with sqlite3.connect(controller.state / 'config' / 'app.db') as db:
            db.execute("INSERT INTO data VALUES ('later change')")
        with pytest.raises(ValueError, match='state'):
            cancel_prepared(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').is_file()


def test_prepared_cancel_rejects_changed_checkpoint_manifest(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            checkpoint = prepare_maintenance(controller, recovery, program, session=session)
        with (checkpoint / 'checkpoint.json').open('ab') as stream:
            stream.write(b' ')
        with pytest.raises(ValueError, match='manifest changed'):
            cancel_prepared(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').is_file()


def test_prepared_cancel_rechecks_recovery_volume_at_final_boundary(checkpoint_profile, tmp_path):
    import windows_native_checkpoint as checkpoints
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        real_volume = checkpoints._volume_identity
        checks = 0
        def replace_volume(path):
            nonlocal checks
            if Path(path).resolve() == recovery.resolve():
                checks += 1
                if checks == 2:
                    return ('replacement-volume', 1)
            return real_volume(path)
        with patch('windows_native_checkpoint._volume_identity', side_effect=replace_volume):
            with pytest.raises(ValueError, match='changed during verification'):
                cancel_prepared(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').is_file()


def test_prepared_cancel_never_removes_replaced_journal(checkpoint_profile, tmp_path):
    import windows_native_checkpoint as checkpoints
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        with maintenance_session(controller) as session:
            prepare_maintenance(controller, recovery, program, session=session)
        original_verify = checkpoints._verify_live_state_unchanged
        def replace_journal(*args, **kwargs):
            original_verify(*args, **kwargs)
            (controller.state / 'maintenance-journal.json').write_bytes(b'other transaction')
        with patch('windows_native_checkpoint._verify_live_state_unchanged', side_effect=replace_journal):
            with pytest.raises(ValueError, match='journal changed'):
                cancel_prepared(controller, program, recovery)
    assert (controller.state / 'maintenance-journal.json').read_bytes() == b'other transaction'


def test_process_death_after_prepared_journal_allows_verified_reentry(checkpoint_profile, tmp_path):
    controller, recovery = checkpoint_profile
    program = _installed_payload(controller, tmp_path)
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if not runtime:
        pytest.skip('set CWNG_NATIVE_TEST_RUNTIME for interrupted-process proof')
    source = r'''
import os, pathlib, sys
from types import SimpleNamespace
root, state, library, ingest, recovery, program, helper = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root / 'scripts'))
import windows_native_checkpoint as checkpoints
from windows_native_controller import NativeController
checkpoints.require_private_directory = lambda p: pathlib.Path(p).resolve(strict=True)
controller = SimpleNamespace(helper=helper, state=state, library=library, ingest=ingest,
    generation_file=state / 'web-generation', environment={'CWA_APP_DB_PATH': str(state / 'config/app.db')},
    web_entrypoint=program / 'versions/1.0.0/scripts/windows_native_web.py')
controller.require_no_maintenance_journal = NativeController.require_no_maintenance_journal
controller._require_no_maintenance_journal = lambda: NativeController._require_no_maintenance_journal(controller)
with checkpoints.maintenance_session(controller) as session:
    checkpoints.prepare_maintenance(controller, recovery, program, session=session)
    os._exit(0)
'''
    root = Path(__file__).resolve().parents[2]
    python = Path(runtime) / 'Scripts' / 'python.exe'
    result = subprocess.run([str(python), '-I', '-c', source, str(root), str(controller.state),
        str(controller.library), str(controller.ingest), str(recovery), str(program), str(controller.helper)],
        cwd=root, timeout=60, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (controller.state / 'maintenance-journal.json').is_file()
    with patch('windows_native_checkpoint.require_private_directory', side_effect=lambda p: Path(p).resolve(strict=True)):
        cancel_prepared(controller, program, recovery)
    assert not (controller.state / 'maintenance-journal.json').exists()
