"""Observe loaded modules while the private Calibre converter actually runs."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / '.m0' / 'calibre-proof' / 'extracted' / 'Calibre Portable' / 'Calibre'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'sample_books' / 'sherlock_holmes.epub'


def main():
    if os.name != 'nt':
        raise RuntimeError('this origin probe requires Windows')
    work = ROOT / '.m0' / ('calibre-origin-' + uuid.uuid4().hex)
    for path in (work, work / 'config', work / 'temp', work / 'hostile', work / 'library'):
        path.mkdir(parents=True, exist_ok=True)
    for name in ('ebook-convert.exe', 'calibredb.exe', 'calibre-debug.exe', 'ebook-meta.exe', 'ebook-polish.exe'):
        (work / 'hostile' / name).write_bytes(b'not a private executable')
    windows = Path(os.environ['SystemRoot']).resolve(strict=True)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    environment.update(PATH=str(work / 'hostile') + os.pathsep + str(windows / 'System32'),
                       PYTHONHOME=str(work / 'hostile'), PYTHONPATH=str(work / 'hostile'),
                       QT_PLUGIN_PATH=str(work / 'hostile'), MAGICK_HOME=str(work / 'hostile'),
                       CALIBRE_CONFIG_DIRECTORY=str(work / 'config'), CALIBRE_CACHE_DIRECTORY=str(work / 'config'),
                       USERPROFILE=str(work / 'config'), APPDATA=str(work / 'config'),
                       LOCALAPPDATA=str(work / 'config'), HOME=str(work / 'config'),
                       TEMP=str(work / 'temp'), TMP=str(work / 'temp'))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetShortPathNameW.restype = wintypes.DWORD
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    tool = TOOL.resolve(strict=True)
    output = work / 'converted.epub'
    short = ctypes.create_unicode_buffer(32768)
    if not kernel.GetShortPathNameW(str(work / 'library'), short, len(short)):
        raise ctypes.WinError(ctypes.get_last_error())
    library = short.value
    commands = [
        ('convert', [str(tool / 'ebook-convert.exe'), str(FIXTURE), str(output), '--no-default-epub-cover']),
        ('metadata', [str(tool / 'ebook-meta.exe'), str(output)]),
        ('polish', [str(tool / 'ebook-polish.exe'), '-U', str(output), str(output)]),
        ('library-init', [str(tool / 'calibre-debug.exe'), '-c',
                          'import sys; from calibre.db.legacy import LibraryDatabase; '
                          'db=LibraryDatabase(sys.argv[1]); db.close()', library]),
        ('library-list', [str(tool / 'calibredb.exe'), 'list', '--library-path=' + library]),
    ]
    evidence = {'scope': 'sampled tool processes only; child processes and transient loads are not covered',
                'commands': []}
    for label, command in commands:
        origins = set()
        samples = 0
        log_path = work / (label + '.log')
        with log_path.open('wb') as log:
            process = subprocess.Popen(command, cwd=work / 'hostile', env=environment,
                                       stdout=log, stderr=subprocess.STDOUT)
            handle = kernel.OpenProcess(0x0400 | 0x0010, False, process.pid)
            if not handle:
                process.terminate()
                process.wait(timeout=10)
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                while process.poll() is None:
                    modules = (wintypes.HMODULE * 4096)()
                    needed = wintypes.DWORD()
                    if psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
                        if needed.value > ctypes.sizeof(modules):
                            raise RuntimeError('module snapshot truncated')
                        for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
                            name = ctypes.create_unicode_buffer(32768)
                            if psapi.GetModuleFileNameExW(handle, module, name, len(name)):
                                origins.add(str(Path(name.value).resolve()))
                        samples += 1
                    time.sleep(.1)
                if process.returncode or not output.is_file() or not samples:
                    raise RuntimeError(label + ' or module sampling failed; see ' + str(log_path))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                kernel.CloseHandle(handle)
        unexpected = [path for path in sorted(origins)
                      if not Path(path).is_relative_to(tool) and not Path(path).is_relative_to(windows)]
        evidence['commands'].append({'label': label, 'samples': samples, 'module_paths': sorted(origins),
                                     'unexpected_paths': unexpected})
        if unexpected:
            raise RuntimeError('unexpected ' + label + ' modules: ' + repr(unexpected))
    (work / 'origin.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Calibre origins: ' + ', '.join(f"{item['label']}={item['samples']} snapshots/{len(item['module_paths'])} modules"
                                         for item in evidence['commands']) + '; ' + str(work))


if __name__ == '__main__':
    main()
