"""The Windows launcher may relaunch only after workers and the lock are done."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class WindowsRestartCleanupTests(unittest.TestCase):
    def exercise(self, restart, exit_code):
        events = []
        server = SimpleNamespace(restart=restart, defer_restart=False)

        @contextmanager
        def environment(_root):
            events.append('lock')
            try:
                yield
            finally:
                events.append('unlock')

        def worker(name):
            return lambda *args, **kwargs: SimpleNamespace(
                start=lambda: events.append(name + '.start'),
                stop=lambda: events.append(name + '.stop'))

        def web_main():
            events.append('web')
            raise SystemExit(exit_code)

        modules = {
            'windows_portable': SimpleNamespace(portable_environment=environment),
            'windows_background_ingest': SimpleNamespace(BackgroundIngest=worker('ingest')),
            'windows_source_metadata': SimpleNamespace(BackgroundMetadata=worker('metadata')),
            'cps': SimpleNamespace(web_server=server),
            'cps.main': SimpleNamespace(main=web_main),
        }
        path = Path(__file__).resolve().parents[2] / 'scripts/windows-run.py'
        spec = importlib.util.spec_from_file_location('windows_runner_test', path)
        runner = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(
                'os.environ', {'CALIBRE_DBPATH': 'state', 'CWA_INGEST_FOLDER': 'inbox'}):
            spec.loader.exec_module(runner)
            with self.assertRaises(SystemExit) as result:
                runner.main()
        self.assertEqual(events, ['lock', 'ingest.start', 'metadata.start', 'web',
                                  'metadata.stop', 'ingest.stop', 'unlock'])
        return result.exception.code, server.defer_restart

    def test_restart_is_returned_to_launcher_after_cleanup(self):
        code, deferred = self.exercise(restart=True, exit_code=0)
        self.assertEqual(code, 75)
        self.assertTrue(deferred)

    def test_shutdown_exits_after_cleanup(self):
        code, _ = self.exercise(restart=False, exit_code=0)
        self.assertEqual(code, 0)

    def test_failure_is_not_restarted(self):
        code, _ = self.exercise(restart=True, exit_code=2)
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
