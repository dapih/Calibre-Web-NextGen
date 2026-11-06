"""Experimental native entrypoint for an already validated Windows profile.

First-run setup must create the profile manifest and setup authority. Until it
does, this entrypoint reports that setup is required and starts no application.
"""
import argparse
from contextlib import closing, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import sys
import time
import webbrowser

# Isolated mode intentionally omits the script directory. Add only the
# resolved, application-owned sibling directory before loading native modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from windows_m0_inbox import _flush_directory, _locked_source, _volume_identity
from windows_m0_dispatch import EnforcementDispatcher, IngestDispatcher, _digest
from windows_m0_epub_fixer import EpubFixerDispatcher
from windows_m0_activation import ControlRecoveryRequired, open_or_activate
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_job import Job, generation_exists
from windows_m0_supervisor import RestartBudget
from windows_native_controller import NativeController
from windows_native_conversion_recovery import confirm_conversion
from windows_native_ingest_recovery import confirm_ingest
from windows_native_profile import require_private_directory
from windows_native_install_gate import install_gate
from windows_native_network_controls import NetworkControls
from windows_native_lan import authenticated_network_status
from windows_native_network import check_loopback_port
from windows_native_tray import NativeTray
from windows_native_update_controls import UpdateControls
from windows_native_setup import _write_marker
import windows_native_autostart


def _inside(path, parent):
    return path == parent or path.is_relative_to(parent)


