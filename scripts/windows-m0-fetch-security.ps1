# Download only the exact private Windows wheels used by the M0 lock.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.m0\python\python.exe'
$destination = Join-Path $root '.m0\security-wheels'
$oldConfig = [Environment]::GetEnvironmentVariable('PIP_CONFIG_FILE', 'Process')
try {
    $env:PIP_CONFIG_FILE = 'NUL'
    & $python -I -m pip download --require-hashes --no-deps --only-binary=:all: --dest $destination -r (Join-Path $PSScriptRoot 'windows-m0-security-requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Security wheel download failed.' }
} finally {
    if ($null -eq $oldConfig) { Remove-Item -LiteralPath 'Env:PIP_CONFIG_FILE' -ErrorAction SilentlyContinue }
    else { $env:PIP_CONFIG_FILE = $oldConfig }
}
