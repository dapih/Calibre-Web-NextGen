# Negative control for the two distributions that both own magic/__init__.py.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$inputs = Join-Path $cache 'magic-inputs'
$standard = Join-Path $inputs 'python_magic-0.4.27-py2.py3-none-any.whl'
$binary = Join-Path $inputs 'python_magic_bin-0.4.14-py2.py3-none-win_amd64.whl'
$expected = @{}
$expected[$standard] = 'C212960AD306F700AA0D01E5D7A325D20548FF97EB9920DCD29513174F0294D3'
$expected[$binary] = '90BE6206AD31071A36065A2FC169C5AFB5E0355CBE6030E87641C6C62EDC2B69'
foreach ($path in @($standard, $binary)) {
    if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected[$path]) {
        throw "Missing or changed pinned wheel: $path. Download the two named wheels to .m0/magic-inputs first."
    }
}
$names = @('PATH','PYTHONHOME','PYTHONPATH','PIP_CONFIG_FILE','TEMP','TMP','MAGIC')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$run = Join-Path $cache ('magic-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''
    $env:PYTHONPATH = ''
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:TEMP = $run
    $env:TMP = $run
    $env:MAGIC = ''
    foreach ($order in @('standard-first','binary-first')) {
        $venv = Join-Path $run $order
        & $python -I -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        $testPython = Join-Path $venv 'Scripts\python.exe'
        if ($order -eq 'standard-first') { $wheels = @($standard,$binary) }
        else { $wheels = @($binary,$standard) }
        foreach ($wheel in $wheels) {
            & $testPython -I -m pip install --no-index --no-deps $wheel
            if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
        }
        $probe = @'
import hashlib, importlib.metadata as m, pathlib, sys
expect, expected_hash = sys.argv[1:]
site = pathlib.Path(sys.prefix) / 'Lib' / 'site-packages'
init = site / 'magic' / '__init__.py'
assert hashlib.sha256(init.read_bytes()).hexdigest() == expected_hash
assert m.version('python-magic') == '0.4.27' and m.version('python-magic-bin') == '0.4.14'
try:
    import magic
except ImportError as e:
    if expect != 'fail' or 'failed to find libmagic' not in str(e): raise
    print('Expected standard-wrapper load failure:', e)
else:
    if expect != 'pass': raise AssertionError('Expected standard-wrapper load failure')
    assert pathlib.Path(magic.__file__).resolve() == init.resolve()
    assert magic.from_buffer(b'%PDF-1.4\n%fixture\n', mime=True) == 'application/pdf'
    print('Binary wrapper loaded; PDF MIME check passed:', magic.__file__)
'@
        $expectedInit = if ($order -eq 'standard-first') { $binary } else { $standard }
        $hashProbe = 'import hashlib,sys,zipfile; print(hashlib.sha256(zipfile.ZipFile(sys.argv[1]).read("magic/__init__.py")).hexdigest())'
        $hash = (& $testPython -I -c $hashProbe $expectedInit).Trim()
        $result = if ($order -eq 'standard-first') { 'pass' } else { 'fail' }
        & $testPython -I -c $probe $result $hash
        if ($LASTEXITCODE -ne 0) { throw "Unexpected $order result." }
    }
    Write-Output "Ownership collision reproduced; these probes do not select or approve a release wrapper. Evidence: $run"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
