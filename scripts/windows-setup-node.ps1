# Prepare Node.js locally for building the web interface. No global install.
param([string]$RuntimeRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) '.cwng-runtime'))
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$node = Join-Path $RuntimeRoot 'node'
if ((Test-Path -LiteralPath (Join-Path $node 'node.exe')) -and
    (Test-Path -LiteralPath (Join-Path $node 'npm.cmd'))) {
    return
}
if (Test-Path -LiteralPath $node) {
    throw "Incomplete Node installation at $node. Remove that folder and retry setup."
}
$work = Join-Path $RuntimeRoot ('node-setup-' + [guid]::NewGuid().ToString('N'))
$archive = Join-Path $work 'node.zip'
New-Item -ItemType Directory -Path $work | Out-Null
try {
    Write-Host 'Downloading Node.js for the web interface...'
    Invoke-WebRequest -Uri 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-win-x64.zip' -OutFile $archive -UseBasicParsing
    $expected = 'CA1BFF5D8EAD13C9F2EA404A176DB46BD430704D5B5113734049FA365E7799E2'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash -ne $expected) {
        throw 'The Node.js download did not match its expected checksum.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $work
    $source = (Resolve-Path -LiteralPath (Join-Path $work 'node-v24.22.2-win-x64')).Path
    if ((Split-Path -Parent $source) -ne $work -or (Split-Path -Parent $node) -ne $RuntimeRoot) {
        throw 'Unexpected Node.js extraction path.'
    }
    Move-Item -LiteralPath $source -Destination $node
} finally {
    if (Test-Path -LiteralPath $work) {
        $resolvedWork = (Resolve-Path -LiteralPath $work).Path
        if ($resolvedWork -ne $work -or (Split-Path -Parent $resolvedWork) -ne $RuntimeRoot) {
            throw 'Unexpected Node.js temporary path.'
        }
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}
