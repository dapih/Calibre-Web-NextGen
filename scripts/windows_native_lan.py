"""Direct native LAN listener, with per-request Private-profile checks.

The listener serves the actual WSGI application without a loopback proxy, so
the application retains the direct device IP for local-only security checks.
The owning web process must close this service when it stops.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time

from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.wsgi import WSGIContainer

from windows_m0_readiness import _proof
from windows_native_network import is_home_subnet, is_live_private_interface, private_ipv4_interfaces, read_settings


_FORWARDED_HEADERS = ('HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST',
                      'HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_PORT',
                      'HTTP_X_FORWARDED_PREFIX', 'HTTP_X_REAL_IP',
                      'HTTP_FORWARDED', 'HTTP_FORWARD_FOR', 'HTTP_X_SCHEME',
                      'HTTP_X_SCRIPT_NAME')
_STATUS_PATH = '/.cwng-m0-network'


class PrivateProfileSnapshot:
    """Short-lived discovery results; profile authorization is never cached."""

    def __init__(self, *, max_age=8, clock=time.monotonic):
        self.max_age, self.clock = max_age, clock
        self._lock = threading.Lock()
        self._items = ()
        self._expires = 0

    def publish(self, items):
        with self._lock:
            self._items = tuple(items)
            self._expires = self.clock() + self.max_age

    def invalidate(self):
        with self._lock:
            self._items = ()
            self._expires = 0

    def current(self):
        with self._lock:
            if self.clock() >= self._expires:
                raise OSError('private-network discovery is stale')
            return self._items


def local_web_ready(port, key, library_status):
    """Authenticate the loopback HTTP responder as this owned web process."""
    connection = None
    try:
        configured, library = library_status()
        if configured is not True or not library:
            return False
        nonce = secrets.token_hex(32)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        connection.request('GET', '/.cwng-m0-ready', headers={'X-CWNG-Nonce': nonce})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            return False
        data = json.loads(body)
        return (isinstance(data, dict) and set(data) == {'pid', 'library', 'proof'} and
                data['pid'] == os.getpid() and data['library'] == library and
                isinstance(data['proof'], str) and
                hmac.compare_digest(data['proof'], _proof(key, nonce, data['pid'], library)))
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
        return False
    finally:
        if connection:
            connection.close()


def _status_mac(key, nonce, payload):
    body = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.digest(key, b'cwng-lan-status\0' + nonce.encode('ascii') + b'\0' + body, 'sha256').hex()


def network_status_middleware(app, key, status):
    """Expose listener state only to a keyed challenge from loopback."""
    if not isinstance(key, bytes) or len(key) != 32 or not callable(status):
        raise ValueError('private network status key required')

    def wrapped(environ, start_response):
        if environ.get('PATH_INFO') != _STATUS_PATH:
            return app(environ, start_response)
        nonce = environ.get('HTTP_X_CWNG_NONCE', '')
        if environ.get('REMOTE_ADDR') not in ('127.0.0.1', '::1') or not re.fullmatch('[0-9a-f]{64}', nonce):
            start_response('404 Not Found', [('Content-Length', '0')])
            return [b'']
        try:
            result = status()
            payload = {'pid': os.getpid(), 'listening': result['listening'], 'error': result['error']}
            proof = _status_mac(key, nonce, payload)
            body = json.dumps({'status': payload, 'proof': proof}, separators=(',', ':')).encode('utf-8')
            if len(body) > 4096:
                raise ValueError('network status too large')
        except Exception:
            start_response('503 Service Unavailable', [('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        start_response('200 OK', [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
                                  ('Cache-Control', 'no-store')])
        return [body]
    return wrapped


def authenticated_network_status(readiness):
    """Verify owned web status and its HMAC before displaying listener state."""
    connection = None
    try:
        if readiness is None or not readiness.verify():
            return None
        nonce = secrets.token_hex(32)
        connection = http.client.HTTPConnection('127.0.0.1', readiness.port, timeout=2)
        connection.request('GET', _STATUS_PATH, headers={'X-CWNG-Nonce': nonce})
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            return None
        data = json.loads(body)
        payload = data['status']
        if (not isinstance(data, dict) or set(data) != {'status', 'proof'} or
                not isinstance(payload, dict) or set(payload) != {'pid', 'listening', 'error'} or
                type(payload['pid']) is not int or not isinstance(payload['listening'], list) or
                any(not isinstance(item, str) for item in payload['listening']) or
                (payload['error'] is not None and not isinstance(payload['error'], str)) or
                not isinstance(data['proof'], str) or
                not hmac.compare_digest(data['proof'], _status_mac(readiness.key, nonce, payload)) or
                readiness.process.wait(0) is not None or not readiness.job.contains_process(payload['pid'])):
            return None
        return payload
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, http.client.HTTPException):
        return None
    finally:
        if connection:
            connection.close()


class PrivateNetworkGate:
    """Allow only a consenting, directly connected Private IPv4 subnet peer.

    The current profile is rechecked on every request; errors deny access.
    The wrapped app receives the genuine WSGI peer, not forwarded headers.
    """

    def __init__(self, app, state, local_address, *, interfaces=private_ipv4_interfaces,
                 proxy_login_header=lambda: '', verify_interface=is_live_private_interface):
        self.app, self.state = app, state
        self.local_address = str(ipaddress.IPv4Address(local_address))
        self.interfaces = interfaces
        self.proxy_login_header = proxy_login_header
        self.verify_interface = verify_interface

    def __call__(self, environ, start_response):
        allowed = False
        try:
            settings = read_settings(self.state)
            peer = ipaddress.IPv4Address(environ.get('REMOTE_ADDR', ''))
            if (settings['lan_enabled'] and not settings['recovery_paused'] and
                    not peer.is_loopback and not peer.is_multicast and not peer.is_unspecified and
                    not peer.is_link_local):
                for address, prefix, interface in self.interfaces():
                    subnet = ipaddress.IPv4Network(f'{address}/{prefix}', strict=False)
                    if (address == self.local_address and peer in subnet and
                            is_home_subnet(address, prefix) and
                            self.verify_interface(address, prefix, interface)):
                        allowed = True
                        break
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if not allowed or environ.get('PATH_INFO') in ('/.cwng-m0-ready', _STATUS_PATH):
            start_response('403 Forbidden', [('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        try:
            auth_header = self.proxy_login_header()
            if auth_header and (not isinstance(auth_header, str) or not re.fullmatch(r'[A-Za-z0-9-]+', auth_header)):
                raise ValueError('invalid reverse-proxy login header')
        except Exception:
            start_response('403 Forbidden', [('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        if auth_header and auth_header.lower() in ('content-type', 'content-length'):
            start_response('403 Forbidden', [('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        if auth_header:
            environ.pop('HTTP_' + auth_header.upper().replace('-', '_'), None)
        for name in _FORWARDED_HEADERS:
            environ.pop(name, None)
        environ['cwng.native_lan'] = True
        return self.app(environ, start_response)


class _NativeWSGIContainer(WSGIContainer):
    def environ(self, request):
        environ = super().environ(request)
        environ['RAW_URI'] = request.path
        return environ


class NativeLanService:
    """Maintain direct Private-profile listeners without blocking the local UI.

    Discovery runs asynchronously. Each request independently revalidates the
    profile, so a cached discovery cannot authorize a Public connection. A
    failed bind or query leaves the loopback app and local control untouched.
    """

    def __init__(self, app, state, port, *, ready, ssl_options=None,
                 interfaces=private_ipv4_interfaces, interval_ms=3000,
                 proxy_login_header=lambda: '', verify_interface=is_live_private_interface):
        if type(port) is not int or not 1 <= port <= 65535 or not callable(ready):
            raise ValueError('invalid LAN service configuration')
        self.app, self.state, self.port = app, state, port
        self.ready, self.ssl_options, self.interfaces = ready, ssl_options, interfaces
        self.proxy_login_header = proxy_login_header
        self.verify_interface = verify_interface
        self.snapshot = PrivateProfileSnapshot()
        self.interval_ms = interval_ms
        self.thread = None
        self.loop = None
        self.servers = {}
        self.error = None
        self._state_lock = threading.Lock()
        self._started = threading.Event()
        self._discovery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='cwng-net-discovery')
        self._request_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='cwng-lan')
        self._refreshing = False

    async def _refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            def discover():
                settings = read_settings(self.state)
                if not settings['lan_enabled'] or settings['recovery_paused'] or not self.ready():
                    return set()
                return tuple(self.interfaces())
            try:
                discovered = await asyncio.get_running_loop().run_in_executor(self._discovery_executor, discover)
                self.snapshot.publish(discovered)
                desired = {address for address, _prefix, _interface in discovered}
                with self._state_lock:
                    self.error = None
            except Exception as error:
                self.snapshot.invalidate()
                desired = set()
                with self._state_lock:
                    self.error = str(error)
            for address in self.servers.keys() - desired:
                with self._state_lock:
                    server = self.servers.pop(address)
                server.stop()
                await server.close_all_connections()
            for address in desired - self.servers.keys():
                listener = None
                server = None
                try:
                    app = PrivateNetworkGate(self.app, self.state, address, interfaces=self.snapshot.current,
                                             proxy_login_header=self.proxy_login_header,
                                             verify_interface=self.verify_interface)
                    server = HTTPServer(_NativeWSGIContainer(app, executor=self._request_executor),
                                        max_buffer_size=209700000, ssl_options=self.ssl_options,
                                        xheaders=False)
                    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    # On Windows this prevents a later SO_REUSEADDR bind from
                    # sharing our port or diverting connections unpredictably.
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    listener.bind((address, self.port))
                    listener.listen(socket.SOMAXCONN)
                    listener.setblocking(False)
                    server.add_socket(listener)
                    with self._state_lock:
                        self.servers[address] = server
                except OSError as error:
                    if listener is not None:
                        listener.close()
                    if server is not None:
                        server.stop()
                    with self._state_lock:
                        self.error = str(error)
        finally:
            self._refreshing = False

    def _run(self):
        asyncio.set_event_loop(asyncio.SelectorEventLoop())
        self.loop = IOLoop.current()
        periodic = PeriodicCallback(self._refresh, self.interval_ms)
        periodic.start()
        self.loop.add_callback(self._refresh)
        self._started.set()
        try:
            self.loop.start()
        finally:
            periodic.stop()
            for server in self.servers.values():
                server.stop()
            with self._state_lock:
                self.servers.clear()
            self.loop.close(all_fds=True)

    def status(self):
        """Return locally bound Private addresses, never a firewall reachability claim."""
        with self._state_lock:
            bound, error = set(self.servers), self.error
        try:
            settings = read_settings(self.state)
            if not settings['lan_enabled'] or settings['recovery_paused']:
                return {'listening': [], 'error': None}
            listening = sorted({address for address, prefix, interface in self.snapshot.current()
                                if address in bound and is_home_subnet(address, prefix) and
                                self.verify_interface(address, prefix, interface)})
            return {'listening': listening, 'error': error}
        except (OSError, ValueError, TypeError):
            return {'listening': [], 'error': 'Private network status is unavailable.'}

    def start(self):
        if self.thread is not None:
            raise RuntimeError('LAN service already started')
        self.thread = threading.Thread(target=self._run, name='cwng-lan-listener', daemon=True)
        self.thread.start()
        if not self._started.wait(5):
            raise RuntimeError('LAN service did not start')

    def close(self):
        if self.loop is not None:
            self.loop.add_callback(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError('LAN service did not stop')
        self._discovery_executor.shutdown(wait=True, cancel_futures=True)
        self._request_executor.shutdown(wait=True, cancel_futures=True)
