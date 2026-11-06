# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process-shared advisory lock for metadata.db writes.

Two actors write to /calibre-library/metadata.db: the Flask app (Edit
Book / cover-picker commits via SQLAlchemy) and the ingest_processor
subprocess (calibredb add). When the library lives on a filesystem
with weak POSIX advisory-lock support — mergerfs, SMB, NFS, Docker
Desktop's virtiofs — SQLite's own fcntl-based locking can silently
fail, surfacing as ``apsw.BusyError: database is locked`` from
calibredb and stranding the import in /processed_books/failed.

This module provides a ``metadata_db_write_lock`` context manager that
both actors acquire before touching metadata.db. The lock lives in
/config (a local Docker volume) where fcntl flock always works, so
coordination is reliable even when the library volume is not.

The lock is intentionally exclusive — concurrency at this layer is
already serial because there's only one inotify watcher, one ingest
worker, and the web app's metadata.db writes are infrequent. The lock
costs only the duration of a single calibredb add or a single
SQLAlchemy commit.

Related: fork issue #192, upstream CWA #1082.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import sys
import threading
import time
from contextvars import ContextVar
from contextlib import closing, contextmanager
from pathlib import Path
from types import ModuleType

# This file is also loaded standalone by calibre_server_guard. Store one
# context variable in the interpreter's module registry so both loaders see
# the same held lock, while each thread/greenlet/async task stays separate.
_new_native_state = ModuleType('_cwng_native_writer_lock_state')
_new_native_state.held = ContextVar('_cwng_native_writer_lock_held', default=None)
_native_state = sys.modules.setdefault(_new_native_state.__name__, _new_native_state)


def _native_execution_identity():
    import asyncio
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    try:
        from greenlet import getcurrent
        greenlet = getcurrent()
    except ImportError:
        greenlet = None
    return os.getpid(), threading.get_ident(), greenlet, task

try:
    from .parallel import cooperative_sleep
except ImportError:  # pragma: no cover — exercised by the standalone loader below
    # This file is also loaded as a STANDALONE module, with no package context:
    # tests/unit/test_metadata_db_write_coordination.py does it on purpose, via
    # spec_from_file_location, to get at the lock without paying for cps/'s
    # Flask init. Production never takes this branch — both real importers
    # (cps.editbooks / cps.api.browse, and ingest_processor's lazy
    # `from cps.services.calibre_db_lock import …`) have the package.
    #
    # Fall back to the exact primitive parallel.cooperative_sleep wraps, so the
    # standalone module behaves the same rather than silently reverting to the
    # blocking sleep this module exists to avoid.
    try:
        from gevent import sleep as cooperative_sleep
    except ImportError:
        cooperative_sleep = time.sleep

try:
    import fcntl

    HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows
    fcntl = None
    HAS_FCNTL = False


DEFAULT_LOCK_BASENAME = ".cwa-metadata-write.lock"
DEFAULT_TIMEOUT_SECONDS = 120.0


def _resolved_config_dir() -> str:
    """Mirror the dependency-free config resolver used by scripts.app_paths."""
    override = (os.environ.get("CALIBRE_DBPATH") or "").strip()
    if override:
        configured = Path(override)
        return str(configured.parent if configured.suffix == ".db" else configured)

    cps_dir = Path(__file__).resolve().parents[1]
    if (cps_dir / ".HOMEDIR").is_file():
        return str(Path(os.path.expanduser("~")) / ".calibre-web-automated")
    return str(cps_dir.parent)


def _resolve_lock_path(lock_dir: str | None) -> str:
    base = (
        lock_dir
        or os.environ.get("CWA_METADATA_LOCK_DIR")
        or _resolved_config_dir()
    )
    return os.path.join(base, DEFAULT_LOCK_BASENAME)


