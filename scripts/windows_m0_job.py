"""M0 Windows Job Object adapter; not a complete controller or recovery fence.

The caller must supply verified private paths and an explicit child environment.
Closing the job terminates its processes; a process handle remains the caller's
responsibility and does not itself own the job.
"""

import ctypes
import os
from pathlib import Path
import re
import subprocess
import threading
from ctypes import wintypes


def _check(result):
    if result:
        raise ctypes.WinError(result)


def generation_name(generation):
    if not isinstance(generation, str) or not re.fullmatch(r"nj1-[0-9a-f]{32}", generation):
        raise ValueError("unknown or invalid job generation")
    return "Global\\CWNG-M0-" + generation


def generation_exists(helper_path, generation):
    """Return false only when the named job is absent; errors fail closed."""
    if os.name != "nt":
        raise OSError("Windows Job Objects require Windows")
    api = ctypes.WinDLL(str(_absolute_local(helper_path)), use_last_error=True)
    api.cwng_job_named_exists.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.BOOL)]
    api.cwng_job_named_exists.restype = wintypes.DWORD
    exists = wintypes.BOOL()
    _check(api.cwng_job_named_exists(generation_name(generation), ctypes.byref(exists)))
    return bool(exists.value)


def current_process_in_generation(helper_path, generation):
    """Check the current process against a specific named job; fail closed."""
    if os.name != "nt":
        raise OSError("Windows Job Objects require Windows")
    api = ctypes.WinDLL(str(_absolute_local(helper_path)), use_last_error=True)
    api.cwng_job_contains_current.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.BOOL)]
    api.cwng_job_contains_current.restype = wintypes.DWORD
    member = wintypes.BOOL()
    _check(api.cwng_job_contains_current(generation_name(generation), ctypes.byref(member)))
    return bool(member.value)


def _absolute_local(path):
    path = Path(path)
    if not path.is_absolute() or not path.drive or path.drive.startswith("\\\\"):
        raise ValueError("an absolute local drive path is required")
    path = path.resolve(strict=True)
    if not path.is_absolute() or not path.drive or path.drive.startswith("\\\\"):
        raise ValueError("resolved path must remain on a local drive")
    return path


def _environment_block(environment):
    """Build a Windows UTF-16 environment block without inheriting the host."""
    pairs = []
    seen = set()
    for name, value in environment.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("environment keys and values must be strings")
        if not name or "=" in name or "\0" in name or "\0" in value:
            raise ValueError("invalid Windows environment entry")
        folded = name.upper()
        if folded in seen:
            raise ValueError("duplicate environment key")
        seen.add(folded)
        pairs.append((folded, f"{name}={value}"))
    # Empty environments still require the double-NUL terminator.
    return ctypes.create_unicode_buffer("\0".join(value for _, value in sorted(pairs)) + "\0\0")


class Process:
    """Own a process handle returned by Job.spawn; close it when finished."""

    def __init__(self, handle, pid, kernel):
        self._handle = handle
        self.pid = pid
        self._kernel = kernel

    def wait(self, timeout_ms=0xFFFFFFFF):
        if not self._handle:
            raise RuntimeError("process handle is closed")
        if not 0 <= timeout_ms <= 0xFFFFFFFF:
            raise ValueError("timeout must be a Windows millisecond timeout")
        result = self._kernel.WaitForSingleObject(self._handle, timeout_ms)
        if result == 0x102:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self._kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self):
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ProfileLock:
    """Hold an OS lock in an already-created, access-restricted profile directory."""

    def __init__(self, helper_path, lock_path):
        if os.name != "nt":
            raise OSError("the native profile lock requires Windows")
        helper_path = _absolute_local(helper_path)
        if not helper_path.is_file():
            raise ValueError("the native helper must be a file")
        lock_path = Path(lock_path)
        if not lock_path.name or lock_path.name in (".", ".."):
            raise ValueError("profile lock must name a file")
        directory = _absolute_local(lock_path.parent)
        if not directory.is_dir():
            raise ValueError("profile lock directory must exist")
        self._api = ctypes.WinDLL(str(helper_path), use_last_error=True)
        self._api.cwng_profile_lock.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE)]
        self._api.cwng_profile_lock.restype = wintypes.DWORD
        self._api.cwng_handle_close.argtypes = [wintypes.HANDLE]
        self._api.cwng_handle_close.restype = wintypes.DWORD
        self._guard = threading.Lock()
        self._handle = wintypes.HANDLE()
        _check(self._api.cwng_profile_lock(str(directory / lock_path.name), ctypes.byref(self._handle)))

    def close(self):
        with self._guard:
            if self._handle:
                _check(self._api.cwng_handle_close(self._handle))
                self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Job:
    """Create workers atomically inside a kill-on-close job on Windows."""

    def __init__(self, helper_path, *, generation=None):
        if os.name != "nt":
            raise OSError("Windows Job Objects require Windows")
        helper_path = _absolute_local(helper_path)
        if not helper_path.is_file():
            raise ValueError("the native helper must be a file")
        self._api = ctypes.WinDLL(str(helper_path), use_last_error=True)
        self._api.cwng_job_create.argtypes = [ctypes.POINTER(wintypes.HANDLE)]
        self._api.cwng_job_create.restype = wintypes.DWORD
        self._api.cwng_job_create_named.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE)]
        self._api.cwng_job_create_named.restype = wintypes.DWORD
        self._api.cwng_job_spawn.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.DWORD),
        ]
        self._api.cwng_job_spawn.restype = wintypes.DWORD
        self._api.cwng_job_active_count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._api.cwng_job_active_count.restype = wintypes.DWORD
        self._api.cwng_handle_close.argtypes = [wintypes.HANDLE]
        self._api.cwng_handle_close.restype = wintypes.DWORD
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._lock = threading.RLock()
        self._handle = wintypes.HANDLE()
        if generation is None:
            _check(self._api.cwng_job_create(ctypes.byref(self._handle)))
        else:
            _check(self._api.cwng_job_create_named(generation_name(generation), ctypes.byref(self._handle)))

    def spawn(self, executable, arguments, *, cwd, environment):
        executable, cwd = _absolute_local(executable), _absolute_local(cwd)
        if not executable.is_file() or not cwd.is_dir():
            raise ValueError("executable must be a file and cwd must be a directory")
        args = [os.fspath(executable), *(os.fspath(arg) for arg in arguments)]
        if any("\0" in arg for arg in args):
            raise ValueError("process arguments cannot contain NUL")
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        env = _environment_block(environment)
        child, pid = wintypes.HANDLE(), wintypes.DWORD()
        with self._lock:
            if not self._handle:
                raise RuntimeError("job is closed")
            _check(self._api.cwng_job_spawn(
                self._handle, str(executable), command, str(cwd), env,
                ctypes.byref(child), ctypes.byref(pid),
            ))
        return Process(child, pid.value, self._kernel)

    def contains_process(self, pid):
        """Check that a live process belongs to this specific owned job."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        kernel = self._kernel
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        with self._lock:
            if not self._handle:
                raise RuntimeError('job is closed')
            process = kernel.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                member = wintypes.BOOL()
                return bool(kernel.IsProcessInJob(process, self._handle, ctypes.byref(member)) and member.value)
            finally:
                kernel.CloseHandle(process)

    def active_count(self):
        with self._lock:
            if not self._handle:
                raise RuntimeError("job is closed")
            count = wintypes.DWORD()
            _check(self._api.cwng_job_active_count(self._handle, ctypes.byref(count)))
            return count.value

    def close(self):
        with self._lock:
            if self._handle:
                _check(self._api.cwng_handle_close(self._handle))
                self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
