import json
from pathlib import Path
import sys
import threading
import subprocess
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import windows_native_first_run as first
from windows_native_setup import SetupCancelled


def test_registered_sync_roots_parses_windows_result(monkeypatch, tmp_path):
    root = tmp_path / 'sync'
    root.mkdir()
    monkeypatch.setattr(first.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps([str(root)]).encode('utf-8')))
    assert first._registered_sync_roots() == [root.resolve()]


def test_registered_sync_roots_rejects_malformed_result(monkeypatch):
    monkeypatch.setattr(first.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'not json'))
    with pytest.raises(ValueError, match='cannot enumerate'):
        first._registered_sync_roots()


@pytest.mark.parametrize('error', [subprocess.TimeoutExpired('powershell', 15),
                                   subprocess.CalledProcessError(1, 'powershell'),
                                   OSError('unavailable')])
def test_registered_sync_roots_rejects_process_failure(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(first.subprocess, 'run', fail)
    with pytest.raises(ValueError, match='cannot enumerate'):
        first._registered_sync_roots()


@pytest.mark.parametrize('status,roots,allowed', [
    (0x80070001, [], True),
    (0x80070186, [], True),
    (0, [], False),
    (0x80070005, [], False),
    (0x80070001, ['nested'], False),
    (0x80070186, ['parent'], False),
])
def test_cloud_filter_and_registered_roots_fail_closed(monkeypatch, tmp_path, status, roots, allowed):
    selected = tmp_path / 'selected'
    selected.mkdir()
    nested = selected / 'nested'
    nested.mkdir()
    class ApiFunction:
        def __call__(self, *args):
            return status
    monkeypatch.setattr(first.ctypes, 'WinDLL', lambda *args, **kwargs: SimpleNamespace(CfGetSyncRootInfoByPath=ApiFunction()))
    registered = [nested if item == 'nested' else tmp_path for item in roots]
    monkeypatch.setattr(first, '_registered_sync_roots', lambda: registered)
    if allowed:
        first._cloud_check(selected)
    else:
        with pytest.raises(ValueError):
            first._cloud_check(selected)


def test_cancel_uses_setup_cancelled():
    event = threading.Event()
    event.set()
    with pytest.raises(SetupCancelled):
        first._check_cancel(event)
