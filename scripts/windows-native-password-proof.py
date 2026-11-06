"""Exercise local password recovery against a fresh disposable real profile.

Run outside the sandbox with the private M0 Python and ``--helper``. This calls
the existing first-run proof, which creates and retains a new temporary profile;
it never opens an existing user library. The fixture passwords below are public
test data, used only for the newly created temporary administrator.
"""
import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    previous = subprocess.run(
        [sys.executable, '-I', str(root / 'scripts/windows-native-first-run-to-launch-proof.py'),
         '--helper', str(Path(args.helper).resolve(strict=True))],
        check=True, capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    prefix = 'Disposable proof: '
    matches = [line[len(prefix):] for line in previous.stdout.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError('First-run proof did not identify exactly one disposable profile.')
    state = Path(matches[0]) / 's'
    sys.path.insert(0, str(root / 'scripts'))
    from werkzeug.security import check_password_hash
    import requests
    from windows_native_launcher import _offline_recovery_owner, _read_profile
    from windows_native_network import read_settings, set_lan_enabled
    from windows_native_password import recover_admin_password

    controller = _read_profile(state)
    session = requests.Session()
    session.trust_env = False
    base = 'http://127.0.0.1:' + str(controller.port)
    assert controller.start() is None
    try:
        page = session.get(base + '/login', timeout=10)
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        assert token, 'Login CSRF token missing.'
        response = session.post(base + '/login', timeout=10, allow_redirects=False,
                                data={'username': 'fixture-admin', 'password': 'fixture-only-password',
                                      'csrf_token': token.group(1), 'remember_me': 'on'})
        assert response.status_code in (302, 303), response.status_code
        assert 'remember_token' in session.cookies
        assert session.get(base + '/admin/view', timeout=30).status_code == 200
    finally:
        controller.close()
    database = controller.library / 'metadata.db'
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    app = Path(controller.environment['CWA_APP_DB_PATH'])
    with closing(sqlite3.connect(app)) as db:
        row = db.execute('SELECT id, password FROM user WHERE name=?', ('fixture-admin',)).fetchone()
        assert row and check_password_hash(row[1], 'fixture-only-password')
        user_id = row[0]
        before_users = db.execute('SELECT COUNT(*) FROM user').fetchone()[0]
        before_pairings = db.execute('SELECT COUNT(*) FROM user_app_password').fetchone()[0]
        db.execute('INSERT INTO user_session (user_id, session_key, random, expiry) VALUES (?, ?, ?, ?)',
                   (user_id, 'fixture-session', 'fixture-random', 0))
        db.commit()
    with _offline_recovery_owner(controller):
        set_lan_enabled(state, True, consent=True)
    recover_admin_password(controller, 'fixture-admin', 'recovered-fixture-password')
    assert read_settings(state) == {'schema': 1, 'lan_enabled': False, 'recovery_paused': True}
    with closing(sqlite3.connect(app)) as db:
        password = db.execute('SELECT password FROM user WHERE id=?', (user_id,)).fetchone()[0]
        assert check_password_hash(password, 'recovered-fixture-password')
        assert not check_password_hash(password, 'fixture-only-password')
        assert not db.execute('SELECT 1 FROM user_session WHERE user_id=?', (user_id,)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM user').fetchone()[0] == before_users
        assert db.execute('SELECT COUNT(*) FROM user_app_password').fetchone()[0] == before_pairings
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert controller.start() is None
    try:
        assert controller.readiness.verify()
        # Both the existing browser session and its remember-me cookie must
        # lose access; saving the pairings does not restore an old web login.
        response = session.get(base + '/admin/view', timeout=30, allow_redirects=False)
        assert response.status_code in (302, 303, 401, 403), response.status_code
        fresh = requests.Session()
        fresh.trust_env = False
        page = fresh.get(base + '/login', timeout=10)
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        assert token, 'Post-recovery login CSRF token missing.'
        response = fresh.post(base + '/login', timeout=10, allow_redirects=False,
                              data={'username': 'fixture-admin', 'password': 'recovered-fixture-password',
                                    'csrf_token': token.group(1)})
        assert response.status_code in (302, 303), response.status_code
        assert fresh.get(base + '/admin/view', timeout=30).status_code == 200
        fresh.close()
    finally:
        session.close()
        controller.close()
    print('Disposable password recovery and subsequent real web readiness passed:', state)


if __name__ == '__main__':
    main()
