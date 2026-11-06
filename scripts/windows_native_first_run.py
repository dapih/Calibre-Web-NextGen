"""Copy-import setup transaction for a fresh Windows profile.

The caller runs this in a private isolated setup process, after collecting the
administrator credentials locally. It publishes the completion marker last.
"""
import ctypes
from contextlib import closing, nullcontext
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from windows_m0_inbox import _flush_directory, _volume_identity
from windows_native_profile import require_private_directory
from windows_native_setup import _write_marker, copy_stopped_library, SetupCancelled
from windows_native_setup_db import prepare_databases, setup_ownership, require_setup_ownership


def _cloud_check(path):
    """Refuse registered Cloud Files roots and ambiguous API results.

    This API does not detect all third-party sync tools; user confirmation is
    also required and this check alone is not proof of no synchronization.
    """
    api = ctypes.WinDLL('CldApi.dll')
    api.CfGetSyncRootInfoByPath.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_uint32, ctypes.c_void_p]
    api.CfGetSyncRootInfoByPath.restype = ctypes.c_int32
    buffer = ctypes.create_string_buffer(4096)
    status = api.CfGetSyncRootInfoByPath(str(path), 0, buffer, len(buffer), None) & 0xffffffff
    if status not in (0x80070001, 0x80070186):  # no filter, or not underneath a sync root
        raise ValueError(f'cloud-sync eligibility could not be established: 0x{status:08x}')
    # Enumeration also detects a sync root nested inside the selected folder.
    for root in _registered_sync_roots():
        if _overlap(path, root):
            raise ValueError('selected folder overlaps a registered cloud sync root')


def _registered_sync_roots():
    """Enumerate Windows-registered roots through the documented WinRT API.

    Windows PowerShell is an OS facility, invoked by absolute system path with
    fixed code; no selected paths or secrets are passed on its command line.
    Errors, inaccessible roots and malformed output fail closed. This API does
    not identify arbitrary tools that do not register with Windows.
    """
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise ValueError('cannot find the Windows system directory')
    system = Path(buffer.value)
    executable = system / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    script = ("$ErrorActionPreference='Stop'; "
              "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
              "$roots=@([Windows.Storage.Provider.StorageProviderSyncRootManager,Windows.Storage.Provider,"
              "ContentType=WindowsRuntime]::GetCurrentSyncRoots() | ForEach-Object { $_.Path.Path }); "
              "ConvertTo-Json -InputObject $roots -Compress")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
    try:
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                cwd=system, env=environment, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        values = json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError('invalid sync root list')
        roots = [Path(value) for value in values]
        if any(not root.is_absolute() or not root.is_dir() for root in roots):
            raise ValueError('registered sync root is unavailable')
        return [root.resolve(strict=True) for root in roots]
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as error:
        raise ValueError('cannot enumerate Windows registered sync roots') from error


