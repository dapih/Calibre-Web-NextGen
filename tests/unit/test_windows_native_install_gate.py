"""Installer exclusion for independently started native commands."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

import pytest

if os.name != 'nt':
    pytest.skip('Windows install gate', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_install_gate import install_gate


def _exclusive(local):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(local / 'CWNG-install.lock'), 0xc0000000, 0, None, 4, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def test_install_gate_excludes_installer_and_allows_other_commands(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    with install_gate():
        with install_gate():
            with pytest.raises(OSError):
                _exclusive(tmp_path)
    kernel, handle = _exclusive(tmp_path)
    try:
        with pytest.raises(OSError):
            with install_gate():
                pass
    finally:
        kernel.CloseHandle(handle)


def test_install_gate_rejects_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    (tmp_path / 'CWNG-install.lock').mkdir()
    with pytest.raises(OSError):
        with install_gate():
            pass


def test_independent_launcher_is_fenced_before_profile_access(tmp_path, monkeypatch):
    import windows_native_launcher as launcher
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    called = []
    monkeypatch.setattr(launcher, '_main', lambda argv: called.append(argv))
    kernel, handle = _exclusive(tmp_path)
    try:
        with pytest.raises(OSError):
            launcher.main(['--review'])
        assert not called
    finally:
        kernel.CloseHandle(handle)
    launcher.main(['--review'])
    assert called == [['--review']]
