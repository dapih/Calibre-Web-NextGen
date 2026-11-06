# Trace the real --version entrypoint in a disposable isolated environment.
param([string]$Runtime)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
if (-not $Runtime) { $Runtime = Join-Path $cache 'n1-runtime-locked' }
$Runtime = (Resolve-Path -LiteralPath $Runtime).Path
$python = Join-Path $Runtime 'Scripts\python.exe'
$magick = Join-Path $cache 'imagemagick-proof\dynamic\app'
$work = Join-Path $cache ('app-origin-' + [guid]::NewGuid().ToString('N'))
$hostile = Join-Path $work 'hostile'
foreach ($path in @($python, (Join-Path $magick 'CORE_RL_MagickWand_.dll'))) { if (-not (Test-Path -LiteralPath $path)) { throw "Missing prerequisite: $path" } }
New-Item -ItemType Directory -Force -Path $work,$hostile,(Join-Path $work 'config'),(Join-Path $work 'home'),(Join-Path $work 'temp') | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','CALIBRE_DBPATH','CALIBRE_CONFIG_DIRECTORY','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','MAGICK_HOME','MAGICK_CONFIGURE_PATH','MAGICK_CODER_MODULE_PATH','MAGICK_FILTER_MODULE_PATH','MAGICK_TEMPORARY_PATH','M0_ROOT','M0_WORK','M0_RUNTIME')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = $magick + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = $hostile
    $env:QT_PLUGIN_PATH = Join-Path $hostile 'qt'
    $env:CALIBRE_DBPATH = Join-Path $work 'config'
    $env:CALIBRE_CONFIG_DIRECTORY = Join-Path $work 'calibre-config'
    $env:HOME = Join-Path $work 'home'
    $env:USERPROFILE = $env:HOME
    $env:APPDATA = $env:HOME
    $env:LOCALAPPDATA = $env:HOME
    $env:TEMP = Join-Path $work 'temp'
    $env:TMP = $env:TEMP
    $env:MAGICK_HOME = $magick
    $env:MAGICK_CONFIGURE_PATH = $magick
    $env:MAGICK_CODER_MODULE_PATH = Join-Path $magick 'modules\coders'
    $env:MAGICK_FILTER_MODULE_PATH = Join-Path $magick 'modules\filters'
    $env:MAGICK_TEMPORARY_PATH = $env:TEMP
    $env:M0_ROOT = $root
    $env:M0_WORK = $work
    $env:M0_RUNTIME = $Runtime
    $probe = @'
import ctypes, json, os, runpy, sys
from pathlib import Path
root, work = Path(os.environ['M0_ROOT']).resolve(), Path(os.environ['M0_WORK']).resolve()
with os.add_dll_directory(os.environ['MAGICK_HOME']), os.add_dll_directory(os.environ['MAGICK_CODER_MODULE_PATH']):
    sys.argv = [str(root / 'cps.py'), '--version']
    try:
        runpy.run_path(sys.argv[0], run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (None, 0): raise
    modules = {name: str(Path(file).resolve()) for name, obj in list(sys.modules.items()) if (file := getattr(obj, '__file__', None)) and file != '<stdin>'}
    python_roots = [root / 'cps', root / 'scripts', root / '.m0' / 'python', Path(os.environ['M0_RUNTIME']).resolve()]
    outside = {n:p for n,p in modules.items() if not any(Path(p).is_relative_to(base) for base in python_roots)}
    if outside: raise RuntimeError('Python modules outside worktree: ' + repr(outside))
    k, ps = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('psapi', use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.c_void_p
    ps.EnumProcessModulesEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    ps.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    ps.GetModuleFileNameExW.restype = ctypes.c_ulong
    handles = (ctypes.c_void_p * 4096)(); needed = ctypes.c_ulong(); proc = k.GetCurrentProcess()
    if not ps.EnumProcessModulesEx(proc, handles, ctypes.sizeof(handles), ctypes.byref(needed), 3): raise ctypes.WinError(ctypes.get_last_error())
    if needed.value > ctypes.sizeof(handles): raise RuntimeError('Module trace truncated')
    paths = []
    for handle in handles[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        buf = ctypes.create_unicode_buffer(32768)
        if not ps.GetModuleFileNameExW(proc, handle, buf, len(buf)): raise ctypes.WinError(ctypes.get_last_error())
        paths.append(str(Path(buf.value).resolve()))
    windows = Path(os.environ['SystemRoot']).resolve()
    native_roots = [root / '.m0' / 'python', Path(os.environ['M0_RUNTIME']).resolve(), Path(os.environ['MAGICK_HOME']).resolve(), windows]
    unexpected = [p for p in paths if not any(Path(p).is_relative_to(base) for base in native_roots)]
    evidence = {'python_modules': modules, 'loaded_modules': paths, 'unexpected_loaded_modules': unexpected}
    (work / 'origin-trace.json').write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding='utf-8')
    if unexpected: raise RuntimeError('Unexpected native module origins: ' + repr(unexpected))
    print('Origin trace:', len(modules), 'Python modules;', len(paths), 'native modules;', work / 'origin-trace.json')
'@
    Push-Location $hostile
    try {
        $probe | & $python -I -
        if ($LASTEXITCODE -ne 0) { throw 'App version entrypoint or origin trace failed.' }
    } finally { Pop-Location }
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
