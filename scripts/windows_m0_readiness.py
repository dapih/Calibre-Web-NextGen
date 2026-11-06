"""Authenticated loopback readiness for a coordinator-owned web process."""
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets


def _proof(key, nonce, pid, library):
    return hmac.digest(key, f'{nonce}\n{pid}\n{library}'.encode('utf-8'), 'sha256').hex()


def readiness_middleware(app, key, library_status):
    """Wrap a WSGI app; library_status returns (setup_complete, library_id)."""
    if not isinstance(key, bytes) or len(key) != 32 or not callable(library_status):
        raise ValueError('a private key and readiness provider are required')

    def wrapped(environ, start_response):
        if environ.get('PATH_INFO') != '/.cwng-m0-ready':
            return app(environ, start_response)
        nonce = environ.get('HTTP_X_CWNG_NONCE', '')
        if environ.get('REMOTE_ADDR') not in ('127.0.0.1', '::1') or not re.fullmatch('[0-9a-f]{64}', nonce):
            start_response('404 Not Found', [('Content-Length', '0')])
            return [b'']
        ready, library = library_status()
        if ready is not True or not isinstance(library, str) or not library or '\n' in library:
            start_response('503 Service Unavailable', [('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        pid = os.getpid()
        body = json.dumps({'pid': pid, 'library': library, 'proof': _proof(key, nonce, pid, library)},
                          separators=(',', ':')).encode('utf-8')
        start_response('200 OK', [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
                                  ('Cache-Control', 'no-store')])
        return [body]
    return wrapped


class OwnedReadiness:
    """Probe only loopback and require the responding process in the live job and expected library."""
    def __init__(self, job, process, port, key, library):
        if (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535 or
                not isinstance(key, bytes) or len(key) != 32 or not isinstance(library, str) or not library):
            raise ValueError('invalid readiness configuration')
        self.job, self.process, self.port, self.key, self.library = job, process, port, key, library

    def verify(self):
        try:
            if self.process.wait(0) is not None or self.job.active_count() < 1:
                return False
            nonce = secrets.token_hex(32)
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
            try:
                connection.request('GET', '/.cwng-m0-ready', headers={'X-CWNG-Nonce': nonce})
                response = connection.getresponse()
                body = response.read(4097)
                if response.status != 200 or len(body) > 4096:
                    return False
            finally:
                connection.close()
            result = json.loads(body)
            if (not isinstance(result, dict) or set(result) != {'pid', 'library', 'proof'} or
                    not self.job.contains_process(result['pid']) or result['library'] != self.library or
                    not isinstance(result['proof'], str)):
                return False
            expected = _proof(self.key, nonce, result['pid'], result['library'])
            return (hmac.compare_digest(result['proof'], expected) and
                    self.process.wait(0) is None and self.job.active_count() >= 1 and
                    self.job.contains_process(result['pid']))
        except (OSError, ValueError, TypeError, RuntimeError, http.client.HTTPException):
            return False
