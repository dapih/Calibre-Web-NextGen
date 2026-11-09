# Install a standalone Python runtime and application dependencies locally.
param([Parameter(Mandatory = $true)][string]$AppRoot, [hashtable]$Pin)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
if (-not $Pin) { $Pin = (Get-CwngWindowsToolPins $AppRoot).python }
$runtime = Join-Path $AppRoot '.cwng-runtime'

function Install-ApplicationDependencies {
    param([string]$Interpreter)

    $build = Join-Path $AppRoot 'build'
    $metadata = Join-Path $AppRoot 'calibre_web_automated.egg-info'
    $createdBySetup = @($build, $metadata) | Where-Object { -not (Test-Path -LiteralPath $_) }
    try {
        & $Interpreter -I -m pip install --no-cache-dir --no-warn-script-location $AppRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the application dependencies.' }
        & $Interpreter -I -m pip --disable-pip-version-check --no-cache-dir check
        if ($LASTEXITCODE -ne 0) { throw 'The installed Python dependencies are inconsistent.' }
    } finally {
        foreach ($directory in $createdBySetup) {
            if ((Split-Path -Parent $directory) -eq $AppRoot -and (Test-Path -LiteralPath $directory)) {
                Remove-Item -LiteralPath $directory -Recurse -Force
            }
        }
    }
}

$updated = Install-CwngWindowsTool -RuntimeRoot $runtime -Name python -Pin $Pin -RequiredFiles @('python.exe', 'Lib\site-packages\pip\__init__.py') -Stage {
    param($work, $asset)
    $archive = Join-Path $work 'python.tar.gz'
    Write-Host 'Downloading stripped Python for this application...'
    Save-CwngWindowsDownload $asset $archive
    & (Join-Path $env:SystemRoot 'System32\tar.exe') -xzf $archive -C $work
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract the application Python runtime.' }
    Install-ApplicationDependencies (Join-Path $work 'python\python.exe')
}
if (-not $updated) {
    Install-ApplicationDependencies (Join-Path $runtime 'python\python.exe')
}
