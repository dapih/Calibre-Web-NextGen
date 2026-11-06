# Run the web application using its Python virtual environment.
param([string]$ListenAddress = '127.0.0.1')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\windows-install.ps1 first.' }
$config = Join-Path $env:LOCALAPPDATA 'Calibre-Web-NextGen'
New-Item -ItemType Directory -Force -Path $config | Out-Null
$env:CALIBRE_DBPATH = $config
$env:CWA_DB_PATH = $config
$env:CWA_APP_DB_PATH = Join-Path $config 'app.db'
$env:CWA_INGEST_FOLDER = Join-Path $env:USERPROFILE 'CWNG-Ingest'
$env:CWA_TMP_CONVERSION_DIR = Join-Path $config 'temp'
New-Item -ItemType Directory -Force -Path $env:CWA_INGEST_FOLDER,$env:CWA_TMP_CONVERSION_DIR | Out-Null
$calibre = Join-Path $env:ProgramFiles 'Calibre2'
if (Test-Path -LiteralPath (Join-Path $calibre 'ebook-convert.exe')) {
    $env:PATH = $calibre + ';' + $env:PATH
}
Push-Location $root
try {
    Write-Host 'Open http://127.0.0.1:8083 in your browser. Press Ctrl+C to stop.'
    Write-Host ('Ingest folder: ' + $env:CWA_INGEST_FOLDER)
    & $python (Join-Path $root 'scripts\windows-run.py') -i $ListenAddress
} finally {
    Pop-Location
}
