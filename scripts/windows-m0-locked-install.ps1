# Install the experimental Windows wheel lock without network or host packages.
param([switch]$Baseline)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$wheels = Join-Path $cache 'n1-wheelhouse-locked'
$lock = Join-Path $PSScriptRoot 'windows-m0-runtime-requirements.txt'
if ($Baseline) { $lock = Join-Path $PSScriptRoot 'windows-m0-runtime-baseline-requirements.txt' }
foreach ($p in @($python, $wheels, $lock)) { if (-not (Test-Path -LiteralPath $p)) { throw "Missing prerequisite: $p" } }
$work = Join-Path $cache ('locked-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','PIP_CONFIG_FILE','TEMP','TMP','M0_MAGIC_PACKAGE','M0_PYPROJECT')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''
    $env:PYTHONPATH = ''
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:TEMP = $work
    $env:TMP = $work
    $venv = Join-Path $work 'runtime'
    & $python -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Private runtime creation failed.' }
    $testPython = Join-Path $venv 'Scripts\python.exe'
    $extraWheels = Join-Path $cache 'magic-repaired'
    $driveWheels = Join-Path $cache 'drive-repaired'
    $securityWheels = Join-Path $cache 'security-wheels'
    & $testPython -I -m pip install --no-index --no-deps --require-hashes --no-cache-dir --find-links $wheels --find-links $extraWheels --find-links $driveWheels --find-links $securityWheels -r $lock *> (Join-Path $work 'install.log')
    if ($LASTEXITCODE -ne 0) { throw "Offline locked installation failed; see $work\install.log" }
    & $testPython -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependency metadata is inconsistent.' }
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:M0_MAGIC_PACKAGE = if ($Baseline) { 'python-magic-bin' } else { 'python-magic-standalone' }
    $env:M0_PYPROJECT = if ($Baseline) { '' } else { Join-Path $root 'pyproject.toml' }
    $probe = @'
import importlib.metadata as m, os
import bibtexparser, cchardet, comicapi, magic, scholarly
import bs4.dammit
from bs4 import BeautifulSoup
if os.environ['M0_PYPROJECT']:
    import tomllib
    from packaging.requirements import Requirement
    with open(os.environ['M0_PYPROJECT'], 'rb') as stream:
        requirements = tomllib.load(stream)['project']['dependencies']
    for raw in requirements:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(): continue
        version = m.version(requirement.name)
        if version not in requirement.specifier:
            raise AssertionError(f'{requirement.name} {version} violates {requirement.specifier}')
assert bibtexparser.__version__ == '1.4.3'
package = os.environ['M0_MAGIC_PACKAGE']
assert m.version(package) == ('0.4.29+cwng.m0' if package == 'python-magic-standalone' else '0.4.14')
other = 'python-magic-bin' if package == 'python-magic-standalone' else 'python-magic-standalone'
try: m.version(other)
except m.PackageNotFoundError: pass
else: raise AssertionError('Conflicting magic distribution installed')
try: m.version('python-magic')
except m.PackageNotFoundError: pass
else: raise AssertionError('Conflicting magic wrapper installed')
assert bs4.dammit.chardet_module.__name__ == 'cchardet'
for text, encoding in [('Caf\u00e9 na\u00efve \u00e9l\u00e8ve ' * 60, 'utf-8'), ('\u65e5\u672c\u8a9e\u306e\u6587\u7ae0\u3067\u3059\u3002\u3053\u308c\u306f\u30c6\u30b9\u30c8\u3067\u3059\u3002' * 60, 'shift_jis')]:
    html = ('<html><head><title>' + text + '</title></head><body>' + text + '</body></html>').encode(encoding)
    assert BeautifulSoup(html, 'html.parser').title.string == text
print('Representative imports and cchardet-backed metadata decoding passed.')
'@
    $probe | & $testPython -I -
    if ($LASTEXITCODE -ne 0) { throw 'Representative locked-runtime checks failed.' }
    Write-Output "Private runtime evidence: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