def _read_profile(state, *, _profile_bytes=None):
    state = require_private_directory(state)
    manifest = state / 'native-profile.json'
    if _profile_bytes is None:
        if not manifest.is_file() or manifest.stat(follow_symlinks=False).st_file_attributes & 0x400:
            raise ValueError('native profile setup is incomplete')
        _profile_bytes = manifest.read_bytes()
    data = json.loads(_profile_bytes.decode('utf-8'))
    required = {'schema', 'library', 'ingest', 'runtime', 'helper', 'calibre', 'kepubify', 'magick', 'port'}
    if not isinstance(data, dict) or set(data) != required or data['schema'] != 1:
        raise ValueError('invalid native profile')
    root = Path(__file__).resolve().parent.parent
    paths = {key: Path(data[key]).resolve(strict=True) for key in required - {'schema', 'port'}}
    if any(not _inside(paths[key], root) for key in ('runtime', 'helper', 'calibre', 'kepubify', 'magick')):
        raise ValueError('private runtime and tools must be inside the program directory')
    if not paths['library'].is_dir() or not paths['ingest'].is_dir():
        raise ValueError('library and ingest folders must exist')
    for path in (state, paths['library'], paths['ingest']):
        _volume_identity(path)
    for left, right in ((state, paths['library']), (state, paths['ingest']),
                        (paths['library'], paths['ingest']), (root, state),
                        (root, paths['library']), (root, paths['ingest'])):
        if _inside(left, right) or _inside(right, left):
            raise ValueError('program, state, library and ingest locations must not overlap')
    config = (state / 'config').resolve(strict=True)
    if not _inside(config, state):
        raise ValueError('configuration must be inside private state')
    temporary = state / 'tmp'
    # Reading a profile must be side-effect free: an unresolved maintenance
    # journal may depend on the precise pre-maintenance state. The controller
    # creates this directory only after taking ownership and checking the journal.
    try:
        info = temporary.stat(follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if getattr(info, 'st_file_attributes', 0) & 0x400 or not temporary.is_dir():
            raise ValueError('temporary directory must be a local directory')
    if temporary.resolve(strict=False).parent != state:
        raise ValueError('temporary directory escapes private state')
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    system32 = Path(env['SYSTEMROOT']) / 'System32'
    env.update({'PATH': os.pathsep.join(map(str, (paths['magick'], paths['calibre'], system32))),
                'HOME': str(state), 'USERPROFILE': str(state), 'APPDATA': str(state),
                'LOCALAPPDATA': str(state), 'TEMP': str(temporary), 'TMP': str(temporary),
                'PYTHONDONTWRITEBYTECODE': '1',
                'CALIBRE_DBPATH': str(config), 'CALIBRE_CONFIG_DIRECTORY': str(config),
                'CALIBRE_CACHE_DIRECTORY': str(state / 'calibre-cache'), 'CWA_DB_PATH': str(config),
                'CWA_APP_DB_PATH': str(config / 'app.db'), 'CWA_METADATA_LOCK_DIR': str(config),
                'CWA_TMP_CONVERSION_DIR': str(temporary), 'CWNG_CALIBRE_TOOLS': str(paths['calibre']),
                'CWNG_KEPUBIFY_PATH': str(paths['kepubify']), 'MAGICK_HOME': str(paths['magick']),
                'MAGICK_CONFIGURE_PATH': str(paths['magick']),
                'MAGICK_CODER_MODULE_PATH': str(paths['magick'] / 'modules' / 'coders')})
    return NativeController(helper=paths['helper'], state=state, runtime=paths['runtime'],
                            library=paths['library'], ingest=paths['ingest'], port=data['port'],
                            environment=env, web_entrypoint=root / 'scripts' / 'windows_native_web.py')


def _message(text, flags=0):
    return ctypes.windll.user32.MessageBoxW(None, text, 'CWNG', flags)


def _startup_executable():
    """Resolve the stable installer launcher, never a versioned Python path."""
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    if not local.is_absolute():
        raise ValueError('Windows user application directory is unavailable')
    program = (local / 'Programs' / 'CWNG-dapih').resolve(strict=True)
    root = Path(__file__).resolve().parent.parent
    executable = (program / 'CWNG.exe').resolve(strict=True)
    if not _inside(root, program) or executable.parent != program or not executable.is_file():
        raise ValueError('installed stable launcher is unavailable')
    return executable


def _toggle_login_startup(state):
    """Ask the user before changing the per-user startup entry."""
    executable = _startup_executable()
    enabled = windows_native_autostart.is_enabled(executable, state)
    prompt = ('CWNG currently starts when you sign in. Stop starting it automatically?'
              if enabled else 'Start CWNG automatically when you sign in?')
    if _message(prompt, 4) == 6:  # Yes; default remains off until consent.
        windows_native_autostart.set_enabled(executable, state, not enabled)
        _message('Automatic startup is now ' + ('off.' if enabled else 'on.'))


def _review_text(ledger):
    entries = ledger.list_jobs('review')
    if not entries:
        return 'No work is awaiting review.'
    lines = ['Work is paused pending review. Inspect the retained inputs before taking action.']
    for entry in entries[:20]:
        lines.append(f"\n{entry['id']}  {entry['operation']}\n{str(entry.get('evidence') or '')[:240]}")
    if len(entries) > 20:
        lines.append(f'\n{len(entries) - 20} more entries are recorded in the local ledger.')
    lines.append('\nUnstarted metadata-debounce jobs can be retried with --retry-reviewed JOB_ID; '
                 'unstarted ingest can be retried with --retry-ingest JOB_ID. '
                 'A changed-output EPUB repair can be checked with --confirm-epub-repair JOB_ID; '
                 'a conversion with a durable completion marker can be checked with --confirm-conversion JOB_ID; '
                 'a qualifying original EPUB ingest can be checked with --confirm-ingest JOB_ID. '
                 'Other work requires operation-specific verification.')
    return '\n'.join(lines)


@contextmanager
def _offline_recovery_owner(controller):
    """Take the profile lock and fence both web and writer generations."""
    NativeController.require_no_maintenance_journal(controller.state)
    with Coordinator(controller.helper, controller.state) as owner:
        NativeController.require_no_maintenance_journal(controller.state)
        if controller.generation_file.exists():
            previous = controller.generation_file.read_text(encoding='ascii').strip()
            if generation_exists(controller.helper, previous):
                raise CoordinatorBusy('previous web process is still active; recovery cannot begin')
        owner.fence_interrupted()
        yield owner


def _verify_recovery_storage(controller):
    marker = json.loads((controller.state / 'setup-complete.json').read_text(encoding='utf-8'))
    app = Path(controller.environment['CWA_APP_DB_PATH']).resolve(strict=True)
    database = (controller.library / 'metadata.db').resolve(strict=True)
    info = database.stat()
    if (not isinstance(marker, dict) or marker.get('schema') != 1 or
            marker.get('library') != str(controller.library) or marker.get('ingest') != str(controller.ingest) or
            marker.get('app_db') != str(app) or
            marker.get('library_volume') != list(_volume_identity(controller.library)) or
            marker.get('ingest_volume') != list(_volume_identity(controller.ingest)) or
            marker.get('state_volume') != list(_volume_identity(controller.state)) or
            marker.get('library_database') != [info.st_dev, info.st_ino]):
        raise ValueError('configured storage identity changed; recovery remains paused')
    return database


def _retry_unstarted_metadata(controller, job_id):
    """Retry only a never-started metadata quiet-period notification."""
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)
        def verify(record, outcome):
            intent = record.get('intent')
            if (outcome != 'retry' or record.get('operation') != 'metadata-debounce' or
                    record.get('generation') is not None or record.get('attempts') != 0 or not isinstance(intent, dict) or
                    set(intent) != {'book_id', 'due_ns'}):
                return False
            book_id, deadline = intent['book_id'], intent['due_ns']
            if (type(book_id) is not int or book_id <= 0 or type(deadline) is not int or deadline <= 0):
                return False
            for pending in owner.ledger.list_jobs('pending'):
                if (pending['operation'] == 'metadata-debounce' and
                        pending['intent'].get('book_id') == book_id):
                    return False
            # Use the same offline, read-only eligibility check that the
            # scheduler will use when it promotes this notification. Merely
            # finding a book row cannot establish that it has a safe EPUB.
            enforcer = EnforcementDispatcher.__new__(EnforcementDispatcher)
            enforcer.library = controller.library
            enforcer.database = database
            info = database.stat()
            enforcer.database_identity = (info.st_dev, info.st_ino)
            enforcer.library_volume = _volume_identity(controller.library)
            try:
                enforcer.prepare(book_id)
                return _verify_recovery_storage(controller) == database
            except (OSError, ValueError, sqlite3.Error):
                return False
        owner.reconcile(job_id, outcome='retry',
                        evidence='unstarted metadata notification verified for retry', verify=verify)
    return 'The unstarted metadata notification is queued again. Other reviewed work remains paused.'


