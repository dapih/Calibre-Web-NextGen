# Install the Calibre command-line tools locally without changing the system.
param([Parameter(Mandatory=$true)][string]$RuntimeRoot)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$runtime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$target = Join-Path $runtime 'calibre'
$calibredb = Join-Path $target 'calibredb.exe'
if (Test-Path -LiteralPath $calibredb -PathType Leaf) { return }
if (Test-Path -LiteralPath $target) { throw "Calibre setup is incomplete at $target. Remove that folder and run setup again." }

# Calibre Portable refuses destination paths longer than 58 characters. Its
# temporary location must therefore be short even if the checkout is not.
$temp = [System.IO.Path]::GetTempPath().TrimEnd('\')
$temp = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($temp).ShortPath
$work = Join-Path $temp ('c' + [guid]::NewGuid().ToString('N').Substring(0, 3))
if (($work.Length + '\Calibre Portable'.Length) -gt 58) {
    throw 'The Windows temporary directory is too long for Calibre setup. Set TEMP and TMP to a shorter local folder and retry.'
}
New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null
try {
    $installer = Join-Path $work 'calibre-installer.exe'
    Write-Host 'Downloading Calibre (about 200 MB)...'
    & curl.exe --fail --location --silent --show-error --output $installer 'https://download.calibre-ebook.com/9.11.0/calibre-portable-installer-9.11.0.exe'
    if ($LASTEXITCODE -ne 0) { throw 'Calibre download failed.' }
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne 'F575080F60E3D1EF48A454C0DB27B7C118C060C9E841F1A1914E091AE619B23F') {
        throw 'The downloaded Calibre installer did not match the expected checksum.'
    }
    Write-Host 'Installing Calibre into the application folder...'
    $process = Start-Process -FilePath $installer -ArgumentList ('"' + $work + '"') -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw 'Calibre setup timed out. Check the temporary path and try again.'
    }
    if ($process.ExitCode -ne 0) { throw "Calibre setup failed with exit code $($process.ExitCode)." }
    $source = Join-Path $work 'Calibre Portable\Calibre'
    if (-not (Test-Path -LiteralPath (Join-Path $source 'calibredb.exe') -PathType Leaf)) {
        throw 'Calibre setup did not produce the expected tools.'
    }
    if ([IO.Path]::GetPathRoot($work) -ieq [IO.Path]::GetPathRoot($runtime)) {
        Move-Item -LiteralPath $source -Destination $target
    } else {
        Copy-Item -LiteralPath $source -Destination $target -Recurse
    }
} finally {
    # The directory was created by this invocation under the resolved temp root.
    if ((Split-Path -Parent $work) -eq $temp -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}
