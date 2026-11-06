import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
if os.name == 'nt':
    import windows_native_tray as tray


@unittest.skipUnless(os.name == 'nt', 'Windows notification area')
class TrayStatusTests(unittest.TestCase):
    def test_status_fits_windows_utf16_buffer_without_split_surrogate(self):
        controller = tray.NativeTray()
        controller.set_status('a' * 126 + '\U0001f4da' + 'x')
        data = tray._NotifyIcon()
        data.tip = controller._status
        self.assertEqual(data.tip, 'a' * 126)
        controller.set_status('a' * 125 + '\U0001f4da' + 'x')
        data.tip = controller._status
        self.assertEqual(data.tip, 'a' * 125 + '\U0001f4da')

    def test_icon_registration_recovers_when_explorer_starts_later(self):
        calls = []

        def notify(operation, data):
            calls.append(operation)
            return len(calls) > 1

        with patch.object(tray, '_notify', side_effect=notify):
            controller = tray.NativeTray()
            controller.start()
            try:
                self.assertEqual(controller.commands.get(timeout=2), 'tray-error')
                self.assertTrue(controller._thread.is_alive())
                self.assertTrue(tray._post(controller._window, controller._taskbar_created, 0, 0))
                self.assertEqual(controller.commands.get(timeout=2), 'tray-recovered')
                self.assertIsNone(controller.error)
                self.assertTrue(controller._icon_present)
            finally:
                controller.close()
        self.assertEqual(calls, [0, 0, 2])
