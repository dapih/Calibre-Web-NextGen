# Build the two portable Windows executables and the source package for a Windows release.
param([string]$OutputPath)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputPath) { $OutputPath = Join-Path $root 'dist\windows\CWNG.exe' }
$launcher = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
$output = Split-Path -Parent $launcher
$installer = Join-Path $output 'cwng-installer.exe'
$package = Join-Path $output 'cwng-windows.zip'
$setupStyle = 'cps/static/css/library-setup-panels.css'
$overrideStyle = 'cps/static/css/caliBlur_override.css'
$framework = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'The Windows .NET Framework C# compiler is required.'
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
$common = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
    "/win32icon:$(Join-Path $root 'cps\static\favicon.ico')",
    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll'
)
$shared = Join-Path $PSScriptRoot 'WindowsInstallation.cs'
& $compiler @common "/out:$launcher" $shared (Join-Path $PSScriptRoot 'WindowsLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Could not compile CWNG.exe.' }

$installerOptions = @(
    "/out:$installer", "/resource:$launcher,CWNG.Launcher",
    "/resource:$(Join-Path $PSScriptRoot 'windows-select-library.py'),CWNG.SelectLibrary",
    "/resource:$(Join-Path $PSScriptRoot 'windows_tray_control.py'),CWNG.TrayControl",
    "/resource:$(Join-Path $root $setupStyle),CWNG.LibrarySetupStyle",
    '/reference:System.Web.Extensions.dll',
    "/reference:$(Join-Path $framework 'System.IO.Compression.dll')",
    "/reference:$(Join-Path $framework 'System.IO.Compression.FileSystem.dll')", $shared,
    (Join-Path $PSScriptRoot 'WindowsInstaller.cs')
)
& $compiler @common @installerOptions
if ($LASTEXITCODE -ne 0) { throw 'Could not compile cwng-installer.exe.' }

# Include the app and its Windows setup inputs from tracked source.
$included = @(
    'cps', 'cps.py', 'LICENSE', 'VERSION', 'dirs.json', 'pyproject.toml',
    'frontend/src', 'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
    'frontend/tsconfig.json', 'frontend/tsconfig.node.json', 'frontend/vite.config.ts',
    ':(glob)scripts/windows*.py', ':(glob)scripts/windows-*.ps1', 'scripts/windows-tool-checksums.psd1',
    'scripts/app_paths.py', 'scripts/audiobook.py', 'scripts/calibre_generate_cover.py',
    'scripts/calibre_ingest_transaction.py',
    'scripts/calibre_library_target.py', 'scripts/calibre_server_user.py', 'scripts/calibre_ticket_fulfillment.py',
    'scripts/convert_library.py', 'scripts/cover_enforcer.py', 'scripts/cwa_db.py', 'scripts/cwa_schema.sql',
    'scripts/ingest_budget.py', 'scripts/ingest_processor.py', 'scripts/kindle_epub_fixer.py',
    'scripts/library_paths.py', 'scripts/metadata_change_dispatch.py', 'scripts/script_lock.py',
    'scripts/service_user.py', 'scripts/watch_fallback.py',
    'create-windows-library.cmd', 'install-windows.cmd', 'start-windows.cmd'
)
$files = @(git -C $root -c core.quotepath=false ls-files -- $included ':(exclude)cps/static/app' ':(exclude)scripts/windows-build-launcher.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Could not list the source payload.' }

# Capture the shared version pins so the extracted package can install on its own.
. (Join-Path $PSScriptRoot 'windows-runtime.ps1')
$versions = Get-CwngSourceToolVersions $root
$versionLines = foreach ($key in ($versions.Keys | Sort-Object)) {
    "    $key = '$($versions[$key])'"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$temporary = Join-Path $output ('cwng-windows-' + [guid]::NewGuid().ToString('N') + '.zip')
try {
    $zip = [IO.Compression.ZipFile]::Open($temporary, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip, (Join-Path $root $file), $file, [IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $launcher, 'CWNG.exe', [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, (Join-Path $root 'docs/install/windows-package.md'), 'README.md', [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
        $manifest = $zip.CreateEntry('scripts/windows-tool-versions.psd1')
        $writer = [IO.StreamWriter]::new($manifest.Open())
        try {
            $writer.WriteLine((@('@{') + $versionLines + '}' -join [Environment]::NewLine))
        } finally {
            $writer.Dispose()
        }
    } finally {
        $zip.Dispose()
    }
    $zip = [IO.Compression.ZipFile]::OpenRead($temporary)
    try {
        $style = $zip.GetEntry($setupStyle)
        $override = $zip.GetEntry($overrideStyle)
        if (-not $style -or -not $override) { throw 'The package is missing the library setup styles.' }
        $reader = [IO.StreamReader]::new($override.Open())
        try {
            if ($reader.ReadLine() -ne '@import url("library-setup-panels.css");') {
                throw 'The packaged theme must load the library setup stylesheet.'
            }
        } finally { $reader.Dispose() }
    } finally { $zip.Dispose() }
    Move-Item -LiteralPath $temporary -Destination $package -Force
    Get-Item -LiteralPath $launcher,$installer,$package
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
