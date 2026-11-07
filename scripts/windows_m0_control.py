"""Experimental local-only, authenticated Windows pipe for M0 control.

The caller owns secret delivery, worker lifecycle, timeouts, and private state.
This module accepts only bounded JSON and does not deserialize Python objects.
"""

import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time

if os.name != 'nt':
    raise OSError('Windows named pipes are required')

_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_advapi = ctypes.WinDLL('advapi32', use_last_error=True)
_HANDLE = wintypes.HANDLE
_INVALID = ctypes.c_void_p(-1).value
_MAX_MESSAGE = 4096


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]


_advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
_advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
_advapi.OpenProcessToken.argtypes = [_HANDLE, wintypes.DWORD, ctypes.POINTER(_HANDLE)]
_advapi.OpenProcessToken.restype = wintypes.BOOL
_advapi.GetTokenInformation.argtypes = [_HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_advapi.GetTokenInformation.restype = wintypes.BOOL
_advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
_kernel.GetCurrentProcess.restype = _HANDLE
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.OpenProcess.restype = _HANDLE
_kernel.LocalFree.argtypes = [ctypes.c_void_p]
_kernel.LocalFree.restype = ctypes.c_void_p
_kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes)]
_kernel.CreateNamedPipeW.restype = _HANDLE
_kernel.ConnectNamedPipe.argtypes = [_HANDLE, ctypes.c_void_p]
_kernel.ConnectNamedPipe.restype = wintypes.BOOL
_kernel.DisconnectNamedPipe.argtypes = [_HANDLE]
_kernel.DisconnectNamedPipe.restype = wintypes.BOOL
_kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, _HANDLE]
_kernel.CreateFileW.restype = _HANDLE
_kernel.CloseHandle.argtypes = [_HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
_kernel.ReadFile.argtypes = [_HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel.ReadFile.restype = wintypes.BOOL
_kernel.WriteFile.argtypes = [_HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel.WriteFile.restype = wintypes.BOOL
_kernel.SetNamedPipeHandleState.argtypes = [_HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
_kernel.SetNamedPipeHandleState.restype = wintypes.BOOL
_kernel.GetNamedPipeClientProcessId.argtypes = [_HANDLE, ctypes.POINTER(wintypes.ULONG)]
_kernel.GetNamedPipeClientProcessId.restype = wintypes.BOOL
_kernel.GetNamedPipeServerProcessId.argtypes = [_HANDLE, ctypes.POINTER(wintypes.ULONG)]
_kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
_kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_kernel.ProcessIdToSessionId.restype = wintypes.BOOL


def _error():
    raise ctypes.WinError(ctypes.get_last_error())


def _name(name):
    if not isinstance(name, str) or not re.fullmatch(r'CWNG-M0-[0-9a-f]{32}', name):
        raise ValueError('invalid pipe name')
    return '\\\\.\\pipe\\' + name


def _key(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError('control key must be 32 random bytes')
    return key


def _token_sid(information_class, process=None):
    token = _HANDLE()
    if not _advapi.OpenProcessToken(process or _kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        _error()
    try:
        needed = wintypes.DWORD()
        _advapi.GetTokenInformation(token, information_class, None, 0, ctypes.byref(needed))
        if not needed.value:
            _error()
        buffer = ctypes.create_string_buffer(needed.value)
        if not _advapi.GetTokenInformation(token, information_class, buffer, len(buffer), ctypes.byref(needed)):
            _error()
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        string = ctypes.c_void_p()
        if not _advapi.ConvertSidToStringSidW(sid, ctypes.byref(string)):
            _error()
        try:
            return ctypes.wstring_at(string)
        finally:
            _kernel.LocalFree(string)
    finally:
        _kernel.CloseHandle(token)


def _is_appcontainer():
    token, value, needed = _HANDLE(), wintypes.DWORD(), wintypes.DWORD()
    if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        _error()
    try:
        if not _advapi.GetTokenInformation(token, 29, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(needed)):
            _error()
        return bool(value.value)
    finally:
        _kernel.CloseHandle(token)


def _pause(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('control pipe timed out')
    time.sleep(0.01)


def _write(handle, data, deadline):
    view = memoryview(data)
    while view:
        written = wintypes.DWORD()
        chunk = bytes(view)
        if not _kernel.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), None):
            error = ctypes.get_last_error()
            if error == 232:  # ERROR_NO_DATA in nonblocking mode
                _pause(deadline)
                continue
            raise ctypes.WinError(error)
        if not written.value:
            raise ConnectionError('pipe write made no progress')
        view = view[written.value:]


def _read(handle, size, deadline):
    result = bytearray()
    while len(result) < size:
        buffer = ctypes.create_string_buffer(size - len(result))
        count = wintypes.DWORD()
        if not _kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            error = ctypes.get_last_error()
            if error == 232:
                _pause(deadline)
                continue
            raise ctypes.WinError(error)
        if not count.value:
            raise ConnectionError('pipe closed')
        result.extend(buffer.raw[:count.value])
    return bytes(result)


def _send_json(handle, value, deadline):
    payload = json.dumps(value, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(payload) > _MAX_MESSAGE:
        raise ValueError('control message too large')
    _write(handle, struct.pack('!I', len(payload)) + payload, deadline)


def _receive_json(handle, deadline):
    size = struct.unpack('!I', _read(handle, 4, deadline))[0]
    if size > _MAX_MESSAGE:
        raise ValueError('control message too large')
    value = json.loads(_read(handle, size, deadline).decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('control message must be an object')
    return value


def _same_user_and_session(handle, *, peer_is_client):
    peer = wintypes.ULONG()
    own_session, peer_session = wintypes.DWORD(), wintypes.DWORD()
    get_peer = _kernel.GetNamedPipeClientProcessId if peer_is_client else _kernel.GetNamedPipeServerProcessId
    if not get_peer(handle, ctypes.byref(peer)):
        _error()
    if not _kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(own_session)):
        _error()
    if not _kernel.ProcessIdToSessionId(peer.value, ctypes.byref(peer_session)):
        _error()
    if own_session.value != peer_session.value:
        return False
    process = _kernel.OpenProcess(0x1000, False, peer.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        _error()
    try:
        return _token_sid(1, process) == _token_sid(1)
    finally:
        _kernel.CloseHandle(process)


class ControlPipe:
    """Single-client pipe; owner must hold profile ownership for its lifetime."""

    def __init__(self, name, key, *, timeout=5):
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError('invalid control timeout')
        self._timeout = timeout
        self._key = _key(key)
        self.name = _name(name)
        descriptor = ctypes.c_void_p()
        # Match the owner's mandatory integrity level, so lower-integrity
        # processes cannot write to a normal-integrity controller's pipe.
        appcontainer = f'(A;;GA;;;{_token_sid(31)})' if _is_appcontainer() else ''
        sddl = f'D:P(A;;GA;;;{_token_sid(1)})(A;;GA;;;SY){appcontainer}S:(ML;;NW;;;{_token_sid(25)})'
        if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            _error()
        try:
            security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            # First-instance rejects name squatting; mode 8 rejects remote clients.
            self._handle = _kernel.CreateNamedPipeW(self.name, 0x00000003 | 0x00080000,
                                                    0x00000008 | 0x00000001, 1, 8192, 8192, 0,
                                                    ctypes.byref(security))
            if self._handle == _INVALID:
                _error()
        finally:
            _kernel.LocalFree(descriptor)

    def serve_one(self, handler):
        deadline = time.monotonic() + self._timeout
        while not _kernel.ConnectNamedPipe(self._handle, None):
            error = ctypes.get_last_error()
            if error == 535:  # ERROR_PIPE_CONNECTED
                break
            if error != 536:  # ERROR_PIPE_LISTENING
                raise ctypes.WinError(error)
            _pause(deadline)
        try:
            if not _same_user_and_session(self._handle, peer_is_client=True):
                raise PermissionError('control peer has a different user or session')
            client_nonce = _read(self._handle, 32, deadline)
            nonce = secrets.token_bytes(32)
            _write(self._handle, nonce, deadline)
            proof = _read(self._handle, 32, deadline)
            expected = hmac.digest(self._key, b'client' + nonce + client_nonce, 'sha256')
            if not hmac.compare_digest(proof, expected):
                raise PermissionError('control authentication failed')
            _write(self._handle, hmac.digest(self._key, b'server' + nonce + client_nonce, 'sha256'), deadline)
            request = _receive_json(self._handle, deadline)
            response = handler(request)
            if not isinstance(response, dict):
                raise TypeError('control handler must return an object')
            _send_json(self._handle, response, deadline)
            if _read(self._handle, 1, deadline) != b'\x06':
                raise ValueError('invalid control acknowledgement')
        finally:
            _kernel.DisconnectNamedPipe(self._handle)

    def close(self):
        if self._handle:
            if not _kernel.CloseHandle(self._handle):
                _error()
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def request(name, key, message, *, timeout=5):
    key = _key(key)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ValueError('invalid control timeout')
    deadline = time.monotonic() + timeout
    handle = _kernel.CreateFileW(_name(name), 0xC0000000, 0, None, 3, 0, None)
    if handle == _INVALID:
        _error()
    try:
        if not _same_user_and_session(handle, peer_is_client=False):
            raise PermissionError('controller has a different user or session')
        mode = wintypes.DWORD(1)  # PIPE_NOWAIT
        if not _kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            _error()
        client_nonce = secrets.token_bytes(32)
        _write(handle, client_nonce, deadline)
        nonce = _read(handle, 32, deadline)
        _write(handle, hmac.digest(key, b'client' + nonce + client_nonce, 'sha256'), deadline)
        proof = _read(handle, 32, deadline)
        if not hmac.compare_digest(proof, hmac.digest(key, b'server' + nonce + client_nonce, 'sha256')):
            raise PermissionError('controller authentication failed')
        _send_json(handle, message, deadline)
        response = _receive_json(handle, deadline)
        _write(handle, b'\x06', deadline)
        return response
    finally:
        _kernel.CloseHandle(handle)
