# Install the application into a local Python virtual environment.
# Python 3.13 and Calibre must be installed separately.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $root '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    & py -3.13 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment. Install Python 3.13 for Windows first.' }
}
& (Join-Path $venv 'Scripts\python.exe') -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Could not install the application dependencies.' }
Write-Host 'Installed. Start the web application with scripts\windows-start.ps1.'
