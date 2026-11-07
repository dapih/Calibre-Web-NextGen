# Download the Windows tools used by the application into its local runtime.
param([string]$RuntimeRoot = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path '.cwng-runtime'))
$ErrorActionPreference = 'Stop'
$runtime = [IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$temporary = Join-Path $runtime ('tools-download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $magick = Join-Path $runtime 'imagemagick'
    if (-not (Test-Path -LiteralPath (Join-Path $magick 'CORE_RL_MagickWand_.dll'))) {
        Write-Host 'Downloading ImageMagick...'
        $setup = Join-Path $temporary 'imagemagick.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ImageMagick/ImageMagick/releases/download/7.1.2-35/ImageMagick-7.1.2-35-Q16-HDRI-x64-dll.exe' -OutFile $setup
        if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ne '32CC21FE23F7D7B7D9133905A2DC5AAC94F574A07740911D948EB88B09A0B432') {
            throw 'ImageMagick download failed its integrity check.'
        }
        # Extract the official installer rather than running it, so setup does
        # not change registry settings or the system PATH.
        $extractorArchive = Join-Path $temporary 'innoextract.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/UserUnknownFactor/innoextract_win/releases/download/670/innoextract670.zip' -OutFile $extractorArchive
        if ((Get-FileHash -LiteralPath $extractorArchive -Algorithm SHA256).Hash -ne '79B69B9B1FCD98F42CCD4B245EFDF6A03BCFB674BA6AF482F5A46891C9ED4D14') {
            throw 'ImageMagick extraction tool failed its integrity check.'
        }
        $extractor = Join-Path $temporary 'extractor'
        Expand-Archive -LiteralPath $extractorArchive -DestinationPath $extractor
        $exe = Get-ChildItem -LiteralPath $extractor -Filter 'innoextract.exe' -File -Recurse | Select-Object -First 1
        if (-not $exe) { throw 'ImageMagick extraction tool is missing.' }
        $unpacked = Join-Path $temporary 'unpacked'
        & $exe.FullName --silent --output-dir $unpacked $setup
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract ImageMagick.' }
        $app = Join-Path $unpacked 'app'
        if (-not (Test-Path -LiteralPath (Join-Path $app 'CORE_RL_MagickWand_.dll'))) {
            throw 'The ImageMagick download does not contain the required libraries.'
        }
        if (Test-Path -LiteralPath $magick) { throw 'Incomplete ImageMagick directory exists; remove it and rerun setup.' }
        Move-Item -LiteralPath $app -Destination $magick
    }
    $kepubify = Join-Path $runtime 'kepubify'
    $kepubifyExe = Join-Path $kepubify 'kepubify.exe'
    if (-not (Test-Path -LiteralPath $kepubifyExe)) {
        Write-Host 'Downloading kepubify...'
        $download = Join-Path $temporary 'kepubify.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/pgaskin/kepubify/releases/download/v4.0.4/kepubify-windows-64bit.exe' -OutFile $download
        if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne 'F3DEE3ECE063329661FEF474262FE403812335BF1676EAB35B672565BF195738') {
            throw 'kepubify download failed its integrity check.'
        }
        New-Item -ItemType Directory -Force -Path $kepubify | Out-Null
        Move-Item -LiteralPath $download -Destination $kepubifyExe
    }
} finally {
    # This directory was created by this invocation inside the runtime root.
    if ((Split-Path -Parent $temporary) -eq $runtime -and (Test-Path -LiteralPath $temporary)) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
