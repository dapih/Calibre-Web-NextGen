# Download and verify a local compiler for the disposable Inno packaging proof.
# Nothing is installed globally or published. The NuGet archive is a third-party
# repack; verify the publisher's actual binaries and setup-loader signatures too.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$proofRoot = Join-Path $root '.m0'
if (-not (Test-Path -LiteralPath $proofRoot -PathType Container)) { throw 'Create the M0 proof directory first.' }
$work = Join-Path $proofRoot ('inno-compiler-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$archive = Join-Path $work 'compiler.nupkg'
$archiveZip = Join-Path $work 'compiler.zip'
$publisherKey = Join-Path $work 'def02.ispublickey'
Invoke-WebRequest -Uri 'https://api.nuget.org/v3-flatcontainer/tools.innosetup/7.1.0/tools.innosetup.7.1.0.nupkg' -OutFile $archive -TimeoutSec 60
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne 'AAD15C662593C6F5656457A5C001F4591BABD72820CACF63F9706D4AF5D93B75') {
    throw 'The pinned third-party compiler archive hash differs.'
}
Copy-Item -LiteralPath $archive -Destination $archiveZip
Expand-Archive -LiteralPath $archiveZip -DestinationPath (Join-Path $work 'package')
$tools = Join-Path $work 'package/tools'
$binaries = @(Get-ChildItem -LiteralPath $tools -File | Where-Object { $_.Extension -in '.exe', '.dll' })
if ($binaries.Count -eq 0 -or -not (Test-Path -LiteralPath (Join-Path $tools 'ISCC.exe'))) { throw 'Compiler binaries are missing.' }
foreach ($binary in $binaries) {
    $signature = Get-AuthenticodeSignature -LiteralPath $binary.FullName
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne 'E0AB19C8D38CBF9C44709925122A7A02F8C70CB7') {
        throw "Compiler component failed publisher-signature verification: $($binary.Name)"
    }
}
Invoke-WebRequest -Uri 'https://files.jrsoftware.org/is/misc/def02.ispublickey' -OutFile $publisherKey -TimeoutSec 30
if ((Get-FileHash -LiteralPath $publisherKey -Algorithm SHA256).Hash -ne '32BEA6BCEB4AC7C4E6B3BECDF3FB38DE77378C5E76D494AB907D87CFAB9E597B') {
    throw 'The publisher public-key hash differs.'
}
$signedFiles = @('Setup.e32', 'Setup.e64', 'SetupLdr.e32', 'SetupLdr.e64') | ForEach-Object { Join-Path $tools $_ }
& (Join-Path $tools 'ISSigTool.exe') "--key-file=$publisherKey" verify @signedFiles
if ($LASTEXITCODE -ne 0) { throw 'An Inno setup component failed publisher-signature verification.' }
Write-Output "Verified experimental compiler: $(Join-Path $tools 'ISCC.exe')"
Write-Output "Evidence: $work"
