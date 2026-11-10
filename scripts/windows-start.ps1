# Run the application with its movable, app-local Python runtime and data.
param(
    [string]$ListenAddress = '127.0.0.1',
    [switch]$ImportLegacyData,
    [switch]$TrayManaged
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.cwng-runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\windows-install.ps1 first.' }
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
    do {
        $launchArgs = @((Join-Path $root 'scripts\windows-run.py'), '-i', $ListenAddress)
        if ($ImportLegacyData) {
            $launchArgs += '--import-legacy-data'
            $ImportLegacyData = $false # Copy once; a restart uses the adopted data.
        }
        & $python -I -X utf8 @launchArgs
        $appExitCode = $LASTEXITCODE
    } while (-not $TrayManaged -and $appExitCode -eq 75) # Requested restart after Windows cleanup.
    if ($TrayManaged) { exit $appExitCode }
    if ($appExitCode -ne 0) { throw 'The application stopped with an error. See the message above.' }
} finally {
    $env:PATH = $savedPath
    $env:MAGICK_HOME = $savedMagickHome
    $env:MAGICK_CONFIGURE_PATH = $savedMagickConfig
    $env:MAGICK_CODER_MODULE_PATH = $savedMagickCoders
    Pop-Location
}
