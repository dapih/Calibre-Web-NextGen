"""Per-user M0 coordinator activation over its authenticated local pipe.

The profile directory must already be private to the installing user. This is
an experimental launcher component, not an HTTP readiness check.
"""
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from windows_m0_control import ControlPipe, request
from windows_m0_coordinator import Coordinator
from windows_m0_supervisor import RestartBudget


class ControlRecoveryRequired(RuntimeError):
    """The control service exhausted its automatic restart budget."""


def _profile(state_dir):
    state = Path(state_dir).resolve(strict=True)
    if not state.is_dir() or not state.drive or state.drive.startswith('\\\\'):
        raise ValueError('an existing local profile directory is required')
    return state


def _pipe_name(state):
    identity = os.path.normcase(str(state)).encode('utf-8')
    return 'CWNG-M0-' + hashlib.sha256(identity).hexdigest()[:32]


class LocalOwner:
    """Hold profile ownership and answer authenticated duplicate launches."""
    def __init__(self, helper, state_dir):
        self.state = _profile(state_dir)
        self.owner = Coordinator(helper, self.state)
        self.pipe = None
        self.thread = None
        self.stopping = threading.Event()
        self.control_error = None
        self.key_path = self.state / 'control.key'
        temporary = self.state / ('.control-' + uuid.uuid4().hex)
        try:
            self.restart_budget = RestartBudget(ledger=self.owner.ledger, role='control')
            if self.restart_budget.paused:
                raise ControlRecoveryRequired('control role is paused after repeated failures; recovery is required')
            key = secrets.token_bytes(32)
            self.pipe = ControlPipe(_pipe_name(self.state), key, timeout=0.5)
            with temporary.open('xb') as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            # The previous owner cannot still hold the profile lock. Replace a
            # stale key only while this process owns it, after the pipe exists.
            os.replace(temporary, self.key_path)
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            if self.pipe:
                self.pipe.close()
            self.owner.close()
            raise

    def _serve(self):
        while not self.stopping.is_set():
            try:
                self.owner.serve_control_once(self.pipe)
            except (TimeoutError, ConnectionError, PermissionError, ValueError):
                # An incomplete or invalid client never changes owner state.
                continue
            except OSError as error:
                if getattr(error, 'winerror', None) in (109, 232, 233, 535):
                    continue
                self.control_error = str(error)
                delay = self.restart_budget.failed()
                if delay is None or self.stopping.wait(delay):
                    return
            except Exception as error:
                self.control_error = str(error)
                delay = self.restart_budget.failed()
                if delay is None or self.stopping.wait(delay):
                    return

    def close(self):
        if self.owner is None:
            return
        self.stopping.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError('control thread did not stop; ownership retained')
        self.pipe.close()
        try:
            self.key_path.unlink(missing_ok=True)
        finally:
            self.owner.close()
            self.owner = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_or_activate(helper, state_dir, *, timeout=5):
    """Return (owner, None) for first launch or (None, status) for a duplicate."""
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ValueError('invalid activation timeout')
    state = _profile(state_dir)
    try:
        return LocalOwner(helper, state), None
    except OSError as error:
        if getattr(error, 'winerror', None) != 33:  # ERROR_LOCK_VIOLATION
            raise
    deadline = time.monotonic() + timeout
    while True:
        try:
            key = (state / 'control.key').read_bytes()
            if len(key) != 32:
                raise ValueError('invalid control key')
            status = request(_pipe_name(state), key, {'operation': 'status'}, timeout=min(1, timeout))
            if set(status) != {'active', 'running', 'review'}:
                raise ValueError('invalid controller status')
            return None, status
        except (OSError, ConnectionError, TimeoutError, PermissionError, ValueError):
            if time.monotonic() >= deadline:
                raise TimeoutError('existing controller did not authenticate')
            time.sleep(.05)
