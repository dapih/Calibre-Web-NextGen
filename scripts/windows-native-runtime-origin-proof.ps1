# Check both direct private interpreters under hostile inherited Python values.
# This is an origin snapshot, not an installed workflow or release gate.
param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$payloadPath = (Resolve-Path -LiteralPath $Payload).Path
$traceDir = Join-Path $root ('.m0/packaged-origins-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $traceDir | Out-Null
$names = @('CWNG_M0_TRACE_DIR','CWNG_NATIVE_HELPER','PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','PATH')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$probe = @'
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'scripts'))
from windows_m0_origin import install
install(sys.argv[2])
for name in ('flask', 'magic', 'wand', 'sqlalchemy', 'tkinter', 'psutil', 'sqlite3', 'packaging'):
    importlib.import_module(name)
assert pathlib.Path(sys.executable).resolve().is_relative_to(root / 'runtime')
assert pathlib.Path(sys.prefix).resolve() == root / 'runtime'
print('Private interpreter:', sys.executable)
'@
try {
    $env:CWNG_M0_TRACE_DIR = $traceDir
    $env:CWNG_NATIVE_HELPER = Join-Path $payloadPath 'cwng-native.dll'
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:QT_PLUGIN_PATH = 'C:\invalid-host-qt'
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    Push-Location -LiteralPath $traceDir
    try {
        $probe | & (Join-Path $payloadPath 'runtime/python.exe') -I -B - $payloadPath 'root-runtime'
        if ($LASTEXITCODE -ne 0) { throw 'Root interpreter origin probe failed.' }
        $probe | & (Join-Path $payloadPath 'runtime/Scripts/python.exe') -I -B - $payloadPath 'worker-runtime'
        if ($LASTEXITCODE -ne 0) { throw 'Worker interpreter origin probe failed.' }
    } finally { Pop-Location }
    $traces = @(Get-ChildItem -LiteralPath $traceDir -Filter '*.json')
    if ($traces.Count -ne 2) { throw 'The expected origin snapshots were not recorded.' }
    foreach ($trace in $traces) {
        $data = Get-Content -LiteralPath $trace.FullName -Raw | ConvertFrom-Json
        if (@($data.unexpected_python.PSObject.Properties).Count -ne 0 -or $data.unexpected_native.Count -ne 0) {
            throw "Unexpected module origins in $($trace.Name)."
        }
        Write-Output "Private module origins passed: $($trace.Name)"
    }
    Write-Output "Evidence: $traceDir"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
