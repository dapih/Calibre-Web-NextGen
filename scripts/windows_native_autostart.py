"""Optional per-user logon startup for an installed native launcher.

The caller supplies the stable installer-owned executable, never the private
version-specific Python runtime. Nothing is registered until the user opts in.
"""

from pathlib import Path
import winreg


_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_VALUE_NAME = 'CWNG-dapih'


def _command(executable, state):
    executable, state = Path(executable), Path(state)
    if not executable.is_absolute() or not state.is_absolute():
        raise ValueError('startup requires absolute program and state paths')
    executable, state = executable.resolve(strict=True), state.resolve(strict=True)
    if not executable.is_file() or executable.suffix.lower() != '.exe' or not state.is_dir():
        raise ValueError('startup requires an existing launcher and state directory')
    if any(c in str(executable) + str(state) for c in ('"', '\x00', '\r', '\n')):
        raise ValueError('invalid startup path')
    return f'"{executable}" --state "{state}"'


def _value(key):
    try:
        return winreg.QueryValueEx(key, _VALUE_NAME)
    except FileNotFoundError:
        return None


def is_enabled(executable, state):
    """Return current setting, refusing to mistake another entry for ours."""
    expected = _command(executable, state)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            current = _value(key)
    except FileNotFoundError:
        return False
    if current is None:
        return False
    if current != (expected, winreg.REG_SZ):
        raise ValueError('a different startup entry uses the CWNG name')
    return True


def set_enabled(executable, state, enabled):
    """Change only our exact startup value; preserve any foreign entry."""
    if not isinstance(enabled, bool):
        raise ValueError('startup choice must be boolean')
    expected = _command(executable, state)
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    try:
        if enabled:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, access)
        else:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, access)
    except FileNotFoundError:
        return
    with key:
        current = _value(key)
        if current is not None and current != (expected, winreg.REG_SZ):
            raise ValueError('a different startup entry uses the CWNG name')
        if enabled and current is None:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, expected)
        elif not enabled and current is not None:
            winreg.DeleteValue(key, _VALUE_NAME)
