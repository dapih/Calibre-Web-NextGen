# Start the real Flask application and production HTTP server in a disposable job.
param([Parameter(Mandatory=$true)][string]$Runtime)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$runtimePath = (Resolve-Path -LiteralPath $Runtime).Path
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if ((Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Compiler archive hash mismatch.' }
if (Test-Path -LiteralPath 'C:\calibre-library\metadata.db') { throw 'Default fallback library exists; refusing the disposable startup probe.' }
$work = Join-Path $cache ('web' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Path $work,(Join-Path $work 'config'),(Join-Path $work 'home'),(Join-Path $work 'temp'),(Join-Path $work 'lib'),(Join-Path $work 'incoming') | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class M0WebPaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetShortPathName(string input, StringBuilder output, uint size);
}
'@
$buffer = [System.Text.StringBuilder]::new(32768)
$length = [M0WebPaths]::GetShortPathName($work, $buffer, $buffer.Capacity)
if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Unable to obtain short proof path.' }
$short = $buffer.ToString()
$helper = Join-Path $work 'cwng-native.dll'
& (Join-Path $cache 'mingw64\bin\gcc.exe') -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc -o $helper (Join-Path $PSScriptRoot 'windows-m0-native-helper.c')
if ($LASTEXITCODE -ne 0) { throw 'Native helper compilation failed.' }
$names = @('M0_HELPER','M0_WORK','M0_ROOT','M0_RUNTIME','M0_MAGICK','CWA_CALIBRE_LIBRARY_DIR','CALIBRE_CONFIG_DIRECTORY','APPDATA','LOCALAPPDATA','TEMP','TMP')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:M0_HELPER = $helper; $env:M0_WORK = $short; $env:M0_ROOT = $root; $env:M0_RUNTIME = $runtimePath
    $env:M0_MAGICK = Join-Path $cache 'imagemagick-proof\dynamic\app'
    $env:CWA_CALIBRE_LIBRARY_DIR = Join-Path $short 'lib'
    $env:CALIBRE_CONFIG_DIRECTORY = Join-Path $short 'config'
    $env:APPDATA = Join-Path $short 'home'; $env:LOCALAPPDATA = $env:APPDATA
    $env:TEMP = Join-Path $short 'temp'; $env:TMP = $env:TEMP
    $calibre = Join-Path $cache 'calibre-proof\extracted\Calibre Portable\Calibre'
    & (Join-Path $calibre 'calibre-debug.exe') -c 'import os; from calibre.db.legacy import LibraryDatabase; db=LibraryDatabase(os.environ["CWA_CALIBRE_LIBRARY_DIR"]); db.close()'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create disposable library.' }
    $probe = @'
import http.client,os,pathlib,secrets,socket,sys,time
root,work=pathlib.Path(os.environ['M0_ROOT']),pathlib.Path(os.environ['M0_WORK'])
sys.path.insert(0,str(root/'scripts'))
from windows_m0_job import Job
from windows_m0_readiness import OwnedReadiness
reservation=socket.socket();reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1];reservation.close()
key=secrets.token_bytes(32)
library=work/'lib'; info=(library/'metadata.db').stat(); identity=f'{info.st_dev}:{info.st_ino}'
clean={name:value for name,value in os.environ.items() if name.upper() in {'SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE'}}
clean.update({'PATH':os.environ['M0_MAGICK']+';'+str(pathlib.Path(os.environ['SystemRoot'])/'System32'),
 'HOME':str(work/'home'),'USERPROFILE':str(work/'home'),'APPDATA':str(work/'home'),'LOCALAPPDATA':str(work/'home'),
 'TEMP':str(work/'temp'),'TMP':str(work/'temp'),'CALIBRE_DBPATH':str(work/'config'),
 'CALIBRE_CONFIG_DIRECTORY':str(work/'config'),'CWA_APP_DB_PATH':str(work/'config'/'app.db'),
 'CWA_DB_PATH':str(work/'config'),'CWA_INGEST_FOLDER':str(work/'incoming'),
 'CWA_CALIBRE_LIBRARY_DIR':str(library),'CWA_TMP_CONVERSION_DIR':str(work/'temp'),
 'CWA_METADATA_LOCK_DIR':str(work/'config'),'CWA_PORT_OVERRIDE':str(port),
 'MAGICK_HOME':os.environ['M0_MAGICK'],'MAGICK_CONFIGURE_PATH':os.environ['M0_MAGICK'],
 'MAGICK_CODER_MODULE_PATH':str(pathlib.Path(os.environ['M0_MAGICK'])/'modules'/'coders'),
 'M0_READY_KEY':key.hex(),'PYTHONHOME':str(work/'hostile-python'),
 'PYTHONPATH':str(work/'hostile-modules'),'QT_PLUGIN_PATH':str(work/'hostile-qt'),
 'M0_RUNTIME':os.environ['M0_RUNTIME'],'CWNG_M0_PROOF':'1','CWNG_NATIVE_MODE':'1','CWNG_M0_TRACE_ORIGINS':'1'})
job=Job(os.environ['M0_HELPER']); process=None
try:
 process=job.spawn(pathlib.Path(os.environ['M0_RUNTIME'])/'Scripts'/'python.exe', ['-I',str(root/'scripts'/'windows_m0_web_proof_worker.py'),str(root),str(work)],cwd=work,environment=clean)
 readiness=OwnedReadiness(job,process,port,key,identity)
 assert not readiness.verify()
 (work/'setup-complete').touch()
 deadline=time.monotonic()+45
 while not readiness.verify():
  if process.wait(0) is not None or time.monotonic()>=deadline:
   detail=(work/'web.log').read_text(encoding='utf-8',errors='replace')[-5000:] if (work/'web.log').exists() else 'no log'
   raise RuntimeError('Real web server did not become ready: '+detail)
  time.sleep(.1)
 connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
 try:
  connection.request('GET','/login'); response=connection.getresponse(); response.read()
  assert response.status in (200,302), f'unexpected login status {response.status}'
 finally: connection.close()
 (work/'trace-refresh').touch()
 for _ in range(12):
  assert readiness.verify(), ('real web process lost readiness', process.wait(0), job.active_count())
  time.sleep(.1)
 assert (work/'web-origins.json').is_file()
 assert 'Starting Tornado server' in (work/'web.log').read_text(encoding='utf-8',errors='replace')
 print('Real Flask app and production HTTP server answered owned readiness and the login route.')
finally:
 job.close()
 if process: process.close()
'@
    $probe | & (Join-Path $runtimePath 'Scripts\python.exe') -I -
    if ($LASTEXITCODE -ne 0) { throw "Real web proof failed: $work" }
    Write-Output "Real web proof: $work"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
