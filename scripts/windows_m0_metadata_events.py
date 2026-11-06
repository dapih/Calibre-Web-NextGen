"""Admit existing application metadata logs into the native durable queue."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time

from windows_m0_inbox import _locked_source


_NAME = re.compile(r'^\d{14}-([1-9]\d*)\.json$')
_MAX_LOG_BYTES = 1024 * 1024


class MetadataEvents:
    """Scan unchanged legacy change logs without consuming their contents."""

    def __init__(self, ledger, environment, settings_db, *, clock_ns=time.time_ns):
        config = Path(environment['CALIBRE_DBPATH'])
        if config.suffix.lower() == '.db':
            config = config.parent
        self.directory = Path(environment.get('CWA_METADATA_CHANGE_LOGS_DIR') or
                              config / 'metadata_change_logs')
        if not self.directory.is_absolute():
            raise ValueError('metadata change log directory must be absolute')
        self.ledger = ledger
        self.settings_db = Path(settings_db)
        self.clock_ns = clock_ns

    def scan(self):
        with closing(sqlite3.connect(self.settings_db.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as db:
            settings = db.execute('SELECT auto_metadata_enforcement FROM cwa_settings').fetchall()
        if len(settings) != 1:
            raise ValueError('metadata enforcement settings are ambiguous')
        if not settings[0][0]:
            return
        if not self.directory.exists():
            return
        root = self.directory.resolve(strict=True)
        if not root.is_dir():
            raise OSError('metadata change log path is not a directory')
        for path in root.iterdir():
            match = _NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                with _locked_source(path, root, path.resolve(strict=True)) as stream:
                    data = stream.read(_MAX_LOG_BYTES + 1)
                if len(data) > _MAX_LOG_BYTES or not isinstance(json.loads(data), dict):
                    continue
            except (OSError, ValueError, UnicodeDecodeError):
                # A writer may still have the file open or be finishing JSON.
                continue
            event_key = path.name + ':' + hashlib.sha256(data).hexdigest()
            self.ledger.admit_metadata_event(event_key, int(match.group(1)),
                                             self.clock_ns() + 2_000_000_000)
