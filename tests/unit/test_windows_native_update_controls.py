import json
import hashlib
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_update_controls import UpdateControls, UpdateManager, load_package_identity
from windows_native_update import UpdateCandidate


def package(root):
    (root / 'native-package.json').write_text(json.dumps({
        'schema': 1, 'repository': 'dapih/Calibre-Web-NextGen',
        'version': '4.1.62', 'revision': 0, 'architecture': 'x64',
    }), encoding='utf-8')


def test_package_identity_is_required_and_binds_the_fork(tmp_path):
    with pytest.raises(ValueError):
        load_package_identity(tmp_path)
    package(tmp_path)
    assert load_package_identity(tmp_path)['version'] == '4.1.62'
    document = json.loads((tmp_path / 'native-package.json').read_text())
    document['repository'] = 'someone/else'
    (tmp_path / 'native-package.json').write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_package_identity(tmp_path)


def test_automatic_checks_are_opt_in_and_throttled_across_restart(tmp_path):
    fetch = Mock(return_value=None)
    now = [100000.0]
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: now[0])
    assert manager.check_due() is False
    assert not fetch.called
    manager.set_preferences(automatic=True, channel='prerelease')
    assert manager.check_due() is True
    assert fetch.call_args.kwargs['channel'] == 'prerelease'
    manager = UpdateManager(tmp_path, manager.identity, 22631, fetch=fetch, clock=lambda: now[0])
    now[0] += 86399
    assert manager.check_due() is False
    now[0] += 1
    assert manager.check_due() is True
    assert fetch.call_count == 2


def test_manual_check_works_when_disabled_and_failure_is_throttled(tmp_path):
    fetch = Mock(side_effect=OSError('offline'))
    now = [100000.0]
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: now[0])
    manager.set_preferences(automatic=True, channel='stable')
    with pytest.raises(OSError):
        manager.check_due()
    assert manager.check_due() is False
    manager.set_preferences(automatic=False, channel='stable')
    with pytest.raises(OSError):
        manager.check_now()
    assert fetch.call_count == 2


def test_clock_moving_backwards_does_not_trigger_repeated_checks(tmp_path):
    fetch = Mock(return_value=None)
    now = [200000.0]
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: now[0])
    manager.set_preferences(automatic=True, channel='stable')
    assert manager.check_due() is True
    now[0] = 100000.0
    assert manager.check_due() is False
    assert fetch.call_count == 1


def test_corrupt_settings_fail_closed_without_network(tmp_path):
    (tmp_path / 'native-update.json').write_text('{')
    fetch = Mock()
    with pytest.raises(ValueError):
        UpdateManager(tmp_path, {}, 22631, fetch=fetch)
    assert not fetch.called


def test_changed_channel_discards_inflight_result(tmp_path):
    candidate = object()
    manager = None
    def fetch(**_kwargs):
        manager.set_preferences(automatic=True, channel='prerelease')
        return candidate
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: 100000.0)
    manager.set_preferences(automatic=True, channel='stable')
    assert manager.check_due() is True
    assert manager.last_candidate is None


def test_concurrent_checks_do_not_start_extra_requests(tmp_path):
    entered, finish = threading.Event(), threading.Event()
    def fetch(**_kwargs):
        entered.set()
        assert finish.wait(5)
        return None
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: 100000.0)
    manager.set_preferences(automatic=True, channel='stable')
    worker = threading.Thread(target=manager.check_now)
    worker.start()
    try:
        assert entered.wait(5)
        assert manager.check_due() is False
        with pytest.raises(RuntimeError, match='already in progress'):
            manager.check_now()
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()


def test_shutdown_fences_settings_and_new_checks(tmp_path):
    fetch = Mock()
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: 100000.0)
    manager.set_preferences(automatic=True, channel='stable')
    manager.shutdown()
    assert manager.check_due() is False
    with pytest.raises(RuntimeError, match='closed'):
        manager.check_now()
    with pytest.raises(RuntimeError, match='closed'):
        manager.set_preferences(automatic=False, channel='prerelease')
    assert not fetch.called


def test_automatic_candidate_is_not_shown_after_opt_out(tmp_path):
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe', 'a' * 64)
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=lambda **_kwargs: candidate, clock=lambda: 100000.0)
    manager.set_preferences(automatic=True, channel='stable')
    controls = UpdateControls(tmp_path, tmp_path)
    controls._manager = manager
    class DialogLock:
        def acquire(self, **_kwargs):
            # Simulate opt-out after the result returned, before dialog lock.
            manager.set_preferences(automatic=False, channel='prerelease')
            return True
        def release(self):
            pass
    controls._dialog = DialogLock()
    with patch.object(controls, '_show_candidate') as show, \
         patch.object(controls._stop, 'wait', return_value=True):
        controls._automatic()
    show.assert_not_called()


def test_automatic_notice_waits_for_existing_settings_dialog(tmp_path):
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe', 'a' * 64)
    fetched, shown = threading.Event(), threading.Event()
    def fetch(**_kwargs):
        fetched.set()
        return candidate
    manager = UpdateManager(tmp_path, {'repository': 'dapih/Calibre-Web-NextGen',
                                     'version': '4.1.62', 'revision': 0},
                            22631, fetch=fetch, clock=lambda: 100000.0)
    manager.set_preferences(automatic=True, channel='stable')
    controls = UpdateControls(tmp_path, tmp_path)
    controls._manager = manager
    controls._dialog.acquire()
    held_by_test = True
    with patch.object(controls, '_show_candidate', side_effect=lambda _candidate: shown.set()):
        worker = threading.Thread(target=controls._automatic)
        worker.start()
        try:
            assert fetched.wait(5)
            assert not shown.is_set()
            controls._dialog.release()
            held_by_test = False
            assert shown.wait(5)
        finally:
            if held_by_test:
                controls._dialog.release()
            controls.close()
            worker.join(5)
    assert not worker.is_alive()


