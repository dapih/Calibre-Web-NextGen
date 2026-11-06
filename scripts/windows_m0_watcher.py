"""Experimental Windows ingest watcher; notification is armed before scanning."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

from windows_m0_inbox import _volume_identity


class _Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                ('offset', wintypes.DWORD), ('offset_high', wintypes.DWORD), ('event', wintypes.HANDLE)]


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel.CreateFileW.restype = wintypes.HANDLE
_kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel.CreateEventW.restype = wintypes.HANDLE
_kernel.ResetEvent.argtypes = [wintypes.HANDLE]
_kernel.ResetEvent.restype = wintypes.BOOL
_kernel.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
                                          wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(_Overlapped), ctypes.c_void_p]
_kernel.ReadDirectoryChangesW.restype = wintypes.BOOL
_kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
_kernel.GetOverlappedResult.restype = wintypes.BOOL
_kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel.WaitForSingleObject.restype = wintypes.DWORD
_kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
_kernel.CancelIoEx.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
_INVALID = ctypes.c_void_p(-1).value
_FORMATS = frozenset('acsm lcpl azw azw3 azw4 cbz cbr cb7 cbc chm djvu docx epub fb2 fbz html htmlz kepub kfx lit lrf mobi odt pdf prc pdb pml rb rtf snb tcr txtz txt m4b m4a mp4'.split())


class IngestWatcher:
    """Arm a native notification before initial scan, then periodically rescan.

    This object is single-threaded. The caller supplies an Inbox and dispatches
    pending job ids; it must preserve the controller's private-state policy.
    """

    def __init__(self, inbox, on_pending, *, scan_interval=1.0, clock=time.monotonic):
        if not callable(on_pending) or not 0 < scan_interval <= 60:
            raise ValueError('invalid watcher callback or scan interval')
        self.inbox = inbox
        self.on_pending = on_pending
        self.interval = scan_interval
        self.clock = clock
        self._handle = None
        self._event = None
        self._overlapped = None
        self._buffer = None
        self._pending = False
        self._next_scan = None
        self._announced = set()

    def start(self):
        if self._handle is not None:
            raise RuntimeError('watcher already started')
        path = self.inbox.source_dir
        if _volume_identity(path) != self.inbox._source_volume:
            raise OSError('ingest volume changed')
        # FILE_LIST_DIRECTORY, share all, BACKUP_SEMANTICS | OVERLAPPED.
        handle = _kernel.CreateFileW(str(path), 0x0001, 7, None, 3, 0x42000000, None)
        if handle == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        self._event = _kernel.CreateEventW(None, True, False, None)
        if not self._event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self._buffer = ctypes.create_string_buffer(32768)
        self._overlapped = _Overlapped()
        self._overlapped.event = self._event
        try:
            self._arm()
            # The OS notification is already outstanding at this point.
            self.scan()
        except BaseException:
            self.close()
            raise

    def _arm(self):
        if not _kernel.ResetEvent(self._event):
            raise ctypes.WinError(ctypes.get_last_error())
        self._overlapped = _Overlapped()
        self._overlapped.event = self._event
        if not _kernel.ReadDirectoryChangesW(self._handle, self._buffer, len(self._buffer), True,
                                              0x0000005B, None, ctypes.byref(self._overlapped), None):
            error = ctypes.get_last_error()
            if error != 997:  # ERROR_IO_PENDING
                raise ctypes.WinError(error)
        self._pending = True

    def scan(self):
        if self._handle is None:
            raise RuntimeError('watcher is not started')
        if _volume_identity(self.inbox.source_dir) != self.inbox._source_volume:
            raise OSError('ingest volume changed')
        def fail(error):
            raise error
        for current, dirs, files in os.walk(self.inbox.source_dir, followlinks=False, onerror=fail):
            base = Path(current)
            safe_dirs = []
            for name in dirs:
                info = (base / name).stat(follow_symlinks=False)
                if not getattr(info, 'st_file_attributes', 0) & 0x400:
                    safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in files:
                if Path(name).suffix.lower().lstrip('.') not in _FORMATS:
                    continue
                try:
                    job_id = self.inbox.observe(base / name)
                except FileNotFoundError:
                    continue
                if job_id and job_id not in self._announced:
                    record = self.inbox.coordinator.ledger.get(job_id)
                    if record and record['state'] == 'pending':
                        self.on_pending(job_id)
                        self._announced.add(job_id)
        self._next_scan = self.clock() + self.interval

    def poll(self, timeout=1.0):
        if self._handle is None:
            raise RuntimeError('watcher is not started')
        if not 0 <= timeout <= 60:
            raise ValueError('invalid watcher timeout')
        delay = max(0.0, min(timeout, self._next_scan - self.clock()))
        result = _kernel.WaitForSingleObject(self._event, int(delay * 1000))
        if result == 0:
            size = wintypes.DWORD()
            if not _kernel.GetOverlappedResult(self._handle, ctypes.byref(self._overlapped), ctypes.byref(size), False):
                error = ctypes.get_last_error()
                if error != 1022:  # ERROR_NOTIFY_ENUM_DIR: rescan after overflow
                    raise ctypes.WinError(error)
            self._pending = False
            # Re-arm before walking the tree, including after an overflow.
            self._arm()
            self.scan()
        elif result == 0x102:
            if self.clock() >= self._next_scan:
                self.scan()
        else:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self._handle is not None:
            if self._pending:
                if not _kernel.CancelIoEx(self._handle, ctypes.byref(self._overlapped)):
                    error = ctypes.get_last_error()
                    if error != 1168:  # ERROR_NOT_FOUND: completion already won the race
                        raise ctypes.WinError(error)
                result = _kernel.WaitForSingleObject(self._event, 5000)
                if result == 0x102:
                    # Keep the buffer and handles alive; retry rather than
                    # releasing memory still owned by an outstanding I/O.
                    raise TimeoutError('directory watcher cancellation did not finish')
                if result != 0:
                    raise ctypes.WinError(ctypes.get_last_error())
                size = wintypes.DWORD()
                _kernel.GetOverlappedResult(self._handle, ctypes.byref(self._overlapped), ctypes.byref(size), False)
                self._pending = False
            _kernel.CloseHandle(self._handle)
            self._handle = None
        if self._event:
            _kernel.CloseHandle(self._event)
            self._event = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
