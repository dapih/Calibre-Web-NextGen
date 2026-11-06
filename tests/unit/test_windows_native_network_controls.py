import os
from pathlib import Path
import sys
import threading

import pytest

if os.name != 'nt':
    pytest.skip('Windows native network', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_network import read_settings, set_lan_enabled
from windows_native_network_controls import NetworkControls


def test_explicit_consent_displays_candidate_device_address(tmp_path):
    messages, finished = [], threading.Event()
    def message(text, flags=0):
        messages.append(text)
        if flags:
            return 6
        finished.set()
        return 1
    controls = NetworkControls(tmp_path, 8083, message=message,
                               discover=lambda: [('192.168.1.2', 4)])
    controls.open()
    assert finished.wait(5)
    assert read_settings(tmp_path)['lan_enabled'] is True
    assert 'http://192.168.1.2:8083/' in messages[0]
    assert 'Firewall' in messages[0]
    controls.close()


def test_closing_while_consent_dialog_is_open_cannot_enable_network(tmp_path):
    opened, release, finished = threading.Event(), threading.Event(), threading.Event()
    def message(text, flags=0):
        opened.set()
        release.wait(5)
        finished.set()
        return 6
    controls = NetworkControls(tmp_path, 8083, message=message, discover=lambda: [])
    controls.open()
    assert opened.wait(5)
    controls.close()
    release.set()
    assert finished.wait(5)
    assert read_settings(tmp_path)['lan_enabled'] is False


def test_declining_disables_existing_consent(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    finished = threading.Event()
    def message(text, flags=0):
        if flags:
            return 7
        finished.set()
        return 1
    controls = NetworkControls(tmp_path, 8083, message=message, discover=lambda: [])
    controls.open()
    assert finished.wait(5)
    assert read_settings(tmp_path)['lan_enabled'] is False
    controls.close()


def test_discovery_and_modal_work_do_not_block_controller(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def discover():
        entered.set()
        release.wait(5)
        return []
    controls = NetworkControls(tmp_path, 8083, message=lambda *_: 2, discover=discover)
    controls.open()
    assert entered.wait(5)
    # The controller remains able to stop while discovery is waiting.
    controls.close()
    release.set()


def test_listener_bind_failure_is_not_presented_as_reachable(tmp_path):
    set_lan_enabled(tmp_path, True, consent=True)
    seen, finished = [], threading.Event()
    def message(text, _flags=0):
        seen.append(text)
        finished.set()
        return 2
    controls = NetworkControls(tmp_path, 8083, message=message,
        discover=lambda: [('192.168.1.2', 4)],
        listener_status=lambda: {'listening': [], 'error': 'address already in use'})
    controls.open()
    assert finished.wait(5)
    assert 'listener is not active' in seen[0]
    assert 'address already in use' in seen[0]
    assert 'Listener bound on' not in seen[0]
    controls.close()
