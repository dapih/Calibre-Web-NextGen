"""Create verified, quiescent native Windows maintenance checkpoints.

This command captures data only. It does not migrate, activate, restore or
delete anything. The caller must retain the matching old program payload and
keep maintenance ownership across any subsequent mutation; this command alone
is not an upgrade transaction. The payload identity is a caller-supplied label,
not verification of installed executable bytes. A future restore must verify
the matching payload and rebind storage identities to restored database files
before startup; the captured setup marker contains the old database identity.
"""

import argparse
from contextlib import closing, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
from types import SimpleNamespace
import uuid

from cryptography.fernet import Fernet
from packaging.version import InvalidVersion, Version

from windows_m0_coordinator import CoordinatorBusy
from windows_m0_inbox import _flush_directory, _volume_identity
from windows_m0_job import ProfileLock, generation_exists
from windows_native_maintenance_gate import adopt_maintenance_gate, maintenance_gate
from windows_native_install_gate import install_gate
from windows_native_launcher import _offline_recovery_owner, _read_profile, _verify_recovery_storage
from windows_native_profile import require_private_directory
from windows_native_recovery_inventory import build_inventory


def _digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _verify_inventory_anchor(recovery, record):
    version = record.get('inventory_version')
    if (not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', version)
            or '..' in version or not isinstance(record.get('checkpoint'), str)
            or not re.fullmatch(r'checkpoint-[0-9a-f]{32}', record['checkpoint'])
            or any(not isinstance(record.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', record[key])
                   for key in ('inventory_sha256', 'bootstrap_sha256'))):
        raise ValueError('invalid recovery inventory anchor')
    path = Path(recovery) / (record['checkpoint'] + '.inventory')
    before = path.stat(follow_symlinks=False)
    if before.st_file_attributes & 0x400 or before.st_nlink != 1 or not path.is_file():
        raise ValueError('unsafe recovery inventory')
    if _digest(path) != record['inventory_sha256']:
        raise ValueError('recovery inventory changed')
    after = path.stat(follow_symlinks=False)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('recovery inventory changed during verification')


def _flush_file(path):
    with path.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _transaction_temp(parent, token, kind):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('invalid maintenance transaction identity')
    return Path(parent) / ('.cwng-activation-' + token + '-' + kind + '.tmp')


def _temp_specs(state, program, token):
    return {kind: _transaction_temp(parent, token, kind) for parent, kind in (
        (state, 'journal'), (state, 'profile-new'), (state, 'profile-old'),
        (program, 'pointer-new'), (program, 'pointer-old'))}


def _reserve_temps(state, program, token):
    """Exclusively create files and record their identity before journal publication."""
    result = {}
    for kind, path in _temp_specs(state, program, token).items():
        with path.open('xb') as stream:
            info = os.fstat(stream.fileno())
            if not info.st_ino or info.st_nlink != 1:
                raise ValueError('transaction reservation has no unique file identity')
            stream.flush()
            os.fsync(stream.fileno())
        result[kind] = [info.st_dev, info.st_ino]
    _flush_directory(Path(state))
    _flush_directory(Path(program))
    return result


def _open_reserved(path, identity):
    if not isinstance(identity, list) or len(identity) != 2 or any(type(item) is not int for item in identity):
        raise ValueError('invalid transaction reservation identity')
    info = path.stat(follow_symlinks=False)
    if info.st_file_attributes & 0x400 or not path.is_file() or info.st_nlink != 1 or [info.st_dev, info.st_ino] != identity:
        raise ValueError('transaction temporary file identity changed')
    stream = path.open('r+b')
    actual = os.fstat(stream.fileno())
    if [actual.st_dev, actual.st_ino] != identity or actual.st_nlink != 1:
        stream.close()
        raise ValueError('transaction temporary file identity changed')
    return stream


def _publish_journal(journal, record, *, initial=False, _fault=None, _previous=None):
    """Atomically publish one token-identified journal, preserving unknown files."""
    token = record['transaction_id']
    # The transaction id must be the first property so a partially written
    # journal temp is recognisably tied to the existing prepared record.
    ordered = {'transaction_id': token, **{key: value for key, value in record.items() if key != 'transaction_id'}}
    data = json.dumps(ordered).encode('utf-8')
    temporary = _transaction_temp(journal.parent, token, 'prepare' if initial else 'journal')
    created = False
    try:
        stream = temporary.open('xb') if initial else _open_reserved(temporary, record['temp_ids']['journal'])
        with stream:
            created = initial
            if not initial and stream.read() != _previous:
                raise ValueError('transaction temporary file changed before journal write')
            stream.seek(0)
            stream.truncate()
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _flush_directory(journal.parent)
        if _fault:
            _fault('prepare-temp' if initial else 'journal-temp')
        if initial:
            os.rename(temporary, journal)
        else:
            os.replace(temporary, journal)
        _flush_directory(journal.parent)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def _owned_temp(path, *, identity, expected=None, token=None):
    """Recognise only a previously reserved transaction temp, including partial writes."""
    try:
        before = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (before.st_file_attributes & 0x400 or not path.is_file() or before.st_size > 65536
            or before.st_nlink != 1 or [before.st_dev, before.st_ino] != identity):
        raise ValueError('unsafe transaction temporary file')
    data = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise ValueError('transaction temporary file changed')
    if expected is None:
        header = ('{"transaction_id": "' + token + '"').encode('ascii')
        valid = header.startswith(data) if len(data) <= len(header) else data.startswith(header)
    else:
        valid = expected.startswith(data)
    if not valid:
        raise ValueError('transaction temporary file has unrecognised contents')
    return path, before.st_dev, before.st_ino, data


def _remove_owned_temp(artifact):
    if artifact is None:
        return
    path, device, inode, data = artifact
    current = path.stat(follow_symlinks=False)
    if (current.st_file_attributes & 0x400 or current.st_nlink != 1 or (current.st_dev, current.st_ino) != (device, inode)
            or path.read_bytes() != data):
        raise ValueError('transaction temporary file changed before cleanup')
    path.unlink()
    _flush_directory(path.parent)


def _reserved_artifacts(record, state, program, expected, *, allow_missing=False):
    token = record.get('transaction_id')
    paths = _temp_specs(state, program, token)
    identities = record.get('temp_ids')
    if not isinstance(identities, dict) or set(identities) != set(paths):
        raise ValueError('invalid transaction reservations')
    seen = set()
    result = {}
    for kind, path in paths.items():
        identity = identities[kind]
        if (not isinstance(identity, list) or len(identity) != 2 or
                any(type(item) is not int for item in identity) or not identity[1] or tuple(identity) in seen):
            raise ValueError('invalid transaction reservation identity')
        seen.add(tuple(identity))
        artifact = _owned_temp(path, identity=identity, token=token if kind == 'journal' else None,
                               expected=None if kind == 'journal' else expected[kind])
        if artifact is None and not allow_missing:
            raise ValueError('transaction reservation is missing')
        result[kind] = artifact
    return result


def _sqlite_file(path):
    with path.open('rb') as source:
        return source.read(16) == b'SQLite format 3\x00'


def _check_key(path):
    try:
        if not path.is_file() or path.stat().st_size != 44:
            raise ValueError('unexpected key length')
        Fernet(path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise ValueError('private encryption key is missing or invalid') from error


def _copy_file(source, destination, sqlite_database):
    if sqlite_database:
        if not _sqlite_file(source):
            raise ValueError('inventoried database is not SQLite')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA journal_mode=DELETE').fetchone() != ('delete',):
                    raise ValueError('SQLite checkpoint cannot be made self-contained')
                if copied.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('SQLite checkpoint failed integrity check')
    else:
        before = source.stat()
        source_hash = _digest(source)
        with source.open('rb') as original, destination.open('xb') as copied:
            shutil.copyfileobj(original, copied, length=1024 * 1024)
        after = source.stat()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if identity(before) != identity(after) or _digest(source) != source_hash or _digest(destination) != source_hash:
            raise ValueError('checkpoint copy differs from stable source')
    _flush_file(destination)


def _copy_tree(source, target, files, *, managed_databases, excluded_locks=(), only_database=False, private_state=False):
    target.mkdir()
    def onerror(error):
        raise error
    for current, directories, names in os.walk(source, followlinks=False, onerror=onerror):
        current = Path(current)
        relative = current.relative_to(source)
        output = target / relative
        for name in (*directories, *names):
            entry = current / name
            if entry.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('checkpoint source contains a reparse point')
        if only_database:
            directories.clear()
            names = [name for name in names if name.casefold() == 'metadata.db']
        for name in directories:
            (output / name).mkdir()
        paths = {name: current / name for name in names
                 if (relative / name).as_posix().casefold() not in excluded_locks}
        databases = {name.casefold() for name in paths
                     if (relative / name).as_posix().casefold() in managed_databases}
        if private_state:
            # Only the explicitly inventoried application databases may have
            # SQLite sidecars folded into a backup. Unknown SQLite state with
            # sidecars needs a separate coverage decision; copying it raw could
            # silently miss committed WAL pages.
            lowers = {name.casefold(): path for name, path in paths.items()}
            for lower, path in lowers.items():
                if lower not in databases and any(lower + suffix in lowers for suffix in ('-wal', '-shm', '-journal')):
                    if _sqlite_file(path):
                        raise ValueError('unknown SQLite state has unresolved sidecars')
        for name, path in paths.items():
            lower = name.casefold()
            if any(lower == database + suffix for database in databases for suffix in ('-wal', '-shm', '-journal')):
                continue
            destination = output / name
            sqlite_database = lower in databases
            _copy_file(path, destination, sqlite_database)
            files.append({'path': destination.relative_to(target.parent).as_posix(),
                          'sha256': _digest(destination), 'sqlite': sqlite_database})


def verify_checkpoint(checkpoint, *, expected_payload_identity=None):
    """Verify all files and SQLite snapshots in a published checkpoint."""
    checkpoint = Path(checkpoint).resolve(strict=True)
    manifest = json.loads((checkpoint / 'checkpoint.json').read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'payload_identity', 'scope', 'files', 'directories'}
            or manifest['schema'] != 1 or manifest['scope'] not in ('state-and-db', 'full')
            or not isinstance(manifest['payload_identity'], str) or not manifest['payload_identity']
            or not isinstance(manifest['files'], list) or not isinstance(manifest['directories'], list)):
        raise ValueError('invalid checkpoint manifest')
    if expected_payload_identity is not None and manifest['payload_identity'] != expected_payload_identity:
        raise ValueError('checkpoint payload identity mismatch')
    seen = set()
    sqlite_entries = set()
    for item in manifest['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'sqlite'}:
            raise ValueError('invalid checkpoint entry')
        name = item['path']
        if (not isinstance(name, str) or '\\' in name or ':' in name or not name or
                PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or
                name.split('/')[0] not in ('state', 'library') or len(name.split('/')) < 2 or
                name.casefold() in seen or not isinstance(item['sha256'], str) or type(item['sqlite']) is not bool):
            raise ValueError('unsafe checkpoint entry')
        seen.add(name.casefold())
        if item['sqlite']:
            sqlite_entries.add(name.casefold())
        path = checkpoint.joinpath(*name.split('/'))
        for component in (path, *path.parents):
            if component == checkpoint:
                break
            if component.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('checkpoint contains a reparse point')
        if not path.is_file() or _digest(path) != item['sha256']:
            raise ValueError('checkpoint file hash mismatch')
        if item['sqlite']:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('checkpoint SQLite integrity failure')
    required_sqlite = {'library/metadata.db', 'state/config/app.db', 'state/config/cwa.db',
                       'state/runtime-ledger.sqlite'}
    required_files = required_sqlite | {'state/setup-complete.json', 'state/config/.key'}
    if not required_files.issubset(seen) or not required_sqlite.issubset(sqlite_entries):
        raise ValueError('checkpoint is missing required data')
    _check_key(checkpoint / 'state' / 'config' / '.key')
    expected_dirs = set()
    for name in manifest['directories']:
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute()
                or name.split('/')[0] not in ('state', 'library')
                or any(part in ('', '.', '..') for part in name.split('/')) or name.casefold() in expected_dirs):
            raise ValueError('unsafe checkpoint directory')
        expected_dirs.add(name.casefold())
    actual, actual_dirs = set(), set()
    def fail(error):
        raise error
    for current, directories, names in os.walk(checkpoint, followlinks=False, onerror=fail):
        for name in (*directories, *names):
            path = Path(current) / name
            if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('checkpoint contains a reparse point')
        for name in names:
            path = (Path(current) / name).relative_to(checkpoint).as_posix()
            if path != 'checkpoint.json':
                actual.add(path.casefold())
        for name in directories:
            actual_dirs.add((Path(current) / name).relative_to(checkpoint).as_posix().casefold())
    if actual != seen or actual_dirs != expected_dirs:
        raise ValueError('checkpoint contains missing or unexpected files')
    return manifest


class _MaintenanceSession:
    def __init__(self, controller):
        self.controller = controller
        self.active = True
        self.prepared = False


def require_maintenance_session(session, controller):
    """Reject a stale or unrelated token before any maintenance mutation."""
    if not isinstance(session, _MaintenanceSession) or not session.active or session.controller is not controller:
        raise ValueError('active maintenance ownership for this profile is required')
    _verify_recovery_storage(controller)


@contextmanager
def maintenance_session(controller):
    """Hold profile ownership and writer exclusion across maintenance steps.

    Callers keep this context open until their checkpoint, mutation, validation
    and activation are done. An unresolved ledger or old job blocks entry.
    """
    require_private_directory(controller.state)
    # Match the bootstrap's lock order: lifetime gate, profile owner, writer gate.
    with maintenance_gate(controller.state):
        # A previous transaction may have mutated data even if its journal is
        # unreadable. Do not treat it as a valid source for a new checkpoint.
        try:
            (controller.state / 'maintenance-journal.json').stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise CoordinatorBusy('unresolved maintenance requires recovery')
        with _offline_recovery_owner(controller) as owner:
            _verify_recovery_storage(controller)
            if owner.ledger.list_jobs('review') or owner.ledger.list_jobs('running'):
                raise CoordinatorBusy('unresolved work prevents maintenance checkpoint')
            owner._acquire_writer_gate(Path(controller.environment['CWA_APP_DB_PATH']).parent)
            _verify_recovery_storage(controller)
            session = _MaintenanceSession(controller)
            try:
                yield session
            finally:
                session.active = False


def create_checkpoint(controller, recovery, *, payload_identity, full=False, session=None, _before_publish=None):
    """Capture a stopped profile and library DB, or the complete library.

    This holds the owner and writer gate for the entire copy and verification.
    Uncertain old jobs, review records and any storage identity change stop it.
    A failed attempt remains in a uniquely named staging directory for review.
    """
    if not isinstance(payload_identity, str) or not payload_identity.strip() or type(full) is not bool:
        raise ValueError('payload identity and checkpoint scope are required')
    if session is None:
        with maintenance_session(controller) as session:
            return create_checkpoint(controller, recovery, payload_identity=payload_identity, full=full,
                                     session=session, _before_publish=_before_publish)
    require_maintenance_session(session, controller)
    if session.prepared:
        raise CoordinatorBusy('prepared maintenance cannot create another checkpoint')
    recovery = require_private_directory(recovery)
    recovery_volume = _volume_identity(recovery)
    sources = (controller.state, controller.library, controller.ingest,
               Path(__file__).resolve().parent.parent)
    for source in sources:
        source = Path(source).resolve(strict=True)
        if recovery == source or recovery.is_relative_to(source) or source.is_relative_to(recovery):
            raise ValueError('recovery and protected paths must not overlap')
    token = uuid.uuid4().hex
    stage, final = recovery / ('pending-' + token), recovery / ('checkpoint-' + token)
    stage.mkdir()
    files = []
    state = Path(controller.state).resolve(strict=True)
    app = Path(controller.environment['CWA_APP_DB_PATH']).resolve(strict=True)
    try:
        app_rel = app.relative_to(state)
    except ValueError as error:
        raise ValueError('application database must be inside private state') from error
    if app_rel.as_posix().casefold() != 'config/app.db':
        raise ValueError('application database is not in the supported private configuration')
    key = app.parent / '.key'
    _check_key(key)
    cwa = app.parent / 'cwa.db'
    if not cwa.is_file():
        raise ValueError('application automation database is missing')
    databases = {app_rel.as_posix().casefold(), cwa.relative_to(state).as_posix().casefold(), 'runtime-ledger.sqlite'}
    locks = {'controller.lock', 'bootstrap-maintenance.lock',
             (app.parent / '.cwng-native-writer.lock').relative_to(state).as_posix().casefold()}
    _copy_tree(state, stage / 'state', files, managed_databases=databases, excluded_locks=locks, private_state=True)
    _copy_tree(controller.library, stage / 'library', files, managed_databases={'metadata.db'}, only_database=not full)
    require_maintenance_session(session, controller)
    if _volume_identity(recovery) != recovery_volume:
        raise ValueError('recovery volume changed during checkpoint')
    directories = [(Path(current) / name).relative_to(stage).as_posix()
                   for current, names, _ in os.walk(stage) for name in names]
    manifest = {'schema': 1, 'payload_identity': payload_identity,
                'scope': 'full' if full else 'state-and-db', 'files': files, 'directories': directories}
    path = stage / 'checkpoint.json'
    with path.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(manifest, output, sort_keys=True, separators=(',', ':'))
        output.flush()
        os.fsync(output.fileno())
    verify_checkpoint(stage, expected_payload_identity=payload_identity)
    for current, _, _ in os.walk(stage, topdown=False):
        _flush_directory(Path(current))
    if _volume_identity(recovery) != recovery_volume:
        raise ValueError('recovery volume changed before checkpoint publication')
    if _before_publish is not None:
        _before_publish()
    os.rename(stage, final)
    _flush_directory(recovery)
    return final


def _versioned_payload_identity(controller, program_root, requested_version=None):
    """Hash one installed payload, without importing or executing its code."""
    program = Path(program_root)
    if not program.is_absolute() or not program.is_dir():
        raise ValueError('installed program directory must exist and be absolute')
    for path in (program, program / 'versions', program / 'active-version.txt', program / 'CWNG.exe'):
        if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
            raise ValueError('installed program path cannot be a reparse point')
    program = program.resolve(strict=True)
    pointer = program / 'active-version.txt'
    value = pointer.read_bytes()
    if len(value) > 127:
        raise ValueError('active payload identity is invalid')
    try:
        active_version = value.decode('ascii').rstrip('\r\n')
    except UnicodeError as error:
        raise ValueError('active payload identity is invalid') from error
    version = active_version if requested_version is None else requested_version
    if (not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', version)
            or '..' in version or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', active_version)
            or '..' in active_version):
        raise ValueError('active payload identity is invalid')
    bootstrap = program / 'CWNG.exe'
    before_bootstrap = bootstrap.stat(follow_symlinks=False)
    bootstrap_digest = _digest(bootstrap)
    after_bootstrap = bootstrap.stat(follow_symlinks=False)
    if ((before_bootstrap.st_dev, before_bootstrap.st_ino, before_bootstrap.st_size, before_bootstrap.st_mtime_ns) !=
            (after_bootstrap.st_dev, after_bootstrap.st_ino, after_bootstrap.st_size, after_bootstrap.st_mtime_ns)):
        raise ValueError('stable bootstrap changed during verification')
    payload = program / 'versions' / version
    if not payload.is_dir() or payload.stat(follow_symlinks=False).st_file_attributes & 0x400:
        raise ValueError('active payload directory is unavailable or unsafe')
    payload = payload.resolve(strict=True)
    if requested_version is None and Path(controller.web_entrypoint).resolve(strict=True) != payload / 'scripts' / 'windows_native_web.py':
        raise ValueError('controller is not from the active payload')
    package = payload / 'native-package.json'
    if package.stat(follow_symlinks=False).st_file_attributes & 0x400:
        raise ValueError('package identity cannot be a reparse point')
    metadata = json.loads(package.read_text(encoding='utf-8'))
    if (not isinstance(metadata, dict) or set(metadata) != {'schema', 'repository', 'version', 'revision', 'architecture'}
            or type(metadata['schema']) is not int or metadata['schema'] != 1
            or metadata['repository'] != 'dapih/Calibre-Web-NextGen' or metadata['architecture'] != 'x64'
            or not isinstance(metadata['version'], str) or not metadata['version']
            or type(metadata['revision']) is not int or metadata['revision'] < 0):
        raise ValueError('active package identity is invalid')
    entries = []
    def fail(error):
        raise error
    for current, directories, files in os.walk(payload, followlinks=False, onerror=fail):
        for name in directories:
            path = Path(current) / name
            if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError('payload contains a reparse point')
            entries.append(['directory', path.relative_to(payload).as_posix()])
        for name in files:
            path = Path(current) / name
            before = path.stat(follow_symlinks=False)
            if before.st_file_attributes & 0x400:
                raise ValueError('payload contains a reparse point')
            digest = _digest(path)
            after = path.stat(follow_symlinks=False)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise ValueError('payload changed during verification')
            entries.append(['file', path.relative_to(payload).as_posix(), digest])
    if pointer.read_bytes() != value:
        raise ValueError('active payload changed during verification')
    serialized = json.dumps({'version_directory': version, 'bootstrap_sha256': bootstrap_digest,
                             'payload': sorted(entries)}, sort_keys=True,
                            separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return 'sha256:' + hashlib.sha256(serialized).hexdigest()


def installed_payload_identity(controller, program_root):
    """Hash the actual active payload tree; this does not authenticate a publisher."""
    return _versioned_payload_identity(controller, program_root)


def create_installed_checkpoint(controller, recovery, program_root, *, session, full=False):
    """Bind a checkpoint to the actual unchanged active installed payload."""
    require_maintenance_session(session, controller)
    raw_program = Path(program_root)
    if raw_program.stat(follow_symlinks=False).st_file_attributes & 0x400:
        raise ValueError('installed program directory cannot be a reparse point')
    program = raw_program.resolve(strict=True)
    destination = Path(recovery).resolve(strict=True)
    if destination == program or destination.is_relative_to(program) or program.is_relative_to(destination):
        raise ValueError('recovery and program paths must not overlap')
    for protected in (controller.state, controller.library, controller.ingest):
        protected = Path(protected).resolve(strict=True)
        if protected == program or protected.is_relative_to(program) or program.is_relative_to(protected):
            raise ValueError('program and protected paths must not overlap')
    program_volume = _volume_identity(program)
    identity = installed_payload_identity(controller, program)
    def unchanged():
        if _volume_identity(program) != program_volume or installed_payload_identity(controller, program) != identity:
            raise ValueError('installed payload changed during checkpoint')
    return create_checkpoint(controller, recovery, payload_identity=identity, full=full,
                             session=session, _before_publish=unchanged)


def prepare_maintenance(controller, recovery, program_root, *, session, full=False, _fault=None):
    """Write a durable no-mutation preparation record under held ownership.

    This internal seam DOES NOT authorize activating or running candidate code.
    It is intentionally not exposed by the CLI or installer until a trusted
    maintenance recovery entrypoint and update transaction exist.
    """
    require_maintenance_session(session, controller)
    if session.prepared:
        raise CoordinatorBusy('maintenance is already prepared')
    journal = Path(controller.state) / 'maintenance-journal.json'
    try:
        journal.stat(follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise CoordinatorBusy('unresolved maintenance requires recovery')
    recovery = require_private_directory(recovery)
    recovery_volume = _volume_identity(recovery)
    program_volume = _volume_identity(Path(program_root))
    checkpoint = create_installed_checkpoint(controller, recovery, program_root, session=session, full=full)
    if _volume_identity(Path(program_root)) != program_volume:
        raise ValueError('program volume changed during maintenance preparation')
    payload_identity = installed_payload_identity(controller, program_root)
    verify_checkpoint(checkpoint, expected_payload_identity=payload_identity)
    program = Path(program_root).resolve(strict=True)
    inventory_version = _active_directory(program)
    inventory = build_inventory(program / 'versions' / inventory_version)
    bootstrap_sha256 = _digest(program / 'CWNG.exe')
    # Keep the inventory adjacent to the durable checkpoint. Its name is
    # derived from that checkpoint; the journal independently anchors bytes.
    # A crash before journal publication leaves an inert retained artifact.
    inventory_path = recovery / (checkpoint.name + '.inventory')
    with inventory_path.open('xb') as stream:
        stream.write(inventory)
        stream.flush()
        os.fsync(stream.fileno())
    _flush_directory(recovery)
    if (installed_payload_identity(controller, program) != payload_identity or
            _active_directory(program) != inventory_version):
        raise ValueError('installed payload changed while creating recovery inventory')
    if _volume_identity(Path(program_root)) != program_volume:
        raise ValueError('program volume changed during maintenance preparation')
    token = uuid.uuid4().hex
    # Reserve exact files before publishing their identities. A crash before
    # the journal leaves harmless, unrecognised files; never remove them.
    temp_ids = _reserve_temps(Path(controller.state), Path(program_root), token)
    record = {'transaction_id': token, 'schema': 1, 'phase': 'prepared-no-mutation', 'checkpoint': checkpoint.name,
              'checkpoint_manifest_sha256': _digest(checkpoint / 'checkpoint.json'),
              'inventory_sha256': hashlib.sha256(inventory).hexdigest(), 'inventory_version': inventory_version,
              'bootstrap_sha256': bootstrap_sha256,
              'payload_identity': payload_identity, 'recovery_volume': list(recovery_volume),
              'program_volume': list(program_volume), 'temp_ids': temp_ids}
    if _volume_identity(recovery) != recovery_volume or _volume_identity(Path(program_root)) != program_volume:
        raise ValueError('recovery volume changed before maintenance preparation')
    # A crash after publication blocks bootstrap. No migration or activation is
    # performed by this seam, and no generic "success" flag clears the journal.
    _publish_journal(journal, record, initial=True, _fault=_fault)
    session.prepared = True
    return checkpoint


@contextmanager
def _journal_recovery_owner(controller, *, inherited_gate=None):
    """Fence recovery without constructing or migrating the runtime ledger."""
    state = require_private_directory(controller.state)
    gate_owner = (maintenance_gate(state) if inherited_gate is None else
                  adopt_maintenance_gate(state, inherited_gate))
    with gate_owner, ProfileLock(controller.helper, state / 'controller.lock'):
        generation = state / 'web-generation'
        try:
            info = generation.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if info.st_file_attributes & 0x400 or generation_exists(controller.helper,
                    generation.read_text(encoding='ascii').strip()):
                raise CoordinatorBusy('old web generation cannot be fenced')
        path = Path(__file__).resolve().parents[1] / 'cps' / 'services' / 'file_lock.py'
        spec = importlib.util.spec_from_file_location('_cwng_checkpoint_recovery_lock', path)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        if api.msvcrt is None:
            raise RuntimeError('native writer locking is unavailable')
        gate = Path(controller.environment['CWA_APP_DB_PATH']).parent / '.cwng-native-writer.lock'
        fd = api.open_lock(str(gate))
        acquired = False
        try:
            acquired = api.acquire(fd, blocking=False)
            if not acquired:
                raise CoordinatorBusy('another writer is active')
            yield
        finally:
            if acquired:
                api.release(fd)
            os.close(fd)


def _logical_sqlite_digest(path):
    result = hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('SQLite integrity cannot be verified')
        for pragma in ('user_version', 'schema_version', 'application_id', 'auto_vacuum', 'page_size', 'encoding'):
            result.update(repr((pragma, connection.execute('PRAGMA ' + pragma).fetchone())).encode('utf-8'))
        for statement in connection.iterdump():
            result.update(statement.encode('utf-8'))
            result.update(b'\n')
    return result.digest()


def _verify_live_state_unchanged(controller, checkpoint, manifest, *, allowed_profile_hashes=(), ignored_state_files=()):
    """Compare live data to its pre-mutation snapshot before cancelling."""
    state = Path(controller.state)
    expected_files = {item['path'].casefold(): item for item in manifest['files']}
    expected_dirs = {name.casefold() for name in manifest['directories']}
    sqlite_names = {name for name, item in expected_files.items() if item['sqlite']}
    actual_files, actual_dirs = set(), set()
    def fail(error):
        raise error
    for label, source in (('state', state), ('library', Path(controller.library))):
        for current, directories, names in os.walk(source, followlinks=False, onerror=fail):
            current = Path(current)
            relative = current.relative_to(source)
            if label == 'library' and manifest['scope'] != 'full':
                directories.clear()
                names = [name for name in names if name.casefold() == 'metadata.db']
            for name in (*directories, *names):
                if (current / name).stat(follow_symlinks=False).st_file_attributes & 0x400:
                    raise ValueError('live state contains a reparse point')
            for name in directories:
                actual_dirs.add((Path(label) / relative / name).as_posix().casefold())
            for name in names:
                rel = (Path(label) / relative / name).as_posix().casefold()
                if label == 'state' and (rel in ignored_state_files or rel in {'state/controller.lock', 'state/bootstrap-maintenance.lock',
                                                'state/maintenance-journal.json',
                                                'state/config/.cwng-native-writer.lock'}):
                    continue
                if any(rel == db + suffix for db in sqlite_names for suffix in ('-wal', '-shm', '-journal')):
                    continue
                item = expected_files.get(rel)
                if item is None:
                    raise ValueError('live state differs from checkpoint')
                actual_files.add(rel)
                snapshot = checkpoint.joinpath(*item['path'].split('/'))
                source_file = current / name
                if item['sqlite']:
                    same = _logical_sqlite_digest(source_file) == _logical_sqlite_digest(snapshot)
                else:
                    before = source_file.stat()
                    digest = _digest(source_file)
                    same = (digest in allowed_profile_hashes if rel == 'state/native-profile.json' and
                            allowed_profile_hashes else digest == item['sha256'])
                    after = source_file.stat()
                    same = same and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                if not same:
                    raise ValueError('live state differs from checkpoint')
    expected_subdirs = expected_dirs - {'state', 'library'}
    if actual_files != set(expected_files) or actual_dirs != expected_subdirs:
        raise ValueError('live state differs from checkpoint')


def cancel_prepared(controller, program_root, recovery, *, _fault=None):
    """Cancel only an unchanged no-mutation preparation; keep the checkpoint.

    Internal recovery seam only. It must be called by a separately trusted
    maintenance recovery entrypoint; normal bootstrap intentionally blocks.
    """
    recovery = require_private_directory(recovery)
    with _journal_recovery_owner(controller):
        _verify_recovery_storage(controller)
        journal = Path(controller.state) / 'maintenance-journal.json'
        info = journal.stat(follow_symlinks=False)
        if info.st_file_attributes & 0x400 or not journal.is_file() or info.st_size > 65536:
            raise ValueError('unsafe maintenance journal')
        journal_bytes = journal.read_bytes()
        record = json.loads(journal_bytes.decode('utf-8'))
        required = {'transaction_id', 'temp_ids', 'schema', 'phase', 'checkpoint', 'checkpoint_manifest_sha256', 'payload_identity', 'recovery_volume', 'program_volume',
                    'inventory_sha256', 'inventory_version', 'bootstrap_sha256'}
        if (not isinstance(record, dict) or set(record) != required or record['schema'] != 1
                or record['phase'] != 'prepared-no-mutation' or not isinstance(record['checkpoint'], str)
                or not re.fullmatch(r'checkpoint-[0-9a-f]{32}', record['checkpoint'])
                or record['recovery_volume'] != list(_volume_identity(recovery))
                or record['program_volume'] != list(_volume_identity(Path(program_root)))):
            raise ValueError('maintenance journal requires manual recovery')
        _verify_inventory_anchor(recovery, record)
        artifacts = _reserved_artifacts(record, controller.state, program_root,
            {'profile-new': b'', 'profile-old': b'', 'pointer-new': b'', 'pointer-old': b''}, allow_missing=True)
        ignored = {'state/' + item[0].name.casefold() for item in artifacts.values()
                   if item and item[0].parent == Path(controller.state)}
        checkpoint = recovery / record['checkpoint']
        before = checkpoint.stat(follow_symlinks=False)
        if before.st_file_attributes & 0x400:
            raise ValueError('unsafe checkpoint path')
        if _digest(checkpoint / 'checkpoint.json') != record['checkpoint_manifest_sha256']:
            raise ValueError('checkpoint manifest changed')
        if installed_payload_identity(controller, program_root) != record['payload_identity']:
            raise ValueError('installed payload changed during maintenance')
        manifest = verify_checkpoint(checkpoint, expected_payload_identity=record['payload_identity'])
        _verify_live_state_unchanged(controller, checkpoint, manifest, ignored_state_files=ignored)
        _verify_recovery_storage(controller)
        after = checkpoint.stat(follow_symlinks=False)
        journal_after = journal.stat(follow_symlinks=False)
        if (journal_after.st_file_attributes & 0x400 or
                (info.st_dev, info.st_ino) != (journal_after.st_dev, journal_after.st_ino) or
                journal.read_bytes() != journal_bytes):
            raise ValueError('maintenance journal changed during verification')
        if (after.st_file_attributes & 0x400 or record['program_volume'] != list(_volume_identity(Path(program_root))) or
                record['recovery_volume'] != list(_volume_identity(recovery)) or
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or
                _digest(checkpoint / 'checkpoint.json') != record['checkpoint_manifest_sha256']):
            raise ValueError('recovery checkpoint changed during verification')
        for kind, artifact in artifacts.items():
            _remove_owned_temp(artifact)
            if _fault:
                _fault('cleanup-' + kind)
        journal.unlink()
        _flush_directory(Path(controller.state))


def _active_directory(program):
    pointer = program / 'active-version.txt'
    if pointer.stat(follow_symlinks=False).st_file_attributes & 0x400:
        raise ValueError('activation pointer cannot be a reparse point')
    raw = pointer.read_bytes()
    if not 0 < len(raw) <= 127:
        raise ValueError('invalid activation pointer')
    try:
        version = raw.decode('ascii').rstrip('\r\n')
    except UnicodeError as error:
        raise ValueError('invalid activation pointer') from error
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', version) or '..' in version:
        raise ValueError('invalid activation pointer')
    return version


def _replace_owned_bytes(path, value, temporary, identity, previous, *, _fault=None, point=None):
    """Durably replace a non-reparse regular file without a partial write."""
    info = path.stat(follow_symlinks=False)
    if info.st_file_attributes & 0x400 or not path.is_file():
        raise ValueError('unsafe activation record')
    with _open_reserved(temporary, identity) as stream:
        if stream.read() != previous:
            raise ValueError('transaction temporary file changed before replacement')
        stream.seek(0)
        stream.truncate()
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    _flush_directory(temporary.parent)
    if _fault:
        _fault(point)
    os.replace(temporary, path)
    _flush_directory(path.parent)


def _guarded_record(controller, recovery):
    """Read a journal and checkpoint with identity/volume verification."""
    journal = Path(controller.state) / 'maintenance-journal.json'
    info = journal.stat(follow_symlinks=False)
    if info.st_file_attributes & 0x400 or not journal.is_file() or info.st_size > 65536:
        raise ValueError('unsafe maintenance journal')
    raw = journal.read_bytes()
    record = json.loads(raw.decode('utf-8'))
    checkpoint_name = record.get('checkpoint') if isinstance(record, dict) else None
    if (not isinstance(checkpoint_name, str) or not re.fullmatch(r'checkpoint-[0-9a-f]{32}', checkpoint_name)
            or record.get('schema') != 1 or record.get('recovery_volume') != list(_volume_identity(recovery))):
        raise ValueError('maintenance journal requires manual recovery')
    checkpoint = recovery / checkpoint_name
    checkpoint_info = checkpoint.stat(follow_symlinks=False)
    if checkpoint_info.st_file_attributes & 0x400 or not checkpoint.is_dir():
        raise ValueError('unsafe checkpoint path')
    if _digest(checkpoint / 'checkpoint.json') != record.get('checkpoint_manifest_sha256'):
        raise ValueError('checkpoint manifest changed')
    manifest = verify_checkpoint(checkpoint, expected_payload_identity=record.get('payload_identity'))
    return journal, info, raw, record, checkpoint, checkpoint_info, manifest


def _check_final_guard(journal, info, raw, checkpoint, checkpoint_info, recovery, record):
    after = checkpoint.stat(follow_symlinks=False)
    journal_after = journal.stat(follow_symlinks=False)
    if (journal_after.st_file_attributes & 0x400 or
            (info.st_dev, info.st_ino) != (journal_after.st_dev, journal_after.st_ino) or
            journal.read_bytes() != raw):
        raise ValueError('maintenance journal changed during verification')
    if (after.st_file_attributes & 0x400 or
            record['recovery_volume'] != list(_volume_identity(recovery)) or
            (checkpoint_info.st_dev, checkpoint_info.st_ino) != (after.st_dev, after.st_ino) or
            _digest(checkpoint / 'checkpoint.json') != record['checkpoint_manifest_sha256']):
        raise ValueError('recovery checkpoint changed during verification')


def stage_guarded_activation(controller, program_root, recovery, candidate_version,
                             expected_candidate_identity, *, session, _fault=None):
    """Switch to a verified candidate while keeping normal startup blocked.

    Internal proof seam: expected identity must come from a verified installer,
    which is not wired yet. This function NEVER clears the journal, runs the
    candidate, migrates data, or authorizes a production update/resume.
    """
    require_maintenance_session(session, controller)
    if not session.prepared:
        raise ValueError('a prepared checkpoint under the same owner is required')
    program = Path(program_root).resolve(strict=True)
    recovery = require_private_directory(recovery)
    journal, info, raw, record, checkpoint, checkpoint_info, manifest = _guarded_record(controller, recovery)
    required = {'transaction_id', 'temp_ids', 'schema', 'phase', 'checkpoint', 'checkpoint_manifest_sha256', 'payload_identity', 'recovery_volume', 'program_volume',
                'inventory_sha256', 'inventory_version', 'bootstrap_sha256'}
    if set(record) != required or record['phase'] != 'prepared-no-mutation':
        raise ValueError('maintenance is not in a prepared state')
    if record['program_volume'] != list(_volume_identity(program)):
        raise ValueError('program volume changed before activation')
    token = record['transaction_id']
    artifacts = _reserved_artifacts(record, controller.state, program,
        {'profile-new': b'', 'profile-old': b'', 'pointer-new': b'', 'pointer-old': b''})
    ignored = {'state/' + item[0].name.casefold() for item in artifacts.values()
               if item and item[0].parent == Path(controller.state)}
    old_version = _active_directory(program)
    _verify_inventory_anchor(recovery, record)
    if old_version != record['inventory_version'] or _digest(program / 'CWNG.exe') != record['bootstrap_sha256']:
        raise ValueError('recovery inventory does not match the original program')
    if installed_payload_identity(controller, program) != record['payload_identity']:
        raise ValueError('old payload changed after checkpoint')
    if candidate_version == old_version or not isinstance(expected_candidate_identity, str):
        raise ValueError('candidate identity is invalid')
    candidate_identity = _versioned_payload_identity(controller, program, candidate_version)
    if candidate_identity != expected_candidate_identity:
        raise ValueError('candidate payload identity mismatch')
    old_payload = program / 'versions' / old_version
    candidate_payload = program / 'versions' / candidate_version
    old_meta = json.loads((old_payload / 'native-package.json').read_text(encoding='utf-8'))
    new_meta = json.loads((candidate_payload / 'native-package.json').read_text(encoding='utf-8'))
    try:
        old_ver, new_ver = Version(old_meta['version']), Version(new_meta['version'])
    except InvalidVersion as error:
        raise ValueError('candidate version is invalid') from error
    if (new_ver.is_prerelease or new_ver.is_devrelease or new_ver.is_postrelease or new_ver.local is not None
            or (new_ver, new_meta['revision']) <= (old_ver, old_meta['revision'])):
        raise ValueError('candidate is not a supported stable upgrade')
    _verify_live_state_unchanged(controller, checkpoint, manifest, ignored_state_files=ignored)
    profile_path = Path(controller.state) / 'native-profile.json'
    old_bytes = profile_path.read_bytes()
    profile = json.loads(old_bytes.decode('utf-8'))
    if not isinstance(profile, dict):
        raise ValueError('native profile is invalid')
    for key in ('runtime', 'helper', 'calibre', 'kepubify', 'magick'):
        try:
            previous = Path(profile[key]).resolve(strict=True)
            relative = previous.relative_to(old_payload.resolve(strict=True))
            replacement = (candidate_payload / relative).resolve(strict=True)
        except (KeyError, OSError, ValueError, TypeError) as error:
            raise ValueError('profile tool path does not belong to the expected payload') from error
        if not replacement.is_relative_to(candidate_payload.resolve(strict=True)):
            raise ValueError('candidate tool path escapes the payload')
        profile[key] = str(replacement)
    new_bytes = json.dumps(profile, sort_keys=True).encode('utf-8')
    switching = dict(record, phase='switching-no-resume', old_version=old_version,
                     candidate_version=candidate_version, candidate_identity=candidate_identity,
                     old_profile_sha256=hashlib.sha256(old_bytes).hexdigest(),
                     new_profile_sha256=hashlib.sha256(new_bytes).hexdigest())
    _check_final_guard(journal, info, raw, checkpoint, checkpoint_info, recovery, record)
    if record['program_volume'] != list(_volume_identity(program)):
        raise ValueError('program volume changed before activation')
    _publish_journal(journal, switching, _fault=_fault, _previous=artifacts['journal'][3])
    if _fault:
        _fault('journal')
    _replace_owned_bytes(profile_path, new_bytes, _transaction_temp(controller.state, token, 'profile-new'),
                         record['temp_ids']['profile-new'], artifacts['profile-new'][3], _fault=_fault, point='profile-temp')
    if _fault:
        _fault('profile')
    if record['program_volume'] != list(_volume_identity(program)):
        raise ValueError('program volume changed during activation')
    _replace_owned_bytes(program / 'active-version.txt', (candidate_version + '\r\n').encode('ascii'),
                         _transaction_temp(program, token, 'pointer-new'), record['temp_ids']['pointer-new'],
                         artifacts['pointer-new'][3], _fault=_fault, point='pointer-temp')
    if _fault:
        _fault('pointer')
    if (_active_directory(program) != candidate_version or _digest(profile_path) != switching['new_profile_sha256']
            or _versioned_payload_identity(controller, program, candidate_version) != candidate_identity):
        raise ValueError('guarded activation did not verify')
    # The journal stays in place; bootstrap must refuse to run candidate code.
    return candidate_identity


def rollback_guarded_activation(controller, program_root, recovery, *, _fault=None, _expected_profile=None,
                                _inherited_gate=None):
    """Idempotently restore the old pointer/profile while startup stays fenced."""
    program = Path(program_root).resolve(strict=True)
    recovery = require_private_directory(recovery)
    with _journal_recovery_owner(controller, inherited_gate=_inherited_gate):
        _verify_recovery_storage(controller)
        journal, info, raw, record, checkpoint, checkpoint_info, manifest = _guarded_record(controller, recovery)
        required = {'transaction_id', 'temp_ids', 'schema', 'phase', 'checkpoint', 'checkpoint_manifest_sha256', 'payload_identity',
                    'program_volume',
                    'recovery_volume', 'old_version', 'candidate_version', 'candidate_identity',
                    'old_profile_sha256', 'new_profile_sha256', 'inventory_sha256', 'inventory_version', 'bootstrap_sha256'}
        if set(record) != required or record['phase'] != 'switching-no-resume':
            raise ValueError('maintenance is not a guarded activation')
        _verify_inventory_anchor(recovery, record)
        if record['old_version'] != record['inventory_version'] or _digest(program / 'CWNG.exe') != record['bootstrap_sha256']:
            raise ValueError('recovery inventory does not match the original program')
        if record['program_volume'] != list(_volume_identity(program)):
            raise ValueError('program volume changed during maintenance')
        token = record['transaction_id']
        _transaction_temp(controller.state, token, 'journal')  # validate identity
        if (not isinstance(record['candidate_version'], str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', record['candidate_version']) or
                '..' in record['candidate_version']):
            raise ValueError('candidate version is invalid')
        old_worker = program / 'versions' / record['old_version'] / 'scripts' / 'windows_native_web.py'
        if Path(controller.web_entrypoint).resolve(strict=True) != old_worker.resolve(strict=True):
            raise ValueError('recovery controller is not from the original payload')
        if (_active_directory(program) not in (record['old_version'], record['candidate_version']) or
                _versioned_payload_identity(controller, program, record['old_version']) != record['payload_identity']):
            raise ValueError('old payload or activation identity changed')
        old_profile = checkpoint / 'state' / 'native-profile.json'
        if _digest(old_profile) != record['old_profile_sha256']:
            raise ValueError('checkpoint profile does not match original')
        profile_path = Path(controller.state) / 'native-profile.json'
        old_bytes = old_profile.read_bytes()
        if _expected_profile is not None and old_bytes != _expected_profile:
            raise ValueError('recovery profile changed before ownership')
        new_profile = json.loads(old_bytes.decode('utf-8'))
        old_payload = (program / 'versions' / record['old_version']).resolve(strict=True)
        candidate_payload = program / 'versions' / record['candidate_version']
        for key in ('runtime', 'helper', 'calibre', 'kepubify', 'magick'):
            try:
                relative = Path(new_profile[key]).resolve(strict=True).relative_to(old_payload)
                new_profile[key] = str((candidate_payload / relative).resolve(strict=False))
            except (KeyError, OSError, ValueError, TypeError) as error:
                raise ValueError('checkpoint profile does not match old payload') from error
        new_bytes = json.dumps(new_profile, sort_keys=True).encode('utf-8')
        if hashlib.sha256(new_bytes).hexdigest() != record['new_profile_sha256']:
            raise ValueError('candidate profile cannot be reconstructed')
        artifacts = _reserved_artifacts(record, controller.state, program, {
            'profile-new': new_bytes, 'profile-old': old_bytes,
            'pointer-new': (record['candidate_version'] + '\r\n').encode('ascii'),
            'pointer-old': (record['old_version'] + '\r\n').encode('ascii')}, allow_missing=True)
        ignored = {'state/' + item[0].name.casefold() for item in artifacts.values()
                   if item and item[0].parent == Path(controller.state)}
        _verify_live_state_unchanged(controller, checkpoint, manifest,
                                     allowed_profile_hashes=(record['old_profile_sha256'], record['new_profile_sha256']),
                                     ignored_state_files=ignored)
        _verify_recovery_storage(controller)
        _check_final_guard(journal, info, raw, checkpoint, checkpoint_info, recovery, record)
        if record['program_volume'] != list(_volume_identity(program)):
            raise ValueError('program volume changed during rollback')
        needs_profile = _digest(profile_path) != record['old_profile_sha256']
        needs_pointer = _active_directory(program) != record['old_version']
        if (needs_profile and artifacts['profile-old'] is None) or (needs_pointer and artifacts['pointer-old'] is None):
            raise ValueError('required rollback reservation is missing')
        for kind in ('journal', 'profile-new', 'pointer-new'):
            _remove_owned_temp(artifacts[kind])
            if _fault:
                _fault('rollback-cleanup-' + kind)
        if needs_profile:
            _replace_owned_bytes(profile_path, old_bytes, _transaction_temp(controller.state, token, 'profile-old'),
                                 record['temp_ids']['profile-old'], artifacts['profile-old'][3],
                                 _fault=_fault, point='rollback-profile-temp')
        else:
            _remove_owned_temp(artifacts['profile-old'])
        if needs_pointer:
            _replace_owned_bytes(program / 'active-version.txt', (record['old_version'] + '\r\n').encode('ascii'),
                                 _transaction_temp(program, token, 'pointer-old'), record['temp_ids']['pointer-old'],
                                 artifacts['pointer-old'][3], _fault=_fault, point='rollback-pointer-temp')
        else:
            _remove_owned_temp(artifacts['pointer-old'])
        _verify_live_state_unchanged(controller, checkpoint, manifest)
        _verify_recovery_storage(controller)
        if installed_payload_identity(controller, program) != record['payload_identity']:
            raise ValueError('restored old payload did not verify')
        if record['program_volume'] != list(_volume_identity(program)):
            raise ValueError('program volume changed during rollback')
        _check_final_guard(journal, info, raw, checkpoint, checkpoint_info, recovery, record)
        journal.unlink()
        _flush_directory(Path(controller.state))


def recover_guarded_activation(state, program_root, recovery, *, _inherited_gate=None):
    """Internal old-payload entrypoint; never imports or runs the candidate."""
    state = require_private_directory(state)
    recovery = require_private_directory(recovery)
    program = Path(program_root).resolve(strict=True)
    # Preliminary verification is repeated under ownership by rollback. The
    # executable and every imported module must come from this old payload.
    journal, info, raw, record, checkpoint, checkpoint_info, manifest = _guarded_record(
        SimpleNamespace(state=state), recovery)
    if record.get('phase') != 'switching-no-resume':
        raise ValueError('maintenance is not a guarded activation')
    old_version = record.get('old_version')
    if (not isinstance(old_version, str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,125}', old_version) or '..' in old_version):
        raise ValueError('original version is invalid')
    payload = (program / 'versions' / old_version).resolve(strict=True)
    if Path(__file__).resolve(strict=True) != payload / 'scripts' / 'windows_native_checkpoint.py':
        raise ValueError('recovery must run from the original installed payload')
    profile = (checkpoint / 'state' / 'native-profile.json').read_bytes()
    if hashlib.sha256(profile).hexdigest() != record.get('old_profile_sha256'):
        raise ValueError('checkpoint profile does not match original')
    controller = _read_profile(state, _profile_bytes=profile)
    if Path(sys.executable).resolve(strict=True) != controller.python:
        raise ValueError('recovery must use the original private interpreter')
    if _versioned_payload_identity(controller, program, old_version) != record.get('payload_identity'):
        raise ValueError('original installed payload changed')
    _check_final_guard(journal, info, raw, checkpoint, checkpoint_info, recovery, record)
    rollback_guarded_activation(controller, program, recovery, _expected_profile=profile,
                                _inherited_gate=_inherited_gate)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--recovery', required=True)
    identities = parser.add_mutually_exclusive_group(required=True)
    identities.add_argument('--program-root', help='Installed program root; hash its active payload')
    identities.add_argument('--payload-identity', help='Diagnostic label only; not release rollback evidence')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    controller = _read_profile(args.state)
    if args.program_root:
        with maintenance_session(controller) as session:
            result = create_installed_checkpoint(controller, args.recovery, args.program_root,
                                                 session=session, full=args.full)
    else:
        result = create_checkpoint(controller, args.recovery, payload_identity=args.payload_identity, full=args.full)
    print(result)


def main():
    with install_gate():
        return _main()


if __name__ == '__main__':
    main()