def _local_directory(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('setup paths must be existing absolute directories')
    for ancestor in (path, *path.parents):
        info = ancestor.stat(follow_symlinks=False)
        if getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('setup paths cannot traverse reparse points')
    path = path.resolve(strict=True)
    _volume_identity(path)
    _cloud_check(path)
    return path


def _overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise SetupCancelled('setup canceled; incomplete work can be resumed')


def complete_copy_setup(*, state, helper, runtime, calibre, kepubify, magick,
                        source, library, ingest, username, password, port,
                        source_stopped=False, storage_not_synced=False, ownership=None, cancel_event=None):
    """Copy a stopped source and configure a fresh profile under one owner.

    Only copy import is supported here. Paths must already exist except for the
    new library destination. Recovery from a partial run remains manual and
    must preserve both the copied library and its source.
    """
    if source_stopped is not True:
        raise ValueError('confirm the source library is stopped')
    _check_cancel(cancel_event)
    if storage_not_synced is not True:
        raise ValueError('confirm the selected folders are not cloud-synchronized')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid port')
    program = Path(__file__).resolve().parent.parent
    state = require_private_directory(state)
    source = _local_directory(source)
    ingest = _local_directory(ingest)
    _local_directory(state)
    library = Path(library)
    if not library.is_absolute() or not library.name or library.is_symlink():
        raise ValueError('choose a new library destination')
    library = _local_directory(library.parent) / library.name
    config = require_private_directory(state / 'config')
    if config.parent != state or any(Path(os.environ.get(k, '')).resolve() != config
                                     for k in ('CWA_DB_PATH', 'CALIBRE_DBPATH')):
        raise ValueError('setup process must use its private configuration')
    if not (source / 'metadata.db').is_file():
        raise ValueError('source must contain a Calibre library')
    locations = (program, state, source, library, ingest)
    if any(_overlap(left, right) for index, left in enumerate(locations) for right in locations[index + 1:]):
        raise ValueError('program, state, source, library and ingest must not overlap')
    tools = {name: Path(value).resolve(strict=True) for name, value in
             dict(helper=helper, runtime=runtime, calibre=calibre, kepubify=kepubify, magick=magick).items()}
    if any(not path.is_relative_to(program) for path in tools.values()):
        raise ValueError('runtime and tools must be private program assets')
    if not tools['helper'].is_file() or not (tools['runtime'] / 'Scripts' / 'python.exe').is_file():
        raise ValueError('native runtime/helper is incomplete')
    if not tools['calibre'].is_dir() or not tools['magick'].is_dir() or not tools['kepubify'].is_file():
        raise ValueError('native tool payload is incomplete')
    if not os.access(ingest, os.W_OK) or not os.access(library.parent, os.W_OK):
        raise PermissionError('library and ingest destinations must be writable')
    source_size = sum(item.stat().st_size for item in source.rglob('*') if item.is_file())
    if shutil.disk_usage(library.parent).free < source_size:
        raise OSError('insufficient space to copy the library')
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > 64 or username.casefold() == 'guest'):
        raise ValueError('invalid administrator name')
    if not isinstance(password, str) or len(password) < 8 or password == 'admin123':
        raise ValueError('a user-selected administrator password is required')
    scope = setup_ownership(state, tools['helper']) if ownership is None else nullcontext(ownership)
    with scope as ownership:
        require_setup_ownership(ownership, state, tools['helper'])
        _check_cancel(cancel_event)
        in_progress = state / 'setup-in-progress.json'
        identity = {'source': str(source), 'library': str(library), 'ingest': str(ingest),
                    'username': username, 'port': port,
                    'source_volume': list(_volume_identity(source)),
                    'library_volume': list(_volume_identity(library.parent)),
                    'ingest_volume': list(_volume_identity(ingest)),
                    'state_volume': list(_volume_identity(state)),
                    **{key: str(value) for key, value in tools.items()}}
        profile_path = state / 'native-profile.json'
        completed = state / 'setup-complete.json'
        if completed.exists() or completed.is_symlink():
            # A crash after publishing completion can leave only the cleanup
            # record. Confirm the exact transaction before deleting it.
            if (not in_progress.is_file() or in_progress.is_symlink() or completed.is_symlink()
                    or profile_path.is_symlink() or not profile_path.is_file()):
                raise FileExistsError('setup is already complete')
            for path in (in_progress, completed, profile_path):
                if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
                    raise ValueError('unsafe setup record')
            record = json.loads(in_progress.read_text(encoding='utf-8'))
            resolved_library = library.resolve(strict=True)
            library_info = (resolved_library / 'metadata.db').stat()
            expected_profile = {'schema': 1, 'library': str(resolved_library), 'ingest': str(ingest), 'port': port,
                                **{key: str(value) for key, value in tools.items()}}
            expected_marker = {'schema': 1, 'library': str(resolved_library), 'ingest': str(ingest),
                               'app_db': str((config / 'app.db').resolve(strict=True)),
                               'library_volume': list(_volume_identity(resolved_library)),
                               'library_database': [library_info.st_dev, library_info.st_ino],
                               'ingest_volume': list(_volume_identity(ingest)),
                               'state_volume': list(_volume_identity(state))}
            if (record.get('identity') != identity or record.get('phase') != 'initializing'
                    or json.loads(profile_path.read_text(encoding='utf-8')) != expected_profile
                    or json.loads(completed.read_text(encoding='utf-8')) != expected_marker):
                raise ValueError('completed setup does not match this transaction')
            from werkzeug.security import check_password_hash
            with closing(sqlite3.connect((config / 'app.db').resolve().as_uri() + '?mode=ro', uri=True)) as db:
                admins = db.execute('SELECT password FROM user WHERE name = ? AND role & 1 = 1', (username,)).fetchall()
            if len(admins) != 1 or not check_password_hash(admins[0][0], password):
                raise ValueError('completed setup credentials do not match')
            in_progress.unlink()
            _flush_directory(state)
            return expected_marker
        protected = (state / 'runtime-ledger.sqlite', state / 'runtime-ledger.sqlite-wal',
                     state / 'runtime-ledger.sqlite-shm', state / 'web-generation')
        if any(p.exists() or p.is_symlink() for p in protected):
            raise FileExistsError('existing setup or runtime state requires review')
        if in_progress.exists() or in_progress.is_symlink():
            if in_progress.is_symlink() or in_progress.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('setup record cannot be a reparse point')
            record = json.loads(in_progress.read_text(encoding='utf-8'))
            if record.get('identity') != identity or record.get('phase') not in ('copying', 'initializing'):
                raise ValueError('unfinished setup belongs to different inputs')
        else:
            if (library.exists() or profile_path.exists() or profile_path.is_symlink()
                    or any((config / n).exists() for n in ('app.db', 'cwa.db', '.key'))):
                raise FileExistsError('destination or application data already exists')
            record = {'identity': identity, 'phase': 'copying'}
            _write_marker(in_progress, record, initial=True)
            _flush_directory(state)
        if record['phase'] == 'copying':
            copy_stopped_library(source, library, source_stopped=True, retain_marker=True, cancel_event=cancel_event)
            library = library.resolve(strict=True)
            info = (library / 'metadata.db').stat()
            if list(_volume_identity(library)) != identity['library_volume']:
                raise ValueError('library storage changed during copy')
            record = {'identity': identity, 'phase': 'initializing', 'database_identity': [info.st_dev, info.st_ino]}
            _write_marker(in_progress, record)
            _flush_directory(state)
        else:
            library = library.resolve(strict=True)
            info = (library / 'metadata.db').stat()
            if (record.get('database_identity') != [info.st_dev, info.st_ino]
                    or list(_volume_identity(library)) != identity['library_volume']):
                raise ValueError('copied library identity changed during setup')
            if profile_path.exists() or profile_path.is_symlink():
                if profile_path.is_symlink() or profile_path.stat(follow_symlinks=False).st_file_attributes & 0x400:
                    raise ValueError('unsafe incomplete profile record')
                expected = {'schema': 1, 'library': str(library), 'ingest': str(ingest), 'port': port,
                            **{key: str(value) for key, value in tools.items()}}
                if json.loads(profile_path.read_text(encoding='utf-8')) != expected:
                    raise ValueError('incomplete profile belongs to different inputs')
                profile_path.unlink()
                _flush_directory(state)
            # These files belong exclusively to this recorded, incomplete setup.
            # Do not remove a library, ingest input, backup, or arbitrary file.
            for name in ('app.db', 'app.db-wal', 'app.db-shm', 'app.db-journal',
                         'cwa.db', 'cwa.db-wal', 'cwa.db-shm', 'cwa.db-journal', '.key'):
                path = config / name
                if path.exists() or path.is_symlink():
                    info = path.stat(follow_symlinks=False)
                    if path.is_symlink() or info.st_file_attributes & 0x400 or not path.is_file():
                        raise ValueError('unsafe file in incomplete setup')
                    path.unlink()
            _flush_directory(config)
        copy_marker = library.parent / ('.' + library.name + '.cwng-copy.json')
        if copy_marker.exists() or copy_marker.is_symlink():
            # Verify the helper's ready marker and completed destination before
            # removing it; the initializing phase is already durable.
            copy_stopped_library(source, library, source_stopped=True, cancel_event=cancel_event)
        _check_cancel(cancel_event)
        prepare_databases(state=state, helper=tools['helper'], library=library,
                          username=username, password=password, ownership=ownership)
        _check_cancel(cancel_event)
        profile = {'schema': 1, 'library': str(library), 'ingest': str(ingest), 'port': port,
                   **{key: str(value) for key, value in tools.items()}}
        _write_marker(state / 'native-profile.json', profile, initial=True)
        _flush_directory(state)
        _check_cancel(cancel_event)
        library_info = (library / 'metadata.db').stat()
        if (list(_volume_identity(library)) != identity['library_volume']
                or list(_volume_identity(ingest)) != identity['ingest_volume']
                or list(_volume_identity(state)) != identity['state_volume']
                or [library_info.st_dev, library_info.st_ino] != record['database_identity']):
            raise ValueError('storage identity changed during setup')
        marker = {'schema': 1, 'library': str(library), 'ingest': str(ingest),
                  'app_db': str((config / 'app.db').resolve(strict=True)),
                  'library_volume': identity['library_volume'],
                  'library_database': record['database_identity'],
                  'ingest_volume': identity['ingest_volume'], 'state_volume': identity['state_volume']}
        _write_marker(state / 'setup-complete.json', marker, initial=True)
        _flush_directory(state)
        in_progress.unlink()
        _flush_directory(state)
        return marker
