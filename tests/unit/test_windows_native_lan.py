"""Direct LAN gate tests: real WSGI behavior and fail-closed transitions."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from wsgiref.simple_server import make_server

import pytest

if os.name != 'nt':
    pytest.skip('Windows native LAN', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_lan import (NativeLanService, PrivateNetworkGate, PrivateProfileSnapshot,
                                authenticated_network_status, network_status_middleware)
from windows_native_network import pause_for_recovery, set_lan_enabled


def _invoke(gate, peer='192.168.3.29', path='/opds', **headers):
    state = {}
    environ = {'REMOTE_ADDR': peer, 'PATH_INFO': path, **headers}
    def start(status, _headers):
        state['status'] = status
    state['body'] = b''.join(gate(environ, start))
    return state


def test_requires_explicit_consent_and_current_private_subnet(tmp_path):
    present = [('192.168.3.2', 24, 3)]
    def app(environ, start):
        start('200 OK', [])
        return [environ['REMOTE_ADDR'].encode()]
    gate = PrivateNetworkGate(app, tmp_path, '192.168.3.2', interfaces=lambda: present,
                              verify_interface=lambda *_: True)
    assert _invoke(gate)['status'] == '403 Forbidden'
    set_lan_enabled(tmp_path, True, consent=True)
    assert _invoke(gate) == {'status': '200 OK', 'body': b'192.168.3.29'}
    assert _invoke(gate, peer='192.168.4.29')['status'] == '403 Forbidden'
    assert _invoke(gate, peer='127.0.0.1')['status'] == '403 Forbidden'
    present.clear()  # Profile changed to Public or interface disappeared.
    assert _invoke(gate)['status'] == '403 Forbidden'


def test_recovery_pause_and_unknown_profile_deny_without_trusting_headers(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    def app(environ, start):
        start('200 OK', [])
        return [json.dumps(environ).encode()]
    gate = PrivateNetworkGate(app, tmp_path, '10.10.0.5', interfaces=lambda: [('10.10.0.5', 24, 8)],
                              proxy_login_header=lambda: 'X-Auth-User', verify_interface=lambda *_: True)
    reply = _invoke(gate, peer='10.10.0.10', HTTP_X_FORWARDED_FOR='127.0.0.1',
                    HTTP_X_REAL_IP='127.0.0.1', HTTP_FORWARDED='for=127.0.0.1',
                    HTTP_X_AUTH_USER='admin', HTTP_X_SCHEME='https', HTTP_X_SCRIPT_NAME='/fake')
    environ = json.loads(reply['body'])
    assert environ['REMOTE_ADDR'] == '10.10.0.10'
    assert 'HTTP_X_FORWARDED_FOR' not in environ
    assert 'HTTP_X_REAL_IP' not in environ
    assert 'HTTP_FORWARDED' not in environ
    assert 'HTTP_X_AUTH_USER' not in environ
    assert 'HTTP_X_SCHEME' not in environ
    assert 'HTTP_X_SCRIPT_NAME' not in environ
    assert environ['cwng.native_lan'] is True
    assert _invoke(gate, peer='10.10.0.10', path='/.cwng-m0-ready')['status'] == '403 Forbidden'
    assert _invoke(gate, peer='10.10.0.10', path='/.cwng-m0-network')['status'] == '403 Forbidden'
    pause_for_recovery(tmp_path)
    assert _invoke(gate, peer='10.10.0.10')['status'] == '403 Forbidden'
    set_lan_enabled(tmp_path, True, consent=True)
    def failed():
        raise OSError('profile unavailable')
    gate.interfaces = failed
    assert _invoke(gate, peer='10.10.0.10')['status'] == '403 Forbidden'


def test_malformed_settings_fail_closed(tmp_path):
    (tmp_path / 'network.json').write_text('{broken', encoding='utf-8')
    gate = PrivateNetworkGate(lambda *_: pytest.fail('reached app'), tmp_path,
                              '192.168.3.2', interfaces=lambda: [('192.168.3.2', 24, 3)],
                              verify_interface=lambda *_: True)
    assert _invoke(gate)['status'] == '403 Forbidden'


def test_direct_listener_keeps_actual_peer_and_stops_on_profile_change(tmp_path):
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pytest.skip('host has no discoverable IPv4 address')
    addresses = [value for value in addresses if ipaddress.IPv4Address(value).is_private
                 and not ipaddress.IPv4Address(value).is_loopback]
    if not addresses:
        pytest.skip('host has no non-loopback private IPv4 address')
    address = addresses[0]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind((address, 0))
        port = reservation.getsockname()[1]
    set_lan_enabled(tmp_path, True, consent=True)
    private = [(address, 24, 1)]
    def app(environ, start):
        start('200 OK', [('Content-Type', 'text/plain')])
        return [environ['REMOTE_ADDR'].encode()]
    service = NativeLanService(app, tmp_path, port, ready=lambda: True,
                               interfaces=lambda: list(private), interval_ms=100,
                               verify_interface=lambda *_: True)
    try:
        service.start()
        deadline = time.monotonic() + 5
        while address not in service.servers and time.monotonic() < deadline:
            time.sleep(.02)
        assert address in service.servers, service.error
        connection = http.client.HTTPConnection(address, port, timeout=5)
        connection.request('GET', '/opds', headers={'X-Forwarded-For': '127.0.0.1'})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read().decode() == address
        connection.close()
        private.clear()
        deadline = time.monotonic() + 5
        while address in service.servers and time.monotonic() < deadline:
            time.sleep(.02)
        assert address not in service.servers
    finally:
        service.close()


def test_occupied_device_port_is_not_adopted_or_terminated(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        service = NativeLanService(lambda *_: (), tmp_path, port, ready=lambda: True,
                                   interfaces=lambda: [('127.0.0.1', 8, 1)], interval_ms=100)
        try:
            service.start()
            deadline = time.monotonic() + 5
            while service.error is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert service.error is not None
            assert not service.servers
            assert other.getsockname()[1] == port
        finally:
            service.close()


def test_stale_discovery_and_live_public_category_deny_requests(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    now = [10.0]
    snapshot = PrivateProfileSnapshot(max_age=2, clock=lambda: now[0])
    snapshot.publish([('192.168.3.2', 24, 3)])
    category = [True]
    def app(_environ, start):
        start('200 OK', [])
        return [b'allowed']
    gate = PrivateNetworkGate(app, tmp_path, '192.168.3.2', interfaces=snapshot.current,
                              verify_interface=lambda *_: category[0])
    assert _invoke(gate)['status'] == '200 OK'
    category[0] = False  # Profile changed, even while discovery remains cached.
    assert _invoke(gate)['status'] == '403 Forbidden'
    category[0] = True
    now[0] = 12.0
    assert _invoke(gate)['status'] == '403 Forbidden'
    snapshot.publish([('192.168.3.2', 24, 3)])
    assert _invoke(gate)['status'] == '200 OK'
    snapshot.invalidate()
    assert _invoke(gate)['status'] == '403 Forbidden'


def test_broad_or_public_subnet_does_not_expose_home_server(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    def app(_environ, start):
        start('200 OK', [])
        return [b'allowed']
    broad = PrivateNetworkGate(app, tmp_path, '10.2.0.1', interfaces=lambda: [('10.2.0.1', 1, 3)],
                               verify_interface=lambda *_: True)
    assert _invoke(broad, peer='10.5.0.1')['status'] == '403 Forbidden'
    public = PrivateNetworkGate(app, tmp_path, '8.8.8.8', interfaces=lambda: [('8.8.8.8', 24, 3)],
                                verify_interface=lambda *_: True)
    assert _invoke(public, peer='8.8.8.9')['status'] == '403 Forbidden'


def test_listener_status_requires_local_peer_and_authenticates_owned_server():
    key = bytes(range(32))
    def fallback(_environ, start):
        start('200 OK', [])
        return [b'app']
    app = network_status_middleware(fallback, key,
        lambda: {'listening': ['192.168.3.2'], 'error': None})
    # Even a syntactically valid challenge does not expose status to a device.
    reply = _invoke(app, peer='192.168.3.29', path='/.cwng-m0-network', HTTP_X_CWNG_NONCE='a' * 64)
    assert reply['status'] == '404 Not Found'
    with make_server('127.0.0.1', 0, app) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            readiness = SimpleNamespace(verify=lambda: True, port=server.server_port, key=key,
                process=SimpleNamespace(wait=lambda _timeout: None),
                job=SimpleNamespace(contains_process=lambda pid: pid == os.getpid()))
            result = authenticated_network_status(readiness)
            assert result['listening'] == ['192.168.3.2']
            assert result['error'] is None
            readiness.key = bytes(reversed(range(32)))
            assert authenticated_network_status(readiness) is None
            readiness.key = key
            readiness.job = SimpleNamespace(contains_process=lambda _pid: False)
            assert authenticated_network_status(readiness) is None
        finally:
            server.shutdown()
            thread.join(timeout=3)
