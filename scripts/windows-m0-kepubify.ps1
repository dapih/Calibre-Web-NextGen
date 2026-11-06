# Experimental conversion proof; the recorded upstream binary hash is not an upstream signature.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$exe = Join-Path $cache 'kepubify-proof\kepubify-windows-64bit.exe'
$python = Join-Path $cache 'python\python.exe'
$fixture = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
$hash = 'F3DEE3ECE063329661FEF474262FE403812335BF1676EAB35B672565BF195738'
if (-not (Test-Path -LiteralPath $exe) -or (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $hash) {
    throw 'Missing or changed experimental kepubify asset. See the progress record for its source and provenance limitation.'
}
$run = Join-Path $cache ('kepubify-run-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','TEMP','TMP')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:TEMP = $run
    $env:TMP = $run
    $before = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
    & $exe --version
    if ($LASTEXITCODE -ne 0) { throw 'Kepubify did not start.' }
    & $exe -o $run $fixture
    if ($LASTEXITCODE -ne 0) { throw 'Kepubify conversion failed.' }
    if ((Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash -ne $before) { throw 'Input fixture changed.' }
    $output = Join-Path $run 'sherlock_holmes_converted.kepub.epub'
    $probe = @'
import pathlib, sys, zipfile
p = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(p) as z:
    assert z.testzip() is None and z.read('mimetype') == b'application/epub+zip'
    html = [n for n in z.namelist() if n.endswith(('.html','.xhtml','.htm'))]
    spans = sum(z.read(n).count(b'koboSpan') for n in html)
    assert html and spans > 0
    print('Verified EPUB archive:', len(html), 'HTML files;', spans, 'Kobo spans')
'@
    & $python -I -c $probe $output
    if ($LASTEXITCODE -ne 0) { throw 'Converted archive validation failed.' }
    Get-FileHash -LiteralPath $output -Algorithm SHA256 | Format-List
    Write-Output "Disposable conversion output: $run"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
