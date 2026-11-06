import os
import sys
import uuid
from pathlib import Path

import pytest
from unittest.mock import patch

if os.name != 'nt':
    pytest.skip('Windows user startup registration', allow_module_level=True)

import winreg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import windows_native_autostart as autostart


@pytest.fixture
def isolated_run_key(monkeypatch):
    key_path = r'Software\CWNG-Native-Test-' + uuid.uuid4().hex
    monkeypatch.setattr(autostart, '_RUN_KEY', key_path)
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path).Close()
    except PermissionError:
        pytest.skip('current environment does not permit temporary HKCU registry writes')
    yield key_path
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass


def test_startup_is_opt_in_and_can_be_disabled(isolated_run_key, tmp_path):
    exe = tmp_path / 'CWNG Launcher.exe'
    exe.touch()
    state = tmp_path / 'Private State'
    state.mkdir()
    assert autostart.is_enabled(exe, state) is False
    autostart.set_enabled(exe, state, True)
    assert autostart.is_enabled(exe, state) is True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, isolated_run_key) as key:
        value, kind = winreg.QueryValueEx(key, autostart._VALUE_NAME)
    assert kind == winreg.REG_SZ
    assert value == f'"{exe}" --state "{state}"'
    autostart.set_enabled(exe, state, False)
    assert autostart.is_enabled(exe, state) is False


def test_existing_foreign_startup_entry_is_preserved(isolated_run_key, tmp_path):
    exe = tmp_path / 'CWNG Launcher.exe'
    exe.touch()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, isolated_run_key) as key:
        winreg.SetValueEx(key, autostart._VALUE_NAME, 0, winreg.REG_SZ, 'foreign command')
    for enabled in (True, False):
        with pytest.raises(ValueError, match='different startup entry'):
            autostart.set_enabled(exe, tmp_path, enabled)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, isolated_run_key) as key:
        assert winreg.QueryValueEx(key, autostart._VALUE_NAME)[0] == 'foreign command'


def test_tray_choice_requires_user_confirmation_and_displays_current_state(tmp_path):
    import windows_native_launcher as launcher
    executable, state = tmp_path / 'CWNG.exe', tmp_path / 'state'
    with patch.object(launcher, '_startup_executable', return_value=executable), \
            patch.object(launcher.windows_native_autostart, 'is_enabled', return_value=False), \
            patch.object(launcher.windows_native_autostart, 'set_enabled') as update, \
            patch.object(launcher, '_message', return_value=7) as message:
        launcher._toggle_login_startup(state)
        update.assert_not_called()
        assert 'Start CWNG automatically' in message.call_args.args[0]
    with patch.object(launcher, '_startup_executable', return_value=executable), \
            patch.object(launcher.windows_native_autostart, 'is_enabled', return_value=True), \
            patch.object(launcher.windows_native_autostart, 'set_enabled') as update, \
            patch.object(launcher, '_message', return_value=6) as message:
        launcher._toggle_login_startup(state)
        update.assert_called_once_with(executable, state, False)
        assert 'now off' in message.call_args.args[0]


def test_installed_payload_resolves_stable_launcher_not_versioned_python(tmp_path, monkeypatch):
    import windows_native_launcher as launcher
    program = tmp_path / 'Programs' / 'CWNG-dapih'
    payload_scripts = program / 'versions' / '1.0' / 'scripts'
    payload_scripts.mkdir(parents=True)
    stable = program / 'CWNG.exe'
    stable.touch()
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(launcher, '__file__', str(payload_scripts / 'windows_native_launcher.py'))
    assert launcher._startup_executable() == stable.resolve()
    monkeypatch.setattr(launcher, '__file__', str(tmp_path / 'elsewhere' / 'scripts' / 'windows_native_launcher.py'))
    with pytest.raises(ValueError, match='stable launcher'):
        launcher._startup_executable()
