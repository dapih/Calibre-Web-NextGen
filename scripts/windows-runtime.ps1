# Shared helpers for provisioning app-local Windows tools.
function Get-CwngWindowsToolPins {
    param([string]$AppRoot)

    $versions = @{}
    foreach ($line in (Get-Content -LiteralPath (Join-Path $AppRoot 'Dockerfile'))) {
        if ($line -match '^FROM\s') { break }
        if ($line -cmatch '^ARG (PYTHON_VERSION|PYTHON_BUILD_STANDALONE_RELEASE|CALIBRE_RELEASE|KEPUBIFY_RELEASE)=(v?[0-9][0-9a-zA-Z.+-]*)\s*$') {
            $versions[$Matches[1]] = $Matches[2]
        }
    }
    foreach ($key in @('PYTHON_VERSION', 'PYTHON_BUILD_STANDALONE_RELEASE', 'CALIBRE_RELEASE', 'KEPUBIFY_RELEASE')) {
        if (-not $versions.ContainsKey($key)) { throw "Missing shared $key pin in Dockerfile." }
    }
    $checksums = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'windows-tool-checksums.psd1')
    $pbs = $versions.PYTHON_BUILD_STANDALONE_RELEASE
    $python = "cpython-$($versions.PYTHON_VERSION)+${pbs}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    $calibre = "calibre-portable-installer-$($versions.CALIBRE_RELEASE).exe"
    $kepubify = "$($versions.KEPUBIFY_RELEASE)/kepubify-windows-64bit.exe"
    foreach ($asset in @($python, $calibre, $kepubify)) {
        if ($checksums[$asset] -notmatch '^[0-9a-f]{64}$') {
            throw "Missing Windows SHA-256 for $asset. Update scripts/windows-tool-checksums.psd1 for the new Dockerfile pin."
        }
    }

    @{
        python = @{
            Url = "https://github.com/astral-sh/python-build-standalone/releases/download/$pbs/$([Uri]::EscapeDataString($python))"
            SHA256 = $checksums[$python]
        }
        calibre = @{
            Url = "https://download.calibre-ebook.com/$($versions.CALIBRE_RELEASE)/$calibre"
            SHA256 = $checksums[$calibre]
        }
        kepubify = @{
            Url = "https://github.com/pgaskin/kepubify/releases/download/$kepubify"
            SHA256 = $checksums[$kepubify]
        }
    }
}

function Save-CwngWindowsDownload {
    param([hashtable]$Pin, [string]$Destination)

    & curl.exe --disable --fail --location --silent --show-error --retry 2 --retry-all-errors --connect-timeout 30 --output $Destination $Pin.Url
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Retrying the download with Windows PowerShell...'
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri $Pin.Url -OutFile $Destination
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Pin.SHA256) {
        throw "SHA-256 verification failed for $($Pin.Url)."
    }
}

function Install-CwngWindowsTool {
    param(
        [string]$RuntimeRoot,
        [ValidateSet('python', 'calibre', 'imagemagick', 'kepubify', 'node')][string]$Name,
        [hashtable]$Pin,
        [string[]]$RequiredFiles,
        [scriptblock]$Stage
    )

    $runtime = [IO.Path]::GetFullPath($RuntimeRoot)
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $target = Join-Path $runtime $Name
    $previous = Join-Path $runtime ".$Name-previous"
    foreach ($directory in @($runtime, $target, $previous)) {
        $item = Get-Item -LiteralPath $directory -Force -ErrorAction SilentlyContinue
        if ($item -and (-not $item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Expected an ordinary directory at $directory."
        }
    }

    # Recover a prior installation if setup was interrupted between renames.
    if ([IO.Directory]::Exists($previous) -and -not [IO.Directory]::Exists($target)) {
        [IO.Directory]::Move($previous, $target)
    }
    $receipt = '.cwng-asset'
    $identity = "$($Pin.Url) $($Pin.SHA256)"
    $marker = Join-Path $target $receipt
    if ((Test-Path -LiteralPath $marker -PathType Leaf) -and
        ([string](Get-Content -LiteralPath $marker -Raw)).Trim() -eq $identity -and
        -not ($RequiredFiles | Where-Object { -not [IO.File]::Exists((Join-Path $target $_)) })) {
        if ([IO.Directory]::Exists($previous)) { [IO.Directory]::Delete($previous, $true) }
        return $false
    }

    $work = Join-Path $runtime (".$Name-stage-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        & $Stage $work $Pin | Out-Host
        $source = Join-Path $work $Name
        foreach ($file in $RequiredFiles) {
            if (-not [IO.File]::Exists((Join-Path $source $file))) { throw "Staged $Name is missing $file." }
        }
        Set-Content -LiteralPath (Join-Path $source $receipt) -Value $identity -Encoding ASCII

        if ([IO.Directory]::Exists($previous)) { [IO.Directory]::Delete($previous, $true) }
        if ([IO.Directory]::Exists($target)) { [IO.Directory]::Move($target, $previous) }
        try {
            [IO.Directory]::Move($source, $target)
        } catch {
            if ([IO.Directory]::Exists($previous) -and -not [IO.Directory]::Exists($target)) {
                [IO.Directory]::Move($previous, $target)
            }
            throw
        }
        if ([IO.Directory]::Exists($previous)) { [IO.Directory]::Delete($previous, $true) }
        return $true
    } finally {
        # Both paths used for recursive cleanup are direct, validated runtime children.
        [IO.Directory]::Delete($work, $true)
    }
}
