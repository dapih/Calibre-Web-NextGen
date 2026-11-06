"""Shared installation lifetime gate for standalone native entrypoints."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from windows_m0_inbox import _FileInformation


@contextmanager
def install_gate():
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    if not local.is_absolute() or not local.is_dir():
        raise ValueError('local application directory is unavailable')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(local / 'CWNG-install.lock'), 0x80000000, 1, None, 4, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileInformation()
        if (kernel.GetFileType(handle) != 1 or
                not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)) or
                info.attributes & (0x400 | 0x10)):
            raise ValueError('installation gate must be a regular local file')
        yield
    finally:
        kernel.CloseHandle(handle)
