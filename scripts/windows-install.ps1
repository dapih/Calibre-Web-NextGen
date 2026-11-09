# Install the application and its tools locally, without global Python or PATH changes.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $root '.cwng-runtime'
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
$pins = Get-CwngWindowsToolPins $root

# Hold the same byte lock as the portable launcher throughout setup.
$data = Join-Path $root '.cwng-data'
New-Item -ItemType Directory -Force -Path $data | Out-Null
$lockPath = Join-Path $data 'running.lock'
$lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
try {
    try {
        $lock.Lock(0, 1)
    } catch [IO.IOException] {
        throw 'This installation is running or another setup is in progress. Stop it before running setup.'
    }

    & (Join-Path $PSScriptRoot 'windows-setup-python.ps1') -AppRoot $root -Pin $pins.python
    & (Join-Path $PSScriptRoot 'windows-setup-calibre.ps1') -RuntimeRoot $runtimeRoot -Pin $pins.calibre
    & (Join-Path $PSScriptRoot 'windows-setup-tools.ps1') -RuntimeRoot $runtimeRoot -KepubifyPin $pins.kepubify

    # Build the web interface with an existing Node installation or an app-local copy.
    $savedPath = $env:PATH
    $nodeDir = Join-Path $runtimeRoot 'node'
    $nodeModules = Join-Path $root 'frontend\node_modules'
    $hadNode = Test-Path -LiteralPath $nodeDir
    $hadNodeModules = Test-Path -LiteralPath $nodeModules
    $frontend = Join-Path $root 'frontend'
    $newCompilerOutputs = @('tsconfig.tsbuildinfo', 'tsconfig.node.tsbuildinfo', 'vite.config.js', 'vite.config.d.ts') |
        ForEach-Object { Join-Path $frontend $_ } |
        Where-Object { -not (Test-Path -LiteralPath $_) }
    Push-Location (Join-Path $root 'frontend')
    try {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        $node = Get-Command node.exe -ErrorAction SilentlyContinue
        if ($npm -and $node) {
            $npmPath = $npm.Source
            $nodePath = $node.Source
        } else {
            & (Join-Path $PSScriptRoot 'windows-setup-node.ps1') -RuntimeRoot $runtimeRoot
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
        # Keep developer-owned tools, but remove build dependencies created by setup.
        if (-not $hadNodeModules -and (Test-Path -LiteralPath $nodeModules)) {
            Remove-Item -LiteralPath $nodeModules -Recurse -Force
        }
        if (-not $hadNode -and (Test-Path -LiteralPath $nodeDir)) {
            Remove-Item -LiteralPath $nodeDir -Recurse -Force
        }
        foreach ($output in $newCompilerOutputs) {
            if ((Split-Path -Parent $output) -eq $frontend -and (Test-Path -LiteralPath $output)) {
                Remove-Item -LiteralPath $output -Force
            }
        }
    }
    Write-Host 'Installed. Start the web application with start-windows.cmd.'
} finally {
    $lock.Dispose()
}
