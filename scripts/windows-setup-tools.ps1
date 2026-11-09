# Install ImageMagick and kepubify into the local runtime.
param([string]$RuntimeRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) '.cwng-runtime'), [hashtable]$KepubifyPin)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
if (-not $KepubifyPin) { $KepubifyPin = (Get-CwngWindowsToolPins (Split-Path -Parent $PSScriptRoot)).kepubify }

$magickPin = @{
    Url = 'https://github.com/ImageMagick/ImageMagick/releases/download/7.1.2-35/ImageMagick-7.1.2-35-Q16-HDRI-x64-dll.exe'
    SHA256 = '32CC21FE23F7D7B7D9133905A2DC5AAC94F574A07740911D948EB88B09A0B432'
}
$null = Install-CwngWindowsTool -RuntimeRoot $RuntimeRoot -Name imagemagick -Pin $magickPin -RequiredFiles @(
    'magick.exe', 'CORE_RL_MagickWand_.dll'
) -Stage {
    param($work, $asset)
    Write-Host 'Downloading ImageMagick...'
    $installer = Join-Path $work 'imagemagick.exe'
    Save-CwngWindowsDownload $asset $installer

    # Extract the official installer without changing the registry or system PATH.
    $extractorPin = @{
        Url = 'https://github.com/UserUnknownFactor/innoextract_win/releases/download/670/innoextract670.zip'
        SHA256 = '79B69B9B1FCD98F42CCD4B245EFDF6A03BCFB674BA6AF482F5A46891C9ED4D14'
    }
    $extractorArchive = Join-Path $work 'innoextract.zip'
    Save-CwngWindowsDownload $extractorPin $extractorArchive
    $extractor = Join-Path $work 'extractor'
    Expand-Archive -LiteralPath $extractorArchive -DestinationPath $extractor
    $exe = Get-ChildItem -LiteralPath $extractor -Filter 'innoextract.exe' -File -Recurse | Select-Object -First 1
    if (-not $exe) { throw 'ImageMagick extraction tool is missing.' }
    $unpacked = Join-Path $work 'unpacked'
    & $exe.FullName --silent --output-dir $unpacked $installer
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract ImageMagick.' }
    [IO.Directory]::Move((Join-Path $unpacked 'app'), (Join-Path $work 'imagemagick'))
}

$null = Install-CwngWindowsTool -RuntimeRoot $RuntimeRoot -Name kepubify -Pin $KepubifyPin -RequiredFiles 'kepubify.exe' -Stage {
    param($work, $asset)
    Write-Host 'Downloading kepubify...'
    $directory = Join-Path $work 'kepubify'
    New-Item -ItemType Directory -Path $directory | Out-Null
    Save-CwngWindowsDownload $asset (Join-Path $directory 'kepubify.exe')
}
