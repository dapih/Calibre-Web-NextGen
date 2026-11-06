"""Stopped-source Calibre library copy for native Windows first-run setup.

The caller must first validate filesystem eligibility, permissions and overlap
with ingest, application state and program paths. This helper also checks that
source and destination do not overlap and never publishes a partial copy.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from contextlib import closing

if os.name == 'nt':
    from windows_m0_inbox import _volume_identity
else:
    def _volume_identity(path):
        return (os.stat(path).st_dev,)


def _ordinary(path, *, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'reparse point in copy: {path}')
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError(f'expected directory: {path}')
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError(f'expected regular file: {path}')


class SetupCancelled(Exception):
    """A first-run operation stopped at a recoverable boundary."""


def _check_cancel(event):
    if event is not None and event.is_set():
        raise SetupCancelled('Setup stopped safely; it can be resumed.')


def _digest(path, cancel_event=None):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            _check_cancel(cancel_event)
            digest.update(chunk)
    return digest.digest()


def _walk_error(error):
    raise error


def _write_marker(marker, value, *, initial=False):
    fd, name = tempfile.mkstemp(prefix='.cwng-copy-marker-', dir=marker.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if initial:
            # On Windows rename refuses an existing marker from another run.
            os.rename(temp, marker)
        else:
            os.replace(temp, marker)
    finally:
        temp.unlink(missing_ok=True)


def _manifest(directory, cancel_event=None):
    _ordinary(directory, directory=True)
    files, directories = {}, []
    for root, dirs, names in os.walk(directory, followlinks=False, onerror=_walk_error):
        _check_cancel(cancel_event)
        root = Path(root)
        for name in dirs:
            path = root / name
            _ordinary(path, directory=True)
            directories.append(path.relative_to(directory).as_posix())
        for name in names:
            path = root / name
            _ordinary(path)
            files[path.relative_to(directory).as_posix()] = _digest(path, cancel_event).hex()
    return {'directories': sorted(directories), 'files': files}


def _replace_copy(source, target, temporary_parent, cancel_event=None):
    fd, name = tempfile.mkstemp(prefix='.cwng-copy-', dir=temporary_parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as incoming:
            while chunk := incoming.read(1024 * 1024):
                _check_cancel(cancel_event)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if _digest(source, cancel_event) != _digest(temp, cancel_event):
            raise OSError(f'source changed during copy: {source}')
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _snapshot_database(source, target, temporary_parent, cancel_event=None):
    # SQLite may create or modify SHM files even on a read-only connection.
    # Open only a private copy, including any WAL or rollback journal.
    with tempfile.TemporaryDirectory(prefix='.cwng-db-', dir=temporary_parent) as directory:
        private = Path(directory) / 'metadata.db'
        _replace_copy(source, private, temporary_parent, cancel_event)
        for suffix in ('-wal', '-journal'):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _ordinary(sidecar)
                _replace_copy(sidecar, private.with_name(private.name + suffix), temporary_parent, cancel_event)
        output = Path(directory) / 'snapshot.db'
        with closing(sqlite3.connect(private)) as reader:
            with closing(sqlite3.connect(output)) as writer:
                reader.backup(writer, pages=256, progress=lambda *_: _check_cancel(cancel_event))
                if writer.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise sqlite3.DatabaseError('copied library database failed integrity check')
        with output.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(output, target)


def copy_stopped_library(source, destination, *, source_stopped=False, retain_marker=False, cancel_event=None):
    """Copy a confirmed stopped library, resuming owned staging after interruption.

    The caller must explicitly confirm that external writers are stopped; the
    helper cannot prove other applications or plugins are quiescent. A private
    SQLite backup includes committed WAL data without modifying the source.
    A sibling staging directory and identity marker survive interruption; a
    completed destination is published only by a same-volume directory rename.
    When retain_marker is true, setup must delete the ready marker after its
    own initializing state is durably recorded.
    """
    if source_stopped is not True:
        raise ValueError('confirm the source library is stopped before copying')
    _check_cancel(cancel_event)
    source = Path(source).resolve(strict=True)
    _ordinary(source, directory=True)
    destination = Path(destination)
    if not destination.is_absolute() or not destination.name:
        raise ValueError('destination must be an absolute non-root path')
    parent = destination.parent.resolve(strict=True)
    _ordinary(parent, directory=True)
    destination = parent / destination.name
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('source and destination must not overlap')
    database = source / 'metadata.db'
    _ordinary(database)
    staging = parent / ('.' + destination.name + '.cwng-copy')
    marker = parent / ('.' + destination.name + '.cwng-copy.json')
    db_info = database.stat()
    identity = {'source': str(source), 'destination': str(destination),
                'source_volume': list(_volume_identity(source)),
                'destination_volume': list(_volume_identity(parent)),
                'database_identity': [db_info.st_dev, db_info.st_ino]}
    if marker.exists() or marker.is_symlink():
        _ordinary(marker)
        record = json.loads(marker.read_text(encoding='utf-8'))
        if record.get('identity') != identity:
            raise ValueError('copy staging belongs to another source or destination')
    else:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f'destination already exists: {destination}')
        if staging.exists() or staging.is_symlink():
            raise FileExistsError('unidentified copy staging already exists')
        record = {'identity': identity, 'phase': 'copying'}
        _write_marker(marker, record, initial=True)
    if destination.exists() or destination.is_symlink():
        if (record.get('phase') != 'ready' or staging.exists() or staging.is_symlink()
                or _manifest(destination, cancel_event) != record.get('manifest')):
            raise FileExistsError(f'destination already exists or differs from completed copy: {destination}')
        if not retain_marker:
            marker.unlink()
        return destination
    staging.mkdir(exist_ok=True)
    _ordinary(staging, directory=True)
    expected_files, expected_dirs = set(), {Path('.')}
    for root, dirs, files in os.walk(source, followlinks=False, onerror=_walk_error):
        _check_cancel(cancel_event)
        root = Path(root)
        relative = root.relative_to(source)
        target_root = staging / relative
        for name in dirs:
            source_dir = root / name
            _ordinary(source_dir, directory=True)
            target_dir = target_root / name
            target_dir.mkdir(exist_ok=True)
            _ordinary(target_dir, directory=True)
            expected_dirs.add(relative / name)
        for name in files:
            item = root / name
            _ordinary(item)
            rel = relative / name
            if relative == Path('.') and name.casefold() in ('metadata.db-wal', 'metadata.db-shm', 'metadata.db-journal'):
                continue
            expected_files.add(rel)
            target = staging / rel
            if target.exists() or target.is_symlink():
                _ordinary(target)
            if rel == Path('metadata.db'):
                _snapshot_database(item, target, parent, cancel_event)
            elif not target.exists() or _digest(item, cancel_event) != _digest(target, cancel_event):
                _replace_copy(item, target, parent, cancel_event)
    for root, dirs, files in os.walk(staging, followlinks=False, onerror=_walk_error):
        _check_cancel(cancel_event)
        root = Path(root)
        for name in dirs:
            child = root / name
            _ordinary(child, directory=True)
            if child.relative_to(staging) not in expected_dirs:
                raise ValueError('staging contains a directory absent from the source')
        for name in files:
            child = root / name
            _ordinary(child)
            if child.relative_to(staging) not in expected_files:
                raise ValueError('staging contains a file absent from the source')
    if Path('metadata.db') not in expected_files:
        raise ValueError('library database disappeared during copying')
    current_db = database.stat()
    if (list(_volume_identity(source)) != identity['source_volume']
            or list(_volume_identity(parent)) != identity['destination_volume']
            or [current_db.st_dev, current_db.st_ino] != identity['database_identity']):
        raise ValueError('copy source or destination storage changed')
    record = {'identity': identity, 'phase': 'ready', 'manifest': _manifest(staging, cancel_event)}
    _write_marker(marker, record)
    _check_cancel(cancel_event)
    # On Windows rename refuses an existing destination, including races.
    os.rename(staging, destination)
    if not retain_marker:
        marker.unlink()
    return destination