@contextmanager
def _native_controller_gate(lock_files, lock_path, timeout, poll_interval):
    """Coordinate native writers with the controller-owned worker lease."""
    if os.name != "nt" or os.environ.get("CWNG_NATIVE_MODE") != "1":
        yield
        return
    if lock_files.msvcrt is None:
        raise RuntimeError("native writer locking is unavailable")
    if os.environ.get("CWNG_NATIVE_WRITER_GATE") == "1":
        # Only a descendant of the named controller job may use its lease.
        import importlib.util
        script = Path(__file__).resolve().parents[2] / "scripts" / "windows_m0_job.py"
        spec = importlib.util.spec_from_file_location("_cwng_native_job_gate", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            gate_dir = Path(os.environ["CWNG_NATIVE_GATE_DIR"]).resolve(strict=True)
            same_dir = gate_dir == Path(lock_path).parent.resolve(strict=True)
            member = module.current_process_in_generation(os.environ["CWNG_NATIVE_HELPER"],
                                                          os.environ["CWNG_NATIVE_JOB_GENERATION"])
        except (KeyError, OSError, ValueError) as error:
            raise RuntimeError("native controller lease cannot be verified") from error
        if not same_dir or not member:
            raise RuntimeError("native controller lease is not owned by this worker")
        yield
        return
    fd = lock_files.open_lock(str(Path(lock_path).parent / ".cwng-native-writer.lock"))
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while not lock_files.acquire(fd, blocking=False):
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the native controller writer lease")
            cooperative_sleep(poll_interval)
        acquired = True
        # A controller crash releases its OS lock before the old job has
        # necessarily disappeared. Consult durable intent while still holding
        # the gate so no new controller claim can race this admission.
        try:
            state = Path(os.environ["CWNG_NATIVE_STATE_DIR"]).resolve(strict=True)
            ledger = state / "runtime-ledger.sqlite"
            if not state.is_dir() or not state.drive or state.drive.startswith("\\\\"):
                raise ValueError("native state directory must be local")
            with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
                if (connection.execute("PRAGMA application_id").fetchone()[0] != 0x43574E47 or
                        connection.execute("PRAGMA user_version").fetchone()[0] != 1):
                    raise ValueError("unknown native ledger")
                unresolved = connection.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('running', 'review') LIMIT 1"
                ).fetchone()
        except (KeyError, OSError, ValueError, sqlite3.Error) as error:
            raise RuntimeError("native writer recovery state cannot be verified") from error
        if unresolved:
            raise RuntimeError("native writer is paused pending recovery")
        yield
    finally:
        if acquired:
            lock_files.release(fd)
        os.close(fd)


@contextmanager
def metadata_db_write_lock(
    lock_dir: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = 0.1,
):
    """Acquire an exclusive process-shared lock for metadata.db writes.

    Parameters
    ----------
    lock_dir
        Directory to place the lock file in. Defaults to the resolved config
        directory (``/config`` in the image). Override via the
        ``CWA_METADATA_LOCK_DIR`` env var or for tests.
    timeout
        Seconds to wait for the lock before raising ``TimeoutError``.
        Default 120s — long enough to ride out the kindle-epub-fixer
        on a 40MB book on slow storage.
    poll_interval
        Seconds between non-blocking flock attempts. Lower is more
        responsive but burns more CPU. Default 0.1s is a fine balance.
    """
    try:
        from . import file_lock as lock_files
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_cwng_metadata_file_lock", Path(__file__).with_name("file_lock.py"))
        lock_files = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lock_files)
    windows_locks = None if HAS_FCNTL else lock_files

    lock_path = _resolve_lock_path(lock_dir)

    native = os.name == 'nt' and os.environ.get('CWNG_NATIVE_MODE') == '1'
    identity = _native_execution_identity() if native else None
    canonical = os.path.normcase(os.path.realpath(lock_path)) if native else None
    held = _native_state.held.get() if native else None
    if held and held[1] == identity:
        if held[0] != canonical:
            raise RuntimeError('cannot nest native writer locks for different directories')
        yield held[2]
        return

    with _native_controller_gate(lock_files, lock_path, timeout, poll_interval):
        # The directory must exist. We deliberately do NOT create the lock
        # directory on demand — that would mask a misconfiguration (e.g.
        # configured directory not mounted). If the directory is missing, the
        # ENOENT-from-open below surfaces clearly.
        fd = lock_files.open_lock(lock_path, mode=0o644)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if windows_locks is None:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    elif not windows_locks.acquire(fd, blocking=False):
                        raise BlockingIOError(errno.EAGAIN, "metadata writer owns the lock")
                    break
                except OSError as e:
                    if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out after {timeout:.1f}s waiting for "
                            f"metadata.db write lock at {lock_path}. Another "
                            f"writer (likely calibredb during ingest) is "
                            f"holding it. If this repeats, check that the "
                            f"ingest_processor isn't stuck."
                        ) from e
                    # Cooperative: this poll loop runs on a request greenlet, and
                    # it has no other yield point. A stdlib sleep here parks the
                    # one OS thread every greenlet shares, so the whole app stops
                    # answering for as long as the other writer holds the lock —
                    # up to `timeout` (120s) while ingest runs a calibredb add.
                    cooperative_sleep(poll_interval)

            try:
                # Record holder PID for diagnostics. Best-effort.
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
                except OSError:
                    pass
                token = _native_state.held.set((canonical, identity, fd)) if native else None
                try:
                    yield fd
                finally:
                    if token is not None:
                        _native_state.held.reset(token)
            finally:
                try:
                    if windows_locks is None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    else:
                        windows_locks.release(fd)
                except OSError:
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
