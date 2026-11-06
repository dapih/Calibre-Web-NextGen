param([switch]$BuildFaust, [ValidateSet('Msvc', 'Mingw')][string]$Compiler = 'Msvc')
$ErrorActionPreference = 'Stop'
$envNames = @('PYTHONHOME','PYTHONPATH','PYTHONNOUSERSITE','PIP_CACHE_DIR','PIP_CONFIG_FILE','TEMP','TMP','PATH','SOURCE_DATE_EPOCH','LDFLAGS','M0_REAL_GXX','M0_PREFIX_MAP')
$savedEnv = @{}
foreach ($name in $envNames) { $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'cpython-3.13.14+20260623-x86_64-pc-windows-msvc-install_only.tar.gz'
$expected = '646254B53FBAC69C1B3C25131C237C9136E9BBED7444123880CEA8DEAF555E1F'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    & gh release download 20260623 --repo astral-sh/python-build-standalone --pattern 'cpython-3.13.14+20260623-x86_64-pc-windows-msvc-install_only.tar.gz' --dir $cache --clobber
    if ($LASTEXITCODE -ne 0) { throw 'Private Python download failed.' }
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Private Python archive hash mismatch; extraction refused.' }
$python = Join-Path $cache 'python\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & tar -xzf $archive -C $cache
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) { throw 'Private Python extraction failed or expected interpreter is missing.' }
}
$env:PYTHONHOME = ''
$env:PYTHONPATH = ''
$env:PYTHONNOUSERSITE = '1'
$env:PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
$env:TEMP = Join-Path $cache 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $python -I -c 'import sys; print("interpreter:", sys.executable); print("version:", sys.version); print("sys.path:", *sys.path, sep="\n  ")'
if ($LASTEXITCODE -ne 0) { throw 'Private interpreter probe failed.' }
if ($BuildFaust) {
    $wheels = Join-Path $cache 'wheels'
    $source = Join-Path $cache 'faust-cchardet-2.1.19.tar.gz'
    $sourceHash = 'F89386297CDE0C8E0F5E21464BC2D6D0E4A4FC1B1D77CDB238CA24D740D872E0'
    $buildPython = Join-Path $cache 'faust-build\Scripts\python.exe'
    $lock = Join-Path $PSScriptRoot 'windows-m0-faust-build-requirements.txt'
    New-Item -ItemType Directory -Force -Path $wheels | Out-Null
    if (-not (Test-Path -LiteralPath $source)) {
        Invoke-WebRequest -Uri 'https://files.pythonhosted.org/packages/e9/53/2125d17d7c2a14b76303bf5c3c5db35ea8e4b8a14817bb191c601d67e288/faust-cchardet-2.1.19.tar.gz' -OutFile $source
    }
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Faust source hash mismatch; build refused.' }
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $python -I -m venv (Join-Path $cache 'faust-build')
        if ($LASTEXITCODE -ne 0) { throw 'Private build environment creation failed.' }
    }
    $env:PIP_CONFIG_FILE = 'NUL'
    & $buildPython -I -m pip install --force-reinstall --require-hashes --only-binary=:all: --no-deps --no-cache-dir -r $lock
    if ($LASTEXITCODE -ne 0) { throw 'Locked build dependency installation failed.' }
    & $buildPython -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Locked build environment has unresolved requirements.' }
    $buildOptions = @()
    if ($Compiler -eq 'Mingw') {
        $compilerArchive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
        $compilerBin = Join-Path $cache 'mingw64\bin'
        if (-not (Test-Path -LiteralPath $compilerArchive) -or (Get-FileHash -LiteralPath $compilerArchive -Algorithm SHA256).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Expected verified MinGW archive is missing or changed.' }
        if (-not (Test-Path -LiteralPath (Join-Path $compilerBin 'gcc.exe'))) { throw 'Extract the verified compiler archive into .m0 first.' }
        $wrapperDir = Join-Path $cache 'gxx-wrapper'
        $wrapperExe = Join-Path $wrapperDir 'g++.exe'
        New-Item -ItemType Directory -Force -Path $wrapperDir | Out-Null
        & (Join-Path $compilerBin 'gcc.exe') -municode -static -O2 -o $wrapperExe (Join-Path $PSScriptRoot 'windows-m0-gxx-wrapper.c')
        if ($LASTEXITCODE -ne 0) { throw 'Compiler prefix-map wrapper build failed.' }
        $env:M0_REAL_GXX = Join-Path $compilerBin 'g++.exe'
        $env:M0_PREFIX_MAP = '-ffile-prefix-map=' + (Join-Path $cache 'python') + '=/cwng-python'
        $env:PATH = $wrapperDir + ';' + $compilerBin + ';' + $env:PATH
        $env:SOURCE_DATE_EPOCH = '1782160000'
        $env:LDFLAGS = '-Wl,--no-insert-timestamp'
        & (Join-Path $compilerBin 'gcc.exe') --version | Select-Object -First 1
        $buildOptions = @('--config-settings=--build-option=build_ext', '--config-settings=--build-option=--compiler=mingw32')
    }
    & $buildPython -I -m pip wheel --no-build-isolation --no-deps --no-cache-dir --wheel-dir $wheels @buildOptions $source
    if ($LASTEXITCODE -ne 0) { throw 'Faust wheel build failed; N1 remains unproved. Review the build output.' }
    $builtWheels = @(Get-ChildItem -LiteralPath $wheels -Filter 'faust_cchardet-2.1.19-*.whl' -File)
    if ($builtWheels.Count -ne 1) { throw 'Expected exactly one Faust wheel; inspect the build output.' }
    $testPython = Join-Path $cache 'faust-test\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $testPython)) {
        & $python -I -m venv (Join-Path $cache 'faust-test')
        if ($LASTEXITCODE -ne 0) { throw 'Private test environment creation failed.' }
    }
    & $testPython -I -m pip install --force-reinstall --no-index --no-deps $builtWheels[0].FullName
    if ($LASTEXITCODE -ne 0) { throw 'Built wheel installation failed.' }
    if ($Compiler -eq 'Mingw') {
        $packageDir = Join-Path $cache 'faust-test\Lib\site-packages\cchardet'
        foreach ($dll in @('libgcc_s_seh-1.dll','libstdc++-6.dll','libwinpthread-1.dll')) {
            Copy-Item -LiteralPath (Join-Path $compilerBin $dll) -Destination $packageDir -Force
            Get-FileHash -LiteralPath (Join-Path $packageDir $dll) -Algorithm SHA256 | Format-List
        }
        Write-Output 'The raw wheel does not contain these runtime DLLs; they were copied for this isolated proof only.'
    }
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    & $testPython -I -c 'import cchardet, importlib.metadata as m; assert m.version("faust-cchardet") == "2.1.19"; text = ("caf" + chr(233) + " na" + chr(239) + "ve ") * 40; a = cchardet.detect(b"ordinary ASCII text"); u = cchardet.detect(text.encode("utf-8")); print("ASCII:", a, "UTF-8:", u); assert a["encoding"].lower() in ("ascii", "us-ascii"); assert u["encoding"].lower().replace("-", "") == "utf8"'
    if ($LASTEXITCODE -ne 0) { throw 'Faust import or encoding probe failed.' }
    if ($Compiler -eq 'Mingw') {
        $originProbe = @'
import ctypes, cchardet, cchardet._cchardet
from pathlib import Path
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [ctypes.c_wchar_p], ctypes.c_void_p
k.GetModuleFileNameW.argtypes, k.GetModuleFileNameW.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong], ctypes.c_ulong
package = Path(cchardet.__file__).resolve().parent
assert Path(cchardet._cchardet.__file__).resolve().parent == package
for name in ('libgcc_s_seh-1.dll', 'libstdc++-6.dll', 'libwinpthread-1.dll'):
    handle = k.GetModuleHandleW(name)
    if not handle: raise RuntimeError('Missing loaded DLL: ' + name)
    path = ctypes.create_unicode_buffer(32768)
    if not k.GetModuleFileNameW(handle, path, len(path)): raise ctypes.WinError(ctypes.get_last_error())
    print(name + ':', path.value)
    assert Path(path.value).resolve().parent == package
'@
        & $testPython -I -c $originProbe
        if ($LASTEXITCODE -ne 0) { throw 'Faust DLL origin probe failed.' }
    }
    Get-FileHash -LiteralPath $builtWheels[0].FullName -Algorithm SHA256 | Format-List
}
Write-Output 'This is only an N1 probe. All six M0 groups remain pending.'
} finally {
    foreach ($name in $envNames) {
        if ($null -eq $savedEnv[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $savedEnv[$name] }
    }
}
