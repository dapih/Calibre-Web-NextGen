"""Windows bootstrap and maintenance handles exclude each other."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

import pytest

if os.name != 'nt':
    pytest.skip('Windows maintenance gate', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_maintenance_gate import adopt_maintenance_gate, maintenance_gate


def _bootstrap_gate(state):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(state / 'bootstrap-maintenance.lock'), 0x80000000, 1, None, 4,
                                0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def test_live_bootstrap_blocks_maintenance(tmp_path):
    kernel, handle = _bootstrap_gate(tmp_path)
    try:
        with pytest.raises(OSError):
            with maintenance_gate(tmp_path):
                pass
    finally:
        kernel.CloseHandle(handle)
    with maintenance_gate(tmp_path):
        pass


def test_maintenance_blocks_bootstrap_until_release(tmp_path):
    with maintenance_gate(tmp_path):
        with pytest.raises(OSError):
            _bootstrap_gate(tmp_path)
    kernel, handle = _bootstrap_gate(tmp_path)
    kernel.CloseHandle(handle)


def test_gate_rejects_directory_at_lock_path(tmp_path):
    (tmp_path / 'bootstrap-maintenance.lock').mkdir()
    with pytest.raises(OSError):
        with maintenance_gate(tmp_path):
            pass


def _native_exclusive_gate(state):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(state / 'bootstrap-maintenance.lock'), 0xc0000000, 0, None, 4,
                                0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def test_adopted_gate_excludes_bootstrap_and_closes_after_use(tmp_path):
    _, handle = _native_exclusive_gate(tmp_path)
    with adopt_maintenance_gate(tmp_path, handle):
        with pytest.raises(OSError):
            _bootstrap_gate(tmp_path)
    kernel, again = _bootstrap_gate(tmp_path)
    kernel.CloseHandle(again)


def test_adoption_rejects_wrong_profile_and_shared_handles(tmp_path):
    first, second = tmp_path / 'one', tmp_path / 'two'
    first.mkdir()
    second.mkdir()
    kernel, wrong = _native_exclusive_gate(first)
    other_kernel, other = _native_exclusive_gate(second)
    other_kernel.CloseHandle(other)
    try:
        with pytest.raises(ValueError, match='does not match'):
            with adopt_maintenance_gate(second, wrong):
                pass
    finally:
        kernel.CloseHandle(wrong)
    kernel, shared = _bootstrap_gate(first)
    try:
        with pytest.raises(ValueError, match='read/write access'):
            with adopt_maintenance_gate(first, shared):
                pass
    finally:
        kernel.CloseHandle(shared)
    shared = kernel.CreateFileW(str(first / 'bootstrap-maintenance.lock'), 0xc0000000, 7, None, 3,
                                0x00200080, None)
    assert shared != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(ValueError, match='does not exclude'):
            with adopt_maintenance_gate(first, shared):
                pass
    finally:
        kernel.CloseHandle(shared)


def test_inherited_gate_keeps_exclusion_after_parent_handle_closes(tmp_path):
    kernel, handle = _native_exclusive_gate(tmp_path)
    os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {'handle_list': [handle]}
    source = r'''
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from windows_native_maintenance_gate import adopt_maintenance_gate
with adopt_maintenance_gate(pathlib.Path(sys.argv[2]), int(sys.argv[3])):
    print('ready', flush=True)
    sys.stdin.readline()
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', source,
        str(Path(__file__).resolve().parents[2] / 'scripts'), str(tmp_path), str(handle)],
        startupinfo=startup, close_fds=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        kernel.CloseHandle(handle)
        handle = None
        with pytest.raises(OSError):
            _bootstrap_gate(tmp_path)
        child.stdin.write('done\n')
        child.stdin.flush()
        _, error = child.communicate(timeout=15)
        assert child.returncode == 0, error
        kernel2, next_handle = _bootstrap_gate(tmp_path)
        kernel2.CloseHandle(next_handle)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if handle is not None:
            kernel.CloseHandle(handle)
