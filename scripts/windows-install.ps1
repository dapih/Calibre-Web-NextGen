# Install the application into a local Python virtual environment.
# Python 3.13 and Calibre must be installed separately.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $root '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    & py -3.13 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment. Install Python 3.13 for Windows first.' }
}
& (Join-Path $venv 'Scripts\python.exe') -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Could not install the application dependencies.' }
# The classic interface works without Node. Build the newer interface when
# npm is available, without requiring Node to run the application.
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if ($npm -and $node) {
    Push-Location (Join-Path $root 'frontend')
    try {
        $lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'package-lock.json').Hash
        $hashMarker = 'node_modules/.cwng-lock-sha256'
        $installedHash = if (Test-Path -LiteralPath $hashMarker) {
            ([string](Get-Content -Raw -LiteralPath $hashMarker)).Trim()
        } else { '' }
        if (-not (Test-Path -LiteralPath 'node_modules/typescript/bin/tsc') -or
            -not (Test-Path -LiteralPath 'node_modules/vite/bin/vite.js') -or
            $installedHash -ne $lockHash) {
            & $npm.Source ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies could not be installed.' }
            Set-Content -LiteralPath $hashMarker -Value $lockHash
        }
        # npm's Windows command shims fail when the checkout path contains &.
        & $node.Source 'node_modules/typescript/bin/tsc' -b
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type checking failed.' }
        & $node.Source 'node_modules/vite/bin/vite.js' build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } catch {
        if (Test-Path -LiteralPath (Join-Path $root 'cps\static\app\index.html')) {
            Write-Warning "Could not build the frontend: $_. The existing frontend bundle remains and may be out of date."
        } else {
            Write-Warning "Could not build the frontend: $_. The classic interface will be used."
        }
    } finally {
        Pop-Location
    }
} elseif (-not (Test-Path -LiteralPath (Join-Path $root 'cps\static\app\index.html'))) {
    Write-Host 'Node.js was not found. The classic interface will be used; install Node.js and rerun setup to build the newer interface.'
}
Write-Host 'Installed. Start the web application with scripts\windows-start.ps1.'
