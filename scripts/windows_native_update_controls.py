"""User-controlled update checks for the native Windows tray.

Checks only the configured fork's feed. Download and installer execution are
user initiated; selected installers are rechecked immediately before launch.
"""
import ctypes
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
import webbrowser

from packaging.version import InvalidVersion, Version
from windows_native_update import fetch_update, verify_installer


_REPOSITORY = 'dapih/Calibre-Web-NextGen'
_INTERVAL = 24 * 60 * 60


def _regular_file(path):
    if path.is_symlink() or not path.is_file():
        return False
    return not (getattr(path.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400)


def load_package_identity(program_root):
    """Read the package's explicit identity; a source checkout has none."""
    path = Path(program_root) / 'native-package.json'
    if not _regular_file(path):
        raise ValueError('The installed Windows package identity is unavailable.')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or set(data) != {'schema', 'repository', 'version', 'revision', 'architecture'}
                or type(data['schema']) is not int or data['schema'] != 1
                or data['repository'] != _REPOSITORY or data['architecture'] != 'x64'
                or type(data['revision']) is not int or data['revision'] < 0
                or not isinstance(data['version'], str)):
            raise ValueError('invalid package identity')
        version = Version(data['version'])
        if version.is_devrelease or version.is_postrelease or version.local is not None:
            raise ValueError('invalid package version')
        return data
    except (OSError, ValueError, InvalidVersion) as error:
        raise ValueError('The installed Windows package identity is invalid.') from error


