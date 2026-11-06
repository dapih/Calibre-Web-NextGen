# Run the existing real ingest overwrite/rollback boundary with private Calibre.
param([Parameter(Mandatory=$true)][string]$Runtime, [string]$ResultPath)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$runtime = (Resolve-Path -LiteralPath $Runtime).Path
$python = Join-Path $runtime 'Scripts\python.exe'
$tool = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre'
$work = Join-Path $cache ('i' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'cfg') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M0IngestPaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [M0IngestPaths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short proof path.' }
$short = $buffer.ToString()
$names = @('PATH','PYTHONHOME','PYTHONPATH','CALIBRE_DBPATH','CWA_METADATA_LOCK_DIR','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP','M0_ROOT','M0_WORK','CWNG_CALIBRE_TOOLS')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = $tool + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:CWNG_CALIBRE_TOOLS = $tool
    $env:PYTHONHOME = 'C:\invalid-host-python'; $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:CALIBRE_DBPATH = Join-Path $short 'cfg'
    $env:CWA_METADATA_LOCK_DIR = if ($env:CWNG_NATIVE_GATE_DIR) { $env:CWNG_NATIVE_GATE_DIR } else { $env:CALIBRE_DBPATH }
    $env:CALIBRE_CONFIG_DIRECTORY = Join-Path $short 'cfg'
    $env:TEMP = $short; $env:TMP = $short
    $env:M0_ROOT = $root; $env:M0_WORK = $short
    $probe = @'
import importlib.util, json, os, pathlib, sys
root, work = pathlib.Path(os.environ['M0_ROOT']), pathlib.Path(os.environ['M0_WORK'])
sys.path.insert(0, str(root / 'scripts'))
import ingest_processor
spec = importlib.util.spec_from_file_location('ingest_runtime_probe', root / 'tests/integration/calibre_ingest_runtime_probe.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
fixture = root / 'tests/fixtures/sample_books/sherlock_holmes.epub'
success = probe._scenario(ingest_processor, fixture, work / 'ok', inject_rollback=False)
rollback = probe._scenario(ingest_processor, fixture, work / 'fail', inject_rollback=True)
(work / 'results.json').write_text(json.dumps({'success':success,'rollback':rollback}, indent=2), encoding='utf-8')
print('Real ingest overwrite and injected rollback preserved their expected book and source states.')
'@
    $probe | & $python -I -
    if ($LASTEXITCODE -ne 0) { throw "Ingest boundary proof failed: $work" }
    if ($ResultPath) { Copy-Item -LiteralPath (Join-Path $work 'results.json') -Destination $ResultPath }
    Write-Output "Ingest boundary proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
