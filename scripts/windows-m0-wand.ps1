# Experimental private ImageMagick/Wand proof. Extracted payload is not a release candidate.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$setup = Join-Path $cache 'imagemagick-proof\ImageMagick-7.1.2-35-Q16-HDRI-x64-dll.exe'
$dir = Join-Path $cache 'imagemagick-proof\dynamic\app'
$python = Join-Path $cache 'n1-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $setup) -or (Get-FileHash -LiteralPath $setup).Hash -ne '32CC21FE23F7D7B7D9133905A2DC5AAC94F574A07740911D948EB88B09A0B432') { throw 'ImageMagick installer hash mismatch.' }
foreach ($path in @($python, (Join-Path $dir 'CORE_RL_MagickWand_.dll'), (Join-Path $dir 'modules\coders\IM_MOD_RL_png_.dll'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing proof prerequisite: $path" }
}
$names = @('PATH','PYTHONHOME','PYTHONPATH','MAGICK_HOME','MAGICK_CONFIGURE_PATH','MAGICK_CODER_MODULE_PATH','MAGICK_FILTER_MODULE_PATH','MAGICK_TEMPORARY_PATH','QT_PLUGIN_PATH')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = $dir + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:QT_PLUGIN_PATH = 'C:\invalid-host-qt'
    $env:MAGICK_HOME = $dir
    $env:MAGICK_CONFIGURE_PATH = $dir
    $env:MAGICK_CODER_MODULE_PATH = Join-Path $dir 'modules\coders'
    $env:MAGICK_FILTER_MODULE_PATH = Join-Path $dir 'modules\filters'
    $env:MAGICK_TEMPORARY_PATH = Join-Path $cache 'temp'
    $probe = @'
import ctypes, os
from pathlib import Path
home = Path(os.environ['MAGICK_HOME']).resolve()
with os.add_dll_directory(str(home)), os.add_dll_directory(str(home / 'modules' / 'coders')):
    from wand.image import Image
    from wand.color import Color
    from wand.api import library
    with Image(width=80, height=60, background=Color('navy')) as image:
        image.resize(40, 30)
        blob = image.make_blob('png')
    with Image(blob=blob) as result:
        assert (result.width, result.height, result.format) == (40, 30, 'PNG')
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [ctypes.c_wchar_p], ctypes.c_void_p
    k.GetModuleFileNameW.argtypes, k.GetModuleFileNameW.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong], ctypes.c_ulong
    def origin(handle):
        buf = ctypes.create_unicode_buffer(32768)
        if not k.GetModuleFileNameW(handle, buf, len(buf)): raise ctypes.WinError(ctypes.get_last_error())
        return Path(buf.value).resolve()
    assert origin(library._handle).parent == home
    coder = k.GetModuleHandleW('IM_MOD_RL_png_.dll')
    assert coder and origin(coder).parent == home / 'modules' / 'coders'
    print('Private Wand DLL:', origin(library._handle))
    print('Private PNG coder:', origin(coder))
    ps = ctypes.WinDLL('psapi', use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.c_void_p
    ps.EnumProcessModulesEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
    ps.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    ps.GetModuleFileNameExW.restype = ctypes.c_ulong
    handles = (ctypes.c_void_p * 4096)(); needed = ctypes.c_ulong(); proc = k.GetCurrentProcess()
    if not ps.EnumProcessModulesEx(proc, handles, ctypes.sizeof(handles), ctypes.byref(needed), 3): raise ctypes.WinError(ctypes.get_last_error())
    if needed.value > ctypes.sizeof(handles): raise RuntimeError('Module trace truncated')
    roots = [home, Path(os.environ['SystemRoot']).resolve(), home.parents[2] / 'python', home.parents[2] / 'n1-runtime']
    paths = []
    for handle in handles[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        buf = ctypes.create_unicode_buffer(32768)
        if not ps.GetModuleFileNameExW(proc, handle, buf, len(buf)): raise ctypes.WinError(ctypes.get_last_error())
        path = Path(buf.value).resolve(); paths.append(path)
        if not any(path.is_relative_to(root) for root in roots): raise RuntimeError('Unexpected loaded module: ' + str(path))
    print('Native module origins checked:', len(paths))
    print('PNG resize and round-trip passed:', len(blob), 'bytes')
'@
    & $python -I -c $probe
    if ($LASTEXITCODE -ne 0) { throw 'Wand/image operation or origin proof failed.' }
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
