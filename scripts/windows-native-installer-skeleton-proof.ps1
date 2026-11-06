# Disposable compiler/installer smoke test. It never registers an uninstaller,
# creates shortcuts, installs a product payload, or touches an existing profile.
param([Parameter(Mandatory=$true)][string]$InnoCompiler)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$proofRoot = Join-Path $root '.m0'
$work = Join-Path $proofRoot ('inno-skeleton-' + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $work 'payload'
$target = Join-Path $work 'installed'
New-Item -ItemType Directory -Path $payload | Out-Null
Set-Content -LiteralPath (Join-Path $payload 'dummy.txt') -Value 'disposable installer fixture'
$bootstrap = Join-Path $work 'CWNG.exe'
$gcc = Join-Path $proofRoot 'mingw64/bin/gcc.exe'
& $gcc '-std=c11' '-municode' '-mwindows' '-Wall' '-Wextra' '-Werror' '-static' `
    '-DCWNG_INSTALLER_PROOF' '-o' $bootstrap (Join-Path $PSScriptRoot 'windows-native-bootstrap.c') `
    '-lshell32' '-luser32' '-ladvapi32'
if ($LASTEXITCODE -ne 0) { throw 'Proof bootstrap compilation failed.' }
& $InnoCompiler '--quiet' "--define=CWNGProofPayload=$payload" "--define=CWNGProofBootstrap=$bootstrap" `
    '--define=CWNGProofVersion=1.2.3' "--define=CWNGProofTarget=$target" "--output-dir=$work" (Join-Path $PSScriptRoot 'windows-native-proof.iss')
if ($LASTEXITCODE -ne 0) { throw 'Proof installer compilation failed.' }
$installer = Join-Path $work 'CWNG-Windows-Installer-PROOF.exe'
$wrongTarget = Join-Path $work 'outside-designated-location'
$wrong = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR="' + $wrongTarget + '"')) -WindowStyle Hidden -Wait -PassThru
if ($wrong.ExitCode -eq 0 -or (Test-Path -LiteralPath $wrongTarget)) { throw 'Proof installer accepted an undesignated location.' }
$args = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR="' + $target + '"'))
$first = Start-Process -FilePath $installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
if ($first.ExitCode -ne 0) { throw "Proof installation failed with exit code $($first.ExitCode)." }
if (-not (Test-Path -LiteralPath (Join-Path $target 'CWNG.exe'))) { throw 'Bootstrap not installed.' }
if (-not (Test-Path -LiteralPath (Join-Path $target 'versions/1.2.3/dummy.txt'))) { throw 'Fixture payload not installed.' }
if ((Get-Content -LiteralPath (Join-Path $target 'active-version.txt') -Raw).Trim() -ne '1.2.3') {
    throw 'Incorrect proof activation record.'
}
$repeat = Start-Process -FilePath $installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
if ($repeat.ExitCode -eq 0) { throw 'Proof installer unexpectedly overwrote an existing directory.' }
$junctionTarget = Join-Path $work 'junction-target'
$junction = Join-Path $work 'junction'
New-Item -ItemType Directory -Path $junctionTarget | Out-Null
New-Item -ItemType Junction -Path $junction -Target $junctionTarget | Out-Null
$junctionInstall = Join-Path $junction 'installed'
& $InnoCompiler '--quiet' "--define=CWNGProofPayload=$payload" "--define=CWNGProofBootstrap=$bootstrap" `
    '--define=CWNGProofVersion=1.2.3' "--define=CWNGProofTarget=$junctionInstall" `
    "--output-dir=$work" '--output-filename=CWNG-Junction-Proof' (Join-Path $PSScriptRoot 'windows-native-proof.iss')
if ($LASTEXITCODE -ne 0) { throw 'Junction proof installer compilation failed.' }
$junctionExe = Join-Path $work 'CWNG-Junction-Proof.exe'
$junctionResult = Start-Process -FilePath $junctionExe -WindowStyle Hidden -Wait -PassThru `
    -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
if ($junctionResult.ExitCode -eq 0 -or (Test-Path -LiteralPath (Join-Path $junctionTarget 'installed'))) {
    throw 'Proof installer accepted a junction ancestor.'
}
Write-Output 'Disposable Inno installation, destination override, existing-directory and junction refusal passed.'
Write-Output "Evidence: $work"
