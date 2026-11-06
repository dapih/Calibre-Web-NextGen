import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
if os.name == 'nt':
    import tkinter as tk
    import windows_native_setup_ui as ui
    from windows_native_setup import SetupCancelled
    from windows_native_setup_ui import SetupWindow


@unittest.skipUnless(os.name == 'nt', 'Windows setup UI')
class SetupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master = tk.Tk()
        cls.master.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.master.destroy()

    def setUp(self):
        self.root = tk.Toplevel(self.master)
        self.root.withdraw()
        self.calls = []

        def copy(**kwargs):
            self.calls.append(('copy', dict(kwargs)))
            return {'ready': True}

        def fresh(**kwargs):
            self.calls.append(('new', dict(kwargs)))
            return {'ready': True}

        self.window = SetupWindow(self.root, {'state': 'private'}, copy_setup=copy, new_setup=fresh)
        self.window.library.set(r'C:\Books\Library')
        self.window.ingest.set(r'C:\Books\Ingest')
        self.window.username.set('reader')
        self.window.password.set('a private test password')
        self.window.confirm_password.set('a private test password')
        self.root.update()

    def tearDown(self):
        try:
            if self.root.winfo_exists():
                self.window.close()
        except tk.TclError:
            pass

    def _wait(self):
        deadline = time.monotonic() + 3
        while not self.window.done and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(self.window.done)

    def test_new_library_requires_cloud_confirmation_then_provisions(self):
        self.window.submit_button.invoke()
        self.assertFalse(self.calls)
        self.assertIn('cloud', self.window.status.get())
        self.window.storage_not_synced.set(True)
        self.window.submit_button.invoke()
        self._wait()
        self.assertEqual(self.calls[0][0], 'new')
        self.assertEqual(self.calls[0][1]['password'], 'a private test password')
        self.assertEqual(self.window.password.get(), '')
        self.assertTrue(self.calls[0][1]['storage_not_synced'])

    def test_copy_requires_stopped_source_confirmation(self):
        self.window.mode.set('copy')
        self.window._mode_changed()
        self.window.source.set(r'C:\OldLibrary')
        self.window.storage_not_synced.set(True)
        self.window.submit_button.invoke()
        self.assertFalse(self.calls)
        self.assertIn('Stop', self.window.status.get())
        self.window.source_stopped.set(True)
        self.window.submit_button.invoke()
        self._wait()
        self.assertEqual(self.calls[0][0], 'copy')
        self.assertEqual(self.calls[0][1]['source'], r'C:\OldLibrary')
        self.assertTrue(self.calls[0][1]['source_stopped'])

    def test_mismatched_password_never_starts_setup(self):
        self.window.storage_not_synced.set(True)
        self.window.confirm_password.set('different')
        self.window.submit_button.invoke()
        self.assertFalse(self.calls)
        self.assertIn('matching', self.window.status.get())

    def test_setup_error_redacts_password_and_allows_retry(self):
        def fail(**kwargs):
            raise RuntimeError('rejected ' + kwargs['password'])

        self.window.new_setup = fail
        self.window.storage_not_synced.set(True)
        self.window.submit_button.invoke()
        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.window.busy)
        self.assertNotIn('a private test password', self.window.status.get())
        self.assertIn('[redacted]', self.window.status.get())
        self.assertTrue(self.window.submit_button.instate(['!disabled']))

    def test_close_while_copying_requests_cancel_and_waits_for_safe_stop(self):
        started = threading.Event()

        def stop(**kwargs):
            started.set()
            if not kwargs['cancel_event'].wait(2):
                raise RuntimeError('cancellation not received')
            raise SetupCancelled('stopped')

        self.window.new_setup = stop
        self.window.storage_not_synced.set(True)
        self.window.submit_button.invoke()
        self.assertTrue(started.wait(2))
        self.window.close_button.invoke()
        self.assertTrue(self.window.cancel_event.is_set())
        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.window.busy)

    def test_entrypoint_holds_ownership_until_window_closes(self):
        import windows_native_setup_db
        events = []
        token = object()

        @contextmanager
        def ownership(state, helper):
            events.append('acquired')
            yield token
            events.append('released')

        def window(root, assets, **kwargs):
            events.append('window')
            self.assertIs(assets['ownership'], token)
            root.withdraw()
            root.after(0, root.quit)

        argv = [part for name in ('state', 'helper', 'runtime', 'calibre', 'kepubify', 'magick')
                for part in ('--' + name, r'C:\private')]
        with patch.dict(os.environ), patch.object(windows_native_setup_db, 'setup_ownership', ownership), \
                patch.object(ui.tk, 'Tk', return_value=self.root), \
                patch.object(ui, 'SetupWindow', window):
            ui.main(argv)
        self.assertEqual(events, ['acquired', 'window', 'released'])


if __name__ == '__main__':
    unittest.main()
