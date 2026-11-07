# Install Python inside the application directory and create its virtual environment.
param([Parameter(Mandatory = $true)][string]$AppRoot)

$ErrorActionPreference = 'Stop'
$runtime = Join-Path $AppRoot '.cwng-runtime'
$python = Join-Path $runtime 'python\python.exe'
$venv = Join-Path $AppRoot '.venv'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $archive = Join-Path $runtime 'python-download.tar.gz'
    $url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.13.14%2B20260623-x86_64-pc-windows-msvc-install_only.tar.gz'
    $expected = '646254B53FBAC69C1B3C25131C237C9136E9BBED7444123880CEA8DEAF555E1F'
    try {
        Write-Host 'Downloading Python for this application...'
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
            throw 'The Python download did not match its expected checksum.'
        }
        & (Join-Path $env:SystemRoot 'System32\tar.exe') -xzf $archive -C $runtime
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw 'Could not extract the application Python runtime.'
        }
    } finally {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    }
}

# Refresh an existing virtual environment as well, so it points to the local
# runtime rather than a Python installation that may later be removed.
& $python -I -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the application virtual environment.' }
return (Join-Path $venv 'Scripts\python.exe')
