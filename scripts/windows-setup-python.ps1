# Install a standalone Python runtime inside the application directory.
param([Parameter(Mandatory = $true)][string]$AppRoot)

$ErrorActionPreference = 'Stop'
$runtime = Join-Path $AppRoot '.cwng-runtime'
$python = Join-Path $runtime 'python\python.exe'

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

# Use this interpreter directly: venv launchers and pyvenv.cfg retain the old
# installation path when a folder moves. Dependencies live beside this runtime.
return $python
