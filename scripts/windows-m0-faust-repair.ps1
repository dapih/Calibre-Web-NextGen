# Experimental local proof, not a release build or license approval.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$compiler = Join-Path $cache 'mingw64\bin'
$rawWheel = Join-Path $cache 'wheels\faust_cchardet-2.1.19-cp313-cp313-win_amd64.whl'
$requirements = Join-Path $PSScriptRoot 'windows-m0-faust-repair-requirements.txt'
foreach ($path in @($python, $rawWheel, (Join-Path $compiler 'libgcc_s_seh-1.dll'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing $path; run the MinGW build probe first." }
}
$expectedInputs = @{}
$expectedInputs[$rawWheel] = 'CFBDF7014DD46935419595340B6FE6EA865E95A752B7D360C7AA685AB50B7D10'
$expectedInputs[(Join-Path $compiler 'libgcc_s_seh-1.dll')] = '84BBA68E880017AB8B041B343E928A6D917F7601071B74530F334B2667A8913E'
$expectedInputs[(Join-Path $compiler 'libstdc++-6.dll')] = 'A85B34E1E12778C5B4EFB20DF23449A1664FD211F24F1C8A9113A0B608CB2F18'
$expectedInputs[(Join-Path $compiler 'libwinpthread-1.dll')] = '5024707B039416C95BC524D4FC6ED6861A66E98B083B5160CCCF81DAF3C47C7E'
foreach ($path in $expectedInputs.Keys) {
    if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expectedInputs[$path]) {
        throw "Missing or changed locked repair input: $path"
    }
}
$envNames = @('PATH','PYTHONHOME','PYTHONPATH','PIP_CONFIG_FILE','PIP_CACHE_DIR','TEMP','TMP','M0_FOREIGN_BIN')
$saved = @{}
foreach ($name in $envNames) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$run = Join-Path $cache ('repair-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''
    $env:PYTHONPATH = ''
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
    $env:TEMP = Join-Path $run 'temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP | Out-Null
    $repairEnv = Join-Path $run 'repair-env'
    $testEnv = Join-Path $run 'test-env'
    & $python -I -m venv $repairEnv
    if ($LASTEXITCODE -ne 0) { throw 'Repair environment creation failed.' }
    $repairPython = Join-Path $repairEnv 'Scripts\python.exe'
    & $repairPython -I -m pip install --require-hashes --only-binary=:all: --no-deps -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Hash-locked repair dependencies failed.' }
    & $repairPython -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Repair dependencies are incomplete.' }
    $output = Join-Path $run 'wheel'
    & $repairPython -I -m delvewheel repair --add-path $compiler -w $output $rawWheel
    if ($LASTEXITCODE -ne 0) { throw 'Wheel repair failed.' }
    $repairedWheel = Join-Path $output (Split-Path $rawWheel -Leaf)
    $wheel = Join-Path (Join-Path $run 'normalized') (Split-Path $rawWheel -Leaf)
    New-Item -ItemType Directory -Path (Split-Path $wheel) | Out-Null
    # Keep the original repaired wheel, including invocation metadata, as evidence.
    # Canonicalize only the distribution artifact, then regenerate its RECORD.
    $normalize = @'
import base64, csv, hashlib, io, stat, sys, zipfile
from pathlib import PurePosixPath
source, target = sys.argv[1:]
with zipfile.ZipFile(source) as z:
    entries = [i for i in z.infolist() if not i.is_dir()]
    names = [i.filename for i in entries]
    if len(names) != len(set(names)): raise ValueError('Duplicate wheel entry')
    for i in entries:
        p = PurePosixPath(i.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in i.filename or stat.S_ISLNK(i.external_attr >> 16):
            raise ValueError('Unsafe wheel entry: ' + i.filename)
    records = [n for n in names if n.endswith('.dist-info/RECORD')]
    if len(records) != 1: raise ValueError('Expected exactly one RECORD')
    record = records[0]
    if any(n in names for n in (record + '.jws', record + '.p7s')): raise ValueError('Signed RECORD cannot be rewritten')
    metadata = record.rsplit('/', 1)[0] + '/DELVEWHEEL'
    if metadata not in names: raise ValueError('Missing DELVEWHEEL metadata')
    contents = {i.filename: z.read(i) for i in entries}
    rows = list(csv.reader(io.StringIO(contents[record].decode('utf-8'), newline='')))
    if len(rows) != len(names) or len({r[0] for r in rows}) != len(rows) or {r[0] for r in rows} != set(names):
        raise ValueError('RECORD does not match wheel members')
    def digest(data):
        return 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode('ascii')
    for row in rows:
        if len(row) != 3: raise ValueError('Invalid RECORD row')
        name, h, size = row
        if name == record:
            if h or size: raise ValueError('Invalid RECORD self entry')
        elif h != digest(contents[name]) or size != str(len(contents[name])):
            raise ValueError('RECORD hash or size mismatch: ' + name)
    modes = {i.filename: (0o755 if (i.external_attr >> 16) & 0o111 else 0o644) for i in entries}
    if not contents[metadata].startswith(b'Version: 1.13.2\n'): raise ValueError('Unexpected repair tool metadata')
    contents[metadata] = b'Version: 1.13.2\nArguments: normalized; see external build evidence\n'
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    for name in sorted(n for n in names if n != record):
        writer.writerow((name, digest(contents[name]), len(contents[name])))
    writer.writerow((record, '', ''))
    contents[record] = text.getvalue().encode('utf-8')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as out:
        for name in sorted(names):
            info = zipfile.ZipInfo(name, (2026, 6, 22, 20, 26, 40))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | modes[name]) << 16
            out.writestr(info, contents[name])
'@
    & $repairPython -I -c $normalize $repairedWheel $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Wheel normalization or RECORD validation failed.' }
    & $python -I -m venv $testEnv
    if ($LASTEXITCODE -ne 0) { throw 'Test environment creation failed.' }
    $testPython = Join-Path $testEnv 'Scripts\python.exe'
    & $testPython -I -m pip install --no-index --no-deps $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Repaired wheel installation failed.' }
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:M0_FOREIGN_BIN = $compiler
    $probe = @'
import ctypes, importlib.metadata, os
from pathlib import Path
foreign = Path(os.environ['M0_FOREIGN_BIN']).resolve()
names = ('libwinpthread-1.dll', 'libgcc_s_seh-1.dll', 'libstdc++-6.dll')
with os.add_dll_directory(str(foreign)):
    preloaded = [ctypes.WinDLL(str(foreign / name)) for name in names]
import cchardet, cchardet._cchardet
assert importlib.metadata.version('faust-cchardet') == '2.1.19'
package = Path(cchardet.__file__).resolve().parent
libs = package.parent / 'faust_cchardet.libs'
assert Path(cchardet._cchardet.__file__).resolve().parent == package
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [ctypes.c_wchar_p], ctypes.c_void_p
k.GetModuleFileNameW.argtypes, k.GetModuleFileNameW.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong], ctypes.c_ulong
def origin(handle):
    path = ctypes.create_unicode_buffer(32768)
    if not k.GetModuleFileNameW(handle, path, len(path)): raise ctypes.WinError(ctypes.get_last_error())
    return Path(path.value).resolve()
for name, loaded in zip(names, preloaded):
    assert k.GetModuleHandleW(name) == loaded._handle
    assert origin(loaded._handle) == foreign / name
vendored = list(libs.glob('*.dll'))
assert len(vendored) == 3
for stem in ('libwinpthread-1-', 'libgcc_s_seh-1-', 'libstdc++-6-'):
    matches = [p for p in vendored if p.name.lower().startswith(stem)]
    assert len(matches) == 1
    handle = k.GetModuleHandleW(matches[0].name)
    assert handle and origin(handle) == matches[0].resolve()
    print('Private loaded DLL:', origin(handle))
cases = {
    'ascii': ('ordinary metadata text ' * 40, 'ascii', {'ascii'}),
    'utf8': ('Caf\u00e9 na\u00efve \u00e9l\u00e8ve \u2014 voil\u00e0. ' * 40, 'utf-8', {'utf-8'}),
    'shift_jis': ('\u65e5\u672c\u8a9e\u306e\u6587\u7ae0\u3067\u3059\u3002\u3053\u308c\u306f\u6587\u5b57\u30b3\u30fc\u30c9\u306e\u5224\u5b9a\u3092\u8a66\u3057\u3066\u3044\u307e\u3059\u3002' * 40, 'shift_jis', {'shift_jis'}),
    'cp1251': ('\u042d\u0442\u043e \u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u043a\u043e\u0434\u0438\u0440\u043e\u0432\u043a\u0438 \u0438 \u043c\u0435\u0442\u0430\u0434\u0430\u043d\u043d\u044b\u0445. ' * 40, 'cp1251', {'windows-1251'}),
}
for label, (text, encoding, expected) in cases.items():
    raw = text.encode(encoding)
    result = cchardet.detect(raw)
    print(label, result)
    assert result['encoding'].lower() in expected
    assert raw.decode(result['encoding']) == text
print('Conflicting original DLL names were preloaded from:', foreign)
'@
    Push-Location $run
    try {
        & $testPython -I -c $probe
        if ($LASTEXITCODE -ne 0) { throw 'Repaired wheel collision or encoding probe failed.' }
    } finally { Pop-Location }
    Get-FileHash -LiteralPath $rawWheel,$wheel -Algorithm SHA256 | Format-List
    Write-Output "Disposable proof files: $run"
    Write-Output 'N1 and N2-N6 remain pending; this is not a distributable release.'
} finally {
    foreach ($name in $envNames) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