def _retry_unstarted_ingest(controller, job_id):
    """Requeue an ingest only if no worker was ever claimed and inputs match."""
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)

        def verify(record, outcome):
            intent = record.get('intent')
            if (outcome != 'retry' or record.get('operation') != 'ingest' or
                    record.get('generation') is not None or record.get('attempts') != 0 or
                    not isinstance(intent, dict) or set(intent) != {'source', 'staged', 'sha256', 'size'}):
                return False
            digest, size = intent['sha256'], intent['size']
            if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
                    type(size) is not int or size < 0 or
                    not isinstance(intent['source'], str) or not isinstance(intent['staged'], str)):
                return False
            source = Path(intent['source'])
            staged = Path(intent['staged'])
            stage_root = (controller.state / 'staged').resolve(strict=True)
            if not source.is_absolute() or not staged.is_absolute() or not stage_root.is_relative_to(controller.state):
                return False
            resolved_source = source.resolve(strict=True)
            resolved_stage = staged.resolve(strict=True)
            for pending in owner.ledger.list_jobs('pending'):
                pending_source = pending['intent'].get('source')
                if (pending['operation'] == 'ingest' and isinstance(pending_source, str) and
                        os.path.normcase(os.path.abspath(pending_source)) == os.path.normcase(str(resolved_source))):
                    return False
            stage_info = staged.stat(follow_symlinks=False)
            if (not resolved_source.is_relative_to(controller.ingest) or
                    not resolved_stage.is_relative_to(stage_root) or not resolved_stage.is_file() or
                    getattr(stage_info, 'st_file_attributes', 0) & 0x400 or stage_info.st_nlink != 1 or
                    stage_info.st_size != size or _digest(resolved_stage) != digest or
                    any(Path(str(source) + suffix).exists() for suffix in ('.cwa.json', '.cwa.failed.json'))):
                return False
            # Match the admission path: deny writers and deletion while reading
            # and ensure the opened file resolves inside the selected inbox.
            with _locked_source(source, controller.ingest, resolved_source) as stream:
                if os.fstat(stream.fileno()).st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    return False
            return _verify_recovery_storage(controller) == database

        owner.reconcile(job_id, outcome='retry',
                        evidence='never-started ingest inputs verified for retry', verify=verify)
    return 'The unstarted ingest is queued again. Other reviewed work remains paused.'


