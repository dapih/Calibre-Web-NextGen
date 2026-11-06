# Evaluate the experimental standalone wheel in a disposable environment.
param([switch]$Repaired)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$wheel = Join-Path $cache 'magic-alternative\python_magic_standalone-0.4.29-py2.py3-none-win_amd64.whl'
if ($Repaired) {
    $wheel = Join-Path $cache 'magic-repaired\python_magic_standalone-0.4.29+cwng.m0-cp313-none-win_amd64.whl'
    if ((Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash -ne 'F0EEE050A262CD9B526B857BD2673E44ECACBA36E5D21B5D6CE792DC80CF7CAB') { throw 'Unexpected repaired wheel hash.' }
}
elseif ((Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash -ne 'EE9767392B66D0D63BC2EC6C79930308585F5B5BCB7F749EB7B68FA1E5BBDFC2') { throw 'Unexpected standalone wheel hash.' }
$run = Join-Path $cache ('magic-alternative-check-' + [guid]::NewGuid().ToString('N'))
$foreign = Join-Path $run 'foreign'
New-Item -ItemType Directory -Path $foreign -Force | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','MAGIC','M0_EPUB','M0_FOREIGN','M0_FILE_HELPER')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''; $env:PYTHONPATH = ''
    $venv = Join-Path $run 'runtime'
    & $python -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating the private environment failed.' }
    $testPython = Join-Path $venv 'Scripts\python.exe'
    & $testPython -I -m pip install --no-index --no-deps $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Installing the standalone wheel failed.' }
    $package = Join-Path $venv 'Lib\site-packages\magic'
    Copy-Item -Path (Join-Path $package '*.dll') -Destination $foreign
    $env:PATH = $foreign + ';' + $env:PATH
    $env:PYTHONHOME = 'C:\invalid-host-python'; $env:PYTHONPATH = $foreign
    if ($Repaired) { $env:MAGIC = Join-Path $foreign 'missing.mgc' }
    else { $env:MAGIC = Join-Path $package 'magic.mgc' }
    $env:M0_EPUB = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
    $env:M0_FOREIGN = $foreign
    $env:M0_FILE_HELPER = Join-Path $root 'cps\file_helper.py'
    $probe = @'
import ctypes, importlib.util, io, os, pathlib, sys, types
foreign = pathlib.Path(os.environ['M0_FOREIGN']).resolve()
import magic
package = pathlib.Path(magic.__file__).resolve().parent
loaded = pathlib.Path(magic.libmagic._name).resolve()
assert loaded == (package / 'libmagic-1.dll').resolve(), loaded
detector = magic.Magic(mime=True)
assert detector.from_buffer(b'%PDF-1.4\n%fixture\n') == 'application/pdf'
assert detector.from_buffer(pathlib.Path(os.environ['M0_EPUB']).read_bytes()) in ('application/epub+zip', 'application/zip')
assert detector.from_buffer(b'plain text\n') == 'text/plain'
cps = types.ModuleType('cps'); cps.__path__ = []
logger = types.ModuleType('cps.logger')
logger.create = lambda: types.SimpleNamespace(error=lambda *args: None)
cps.logger = logger
sys.modules['cps'] = cps; sys.modules['cps.logger'] = logger
spec = importlib.util.spec_from_file_location('cps.file_helper', os.environ['M0_FILE_HELPER'])
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
assert helper.validate_mime_type(io.BytesIO(b'%PDF-1.4\n%fixture\n'), ['pdf'])
assert not helper.validate_mime_type(io.BytesIO(b'plain text\n'), ['pdf'])
print('Explicit private database and bundled DLL detected PDF, EPUB and text.')
'@
    Push-Location $foreign
    try {
        $probe | & $testPython -I -
        if ($LASTEXITCODE -ne 0) { throw 'Standalone MIME probe failed.' }
        if ($Repaired) {
            $foreignDependency = @'
import ctypes, os, pathlib
foreign = ctypes.CDLL(str(pathlib.Path(os.environ['M0_FOREIGN']) / 'libsystre-0.dll'), winmode=0x900)
try:
    import magic
except ImportError as error:
    assert 'foreign DLL already loaded' in str(error), error
    print('Preloaded foreign dependency was rejected.')
else:
    raise AssertionError('Preloaded foreign dependency was accepted')
'@
            $foreignDependency | & $testPython -I -
            if ($LASTEXITCODE -ne 0) { throw 'Foreign dependency check failed.' }
            Rename-Item -LiteralPath (Join-Path $package 'magic.mgc') -NewName 'magic.mgc.disabled'
            $missingDatabase = @'
import magic
try:
    magic.Magic(mime=True)
except magic.MagicException:
    print('Missing private database failed closed despite hostile MAGIC.')
else:
    raise AssertionError('Missing private database was accepted')
'@
            $missingDatabase | & $testPython -I -
            if ($LASTEXITCODE -ne 0) { throw 'Missing database check failed.' }
        }
        # Remove only the disposable private DLL to test whether the wrapper fails closed.
        Remove-Item -LiteralPath (Join-Path $package 'libmagic-1.dll')
        if ($Repaired) {
            $fallback = @'
try:
    import magic
except ImportError:
    print('Missing private DLL failed closed despite foreign copy.')
else:
    raise AssertionError('Missing private DLL loaded a foreign DLL')
'@
        } else { $fallback = @'
import os, pathlib, magic
foreign = pathlib.Path(os.environ['M0_FOREIGN']).resolve()
loaded = pathlib.Path(magic.libmagic._name).resolve()
assert loaded == foreign / 'libmagic-1.dll', loaded
print('Missing bundled DLL caused fallback to:', loaded)
'@
        }
        $fallback | & $testPython -I -
        if ($LASTEXITCODE -ne 0) { throw 'Standalone fallback check failed.' }
    } finally { Pop-Location }
    Write-Output "Experimental standalone evidence: $run"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
