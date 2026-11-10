# Build the Windows EXE with an embedded source payload and the inbox compiler.
param([string]$OutputPath)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputPath) { $OutputPath = Join-Path $root 'dist\windows\CWNG.exe' }
$output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
$framework = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'The Windows .NET Framework C# compiler is required to build CWNG.exe.'
}

# Use tracked source paths and read their working copies. This excludes private
# data, installed tools, developer dependencies and generated browser assets.
$included = @(
    'cps', 'scripts', 'frontend', 'koreader', 'docs/install', 'cps.py',
    'Dockerfile', 'LICENSE', 'README.md', 'VERSION', 'dirs.json', 'pyproject.toml',
    'create-windows-library.cmd', 'install-windows.cmd', 'start-windows.cmd'
)
$files = @(git -C $root -c core.quotepath=false ls-files -- $included ':(exclude)cps/static/app' ':(exclude)frontend/node_modules')
if ($LASTEXITCODE -ne 0) { throw 'Could not list the source payload.' }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
$payload = Join-Path ([IO.Path]::GetTempPath()) ("cwng-source-" + [guid]::NewGuid().ToString('N') + '.zip')
try {
    $zip = [IO.Compression.ZipFile]::Open($payload, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            $source = Join-Path $root $file
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip, $source, $file, [IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    } finally {
        $zip.Dispose()
    }
    $compilerArguments = @(
        '/nologo', '/target:winexe', '/platform:x64', '/optimize+', "/out:$output",
        "/win32icon:$(Join-Path $root 'cps\static\favicon.ico')", "/resource:$payload,CWNG.Source",
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        "/reference:$(Join-Path $framework 'System.IO.Compression.dll')",
        "/reference:$(Join-Path $framework 'System.IO.Compression.FileSystem.dll')",
        (Join-Path $PSScriptRoot 'WindowsLauncher.cs')
    )
    & $compiler @compilerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile the Windows launcher.' }
    Get-Item -LiteralPath $output
} finally {
    if (Test-Path -LiteralPath $payload) { Remove-Item -LiteralPath $payload }
}
