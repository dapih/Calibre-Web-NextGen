"""Native mode must not accept HTTPS settings while its listener uses HTTP."""

import sqlite3
import sys
from pathlib import Path

import pytest
from flask import Blueprint, Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_http import install_http_only_guard, validate_http_settings


@pytest.mark.parametrize('https,cert,key', [
    (1, '', ''), (0, 'cert.pem', ''), (0, '', 'key.pem'),
    (0, 'cert.pem', 'key.pem'),
])
def test_saved_https_settings_are_refused_before_native_start(https, cert, key):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE settings (config_use_https, config_certfile, config_keyfile)')
        db.execute('INSERT INTO settings VALUES (?, ?, ?)', (https, cert, key))
        with pytest.raises(ValueError, match='HTTPS.*not yet supported'):
            validate_http_settings(db)


def test_legacy_missing_columns_and_disabled_settings_allow_http():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE settings (config_calibre_dir TEXT)')
        db.execute("INSERT INTO settings VALUES ('library')")
        validate_http_settings(db)


def test_explicit_http_defaults_allow_start():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE settings (config_use_https, config_certfile, config_keyfile)')
        db.execute('INSERT INTO settings VALUES (0, NULL, NULL)')
        validate_http_settings(db)


@pytest.mark.parametrize('login_type', [1, 2, '02', '+2', ' 2 ', 2.0])
def test_saved_ldap_or_oauth_cannot_start_without_native_support(login_type):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE settings (config_login_type)')
        db.execute('INSERT INTO settings VALUES (?)', (login_type,))
        with pytest.raises(ValueError, match='LDAP and OAuth'):
            validate_http_settings(db)


@pytest.mark.parametrize('data', [
    {'config_use_https': 'on'}, {'config_certfile': 'cert.pem'},
    {'config_keyfile': 'key.pem'},
])
def test_native_admin_tls_changes_never_reach_update_handler(data):
    app = Flask(__name__)
    admin = Blueprint('admin', __name__)
    calls = []

    @admin.post('/admin/ajaxconfig')
    def ajax_config():
        calls.append(True)
        return 'saved'

    app.register_blueprint(admin)
    install_http_only_guard(app)
    response = app.test_client().post('/admin/ajaxconfig', data=data)
    assert response.status_code == 400
    assert b'HTTPS' in response.data
    assert not calls


def test_unrelated_settings_remain_saveable_in_native_mode():
    app = Flask(__name__)
    admin = Blueprint('admin', __name__)

    @admin.post('/admin/ajaxconfig')
    def ajax_config():
        return 'saved'

    app.register_blueprint(admin)
    install_http_only_guard(app)
    response = app.test_client().post('/admin/ajaxconfig', data={'config_port': '8083'})
    assert response.status_code == 200
    assert response.data == b'saved'


@pytest.mark.parametrize('ssl', [
    {'use_https': True}, {'certfile': 'cert.pem'}, {'keyfile': 'key.pem'},
    {'use_https': 'yes'}, 'invalid', ['invalid'],
])
def test_native_spa_tls_changes_are_rejected_before_any_settings_are_saved(ssl):
    app = Flask(__name__)
    api = Blueprint('api_v1', __name__)
    calls = []

    @api.post('/api/v1/admin/security')
    def admin_update_security():
        calls.append(True)
        return 'saved'

    app.register_blueprint(api)
    install_http_only_guard(app)
    response = app.test_client().post('/api/v1/admin/security', json={'ssl': ssl, 'login_type': 0})
    assert response.status_code == 400
    assert 'HTTPS' in response.json['error']['message']
    assert not calls


def test_native_spa_can_clear_tls_settings_or_save_other_security_settings():
    app = Flask(__name__)
    api = Blueprint('api_v1', __name__)

    @api.post('/api/v1/admin/security')
    def admin_update_security():
        return 'saved'

    app.register_blueprint(api)
    install_http_only_guard(app)
    client = app.test_client()
    for data in ({'ssl': {'use_https': False, 'certfile': '', 'keyfile': ''}},
                 {'reverse_proxy': {'enabled': False}}):
        response = client.post('/api/v1/admin/security', json=data)
        assert response.status_code == 200
        assert response.data == b'saved'


@pytest.mark.parametrize('login_type', [1, 2, '1', '2', '02', '+2', ' 2 ', 2.0])
def test_native_spa_rejects_unsupported_authentication_before_save(login_type):
    app = Flask(__name__)
    api = Blueprint('api_v1', __name__)
    calls = []

    @api.post('/api/v1/admin/security')
    def admin_update_security():
        calls.append(True)
        return 'saved'

    app.register_blueprint(api)
    install_http_only_guard(app)
    response = app.test_client().post('/api/v1/admin/security', json={'login_type': login_type})
    assert response.status_code == 400
    assert 'LDAP and OAuth' in response.json['error']['message']
    assert not calls


@pytest.mark.parametrize('login_type', ['1', '2', '02', '+2', ' 2 '])
def test_native_classic_rejects_unsupported_authentication_before_save(login_type):
    app = Flask(__name__)
    admin = Blueprint('admin', __name__)
    calls = []

    @admin.post('/admin/ajaxconfig')
    def ajax_config():
        calls.append(True)
        return 'saved'

    app.register_blueprint(admin)
    install_http_only_guard(app)
    response = app.test_client().post('/admin/ajaxconfig', data={'config_login_type': login_type})
    assert response.status_code == 400
    assert b'LDAP and OAuth' in response.data
    assert not calls


def test_native_spa_security_settings_only_offer_supported_login_type():
    app = Flask(__name__)
    api = Blueprint('api_v1', __name__)

    @api.get('/api/v1/admin/security')
    def admin_get_security():
        return jsonify({'login_type': 0, 'login_types': [
            {'id': 0, 'name': 'Standard'}, {'id': 1, 'name': 'LDAP'}, {'id': 2, 'name': 'OAuth'}]})

    app.register_blueprint(api)
    install_http_only_guard(app)
    result = app.test_client().get('/api/v1/admin/security')
    assert result.status_code == 200
    assert result.json['login_types'] == [{'id': 0, 'name': 'Standard'}]
    assert result.json['native_http_only'] is True
