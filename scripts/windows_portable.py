"""Keep Windows source-install state with the application folder."""

import argparse
from contextlib import contextmanager
import json
import msvcrt
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


# Only filesystem settings are rebased, and only when inside the moved folder.
# External libraries, certificates and user-selected tools keep their locations.
PATH_SETTINGS = (
    'config_calibre_dir', 'config_calibre_split_dir', 'config_binariesdir',
    'config_converterpath', 'config_kepubifypath', 'config_rarfile_location',
    'config_certfile', 'config_keyfile', 'config_logfile', 'config_access_logfile',
)


def _rebase(value, moves):
    if not isinstance(value, str) or not Path(value).is_absolute():
        return value
    for old, new in moves:
        try:
            return str(new / Path(value).relative_to(old))
        except ValueError:
            pass
    return value


def _rebase_settings(config, moves):
    database = config / 'app.db'
    if database.is_file():
        with sqlite3.connect(database) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(settings)')}
            for column in PATH_SETTINGS:
                if column not in columns:
                    continue
                for rowid, value in db.execute(f'SELECT rowid, "{column}" FROM settings').fetchall():
                    updated = _rebase(value, moves)
                    if updated != value:
                        db.execute(f'UPDATE settings SET "{column}"=? WHERE rowid=?', (updated, rowid))
    marker = config / 'unfinished-ingest.json'
    if marker.is_file():
        record = json.loads(marker.read_text(encoding='utf-8'))
        for key in ('source', 'working_copy'):
            if key in record:
                record[key] = _rebase(record[key], moves)
        _write_json(marker, record)


def _write_json(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _copy_legacy(data, config, inbox):
    legacy_config = Path(os.environ['LOCALAPPDATA']) / 'Calibre-Web-NextGen'
    legacy_inbox = Path(os.environ['USERPROFILE']) / 'CWNG-Ingest'
    if config.exists() or inbox.exists():
        raise RuntimeError('Local config or ingest already exists. Legacy data will not be merged or overwritten.')
    # Stage both copies before adopting them. Originals are never modified.
    with tempfile.TemporaryDirectory(prefix='legacy-copy-', dir=data) as temporary:
        staging = Path(temporary)
        for source, name in ((legacy_config, 'config'), (legacy_inbox, 'ingest')):
            if source.exists():
                shutil.copytree(source, staging / name)
            else:
                (staging / name).mkdir()
        (staging / 'config').rename(config)
        (staging / 'ingest').rename(inbox)
    return [(legacy_config, config), (legacy_inbox, inbox)]


@contextmanager
def portable_environment(root):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--import-legacy-data', action='store_true')
    options, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    data = root / '.cwng-data'
    config, default_inbox = data / 'config', data / 'ingest'
    location = data / 'location.json'
    legacy_config = Path(os.environ['LOCALAPPDATA']) / 'Calibre-Web-NextGen'
    legacy_inbox = Path(os.environ['USERPROFILE']) / 'CWNG-Ingest'
    if not location.exists() and not options.import_legacy_data and (
            legacy_config.exists() or legacy_inbox.exists()):
        raise RuntimeError(
            'Existing Windows profile data was found. Stop the old application, then run '
            'start-windows.cmd -ImportLegacyData to COPY its configuration and default ingest '
            'here. Originals and external libraries are retained. See docs/install/windows-source.md.')
    data.mkdir(exist_ok=True)
    with (data / 'running.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('This installation is already running. Stop it before starting or moving it.') from error
        try:
            moves = []
            if options.import_legacy_data:
                moves = _copy_legacy(data, config, default_inbox)
            record = json.loads(location.read_text(encoding='utf-8')) if location.exists() else {}
            old_root = record.get('root')
            if old_root and Path(old_root) != root:
                moves.append((Path(old_root), root))
            config.mkdir(exist_ok=True)
            _rebase_settings(config, moves)
            inbox = default_inbox
            temp = config / 'temp'
            calibre_config = data / 'calibre-config'
            for directory in (inbox, temp, calibre_config):
                directory.mkdir(parents=True, exist_ok=True)
            os.environ.update({
                'CWA_APP_ROOT': str(root),
                'CALIBRE_DBPATH': str(config),
                'CWA_DB_PATH': str(config),
                'CWA_APP_DB_PATH': str(config / 'app.db'),
                'CWA_INGEST_FOLDER': str(inbox),
                'CWA_TMP_CONVERSION_DIR': str(temp),
                'CALIBRE_CONFIG_DIRECTORY': str(calibre_config),
                'TEMP': str(temp), 'TMP': str(temp),
                'PYTHONUTF8': '1',
            })
            _write_json(location, {'root': str(root)})
            print(f'Configuration: {config}\nIngest folder: {inbox}', flush=True)
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
