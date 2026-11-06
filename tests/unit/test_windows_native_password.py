"""Local recovery updates real SQLite records while preserving unrelated data."""
from contextlib import contextmanager
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import windows_native_password as recovery


@pytest.fixture
def profile(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    app = state / 'app.db'
    db = sqlite3.connect(app)
    db.executescript('''
        CREATE TABLE user(id INTEGER PRIMARY KEY, name TEXT, role INTEGER, password TEXT);
        CREATE TABLE user_session(id INTEGER PRIMARY KEY, user_id INTEGER, session_key TEXT);
        CREATE TABLE reading_progress(user_id INTEGER, book INTEGER, position TEXT);
        CREATE TABLE user_app_password(id INTEGER PRIMARY KEY, user_id INTEGER, revoked INTEGER);
    ''')
    db.executemany('INSERT INTO user VALUES (?, ?, ?, ?)', [
        (1, 'owner', 1, generate_password_hash('old-password')),
        (2, 'reader', 0, generate_password_hash('reader-password')),
    ])
    db.executemany('INSERT INTO user_session VALUES (?, ?, ?)', [(1, 1, 'old-session'), (2, 2, 'other-session')])
    db.execute("INSERT INTO reading_progress VALUES (1, 9, 'chapter 2')")
    db.execute('INSERT INTO user_app_password VALUES (1, 1, 0)')
    db.commit()
    db.close()
    controller = SimpleNamespace(state=state, environment={'CWA_APP_DB_PATH': str(app)})
    events = []

    @contextmanager
    def owner(_controller):
        events.append('lock')
        yield object()
        events.append('unlock')

    monkeypatch.setattr(recovery, '_recovery_owner', owner)
    monkeypatch.setattr(recovery, '_verify_storage', lambda _controller: events.append('verify'))
    monkeypatch.setattr(recovery, '_pause_lan', lambda _state: events.append('pause'))
    return controller, app, events


def test_admin_reset_pauses_lan_and_invalidates_only_admin_web_sessions(profile):
    controller, app, events = profile
    recovery.recover_admin_password(controller, 'owner', 'chosen-password')
    assert events == ['lock', 'verify', 'pause', 'unlock']
    with sqlite3.connect(app) as db:
        assert check_password_hash(db.execute('SELECT password FROM user WHERE id=1').fetchone()[0], 'chosen-password')
        assert db.execute('SELECT user_id, session_key FROM user_session').fetchall() == [(2, 'other-session')]
        assert db.execute('SELECT * FROM reading_progress').fetchall() == [(1, 9, 'chapter 2')]
        assert db.execute('SELECT * FROM user_app_password').fetchall() == [(1, 1, 0)]
        assert check_password_hash(db.execute('SELECT password FROM user WHERE id=2').fetchone()[0], 'reader-password')


@pytest.mark.parametrize('name,password', [('reader', 'chosen-password'), ('missing', 'chosen-password'),
                                           ('owner', 'admin123'), ('owner', 'short')])
def test_invalid_request_does_not_change_database_or_pause_lan(profile, name, password):
    controller, app, events = profile
    before = app.read_bytes()
    with pytest.raises(ValueError):
        recovery.recover_admin_password(controller, name, password)
    assert 'pause' not in events
    assert app.read_bytes() == before


def test_network_pause_failure_aborts_credential_change(profile, monkeypatch):
    controller, app, _ = profile
    monkeypatch.setattr(recovery, '_pause_lan', lambda _state: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError):
        recovery.recover_admin_password(controller, 'owner', 'chosen-password')
    with sqlite3.connect(app) as db:
        assert check_password_hash(db.execute('SELECT password FROM user WHERE id=1').fetchone()[0], 'old-password')
        assert db.execute('SELECT COUNT(*) FROM user_session').fetchone()[0] == 2


def test_real_network_consent_stays_paused_after_recovery(profile, monkeypatch):
    import windows_native_network as network
    controller, _, _ = profile
    network.set_lan_enabled(controller.state, True, consent=True)
    monkeypatch.setattr(recovery, '_pause_lan', network.pause_for_recovery)
    recovery.recover_admin_password(controller, 'owner', 'chosen-password')
    assert network.read_settings(controller.state) == {
        'schema': 1, 'lan_enabled': False, 'recovery_paused': True,
    }
