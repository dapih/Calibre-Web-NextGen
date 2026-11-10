"""Select, create, or look up a library for the portable Windows installation."""

from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from uuid import uuid4


def default_library_path(root):
    return root.parent / 'calibre-library'


def prepare_library(root, location, *, create=False):
    """Return the real library directory and whether a database was created."""
    directory = Path(location).expanduser()
    if not directory.is_absolute():
        raise ValueError("Enter an absolute path to the library folder.")
    if directory.name.lower() == 'metadata.db' and directory.is_file():
        directory = directory.parent
    directory = directory.resolve()
    metadata = directory / 'metadata.db'
    if metadata.is_file():
        return directory, False
    if not create:
        raise ValueError("Choose a library folder containing metadata.db.")
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("Choose an empty folder or a new path to create a library.")

    from calibre_library_target import local_library_path

    binary = root / '.cwng-runtime' / 'calibre' / 'calibredb.exe'
    if not binary.is_file():
        raise ValueError("The bundled Calibre tools are missing. Run Windows setup again.")
    config = root / '.cwng-data' / 'calibre-config'
    config.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'CALIBRE_CONFIG_DIRECTORY': str(config),
                   'CALIBRE_CACHE_DIRECTORY': str(root / '.cwng-data' / 'calibre-cache')}
    # Publish only after Calibre finishes. The staging folder needs the normal
    # inherited ACL because Windows retains its permissions after the rename.
    directory.parent.mkdir(parents=True, exist_ok=True)
    staged = directory.parent / f'cw{uuid4().hex[:8]}'
    staged.mkdir()
    published = False
    try:
        subprocess.run([str(binary), 'list', '--with-library', local_library_path(staged)],
                       env=environment, check=True, timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not (staged / 'metadata.db').is_file():
            raise ValueError("Calibre did not create metadata.db.")
        if metadata.is_file():
            return directory, False
        # rmdir fails if another program has added any files since the check.
        had_empty_directory = directory.is_dir()
        if had_empty_directory:
            directory.rmdir()
        try:
            staged.rename(directory)
            published = True
        except OSError:
            if metadata.is_file():
                return directory, False
            if had_empty_directory:
                directory.mkdir(exist_ok=True)
            raise
    finally:
        if not published:
            shutil.rmtree(staged)
    return directory, True


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
