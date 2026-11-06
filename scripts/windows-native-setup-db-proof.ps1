param(
    [Parameter(Mandatory=$true)][string]$Helper,
    [string]$Runtime = '.m0/n1-runtime-locked/Scripts/python.exe',
    [switch]$TestOnlyBypassAcl
)
# Disposable integration proof. TestOnlyBypassAcl does not validate profile
# security and is only for sandboxes that cannot set a private Windows DACL.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = (Resolve-Path -LiteralPath (Join-Path $root $Runtime)).Path
$helperPath = (Resolve-Path -LiteralPath $Helper).Path
$work = Join-Path $root ('.m0/setup-db-proof-' + [guid]::NewGuid().ToString('N'))
$state = Join-Path $work 'state'
$config = Join-Path $state 'config'
$library = Join-Path $work 'library'
New-Item -ItemType Directory -Force -Path $config,$library | Out-Null
if (-not $TestOnlyBypassAcl) {
    $acl = Get-Acl -LiteralPath $state
    $acl.SetAccessRuleProtection($true, $false)
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $state -AclObject $acl
    Set-Acl -LiteralPath $config -AclObject $acl
}
$env:CWA_DB_PATH = $config
$env:CALIBRE_DBPATH = $config
$env:TEST_SETUP_ROOT = $root
$env:TEST_SETUP_STATE = $state
$env:TEST_SETUP_LIBRARY = $library
$env:TEST_SETUP_HELPER = $helperPath
$env:TEST_SETUP_BYPASS_ACL = if ($TestOnlyBypassAcl) { '1' } else { '0' }
$probe = @'
import os, pathlib, sqlite3, subprocess, sys, time
root = pathlib.Path(os.environ['TEST_SETUP_ROOT'])
state = pathlib.Path(os.environ['TEST_SETUP_STATE'])
library = pathlib.Path(os.environ['TEST_SETUP_LIBRARY'])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'scripts'))
with sqlite3.connect(library / 'metadata.db') as db:
    db.execute('create table fixture (id integer)')
import windows_native_setup_db as setup
if os.environ['TEST_SETUP_BYPASS_ACL'] == '1':
    setup.require_private_directory = lambda path: pathlib.Path(path).resolve(strict=True)
options = dict(state=state, helper=os.environ['TEST_SETUP_HELPER'], library=library,
               username='testowner', password='NotARealPassword123!')
try:
    setup.prepare_databases(**dict(options, username='gUeSt'))
except ValueError:
    pass
else:
    raise AssertionError('the reserved Guest name must be rejected')
assert not (state / 'config' / 'app.db').exists()
with setup.setup_ownership(state, options['helper']) as owner:
    setup.prepare_databases(**options, ownership=owner)
    from windows_m0_job import ProfileLock
    try:
        ProfileLock(options['helper'], state / 'controller.lock')
    except OSError:
        pass
    else:
        raise AssertionError('setup ownership must remain held after database preparation')
try:
    setup.prepare_databases(**options, ownership=owner)
except ValueError:
    pass
else:
    raise AssertionError('expired setup ownership must be refused')
from werkzeug.security import check_password_hash
with sqlite3.connect(state / 'config' / 'app.db') as db:
    admins = db.execute('select name,password from user where role & 1 = 1').fetchall()
    assert len(admins) == 1 and admins[0][0] == 'testowner'
    assert check_password_hash(admins[0][1], options['password'])
    assert not check_password_hash(admins[0][1], 'admin123')
    assert db.execute('select config_calibre_dir,config_calibre_split from settings').fetchone() == (str(library.resolve()), 0)
    assert db.execute('select count(*) from magic_shelf').fetchone()[0] >= 7
with sqlite3.connect(state / 'config' / 'cwa.db') as db:
    assert db.execute('select count(*) from cwa_settings').fetchone()[0] >= 1
assert not (state / 'setup-complete.json').exists()
before = (state / 'config' / 'app.db').read_bytes()
try:
    setup.prepare_databases(**options)
except FileExistsError:
    pass
else:
    raise AssertionError('re-entry must reject existing databases')
assert (state / 'config' / 'app.db').read_bytes() == before
interrupted = state / 'interrupted'
(interrupted / 'config').mkdir(parents=True)
child_env = dict(os.environ, CWA_DB_PATH=str(interrupted / 'config'),
                 CALIBRE_DBPATH=str(interrupted / 'config'))
child_code = '''
import os,pathlib,sys
root=pathlib.Path(os.environ["TEST_SETUP_ROOT"])
sys.path[:0]=[str(root),str(root/"scripts")]
import windows_native_setup_db as setup
if os.environ['TEST_SETUP_BYPASS_ACL'] == '1':
 setup.require_private_directory=lambda p:pathlib.Path(p).resolve(strict=True)
setup.prepare_databases(state=sys.argv[1],helper=os.environ["TEST_SETUP_HELPER"],
 library=os.environ["TEST_SETUP_LIBRARY"],username="testowner",password="NotARealPassword123!")
'''
child = subprocess.Popen([sys.executable, '-I', '-c', child_code, str(interrupted)], env=child_env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
partial = interrupted / 'config' / 'app.db'
deadline = time.monotonic() + 30
while not partial.exists() and child.poll() is None and time.monotonic() < deadline:
    time.sleep(.005)
if not partial.exists() or child.poll() is not None:
    child.terminate()
    child.wait()
    raise AssertionError('could not interrupt initialization after database creation')
child.terminate()
child.wait()
partial_bytes = partial.read_bytes()
os.environ['CWA_DB_PATH'] = os.environ['CALIBRE_DBPATH'] = str(interrupted / 'config')
try:
    setup.prepare_databases(**dict(options, state=interrupted))
except FileExistsError:
    pass
else:
    raise AssertionError('interrupted initialization must remain stopped for review')
assert partial.read_bytes() == partial_bytes and not (interrupted / 'setup-complete.json').exists()
print('Real app and CWA databases initialized; selected administrator verified; marker absent; re-entry rejected.')
print('Interrupted initialization retained its partial database and refused re-entry.')
if os.environ['TEST_SETUP_BYPASS_ACL'] == '1':
    print('Profile ACL validation was substituted for this sandbox fixture.')
'@
$probe | & $python -I -
if ($LASTEXITCODE -ne 0) { throw "Disposable database proof failed; inspect $work" }
Write-Output "Disposable evidence: $work"
