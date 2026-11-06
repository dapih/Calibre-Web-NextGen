# Single-owner libmagic proof; this is not a production runtime bootstrap.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$wheel = Join-Path $cache 'magic-inputs\python_magic_bin-0.4.14-py2.py3-none-win_amd64.whl'
if (-not (Test-Path -LiteralPath $wheel) -or (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash -ne '90BE6206AD31071A36065A2FC169C5AFB5E0355CBE6030E87641C6C62EDC2B69') {
    throw 'Missing or changed python-magic-bin wheel; run the ownership probe prerequisites first.'
}
$names = @('PATH','PYTHONHOME','PYTHONPATH','PIP_CONFIG_FILE','TEMP','TMP','MAGIC','M0_EPUB','M0_FILE_HELPER','M0_HOSTILE')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$run = Join-Path $cache ('magic-private-' + [guid]::NewGuid().ToString('N'))
$hostile = Join-Path $run 'hostile'
New-Item -ItemType Directory -Path $hostile -Force | Out-Null
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''
    $env:PYTHONPATH = ''
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:TEMP = $run
    $env:TMP = $run
    $venv = Join-Path $run 'private-env'
    & $python -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Private environment creation failed.' }
    $testPython = Join-Path $venv 'Scripts\python.exe'
    & $testPython -I -m pip install --no-index --no-deps $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Private wheel installation failed.' }
    # Preload a same-named copy of the verified wheel's DLL from a foreign path.
    Copy-Item -LiteralPath (Join-Path $venv 'Lib\site-packages\magic\libmagic\libmagic.dll') -Destination (Join-Path $hostile 'libmagic.dll')
    Set-Content -LiteralPath (Join-Path $hostile 'magic1.dll') -Value 'invalid decoy' -Encoding ASCII
    $env:PATH = $hostile + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = $hostile
    $env:MAGIC = Join-Path $hostile 'missing.mgc'
    $env:M0_EPUB = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
    $env:M0_FILE_HELPER = Join-Path $root 'cps\file_helper.py'
    $env:M0_HOSTILE = $hostile
    $probe = @'
import ctypes, importlib.metadata as metadata, importlib.util, io, os, sys, types
from pathlib import Path
foreign_path = (Path(os.environ['M0_HOSTILE']) / 'libmagic.dll').resolve()
foreign = ctypes.CDLL(str(foreign_path))
import magic
import magic.magic as wrapper
try:
    metadata.version('python-magic')
except metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError('Conflicting second magic distribution is installed')
assert metadata.version('python-magic-bin') == '0.4.14'
package = Path(magic.__file__).resolve().parent
private_dll = (package / 'libmagic' / 'libmagic.dll').resolve()
private_database = (package / 'libmagic' / 'magic.mgc').resolve()
assert Path(wrapper.libmagic._name).resolve() == private_dll
assert Path(wrapper.default_magic_file).resolve() == private_database
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
k.GetModuleFileNameW.restype = ctypes.c_ulong
buffer = ctypes.create_unicode_buffer(32768)
if not k.GetModuleFileNameW(wrapper.libmagic._handle, buffer, len(buffer)): raise ctypes.WinError(ctypes.get_last_error())
assert Path(buffer.value).resolve() == private_dll
assert wrapper.libmagic._handle != foreign._handle
if not k.GetModuleFileNameW(foreign._handle, buffer, len(buffer)): raise ctypes.WinError(ctypes.get_last_error())
assert Path(buffer.value).resolve() == foreign_path
epub = Path(os.environ['M0_EPUB']).read_bytes()
cases = {'pdf': (b'%PDF-1.4\n%fixture\n', {'application/pdf'}),
         'epub': (epub, {'application/epub+zip', 'application/zip'}),
         'text': (b'ordinary plain text\n', {'text/plain'})}
detector = magic.Magic(mime=True)
for name, (data, expected) in cases.items():
    result = detector.from_buffer(data)
    print(name, result)
    assert result in expected
# Exercise the existing upload MIME function without starting Flask or databases.
cps = types.ModuleType('cps')
cps.__path__ = []
logger = types.ModuleType('cps.logger')
logger.create = lambda: types.SimpleNamespace(error=lambda *args: None)
cps.logger = logger
sys.modules['cps'] = cps
sys.modules['cps.logger'] = logger
spec = importlib.util.spec_from_file_location('cps.file_helper', os.environ['M0_FILE_HELPER'])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
assert helper.error is None
for name, allowed, expected in [('pdf', ['pdf'], True), ('epub', ['epub'], True), ('text', ['pdf'], False)]:
    stream = io.BytesIO(cases[name][0])
    actual = helper.validate_mime_type(stream, allowed)
    assert actual is expected and stream.tell() == 0
    print('Application MIME validation:', name, actual)
print('Only wrapper:', package)
print('Private loaded DLL:', private_dll)
print('Preloaded same-named foreign DLL:', foreign_path)
print('Explicit database:', private_database)
'@
    Push-Location $hostile
    try {
        & $testPython -I -c $probe
        if ($LASTEXITCODE -ne 0) { throw 'Private libmagic isolation or MIME probe failed.' }
    } finally { Pop-Location }
    Write-Output "Experimental single-owner evidence: $run"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
