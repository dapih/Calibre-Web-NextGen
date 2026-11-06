# Build and exercise the local native helper using the private toolchain.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
$compiler = Join-Path $cache 'mingw64\bin\gcc.exe'
$python = Join-Path $cache 'python\python.exe'
if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Verified compiler archive is missing.' }
foreach ($path in @($compiler, $python)) { if (-not (Test-Path -LiteralPath $path)) { throw "Missing prerequisite: $path" } }
$work = Join-Path $cache ('native-helper-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$dll = Join-Path $work 'cwng-native.dll'
& $compiler -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $dll (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('PATH','PYTHONHOME','PYTHONPATH','M0_HELPER','M0_WORK','M0_SCRIPTS')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = 'C:\invalid-host-python'
    $env:PYTHONPATH = 'C:\invalid-host-modules'
    $env:M0_HELPER = $dll
    $env:M0_WORK = $work
    $env:M0_SCRIPTS = $PSScriptRoot
    $probe = @'
import ctypes, os, pathlib, subprocess, sys, time, uuid
from ctypes import wintypes
work = pathlib.Path(os.environ['M0_WORK']).resolve()
sys.path.insert(0, os.environ['M0_SCRIPTS'])
from windows_m0_job import Job, ProfileLock
clean = {'SystemRoot': os.environ['SystemRoot'], 'PATH': str(pathlib.Path(os.environ['SystemRoot']) / 'System32'), 'TEMP': str(work), 'TMP': str(work), 'M0_READY': str(work / 'ready')}
lock_path = work / 'profile.lock'
holder_code = "import os,pathlib,sys,time; sys.path.insert(0,sys.argv[1]); from windows_m0_job import ProfileLock; lock=ProfileLock(sys.argv[2],sys.argv[3]); pathlib.Path(sys.argv[4]).touch(); time.sleep(30)"
holder = subprocess.Popen([sys.executable, '-I', '-c', holder_code, os.environ['M0_SCRIPTS'], os.environ['M0_HELPER'], str(lock_path), str(work / 'lock-ready')], cwd=work, env=clean)
try:
    deadline = time.monotonic() + 10
    while not (work / 'lock-ready').exists() and time.monotonic() < deadline:
        if holder.poll() is not None: raise RuntimeError('Profile owner exited early')
        time.sleep(0.05)
    assert (work / 'lock-ready').is_file()
    try:
        duplicate = ProfileLock(os.environ['M0_HELPER'], lock_path)
    except OSError as error:
        if error.winerror != 33: raise
    else:
        duplicate.close()
        raise AssertionError('A second profile owner acquired the lock')
    holder.terminate(); holder.wait(timeout=10)
    with ProfileLock(os.environ['M0_HELPER'], lock_path):
        pass
    print('Duplicate profile owner was excluded; ownership recovered after process exit.')
finally:
    if holder.poll() is None: holder.terminate(); holder.wait(timeout=10)
generation = 'nj1-' + uuid.uuid4().hex
job, child = Job(os.environ['M0_HELPER'], generation=generation), None
try:
    code = "import os,pathlib,subprocess,sys,time; sys.path.insert(0,sys.argv[2]); from windows_m0_job import current_process_in_generation; assert current_process_in_generation(sys.argv[3],sys.argv[4]); assert 'PYTHONPATH' not in os.environ; assert sys.argv[1] == 'space & quote \\\"here\\\"'; p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)']); pathlib.Path(os.environ['M0_READY']).write_text(str(os.getpid())+' '+str(p.pid)); time.sleep(30)"
    child = job.spawn(sys.executable, ['-I', '-c', code, 'space & quote "here"', os.environ['M0_SCRIPTS'], os.environ['M0_HELPER'], generation], cwd=work, environment=clean)
    deadline = time.monotonic() + 10
    while not (work / 'ready').exists() and time.monotonic() < deadline:
        if child.wait(0) is not None: raise RuntimeError('Worker exited before readiness')
        time.sleep(0.05)
    assert (work / 'ready').is_file()
    worker_pid, grandchild_pid = map(int, (work / 'ready').read_text().split())
    assert worker_pid == child.pid
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    grandchild = kernel.OpenProcess(0x00100000 | 0x1000, False, grandchild_pid)
    if not grandchild: raise ctypes.WinError(ctypes.get_last_error())
    try:
        member = wintypes.BOOL()
        if not kernel.IsProcessInJob(grandchild, job._handle, ctypes.byref(member)): raise ctypes.WinError(ctypes.get_last_error())
        assert member.value and job.active_count() >= 2
        job.close()
        assert child.wait(10000) is not None, 'Closing the job did not terminate the child'
        assert child._kernel.WaitForSingleObject(grandchild, 10000) == 0, 'Grandchild remained alive'
    finally:
        child._kernel.CloseHandle(grandchild)
    print('Private worker and grandchild started in the job and exited when it closed.')
finally:
    job.close()
    if child is not None: child.close()
# A job handle made inheritable in the controller must still not reach workers.
kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
kernel.SetHandleInformation.restype = wintypes.BOOL
inherit_job = Job(os.environ['M0_HELPER'])
inherit_child = None
try:
    if not kernel.SetHandleInformation(inherit_job._handle, 1, 1): raise ctypes.WinError(ctypes.get_last_error())
    inherit_child = inherit_job.spawn(sys.executable, ['-I','-c','import time;time.sleep(30)'], cwd=work, environment=clean)
    assert inherit_child.wait(100) is None
    inherit_job.close()
    assert inherit_child.wait(10000) is not None, 'An inherited job handle prevented kill-on-close'
finally:
    if inherit_child is not None: inherit_child.close()
    inherit_job.close()
print('The worker did not inherit a deliberately inheritable job handle.')
from windows_m0_coordinator import Coordinator, CoordinatorBusy
from windows_m0_job import generation_name
state = work / 'state'
state.mkdir()
source = work / 'source-book'
source.write_text('original')
coordinator = Coordinator(os.environ['M0_HELPER'], state)
leaked_handle = None
try:
    first = coordinator.enqueue('first', 'fixture', {'source': str(source)})
    result_marker = work / 'verified-result'
    try:
        coordinator.start(first, sys.executable, ['-I', '-c', 'pass'], cwd=work, environment=clean)
    except ValueError:
        assert coordinator.ledger.get(first)['state'] == 'pending'
    else:
        raise AssertionError('Ungated job was admitted without an explicit fixture opt-in')
    coordinator.start(first, sys.executable, ['-I', '-c', 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("ok")', str(result_marker)], cwd=work, environment=clean, allow_ungated_fixture=True)
    deadline = time.monotonic() + 10
    while True:
        try:
            coordinator.finish(first, evidence='fixture result verified', verify=lambda: result_marker.read_text() == 'ok')
            break
        except CoordinatorBusy:
            if time.monotonic() >= deadline: raise
            time.sleep(0.05)
    assert coordinator.ledger.get(first)['state'] == 'completed'
    interrupted = coordinator.enqueue('interrupted', 'fixture', {'source': str(source)})
    waiting = coordinator.enqueue('waiting', 'fixture', {'source': str(source)})
    coordinator.start(interrupted, sys.executable, ['-I', '-c', 'import time; time.sleep(30)'], cwd=work, environment=clean, allow_ungated_fixture=True)
    from calibre_library_target import offline_child_ownership, LibraryBusyError
    native_names = ('CWNG_NATIVE_MODE', 'CWNG_NATIVE_JOB_GENERATION', 'CWNG_NATIVE_HELPER')
    saved_native = {name: os.environ.get(name) for name in native_names}
    try:
        os.environ.update({'CWNG_NATIVE_MODE':'1', 'CWNG_NATIVE_JOB_GENERATION':coordinator.generation,
                           'CWNG_NATIVE_HELPER':os.environ['M0_HELPER']})
        try: offline_child_ownership()
        except LibraryBusyError: pass
        else: raise AssertionError('Process outside the controller job was accepted')
    finally:
        for name, value in saved_native.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
    try:
        collision = Job(os.environ['M0_HELPER'], generation=coordinator.generation)
    except OSError as error:
        if error.winerror != 183: raise
    else:
        collision.close()
        raise AssertionError('An existing generation job was adopted')
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    leaked_handle = kernel.OpenJobObjectW(0x0004, False, generation_name(coordinator.generation))
    if not leaked_handle: raise ctypes.WinError(ctypes.get_last_error())
    try:
        coordinator.start(waiting, sys.executable, ['-I', '-c', 'pass'], cwd=work, environment=clean)
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('A second writer was admitted')
finally:
    coordinator.close()
with Coordinator(os.environ['M0_HELPER'], state) as recovered:
    assert recovered.ledger.get(interrupted)['state'] == 'running'
    try:
        recovered.enqueue('after-restart', 'fixture', {'source': str(source)})
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('Interrupted work did not block admission')
    try:
        recovered.fence_interrupted()
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('Leaked job handle did not block the recovery fence')
    if not kernel.CloseHandle(leaked_handle): raise ctypes.WinError(ctypes.get_last_error())
    leaked_handle = None
    deadline = time.monotonic() + 10
    while True:
        try:
            assert recovered.fence_interrupted() == 1
            break
        except CoordinatorBusy:
            if time.monotonic() >= deadline: raise
            time.sleep(0.05)
    assert recovered.ledger.get(interrupted)['state'] == 'review'
    try:
        recovered.enqueue('still-review', 'fixture', {'source': str(source)})
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('Unreconciled work did not block admission')
    try:
        recovered.reconcile(interrupted, outcome='retry', evidence='not verified', verify=lambda *_: False)
    except CoordinatorBusy:
        pass
    else:
        raise AssertionError('Unverified recovery was accepted')
    assert recovered.ledger.get(interrupted)['state'] == 'review'
    recovered.reconcile(interrupted, outcome='retry', evidence='fixture input retained and no fixture write occurred',
                        verify=lambda record, outcome: outcome == 'retry' and record['operation'] == 'fixture' and source.read_text() == 'original')
    assert recovered.ledger.get(interrupted)['state'] == 'pending'
assert source.read_text() == 'original'
print('Coordinator excluded concurrent writers, fenced a leaked job and retained interrupted work for review.')
crash_state = work / 'crash-state'
crash_state.mkdir()
crash_ready = work / 'crash-ready'
owner_code = "import os,pathlib,sys,time; sys.path.insert(0,sys.argv[1]); from windows_m0_coordinator import Coordinator; c=Coordinator(sys.argv[2],sys.argv[3]); j=c.enqueue('crash','fixture',{}); p=c.start(j,sys.executable,['-I','-c','import time;time.sleep(30)'],cwd=sys.argv[3],environment={'SystemRoot':os.environ['SystemRoot'],'PATH':os.path.join(os.environ['SystemRoot'],'System32')},allow_ungated_fixture=True); pathlib.Path(sys.argv[4]).write_text(c.generation+' '+str(p)); time.sleep(30)"
owner = subprocess.Popen([sys.executable, '-I', '-c', owner_code, os.environ['M0_SCRIPTS'], os.environ['M0_HELPER'], str(crash_state), str(crash_ready)], cwd=work, env=clean)
leak = worker_handle = None
try:
    deadline = time.monotonic() + 10
    while not crash_ready.exists() and time.monotonic() < deadline:
        if owner.poll() is not None: raise RuntimeError('Crash owner exited before readiness')
        time.sleep(0.05)
    assert crash_ready.is_file()
    old_generation, old_pid = crash_ready.read_text().split()
    leak = kernel.OpenJobObjectW(0x0004, False, generation_name(old_generation))
    if not leak: raise ctypes.WinError(ctypes.get_last_error())
    worker_handle = kernel.OpenProcess(0x00100000, False, int(old_pid))
    if not worker_handle: raise ctypes.WinError(ctypes.get_last_error())
    owner.terminate(); owner.wait(timeout=10)
    with Coordinator(os.environ['M0_HELPER'], crash_state) as recovered:
        try:
            recovered.fence_interrupted()
        except CoordinatorBusy:
            pass
        else:
            raise AssertionError('Recovery accepted a leaked old job after owner death')
        assert kernel.WaitForSingleObject(worker_handle, 0) == 0x102
        if not kernel.CloseHandle(leak): raise ctypes.WinError(ctypes.get_last_error())
        leak = None
        assert kernel.WaitForSingleObject(worker_handle, 10000) == 0
        deadline = time.monotonic() + 10
        while True:
            try:
                assert recovered.fence_interrupted() == 1
                break
            except CoordinatorBusy:
                if time.monotonic() >= deadline: raise
                time.sleep(0.05)
        assert len(recovered.ledger.list_jobs('review')) == 1
    print('Owner termination with a leaked job handle blocked recovery until the worker exited.')
finally:
    if leak: kernel.CloseHandle(leak)
    if worker_handle: kernel.CloseHandle(worker_handle)
    if owner.poll() is None: owner.terminate(); owner.wait(timeout=10)
# A normal crash without a leaked handle must not admit recovery while the
# actual old worker process is still alive.
crash_plain = work / 'crash-plain'; crash_plain.mkdir()
plain_ready = work / 'crash-plain-ready'
plain_owner = subprocess.Popen([sys.executable, '-I', '-c', owner_code, os.environ['M0_SCRIPTS'], os.environ['M0_HELPER'], str(crash_plain), str(plain_ready)], cwd=work, env=clean)
plain_worker = None
try:
    deadline = time.monotonic() + 10
    while not plain_ready.exists():
        if plain_owner.poll() is not None or time.monotonic() > deadline: raise RuntimeError('plain crash worker did not start')
        time.sleep(.05)
    _, plain_pid = plain_ready.read_text().split()
    plain_worker = kernel.OpenProcess(0x00100000, False, int(plain_pid))
    if not plain_worker: raise ctypes.WinError(ctypes.get_last_error())
    plain_owner.terminate(); plain_owner.wait(timeout=10)
    with Coordinator(os.environ['M0_HELPER'], crash_plain) as recovered:
        deadline = time.monotonic() + 10
        while True:
            try:
                recovered.fence_interrupted()
                break
            except CoordinatorBusy:
                if time.monotonic() > deadline: raise
                time.sleep(.01)
        assert kernel.WaitForSingleObject(plain_worker, 0) == 0, 'Fence completed while old worker was alive'
        assert len(recovered.ledger.list_jobs('review')) == 1
finally:
    if plain_worker: kernel.CloseHandle(plain_worker)
    if plain_owner.poll() is None: plain_owner.terminate(); plain_owner.wait(timeout=10)
print('Recovery fence returned only after the old worker exited in an ordinary controller crash.')
from windows_m0_ledger import Ledger
legacy_state = work / 'legacy-state'
legacy_state.mkdir()
legacy = Ledger(legacy_state / 'runtime-ledger.sqlite')
legacy_id = legacy.enqueue('legacy', 'fixture', {})
legacy.start(legacy_id, 'legacy-generation')
with Coordinator(os.environ['M0_HELPER'], legacy_state) as recovered:
    try: recovered.fence_interrupted()
    except CoordinatorBusy: pass
    else: raise AssertionError('An unnamed legacy generation was accepted as fenced')
    assert recovered.ledger.get(legacy_id)['state'] == 'running'
print('Named job collisions and unverifiable legacy generations failed closed.')
phase_code = r'''
import os, pathlib, sys
sys.path.insert(0,sys.argv[1])
import windows_m0_coordinator as c
phase, state = sys.argv[4], pathlib.Path(sys.argv[3])
target, method = (c.Ledger, 'start') if phase == 'ledger' else (c.Job, '__init__' if phase == 'job' else 'spawn')
original = getattr(target, method)
def crash_after(*args, **kwargs):
    result = original(*args, **kwargs)
    (state / 'phase-reached').write_text(phase)
    os._exit(91)
setattr(target, method, crash_after)
owner = c.Coordinator(sys.argv[2], state)
job = owner.enqueue('phase', 'fixture', {})
owner.start(job, sys.executable, ['-I','-c','import time;time.sleep(30)'], cwd=state,
            environment={'SystemRoot':os.environ['SystemRoot'],'PATH':os.path.join(os.environ['SystemRoot'],'System32')}, allow_ungated_fixture=True)
raise AssertionError('fault point did not terminate owner')
'''
unrelated = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(30)'], cwd=work, env=clean)
try:
    for phase in ('ledger', 'job', 'spawn'):
        phase_state = work / ('phase-' + phase)
        phase_state.mkdir()
        faulted = subprocess.run([sys.executable, '-I', '-c', phase_code, os.environ['M0_SCRIPTS'], os.environ['M0_HELPER'], str(phase_state), phase], cwd=work, env=clean, timeout=15)
        assert faulted.returncode == 91 and (phase_state / 'phase-reached').read_text() == phase
        with Coordinator(os.environ['M0_HELPER'], phase_state) as recovered:
            assert len(recovered.ledger.list_jobs('running')) == 1
            deadline = time.monotonic() + 10
            while True:
                try:
                    assert recovered.fence_interrupted() == 1
                    break
                except CoordinatorBusy:
                    if time.monotonic() >= deadline: raise
                    time.sleep(0.05)
            assert len(recovered.ledger.list_jobs('review')) == 1
        assert unrelated.poll() is None, 'Unrelated process was terminated'
    print('Controller termination after journal, job creation, and spawn left intent in review; unrelated process survived.')
finally:
    if unrelated.poll() is None: unrelated.terminate(); unrelated.wait(timeout=10)
def failed_verifier():
    raise OSError('fixture verification failed')
for label, verifier in (('missing', None), ('negative', lambda: False), ('error', failed_verifier)):
    unverified_state = work / ('unverified-' + label)
    unverified_state.mkdir()
    with Coordinator(os.environ['M0_HELPER'], unverified_state) as owner:
        job_id = owner.enqueue('unverified', 'fixture', {})
        owner.start(job_id, sys.executable, ['-I', '-c', 'pass'], cwd=work, environment=clean, allow_ungated_fixture=True)
        deadline = time.monotonic() + 10
        while True:
            try:
                owner.finish(job_id, evidence='exit alone', verify=verifier)
                break
            except CoordinatorBusy:
                if time.monotonic() >= deadline: raise
                time.sleep(0.05)
        assert owner.ledger.get(job_id)['state'] == 'review'
print('Missing, negative and failed outcome verification all remained in review.')
from windows_m0_inbox import Inbox
inbox_source, inbox_state = work / 'inbox-source', work / 'inbox-state'
inbox_stage = inbox_state / 'staged'
for directory in (inbox_source, inbox_state, inbox_stage): directory.mkdir()
incoming = inbox_source / 'slow.epub'
copy_ready, copy_release = work / 'copy-ready', work / 'copy-release'
writer_code = "import os,pathlib,sys,time; f=open(sys.argv[1],'wb'); f.write(b'partial'); f.flush(); os.fsync(f.fileno()); pathlib.Path(sys.argv[2]).touch();\nwhile not pathlib.Path(sys.argv[3]).exists(): time.sleep(.05)\nf.write(b'-complete');f.flush();os.fsync(f.fileno());f.close()"
copy_writer = subprocess.Popen([sys.executable, '-I', '-c', writer_code, str(incoming), str(copy_ready), str(copy_release)], cwd=work, env=clean)
try:
    deadline = time.monotonic() + 10
    while not copy_ready.exists() and time.monotonic() < deadline:
        if copy_writer.poll() is not None: raise RuntimeError('Slow-copy fixture exited early')
        time.sleep(0.05)
    assert copy_ready.exists()
    clock = [0.0]
    with Coordinator(os.environ['M0_HELPER'], inbox_state) as owner:
        inbox = Inbox(owner, inbox_source, inbox_stage, quiet_seconds=2, clock=lambda: clock[0])
        assert inbox.observe(incoming) is None
        clock[0] = 3.0
        assert inbox.observe(incoming) is None, 'Open source was admitted'
        copy_release.touch(); assert copy_writer.wait(timeout=10) == 0
        assert inbox.observe(incoming) is None
        clock[0] = 6.0
        admitted = inbox.observe(incoming)
        assert admitted
        record = owner.ledger.get(admitted)
        assert record['state'] == 'pending'
        assert incoming.read_bytes() == b'partial-complete'
        assert pathlib.Path(record['intent']['staged']).read_bytes() == b'partial-complete'
        assert inbox.observe(incoming) is None
        clock[0] = 9.0
        assert inbox.observe(incoming) == admitted, 'Repeated input did not deduplicate'
        import errno
        from unittest.mock import patch
        full_source = inbox_source / 'disk-full.epub'
        full_source.write_bytes(b'input survives')
        assert inbox.observe(full_source) is None
        clock[0] = 12.0
        original_open = pathlib.Path.open
        def fail_staging(path, *args, **kwargs):
            if path.parent == inbox_stage and path.name.startswith('.incoming-'):
                raise OSError(errno.ENOSPC, 'injected disk full')
            return original_open(path, *args, **kwargs)
        with patch.object(pathlib.Path, 'open', fail_staging):
            try: inbox.observe(full_source)
            except OSError as error: assert error.errno == errno.ENOSPC
            else: raise AssertionError('Disk-full staging was accepted')
        assert full_source.read_bytes() == b'input survives'
        assert len(owner.ledger.list_jobs('pending')) == 1
        with patch('windows_m0_inbox._volume_identity', return_value=('different-volume', 0)):
            try: inbox.observe(full_source)
            except OSError: pass
            else: raise AssertionError('A replaced source volume was accepted')
        assert full_source.read_bytes() == b'input survives'
    with Coordinator(os.environ['M0_HELPER'], inbox_state) as restarted:
        pending = restarted.ledger.get(admitted)
        assert pending['state'] == 'pending'
        assert pathlib.Path(pending['intent']['staged']).read_bytes() == b'partial-complete'
        from windows_m0_ledger import LedgerStateError
        with patch('windows_m0_inbox._volume_identity', return_value=('replacement-volume', 123)):
            try: Inbox(restarted, inbox_source, inbox_stage, quiet_seconds=2)
            except LedgerStateError: pass
            else: raise AssertionError('Replacement volume was accepted after restart')
        assert incoming.read_bytes() == b'partial-complete'
    print('Slow copy stayed pending until closed and stable; staged intent was committed and deduplicated.')
    print('A staging disk-full failure retained the source; pending staged work survived restart.')
finally:
    if copy_writer.poll() is None: copy_writer.terminate(); copy_writer.wait(timeout=10)
from windows_m0_watcher import IngestWatcher
watch_source, watch_state = work / 'watch-source', work / 'watch-state'
watch_source.mkdir(); watch_state.mkdir(); (watch_state / 'staged').mkdir()
(watch_source / 'before.txt').write_text('before')
watch_clock, announced = [0.0], []
with Coordinator(os.environ['M0_HELPER'], watch_state) as owner:
    inbox = Inbox(owner, watch_source, watch_state / 'staged', quiet_seconds=2, clock=lambda:watch_clock[0])
    with IngestWatcher(inbox, announced.append, scan_interval=1, clock=lambda:watch_clock[0]) as watcher:
        (watch_source / 'after.txt').write_text('after')
        watch_clock[0] = 3.0
        watcher.poll(timeout=0)
        watch_clock[0] = 6.0
        watcher.poll(timeout=0)
        assert len(announced) == 2 and len(set(announced)) == 2
        assert len(owner.ledger.list_jobs('pending')) == 2
        assert (watch_source / 'before.txt').read_text() == 'before'
        assert (watch_source / 'after.txt').read_text() == 'after'
print('Watcher armed before its initial scan and admitted both existing and newly created files.')
from windows_m0_scheduler import IngestScheduler
stop_source, stop_state = work / 'stop-source', work / 'stop-state'
stop_source.mkdir(); stop_state.mkdir(); (stop_state / 'staged').mkdir()
retained = stop_source / 'retained.bin'
retained.write_bytes(b'retain interrupted input')
ready = stop_state / 'worker-ready'
class PauseDispatcher:
    def __init__(self, owner): self.owner = owner
    def start(self, job):
        code = "import pathlib,sys,time;pathlib.Path(sys.argv[1]).touch();time.sleep(30)"
        self.owner.start(job, sys.executable, ['-I','-c',code,str(ready)], cwd=stop_state, environment=clean, allow_ungated_fixture=True)
    def finish(self, job):
        self.owner.finish(job, evidence='unexpected completion', verify=lambda:False)
with Coordinator(os.environ['M0_HELPER'], stop_state) as owner:
    inbox = Inbox(owner, stop_source, stop_state / 'staged', quiet_seconds=2)
    job = owner.enqueue('stop-fixture', 'ingest', {'source':str(retained)})
    scheduler = IngestScheduler(owner, inbox, PauseDispatcher(owner))
    scheduler.start()
    assert scheduler.step(timeout=0) == 'running'
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline: raise TimeoutError('stop fixture did not start')
        time.sleep(.05)
    state = scheduler.stop_safely()
    deadline = time.monotonic() + 10
    while state == 'recovery':
        if time.monotonic() > deadline: raise TimeoutError('stop fixture remained live')
        time.sleep(.05)
        state = scheduler.stop_safely()
    assert state == 'review'
    assert owner.ledger.get(job)['state'] == 'review'
    assert retained.read_bytes() == b'retain interrupted input'
    scheduler.close()
with Coordinator(os.environ['M0_HELPER'], stop_state) as owner:
    assert owner.ledger.get(job)['state'] == 'review'
print('Stop safely fenced the contained worker and retained its interrupted intent and source for review.')
timeout_state = work / 'timeout-state'; timeout_state.mkdir(); (timeout_state / 'staged').mkdir()
stop_state, ready = timeout_state, timeout_state / 'worker-ready'
timeout_clock = [0.0]
with Coordinator(os.environ['M0_HELPER'], timeout_state) as owner:
    inbox = Inbox(owner, stop_source, timeout_state / 'staged', quiet_seconds=2)
    job = owner.enqueue('timeout-fixture', 'ingest', {'source':str(retained)})
    scheduler = IngestScheduler(owner, inbox, PauseDispatcher(owner), clock=lambda:timeout_clock[0], max_job_seconds=1)
    scheduler.start()
    assert scheduler.step(timeout=0) == 'running'
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline: raise TimeoutError('timeout fixture did not start')
        time.sleep(.05)
    timeout_clock[0] = 2.0
    state = scheduler.step(timeout=0)
    deadline = time.monotonic() + 10
    while state == 'recovery':
        if time.monotonic() > deadline: raise TimeoutError('timed-out worker remained live')
        time.sleep(.05)
        state = scheduler.step(timeout=0)
    assert state == 'review' and owner.ledger.get(job)['state'] == 'review'
    assert retained.read_bytes() == b'retain interrupted input'
    scheduler.close()
print('A worker timeout stopped its contained job and retained its input and intent for review.')
storage_state = work / 'storage-state'; storage_state.mkdir(); (storage_state / 'staged').mkdir()
storage_clock = [0.0]
with Coordinator(os.environ['M0_HELPER'], storage_state) as owner:
    inbox = Inbox(owner, stop_source, storage_state / 'staged', quiet_seconds=2, clock=lambda:storage_clock[0])
    scheduler = IngestScheduler(owner, inbox, PauseDispatcher(owner), clock=lambda:storage_clock[0])
    scheduler.start()
    from windows_m0_lifecycle import _user
    assert _user.PostMessageW(scheduler.lifecycle.window, 0x218, 4, 0)
    deadline = time.monotonic() + 10
    status = 'idle'
    while status != 'storage':
        if time.monotonic() > deadline: raise TimeoutError('idle suspend was not handled')
        time.sleep(.05); status = scheduler.step(timeout=0)
    assert _user.PostMessageW(scheduler.lifecycle.window, 0x218, 18, 0)
    while status != 'idle':
        if time.monotonic() > deadline: raise TimeoutError('idle resume was not handled')
        time.sleep(.05); status = scheduler.step(timeout=0)
    storage_clock[0] = 3.0
    with patch('windows_m0_watcher._volume_identity', return_value=('unavailable-volume', 0)):
        assert scheduler.step(timeout=0) == 'storage'
    assert retained.read_bytes() == b'retain interrupted input'
    with patch('windows_m0_scheduler._volume_identity', return_value=('replacement-volume', 0)):
        try: scheduler.resume_storage()
        except OSError: pass
        else: raise AssertionError('Replacement storage was accepted')
    assert scheduler.step(timeout=0) == 'storage'
    scheduler.resume_storage()
    assert scheduler.step(timeout=0) == 'idle'
    assert retained.read_bytes() == b'retain interrupted input'
    # A missing staging path must pause even when the watched source remains online.
    (storage_state / 'staged').rename(storage_state / 'staged-offline')
    assert scheduler.step(timeout=0) == 'storage'
    (storage_state / 'staged-offline').rename(storage_state / 'staged')
    scheduler.resume_storage()
    assert scheduler.step(timeout=0) == 'idle'
    scheduler.close()
print('Unavailable storage paused admission; replacement was refused and original storage resumed safely.')
active_storage = work / 'storage-active'; active_storage.mkdir(); (active_storage / 'staged').mkdir()
stop_state, ready = active_storage, active_storage / 'worker-ready'
active_clock = [0.0]
with Coordinator(os.environ['M0_HELPER'], active_storage) as owner:
    inbox = Inbox(owner, stop_source, active_storage / 'staged', quiet_seconds=2, clock=lambda:active_clock[0])
    job = owner.enqueue('disconnect-fixture','ingest',{'source':str(retained)})
    scheduler = IngestScheduler(owner,inbox,PauseDispatcher(owner),clock=lambda:active_clock[0])
    scheduler.start(); assert scheduler.step(timeout=0) == 'running'
    deadline=time.monotonic()+10
    while not ready.exists():
        if time.monotonic()>deadline: raise TimeoutError('active storage fixture did not start')
        time.sleep(.05)
    active_clock[0] = 3.0
    with patch('windows_m0_watcher._volume_identity', side_effect=OSError('simulated disconnect')):
        status=scheduler.step(timeout=0)
    deadline=time.monotonic()+10
    while status == 'recovery':
        if time.monotonic()>deadline: raise TimeoutError('disconnected worker did not exit')
        time.sleep(.05); status=scheduler.step(timeout=0)
    assert status == 'review' and owner.ledger.get(job)['state'] == 'review'
    try: scheduler.resume_storage()
    except CoordinatorBusy: pass
    else: raise AssertionError('Storage resumed before reconciling an interrupted writer')
    assert retained.read_bytes() == b'retain interrupted input'
    scheduler.close()
print('Storage loss during an active writer stopped and fenced it; recovery remained in review.')
power_state = work / 'power-active'; power_state.mkdir(); (power_state / 'staged').mkdir()
stop_state, ready = power_state, power_state / 'worker-ready'
from windows_m0_lifecycle import _user
with Coordinator(os.environ['M0_HELPER'], power_state) as owner:
    inbox = Inbox(owner, stop_source, power_state / 'staged', quiet_seconds=2)
    job = owner.enqueue('suspend-fixture', 'ingest', {'source':str(retained)})
    scheduler = IngestScheduler(owner, inbox, PauseDispatcher(owner))
    scheduler.start(); assert scheduler.step(timeout=0) == 'running'
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline: raise TimeoutError('power fixture did not start')
        time.sleep(.05)
    # Deliver the actual Windows message to the hidden top-level window; no
    # system-wide sleep or user configuration is needed.
    assert _user.PostMessageW(scheduler.lifecycle.window, 0x218, 4, 0)
    status = 'running'
    while status in ('running', 'recovery'):
        if time.monotonic() > deadline: raise TimeoutError('suspend was not handled')
        time.sleep(.05); status = scheduler.step(timeout=0)
    assert status == 'review' and owner.ledger.get(job)['state'] == 'review'
    assert retained.read_bytes() == b'retain interrupted input'
    try: scheduler.resume_storage()
    except CoordinatorBusy: pass
    else: raise AssertionError('Suspend resumed before interrupted work was reviewed')
    scheduler.close()
with Coordinator(os.environ['M0_HELPER'], power_state) as owner:
    assert owner.ledger.get(job)['state'] == 'review'
print('A Windows suspend message stopped and fenced active work; input and review survived restart.')
'@
    $probe | & $python -I -
    if ($LASTEXITCODE -ne 0) { throw 'Native helper proof failed.' }
    Write-Output "Native helper proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ("Env:" + $name) -Value $saved[$name] }
    }
}
