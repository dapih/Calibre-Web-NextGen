# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free Calibre addressing policy shared by app and standalone tools."""
import ipaddress
import hashlib
import os
import time
import json
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path
import stat

LibraryTarget = namedtuple("LibraryTarget", "args stdin")


def _short_windows_path(path):
    import ctypes
    from ctypes import wintypes

    short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    short_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = short_path(os.fspath(path), buffer, len(buffer))
    if 0 < size < len(buffer):
        return buffer.value
    return None


def local_library_path(library_dir):
    """Give Windows Calibre a short path, retaining the real path in config.

    Calibre budgets library roots at 75 characters even on long-path-aware
    Windows. The junction is derived cache state, not a copy of the library.
    Never replace an existing cache entry that points somewhere unexpected.
    """
    value = os.fspath(library_dir)
    if not value or os.name != "nt" or value.lower().startswith(("http://", "https://")):
        return value
    actual = os.path.abspath(value)
    if len(actual) <= 75:
        return value
    if not os.path.isdir(actual):
        raise FileNotFoundError("Calibre library directory does not exist: " + actual)
    # A DOS short name addresses the real directory directly. Some Windows
    # installations refuse to create child directories through a junction,
    # including Calibre's .calnotes and author directories.
    short = _short_windows_path(actual)
    if short and len(short) <= 75 and os.path.samefile(short, actual):
        return short

    import _winapi
    cache_root = os.environ.get("LOCALAPPDATA")
    if not cache_root:
        raise RuntimeError("LOCALAPPDATA is required for the Windows Calibre path cache")
    cache = Path(cache_root) / "CWNG-Paths"
    cache.mkdir(parents=True, exist_ok=True)
    if cache.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise RuntimeError("Calibre path cache must be a regular directory")
    key = hashlib.sha256(os.path.normcase(actual).encode("utf-16-le")).hexdigest()[:12]
    alias = cache / ("l" + key)
    if not os.path.lexists(alias):
        try:
            _winapi.CreateJunction(actual, str(alias))
        except FileExistsError:
            pass  # Another worker may have created this exact alias.
    if (alias.lstat().st_reparse_tag != stat.IO_REPARSE_TAG_MOUNT_POINT
            or not os.path.samefile(alias, actual)):
        raise RuntimeError("Calibre path cache entry has an unexpected target: " + str(alias))
    result = str(alias)
    if len(result) > 75:
        # A long Windows account name may itself need its existing short form.
        result = _short_windows_path(alias)
        if not result or len(result) > 75:
            raise RuntimeError("Windows user cache path is too long for Calibre")
    return result


def library_id(library_dir):
    """calibre-server's id for a library: folder name, spaces as ``_``.

    Mirrors Calibre's ``library_id_from_path``; both app and scripts use
    this implementation.
    """
    return os.path.basename(local_library_path(library_dir).rstrip("/\\")).replace(" ", "_")


def connect_host(listen):
    """Where this host reaches the server; shared by app and scripts."""
    listen = (listen or "").strip()
    if listen in ("", "0.0.0.0"):
        return "127.0.0.1"
    if listen == "::":
        return "::1"
    try:
        return str(ipaddress.ip_address(listen))
    except ValueError:
        return "127.0.0.1"


def server_target(library_dir, enabled, port, listen, anonymous_writes, username, password,
                  is_answering, announce_fallback, owner_busy=None):
    """Shared app/script routing policy; an empty target means use the local path.

    Only the explicit anonymous choice can omit credentials. This policy is
    separate from loading Flask config or encrypted app.db values so both
    consumers use the same decision and argument boundaries.
    """
    if not enabled or not library_dir:
        path_is_available(owner_busy)
        return LibraryTarget([], None)
    if not anonymous_writes and not (username and password):
        path_is_available(owner_busy)
        announce_fallback("no content server credentials are configured")
        return LibraryTarget([], None)
    host = connect_host(listen)
    answering = is_answering(host, port)
    if answering and owner_busy is not None and not owner_busy():
        raise RuntimeError("Configured content-server port answers without managed ownership")
    if not answering and owner_busy is not None:
        deadline = time.monotonic() + 30
        while owner_busy():
            if time.monotonic() >= deadline:
                raise TimeoutError("Managed Calibre server owns the library but is not ready")
            time.sleep(0.1)
            answering = is_answering(host, port)
            if answering:
                break
    if not answering:
        path_is_available(owner_busy)
        announce_fallback("it is not answering on {} port {}".format(host, port))
        return LibraryTarget([], None)
    url_host = "[{}]".format(host) if ":" in host else host
    args = ["--with-library", "http://{}:{}/#{}".format(url_host, port, library_id(library_dir))]
    if anonymous_writes:
        return LibraryTarget(args, None)
    args += ["--username", username, "--password", "<stdin>"]
    return LibraryTarget(args, password + "\n")


def path_is_available(owner_busy):
    if owner_busy is not None and owner_busy():
        raise TimeoutError("Managed Calibre server still owns the library; retry after reconciliation")


def server_ready(host, port, library_dir, timeout=0.5):
    """Recognize Calibre without sending credentials to an arbitrary listener."""
    host = "[{}]".format(host) if ":" in host else host
    request = urllib.request.Request("http://{}:{}/ajax/library-info".format(host, int(port)))
    # Local service probes must bypass the deployment's outbound proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            if not response.headers.get("Server", "").startswith("calibre "):
                return False
            data = json.loads(response.read(65537))
            libraries = data.get("library_map") if isinstance(data, dict) else None
            return isinstance(libraries, dict) and library_id(library_dir) in libraries
    except urllib.error.HTTPError as error:
        try:
            server = error.headers.get("Server", "")
            # Calibre 9.11 omits Server on its real authentication error.
            # Match its challenge without offering credentials; routing also
            # requires the independent managed-process owner lock.
            return (error.code == 401
                    and (not server or server.startswith("calibre "))
                    and error.headers.get("WWW-Authenticate", "").startswith('Digest realm="calibre",'))
        finally:
            error.close()
    except (OSError, ValueError, TypeError):
        return False


def calibredb_command(command, target):
    """Preserve CLI arguments while preventing getpass from selecting a TTY."""
    command = list(command)
    for index, argument in enumerate(command):
        if not isinstance(argument, str):
            continue
        if argument in ("--with-library", "--library-path") and index + 1 < len(command):
            command[index + 1] = local_library_path(command[index + 1])
        elif argument.startswith(("--with-library=", "--library-path=")):
            option, value = argument.split("=", 1)
            command[index] = option + "=" + local_library_path(value)
    if target.stdin is None:
        return command
    binary = Path(command[0])
    debug = binary.with_name("calibre-debug.exe" if binary.suffix.lower() == ".exe" else "calibre-debug")
    helper = Path(__file__).with_name("calibredb_pipe.py")
    return [str(debug), "-e", str(helper), "--", *command[1:]]
