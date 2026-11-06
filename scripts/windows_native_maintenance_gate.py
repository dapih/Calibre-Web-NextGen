"""Exclusive side of the stable native bootstrap/maintenance gate.

Acquire before profile ownership. Bootstrap keeps a shared read handle while
its Python child is alive. Inheriting that handle also keeps the gate closed if
the bootstrap dies before the child. A sharing violation must stop maintenance.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from pathlib import Path

from windows_m0_inbox import _FileInformation


@contextmanager
def maintenance_gate(state):
    """Hold the exclusive Windows sharing gate for a validated private profile."""
    state = Path(state)
    if not state.is_absolute() or not state.is_dir():
        raise ValueError('maintenance gate requires an existing absolute profile')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    # OPEN_ALWAYS; do not follow a final reparse point. Exclusive sharing is
    # essential: bootstrap holds a share-read handle for its child's lifetime.
    handle = kernel.CreateFileW(str(state / 'bootstrap-maintenance.lock'), 0xc0000000, 0, None, 4,
                                0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileInformation()
        if kernel.GetFileType(handle) != 1 or not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError('maintenance gate is not a readable disk file')
        if info.attributes & (0x400 | 0x10):
            raise ValueError('maintenance gate cannot be a reparse point or directory')
        yield
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def adopt_maintenance_gate(state, inherited_handle):
    """Adopt a native parent's already-exclusive inherited handle.

    This internal seam does not authenticate the parent. A trusted native
    launcher must create the handle with share=0 and pass it by a restricted
    handle allowlist; do not expose a caller-selected handle in a public CLI.
    """
    state = Path(state)
    if not state.is_absolute() or not state.is_dir() or type(inherited_handle) is not int or inherited_handle <= 0:
        raise ValueError('invalid inherited maintenance gate')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtQueryObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                   ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryObject.restype = ctypes.c_long
    invalid = ctypes.c_void_p(-1).value
    # ObjectBasicInformation returns the mapped granted-access mask. Both
    # data rights must be present, otherwise another handle could account for
    # a conflicting-open probe while this inherited handle permits sharing.
    basic = (wintypes.ULONG * 14)()
    if ntdll.NtQueryObject(inherited_handle, 0, basic, ctypes.sizeof(basic), None) != 0 or basic[1] & 3 != 3:
        raise ValueError('inherited maintenance gate lacks read/write access')
    info = _FileInformation()
    if (kernel.GetFileType(inherited_handle) != 1 or
            not kernel.GetFileInformationByHandle(inherited_handle, ctypes.byref(info)) or
            info.attributes & (0x400 | 0x10)):
        raise ValueError('inherited maintenance gate is not a regular file')
    gate = state / 'bootstrap-maintenance.lock'
    identity = (info.volume, info.index_high, info.index_low)
    # A zero-access metadata handle is compatible with the no-share gate.
    path_handle = kernel.CreateFileW(str(gate), 0, 7, None, 3, 0x00200080, None)
    if path_handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        current = _FileInformation()
        if (not kernel.GetFileInformationByHandle(path_handle, ctypes.byref(current)) or
                current.attributes & (0x400 | 0x10) or
                (current.volume, current.index_high, current.index_low) != identity):
            raise ValueError('inherited maintenance gate does not match the profile')
    finally:
        kernel.CloseHandle(path_handle)
    for desired in (0x80000000, 0x40000000):
        probe = kernel.CreateFileW(str(gate), desired, 7, None, 3, 0x00200080, None)
        if probe != invalid:
            kernel.CloseHandle(probe)
            raise ValueError('inherited maintenance gate does not exclude other opens')
        if ctypes.get_last_error() != 32:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(inherited_handle)