def test_download_requires_user_choice_and_mismatch_is_reported(tmp_path):
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe', 'a' * 64)
    controls = UpdateControls(tmp_path, tmp_path)
    installer = tmp_path / 'download.exe'
    installer.write_bytes(b'tampered')
    messages = []
    def message(text, flags=0):
        messages.append(text)
        return 7 if len(messages) == 1 else 0
    with patch('windows_native_update_controls._message', side_effect=message), \
         patch('windows_native_update_controls._select_installer', return_value=str(installer)), \
         patch('windows_native_update_controls.webbrowser.open') as open_browser:
        controls._show_candidate(candidate)
    open_browser.assert_not_called()
    assert 'does not match' in messages[-1]
    assert 'Do not run' in messages[-1]


def test_browser_download_occurs_only_after_confirmation(tmp_path):
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe', 'a' * 64)
    controls = UpdateControls(tmp_path, tmp_path)
    with patch('windows_native_update_controls._message', return_value=6), \
         patch('windows_native_update_controls.webbrowser.open') as open_browser:
        controls._show_candidate(candidate)
    open_browser.assert_called_once_with(candidate.url)


def test_matching_installer_is_checked_without_executing_it(tmp_path):
    installer = tmp_path / 'download.exe'
    installer.write_bytes(b'installer')
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe',
                                hashlib.sha256(b'installer').hexdigest())
    controls = UpdateControls(tmp_path, tmp_path)
    messages = []
    def message(text, flags=0):
        messages.append(text)
        return 7 if len(messages) == 1 else 0
    with patch('windows_native_update_controls._message', side_effect=message), \
         patch('windows_native_update_controls._select_installer', return_value=str(installer)), \
         patch('windows_native_update_controls.webbrowser.open') as open_browser:
        controls._show_candidate(candidate)
    open_browser.assert_not_called()
    assert 'matches the feed SHA-256' in messages[-1]
    assert 'does not establish publisher identity' in messages[-1]


def test_verified_installer_runs_only_after_explicit_confirmation(tmp_path):
    installer = tmp_path / 'download.exe'
    installer.write_bytes(b'installer')
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe',
                                hashlib.sha256(b'installer').hexdigest())
    controls = UpdateControls(tmp_path, tmp_path)
    with patch('windows_native_update_controls._message', side_effect=[7, 6]), \
         patch('windows_native_update_controls._select_installer', return_value=str(installer)), \
         patch('windows_native_update_controls._run_verified_installer') as run:
        controls._show_candidate(candidate)
    run.assert_called_once_with(installer, candidate)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows installer execution')
def test_installer_is_rechecked_before_launch(tmp_path):
    from windows_native_update_controls import _run_verified_installer

    installer = tmp_path / 'download.exe'
    installer.write_bytes(b'installer')
    candidate = UpdateCandidate('4.2.0', 0, 'stable', 'https://github.com/example/setup.exe',
                                hashlib.sha256(b'installer').hexdigest())
    with patch('windows_native_update_controls.subprocess.Popen') as run:
        _run_verified_installer(installer, candidate)
        run.assert_called_once_with([str(installer.resolve())], close_fds=True)
        installer.write_bytes(b'changed')
        with pytest.raises(ValueError, match='does not match'):
            _run_verified_installer(installer, candidate)
        assert run.call_count == 1


def test_update_dialog_does_not_block_controller_and_close_cancels_check(tmp_path):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    manager = Mock()
    controls = UpdateControls(tmp_path, tmp_path)
    controls._manager = manager
    def message(_text, _flags=0):
        entered.set()
        assert release.wait(5)
        closed.set()
        return 6
    with patch('windows_native_update_controls._message', side_effect=message):
        controls.open()
        assert entered.wait(5)
        assert not closed.is_set()  # open returned while its UI thread is still waiting
        controls.close()
        release.set()
        assert closed.wait(5)
    assert not manager.check_now.called


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native launcher')
def test_tray_update_action_opens_and_closes_controls(tmp_path):
    import windows_native_launcher as launcher
    from types import SimpleNamespace
    controller = SimpleNamespace(start=lambda: None, step=lambda timeout: 'idle', close=lambda: None,
                                 scheduler=SimpleNamespace(active=None), port=18083)
    class Tray:
        def __init__(self):
            self.commands = self
            self.actions = iter(('updates', 'exit'))
        def start(self):
            pass
        def set_status(self, _status):
            pass
        def get_nowait(self):
            return next(self.actions)
        def close(self):
            pass
    controls = Mock()
    with patch.object(launcher, '_read_profile', return_value=controller), \
         patch.object(launcher, '_launch_setup', return_value=True), \
         patch.object(launcher, '_start_with_port_retry', return_value=(controller, None)), \
         patch.object(launcher, 'NativeTray', Tray), \
         patch.object(launcher, 'NetworkControls'), \
         patch.object(launcher, 'UpdateControls', return_value=controls):
        assert launcher.main(['--state', str(tmp_path)]) == 0
    controls.start.assert_called_once_with()
    controls.open.assert_called_once_with()
    controls.close.assert_called_once_with()