def _retry_unstarted_request(controller, job_id):
    """Requeue only an unprepared bulk or repair request that never ran."""
    operations = {
        'convert-library-request': 'convert-library',
        'manual-enforce-request': 'manual-enforce',
        'epub-fixer-request': 'epub-fixer',
    }
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)

        def verify(record, outcome):
            operation = record.get('operation')
            intent = record.get('intent')
            if (outcome != 'retry' or operation not in operations or
                    record.get('generation') is not None or record.get('attempts') != 0):
                return False
            if operation == 'epub-fixer-request':
                if (not isinstance(intent, dict) or set(intent) != {'book_id'} or
                        (intent['book_id'] is not None and
                         (type(intent['book_id']) is not int or intent['book_id'] <= 0))):
                    return False
            elif intent != {}:
                return False
            for state in ('pending', 'running'):
                if any(item['operation'] in (operation, operations[operation])
                       for item in owner.ledger.list_jobs(state)):
                    return False
            return _verify_recovery_storage(controller) == database

        owner.reconcile(job_id, outcome='retry',
                        evidence='never-started request queued for preparation', verify=verify)
    return 'The request is queued again. CWNG will check whether it can run.'


def _confirm_conversion(controller, job_id):
    """Verify a completed conversion while holding exclusive offline ownership."""
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)
        return confirm_conversion(controller, owner, database, job_id, lambda: _verify_recovery_storage(controller))


def _confirm_ingest(controller, job_id):
    """Verify a qualifying ingest while holding exclusive offline ownership."""
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)
        return confirm_ingest(controller, owner, database, job_id, lambda: _verify_recovery_storage(controller))


def _reset_restart_budget(controller, role):
    """Explicitly clear only a non-writing role's restart budget."""
    if role not in ('control', 'web'):
        raise ValueError('unknown restart role')
    with _offline_recovery_owner(controller) as owner:
        _verify_recovery_storage(controller)
        owner.ledger.restart_state(role, reset=True)
    return 'Restart budget cleared. Any reviewed work remains paused.'


