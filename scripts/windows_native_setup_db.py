"""Prepare fresh application databases under exclusive native profile ownership.

This is a setup building block, not setup completion: the caller must validate
all storage and copy/new-library choices before publishing any setup marker.
The library is only named in settings; this module never opens or changes it.
"""
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path

from windows_m0_inbox import _flush_directory, _volume_identity
from windows_m0_job import ProfileLock
from windows_native_profile import require_private_directory


class _SetupOwnership:
    def __init__(self, state, helper):
        self.state, self.helper, self.active = state, helper, True


def require_setup_ownership(ownership, state, helper):
    if (not isinstance(ownership, _SetupOwnership) or not ownership.active or ownership.state != state
            or ownership.helper != Path(helper).resolve(strict=True)):
        raise ValueError('active ownership for this profile is required')


@contextmanager
def setup_ownership(state, helper):
    """Hold controller ownership throughout a setup transaction.

    A setup orchestrator can retain this context until it has validated and
    durably published its setup-completion record. It must fence old workers
    before mutating an existing profile; this helper supports fresh setup only.
    """
    state = require_private_directory(state)
    helper = Path(helper).resolve(strict=True)
    _volume_identity(state)
    from windows_native_controller import NativeController
    NativeController.require_no_maintenance_journal(state)
    with ProfileLock(helper, state / 'controller.lock'):
        NativeController.require_no_maintenance_journal(state)
        ownership = _SetupOwnership(state, helper)
        try:
            yield ownership
        finally:
            ownership.active = False


def prepare_databases(*, state, helper, library, username, password, ownership=None):
    """Create fresh app.db and cwa.db, with no shared default administrator.

    Must run in an isolated native setup process with CWA_DB_PATH and
    CALIBRE_DBPATH set to the private config directory before Python starts.
    A failed partial initialization must be reviewed; existing databases are
    never overwritten or resumed by this function.
    """
    state = require_private_directory(state)
    config = require_private_directory(state / 'config')
    if config.parent != state:
        raise ValueError('configuration must be directly inside private state')
    library = Path(library).resolve(strict=True)
    if not library.is_dir() or not (library / 'metadata.db').is_file():
        raise ValueError('an existing initialized library is required')
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > 64 or username.casefold() == 'guest'):
        raise ValueError('invalid administrator name')
    if not isinstance(password, str) or len(password) < 8 or password == 'admin123':
        raise ValueError('a user-selected administrator password is required')
    if any(Path(os.environ.get(key, '')).resolve() != config for key in ('CWA_DB_PATH', 'CALIBRE_DBPATH')):
        raise ValueError('setup process must use its private configuration')
    _volume_identity(state)
    _volume_identity(library)
    scope = setup_ownership(state, helper) if ownership is None else nullcontext(ownership)
    with scope as owner:
        require_setup_ownership(owner, state, helper)
        protected = (config / 'app.db', config / 'cwa.db', config / '.key', state / 'setup-complete.json',
                     state / 'runtime-ledger.sqlite', state / 'runtime-ledger.sqlite-wal',
                     state / 'runtime-ledger.sqlite-shm', state / 'web-generation')
        if any(path.exists() or path.is_symlink() for path in protected):
            raise FileExistsError('existing or incomplete setup requires review')
        # Import the actual app modules only after ownership and fresh-state
        # checks. Override its normal default-admin creator before init_db.
        from cps import constants, ub, config_sql
        from werkzeug.security import generate_password_hash

        def create_selected_admin(session):
            user = ub.User()
            user.name = username
            user.email = 'admin@example.org'
            user.role = constants.ADMIN_USER_ROLES
            user.sidebar_view = constants.ADMIN_USER_SIDEBAR
            user.password = generate_password_hash(password)
            session.add(user)
            session.commit()
            from cps import magic_shelf
            magic_shelf.create_system_magic_shelves(user.id)

        ub.create_admin_user = create_selected_admin
        ub.init_db(str(config / 'app.db'))
        key, error = config_sql.get_encryption_key(str(config))
        if error or not key or not (config / '.key').is_file():
            raise RuntimeError('could not create private encryption key')
        config_sql.load_configuration(ub.session, key)
        settings = ub.session.query(config_sql._Settings).one()
        settings.config_calibre_dir = str(library)
        settings.config_calibre_split = False
        ub.session.commit()
        from cwa_db import CWA_DB
        cwa = CWA_DB()
        cwa.con.close()
        ub.session.close()
        _flush_directory(config)
        return config / 'app.db', config / 'cwa.db'
