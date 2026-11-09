# Prepare app-local Node.js for building the web interface.
param([string]$RuntimeRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) '.cwng-runtime'))

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
$nodeVersion = '24.22.2'
$pin = @{
    Url = "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip"
    SHA256 = 'CA1BFF5D8EAD13C9F2EA404A176DB46BD430704D5B5113734049FA365E7799E2'
}
$null = Install-CwngWindowsTool -RuntimeRoot $RuntimeRoot -Name node -Pin $pin -RequiredFiles @('node.exe', 'npm.cmd') -Stage {
    param($work, $asset)
    $archive = Join-Path $work 'node.zip'
    Write-Host 'Downloading Node.js for the web interface...'
    Save-CwngWindowsDownload $asset $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $work
    [IO.Directory]::Move((Join-Path $work "node-v$nodeVersion-win-x64"), (Join-Path $work 'node'))
}
