"""Native network consent, recovery pause and local port behavior."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

if os.name != 'nt':
    pytest.skip('Windows native network', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import windows_native_network as network


def test_default_and_explicit_consent_survive_restart(tmp_path):
    assert network.read_settings(tmp_path) == {'schema': 1, 'lan_enabled': False, 'recovery_paused': False}
    with pytest.raises(ValueError, match='consent'):
        network.set_lan_enabled(tmp_path, True)
    network.set_lan_enabled(tmp_path, True, consent=True)
    assert network.read_settings(tmp_path)['lan_enabled'] is True
    network.set_lan_enabled(tmp_path, False)
    assert network.read_settings(tmp_path)['lan_enabled'] is False


def test_credential_recovery_revokes_consent_until_explicitly_granted(tmp_path):
    network.set_lan_enabled(tmp_path, True, consent=True)
    network.pause_for_recovery(tmp_path)
    assert network.read_settings(tmp_path) == {'schema': 1, 'lan_enabled': False, 'recovery_paused': True}
    with pytest.raises(ValueError, match='consent'):
        network.set_lan_enabled(tmp_path, True)
    assert network.read_settings(tmp_path)['recovery_paused'] is True
    network.set_lan_enabled(tmp_path, True, consent=True)
    assert network.read_settings(tmp_path) == {'schema': 1, 'lan_enabled': True, 'recovery_paused': False}


def test_corrupt_settings_do_not_restore_network_consent(tmp_path):
    path = tmp_path / 'network.json'
    path.write_text('{bad', encoding='utf-8')
    with pytest.raises(ValueError, match='invalid'):
        network.read_settings(tmp_path)
    with pytest.raises(ValueError, match='invalid'):
        network.pause_for_recovery(tmp_path)
    assert path.read_text(encoding='utf-8') == '{bad'


def test_recovery_pause_does_not_report_success_when_flush_fails(tmp_path, monkeypatch):
    network.set_lan_enabled(tmp_path, True, consent=True)
    def fail(_directory):
        raise OSError('disk unavailable')
    monkeypatch.setattr(network, '_flush_directory', fail)
    with pytest.raises(OSError, match='disk unavailable'):
        network.pause_for_recovery(tmp_path)
    # A caller must not modify credentials after a failure. If the replacement
    # reached disk, it is nevertheless conservative rather than enabling LAN.
    assert network.read_settings(tmp_path)['lan_enabled'] is False


def test_port_conflict_does_not_touch_the_other_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        assert network.check_loopback_port(port) is False
        assert other.getsockname()[1] == port
    assert network.check_loopback_port(port) is True


def test_private_addresses_are_filtered_and_deduplicated(monkeypatch):
    output = json.dumps([
        {'address': '192.168.1.3', 'interface': 2, 'prefix': 24},
        {'address': '192.168.1.3', 'interface': 2, 'prefix': 24},
        {'address': '169.254.2.3', 'interface': 2, 'prefix': 16},
        {'address': '127.0.0.1', 'interface': 2, 'prefix': 8},
    ]).encode()
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, output, b''))
    assert network.private_ipv4_addresses() == [('192.168.1.3', 2)]


@pytest.mark.parametrize('output', [b'not-json', b'{}', b'[{"address":"192.168.1.1","interface":"2"}]'])
def test_bad_profile_data_fails_closed(monkeypatch, output):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, output, b''))
    with pytest.raises(OSError, match='profile'):
        network.private_ipv4_addresses()


def test_profile_query_failure_fails_closed(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(OSError, match='profile'):
        network.private_ipv4_addresses()
