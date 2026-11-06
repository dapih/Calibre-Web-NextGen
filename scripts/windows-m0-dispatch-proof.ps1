# Exercise admission -> real ingest entrypoint -> independent verification.
param([Parameter(Mandatory=$true)][string]$Runtime)
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'This proof requires PowerShell 7.' }
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$runtime = (Resolve-Path -LiteralPath $Runtime).Path
$python = Join-Path $runtime 'Scripts\python.exe'
$tool = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$work = Join-Path $cache ('d' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'state'),(Join-Path $work 'state\staged'),(Join-Path $work 'incoming'),(Join-Path $work 'lib'),(Join-Path $work 'source'),(Join-Path $work 'cfg'),(Join-Path $work 'tmp') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M0DispatchPaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [M0DispatchPaths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short proof path.' }
$short = $buffer.ToString()
$native = Join-Path $work 'cwng-native.dll'
& (Join-Path $cache 'mingw64\bin\gcc.exe') -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $native (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('PATH','PYTHONHOME','PYTHONPATH','CALIBRE_DBPATH','CWA_DB_PATH','CWA_APP_DB_PATH','CWA_INGEST_FOLDER','CWA_CALIBRE_LIBRARY_DIR','CWA_TMP_CONVERSION_DIR','CWA_METADATA_LOCK_DIR','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP','M0_ROOT','M0_WORK','M0_RUNTIME','M0_NATIVE')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = $tool + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''; $env:PYTHONPATH = ''
    $env:CALIBRE_DBPATH = Join-Path $short 'cfg'; $env:CWA_DB_PATH = $env:CALIBRE_DBPATH
    $env:CWA_APP_DB_PATH = Join-Path $env:CALIBRE_DBPATH 'app.db'
    $env:CWA_INGEST_FOLDER = Join-Path $short 'incoming'
    $env:CWA_CALIBRE_LIBRARY_DIR = Join-Path $short 'lib'
    $env:CWA_TMP_CONVERSION_DIR = Join-Path $short 'tmp'
    $env:CWA_METADATA_LOCK_DIR = $env:CALIBRE_DBPATH
    $env:CALIBRE_CONFIG_DIRECTORY = $env:CALIBRE_DBPATH
    $env:TEMP = $env:CWA_TMP_CONVERSION_DIR; $env:TMP = $env:TEMP
    $env:M0_ROOT = $root; $env:M0_WORK = $short; $env:M0_RUNTIME = $runtime; $env:M0_NATIVE = $native
    $env:CWA_CALIBRE_LIBRARY_DIR = Join-Path $short 'source'
    & (Join-Path $tool 'calibre-debug.exe') -c 'import os; from calibre.db.legacy import LibraryDatabase; db=LibraryDatabase(os.environ["CWA_CALIBRE_LIBRARY_DIR"]); db.close()'
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize disposable source library.' }
    Copy-Item -Path (Join-Path $env:CWA_CALIBRE_LIBRARY_DIR '*') -Destination (Join-Path $short 'lib') -Recurse
    $env:CWA_CALIBRE_LIBRARY_DIR = Join-Path $short 'lib'
    $probe = @'
import hashlib, json, os, pathlib, secrets, shutil, socket, sqlite3, subprocess, sys, time, zipfile
root, work = pathlib.Path(os.environ['M0_ROOT']), pathlib.Path(os.environ['M0_WORK'])
source_database = work / 'source' / 'metadata.db'
source_hash = hashlib.sha256(source_database.read_bytes()).hexdigest()
sys.path.insert(0, str(root / 'scripts'))
from cwa_db import CWA_DB
cwa = CWA_DB()
cwa.cur.execute("UPDATE cwa_settings SET auto_convert=1, auto_convert_target_format='epub', kindle_epub_fixer=0, auto_backup_imports=1, auto_backup_conversions=0, auto_metadata_enforcement=0")
cwa.con.commit(); cwa.con.close()
# An ordinary file at the requested backup directory forces backup failure.
backup_root = work / 'cfg' / 'processed_books'
backup_root.mkdir(exist_ok=True)
(backup_root / 'imported').write_text('not a directory', encoding='utf-8')
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_activation import LocalOwner
from windows_m0_inbox import Inbox
from windows_m0_dispatch import IngestDispatcher, EnforcementDispatcher
from windows_m0_scheduler import IngestScheduler
source = pathlib.Path(os.environ['CWA_INGEST_FOLDER']) / 'example.txt'
source.write_text('A small private test book.\n\n' * 200, encoding='utf-8')
original = source.read_bytes()
from windows_m0_job import Job
from windows_m0_readiness import OwnedReadiness
reservation = socket.socket(); reservation.bind(('127.0.0.1', 0))
port = reservation.getsockname()[1]; reservation.close()
key = secrets.token_bytes(32)
proof_password = secrets.token_urlsafe(24)
identity = str((work / 'lib' / 'metadata.db').stat().st_dev) + ':' + str((work / 'lib' / 'metadata.db').stat().st_ino)
wanted = {
    'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMDATA',
    'PATH','CALIBRE_DBPATH','CWA_DB_PATH','CWA_APP_DB_PATH','CWA_INGEST_FOLDER','CWA_CALIBRE_LIBRARY_DIR',
    'CWA_TMP_CONVERSION_DIR','CWA_METADATA_LOCK_DIR','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP'}
environment = {key:value for key,value in os.environ.items() if key.upper() in wanted}
environment['CWNG_CALIBRE_TOOLS'] = str(root / '.m0' / 'calibre-proof' / 'extracted' / 'Calibre Portable' / 'Calibre')
environment['CWNG_KEPUBIFY_PATH'] = str(root / '.m0' / 'kepubify-proof' / 'kepubify-windows-64bit.exe')
trace_dir = work / 'state' / 'origins'
trace_dir.mkdir()
environment['CWNG_M0_TRACE_DIR'] = str(trace_dir)
environment['HOME'] = environment['USERPROFILE'] = environment['APPDATA'] = environment['LOCALAPPDATA'] = str(work / 'cfg')
environment['CALIBRE_CACHE_DIRECTORY'] = str(work / 'cfg' / 'calibre-cache')
# Windows executable resolution searches the child's working directory before
# PATH. Non-executables with the same names expose accidental bare-name calls.
for tool_name in ('ebook-convert', 'calibre-debug', 'calibredb', 'ebook-meta', 'ebook-polish', 'kepubify'):
    (work / 'state' / (tool_name + '.exe')).write_bytes(b'not a private executable')
environment['PATH'] = str(work / 'state') + ';' + environment['PATH']
environment['CWA_PORT_OVERRIDE'] = str(port)
magick = root / '.m0' / 'imagemagick-proof' / 'dynamic' / 'app'
server_environment = dict(environment, M0_READY_KEY=key.hex(), M0_RUNTIME=os.environ['M0_RUNTIME'], M0_PROOF_PASSWORD=proof_password,
                          CWNG_M0_PROOF='1', CWNG_NATIVE_MODE='1', CWNG_NATIVE_STATE_DIR=str((work/'state').resolve()), M0_PROOF_KEPUBIFY=str(root/'.m0'/'kepubify-proof'/'kepubify-windows-64bit.exe'), MAGICK_HOME=str(magick),
                          MAGICK_CODER_MODULE_PATH=str(magick / 'modules' / 'coders'))
server_environment['PATH'] = str(magick) + ';' + server_environment['PATH']
web_job = Job(os.environ['M0_NATIVE'])
local_owner = LocalOwner(os.environ['M0_NATIVE'], (work / 'state').resolve())
import atexit
atexit.register(local_owner.close)
web_process = web_job.spawn(pathlib.Path(os.environ['M0_RUNTIME']) / 'Scripts' / 'python.exe',
                            ['-I',str(root / 'scripts' / 'windows_m0_web_proof_worker.py'),str(root),str(work)],
                            cwd=work,environment=server_environment)
readiness = OwnedReadiness(web_job,web_process,port,key,identity)
assert not readiness.verify()
clock = [0.0]
with local_owner:
    owner = local_owner.owner
    inbox = Inbox(owner, work / 'incoming', work / 'state' / 'staged', quiet_seconds=2, clock=lambda:clock[0])
    try:
        IngestDispatcher(owner, os.environ['M0_RUNTIME'], work / 'lib', environment,
                         verify_endpoint=lambda candidate: candidate == port and readiness.verify())
    except ValueError:
        pass
    else:
        raise AssertionError('Ingest was enabled before setup readiness')
    assert not owner.ledger.list_jobs('running')
    (work / 'setup-complete').touch()
    deadline = time.monotonic() + 60
    while not readiness.verify():
        if web_process.wait(0) is not None or time.monotonic() >= deadline:
            detail = (work/'web.log').read_text(encoding='utf-8',errors='replace') if (work/'web.log').exists() else 'no log'
            raise RuntimeError('owned readiness server failed to start: ' + detail)
        time.sleep(.05)
    class OnceBusyDispatcher(IngestDispatcher):
        busy = True
        def start(self, job_id):
            if self.busy:
                self.busy = False
                raise OSError('injected transient busy condition')
            return super().start(job_id)
    dispatch = OnceBusyDispatcher(owner, os.environ['M0_RUNTIME'], work / 'lib', environment,
                                  verify_endpoint=lambda candidate: candidate == port and readiness.verify())
    scheduler = IngestScheduler(owner, inbox, dispatch, clock=lambda:clock[0])
    scheduler.start()
    clock[0] = 3.0
    assert scheduler.step(timeout=0) == 'waiting'
    assert len(owner.ledger.list_jobs('pending')) == 1
    assert scheduler.step(timeout=0) == 'waiting'
    clock[0] = 6.0
    state = scheduler.step(timeout=0)
    assert state == 'running', (state, web_process.wait(0), web_job.active_count(), owner.ledger.list_jobs('review'), owner.ledger.list_jobs('pending'))
    job = scheduler.active
    assert job and owner.ledger.get(job)['state'] == 'running'
    try:
        owner.start(job, pathlib.Path(os.environ['M0_RUNTIME']) / 'Scripts' / 'python.exe',
                    ['-I','-c','raise SystemExit(1)'], cwd=work, environment=environment)
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('A web edit started while ingest was active')
    scheduler.finish_current()
    deadline = time.monotonic() + 90
    while scheduler.active:
        state = scheduler.step(timeout=0)
        if state in ('review', 'recovery'): break
        if time.monotonic() >= deadline: raise TimeoutError('ingest did not finish')
        time.sleep(0.1)
    assert scheduler.step(timeout=0) == 'stopped'
    scheduler.close()
    result = owner.ledger.get(job)
    if result['state'] != 'completed':
        logs = list((work / 'state' / 'ingest-work').glob('*/worker.log'))
        detail = logs[-1].read_text(encoding='utf-8', errors='replace')[-5000:] if logs else '(no worker log)'
        raise RuntimeError('Ingest did not verify: ' + repr(result) + '\n' + detail)
    assert source.read_bytes() == original
    assert pathlib.Path(result['intent']['staged']).read_bytes() == original
    ingest_logs = list((work / 'state' / 'ingest-work').glob('*/worker.log'))
    assert any('Failed to backup' in log.read_text(encoding='utf-8', errors='replace') for log in ingest_logs)
    with sqlite3.connect(str(work / 'lib' / 'metadata.db')) as database:
        assert database.execute("SELECT COUNT(*) FROM data WHERE format='EPUB'").fetchone()[0] == 1
        book_id, initial_path = database.execute('SELECT id,path FROM books').fetchone()
    assert book_id == 1
    initial_cover = work / 'lib' / pathlib.PurePosixPath(initial_path) / 'cover.jpg'
    initial_cover_digest = hashlib.sha256(initial_cover.read_bytes()).hexdigest()
    def title_matches():
        with sqlite3.connect((work / 'lib' / 'metadata.db').resolve().as_uri() + '?mode=ro', uri=True) as database:
            title, book_path = database.execute('SELECT title,path FROM books WHERE id=?', (book_id,)).fetchone()
            row = database.execute("SELECT name FROM data WHERE book=? AND format='KEPUB'", (book_id,)).fetchone()
        directory = work / 'lib' / pathlib.PurePosixPath(book_path)
        cover = directory / 'cover.jpg'
        if not (title == 'M0 Enforced' and cover.is_file() and row and (work/'maintenance-done').exists() and
                (work/'convert-done').exists() and hashlib.sha256(cover.read_bytes()).hexdigest() != initial_cover_digest):
            return False
        with zipfile.ZipFile(directory / (row[0] + '.kepub')) as archive:
            return any(b'koboSpan' in archive.read(name) for name in archive.namelist()
                       if name.lower().endswith(('.html','.xhtml','.htm')))
    edit_env = dict(environment, M0_PROOF_PASSWORD=proof_password,
                    M0_COVER_READY=str(work/'cover-ready'), M0_COVER_GO=str(work/'cover-go'),
                    M0_MAINT_READY=str(work/'maintenance-ready'), M0_MAINT_GO=str(work/'maintenance-go'),
                    M0_MAINT_QUEUED=str(work/'maintenance-queued'), M0_MAINT_DONE=str(work/'maintenance-done'),
                    M0_CONVERT_READY=str(work/'convert-ready'), M0_CONVERT_GO=str(work/'convert-go'),
                    M0_CONVERT_QUEUED=str(work/'convert-queued'), M0_CONVERT_DONE=str(work/'convert-done'))
    edit_process = subprocess.Popen([str(pathlib.Path(os.environ['M0_RUNTIME']) / 'Scripts' / 'python.exe'),
                '-I', str(root / 'scripts' / 'windows_m0_web_edit_proof.py'), str(port),
                str(book_id), 'M0 Enforced', str(work / 'web-edit.log')], cwd=work, env=edit_env)
    deadline = time.monotonic() + 30
    while not (work/'cover-ready').exists():
        if time.monotonic() >= deadline: raise TimeoutError('web title edit did not reach cover step')
        time.sleep(.05)
    import importlib.util
    lock_spec = importlib.util.spec_from_file_location('_m0_cover_writer_lock', root / 'cps' / 'services' / 'calibre_db_lock.py')
    lock_module = importlib.util.module_from_spec(lock_spec); lock_spec.loader.exec_module(lock_module)
    with lock_module.metadata_db_write_lock(lock_dir=str(work/'cfg')):
        (work/'cover-go').touch()
        time.sleep(.5)
        with sqlite3.connect((work/'lib'/'metadata.db').resolve().as_uri()+'?mode=ro',uri=True) as database:
            locked_path = database.execute('SELECT path FROM books WHERE id=?',(book_id,)).fetchone()[0]
        locked_cover = work/'lib'/pathlib.PurePosixPath(locked_path)/'cover.jpg'
        assert hashlib.sha256(locked_cover.read_bytes()).hexdigest() == initial_cover_digest
        assert edit_process.poll() is None
    deadline = time.monotonic() + 30
    while not (work/'maintenance-ready').exists():
        if time.monotonic() >= deadline: raise TimeoutError('web cover edit did not reach maintenance step')
        time.sleep(.05)
    with lock_module.metadata_db_write_lock(lock_dir=str(work/'cfg')):
        (work/'maintenance-go').touch()
        deadline = time.monotonic() + 10
        while not (work/'maintenance-queued').exists():
            if time.monotonic() >= deadline: raise TimeoutError('metadata maintenance was not queued')
            time.sleep(.05)
        time.sleep(.5)
        assert not (work/'maintenance-done').exists()
        assert edit_process.poll() is None
    deadline = time.monotonic() + 30
    while not (work/'convert-ready').exists():
        if time.monotonic() >= deadline: raise TimeoutError('maintenance did not reach conversion step')
        time.sleep(.05)
    with lock_module.metadata_db_write_lock(lock_dir=str(work/'cfg')):
        (work/'convert-go').touch()
        deadline = time.monotonic() + 30
        while not (work/'convert-queued').exists():
            if time.monotonic() >= deadline: raise TimeoutError('conversion was not queued')
            time.sleep(.05)
        time.sleep(.5)
        assert not (work/'convert-done').exists()
        assert edit_process.poll() is None
    assert edit_process.wait(timeout=90) == 0, ((work/'web-edit.log').read_text(encoding='utf-8',errors='replace') if (work/'web-edit.log').exists() else 'no web-edit log')
    assert title_matches()
    if not readiness.verify():
        raise RuntimeError('web process stopped during ingest: ' + repr((web_process.wait(0),web_job.active_count())))
    enforcer = EnforcementDispatcher(owner, os.environ['M0_RUNTIME'], work / 'lib', environment,
                                     verify_endpoint=lambda candidate: candidate == port and readiness.verify())
    wall = [10_000_000_000]
    with sqlite3.connect(str(work / 'cfg' / 'cwa.db')) as settings:
        settings.execute('UPDATE cwa_settings SET auto_metadata_enforcement=1')
        settings.commit()
    metadata_logs = list((work / 'cfg' / 'metadata_change_logs').glob('*.json'))
    assert metadata_logs, 'real web edits did not produce metadata change logs'
    with IngestScheduler(owner, inbox, dispatch, enforcer=enforcer, clock=lambda:clock[0],
                         wall_clock_ns=lambda:wall[0]) as enforcement_scheduler:
        assert enforcement_scheduler.step(timeout=0) == 'waiting'
        pending = owner.ledger.list_jobs('pending')
        metadata = [row for row in pending if row['operation'] == 'metadata-debounce']
        assert len(metadata) == 1 and metadata[0]['intent']['book_id'] == book_id
        enforcement = metadata[0]['id']
        assert enforcement_scheduler.step(timeout=0) == 'waiting'
        assert owner.ledger.get(enforcement)['intent']['due_ns'] == wall[0] + 2_000_000_000
        wall[0] += 3_000_000_000
        assert enforcement_scheduler.step(timeout=0) == 'running'
        assert enforcement_scheduler.active == enforcement
        deadline = time.monotonic() + 90
        while enforcement_scheduler.active:
            state = enforcement_scheduler.step(timeout=0)
            if state in ('review', 'recovery'): break
            if time.monotonic() >= deadline: raise TimeoutError('enforcement did not finish')
            time.sleep(0.1)
        # Exercise the actual web service's durable whole-library conversion
        # request and cancellation, using only this disposable library.
        import requests, re
        session = requests.Session(); session.trust_env = False
        base = 'http://127.0.0.1:' + str(port)
        page = session.get(base + '/login', timeout=10); page.raise_for_status()
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text).group(1)
        logged = session.post(base + '/login', timeout=10, allow_redirects=False,
                              data={'username':'admin','password':proof_password,'csrf_token':token})
        assert logged.status_code in (302, 303)
        with sqlite3.connect(str(work / 'cfg' / 'cwa.db')) as settings:
            settings.execute("UPDATE cwa_settings SET auto_convert_target_format='kepub'")
            settings.commit()
        with sqlite3.connect(str(work / 'lib' / 'metadata.db')) as database:
            row = database.execute("SELECT books.path,data.name FROM books JOIN data ON data.book=books.id "
                                   "WHERE books.id=? AND data.format='KEPUB'", (book_id,)).fetchone()
            database.execute("DELETE FROM data WHERE book=? AND format='KEPUB'", (book_id,))
            database.commit()
        kepub_file = work / 'lib' / pathlib.PurePosixPath(row[0]) / (row[1] + '.kepub')
        kepub_file.unlink()
        response = session.get(base + '/cwa-convert-library-start', timeout=10, allow_redirects=False)
        assert response.status_code in (302, 303)
        response = session.get(base + '/convert-library-status', timeout=10)
        conversion = response.json()['job']
        assert conversion and conversion['state'] == 'pending'
        assert session.get(base + '/convert-library-cancel', timeout=10, allow_redirects=False).status_code in (302,303)
        assert owner.ledger.get(conversion['id'])['evidence'] == 'cancelled before launch'
        assert session.get(base + '/cwa-convert-library-start', timeout=10, allow_redirects=False).status_code in (302,303)
        conversion = session.get(base + '/convert-library-status', timeout=10).json()['job']
        assert conversion['state'] == 'pending'
        gate_spec = importlib.util.spec_from_file_location('_m0_writer_gate', root/'cps'/'services'/'file_lock.py')
        gate_api = importlib.util.module_from_spec(gate_spec); gate_spec.loader.exec_module(gate_api)
        gate_fd = gate_api.open_lock(str(work/'cfg'/'.cwng-native-writer.lock'))
        try:
            assert gate_api.acquire(gate_fd, blocking=False)
            assert enforcement_scheduler.step(timeout=0) == 'waiting'
            assert owner.ledger.get(conversion['id'])['state'] == 'pending'
        finally:
            gate_api.release(gate_fd); os.close(gate_fd)
        clock[0] += 3
        assert enforcement_scheduler.step(timeout=0) == 'running'
        assert enforcement_scheduler.active == conversion['id']
        deadline = time.monotonic() + 90
        while enforcement_scheduler.active:
            state = enforcement_scheduler.step(timeout=0)
            if state in ('review','recovery'): break
            if time.monotonic() >= deadline: raise TimeoutError('library conversion did not finish')
            time.sleep(.1)
        converted = owner.ledger.get(conversion['id'])
        if converted['state'] != 'completed':
            logs = sorted((work/'state'/'ingest-work').glob('*/worker.log'), key=lambda path:path.stat().st_mtime_ns)
            raise RuntimeError('Library conversion failed verification: ' + repr(converted) + '\n' +
                               (logs[-1].read_text(encoding='utf-8',errors='replace')[-5000:] if logs else 'no log'))
        assert session.get(base + '/convert-library-status', timeout=10).json()['job']['state'] == 'completed'
        assert kepub_file.is_file()
        fixed = session.post(base + '/cwa-epub-fixer/run-book', timeout=10,
                             headers={'X-CSRFToken':token}, json={'book_id':book_id})
        fixed.raise_for_status(); fixer_id = fixed.json()['job_id']
        assert enforcement_scheduler.step(timeout=0) == 'running'
        assert enforcement_scheduler.active == fixer_id
        deadline = time.monotonic() + 90
        while enforcement_scheduler.active:
            state = enforcement_scheduler.step(timeout=0)
            if state in ('review','recovery'): break
            if time.monotonic() >= deadline: raise TimeoutError('EPUB repair did not finish')
            time.sleep(.1)
        fixed_record = owner.ledger.get(fixer_id)
        if fixed_record['state'] != 'completed':
            logs = sorted((work/'state'/'ingest-work').glob('*/worker.log'), key=lambda path:path.stat().st_mtime_ns)
            raise RuntimeError('EPUB repair failed verification: ' + repr(fixed_record) + '\n' +
                               (logs[-1].read_text(encoding='utf-8',errors='replace')[-5000:] if logs else 'no log'))
        assert session.get(base + '/epub-fixer-status', timeout=10).json()['job']['state'] == 'completed'
        refresh = session.get(base + '/cwa-library-refresh', timeout=10)
        refresh.raise_for_status()
        refresh_id = refresh.json()['job_id']
        assert owner.ledger.get(refresh_id)['state'] == 'pending'
        assert enforcement_scheduler.step(timeout=0) == 'waiting'
        assert owner.ledger.get(refresh_id)['state'] == 'completed'
        assert source.read_bytes() == original
        manual = session.post(base + '/cwa-cover-enforcer-start', timeout=10, headers={'X-CSRFToken':token})
        manual.raise_for_status()
        manual_id = manual.json()['job_id']
        assert enforcement_scheduler.step(timeout=0) == 'running'
        deadline = time.monotonic() + 120
        while enforcement_scheduler.active:
            state = enforcement_scheduler.step(timeout=0)
            if state in ('review', 'recovery'): break
            if time.monotonic() >= deadline: raise TimeoutError('manual enforcement did not finish')
            time.sleep(.1)
        manual_record = owner.ledger.get(manual_id)
        if manual_record['state'] != 'completed':
            logs = sorted((work/'state'/'ingest-work').glob('*/worker.log'), key=lambda p:p.stat().st_mtime_ns)
            raise RuntimeError('Manual enforcement did not verify: ' + repr(manual_record) + '\n' +
                               (logs[-1].read_text(encoding='utf-8', errors='replace')[-5000:] if logs else 'no log'))
        assert session.get(base + '/cover-enforcer-status', timeout=10).json()['job']['state'] == 'completed'
        assert source.read_bytes() == original
        deferred = owner.enqueue('deferred-proof', 'unsupported-proof', {'fixture': True})
        enforcement_scheduler.finish_current()
        assert enforcement_scheduler.step(timeout=0) == 'stopped'
        assert owner.ledger.get(deferred)['state'] == 'pending'
    enforcement_record = owner.ledger.get(enforcement)
    assert all(path.is_file() for path in metadata_logs)
    if enforcement_record['state'] != 'completed':
        logs = sorted((work / 'state' / 'ingest-work').glob('*/worker.log'), key=lambda path:path.stat().st_mtime_ns)
        detail = logs[-1].read_text(encoding='utf-8', errors='replace')[-5000:] if logs else '(no worker log)'
        raise RuntimeError('Enforcement did not verify: ' + repr(enforcement_record) + '\n' + detail)
    # No writer is active here: compare a stopped-library copy without touching
    # the source database, including the independently verified EPUB bytes.
    def digest(path):
        with pathlib.Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()
    source_db = work / 'lib' / 'metadata.db'
    before = digest(source_db)
    with sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True) as database:
        book_path, data_name = database.execute("SELECT books.path,data.name FROM books JOIN data ON data.book=books.id WHERE books.id=? AND data.format='EPUB'", (book_id,)).fetchone()
    original_epub = work / 'lib' / pathlib.PurePosixPath(book_path) / (data_name + '.epub')
    copy = work / 'copy'
    shutil.copytree(work / 'lib', copy)
    with sqlite3.connect((copy / 'metadata.db').resolve().as_uri() + '?mode=ro', uri=True) as database:
        assert database.execute('SELECT title FROM books WHERE id=?', (book_id,)).fetchone() == ('M0 Enforced',)
    assert digest(copy / pathlib.PurePosixPath(book_path) / (data_name + '.epub')) == digest(original_epub)
    assert digest(source_db) == before
    assert digest(source_database) == source_hash
    with sqlite3.connect(source_database.resolve().as_uri() + '?mode=ro', uri=True) as source_db_readonly:
        assert source_db_readonly.execute('SELECT COUNT(*) FROM books').fetchone()[0] == 0
