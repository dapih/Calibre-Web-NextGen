"""Internal recovery proof, invoked only using the original private Python."""
from pathlib import Path
import sys


def _require_private_interpreter():
    payload = Path(__file__).resolve(strict=True).parent.parent
    runtime = (payload / 'runtime').resolve(strict=True)
    if (not sys.flags.isolated or not sys.dont_write_bytecode or
            Path(sys.executable).resolve(strict=True) != runtime / 'Scripts' / 'python.exe' or
            Path(sys.prefix).resolve(strict=True) != runtime or
            Path(sys.base_prefix).resolve(strict=True) != runtime or
            (runtime / 'pyvenv.cfg').exists() or (runtime / 'Scripts' / 'pyvenv.cfg').exists()):
        raise RuntimeError('recovery requires the isolated original private interpreter')
    for entry in sys.path:
        if not entry or not Path(entry).resolve(strict=False).is_relative_to(runtime):
            raise RuntimeError('recovery interpreter has an external import path')
    for module in tuple(sys.modules.values()):
        origin = getattr(module, '__file__', None)
        if origin:
            path = Path(origin).resolve(strict=False)
            if path != Path(__file__).resolve(strict=True) and not path.is_relative_to(runtime):
                raise RuntimeError('recovery interpreter loaded an external module')


_require_private_interpreter()
import argparse
import ctypes
sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_native_checkpoint import recover_guarded_activation


def _require_private_origins():
    payload = Path(__file__).resolve(strict=True).parent.parent
    for module in tuple(sys.modules.values()):
        origin = getattr(module, '__file__', None)
        if origin and not Path(origin).resolve(strict=False).is_relative_to(payload):
            raise RuntimeError('recovery loaded an external Python module')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetSystemWindowsDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetSystemWindowsDirectoryW.restype = ctypes.c_uint
    name = ctypes.create_unicode_buffer(32768)
    if not kernel.GetSystemWindowsDirectoryW(name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    windows = Path(name.value).resolve(strict=True)
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
        raise RuntimeError('recovery native module list is incomplete')
    for handle in handles[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        name = ctypes.create_unicode_buffer(32768)
        if not psapi.GetModuleFileNameExW(process, handle, name, len(name)):
            raise ctypes.WinError(ctypes.get_last_error())
        origin = Path(name.value).resolve(strict=True)
        if not origin.is_relative_to(payload) and not origin.is_relative_to(windows):
            raise RuntimeError('recovery loaded an external native module')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--program-root', required=True)
    parser.add_argument('--recovery', required=True)
    args = parser.parse_args()
    _require_private_origins()
    recover_guarded_activation(args.state, args.program_root, args.recovery)


if __name__ == '__main__':
    main()
