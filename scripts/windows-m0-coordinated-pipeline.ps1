# Exercise a real disposable Calibre pipeline within the M0 coordinator job.
param([string]$IngestRuntime)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$compiler = Join-Path $cache 'mingw64\bin\gcc.exe'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$powershell = (Get-Command pwsh -ErrorAction Stop).Source
$work = Join-Path $cache ('coordinated-pipeline-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$helper = Join-Path $work 'cwng-native.dll'
& $compiler -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $helper (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('M0_HELPER','M0_WORK','M0_SCRIPTS','M0_POWERSHELL','M0_PIPELINE','M0_INGEST_RUNTIME')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:M0_HELPER = $helper; $env:M0_WORK = $work; $env:M0_SCRIPTS = $PSScriptRoot
    $env:M0_POWERSHELL = $powershell
    if ($IngestRuntime) {
        $env:M0_PIPELINE = Join-Path $PSScriptRoot 'windows-m0-ingest-boundary.ps1'
        $env:M0_INGEST_RUNTIME = (Resolve-Path -LiteralPath $IngestRuntime).Path
    } else {
        $env:M0_PIPELINE = Join-Path $PSScriptRoot 'windows-m0-pipeline.ps1'
        $env:M0_INGEST_RUNTIME = ''
    }
    $probe = @'
import hashlib, json, os, pathlib, sqlite3, sys, time
work = pathlib.Path(os.environ['M0_WORK']).resolve()
sys.path.insert(0, os.environ['M0_SCRIPTS'])
from windows_m0_coordinator import Coordinator, CoordinatorBusy
clean = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA') if key in os.environ}
clean['PATH'] = str(pathlib.Path(os.environ['SystemRoot']) / 'System32')
clean['TEMP'] = clean['TMP'] = str(work)
program_data = work / 'programdata'
program_data.mkdir()
clean['PROGRAMDATA'] = str(program_data)
result_path = work / 'pipeline-result.json'
def sha256(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
def library_book_count(db, marker=None):
    with sqlite3.connect(pathlib.Path(db).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        count = connection.execute('SELECT COUNT(*) FROM books').fetchone()[0]
        if marker is None:
            return count
        markers = connection.execute('SELECT COUNT(*) FROM identifiers WHERE type=? AND val=?',
                                     ('cwng_ingest_sha256_' + marker, marker)).fetchone()[0]
        return count, markers
def verify_result():
    result = json.loads(result_path.read_text(encoding='utf-8-sig'))
    if result.get('kind') == 'calibre':
        if sha256(result['fixture']) != result['fixture_sha256']: return False
        for library in (result['library'], result['copy']):
            db = pathlib.Path(library) / 'metadata.db'
            if not db.is_file(): return False
            with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as connection:
                row = connection.execute('SELECT title FROM books WHERE id=?', (result['book_id'],)).fetchone()
                if row != (result['title'],): return False
        return True
    for label, expected_marker in (('success', 1), ('rollback', 0)):
        scenario = result[label]
        path = pathlib.Path(scenario['candidate_paths'][-1])
        library = path.parents[2]
        incoming = library.parent / 'incoming.epub'
        if not path.is_file() or not incoming.is_file(): return False
        if sha256(path) != scenario['library_digest'] or sha256(incoming) != scenario['incoming_digest']: return False
        if library_book_count(library / 'metadata.db', scenario['incoming_digest']) != (1, expected_marker): return False
        expected = scenario['incoming_digest'] if label == 'success' else scenario['existing_digest']
        if sha256(path) != expected: return False
        backups = (library.parent / 'processed_books' / 'overwritten').rglob('*')
        if not any(p.is_file() and sha256(p) == scenario['existing_digest'] for p in backups): return False
    return True
with Coordinator(os.environ['M0_HELPER'], work) as owner:
    job_id = owner.enqueue('native-pipeline', 'pipeline-proof', {'fixture': 'sherlock_holmes.epub'})
    waiting = owner.enqueue('waiting', 'pipeline-proof', {'fixture': 'sherlock_holmes.epub'})
    args = ['-NoProfile', '-File', os.environ['M0_PIPELINE']]
    if os.environ.get('M0_INGEST_RUNTIME'):
        args += ['-Runtime', os.environ['M0_INGEST_RUNTIME'], '-ResultPath', str(result_path)]
    else:
        args += ['-TranscriptPath', str(work / 'pipeline.log'), '-ResultPath', str(result_path)]
    owner.start(job_id, os.environ['M0_POWERSHELL'], args, cwd=work, environment=clean, writer_lock_dir=work)
    try:
        owner.start(waiting, os.environ['M0_POWERSHELL'], args, cwd=work, environment=clean)
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('A second pipeline writer was admitted')
    deadline = time.monotonic() + 120
    while True:
        try:
            owner.finish(job_id, evidence='Independent disposable pipeline verification', verify=verify_result)
            break
        except CoordinatorBusy:
            if time.monotonic() >= deadline: raise TimeoutError('Pipeline did not quiesce')
            time.sleep(0.1)
    record = owner.ledger.get(job_id)
    if record['state'] != 'completed':
        raise RuntimeError('Pipeline failed or requires review: ' + repr(record) + '; log: ' + str(work / 'pipeline.log'))
print('Contained real Calibre pipeline completed and its durable record was finalized.')
'@
    $probe | & $python -I -
    if ($LASTEXITCODE -ne 0) { throw 'Coordinated pipeline proof failed.' }
    Write-Output "Coordinator evidence: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
