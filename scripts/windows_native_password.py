"""Local administrator password recovery while the native owner is stopped."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from werkzeug.security import generate_password_hash


@contextmanager
def _recovery_owner(controller):
    # Import lazily so the launcher's recovery action can call this module.
    from windows_native_launcher import _offline_recovery_owner
    from windows_native_profile import require_private_directory

    require_private_directory(controller.state)
    with _offline_recovery_owner(controller) as owner:
        require_private_directory(controller.state)
        yield owner


def _verify_storage(controller):
    from windows_native_launcher import _verify_recovery_storage
    return _verify_recovery_storage(controller)


def _pause_lan(state):
    # The network setting must be durable before a password is changed.
    from windows_native_network import pause_for_recovery
    pause_for_recovery(state)


def recover_admin_password(controller, username, password):
    """Reset one administrator locally; preserve books, other users and pairings.

    An exclusive native owner fences the old web and worker jobs. Existing web
    sessions for this administrator are invalidated in the same SQLite commit.
    Application passwords and device pairings are intentionally preserved.
    """
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > 64 or username.casefold() == 'guest'):
        raise ValueError('Enter an existing administrator name.')
    if not isinstance(password, str) or len(password) < 8 or password == 'admin123':
        raise ValueError('Choose a password of at least eight characters, excluding the default password.')
    password_hash = generate_password_hash(password)
    with _recovery_owner(controller):
        _verify_storage(controller)
        app = Path(controller.environment['CWA_APP_DB_PATH']).resolve(strict=True)
        if not app.is_file() or not app.is_relative_to(Path(controller.state).resolve(strict=True)):
            raise ValueError('The private application database is unavailable.')
        with closing(sqlite3.connect(app.as_uri() + '?mode=rw', uri=True, isolation_level=None)) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            try:
                rows = db.execute('SELECT id FROM user WHERE name=? AND (role & 1) = 1', (username,)).fetchall()
                if len(rows) != 1:
                    raise ValueError('That administrator account was not found.')
                _pause_lan(controller.state)
                user_id = rows[0][0]
                db.execute('UPDATE user SET password=? WHERE id=?', (password_hash, user_id))
                db.execute('DELETE FROM user_session WHERE user_id=?', (user_id,))
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
    return ('Administrator password changed. Home-network access remains paused until explicitly re-enabled. '
            'Sign in again in the browser; saved device pairings and app passwords remain.')


def prompt_and_recover(controller):
    """Prompt locally without passing credentials in arguments, URLs or logs."""
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        username = simpledialog.askstring('Recover administrator', 'Administrator name:', parent=root)
        if username is None:
            return False
        password = simpledialog.askstring('Recover administrator', 'New password:', parent=root, show='*')
        if password is None:
            return False
        confirmation = simpledialog.askstring('Recover administrator', 'Confirm new password:', parent=root, show='*')
        if confirmation is None:
            return False
        if password != confirmation:
            raise ValueError('The passwords do not match.')
        message = recover_admin_password(controller, username, password)
        messagebox.showinfo('Administrator recovery', message, parent=root)
        return True
    finally:
        root.destroy()