def _confirm_epub_repair(controller, job_id):
    """Complete a fenced repair only when every output proves publication.

    A changed output can only be published by the managed worker after its
    fixer exits successfully; unchanged outputs cannot prove that it ran.
    Existing verifier checks cover backup hashes, archive integrity, database
    consistency and sizes. All other outcomes stay in review.
    """
    with _offline_recovery_owner(controller) as owner:
        database = _verify_recovery_storage(controller)

        class OfflineIngest:
            _check_settings = IngestDispatcher._check_settings

        ingest = OfflineIngest()
        ingest.owner = owner
        ingest.library = controller.library
        ingest.database = database
        ingest.app_db = Path(controller.environment['CWA_APP_DB_PATH']).resolve(strict=True)
        ingest.cwa_db = (Path(controller.environment['CWA_DB_PATH']) / 'cwa.db').resolve(strict=True)
        ingest.work_root = (controller.state / 'ingest-work').resolve(strict=True)
        if not ingest.work_root.is_relative_to(controller.state):
            raise ValueError('repair evidence escapes private state')
        verifier = EpubFixerDispatcher(ingest)

        def verify(record, outcome):
            if (outcome != 'completed' or record.get('operation') != 'epub-fixer' or
                    not isinstance(record.get('generation'), str) or
                    generation_exists(controller.helper, record['generation'])):
                return False
            intent = record.get('intent')
            if not isinstance(intent, dict) or set(intent) != {'book_id', 'files'}:
                return False
            files = intent['files']
            if not isinstance(files, list) or not files:
                return False
            selected = intent['book_id']
            if selected is not None and (type(selected) is not int or selected <= 0):
                return False
            seen = set()
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                for item in files:
                    if (not isinstance(item, dict) or set(item) != {'book_id', 'format', 'path', 'sha256'} or
                            type(item['book_id']) is not int or item['book_id'] <= 0 or
                            (selected is not None and item['book_id'] != selected) or
                            item['format'] not in ('EPUB', 'EPUB3') or not isinstance(item['path'], str) or
                            not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                        return False
                    identity = (item['book_id'], item['format'])
                    if identity in seen:
                        return False
                    seen.add(identity)
                    rows = db.execute('SELECT books.path, data.name, data.format FROM books '
                                      'JOIN data ON books.id=data.book WHERE books.id=? AND data.format=?',
                                      identity).fetchall()
                    if len(rows) != 1:
                        return False
                    current = verifier._path(*rows[0])
                    recorded = (controller.library / item['path']).resolve(strict=True)
                    if current != recorded or _digest(current) == item['sha256']:
                        return False
            # No file-only shortcut: run the exact normal completion verifier,
            # then check the storage identity once more before the transition.
            return verifier.verify(job_id) is True and _verify_recovery_storage(controller) == database

        owner.reconcile(job_id, outcome='completed', evidence='offline EPUB repair verified after fencing',
                        verify=verify)
    return 'The completed EPUB repair was verified. Other reviewed work remains paused.'


def _review_profile(controller):
    NativeController.require_no_maintenance_journal(controller.state)
    local, existing = open_or_activate(controller.helper, controller.state)
    if existing is not None:
        NativeController.require_no_maintenance_journal(controller.state)
        return 'CWNG is already running. Open Diagnostics from its tray menu.'
    try:
        NativeController.require_no_maintenance_journal(controller.state)
        if controller.generation_file.exists():
            previous = controller.generation_file.read_text(encoding='ascii').strip()
            if generation_exists(controller.helper, previous):
                raise CoordinatorBusy('previous web process is still active; review cannot begin')
        local.owner.fence_interrupted()
        return _review_text(local.owner.ledger)
    finally:
        local.close()


def _recover_work(controller, reason):
    """Let the user choose an existing, independently verified recovery action."""
    from tkinter import simpledialog
    import tkinter as tk

    try:
        with _offline_recovery_owner(controller) as owner:
            _verify_recovery_storage(controller)
            jobs = owner.ledger.list_jobs('review')
    except (CoordinatorBusy, OSError, ValueError) as error:
        _message(str(error), 0x10)
        return False
    if not jobs:
        _message(reason, 0x10)
        return False

    actions = []
    for job in jobs:
        operation = job['operation']
        if operation == 'metadata-debounce' and job.get('attempts') == 0:
            action, label = _retry_unstarted_metadata, 'Retry metadata notification'
        elif operation == 'ingest' and job.get('attempts') == 0:
            action, label = _retry_unstarted_ingest, 'Retry unstarted import'
        elif operation == 'ingest':
            action, label = _confirm_ingest, 'Verify completed import'
        elif operation == 'convert-library':
            action, label = _confirm_conversion, 'Verify completed conversion'
        elif operation == 'epub-fixer':
            action, label = _confirm_epub_repair, 'Verify completed EPUB repair'
        elif operation in ('convert-library-request', 'manual-enforce-request', 'epub-fixer-request') and job.get('attempts') == 0:
            action, label = _retry_unstarted_request, 'Retry unstarted ' + {
                'convert-library-request': 'library conversion',
                'manual-enforce-request': 'manual enforcement',
                'epub-fixer-request': 'EPUB repair',
            }[operation]
        else:
            continue
        actions.append((job['id'], label, action))
    if not actions:
        _message('Work needs review, but no automatic recovery is available. '
                 'Your inputs are retained. Open the logs for details.', 0x10)
        return False

    root = tk.Tk()
    root.withdraw()
    try:
        choices = '\n'.join(f'{index}. {label} ({job_id})'
                            for index, (job_id, label, _) in enumerate(actions, 1))
        selection = simpledialog.askinteger(
            'Review unfinished work',
            'CWNG stopped because work needs review. Choose an action, or cancel.\n\n' + choices,
            parent=root, minvalue=1, maxvalue=len(actions),
        )
    finally:
        root.destroy()
    if selection is None:
        return False
    job_id, label, action = actions[selection - 1]
    if _message(label + '?\n\nCWNG will verify the stored evidence before changing the queue.', 4 | 0x20) != 6:
        return False
    try:
        _message(action(controller, job_id))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        _message('Work remains paused: ' + str(error), 0x10)
        return False
    return True


def _change_port(controller, port):
    """Change the saved local port only while no existing owner can write."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Enter a port from 1 to 65535.')
    with _offline_recovery_owner(controller):
        _verify_recovery_storage(controller)
        if not check_loopback_port(port):
            raise ValueError('That local port is already in use. Choose another port.')
        path = controller.state / 'native-profile.json'
        info = path.lstat()
        if not path.is_file() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe native profile record.')
        profile = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(profile, dict) or profile.get('schema') != 1 or
                profile.get('port') != controller.port):
            raise ValueError('The configured port changed; reload the profile and retry.')
        profile['port'] = port
        _write_marker(path, profile)
        _flush_directory(controller.state)
    return f'Local port changed to {port}.'


def _prompt_replacement_port(current, error=''):
    """Ask locally for an explicit replacement; cancellation leaves state intact."""
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    try:
        return simpledialog.askinteger(
            'CWNG port unavailable',
            (error + '\n\n' if error else '') +
            f'Local port {current} is unavailable. Choose another port, or cancel to keep it.',
            parent=root, initialvalue=current, minvalue=1, maxvalue=65535,
        )
    finally:
        root.destroy()


def _start_with_port_retry(controller, state):
    """Preserve another listener; retry only after an explicit port choice."""
    while True:
        try:
            return controller, controller.start()
        except (CoordinatorBusy, ControlRecoveryRequired):
            raise
        except RuntimeError:
            if check_loopback_port(controller.port):
                raise
            error = ''
            while True:
                replacement = _prompt_replacement_port(controller.port, error)
                if replacement is None:
                    return None, None
                try:
                    _change_port(controller, replacement)
                except ValueError as problem:
                    error = str(problem)
                    continue
                controller = _read_profile(state)
                break


def _launch_setup(args):
    """Run first-run UI in a contained private process; no credentials in argv."""
    state = require_private_directory(args.state)
    NativeController.require_no_maintenance_journal(state)
    _volume_identity(state)
    root = Path(__file__).resolve().parent.parent
    names = ('helper', 'runtime', 'calibre', 'kepubify', 'magick')
    if any(getattr(args, name) is None for name in names):
        raise ValueError('first-run setup needs the private runtime and tool paths')
    paths = {name: Path(getattr(args, name)).resolve(strict=True) for name in names}
    if any(not _inside(value, root) for value in paths.values()):
        raise ValueError('first-run runtime and tools must be private program assets')
    if any((state / name).exists() for name in ('web-generation', 'runtime-ledger.sqlite')):
        raise ValueError('existing runtime state requires local review before setup')
    executable = (paths['runtime'] / 'Scripts' / 'python.exe').resolve(strict=True)
    setup_script = (root / 'scripts/windows_native_setup_ui.py').resolve(strict=True)
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    env.update({'PATH': os.pathsep.join(map(str, (paths['magick'], paths['calibre'],
                                                   Path(env['SYSTEMROOT']) / 'System32'))),
                'HOME': str(state), 'USERPROFILE': str(state), 'APPDATA': str(state),
                'LOCALAPPDATA': str(state), 'TEMP': str(state), 'TMP': str(state),
                'PYTHONDONTWRITEBYTECODE': '1'})
    arguments = ['-I', '-B', str(setup_script), '--state', str(state)]
    for name in names:
        arguments.extend(['--' + name, str(paths[name])])
    job = Job(paths['helper'])
    process = None
    try:
        process = job.spawn(executable, arguments, cwd=state, environment=env)
        code = process.wait()
        if code != 0:
            raise RuntimeError('first-run setup process failed')
    finally:
        job.close()
        if process is not None:
            process.close()
    # Cancel and success both exit normally. Only the durable setup authority
    # allows the controller to continue into the normal validated startup.
    return (state / 'setup-complete.json').is_file()


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Existing validated private profile directory')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--review', action='store_true', help='Inspect locally retained work awaiting review')
    actions.add_argument('--retry-reviewed', metavar='JOB_ID', help='Retry a verified unstarted metadata notification')
    actions.add_argument('--retry-ingest', metavar='JOB_ID', help='Retry verified ingest that was never started')
    actions.add_argument('--reset-restarts', choices=('control', 'web'), help='Clear a corrected role restart budget')
    actions.add_argument('--confirm-epub-repair', metavar='JOB_ID', help='Verify a completed EPUB repair after recovery')
    actions.add_argument('--confirm-conversion', metavar='JOB_ID', help='Verify a completed library conversion after recovery')
    actions.add_argument('--confirm-ingest', metavar='JOB_ID', help='Verify a qualifying completed EPUB ingest after recovery')
    actions.add_argument('--recover-password', action='store_true', help='Recover an administrator password locally')
    actions.add_argument('--set-port', type=int, metavar='PORT', help='Change the local port while CWNG is stopped')
    for name in ('helper', 'runtime', 'calibre', 'kepubify', 'magick'):
        parser.add_argument('--' + name, help='Private program asset used by first-run setup')
    args = parser.parse_args(argv)
    if not (args.review or args.retry_reviewed or args.retry_ingest or args.reset_restarts or args.confirm_epub_repair or args.confirm_conversion or args.confirm_ingest or args.recover_password or args.set_port is not None) and not (Path(args.state) / 'setup-complete.json').is_file():
        if not _launch_setup(args):
            return 0
    controller = _read_profile(args.state)
    if args.set_port is not None:
        _message(_change_port(controller, args.set_port))
        return 0
    if args.recover_password:
        from windows_native_password import prompt_and_recover
        prompt_and_recover(controller)
        return 0
    if args.review:
        _message(_review_profile(controller))
        return 0
    if args.retry_reviewed:
        _message(_retry_unstarted_metadata(controller, args.retry_reviewed))
        return 0
    if args.retry_ingest:
        _message(_retry_unstarted_ingest(controller, args.retry_ingest))
        return 0
    if args.reset_restarts:
        _message(_reset_restart_budget(controller, args.reset_restarts))
        return 0
    if args.confirm_epub_repair:
        _message(_confirm_epub_repair(controller, args.confirm_epub_repair))
        return 0
    if args.confirm_conversion:
        _message(_confirm_conversion(controller, args.confirm_conversion))
        return 0
    if args.confirm_ingest:
        _message(_confirm_ingest(controller, args.confirm_ingest))
        return 0
    while True:
        try:
            controller, duplicate = _start_with_port_retry(controller, args.state)
            break
        except CoordinatorBusy as error:
            if not _recover_work(controller, str(error)):
                return 1
            controller = _read_profile(args.state)
        except ControlRecoveryRequired:
            if _message('The local control service stopped after repeated failures. '
                        'Check the logs in ' + str(controller.state / 'config') +
                        '. After correcting the cause, reset its restart limit and try again?', 4 | 0x20) != 6:
                return 1
            try:
                _reset_restart_budget(controller, 'control')
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                _message('Recovery could not start: ' + str(error), 0x10)
                return 1
            controller = _read_profile(args.state)
    if controller is None:
        return 0
    if duplicate is not None:
        _message('CWNG is already running for this profile.')
        return 0
    tray = NativeTray()
    updates = UpdateControls(args.state, Path(__file__).resolve().parent.parent)
    network = NetworkControls(args.state, controller.port,
                              listener_status=lambda: authenticated_network_status(controller.readiness))
    recover_requested = False
    try:
        tray.start()
        updates.start()
        tray.set_status('CWNG is ready')
        finishing = None
        running = True
        retry_at = None
        displayed = None
        while True:
            if running:
                try:
                    status = controller.step(timeout=.2)
                except CoordinatorBusy as error:
                    controller.pause()
                    running = False
                    retry_at = None
                    tray.set_status('Local control stopped; recovery required')
                    print(str(error), file=sys.stderr)
                    continue
                except RuntimeError as error:
                    controller.pause()
                    ledger = controller.local.owner.ledger
                    review = ledger.list_jobs('review') or ledger.list_jobs('running')
                    delay = None if review else RestartBudget(ledger=ledger, role='web').failed()
                    running = False
                    retry_at = time.monotonic() + delay if delay is not None else None
                    tray.set_status('Web stopped; recovery required' if delay is None else 'Web stopped; retrying')
                    print(str(error), file=sys.stderr)
                    continue
                label = {'storage': 'Storage unavailable; work paused',
                         'review': 'Work needs review', 'recovery': 'Recovery required',
                         'running': 'Processing a book', 'draining': 'Finishing current book',
                         'idle': 'CWNG is ready', 'waiting': 'CWNG is ready',
                         'stopped': 'CWNG is stopped'}.get(status, 'CWNG is running')
                if label != displayed:
                    tray.set_status(label)
                    displayed = label
                if finishing and status in ('stopped', 'review', 'recovery'):
                    if finishing in ('exit', 'recover'):
                        recover_requested = finishing == 'recover'
                        break
                    controller.pause()
                    running, finishing = False, None
                    tray.set_status('CWNG is stopped')
            else:
                status = 'stopped'
                time.sleep(.2)
                if retry_at is not None and time.monotonic() >= retry_at:
                    retry_at = None
                    try:
                        controller.start()
                        running = True
                        tray.set_status('CWNG is ready')
                    except Exception as error:
                        if controller.local is not None and not isinstance(error, (CoordinatorBusy, ValueError)):
                            delay = RestartBudget(ledger=controller.local.owner.ledger, role='web').failed()
                            retry_at = time.monotonic() + delay if delay is not None else None
                        tray.set_status('Web stopped; retrying' if retry_at is not None else 'Recovery required')
                        print(str(error), file=sys.stderr)
            try:
                command = tray.commands.get_nowait()
            except queue.Empty:
                continue
            if command == 'open':
                if running and controller.readiness.verify():
                    webbrowser.open('http://127.0.0.1:' + str(controller.port))
                else:
                    _message('The library is not ready. Start CWNG before opening it.')
            elif command == 'locations':
                _message('Library: ' + str(controller.library) + '\nIngest: ' + str(controller.ingest) +
                         '\nApplication state: ' + str(controller.state))
            elif command == 'diagnostics':
                log_dir = controller.state / 'config'
                choice = _message('Application state: ' + str(controller.state) + '\nCurrent status: ' + status +
                                  '\n\n' + _review_text(controller.local.owner.ledger) +
                                  '\n\nOpen the default log folder?', 0x4 | 0x20)
                if choice == 6:
                    try:
                        os.startfile(log_dir)
                    except OSError as error:
                        _message('Unable to open the log folder: ' + str(error), 0x10)
            elif command == 'recover-password':
                if not running or controller.scheduler.active is None:
                    recover_requested = True
                    break
                choice = _message('Finish the current book before recovering the administrator password?\n\nYes: finish current book.\nNo: stop safely.\nCancel: keep running.', 3)
                if choice == 6:
                    controller.finish_current()
                    finishing = 'recover'
                elif choice == 7:
                    controller.stop_safely()
                    recover_requested = True
                    break
            elif command == 'autostart':
                try:
                    _toggle_login_startup(controller.state)
                except (OSError, ValueError) as error:
                    _message('Unable to change automatic startup: ' + str(error), 0x10)
            elif command == 'updates':
                updates.open()
            elif command == 'network':
                network.open()
            elif command == 'start' and not running:
                try:
                    controller.start()
                    running = True
                    retry_at = None
                    tray.set_status('CWNG is ready')
                except Exception as error:
                    _message(str(error), 0x10)
            elif command == 'stop' and running:
                if controller.scheduler.active is None:
                    controller.pause()
                    running = False
                    tray.set_status('CWNG is stopped')
                else:
                    choice = _message('Finish the current book before stopping?\n\nYes: finish current book.\nNo: stop safely.\nCancel: keep running.', 3)
                    if choice == 6:
                        controller.finish_current()
                        finishing = 'stop'
                    elif choice == 7:
                        controller.stop_safely()
                        controller.pause()
                        running = False
                        tray.set_status('CWNG is stopped')
            elif command == 'tray-error':
                print('Notification area is unavailable; waiting for Explorer.', file=sys.stderr)
            elif command == 'tray-recovered':
                tray.set_status(displayed if running and displayed else 'CWNG is stopped')
            elif command == 'exit':
                if not running or controller.scheduler.active is None:
                    break
                choice = _message('Finish the current book before exiting?\n\nYes: finish current book.\nNo: stop safely.\nCancel: keep running.', 3)
                if choice == 6:
                    controller.finish_current()
                    finishing = 'exit'
                elif choice == 7:
                    controller.stop_safely()
                    break
    finally:
        try:
            network.close()
        finally:
            try:
                updates.close()
            finally:
                try:
                    tray.close()
                finally:
                    controller.close()
    if recover_requested:
        from windows_native_password import prompt_and_recover
        prompt_and_recover(controller)
    return 0


def main(argv=None):
    with install_gate():
        return _main(argv)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        _message(str(error), 0x10)
        raise
