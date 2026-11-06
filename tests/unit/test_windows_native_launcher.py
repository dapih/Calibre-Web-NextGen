import os
import json
from contextlib import nullcontext
import sys
import socket
import unittest
from pathlib import Path
from queue import Empty
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
if os.name == 'nt':
    import windows_native_launcher as launcher


@unittest.skipUnless(os.name == 'nt', 'Windows native launcher')
class LauncherLifecycleTests(unittest.TestCase):
    def test_startup_offers_recovery_then_retries(self):
        controller = object()
        with patch.object(launcher, '_read_profile', return_value=controller), \
             patch.object(launcher, '_launch_setup', return_value=True), \
             patch.object(launcher, '_start_with_port_retry', side_effect=[
                 launcher.CoordinatorBusy('unfinished work'), (controller, {'active': True})]), \
             patch.object(launcher, '_recover_work', return_value=True) as recover, \
             patch.object(launcher, '_message'):
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        recover.assert_called_once_with(controller, 'unfinished work')

    def test_review_prompt_runs_only_the_selected_existing_verifier(self):
        from contextlib import nullcontext

        owner = SimpleNamespace(ledger=SimpleNamespace(list_jobs=lambda _state: [
            {'id': 'job-one', 'operation': 'ingest', 'attempts': 0},
            {'id': 'job-two', 'operation': 'convert-library', 'attempts': 1},
        ]))
        root = SimpleNamespace(withdraw=lambda: None, destroy=lambda: None)
        controller = object()
        with patch.object(launcher, '_offline_recovery_owner', return_value=nullcontext(owner)), \
             patch.object(launcher, '_verify_recovery_storage'), \
             patch('tkinter.Tk', return_value=root), \
             patch('tkinter.simpledialog.askinteger', return_value=2), \
             patch.object(launcher, '_message', side_effect=[6, 1]), \
             patch.object(launcher, '_confirm_conversion', return_value='verified') as confirm, \
             patch.object(launcher, '_retry_unstarted_ingest') as retry:
            self.assertTrue(launcher._recover_work(controller, 'paused'))
        confirm.assert_called_once_with(controller, 'job-two')
        retry.assert_not_called()

    def test_read_profile_does_not_create_temp_during_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state, library, ingest, program = [base / name for name in ('state', 'library', 'ingest', 'program')]
            for path in (state / 'config', library, ingest, program / 'scripts', program / 'runtime' / 'Scripts',
                         program / 'calibre', program / 'magick'):
                path.mkdir(parents=True)
            for path in (program / 'scripts' / 'windows_native_launcher.py',
                         program / 'scripts' / 'windows_native_web.py',
                         program / 'runtime' / 'Scripts' / 'python.exe', program / 'helper.dll',
                         program / 'kepubify.exe'):
                path.touch()
            manifest = {'schema': 1, 'library': str(library), 'ingest': str(ingest),
                        'runtime': str(program / 'runtime'), 'helper': str(program / 'helper.dll'),
                        'calibre': str(program / 'calibre'), 'kepubify': str(program / 'kepubify.exe'),
                        'magick': str(program / 'magick'), 'port': 18083}
            (state / 'native-profile.json').write_text(json.dumps(manifest), encoding='utf-8')
            journal = state / 'maintenance-journal.json'
            journal.write_bytes(b'pending transaction')
            before = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
            with patch.object(launcher, '__file__', str(program / 'scripts' / 'windows_native_launcher.py')), \
                 patch.object(launcher, 'require_private_directory', side_effect=lambda path: Path(path).resolve(strict=True)), \
                 patch.object(launcher, '_volume_identity', return_value=('volume', 1)):
                controller = launcher._read_profile(state)
            self.assertEqual(controller.state, state)
            self.assertFalse((state / 'tmp').exists())
            self.assertEqual(before, {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()})

    def test_change_port_refuses_occupied_port_and_persists_available_choice(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
            state = Path(directory)
            path = state / 'native-profile.json'
            path.write_text(json.dumps({'schema': 1, 'port': 18083}), encoding='utf-8')
            controller = SimpleNamespace(state=state, port=18083)
            other.bind(('127.0.0.1', 0))
            other.listen()
            occupied = other.getsockname()[1]
            with patch.object(launcher, '_offline_recovery_owner', return_value=nullcontext()), \
                 patch.object(launcher, '_verify_recovery_storage'):
                with self.assertRaisesRegex(ValueError, 'already in use'):
                    launcher._change_port(controller, occupied)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['port'], 18083)
                self.assertEqual(other.getsockname()[1], occupied)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as free:
                    free.bind(('127.0.0.1', 0))
                    replacement = free.getsockname()[1]
                self.assertIn(str(replacement), launcher._change_port(controller, replacement))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['port'], replacement)

    def test_change_port_waits_for_the_real_profile_owner_to_exit(self):
        helpers = list((Path(__file__).resolve().parents[2] / '.m0').glob('control-proof-*/cwng-native.dll'))
        if not helpers:
            self.skipTest('private native helper is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / 'native-profile.json'
            path.write_text(json.dumps({'schema': 1, 'port': 18083}), encoding='utf-8')
            controller = SimpleNamespace(state=state, port=18083, helper=helpers[0],
                                         generation_file=state / 'web-generation')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary:
                temporary.bind(('127.0.0.1', 0))
                replacement = temporary.getsockname()[1]
            with patch.object(launcher, '_verify_recovery_storage'):
                with launcher.Coordinator(helpers[0], state):
                    with self.assertRaises(OSError):
                        launcher._change_port(controller, replacement)
                    self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['port'], 18083)
                launcher._change_port(controller, replacement)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['port'], replacement)

    def test_initial_port_conflict_requires_explicit_choice_and_restarts_profile(self):
        class Occupied:
            port = 18083
            def start(self):
                raise RuntimeError('web process exited before readiness')
        class Replacement:
            port = 18084
            def start(self):
                return None
        original, replacement = Occupied(), Replacement()
        with patch.object(launcher, 'check_loopback_port', return_value=False), \
             patch.object(launcher, '_prompt_replacement_port', return_value=18084), \
             patch.object(launcher, '_change_port') as change, \
             patch.object(launcher, '_read_profile', return_value=replacement):
            started, duplicate = launcher._start_with_port_retry(original, 'state')
        self.assertIs(started, replacement)
        self.assertIsNone(duplicate)
        change.assert_called_once_with(original, 18084)

    def test_cancelling_initial_port_conflict_does_not_change_profile(self):
        class Occupied:
            port = 18083
            def start(self):
                raise RuntimeError('web process exited before readiness')
        with patch.object(launcher, 'check_loopback_port', return_value=False), \
             patch.object(launcher, '_prompt_replacement_port', return_value=None), \
             patch.object(launcher, '_change_port') as change:
            self.assertEqual(launcher._start_with_port_retry(Occupied(), 'state'), (None, None))
        change.assert_not_called()

    def test_confirm_conversion_is_local_recovery_without_web_start(self):
        controller = SimpleNamespace()
        with patch.object(launcher, '_read_profile', return_value=controller), \
             patch.object(launcher, '_confirm_conversion', return_value='verified') as confirm, \
             patch.object(launcher, '_launch_setup') as setup, \
             patch.object(launcher, '_message') as message:
            self.assertEqual(launcher.main(['--state', 'unused', '--confirm-conversion', 'job-1']), 0)
        confirm.assert_called_once_with(controller, 'job-1')
        setup.assert_not_called()
        message.assert_called_once_with('verified')

    def test_confirm_ingest_is_local_recovery_without_web_start(self):
        controller = SimpleNamespace()
        with patch.object(launcher, '_read_profile', return_value=controller), \
             patch.object(launcher, '_confirm_ingest', return_value='verified') as confirm, \
             patch.object(launcher, '_launch_setup') as setup, \
             patch.object(launcher, '_message') as message:
            self.assertEqual(launcher.main(['--state', 'unused', '--confirm-ingest', 'job-1']), 0)
        confirm.assert_called_once_with(controller, 'job-1')
        setup.assert_not_called()
        message.assert_called_once_with('verified')

    def test_storage_failure_is_visible_in_tray(self):
        class Controller:
            port = 18083
            scheduler = SimpleNamespace(active=None)
            def start(self):
                pass
            def step(self, timeout):
                return 'storage'
            def close(self):
                pass
        class Tray:
            def __init__(self):
                self.commands = self
                self.labels = []
            def start(self):
                pass
            def set_status(self, label):
                self.labels.append(label)
            def get_nowait(self):
                return 'exit'
            def close(self):
                pass
        tray = Tray()
        with patch.object(launcher, '_read_profile', return_value=Controller()), \
             patch.object(launcher, '_launch_setup', return_value=True), \
             patch.object(launcher, 'NativeTray', return_value=tray):
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        self.assertIn('Storage unavailable; work paused', tray.labels)

    def test_network_tray_action_opens_controls_and_stops_them_before_owner_close(self):
        order = []
        class Controller:
            port = 18083
            scheduler = SimpleNamespace(active=None)
            def start(self):
                pass
            def step(self, timeout):
                return 'idle'
            def close(self):
                order.append('owner-closed')
        class Tray:
            def __init__(self):
                self.commands = self
                self.actions = iter(('network', 'exit'))
            def start(self):
                pass
            def set_status(self, _value):
                pass
            def get_nowait(self):
                return next(self.actions)
            def close(self):
                pass
        class Controls:
            def __init__(self, _state, port, **_kwargs):
                assert port == 18083
            def open(self):
                order.append('network-opened')
            def close(self):
                order.append('network-closed')
        with patch.object(launcher, '_read_profile', return_value=Controller()), \
             patch.object(launcher, '_launch_setup', return_value=True), \
             patch.object(launcher, 'NativeTray', Tray), \
             patch.object(launcher, 'NetworkControls', Controls):
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        self.assertEqual(order, ['network-opened', 'network-closed', 'owner-closed'])

    def test_failed_web_restarts_use_all_available_retry_delays(self):
        class Controller:
            port = 18083
            def __init__(self):
                self.calls = 0
                self.failed_once = False
                self.local = SimpleNamespace(owner=SimpleNamespace(ledger=SimpleNamespace(
                    list_jobs=lambda _state: [])))
                self.scheduler = SimpleNamespace(active=None)
            def start(self):
                self.calls += 1
                if self.calls in (2, 3):
                    raise OSError('web unavailable')
            def step(self, timeout):
                if not self.failed_once:
                    self.failed_once = True
                    raise RuntimeError('web failed')
                return 'idle'
            def pause(self):
                pass
            def close(self):
                pass
        controller = Controller()
        delays = []
        class Budget:
            def __init__(self, **_kwargs):
                pass
            def failed(self):
                delay = (2, 10, 30)[len(delays)]
                delays.append(delay)
                return delay
        class Tray:
            def __init__(self):
                self.commands = self
                self.labels = []
            def start(self):
                pass
            def set_status(self, value):
                self.labels.append(value)
            def get_nowait(self):
                if controller.calls >= 4:
                    return 'exit'
                raise Empty
            def close(self):
                pass
        tray = Tray()
        ticks = iter(range(10000))
        with patch.object(launcher, '_read_profile', return_value=controller), \
             patch.object(launcher, '_launch_setup', return_value=True), \
             patch.object(launcher, 'NativeTray', return_value=tray), \
             patch.object(launcher, 'RestartBudget', Budget), \
             patch.object(launcher.time, 'monotonic', side_effect=lambda: next(ticks)), \
             patch.object(launcher.time, 'sleep'):
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        self.assertEqual(delays, [2, 10, 30])
        self.assertEqual(controller.calls, 4)
        self.assertIn('Web stopped; retrying', tray.labels)

    def test_open_after_stop_never_opens_unowned_port(self):
        class Controller:
            port = 18083
            library = ingest = state = Path('C:/fixture')
            scheduler = SimpleNamespace(active=None)
            readiness = SimpleNamespace(verify=lambda: True)
            def start(self):
                pass
            def step(self, timeout):
                return 'idle'
            def pause(self):
                pass
            def close(self):
                pass
        class Tray:
            def __init__(self):
                self.commands = self
                self.actions = iter(('stop', 'open', 'exit'))
            def start(self):
                pass
            def set_status(self, _value):
                pass
            def get_nowait(self):
                return next(self.actions)
            def close(self):
                pass
        with patch.object(launcher, '_read_profile', return_value=Controller()), \
             patch.object(launcher, '_launch_setup', return_value=True), \
             patch.object(launcher, 'NativeTray', Tray), \
             patch.object(launcher.time, 'sleep'), \
             patch.object(launcher.webbrowser, 'open') as browser, \
             patch.object(launcher, '_message') as message:
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        browser.assert_not_called()
        self.assertIn('not ready', message.call_args.args[0])

    def test_cancelled_first_run_does_not_start_web(self):
        with patch.object(launcher, '_launch_setup', return_value=False) as setup, \
             patch.object(launcher, '_read_profile') as read_profile:
            self.assertEqual(launcher.main(['--state', 'unused']), 0)
        setup.assert_called_once()
        read_profile.assert_not_called()

    def test_setup_handoff_contains_process_and_requires_completion_marker(self):
        root = Path(__file__).resolve().parents[2]
        helpers = list((root / '.m0').glob('control-proof-*/cwng-native.dll'))
        runtime = root / '.m0/n1-runtime-locked'
        if not helpers or not (runtime / 'Scripts/python.exe').is_file():
            self.skipTest('private M0 proof payload is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            args = SimpleNamespace(state=str(state), helper=str(helpers[0]), runtime=str(runtime),
                                   calibre=str(root / '.m0/calibre-proof/extracted/Calibre Portable/Calibre'),
                                   kepubify=str(root / '.m0/kepubify-proof/kepubify-windows-64bit.exe'),
                                   magick=str(root / '.m0/imagemagick-proof/dynamic/app'))
            with patch.object(launcher, 'require_private_directory', return_value=state), \
                 patch.object(launcher, '_volume_identity'), patch.object(launcher, 'Job') as job_type:
                process = job_type.return_value.spawn.return_value
                process.wait.return_value = 0
                self.assertFalse(launcher._launch_setup(args))
                arguments = job_type.return_value.spawn.call_args.args[1]
                self.assertTrue(any(str(arg).endswith('windows_native_setup_ui.py') for arg in arguments))
                self.assertNotIn('--password', arguments)
                job_type.return_value.close.assert_called_once()
                process.close.assert_called_once()
