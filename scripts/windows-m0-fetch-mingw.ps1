$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
$url = 'https://github.com/niXman/mingw-builds-binaries/releases/download/16.2.0-rt_v14-rev1/x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
$expectedSize = 107890935
$expectedHash = '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
for ($attempt = 1; $attempt -le 8; $attempt++) {
    $size = if (Test-Path -LiteralPath $archive) { (Get-Item -LiteralPath $archive).Length } else { 0 }
    if ($size -eq $expectedSize) { break }
    if ($size -gt $expectedSize) { throw 'Existing compiler archive is larger than expected; inspect it before retrying.' }
    & curl.exe -fsSL -C - -o $archive $url
    # A dropped connection can leave a resumable partial file; size and hash are checked below.
}
if (-not (Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -ne $expectedSize) { throw 'Compiler download remains incomplete.' }
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Compiler archive hash mismatch; extraction refused.' }
$compiler = Join-Path $cache 'mingw64\bin\gcc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $entries = @(& tar -tf $archive)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect compiler archive.' }
    if (@($entries | Where-Object { $_ -match '(^/|^[A-Za-z]:|(^|/)\.\.(/|$))' }).Count) { throw 'Unsafe compiler archive path; extraction refused.' }
    & tar -xf $archive -C $cache
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $compiler)) { throw 'Compiler extraction failed.' }
}
$versionOutput = @(& $compiler --version)
if ($LASTEXITCODE -ne 0) { throw 'Private compiler failed to start.' }
$versionOutput | Select-Object -First 1
Write-Output 'Archive hash verified. Extracted tools remain in the ignored .m0 workspace; N1 is still pending.'
