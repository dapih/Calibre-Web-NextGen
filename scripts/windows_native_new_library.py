"""Create a disposable empty Calibre source for the native setup transaction."""
import json
import ctypes
from contextlib import closing, nullcontext
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from windows_m0_inbox import _flush_directory, _volume_identity
from windows_m0_job import Job, generation_exists
from windows_native_first_run import _check_cancel, _local_directory, _overlap, complete_copy_setup
from windows_native_profile import require_private_directory
from windows_native_setup import _manifest, _write_marker
from windows_native_setup_db import setup_ownership, require_setup_ownership


def _short_path(path):
    """Use an existing Windows short path when available for Calibre limits."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    api.GetShortPathNameW.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    count = api.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < count < len(buffer) else str(path)


def complete_new_setup(*, state, helper, runtime, calibre, kepubify, magick,
                       library, ingest, username, password, port, storage_not_synced=False, ownership=None,
                       cancel_event=None):
    """Create and copy a new empty library, with resumable setup staging.

    A contained Calibre process writes only an owned staging directory. On
    interruption uncertain stages remain on disk; a live old job blocks retry.
    The setup process never deletes user content or publishes a child-written
    library until that child and its descendants are quiescent.
    """
    if storage_not_synced is not True:
        raise ValueError('confirm the selected folders are not cloud-synchronized')
    _check_cancel(cancel_event)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid port')
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > 64 or username.casefold() == 'guest'):
        raise ValueError('invalid administrator name')
    if not isinstance(password, str) or len(password) < 8 or password == 'admin123':
        raise ValueError('a user-selected administrator password is required')
    state = require_private_directory(state)
    _local_directory(state)
    config = require_private_directory(state / 'config')
    if config.parent != state or any(Path(os.environ.get(key, '')).resolve() != config
                                     for key in ('CWA_DB_PATH', 'CALIBRE_DBPATH')):
        raise ValueError('setup process must use its private configuration')
    ingest = _local_directory(ingest)
    library = Path(library)
    if not library.is_absolute() or not library.name or library.is_symlink():
        raise ValueError('invalid library destination')
    library = _local_directory(library.parent) / library.name
    program = Path(__file__).resolve().parent.parent
    locations = (program, state, library, ingest)
    if any(_overlap(a, b) for i, a in enumerate(locations) for b in locations[i + 1:]):
        raise ValueError('program, state, library and ingest must not overlap')
    paths = {key: Path(value).resolve(strict=True) for key, value in
             dict(helper=helper, runtime=runtime, calibre=calibre, kepubify=kepubify, magick=magick).items()}
    if any(not value.is_relative_to(program) for value in paths.values()):
        raise ValueError('runtime and tools must be private program assets')
    executable = paths['calibre'] / 'calibre-debug.exe'
    if (not paths['helper'].is_file() or not executable.is_file()
            or not (paths['runtime'] / 'Scripts' / 'python.exe').is_file()
            or not paths['kepubify'].is_file() or not paths['magick'].is_dir()):
        raise ValueError('native payload is incomplete')
    marker = state / 'new-library-stage.json'
    identity = {'library': str(library), 'ingest': str(ingest), 'username': username, 'port': port,
                'library_volume': list(_volume_identity(library.parent)),
                'ingest_volume': list(_volume_identity(ingest)),
                **{key: str(value) for key, value in paths.items()}}
    scope = setup_ownership(state, paths['helper']) if ownership is None else nullcontext(ownership)
    with scope as ownership:
        require_setup_ownership(ownership, state, paths['helper'])
        _check_cancel(cancel_event)
        if (state / 'setup-complete.json').exists() and not (state / 'setup-in-progress.json').exists():
            raise FileExistsError('setup has already completed')
        if marker.exists() or marker.is_symlink():
            if marker.is_symlink() or marker.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('unsafe new-library staging record')
            record = json.loads(marker.read_text(encoding='utf-8'))
            if record.get('identity') != identity or record.get('phase') not in ('creating', 'ready'):
                raise ValueError('new-library staging belongs to different setup inputs')
            if generation_exists(paths['helper'], record['generation']):
                raise RuntimeError('the previous setup worker is still active')
        else:
            if library.exists() or (state / 'setup-in-progress.json').exists():
                raise FileExistsError('destination or another setup already exists')
            record = None
        if record is None or record['phase'] == 'creating':
            generation = 'nj1-' + uuid.uuid4().hex
            # Calibre limits library path length; keep staging names compact.
            source = library.parent / ('.cwng-new-' + uuid.uuid4().hex[:12])
            record = {'identity': identity, 'source': str(source), 'generation': generation, 'phase': 'creating'}
            _write_marker(marker, record, initial=not marker.exists())
            _flush_directory(state)
            source.mkdir()
            _flush_directory(source.parent)
            temporary = state / 'tmp'
            temporary.mkdir(exist_ok=True)
            temporary = require_private_directory(temporary)
            if temporary.parent != state:
                raise ValueError('temporary directory escaped private state')
            env = {k: v for k, v in os.environ.items()
                   if k.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
            env.update({'PATH': os.pathsep.join((str(paths['calibre']), str(Path(env['SYSTEMROOT']) / 'System32'))),
                        'HOME': str(state), 'USERPROFILE': str(state), 'APPDATA': str(state),
                        'LOCALAPPDATA': str(state), 'TEMP': str(temporary), 'TMP': str(temporary),
                        'CALIBRE_CONFIG_DIRECTORY': str(config), 'CALIBRE_DBPATH': str(config),
                        'CWNG_SETUP_LIBRARY': _short_path(source)})
            job = Job(paths['helper'], generation=generation)
            process = None
            try:
                code = "import os; from calibre.db.legacy import LibraryDatabase; db=LibraryDatabase(os.environ['CWNG_SETUP_LIBRARY']); db.close()"
                process = job.spawn(executable, ['-c', code], cwd=source, environment=env)
                deadline = time.monotonic() + 120
                status = None
                while status is None and time.monotonic() < deadline:
                    _check_cancel(cancel_event)
                    status = process.wait(200)
                _check_cancel(cancel_event)
                if status != 0:
                    raise RuntimeError('Calibre could not create the empty library')
                deadline = time.monotonic() + 30
                while job.active_count():
                    _check_cancel(cancel_event)
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Calibre setup descendants did not finish')
                    time.sleep(.05)
            finally:
                if process is not None:
                    process.close()
                job.close()
            if not (source / 'metadata.db').is_file():
                raise RuntimeError('Calibre did not create a library database')
            info = (source / 'metadata.db').stat()
            record.update(phase='ready', database_identity=[info.st_dev, info.st_ino], manifest=_manifest(source))
            _write_marker(marker, record)
            _flush_directory(state)
        source = _local_directory(record['source'])
        if source.parent != library.parent or not source.name.startswith('.cwng-new-'):
            raise ValueError('invalid new-library staging path')
        dbpath = source / 'metadata.db'
        info = dbpath.stat()
        if [info.st_dev, info.st_ino] != record.get('database_identity'):
            raise ValueError('new-library staging changed')
        with closing(sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('pragma quick_check').fetchone() != ('ok',):
                raise ValueError('new-library staging is invalid')
        result = complete_copy_setup(state=state, helper=paths['helper'], runtime=paths['runtime'],
                                     calibre=paths['calibre'], kepubify=paths['kepubify'], magick=paths['magick'],
                                     source=source, library=library, ingest=ingest, username=username,
                                     password=password, port=port, source_stopped=True,
                                     storage_not_synced=True, ownership=ownership, cancel_event=cancel_event)
        # The completion marker is durable. Remove only the known empty
        # Calibre-generated staging tree if it still exactly matches its
        # captured manifest; unexpected files or changes remain untouched.
        try:
            snapshot = record['manifest']
            allowed_dirs = {'.calnotes', '.calnotes/backup', '.calnotes/resources', '.calnotes/retired'}
            if (set(snapshot['files']) in ({'metadata.db'}, {'metadata.db', '.calnotes/notes.db'})
                    and set(snapshot['directories']).issubset(allowed_dirs)
                    and _manifest(source) == snapshot):
                for relative in snapshot['files']:
                    (source / relative).unlink()
                for relative in sorted(snapshot['directories'], key=lambda value: value.count('/'), reverse=True):
                    (source / relative).rmdir()
                source.rmdir()
                _flush_directory(source.parent)
                marker.unlink()
                _flush_directory(state)
            else:
                logging.warning('New-library staging has unexpected content and was retained: %s', source)
        except (OSError, ValueError, KeyError):
            logging.warning('New-library staging could not be safely cleaned: %s', source, exc_info=True)
        return result
