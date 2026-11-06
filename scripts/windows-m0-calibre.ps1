# Disposable Calibre executable proof; not a native app launcher.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$installer = Join-Path $cache 'calibre-proof\calibre-portable-installer-9.11.0.exe'
$tool = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre'
$fixture = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
if (-not (Test-Path -LiteralPath $installer) -or (Get-FileHash -LiteralPath $installer).Hash -ne 'F575080F60E3D1EF48A454C0DB27B7C118C060C9E841F1A1914E091AE619B23F') { throw 'Calibre installer hash mismatch.' }
foreach ($name in @('calibredb.exe','ebook-meta.exe','ebook-convert.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $tool $name))) { throw "Missing Calibre executable $name" }
}
$work = Join-Path $cache ('calibre-run-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work,(Join-Path $work 'config'),(Join-Path $work 'temp') | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','MAGICK_HOME','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:QT_PLUGIN_PATH = 'C:\invalid-host-qt'
    $env:MAGICK_HOME = 'C:\invalid-host-magick'
    $env:CALIBRE_CONFIG_DIRECTORY = Join-Path $work 'config'
    $env:TEMP = Join-Path $work 'temp'
    $env:TMP = $env:TEMP
    $before = (Get-FileHash -LiteralPath $fixture).Hash
    $version = @(& (Join-Path $tool 'calibredb.exe') --version)
    if ($LASTEXITCODE -ne 0 -or ($version -join ' ') -notmatch 'calibre 9\.11') { throw 'Unexpected Calibre version.' }
    $output = Join-Path $work 'converted.epub'
    & (Join-Path $tool 'ebook-convert.exe') $fixture $output --no-default-epub-cover *> (Join-Path $work 'conversion.log')
    if ($LASTEXITCODE -ne 0) { throw "Calibre conversion failed; see $work\conversion.log" }
    if ((Get-FileHash -LiteralPath $fixture).Hash -ne $before) { throw 'Source fixture changed.' }
    $metadata = @(& (Join-Path $tool 'ebook-meta.exe') $output)
    if ($LASTEXITCODE -ne 0 -or ($metadata -join "`n") -notmatch 'The Adventures of Sherlock Holmes' -or ($metadata -join "`n") -notmatch 'Arthur Conan Doyle') { throw 'Converted metadata check failed.' }
    $metadata
    Write-Output "Calibre conversion and metadata passed: $output"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
