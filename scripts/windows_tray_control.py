"""Receive tray requests through local events created by the Windows launcher."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import threading


@contextmanager
def tray_control(web_server):
    prefix = os.environ.pop('CWNG_TRAY_CONTROL', '')
    if not prefix and not os.environ.get('CWA_APP_ROOT'):
        yield
        return
    if prefix and not re.fullmatch(r'Local\\CWNG-[0-9A-F]{32}', prefix):
        raise ValueError('Invalid Windows tray control name')

    address = Path(os.environ['CALIBRE_DBPATH']).parent / 'launcher-url'
    pending_address = address.with_suffix('.tmp')
    address.unlink(missing_ok=True)

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    done = threading.Event()
    handles = []
    thread = None

    def watch():
        # On Windows, Tornado binds its socket before publishing its IOLoop.
        while (loop := web_server.wsgiserver) is None:
            if done.wait(0.1):
                return
        scheme = 'https' if web_server.ssl_args else 'http'
        url = f'{scheme}://127.0.0.1:{web_server.listen_port}/'
        if prefix:
            print(f'CWNG_TRAY_READY {prefix} {url}', flush=True)
        try:
            pending_address.write_text(url, encoding='ascii')
            pending_address.replace(address)
        except OSError as error:
            print(f'Could not save CWNG launcher address: {error}', flush=True)
        if not prefix:
            return
        while not done.is_set():
            for restart, handle in zip((False, True), handles):
                if kernel.WaitForSingleObject(handle, 0) == 0:
                    loop.add_callback(web_server.stop, restart=restart)
                    return
            done.wait(0.1)

    try:
        for action in ('quit', 'restart') if prefix else ():
            handle = kernel.OpenEventW(0x00100000, False, prefix + '-' + action)  # SYNCHRONIZE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        thread = threading.Thread(target=watch, name='windows-tray-control', daemon=True)
        thread.start()
        yield
    finally:
        done.set()
        if thread is not None:
            thread.join()
        address.unlink(missing_ok=True)
        pending_address.unlink(missing_ok=True)
        for handle in handles:
            kernel.CloseHandle(handle)
