"""Receive tray requests through local events created by the Windows launcher."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
import re
import threading


@contextmanager
def tray_control(web_server):
    prefix = os.environ.pop('CWNG_TRAY_CONTROL', '')
    if not prefix:
        yield
        return
    if not re.fullmatch(r'Local\\CWNG-[0-9A-F]{32}', prefix):
        raise ValueError('Invalid Windows tray control name')

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
        print(f'CWNG_TRAY_READY {prefix} {scheme}://127.0.0.1:{web_server.listen_port}/', flush=True)
        while not done.is_set():
            for restart, handle in zip((False, True), handles):
                if kernel.WaitForSingleObject(handle, 0) == 0:
                    loop.add_callback(web_server.stop, restart=restart)
                    return
            done.wait(0.1)

    try:
        for action in ('quit', 'restart'):
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
        for handle in handles:
            kernel.CloseHandle(handle)
