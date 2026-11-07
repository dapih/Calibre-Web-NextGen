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
$runtime = Join-Path $root '.cwng-runtime'
$calibre = Join-Path $runtime 'calibre'
$magick = Join-Path $runtime 'imagemagick'
$kepubify = Join-Path $runtime 'kepubify'
if (-not (Test-Path -LiteralPath (Join-Path $calibre 'calibredb.exe'))) {
    throw 'Calibre is not set up. Run scripts\windows-install.ps1 first.'
}
$savedPath = $env:PATH
$savedMagickHome = $env:MAGICK_HOME
$savedMagickConfig = $env:MAGICK_CONFIGURE_PATH
$savedMagickCoders = $env:MAGICK_CODER_MODULE_PATH
$env:PATH = $calibre + ';' + $magick + ';' + $kepubify + ';' + $savedPath
$env:MAGICK_HOME = $magick
$env:MAGICK_CONFIGURE_PATH = $magick
$env:MAGICK_CODER_MODULE_PATH = Join-Path $magick 'modules\coders'
Push-Location $root
try {
    Write-Host 'Open http://127.0.0.1:8083 in your browser. Press Ctrl+C to stop.'
    Write-Host ('Ingest folder: ' + $env:CWA_INGEST_FOLDER)
    & $python (Join-Path $root 'scripts\windows-run.py') -i $ListenAddress
} finally {
    $env:PATH = $savedPath
    $env:MAGICK_HOME = $savedMagickHome
    $env:MAGICK_CONFIGURE_PATH = $savedMagickConfig
    $env:MAGICK_CODER_MODULE_PATH = $savedMagickCoders
    Pop-Location
}
