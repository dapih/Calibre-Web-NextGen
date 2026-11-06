# Exercise a real Calibre transaction after its calling worker is terminated.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$work = Join-Path $cache ('w' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'state'),(Join-Path $work 'lib'),(Join-Path $work 'cfg'),(Join-Path $work 'programdata') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M0LeasePaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [M0LeasePaths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short proof path.' }
$short = $buffer.ToString()
$native = Join-Path $work 'cwng-native.dll'
& (Join-Path $cache 'mingw64\bin\gcc.exe') -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $native (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$child = Join-Path $work 'transaction.py'
@'
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location('lease_transaction', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
original = module.add_with_automerge
def paused(*args):
    result = original(*args)
    pathlib.Path(sys.argv[2]).write_text('transaction is open')
    deadline = time.monotonic() + 40
    while not pathlib.Path(sys.argv[3]).exists():
        if time.monotonic() >= deadline: raise TimeoutError('lease proof timed out')
        time.sleep(0.05)
    return result
module.add_with_automerge = paused
module.run(module.parse_args(sys.argv[4:]))
'@ | Set-Content -LiteralPath $child -Encoding UTF8
$writer = Join-Path $work 'caller.py'
@'
import subprocess, sys
command = [sys.argv[1], '-e', sys.argv[2], '--', *sys.argv[3:]]
with open(sys.argv[4] + '.child.log', 'wb') as log:
    raise SystemExit(subprocess.call(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log))
'@ | Set-Content -LiteralPath $writer -Encoding UTF8
$names = @('M0_LEASE_WORK','M0_LEASE_HELPER','M0_LEASE_SCRIPTS','M0_LEASE_CHILD','M0_LEASE_WRITER','M0_LEASE_TOOL','M0_LEASE_FIXTURE','M0_LEASE_TRANSACTION')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:M0_LEASE_WORK = $short; $env:M0_LEASE_HELPER = $native; $env:M0_LEASE_SCRIPTS = $PSScriptRoot
    $env:M0_LEASE_CHILD = $child; $env:M0_LEASE_WRITER = $writer
    $env:M0_LEASE_TOOL = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre\calibre-debug.exe'
    $env:M0_LEASE_FIXTURE = Join-Path $root 'tests\fixtures\sample_books\sherlock_holmes.epub'
    $env:M0_LEASE_TRANSACTION = Join-Path $PSScriptRoot 'calibre_ingest_transaction.py'
    $probe = @'
import ctypes, hashlib, os, pathlib, sqlite3, subprocess, sys, time
from ctypes import wintypes
sys.path.insert(0, os.environ['M0_LEASE_SCRIPTS'])
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_job import generation_name
work = pathlib.Path(os.environ['M0_LEASE_WORK'])
fixture = pathlib.Path(os.environ['M0_LEASE_FIXTURE'])
with fixture.open('rb') as stream: source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
ready, release = work / 'ready', work / 'release'
library = work / 'lib'
clean = {k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','USERPROFILE','APPDATA','LOCALAPPDATA') if k in os.environ}
clean.update({'PATH':str(pathlib.Path(os.environ['M0_LEASE_TOOL']).parent) + ';' + str(pathlib.Path(os.environ['SystemRoot']) / 'System32'),
              'CALIBRE_CONFIG_DIRECTORY':str(work / 'cfg'), 'TEMP':str(work / 'cfg'), 'TMP':str(work / 'cfg'),
              'PROGRAMDATA':str(work / 'programdata')})
args = [os.environ['M0_LEASE_WRITER'], os.environ['M0_LEASE_TOOL'], os.environ['M0_LEASE_CHILD'],
        os.environ['M0_LEASE_TRANSACTION'], str(ready), str(release), '--library-path', str(library),
        '--path', str(fixture), '--identity-path', str(fixture), '--expected-import-sha256', source_hash,
        '--expected-source-sha256', source_hash, '--automerge', 'disabled']
def committed():
    db = library / 'metadata.db'
    if not db.is_file(): return False
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as con:
        found = con.execute('SELECT COUNT(*) FROM identifiers WHERE type=? AND val=?',
                            ('cwng_ingest_sha256_' + source_hash, source_hash)).fetchone()[0]
    with fixture.open('rb') as stream: intact = hashlib.file_digest(stream, 'sha256').hexdigest() == source_hash
    return found == 1 and intact
with Coordinator(os.environ['M0_LEASE_HELPER'], (work / 'state').resolve()) as owner:
    first = owner.enqueue('first', 'ingest-lease-proof', {'source_sha256':source_hash})
    second = owner.enqueue('second', 'ingest-lease-proof', {'source_sha256':source_hash})
    pid = owner.start(first, sys.executable, args, cwd=work, environment=clean, writer_lock_dir=work/'cfg')
    deadline = time.monotonic() + 30
    while not ready.exists():
        if time.monotonic() >= deadline: raise TimeoutError('Calibre transaction did not reach pause')
        time.sleep(0.05)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE,wintypes.UINT]; kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]; kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]; kernel.CloseHandle.restype = wintypes.BOOL
    caller = kernel.OpenProcess(0x0001 | 0x00100000, False, pid)
    if not caller: raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.TerminateProcess(caller, 42): raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(caller, 10000) != 0: raise TimeoutError('caller did not exit')
    finally: kernel.CloseHandle(caller)
    # A normal native web writer must remain blocked even though the caller
    # that launched the raw Calibre child has died.
    lock_module = pathlib.Path(os.environ['M0_LEASE_SCRIPTS']).parent / 'cps' / 'services' / 'calibre_db_lock.py'
    probe_code = "import importlib.util,sys;spec=importlib.util.spec_from_file_location('lease_probe',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);\ntry:\n with m.metadata_db_write_lock(lock_dir=sys.argv[2],timeout=.3): pass\nexcept (TimeoutError,RuntimeError): sys.exit(0 if sys.argv[3]=='blocked' else 2)\nelse: sys.exit(2 if sys.argv[3]=='blocked' else 0)"
    native_env = dict(clean, CWNG_NATIVE_MODE='1', CWNG_NATIVE_STATE_DIR=str((work/'state').resolve()))
    def probe_gate(expected):
        result = subprocess.run([sys.executable,'-I','-c',probe_code,str(lock_module),str(work/'cfg'),expected],env=native_env,timeout=10)
        assert result.returncode == 0, 'native web writer gate result was wrong'
    probe_gate('blocked')
    forged_env = dict(native_env, CWNG_NATIVE_WRITER_GATE='1', CWNG_NATIVE_GATE_DIR=str(work/'cfg'),
                      CWNG_NATIVE_HELPER=os.environ['M0_LEASE_HELPER'], CWNG_NATIVE_JOB_GENERATION=owner.generation)
    forged = subprocess.run([sys.executable,'-I','-c',probe_code,str(lock_module),str(work/'cfg'),'available'],
                            env=forged_env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    assert forged.returncode != 0, 'an unrelated process claimed the contained worker lease'
    try: owner.finish(first, evidence='caller died', verify=committed)
    except CoordinatorBusy: pass
    else: raise AssertionError('Writer lease ended while Calibre transaction was active')
    try: owner.start(second, sys.executable, args, cwd=work, environment=clean)
    except CoordinatorBusy: pass
    else: raise AssertionError('A second writer started during the raw transaction')
    release.write_text('continue')
    deadline = time.monotonic() + 30
    while True:
        try:
            owner.finish(first, evidence='caller died; transaction requires review', verify=committed)
            break
        except CoordinatorBusy:
            if time.monotonic() >= deadline: raise
            time.sleep(0.05)
    assert owner.ledger.get(first)['state'] == 'review'
    probe_gate('blocked')
    owner.reconcile(first, outcome='completed', evidence='source and committed Calibre marker verified',
                    verify=lambda record, outcome: outcome == 'completed' and committed())
    assert owner.ledger.get(first)['state'] == 'completed'
    probe_gate('available')
print('Caller death did not release the writer lease while the real Calibre transaction was active; committed state was reconciled.')
# Start a second controller in a separate process and kill it while the same
# real transaction helper is paused before commit. A deliberately retained
# job handle keeps the tree alive, so restart must stay fenced.
state2, lib2, cfg2 = work / 'state2', work / 'lib2', work / 'cfg2'
for directory in (state2, lib2, cfg2): directory.mkdir()
ready2, release2 = work / 'ready2', work / 'release2'
child_env = dict(clean, CALIBRE_CONFIG_DIRECTORY=str(cfg2), TEMP=str(cfg2), TMP=str(cfg2))
controller_code = r'''
import os,pathlib,sys,time
sys.path.insert(0,sys.argv[1])
from windows_m0_coordinator import Coordinator
state=pathlib.Path(sys.argv[3]); owner=Coordinator(sys.argv[2],state)
job=owner.enqueue('crash','ingest-lease-proof',{'source_sha256':sys.argv[12]})
args=[sys.argv[4],sys.argv[5],sys.argv[6],sys.argv[7],sys.argv[8],sys.argv[9],
      '--library-path',sys.argv[10],'--path',sys.argv[11],'--identity-path',sys.argv[11],
      '--expected-import-sha256',sys.argv[12],'--expected-source-sha256',sys.argv[12],'--automerge','disabled']
owner.start(job,sys.executable,args,cwd=state.parent,environment=dict(os.environ),writer_lock_dir=os.environ['CALIBRE_CONFIG_DIRECTORY'])
(state/'generation').write_text(owner.generation)
time.sleep(60)
'''
crashing = subprocess.Popen([sys.executable, '-I', '-c', controller_code, os.environ['M0_LEASE_SCRIPTS'],
    os.environ['M0_LEASE_HELPER'], str(state2), os.environ['M0_LEASE_WRITER'], os.environ['M0_LEASE_TOOL'],
    os.environ['M0_LEASE_CHILD'], os.environ['M0_LEASE_TRANSACTION'], str(ready2), str(release2),
    str(lib2), str(fixture), source_hash], cwd=work, env=child_env, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
leak = None
try:
    deadline = time.monotonic() + 30
    while not ready2.exists() or not (state2 / 'generation').exists():
        if crashing.poll() is not None: raise RuntimeError('Controller exited before the transaction pause')
        if time.monotonic() >= deadline: raise TimeoutError('Crash transaction did not reach pause')
        time.sleep(0.05)
    generation = (state2 / 'generation').read_text()
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    leak = kernel.OpenJobObjectW(0x0004, False, generation_name(generation))
    if not leak: raise ctypes.WinError(ctypes.get_last_error())
    crashing.terminate(); crashing.wait(timeout=10)
    crash_env = dict(clean, CWNG_NATIVE_MODE='1', CWNG_NATIVE_STATE_DIR=str(state2.resolve()))
    def crash_probe(expected):
        result = subprocess.run([sys.executable,'-I','-c',probe_code,str(lock_module),str(cfg2),expected],
                                env=crash_env,timeout=10)
        assert result.returncode == 0, 'writer admitted before interrupted work was reconciled'
    crash_probe('blocked')
    with Coordinator(os.environ['M0_LEASE_HELPER'], state2.resolve()) as recovered:
        try: recovered.fence_interrupted()
        except CoordinatorBusy: pass
        else: raise AssertionError('Restart admitted a surviving old transaction')
        if not kernel.CloseHandle(leak): raise ctypes.WinError(ctypes.get_last_error())
        leak = None
        deadline = time.monotonic() + 15
        while True:
            try:
                assert recovered.fence_interrupted() == 1
                break
            except CoordinatorBusy:
                if time.monotonic() >= deadline: raise
                time.sleep(0.05)
        assert len(recovered.ledger.list_jobs('review')) == 1
        crash_probe('blocked')
        with sqlite3.connect(str(lib2 / 'metadata.db')) as con:
            count = con.execute('SELECT COUNT(*) FROM identifiers WHERE type=? AND val=?',
                                ('cwng_ingest_sha256_' + source_hash, source_hash)).fetchone()[0]
        assert count == 0, 'Uncommitted transaction unexpectedly retained its marker'
        with fixture.open('rb') as stream: assert hashlib.file_digest(stream, 'sha256').hexdigest() == source_hash
        reviewed = recovered.ledger.list_jobs('review')[0]
        recovered.reconcile(reviewed['id'], outcome='retry', evidence='uncommitted marker absent and source retained',
                            verify=lambda record, outcome: outcome == 'retry' and count == 0)
        crash_probe('available')
    print('Controller death kept a surviving transaction fenced; after job closure the intent remained in review and the uncommitted marker was absent.')
finally:
    if leak: kernel.CloseHandle(leak)
    if crashing.poll() is None: crashing.terminate(); crashing.wait(timeout=10)
'@
    $probe | & (Join-Path $cache 'python\python.exe') -I -
    if ($LASTEXITCODE -ne 0) { throw "Writer lease proof failed: $work" }
    Write-Output "Writer lease proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
