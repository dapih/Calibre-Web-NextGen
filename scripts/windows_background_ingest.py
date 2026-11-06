"""A small Windows polling adapter for the existing ingest processor."""
import ctypes
from ctypes import wintypes
from contextlib import ExitStack
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from watch_fallback import iter_files
from windows_m0_inbox import _kernel, _FileInformation, _INVALID
from windows_source_library import configured_library


_FORMATS = set('epub mobi azw3 azw azw4 pdf txt rtf cbz cbr cb7 cbc fb2 fbz docx html htmlz lit lrf odt prc pdb pml rb snb tcr txtz kepub m4b m4a mp4 acsm lcpl chm djvu kfx kfx-zip'.split())
_set_file_information = _kernel.SetFileInformationByHandle
_set_file_information.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_set_file_information.restype = wintypes.BOOL


def _open_source(path, inbox):
    """Deny concurrent writes/deletion and retain a handle able to delete."""
    handle = _kernel.CreateFileW(str(path), 0x80000000 | 0x00010000, 1, None, 3, 0x00200000, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileInformation()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10):
            raise ValueError('input is not a regular file')
        final = ctypes.create_unicode_buffer(32768)
        length = _kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
        if not length or length >= len(final):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = final.value
        if actual.startswith('\\\\?\\UNC\\'):
            raise ValueError('network inbox paths are not supported')
        if actual.startswith('\\\\?\\'):
            actual = actual[4:]
        if Path(actual).resolve(strict=True) != path.resolve(strict=True) or not path.resolve(strict=True).is_relative_to(inbox):
            raise ValueError('input path changed')
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        _kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'rb')


class BackgroundIngest:
    def __init__(self, inbox, state, *, interval=5, environment=None, writer_lock=None):
        self.inbox = Path(inbox).resolve(strict=True)
        self.state = Path(state).resolve(strict=True)
        if not self.inbox.is_dir() or not self.state.is_dir() or self.state.is_relative_to(self.inbox):
            raise ValueError('inbox and private state must be separate directories')
        self.interval = interval
        self.writer_lock = writer_lock or threading.Lock()
        self.environment = dict(os.environ if environment is None else environment)
        self.environment['CWA_INGEST_FOLDER'] = str(self.inbox)
        self._stop = threading.Event()
        self._thread = None
        self._observed = {}

    def start(self):
        if self._thread is not None:
            raise RuntimeError('background ingest already started')
        self._thread = threading.Thread(target=self._run, name='windows-ingest', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        try:
            self._poll()
        except Exception as error:
            print(f'[windows-ingest] stopped: {error}', file=sys.stderr, flush=True)

    def _poll(self):
        marker = self.state / 'unfinished-ingest.json'
        work = self.state / 'background-ingest-work'
        work.mkdir(exist_ok=True)
        if marker.exists():
            raise RuntimeError(f'an earlier import needs review; see {marker}')
        while not self._stop.is_set():
            library = configured_library(self.environment)
            if not library:
                self._stop.wait(self.interval)
                continue
            self.environment['CWA_CALIBRE_LIBRARY_DIR'] = library
            for raw in iter_files(str(self.inbox), recursive=True, extensions=_FORMATS):
                if self._stop.is_set():
                    break
                path = Path(raw)
                if Path(str(path) + '.cwa.failed.json').exists():
                    continue
                try:
                    info = path.stat(follow_symlinks=False)
                    identity = (info.st_size, info.st_mtime_ns)
                    if self._observed.get(str(path)) != identity:
                        self._observed[str(path)] = identity
                        continue
                    self._import(path, identity, marker, work)
                except (OSError, ValueError) as error:
                    if marker.exists():
                        raise RuntimeError(f'import needs review; see {marker}: {error}') from error
                    print(f'[windows-ingest] unable to read {path}: {error}', file=sys.stderr, flush=True)
            self._stop.wait(self.interval)

    def _import(self, path, identity, marker, work):
        attempt = work / uuid.uuid4().hex
        attempt.mkdir()
        staged = attempt / path.relative_to(self.inbox)
        staged.parent.mkdir(parents=True, exist_ok=True)
        sidecar = Path(str(path) + '.cwa.json')
        staged_sidecar = Path(str(staged) + '.cwa.json')
        try:
            with ExitStack() as stack:
                source = stack.enter_context(_open_source(path, self.inbox))
                info = os.fstat(source.fileno())
                if (info.st_size, info.st_mtime_ns) != identity:
                    return
                with staged.open('xb') as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                sidecar_source = None
                if sidecar.exists():
                    sidecar_source = stack.enter_context(_open_source(sidecar, self.inbox))
                    with staged_sidecar.open('xb') as target:
                        shutil.copyfileobj(sidecar_source, target, 1024 * 1024)
                with marker.open('x', encoding='utf-8') as record:
                    json.dump({'source': str(path), 'working_copy': str(staged)}, record)
                    record.flush()
                    os.fsync(record.fileno())
                log = self.state / 'background-ingest.log'
                environment = dict(self.environment)
                environment['CWA_INGEST_FOLDER'] = str(attempt)
                with self.writer_lock, log.open('a', encoding='utf-8') as output:
                    result = subprocess.run([sys.executable, str(Path(__file__).with_name('ingest_processor.py')),
                                             str(staged)], env=environment, stdout=output,
                                            stderr=subprocess.STDOUT, check=False)
                if (result.returncode != 0 or staged.exists() or staged_sidecar.exists() or
                        Path(str(staged) + '.cwa.failed.json').exists()):
                    raise RuntimeError(f'import needs review; see {marker} and {log}')
                # The handle has denied writes and deletion since before the
                # copy. Mark that exact file for deletion before releasing it.
                for item in (source, sidecar_source):
                    if item is not None:
                        disposition = wintypes.BOOL(True)
                        if not _set_file_information(msvcrt.get_osfhandle(item.fileno()), 4,
                                                     ctypes.byref(disposition), ctypes.sizeof(disposition)):
                            raise ctypes.WinError(ctypes.get_last_error())
            marker.unlink()
        finally:
            if not marker.exists() and attempt.exists():
                shutil.rmtree(attempt)
