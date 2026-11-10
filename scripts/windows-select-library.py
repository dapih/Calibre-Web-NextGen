"""Select a validated internal library in the staged Windows installation."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys


def select_library(staged, installed):
    metadata = staged / 'metadata.db'
    with closing(sqlite3.connect(metadata.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute('SELECT uuid FROM library_id').fetchone()
        if not row or not row[0]:
            raise ValueError('The selected metadata.db has no Calibre library ID.')
        uuid = row[0]

    root = Path(__file__).resolve().parents[1]
    config = root / '.cwng-data' / 'config'
    database = config / 'app.db'
    if not database.is_file():
        config.mkdir(parents=True, exist_ok=True)
        os.environ.update(CALIBRE_DBPATH=str(config), CWA_DB_PATH=str(config),
                          CWA_APP_DB_PATH=str(database), CWA_APP_ROOT=str(root))
        sys.path.insert(0, str(root))
        from cps import config_sql, ub

        ub.init_db(str(database))
        key, _ = config_sql.get_encryption_key(str(config))
        config_sql.load_configuration(ub.session, key)
        ub.dispose()

    with closing(sqlite3.connect(database)) as connection, connection:
        current_path, current_uuid = connection.execute(
            'SELECT config_calibre_dir, config_calibre_uuid FROM settings').fetchone()
        same_path = current_path and Path(current_path) == installed
        if (current_uuid and current_uuid != uuid) or (current_path and not current_uuid and not same_path):
            raise ValueError('The saved settings belong to another library. To keep its reading progress, '
                             'reinstall without selecting a different library or choose fresh settings.')
        connection.execute('UPDATE settings SET config_calibre_dir=?, config_calibre_uuid=?', (str(installed), uuid))


if __name__ == '__main__':
    try:
        select_library(*(Path(value) for value in sys.argv[1:]))
    except (OSError, ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from error
