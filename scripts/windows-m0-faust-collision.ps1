# Disposable negative control for the experimental Faust DLL arrangement.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'faust-test\Scripts\python.exe'
$package = Join-Path $cache 'faust-test\Lib\site-packages\cchardet'
$foreign = Join-Path $cache 'mingw64\bin'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $package '_cchardet.cp313-win_amd64.pyd')) -or -not (Test-Path -LiteralPath (Join-Path $foreign 'libgcc_s_seh-1.dll'))) {
    throw 'Run the MinGW Faust build probe first.'
}
$copyRoot = Join-Path $cache ('collision-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $copyRoot | Out-Null
Copy-Item -LiteralPath $package -Destination (Join-Path $copyRoot 'cchardet') -Recurse
Remove-Item -LiteralPath (Join-Path $copyRoot 'cchardet\libgcc_s_seh-1.dll')
$names = @('PATH', 'PYTHONHOME', 'PYTHONPATH', 'M0_FOREIGN_BIN', 'M0_COPY_ROOT')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:M0_FOREIGN_BIN = $foreign
    $env:M0_COPY_ROOT = $copyRoot
    $baseline = @'
import os, sys
sys.path.insert(0, os.environ['M0_COPY_ROOT'])
try:
    import cchardet
except ImportError as exc:
    print('Baseline without preloaded DLL: import failed:', exc)
else:
    raise SystemExit('Unexpected import without the required private DLL')
'@
    & $python -I -S -c $baseline
    if ($LASTEXITCODE -ne 0) { throw 'Missing-DLL baseline did not fail as expected.' }
    $probe = @'
import ctypes, os, sys
from pathlib import Path
foreign = Path(os.environ['M0_FOREIGN_BIN']).resolve()
copy = Path(os.environ['M0_COPY_ROOT']).resolve()
assert not (copy / 'cchardet' / 'libgcc_s_seh-1.dll').exists()
with os.add_dll_directory(str(foreign)):
    loaded = ctypes.WinDLL(str(foreign / 'libgcc_s_seh-1.dll'))
sys.path.insert(0, str(copy))
import cchardet, cchardet._cchardet
assert Path(cchardet.__file__).resolve().parent == copy / 'cchardet'
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [ctypes.c_wchar_p], ctypes.c_void_p
k.GetModuleFileNameW.argtypes, k.GetModuleFileNameW.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong], ctypes.c_ulong
handle = k.GetModuleHandleW('libgcc_s_seh-1.dll')
assert handle == loaded._handle
path = ctypes.create_unicode_buffer(32768)
if not k.GetModuleFileNameW(handle, path, len(path)): raise ctypes.WinError(ctypes.get_last_error())
assert Path(path.value).resolve().parent == foreign
assert foreign != Path(cchardet.__file__).resolve().parent
assert cchardet.detect(b'ordinary ASCII text')['encoding'].lower() == 'ascii'
print('NEGATIVE CONTROL: import succeeded with same-named runtime already loaded from:', path.value)
print('Private package:', Path(cchardet.__file__).resolve().parent)
'@
    & $python -I -S -c $probe
    if ($LASTEXITCODE -ne 0) { throw 'Collision negative control did not produce the expected result.' }
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
