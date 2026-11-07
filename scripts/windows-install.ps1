# Install the application and its tools locally, without global Python or PATH changes.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $root '.cwng-runtime'
$python = & (Join-Path $PSScriptRoot 'windows-setup-python.ps1') -AppRoot $root
& $python -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Could not install the application dependencies.' }
& (Join-Path $PSScriptRoot 'windows-setup-calibre.ps1') -RuntimeRoot $runtimeRoot
& (Join-Path $PSScriptRoot 'windows-setup-tools.ps1') -RuntimeRoot $runtimeRoot

# Build the web interface with an existing Node installation or an app-local copy.
$savedPath = $env:PATH
Push-Location (Join-Path $root 'frontend')
try {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($npm -and $node) {
        $npmPath = $npm.Source
        $nodePath = $node.Source
    } else {
        & (Join-Path $PSScriptRoot 'windows-setup-node.ps1') -RuntimeRoot $runtimeRoot
        $nodeDir = Join-Path $runtimeRoot 'node'
        $env:PATH = $nodeDir + ';' + $savedPath
        $npmPath = Join-Path $nodeDir 'npm.cmd'
        $nodePath = Join-Path $nodeDir 'node.exe'
    }
    $lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'package-lock.json').Hash
    $hashMarker = 'node_modules/.cwng-lock-sha256'
    $installedHash = if (Test-Path -LiteralPath $hashMarker) {
        ([string](Get-Content -Raw -LiteralPath $hashMarker)).Trim()
    } else { '' }
    if (-not (Test-Path -LiteralPath 'node_modules/typescript/bin/tsc') -or
        -not (Test-Path -LiteralPath 'node_modules/vite/bin/vite.js') -or
        $installedHash -ne $lockHash) {
        & $npmPath ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies could not be installed.' }
        Set-Content -LiteralPath $hashMarker -Value $lockHash
    }
    # npm's Windows command shims fail when the checkout path contains &.
    & $nodePath 'node_modules/typescript/bin/tsc' -b
    if ($LASTEXITCODE -ne 0) { throw 'Frontend type checking failed.' }
    & $nodePath 'node_modules/vite/bin/vite.js' build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} catch {
    if (Test-Path -LiteralPath (Join-Path $root 'cps\static\app\index.html')) {
        Write-Warning "Could not build the frontend: $_. The existing frontend bundle remains and may be out of date."
    } else {
        Write-Warning "Could not build the frontend: $_. The classic interface will be used."
    }
} finally {
    $env:PATH = $savedPath
    Pop-Location
}
Write-Host 'Installed. Start the web application with scripts\windows-start.ps1.'
