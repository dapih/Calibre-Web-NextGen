# Install the Calibre command-line tools locally without changing the system.
param([Parameter(Mandatory = $true)][string]$RuntimeRoot, [hashtable]$Pin)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
if (-not $Pin) { $Pin = (Get-CwngWindowsToolPins (Split-Path -Parent $PSScriptRoot)).calibre }
$null = Install-CwngWindowsTool -RuntimeRoot $RuntimeRoot -Name calibre -Pin $Pin -RequiredFiles @(
    'calibredb.exe', 'ebook-convert.exe', 'ebook-meta.exe', 'calibre-debug.exe', 'app\bin\calibre-launcher.dll'
) -Stage {
    param($work, $asset)

    # The Portable installer requires a short destination even for deep checkouts.
    $temp = [IO.Path]::GetTempPath().TrimEnd('\')
    $temp = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($temp).ShortPath
    $scratch = Join-Path $temp ('c' + [guid]::NewGuid().ToString('N').Substring(0, 3))
    if (($scratch.Length + '\Calibre Portable'.Length) -gt 58) {
        throw 'The Windows temporary directory is too long for Calibre setup. Set TEMP and TMP to a shorter local folder and retry.'
    }
    New-Item -ItemType Directory -Path $scratch | Out-Null
    try {
        $installer = Join-Path $scratch 'calibre-installer.exe'
        Write-Host 'Downloading Calibre (about 200 MB)...'
        Save-CwngWindowsDownload $asset $installer
        Write-Host 'Extracting Calibre into the application folder...'
        $process = Start-Process -FilePath $installer -ArgumentList ('"' + $scratch + '"') -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(180000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw 'Calibre setup timed out. Check the temporary path and try again.'
        }
        if ($process.ExitCode -ne 0) { throw "Calibre setup failed with exit code $($process.ExitCode)." }
        $source = Join-Path $scratch 'Calibre Portable\Calibre'
        $destination = Join-Path $work 'calibre'
        if ([IO.Path]::GetPathRoot($scratch) -ieq [IO.Path]::GetPathRoot($work)) {
            [IO.Directory]::Move($source, $destination)
        } else {
            Copy-Item -LiteralPath $source -Destination $destination -Recurse
        }
    } finally {
        if ((Split-Path -Parent $scratch) -eq $temp) { [IO.Directory]::Delete($scratch, $true) }
    }
}
