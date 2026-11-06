# Exercise the actual Calibre ingest transaction with a disposable local library.
# PowerShell 7 preserves the metadata JSON argument when launching native tools.
param([string]$TranscriptPath, [string]$ResultPath)
if ($TranscriptPath) { Start-Transcript -Path $TranscriptPath | Out-Null }
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'This proof requires PowerShell 7.' }
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$tool = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre'
$fixture = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
$helper = Join-Path $PSScriptRoot 'calibre_ingest_transaction.py'
$work = Join-Path $cache ('p' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'lib'),(Join-Path $work 'cfg'),(Join-Path $work 'tmp') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M0Paths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetLongPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [M0Paths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short local proof path.' }
$short = $buffer.ToString()
$library = Join-Path $short 'lib'
$long = [System.Text.StringBuilder]::new(32768)
$longLength = [M0Paths]::GetLongPathName($library, $long, $long.Capacity)
if ($longLength -eq 0 -or $longLength -ge $long.Capacity -or $long.ToString() -ne (Join-Path $work 'lib')) { throw 'Short library path did not resolve to the proof library.' }
$names = @('PATH','PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'; $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:QT_PLUGIN_PATH = 'C:\invalid-host-qt'
    $env:CALIBRE_CONFIG_DIRECTORY = Join-Path $short 'cfg'
    $env:TEMP = Join-Path $short 'tmp'; $env:TMP = $env:TEMP
    $sourceHash = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLower()
    $converted = Join-Path $short 'book.epub'
    & (Join-Path $tool 'ebook-convert.exe') $fixture $converted --no-default-epub-cover *> (Join-Path $work 'conversion.log')
    if ($LASTEXITCODE -ne 0) { throw "Conversion exited $LASTEXITCODE; see $work\conversion.log" }
    $convertedHash = (Get-FileHash -LiteralPath $converted -Algorithm SHA256).Hash.ToLower()
    $arguments = @('-e', $helper, '--', '--library-path', $library, '--path', $converted,
        '--identity-path', $fixture, '--expected-import-sha256', $convertedHash,
        '--expected-source-sha256', $sourceHash, '--automerge', 'disabled',
        '--metadata-json', '{"title":"M0 Fixture","authors":"M0 Author"}')
    $first = @(& (Join-Path $tool 'calibre-debug.exe') @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Ingest failed: $($first -join "`n")" }
    $line = @($first | Where-Object { $_ -like 'CWNG_INGEST_RESULT=*' })
    if ($line.Count -ne 1) { throw 'Missing ingest transaction result.' }
    $result = ($line[0] -replace '^CWNG_INGEST_RESULT=', '') | ConvertFrom-Json
    if ($result.status -ne 'imported' -or $result.book_ids.Count -ne 1) { throw 'Unexpected import result.' }
    $again = @(& (Join-Path $tool 'calibre-debug.exe') @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Restart inspection failed: $($again -join "`n")" }
    $repeatLine = @($again | Where-Object { $_ -like 'CWNG_INGEST_RESULT=*' })
    if ($repeatLine.Count -ne 1) { throw 'Missing repeat transaction result.' }
    $repeat = ($repeatLine[0] -replace '^CWNG_INGEST_RESULT=', '') | ConvertFrom-Json
    if ($repeat.status -ne 'already_imported' -or $repeat.book_ids[0] -ne $result.book_ids[0]) { throw 'Restart did not preserve the import receipt.' }
    $listing = @(& (Join-Path $tool 'calibredb.exe') list --with-library $library --for-machine --fields 'id,title,authors' 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Calibre listing failed: $($listing -join "`n")" }
    $books = @((($listing -join "`n") | ConvertFrom-Json))
    if ($books.Count -ne 1 -or $books[0].title -ne 'M0 Fixture' -or ($books[0].authors -join ',') -notmatch 'M0 Author') { throw 'Persisted book metadata did not match.' }
    # The Calibre processes above have exited. Copy the stopped library and
    # inspect the copy without changing the source library database.
    $sourceDatabase = Join-Path $library 'metadata.db'
    $databaseHash = (Get-FileHash -LiteralPath $sourceDatabase -Algorithm SHA256).Hash
    $copy = Join-Path $short 'copy'
    Copy-Item -LiteralPath $library -Destination $copy -Recurse
    $copiedList = @(& (Join-Path $tool 'calibredb.exe') list --with-library $copy --for-machine --fields 'id,title,authors' 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Copied-library listing failed: $($copiedList -join "`n")" }
    $copiedBooks = @((($copiedList -join "`n") | ConvertFrom-Json))
    if ($copiedBooks.Count -ne 1 -or $copiedBooks[0].title -ne 'M0 Fixture' -or $copiedBooks[0].id -ne $books[0].id) { throw 'Copied library does not match the source.' }
    if ((Get-FileHash -LiteralPath $sourceDatabase -Algorithm SHA256).Hash -ne $databaseHash) { throw 'Stopped source library changed.' }
    if ((Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLower() -ne $sourceHash) { throw 'Input fixture changed.' }
    if ($ResultPath) {
        @{ kind='calibre'; library=$library; copy=$copy; fixture=$fixture; fixture_sha256=$sourceHash;
           book_id=$result.book_ids[0]; title='M0 Fixture' } | ConvertTo-Json -Compress |
            Set-Content -LiteralPath $ResultPath -Encoding utf8NoBOM
    }
    Write-Output "Converted and imported one book; a fresh process recovered its receipt and a stopped-library copy matched. Evidence: $work"
} catch {
    if ($TranscriptPath) { $_ | Out-String | Add-Content -LiteralPath ($TranscriptPath + '.error') }
    throw
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
    if ($TranscriptPath) { Stop-Transcript | Out-Null }
}
