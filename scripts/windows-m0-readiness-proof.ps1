# Exercise owned loopback readiness in a disposable process.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
$work = Join-Path $cache ('readiness-proof-' + [guid]::NewGuid().ToString('N'))
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
import os, pathlib, secrets, socket, sys, time
work = pathlib.Path(os.environ['M0_WORK']).resolve()
sys.path.insert(0, os.environ['M0_SCRIPTS'])
from windows_m0_job import Job
from windows_m0_readiness import OwnedReadiness
key = secrets.token_bytes(32)
clean = {'SystemRoot':os.environ['SystemRoot'], 'PATH':str(pathlib.Path(os.environ['SystemRoot'])/'System32'), 'M0_KEY':key.hex()}
server = """import os,pathlib,sys
from wsgiref.simple_server import make_server,WSGIRequestHandler
sys.path.insert(0,sys.argv[1])
from windows_m0_readiness import readiness_middleware
class Quiet(WSGIRequestHandler):
 def log_message(self,*args): pass
def app(env,start): start('200 OK',[('Content-Length','2')]);return [b'ok']
ready=pathlib.Path(sys.argv[3])
application=readiness_middleware(app,bytes.fromhex(os.environ['M0_KEY']),lambda:(ready.exists(),'library-one'))
with make_server('127.0.0.1',int(sys.argv[2]),application,handler_class=Quiet) as http: http.serve_forever()
"""
foreign = socket.socket(); foreign.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
foreign.bind(('127.0.0.1',0)); foreign.listen(); occupied = foreign.getsockname()[1]
blocked_job = Job(os.environ['M0_HELPER']); blocked = None
try:
 blocked = blocked_job.spawn(sys.executable,['-I','-c',server,os.environ['M0_SCRIPTS'],str(occupied),str(work/'ready')],cwd=work,environment=clean)
 assert blocked.wait(10000) not in (None,0)
 assert not OwnedReadiness(blocked_job,blocked,occupied,key,'library-one').verify()
 assert foreign.getsockname()[1] == occupied
finally:
 if blocked: blocked.close()
 blocked_job.close(); foreign.close()
reservation = socket.socket(); reservation.bind(('127.0.0.1',0)); port=reservation.getsockname()[1]; reservation.close()
job=Job(os.environ['M0_HELPER']); child=None
try:
 child=job.spawn(sys.executable,['-I','-c',server,os.environ['M0_SCRIPTS'],str(port),str(work/'ready')],cwd=work,environment=clean)
 probe=OwnedReadiness(job,child,port,key,'library-one')
 deadline=time.monotonic()+10
 time.sleep(.4); assert not probe.verify()
 (work/'ready').touch()
 while not probe.verify():
  if child.wait(0) is not None or time.monotonic()>deadline: raise RuntimeError('owned readiness did not start')
  time.sleep(.05)
 assert not OwnedReadiness(job,child,port,secrets.token_bytes(32),'library-one').verify()
 assert not OwnedReadiness(job,child,port,key,'wrong-library').verify()
 job.close(); assert child.wait(10000) is not None
 assert not probe.verify()
finally:
 job.close()
 if child: child.close()
print('Occupied port was not adopted; owned readiness required setup, the live job, the correct key and library.')
'@
    $probe | & (Join-Path $cache 'python\python.exe') -I -
    if ($LASTEXITCODE -ne 0) { throw 'Owned readiness proof failed.' }
    Write-Output "Readiness proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
