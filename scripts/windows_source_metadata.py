"""Watch metadata changes for a Windows source installation."""

import os
from pathlib import Path
import subprocess
import sys
import threading
from contextlib import nullcontext

from metadata_change_dispatch import ChangeLogDispatcher
from watch_fallback import scan_once
from windows_source_library import configured_library


class BackgroundMetadata:
    def __init__(self, *, environment=None, writer_lock=None, interval=1):
        self.environment = dict(os.environ if environment is None else environment)
        config = self.environment.get('CWA_DB_PATH') or self.environment['CALIBRE_DBPATH']
        self.directory = Path(self.environment.get('CWA_METADATA_CHANGE_LOGS_DIR') or
                              Path(config) / 'metadata_change_logs')
        self.writer_lock = writer_lock
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name='windows-metadata', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            dispatcher = ChangeLogDispatcher(self._enforce)
            index = {}
            while not self._stop.is_set():
                if not configured_library(self.environment):
                    self._stop.wait(self.interval)
                    continue
                scan_once(str(self.directory), False, {'json'}, index, 1.0,
                          lambda _event, path: dispatcher.observe(os.path.basename(path)))
                dispatcher.flush_ready()
                self._stop.wait(self.interval)
        except Exception as error:
            print(f'[windows-metadata] stopped: {error}', file=sys.stderr, flush=True)

    def _enforce(self, filename):
        enforcer = Path(__file__).with_name('cover_enforcer.py')
        with self.writer_lock if self.writer_lock is not None else nullcontext():
            library = configured_library(self.environment)
            if not library:
                raise RuntimeError('configured library is unavailable; metadata log retained')
            environment = {**self.environment, 'CWA_CALIBRE_LIBRARY_DIR': library}
            subprocess.run([sys.executable, str(enforcer), '--log', filename],
                           env=environment, check=True)
