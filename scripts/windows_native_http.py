"""Keep native Windows' local and LAN listeners consistently HTTP-only."""


_MESSAGE = ('HTTPS and secure-cookie settings are not yet supported by the native '
            'Windows launcher. Clear the certificate, key and HTTPS settings before starting.')
_AUTH_MESSAGE = ('LDAP and OAuth login are not yet supported in native Windows mode. '
                 'Use standard authentication.')
_COLUMNS = ('config_use_https', 'config_certfile', 'config_keyfile', 'config_login_type')


def _unsupported_login(value):
    """Match the admin handlers' integer conversion before checking the mode."""
    try:
        return int(value) in (1, 2)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_http_settings(database):
    """Refuse unsupported saved settings before the native web process starts.

    Older databases may not yet have these columns. Their schema migration adds
    them with HTTP defaults, so missing columns cannot enable HTTPS.
    """
    available = {row[1] for row in database.execute('PRAGMA table_info(settings)')}
    selected = [column for column in _COLUMNS if column in available]
    if not selected:
        return
    rows = database.execute('SELECT ' + ', '.join(selected) + ' FROM settings').fetchall()
    for row in rows:
        values = dict(zip(selected, row))
        if _unsupported_login(values.get('config_login_type')):
            raise ValueError(_AUTH_MESSAGE)
        if (values.get('config_use_https') not in (None, 0, False) or
                values.get('config_certfile') or values.get('config_keyfile')):
            raise ValueError(_MESSAGE)


def install_http_only_guard(app):
    """Reject attempts to enable unsupported settings before saving any data."""
    from flask import jsonify, request

    @app.before_request
    def _reject_https_settings():
        if request.method != 'POST':
            return None
        if request.endpoint == 'admin.ajax_config':
            if any(_unsupported_login(value) for value in request.form.getlist('config_login_type')):
                return _AUTH_MESSAGE, 400
            if (any(value == 'on' for value in request.form.getlist('config_use_https')) or
                    any(value.strip() for field in ('config_certfile', 'config_keyfile')
                        for value in request.form.getlist(field))):
                return _MESSAGE, 400
        elif request.endpoint == 'api_v1.admin_update_security':
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({'error': {'code': 'invalid_request', 'message': _MESSAGE}}), 400
            if _unsupported_login(data.get('login_type')):
                return jsonify({'error': {'code': 'invalid_request', 'message': _AUTH_MESSAGE}}), 400
            ssl = data.get('ssl')
            if ssl is not None and (not isinstance(ssl, dict) or ssl.get('use_https') or
                                    any(str(ssl.get(name) or '').strip() for name in ('certfile', 'keyfile'))):
                return jsonify({'error': {'code': 'invalid_request', 'message': _MESSAGE}}), 400
        return None

    @app.after_request
    def _native_security_capabilities(response):
        if request.endpoint != 'api_v1.admin_get_security' or response.status_code != 200 or not response.is_json:
            return response
        payload = response.get_json(silent=True)
        if isinstance(payload, dict) and isinstance(payload.get('login_types'), list):
            payload['login_types'] = [item for item in payload['login_types']
                                      if isinstance(item, dict) and item.get('id') == 0]
            payload['native_http_only'] = True
            response.set_data(app.json.dumps(payload))
        return response