with Coordinator(os.environ['M0_NATIVE'], (work / 'state').resolve()) as restarted:
    assert restarted.ledger.get(job)['state'] == 'completed'
    assert restarted.ledger.get(enforcement)['state'] == 'completed'
    assert restarted.ledger.get(deferred)['state'] == 'pending'
    from unittest.mock import patch
    from windows_m0_ledger import LedgerStateError
    with patch('windows_m0_dispatch._volume_identity', return_value=('replacement-volume', 123)):
        try:
            IngestDispatcher(restarted, os.environ['M0_RUNTIME'], work / 'lib', environment,
                             verify_endpoint=lambda candidate: candidate == port and readiness.verify())
        except LedgerStateError: pass
        else: raise AssertionError('Replacement library volume was accepted after restart')
    resumed_inbox = Inbox(restarted, work / 'incoming', work / 'state' / 'staged', quiet_seconds=2, clock=lambda:clock[0])
    resumed_dispatch = IngestDispatcher(restarted, os.environ['M0_RUNTIME'], work / 'lib', environment,
                                        verify_endpoint=lambda candidate: candidate == port and readiness.verify())
    with IngestScheduler(restarted, resumed_inbox, resumed_dispatch, clock=lambda:clock[0]) as resumed:
        assert resumed.step(timeout=0) == 'review'
        assert restarted.ledger.get(deferred)['state'] == 'review'
web_job.close(); web_process.close()
traces = [json.loads(path.read_text(encoding='utf-8')) for path in trace_dir.glob('*.json')]
assert {'ingest_processor.py', 'cover_enforcer.py'} <= {item['entrypoint'] for item in traces}
unexpected = [(item['entrypoint'], item['unexpected_python'], item['unexpected_native'])
              for item in traces if item['unexpected_python'] or item['unexpected_native']]
assert not unexpected, unexpected
print('Worker origins:', [(item['entrypoint'], len(item['python']), len(item['native'])) for item in traces])
print('Stable input was converted, imported and metadata-enforced by real entrypoints; verified state survived restart and original bytes remained.')
'@
    $probe | & $python -I -
    if ($LASTEXITCODE -ne 0) { throw "Native ingest dispatch proof failed: $work" }
    Write-Output "Native ingest dispatch proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
