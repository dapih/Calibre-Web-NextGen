# Exercise the built package and, when supplied, the previous published package.
param([string]$PreviousPackage)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$dist = Join-Path $repo 'dist\windows'
$installer = [Reflection.Assembly]::LoadFrom((Join-Path $dist 'cwng-installer.exe'))
$flags = [Reflection.BindingFlags]'Static, NonPublic'
$work = Join-Path ([IO.Path]::GetTempPath()) ('cwng-ci-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$root = Join-Path $work 'CWNG'
$candidate = Join-Path $work 'candidate'

function Invoke-InstallerMethod {
    param([string]$Name, [object[]]$Arguments)
    $installer.GetType('WindowsInstaller').GetMethod($Name, $flags).Invoke($null, $Arguments)
}

function Install-Package {
    param([string]$Package, [string]$Destination)
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    Invoke-InstallerMethod ExtractRelease @($Package, $Destination, $true, '') | Out-Null
    & (Join-Path $Destination 'scripts\windows-install.ps1')
    $missing = $installer.GetType('WindowsInstallation').GetMethod('MissingFile', $flags).Invoke($null, @($Destination))
    if ($missing) { throw "Windows package is missing $missing after setup." }
}

function Start-And-Check {
    param([string]$InstallRoot, [switch]$ImportBook)
    $prefix = $installer.GetType('WindowsInstallation').GetMethod('Prefix', $flags).Invoke($null, @($InstallRoot))
    $data = Join-Path $InstallRoot '.cwng-data'
    $address = Join-Path $data 'launcher-url'
    $metadata = Join-Path $InstallRoot 'library\metadata.db'
    $python = Join-Path $InstallRoot '.cwng-runtime\python\python.exe'
    $version = (Get-Content -LiteralPath (Join-Path $InstallRoot 'VERSION') -Raw).Trim()
    if (Test-Path -LiteralPath $address) { Remove-Item -LiteralPath $address }
    $process = Start-Process -FilePath (Join-Path $InstallRoot 'CWNG.exe') -ArgumentList '--background' -WindowStyle Hidden -PassThru
    try {
        $deadline = [DateTime]::UtcNow.AddMinutes(2)
        $ready = $false
        do {
            if ($process.HasExited) { throw 'The launcher exited before CWNG became healthy.' }
            if (Test-Path -LiteralPath $address) {
                $url = (Get-Content -LiteralPath $address -Raw).Trim()
                try {
                    $health = Invoke-RestMethod -Uri ($url + 'health') -TimeoutSec 5
                    $ready = $health.status -eq 'ok' -and $health.version -eq "Calibre-Web-NextGen/$version"
                } catch {
                    Write-Verbose "Waiting for CWNG health: $_"
                }
            }
            if (-not $ready) { Start-Sleep -Seconds 1 }
        } until ($ready -or [DateTime]::UtcNow -ge $deadline)
        if (-not $ready) { throw "CWNG $version did not start with the configured library." }

        if ($ImportBook) {
            Copy-Item -LiteralPath (Join-Path $repo 'tests\fixtures\sample_books\test_minimal_valid.epub') -Destination (Join-Path $data 'ingest')
        }
        $deadline = [DateTime]::UtcNow.AddMinutes(2)
        do {
            $count = & $python -I -c "import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute('select count(*) from books').fetchone()[0])" $metadata
            if ($LASTEXITCODE) { throw 'Could not read the Calibre library.' }
            if ($count -ne '1') { Start-Sleep -Seconds 2 }
        } until ($count -eq '1' -or [DateTime]::UtcNow -ge $deadline)
        if ($count -ne '1') { throw 'The library does not contain exactly one smoke-test book.' }
    } finally {
        if (-not $process.HasExited) {
            $signal = [Threading.EventWaitHandle]::OpenExisting("$prefix-quit")
            try { [void]$signal.Set() } finally { $signal.Dispose() }
            if (-not $process.WaitForExit(120000)) { throw 'CWNG did not shut down cleanly.' }
        }
    }
    if ($process.ExitCode -ne 0 -or $installer.GetType('WindowsInstallation').GetMethod('IsRunning', $flags).Invoke($null, @($InstallRoot))) {
        throw 'CWNG did not release the installation after shutdown.'
    }
    Write-Host "CWNG $version opened the library with one book and shut down cleanly."
}

try {
    $package = Join-Path $dist 'cwng-windows.zip'
    if ($PreviousPackage) {
        Install-Package $PreviousPackage $root
        Install-Package $package $candidate
    } else {
        Install-Package $package $root
    }

    $python = Join-Path $root '.cwng-runtime\python\python.exe'
    $library = Join-Path $root 'library'
    & $python -I -X utf8 (Join-Path $root 'scripts\windows-create-library.py') $library
    if ($LASTEXITCODE) { throw 'Could not create the smoke-test library.' }
    & $python -I -X utf8 (Join-Path $root 'scripts\windows-select-library.py') $library $library
    if ($LASTEXITCODE) { throw 'Could not select the smoke-test library.' }
    Start-And-Check $root -ImportBook

    if ($PreviousPackage) {
        $libraries = $installer.GetType('WindowsInstaller').GetMethod('FindLibraries', $flags).Invoke($null, @($root))
        $reviewed = [Collections.Generic.List[string]]::new([string[]]$libraries)
        $kept = [Collections.Generic.HashSet[string]]::new($reviewed, [StringComparer]::OrdinalIgnoreCase)
        Invoke-InstallerMethod ReplaceInstallation @($root, $candidate, $false, $true, $reviewed, $kept, $null) | Out-Null
        Start-And-Check $root
    }
} catch {
    foreach ($log in '.cwng-data\windows-launcher.log', '.cwng-data\config\calibre-web.log', '.cwng-data\config\background-ingest.log') {
        $path = Join-Path $root $log
        if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Tail 60 }
    }
    throw
}
