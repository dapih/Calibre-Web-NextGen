# Exercise authenticated local control against a disposable coordinator.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$work = Join-Path $cache ('control-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$helper = Join-Path $work 'cwng-native.dll'
& (Join-Path $cache 'mingw64\bin\gcc.exe') -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $helper (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('M0_HELPER','M0_WORK','M0_SCRIPTS')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:M0_HELPER = $helper; $env:M0_WORK = $work; $env:M0_SCRIPTS = $PSScriptRoot
    $probe = @'
import json, os, pathlib, secrets, subprocess, sys, threading, uuid
sys.path.insert(0, os.environ['M0_SCRIPTS'])
from windows_m0_control import ControlPipe, _kernel, _INVALID
from windows_m0_coordinator import Coordinator
name, key = 'CWNG-M0-' + uuid.uuid4().hex, secrets.token_bytes(32)
child_code = "import json,sys; sys.path.insert(0,sys.argv[1]); from windows_m0_control import request; key=sys.stdin.buffer.read(32); mode=sys.argv[3];\ntry:\n r=request(sys.argv[2],key,{'operation':'status'}); print(json.dumps(r)); sys.exit(0 if mode=='good' else 2)\nexcept (OSError,PermissionError,ConnectionError):\n sys.exit(0 if mode=='bad' else 3)"
clean = {'SystemRoot': os.environ['SystemRoot'], 'PATH': str(pathlib.Path(os.environ['SystemRoot']) / 'System32')}
def client(supplied_key, mode):
    process = subprocess.Popen([sys.executable, '-I', '-c', child_code, os.environ['M0_SCRIPTS'], name, mode],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=clean)
    process.stdin.write(supplied_key); process.stdin.close(); process.stdin = None
    return process
def exchange(pipe, owner, supplied_key, mode):
    errors = []
    def serve():
        try: owner.serve_control_once(pipe)
        except BaseException as error: errors.append(error)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    process = client(supplied_key, mode)
    try:
        out, err = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill(); process.communicate(); raise
    thread.join(timeout=10)
    if thread.is_alive(): raise RuntimeError('Control server did not finish; client stderr: ' + err.decode(errors='replace'))
    return process.returncode, out, err, errors
with Coordinator(os.environ['M0_HELPER'], pathlib.Path(os.environ['M0_WORK']).resolve()) as owner:
    with ControlPipe(name, key) as pipe:
        try:
            other = ControlPipe(name, key)
        except OSError:
            pass
        else:
            other.close(); raise AssertionError('Second server acquired a control name')
        code, out, err, errors = exchange(pipe, owner, secrets.token_bytes(32), 'bad')
        assert code == 0, err
        assert len(errors) == 1 and isinstance(errors[0], PermissionError), errors
        code, out, err, errors = exchange(pipe, owner, key, 'good')
        assert code == 0 and not errors, (err, errors)
        assert json.loads(out) == {'active': False, 'running': 0, 'review': 0}
    with ControlPipe('CWNG-M0-' + uuid.uuid4().hex, key, timeout=0.25) as pipe:
        stalled = _kernel.CreateFileW(pipe.name, 0xC0000000, 0, None, 3, 0, None)
        if stalled == _INVALID: raise OSError('Failed to create stalled test client')
        try:
            try: owner.serve_control_once(pipe)
            except TimeoutError: pass
            else: raise AssertionError('Stalled client was not timed out')
        finally:
            _kernel.CloseHandle(stalled)
print('Local pipe rejected collision and a wrong key, timed out a stalled client, and returned authenticated status.')
from windows_m0_activation import open_or_activate
owner, status = open_or_activate(os.environ['M0_HELPER'], pathlib.Path(os.environ['M0_WORK']))
assert owner is not None and status is None
first_key = (pathlib.Path(os.environ['M0_WORK']) / 'control.key').read_bytes()
activation_code = "import json,sys;sys.path.insert(0,sys.argv[1]);from windows_m0_activation import open_or_activate;owner,status=open_or_activate(sys.argv[2],sys.argv[3]);assert owner is None;print(json.dumps(status))"
try:
    duplicate = subprocess.run([sys.executable,'-I','-c',activation_code,os.environ['M0_SCRIPTS'],
                                os.environ['M0_HELPER'],os.environ['M0_WORK']],
                               env=clean,capture_output=True,timeout=10)
    assert duplicate.returncode == 0, duplicate.stderr
    assert json.loads(duplicate.stdout) == {'active':False,'running':0,'review':0}
finally:
    owner.close()
assert not (pathlib.Path(os.environ['M0_WORK']) / 'control.key').exists()
with open_or_activate(os.environ['M0_HELPER'], pathlib.Path(os.environ['M0_WORK']))[0] as restarted:
    assert (pathlib.Path(os.environ['M0_WORK']) / 'control.key').read_bytes() != first_key
print('Duplicate launch authenticated the existing owner; restart acquired ownership with a fresh key.')
'@
    $probe | & (Join-Path $cache 'python\python.exe') -I -
    if ($LASTEXITCODE -ne 0) { throw 'Authenticated control proof failed.' }
    Write-Output "Control proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
