"""Experimental stable-file admission into a private Windows M0 ledger.

Callers supply private, already-validated local directories and must install a
watcher before their initial scan. Original files are never removed here.
"""

import ctypes
from ctypes import wintypes
import hashlib
import msvcrt
import os
from pathlib import Path
import stat
import threading
import time
import uuid


class _FileInformation(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                ('accessed', wintypes.FILETIME), ('modified', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel.CreateFileW.restype = wintypes.HANDLE
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
_kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
_kernel.FlushFileBuffers.restype = wintypes.BOOL
_kernel.GetFileType.argtypes = [wintypes.HANDLE]
_kernel.GetFileType.restype = wintypes.DWORD
_kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
_kernel.GetFileInformationByHandle.restype = wintypes.BOOL
_kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
_kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
_kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_kernel.GetVolumePathNameW.restype = wintypes.BOOL
_kernel.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_kernel.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
_kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_kernel.GetDriveTypeW.restype = wintypes.UINT
_kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                          ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
_kernel.GetVolumeInformationW.restype = wintypes.BOOL
_INVALID = ctypes.c_void_p(-1).value


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _volume_identity(path):
    mount = ctypes.create_unicode_buffer(32768)
    if not _kernel.GetVolumePathNameW(str(path), mount, len(mount)):
        raise ctypes.WinError(ctypes.get_last_error())
    if _kernel.GetDriveTypeW(mount.value) not in (2, 3):  # removable or fixed local disk
        raise ValueError('ingest and staging require local disk volumes')
    name = ctypes.create_unicode_buffer(1024)
    if not _kernel.GetVolumeNameForVolumeMountPointW(mount.value, name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    filesystem, serial = ctypes.create_unicode_buffer(256), wintypes.DWORD()
    if not _kernel.GetVolumeInformationW(mount.value, None, 0, ctypes.byref(serial), None, None,
                                         filesystem, len(filesystem)):
        raise ctypes.WinError(ctypes.get_last_error())
    if filesystem.value.upper() != 'NTFS':
        raise ValueError('ingest and staging require NTFS')
    return name.value.casefold(), serial.value


def _flush_directory(path):
    handle = _kernel.CreateFileW(str(path), 0x40000000, 7, None, 3, 0x02000000, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel.CloseHandle(handle)


def _locked_source(path, source_root, expected):
    # FILE_SHARE_READ only: existing writers prevent opening, and new writers
    # or deletion are denied until the copy and identity checks have finished.
    handle = _kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _kernel.GetFileType(handle) != 1:
            raise ValueError('ingest source must be a disk file')
        info = _FileInformation()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10):
            raise ValueError('ingest source must not be a reparse point or directory')
        final = ctypes.create_unicode_buffer(32768)
        length = _kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
        if not length or length >= len(final):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = final.value
        if actual.startswith('\\\\?\\UNC\\'):
            raise ValueError('network ingest sources are not supported')
        if actual.startswith('\\\\?\\'):
            actual = actual[4:]
        actual = Path(actual).resolve(strict=True)
        if actual != expected or not actual.is_relative_to(source_root):
            raise ValueError('ingest source path changed or escaped its directory')
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'rb')


class Inbox:
    """Observe stable files and commit staged intent without consuming sources."""

    def __init__(self, coordinator, source_dir, stage_dir, *, quiet_seconds=2.0, clock=time.monotonic):
        self.coordinator = coordinator
        self.source_dir = Path(source_dir).resolve(strict=True)
        self.stage_dir = Path(stage_dir).resolve(strict=True)
        state_dir = Path(coordinator.state_dir).resolve(strict=True)
        if not self.source_dir.is_dir() or not self.stage_dir.is_dir():
            raise ValueError('source and private staging directories must exist')
        if not self.stage_dir.is_relative_to(state_dir) or self.stage_dir == state_dir:
            raise ValueError('staging must be a private subdirectory of controller state')
        if self.source_dir.is_relative_to(state_dir) or state_dir.is_relative_to(self.source_dir):
            raise ValueError('ingest and controller state directories must not overlap')
        if (self.source_dir.is_relative_to(self.stage_dir) or
                self.stage_dir.is_relative_to(self.source_dir)):
            raise ValueError('source and staging directories must not overlap')
        self._source_volume = _volume_identity(self.source_dir)
        self._stage_volume = _volume_identity(self.stage_dir)
        if not 0 < quiet_seconds <= 3600:
            raise ValueError('invalid quiet interval')
        coordinator.ledger.bind_profile('ingest-storage', {
            'source': os.path.normcase(str(self.source_dir)), 'source_volume': self._source_volume,
            'stage': os.path.normcase(str(self.stage_dir)), 'stage_volume': self._stage_volume,
        })
        self.quiet_seconds = quiet_seconds
        self.clock = clock
        self._observed = {}
        self._guard = threading.RLock()

    def observe(self, source):
        """Return a durable job id only after stability, exclusive copy and commit.

        A busy/changing source returns None and must be observed again. Storage,
        ledger and identity errors propagate; they never delete the original.
        """
        with self._guard:
            return self._observe(source)

    def _observe(self, source):
        source = Path(source)
        if (_volume_identity(self.source_dir) != self._source_volume or
                _volume_identity(self.stage_dir) != self._stage_volume):
            raise OSError('ingest or staging volume changed; refusing admission')
        if not source.is_absolute():
            raise ValueError('source path must be absolute')
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(self.source_dir):
            raise ValueError('source is outside the ingest directory')
        info = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('source must be a regular non-reparse file')
        identity = _identity(info)
        if not info.st_ino:
            raise OSError('source identity is unavailable')
        previous = self._observed.get(str(source))
        now = self.clock()
        if previous is None or previous[0] != identity:
            self._observed[str(source)] = (identity, now)
            return None
        if now - previous[1] < self.quiet_seconds:
            return None
        try:
            stream = _locked_source(source, self.source_dir, resolved)
        except OSError as error:
            if getattr(error, 'winerror', None) in (32, 33):
                self._observed.pop(str(source), None)
                return None
            raise
        temporary = self.stage_dir / ('.incoming-' + uuid.uuid4().hex)
        try:
            with stream:
                if _identity(os.fstat(stream.fileno())) != identity:
                    self._observed.pop(str(source), None)
                    return None
                digest = hashlib.sha256()
                with temporary.open('xb') as output:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if _identity(os.fstat(stream.fileno())) != identity:
                    self._observed.pop(str(source), None)
                    return None
                sha256 = digest.hexdigest()
                suffix = source.suffix.lower()
                if len(suffix) > 20 or any(ch not in '.abcdefghijklmnopqrstuvwxyz0123456789' for ch in suffix):
                    raise ValueError('unsupported source extension')
                staged = self.stage_dir / (sha256 + suffix)
                try:
                    os.rename(temporary, staged)
                except FileExistsError:
                    existing_info = staged.stat(follow_symlinks=False)
                    if (not stat.S_ISREG(existing_info.st_mode) or existing_info.st_nlink != 1 or
                            getattr(existing_info, 'st_file_attributes', 0) & 0x400):
                        raise OSError('existing staged path is not a regular file')
                    with staged.open('rb') as existing:
                        if hashlib.file_digest(existing, 'sha256').hexdigest() != sha256:
                            raise OSError('existing staged file does not match its digest')
                _flush_directory(self.stage_dir)
                if (_volume_identity(self.source_dir) != self._source_volume or
                        _volume_identity(self.stage_dir) != self._stage_volume):
                    raise OSError('ingest or staging volume changed during copy')
                key = hashlib.sha256((str(resolved).casefold() + '\0' + sha256).encode('utf-8')).hexdigest()
                intent = {'source': str(resolved), 'staged': str(staged), 'sha256': sha256,
                          'size': identity[2]}
                job = self.coordinator.enqueue(key, 'ingest', intent)
                self._observed.pop(str(source), None)
                return job
        finally:
            temporary.unlink(missing_ok=True)
