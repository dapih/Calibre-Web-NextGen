"""Build the experimental native recovery inventory; never starts recovery.

The digest returned to the caller must be protected independently before this
format can be used as a trust anchor. Publication into a maintenance journal
and adopting a maintenance gate are intentionally not implemented here.
"""
import hashlib
import os
import ctypes
from ctypes import wintypes
from pathlib import Path
import struct


def _no_named_streams(path):
    class StreamData(ctypes.Structure):
        _fields_ = [('size', ctypes.c_longlong), ('name', wintypes.WCHAR * 296)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    kernel.FindNextStreamW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    data = StreamData()
    handle = kernel.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        if ctypes.get_last_error() == 38:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        while True:
            if data.name != '::$DATA':
                raise ValueError('payload contains a named stream')
            if not kernel.FindNextStreamW(handle, ctypes.byref(data)):
                if ctypes.get_last_error() != 38:
                    raise ctypes.WinError(ctypes.get_last_error())
                break
    finally:
        kernel.FindClose(handle)


def build_inventory(payload):
    """Return a deterministic UTF-16 binary inventory of the entire tree."""
    root = Path(payload)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('payload must be an absolute directory')
    if os.path.normpath(str(root)) != str(root):
        raise ValueError('payload path must be canonical')
    for part in (root, *root.parents):
        if part.stat(follow_symlinks=False).st_file_attributes & 0x400:
            raise ValueError('payload path has a reparse point')
    _no_named_streams(root)
    entries = []

    def fail(error):
        raise error

    for current, directories, files in os.walk(root, followlinks=False, onerror=fail):
        for name, is_directory in [(name, True) for name in directories] + [(name, False) for name in files]:
            path = Path(current) / name
            before = path.stat(follow_symlinks=False)
            if before.st_file_attributes & 0x400:
                raise ValueError('payload contains a reparse point')
            if not is_directory and before.st_nlink != 1:
                raise ValueError('payload contains a hard-linked file')
            if not name or name[-1] in '. ' or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name):
                raise ValueError('payload contains an unsafe name')
            _no_named_streams(path)
            relative = str(path.relative_to(root))
            if is_directory:
                digest = bytes(32)
            else:
                digest_object = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest_object.update(block)
                after = path.stat(follow_symlinks=False)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('payload changed during inventory')
                digest = digest_object.digest()
            entries.append((relative, is_directory, digest))
            if len(entries) > 500000:
                raise ValueError('payload contains too many entries for recovery verification')
    # Windows wchar_t comparison is UTF-16 code-unit order, not Python's
    # Unicode scalar order. Big-endian encoding preserves that order.
    entries.sort(key=lambda item: item[0].encode('utf-16-be', 'surrogatepass'))
    result = bytearray(b'CWNGINV1' + struct.pack('<I', len(entries)))
    for relative, is_directory, digest in entries:
        name = relative.encode('utf-16-le', 'surrogatepass')
        result.extend(struct.pack('<IB', len(name) // 2, int(is_directory)))
        result.extend(name)
        result.extend(digest)
        if len(result) > 128 * 1024 * 1024:
            raise ValueError('recovery inventory exceeds the native verifier limit')
    return bytes(result)
