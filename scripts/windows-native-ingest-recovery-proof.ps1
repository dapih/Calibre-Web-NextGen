# Disposable Windows proof: real unmodified EPUB ingest, interrupted ledger
# handoff, then fenced offline receipt/DB/file verification. No user library.
param([Parameter(Mandatory=$true)][string]$Runtime)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$runtime = (Resolve-Path -LiteralPath $Runtime).Path
$python = Join-Path $runtime 'Scripts/python.exe'
$tool = Join-Path $cache 'calibre-proof/extracted/Calibre Portable/Calibre'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$work = Join-Path $cache ('ir' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'state'),(Join-Path $work 'state/staged'),(Join-Path $work 'incoming'),(Join-Path $work 'lib'),(Join-Path $work 'cfg'),(Join-Path $work 'tmp') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class IngestRecoveryProofPaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [IngestRecoveryProofPaths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short proof path.' }
$short = $buffer.ToString()
$native = Join-Path $work 'cwng-native.dll'
& (Join-Path $cache 'mingw64/bin/gcc.exe') -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $native (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('PATH','PYTHONHOME','PYTHONPATH','CALIBRE_DBPATH','CWA_DB_PATH','CWA_APP_DB_PATH','CWA_INGEST_FOLDER','CWA_CALIBRE_LIBRARY_DIR','CWA_TMP_CONVERSION_DIR','CWA_METADATA_LOCK_DIR','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP','M0_ROOT','M0_WORK','M0_RUNTIME','M0_NATIVE')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = $tool + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''; $env:PYTHONPATH = ''
    $env:CALIBRE_DBPATH = Join-Path $short 'cfg'; $env:CWA_DB_PATH = $env:CALIBRE_DBPATH
    $env:CWA_APP_DB_PATH = Join-Path $env:CALIBRE_DBPATH 'app.db'
    $env:CWA_INGEST_FOLDER = Join-Path $short 'incoming'; $env:CWA_CALIBRE_LIBRARY_DIR = Join-Path $short 'lib'
    $env:CWA_TMP_CONVERSION_DIR = Join-Path $short 'tmp'; $env:CWA_METADATA_LOCK_DIR = $env:CALIBRE_DBPATH
    $env:CALIBRE_CONFIG_DIRECTORY = $env:CALIBRE_DBPATH; $env:TEMP = $env:CWA_TMP_CONVERSION_DIR; $env:TMP = $env:TEMP
    $env:M0_ROOT = $root; $env:M0_WORK = $short; $env:M0_RUNTIME = $runtime; $env:M0_NATIVE = $native
    & (Join-Path $tool 'calibre-debug.exe') -c 'import os; from calibre.db.legacy import LibraryDatabase; db=LibraryDatabase(os.environ["CWA_CALIBRE_LIBRARY_DIR"]); db.close()'
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize disposable library.' }
    $probe = @'
import hashlib, json, os, pathlib, secrets, shutil, socket, sqlite3, sys, time
from types import SimpleNamespace
root, work = pathlib.Path(os.environ['M0_ROOT']), pathlib.Path(os.environ['M0_WORK'])
sys.path.insert(0, str(root / 'scripts'))
from cwa_db import CWA_DB
from windows_m0_activation import LocalOwner
from windows_m0_coordinator import Coordinator
from windows_m0_dispatch import IngestDispatcher
from windows_m0_inbox import Inbox
from windows_m0_job import Job
from windows_m0_readiness import OwnedReadiness
from windows_native_ingest_recovery import confirm_ingest
cwa = CWA_DB()
cwa.cur.execute("UPDATE cwa_settings SET auto_convert=1, auto_convert_target_format='epub', kindle_epub_fixer=0, auto_backup_imports=0, auto_metadata_enforcement=0")
cwa.con.commit(); cwa.con.close()
source = work / 'incoming' / 'input.epub'
shutil.copyfile(root / 'tests/fixtures/sample_books/test_rich_metadata.epub', source)
original = source.read_bytes()
reservation = socket.socket(); reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]; reservation.close()
key = secrets.token_bytes(32)
info = (work / 'lib' / 'metadata.db').stat(); identity = f'{info.st_dev}:{info.st_ino}'
wanted = {'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMDATA',
          'PATH','CALIBRE_DBPATH','CWA_DB_PATH','CWA_APP_DB_PATH','CWA_INGEST_FOLDER','CWA_CALIBRE_LIBRARY_DIR',
          'CWA_TMP_CONVERSION_DIR','CWA_METADATA_LOCK_DIR','CALIBRE_CONFIG_DIRECTORY','TEMP','TMP'}
environment = {key:value for key,value in os.environ.items() if key.upper() in wanted}
environment['CWNG_CALIBRE_TOOLS'] = str(root / '.m0/calibre-proof/extracted/Calibre Portable/Calibre')
environment['CWNG_KEPUBIFY_PATH'] = str(root / '.m0/kepubify-proof/kepubify-windows-64bit.exe')
environment['HOME'] = environment['USERPROFILE'] = environment['APPDATA'] = environment['LOCALAPPDATA'] = str(work / 'cfg')
environment['CALIBRE_CACHE_DIRECTORY'] = str(work / 'cfg/calibre-cache')
environment['CWA_PORT_OVERRIDE'] = str(port)
magick = root / '.m0/imagemagick-proof/dynamic/app'
web_env = dict(environment, M0_READY_KEY=key.hex(), M0_RUNTIME=os.environ['M0_RUNTIME'],
               M0_PROOF_PASSWORD=secrets.token_urlsafe(24), CWNG_M0_PROOF='1', CWNG_NATIVE_MODE='1',
               CWNG_NATIVE_STATE_DIR=str((work/'state').resolve()), MAGICK_HOME=str(magick),
               MAGICK_CODER_MODULE_PATH=str(magick/'modules/coders'))
web_env['PATH'] = str(magick) + ';' + web_env['PATH']
local = LocalOwner(os.environ['M0_NATIVE'], (work/'state').resolve())
web_job = Job(os.environ['M0_NATIVE'])
web = web_job.spawn(pathlib.Path(os.environ['M0_RUNTIME'])/'Scripts/python.exe',
                    ['-I','-B',str(root/'scripts/windows_m0_web_proof_worker.py'),str(root),str(work)],
                    cwd=work, environment=web_env)
readiness = OwnedReadiness(web_job, web, port, key, identity)
try:
    (work/'setup-complete').touch()
    deadline = time.monotonic()+90
    while not readiness.verify():
        if web.wait(0) is not None or time.monotonic() >= deadline:
            raise RuntimeError('web readiness failed: ' + (work/'web.log').read_text(encoding='utf-8',errors='replace')[-5000:])
        time.sleep(.1)
    owner = local.owner
    clock = [0.0]
    inbox = Inbox(owner, work/'incoming', work/'state/staged', quiet_seconds=2, clock=lambda:clock[0])
    assert inbox.observe(source) is None
    clock[0] = 3.0
    job_id = inbox.observe(source)
    assert job_id
    dispatch = IngestDispatcher(owner, os.environ['M0_RUNTIME'], work/'lib', environment,
                                verify_endpoint=lambda candidate: candidate == port and readiness.verify())
    dispatch.start(job_id)
    active = owner._active
    deadline = time.monotonic()+120
    while active['process'].wait(0) is None or active['job'].active_count():
        if time.monotonic() >= deadline:
            raise TimeoutError('ingest worker did not finish')
        time.sleep(.1)
    assert active['process'].wait(0) == 0
    assert owner.ledger.get(job_id)['state'] == 'running'
    receipts = list((work/'state/ingest-work').glob('ingest-*/ingest-complete.json'))
    if len(receipts) != 1:
        logs = list((work/'state/ingest-work').glob('ingest-*/worker.log'))
        raise RuntimeError('expected one receipt; logs: ' + '\n'.join(p.read_text(encoding='utf-8',errors='replace')[-6000:] for p in logs))
    receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
    assert receipt['job_id'] == job_id and receipt['mode'] == 'original-epub-no-backup'
    # Close ownership without finalizing the running ledger entry, simulating
    # loss after the worker completed but before the controller acknowledged it.
    local.close(); local = None
    web_job.close(); web.close()
    with Coordinator(os.environ['M0_NATIVE'], (work/'state').resolve()) as resumed:
        resumed.fence_interrupted()
        assert resumed.ledger.get(job_id)['state'] == 'review'
        controller = SimpleNamespace(helper=pathlib.Path(os.environ['M0_NATIVE']), state=(work/'state').resolve(),
                                     library=(work/'lib').resolve(), ingest=(work/'incoming').resolve())
        database = (work/'lib/metadata.db').resolve()
        confirm_ingest(controller, resumed, database, job_id, lambda: database)
        assert resumed.ledger.get(job_id)['state'] == 'completed'
        assert source.read_bytes() == original
    print('Real unmodified EPUB ingest produced a job-bound receipt and recovered after owner handoff.')
finally:
    if local is not None: local.close()
    web_job.close(); web.close()
'@
    $probe | & $python -I -B -
    if ($LASTEXITCODE -ne 0) { throw "Native ingest recovery proof failed: $work" }
    Write-Output "Native ingest recovery proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
