"""Optional M0 process-origin snapshot at worker exit."""
import atexit
import ctypes
import json
import os
from pathlib import Path
import sys


def install(entrypoint):
    destination = os.environ.get('CWNG_M0_TRACE_DIR')
    if not destination:
        return
    destination = Path(destination).resolve(strict=True)
    root = Path(__file__).resolve().parent.parent
    runtime = Path(sys.prefix).resolve(strict=True)
    windows = Path(os.environ['SystemRoot']).resolve(strict=True)
    helper = Path(os.environ['CWNG_NATIVE_HELPER']).resolve(strict=True)

    def snapshot():
        python = {name: str(Path(file).resolve()) for name, module in list(sys.modules.items())
                  if (file := getattr(module, '__file__', None)) and file != '<stdin>'}
        python_roots = (root / 'cps', root / 'scripts', root / '.m0' / 'python', runtime)
        unexpected_python = {name: path for name, path in python.items()
                             if not any(Path(path).is_relative_to(base) for base in python_roots)}
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.EnumProcessModulesEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
        psapi.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        psapi.GetModuleFileNameExW.restype = ctypes.c_ulong
        handles = (ctypes.c_void_p * 4096)()
        needed = ctypes.c_ulong()
        process = kernel.GetCurrentProcess()
        if not psapi.EnumProcessModulesEx(process, handles, ctypes.sizeof(handles), ctypes.byref(needed), 3):
            raise ctypes.WinError(ctypes.get_last_error())
        if needed.value > ctypes.sizeof(handles):
            raise RuntimeError('module snapshot truncated')
        native = []
        for handle in handles[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            name = ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleFileNameExW(process, handle, name, len(name)):
                raise ctypes.WinError(ctypes.get_last_error())
            native.append(str(Path(name.value).resolve()))
        native_roots = (root / '.m0' / 'python', runtime, windows)
        unexpected_native = [path for path in native
                             if Path(path) != helper and
                             not any(Path(path).is_relative_to(base) for base in native_roots)]
        result = {'entrypoint': Path(entrypoint).name, 'python': python, 'native': native,
                  'unexpected_python': unexpected_python, 'unexpected_native': unexpected_native}
        (destination / (Path(entrypoint).stem + '-' + str(os.getpid()) + '.json')).write_text(
            json.dumps(result, indent=2) + '\n', encoding='utf-8')

    atexit.register(snapshot)