class UpdateManager:
    """Persist check preferences and perform a single explicitly scoped check."""

    def __init__(self, state, identity, windows_build, *, fetch=fetch_update, clock=time.time):
        self.path = Path(state) / 'native-update.json'
        self.identity = identity
        self.windows_build = windows_build
        self.fetch = fetch
        self.clock = clock
        self._lock = threading.Lock()
        self._checking = threading.Lock()
        self._closed = False
        self._settings = self._read()
        self.last_candidate = None

    def _read(self):
        if not self.path.exists() and not self.path.is_symlink():
            return {'automatic': False, 'channel': 'stable', 'last_attempt': None}
        try:
            if not _regular_file(self.path):
                raise ValueError('unsafe update settings')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or set(data) != {'automatic', 'channel', 'last_attempt'}
                    or type(data['automatic']) is not bool or data['channel'] not in ('stable', 'prerelease')
                    or (data['last_attempt'] is not None and
                        (type(data['last_attempt']) not in (int, float) or not math.isfinite(data['last_attempt'])
                         or data['last_attempt'] < 0))):
                raise ValueError('invalid update settings')
            return data
        except (OSError, ValueError, TypeError) as error:
            raise ValueError('Update settings are invalid; no check was made.') from error

    def _write(self, settings):
        if (self.path.exists() or self.path.is_symlink()) and not _regular_file(self.path):
            raise ValueError('unsafe update settings')
        temporary = self.path.with_name('.native-update-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(settings, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def preferences(self):
        with self._lock:
            return dict(self._settings)

    def set_preferences(self, *, automatic, channel):
        if type(automatic) is not bool or channel not in ('stable', 'prerelease'):
            raise ValueError('invalid update preferences')
        with self._lock:
            if self._closed:
                raise RuntimeError('Update controls are closed.')
            updated = dict(self._settings, automatic=automatic, channel=channel)
            self._write(updated)
            self._settings = updated

    def _check(self, *, automatic):
        if not self._checking.acquire(blocking=False):
            if automatic:
                return False, None
            raise RuntimeError('An update check is already in progress.')
        try:
            with self._lock:
                if self._closed:
                    if automatic:
                        return False, None
                    raise RuntimeError('Update controls are closed.')
                now = self.clock()
                if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
                    raise ValueError('Invalid system time; update checks are paused.')
                last = self._settings['last_attempt']
                if automatic and (not self._settings['automatic'] or
                                  (last is not None and now < last + _INTERVAL)):
                    return False, None
                channel = self._settings['channel']
                updated = dict(self._settings, last_attempt=now)
                # Record the attempt before network I/O, including failure, so
                # an offline server or a restart cannot trigger a request loop.
                self._write(updated)
                self._settings = updated
            result = self.fetch(repository=self.identity['repository'], channel=channel,
                                current_version=self.identity['version'],
                                current_revision=self.identity['revision'], windows_build=self.windows_build)
            with self._lock:
                if (self._settings['channel'] != channel or
                        (automatic and not self._settings['automatic'])):
                    # A settings change while the request was in flight makes
                    # its result stale; never offer the wrong channel.
                    result = None
            self.last_candidate = result
            return True, result
        finally:
            self._checking.release()

    def check_due(self):
        attempted, _ = self._check(automatic=True)
        return attempted

    def check_now(self):
        return self._check(automatic=False)[1]

    def shutdown(self):
        """Fence future settings writes before profile ownership is released."""
        with self._lock:
            self._closed = True


def _message(text, flags=0):
    return ctypes.windll.user32.MessageBoxW(None, text, 'CWNG updates', flags)


def _select_installer():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        return filedialog.askopenfilename(parent=root, title='Select the downloaded CWNG installer',
                                          filetypes=[('Windows installers', '*.exe')])
    finally:
        root.destroy()


def _run_verified_installer(path, candidate):
    """Hold the selected file stable while rechecking its hash and starting it."""
    from windows_m0_inbox import _locked_source

    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != '.exe':
        raise ValueError('Select a Windows installer executable.')
    with _locked_source(path, path.parent, path) as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if not hmac.compare_digest(digest, candidate.sha256):
            raise ValueError('The installer changed or does not match the feed SHA-256.')
        subprocess.Popen([str(path)], close_fds=True)


class UpdateControls:
    """Keep update network and dialog work off the controller's event loop."""

    def __init__(self, state, program_root, *, windows_build=None):
        self.state, self.program_root = Path(state), Path(program_root)
        self.windows_build = windows_build
        self._manager = None
        self._error = None
        self._stop = threading.Event()
        self._dialog = threading.Lock()
        self._thread = None

    def start(self):
        try:
            identity = load_package_identity(self.program_root)
            if self.windows_build is None:
                import sys
                self.windows_build = sys.getwindowsversion().build
            self._manager = UpdateManager(self.state, identity, self.windows_build)
        except (OSError, ValueError) as error:
            self._error = str(error)
            return
        self._thread = threading.Thread(target=self._automatic, name='cwng-update-checks', daemon=True)
        self._thread.start()

    def _automatic(self):
        while not self._stop.is_set():
            try:
                attempted, candidate = self._manager._check(automatic=True)
                if attempted and candidate is not None:
                    # Preserve the notification if a settings dialog is open.
                    # Recheck its channel/opt-in after that dialog closes.
                    while not self._stop.is_set():
                        if self._dialog.acquire(timeout=.2):
                            try:
                                preferences = self._manager.preferences()
                                if (preferences['automatic'] and preferences['channel'] == candidate.channel
                                        and not self._stop.is_set()):
                                    self._show_candidate(candidate)
                            finally:
                                self._dialog.release()
                            break
            except Exception:
                # Offline failures wait for the next scheduled attempt. A
                # manual check reports its error to the user instead.
                pass
            if self._stop.wait(60):
                return

    def _show_candidate(self, candidate):
        choice = _message('A Windows update is available: ' + candidate.version +
                          '\n\nSHA-256: ' + candidate.sha256 +
                          '\n\nYes: open the installer download.\nNo: verify an already downloaded installer.\nCancel: close.'
                          '\n\nA matching hash does not establish publisher identity.',
                          0x3 | 0x20)
        if choice == 6 and not self._stop.is_set():
            webbrowser.open(candidate.url)
        elif choice == 7 and not self._stop.is_set():
            selected = _select_installer()
            if selected and not self._stop.is_set():
                if verify_installer(selected, candidate):
                    choice = _message('The selected installer matches the feed SHA-256. '
                                      'This does not establish publisher identity.\n\nRun the installer now?',
                                      0x4 | 0x20)
                    if choice == 6 and not self._stop.is_set():
                        try:
                            _run_verified_installer(Path(selected), candidate)
                        except (OSError, ValueError) as error:
                            if not self._stop.is_set():
                                _message('Could not start installer: ' + str(error), 0x10)
                else:
                    _message('The installer SHA-256 does not match. Do not run this file.', 0x10)

    def _open_dialog(self):
        try:
            if self._manager is None:
                _message(self._error or 'Windows update information is unavailable.', 0x10)
                return
            choice = _message('Check for updates now?\n\nYes: check now.\nNo: change update settings.\nCancel: close.',
                              0x3 | 0x20)
            if self._stop.is_set():
                return
            if choice == 6:
                candidate = self._manager.check_now()
                if self._stop.is_set():
                    return
                if candidate is None:
                    _message('CWNG is up to date.')
                else:
                    self._show_candidate(candidate)
            elif choice == 7:
                enabled = _message('Enable automatic update checks?\n\nYes: check at most once a day.\nNo: disable automatic checks.\nCancel: keep current settings.',
                                   0x3 | 0x20)
                if enabled not in (6, 7) or self._stop.is_set():
                    return
                channel = _message('Include prerelease updates?\n\nYes: prerelease channel.\nNo: stable channel.\nCancel: keep current settings.',
                                   0x3 | 0x20)
                if channel in (6, 7) and not self._stop.is_set():
                    self._manager.set_preferences(automatic=enabled == 6,
                                                  channel='prerelease' if channel == 6 else 'stable')
                    _message('Update settings saved.')
        except Exception as error:
            if not self._stop.is_set():
                _message('Update check failed: ' + str(error), 0x10)
        finally:
            self._dialog.release()

    def open(self):
        if not self._stop.is_set() and self._dialog.acquire(blocking=False):
            threading.Thread(target=self._open_dialog, name='cwng-update-dialog', daemon=True).start()

    def close(self):
        self._stop.set()
        if self._manager is not None:
            self._manager.shutdown()
