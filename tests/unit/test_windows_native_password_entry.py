"""Password recovery remains local and starts only after the owner stops."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

if os.name == 'nt':
    import windows_native_launcher as launcher
    import windows_native_password as password


def test_command_line_opens_local_prompt_without_starting_web(monkeypatch):
    events = []
    controller = SimpleNamespace(start=lambda: events.append('web'))
    monkeypatch.setattr(launcher, '_read_profile', lambda _state: controller)
    monkeypatch.setattr(launcher, '_launch_setup', lambda _args: events.append('setup'))
    monkeypatch.setattr(password, 'prompt_and_recover', lambda value: events.append(('prompt', value)))
    assert launcher.main(['--state', 'fixture', '--recover-password']) == 0
    assert events == [('prompt', controller)]


@pytest.mark.parametrize('active,choice', [(False, None), (True, 7), (True, 6)])
def test_tray_recovery_stops_owner_before_prompt(monkeypatch, active, choice):
    events = []

    class Controller:
        def __init__(self):
            self.scheduler = SimpleNamespace(active=object() if active else None)
            self.finishing = False
            self.port = 18083
        def start(self):
            events.append('start')
        def step(self, timeout):
            return 'stopped' if self.finishing else 'idle'
        def stop_safely(self):
            events.append('stop')
        def finish_current(self):
            self.finishing = True
            events.append('finish')
        def close(self):
            events.append('closed')

    class Tray:
        def __init__(self):
            self.commands = self
        def start(self):
            pass
        def set_status(self, _status):
            pass
        def get_nowait(self):
            return 'recover-password'
        def close(self):
            events.append('tray-closed')

    controller = Controller()
    # These controls run their own UI/worker loops; this test isolates the
    # recovery handoff and verifies their cleanup before the local dialog.
    class Controls:
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(launcher, '_read_profile', lambda _state: controller)
    monkeypatch.setattr(launcher, '_launch_setup', lambda _args: True)
    monkeypatch.setattr(launcher, 'NativeTray', Tray)
    monkeypatch.setattr(launcher, 'NetworkControls', Controls)
    monkeypatch.setattr(launcher, 'UpdateControls', Controls)
    monkeypatch.setattr(launcher, '_message', lambda *_args: choice)
    monkeypatch.setattr(password, 'prompt_and_recover', lambda _controller: events.append('prompt'))
    assert launcher.main(['--state', 'unused']) == 0
    assert events.index('closed') < events.index('prompt')
    if choice == 7:
        assert events.index('stop') < events.index('closed')
    if choice == 6:
        assert events.index('finish') < events.index('closed')
