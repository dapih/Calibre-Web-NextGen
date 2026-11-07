"""Find the library currently configured in the source installation."""

from contextlib import closing
from pathlib import Path
import sqlite3


def configured_library(environment):
    app_db = environment.get('CWA_APP_DB_PATH')
    if not app_db:
        config = environment.get('CWA_DB_PATH') or environment.get('CALIBRE_DBPATH')
        if not config:
            return None
        app_db = str(Path(config) / 'app.db')
    try:
        with closing(sqlite3.connect(Path(app_db).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT config_calibre_dir FROM settings').fetchone()
        return row[0] if row and row[0] and (Path(row[0]) / 'metadata.db').is_file() else None
    except (OSError, ValueError, sqlite3.Error):
        return None
