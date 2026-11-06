param([Parameter(Mandatory=$true)][string]$Helper, [switch]$TestOnlyBypassAclAndCloud)
# Disposable integration fixture. This mode substitutes profile ACL and Cloud
# Files checks because this sandbox cannot create private ACLs and its Cloud
# Files query returns an ambiguous error. Dummy payload files are not a package.
$ErrorActionPreference = 'Stop'
if (-not $TestOnlyBypassAclAndCloud) { throw 'This fixture requires explicit test-only ACL/cloud substitution.' }
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$env:TEST_FIRST_ROOT = $root
$env:TEST_FIRST_HELPER = (Resolve-Path -LiteralPath $Helper).Path
$env:TEST_FIRST_WORK = Join-Path $root ('.m0/first-run-proof-' + [guid]::NewGuid().ToString('N'))
$python = Join-Path $root '.m0/n1-runtime-locked/Scripts/python.exe'
$probe = @'
import hashlib, json, os, pathlib, shutil, sqlite3, subprocess, sys
root = pathlib.Path(os.environ['TEST_FIRST_ROOT'])
work = pathlib.Path(os.environ['TEST_FIRST_WORK'])
state, source, ingest, parent, program = [work / name for name in ('state', 'source', 'ingest', 'target', 'program')]
for path in (state / 'config', source, ingest, parent, program / 'scripts', program / 'runtime' / 'Scripts', program / 'calibre', program / 'magick'):
    path.mkdir(parents=True)
helper = program / 'helper.dll'
shutil.copyfile(os.environ['TEST_FIRST_HELPER'], helper)
(program / 'runtime' / 'Scripts' / 'python.exe').write_bytes(b'fixture')
(program / 'kepubify.exe').write_bytes(b'fixture')
with sqlite3.connect(source / 'metadata.db') as db:
    db.execute('create table fixture (id integer)')
(source / 'book.epub').write_bytes(b'fixture book')
source_digest = hashlib.sha256((source / 'metadata.db').read_bytes()).hexdigest()
env = dict(os.environ, CWA_DB_PATH=str(state / 'config'), CALIBRE_DBPATH=str(state / 'config'))
child = '''
import os,pathlib,sys
root=pathlib.Path(os.environ['TEST_FIRST_ROOT']); work=pathlib.Path(os.environ['TEST_FIRST_WORK'])
sys.path[:0]=[str(root),str(root/'scripts')]
import windows_native_first_run as first
import windows_native_setup_db as db
first.require_private_directory=db.require_private_directory=lambda p:pathlib.Path(p).resolve(strict=True)
first._cloud_check=lambda p:None
first.__file__=str(work/'program'/'scripts'/'windows_native_first_run.py')
volume=first._volume_identity
if sys.argv[1]=='volume-changed':
 first._volume_identity=lambda p:('changed-volume',1) if pathlib.Path(p).name=='ingest' else volume(p)
original=first._write_marker
def injection(path, value, **kwargs):
 if sys.argv[1]=='after-copy' and path.name=='setup-in-progress.json' and value.get('phase')=='initializing': raise RuntimeError('injected after copy')
 if sys.argv[1]=='after-profile' and path.name=='setup-complete.json': raise RuntimeError('injected after profile')
 if sys.argv[1]=='after-complete' and path.name=='setup-complete.json':
  original(path,value,**kwargs)
  raise RuntimeError('injected after completion')
 return original(path,value,**kwargs)
first._write_marker=injection
first.complete_copy_setup(state=work/'state',helper=work/'program'/'helper.dll',runtime=work/'program'/'runtime',
 calibre=work/'program'/'calibre',kepubify=work/'program'/'kepubify.exe',magick=work/'program'/'magick',
 source=work/'source',library=work/'target'/'library',ingest=work/'ingest',
 username='Guest' if sys.argv[1]=='guest' else 'testowner',
 password='NotARealPassword123!',port=8083,source_stopped=True,
 storage_not_synced=sys.argv[1]!='sync-unconfirmed')
'''
for mode in ('guest', 'sync-unconfirmed', 'after-copy', 'volume-changed', 'after-profile', 'after-complete', 'success'):
    result = subprocess.run([sys.executable, '-I', '-c', child, mode], env=env, capture_output=True, text=True)
    if mode in ('guest', 'sync-unconfirmed'):
        assert result.returncode != 0, (mode, result.stdout, result.stderr)
        assert not (parent / 'library').exists() and not (state / 'setup-in-progress.json').exists()
    elif mode == 'volume-changed':
        assert result.returncode != 0 and 'different inputs' in result.stderr, result.stderr
        assert not (state / 'setup-complete.json').exists()
    elif mode != 'success':
        assert result.returncode != 0 and 'injected' in result.stderr, (mode, result.stdout, result.stderr)
        assert (state / 'setup-complete.json').exists() == (mode == 'after-complete')
    else:
        assert result.returncode == 0, (result.stdout, result.stderr)
from werkzeug.security import check_password_hash
with sqlite3.connect(state / 'config' / 'app.db') as db:
    users = db.execute('select name,password from user where role & 1 = 1').fetchall()
    assert len(users) == 1 and users[0][0] == 'testowner' and check_password_hash(users[0][1], 'NotARealPassword123!')
marker = json.loads((state / 'setup-complete.json').read_text(encoding='utf-8'))
assert marker['library'] == str((parent / 'library').resolve())
assert not (state / 'setup-in-progress.json').exists()
assert not (parent / '.library.cwng-copy.json').exists()
assert hashlib.sha256((source / 'metadata.db').read_bytes()).hexdigest() == source_digest
assert (source / 'book.epub').read_bytes() == (parent / 'library' / 'book.epub').read_bytes()
print('Copy and profile publication interruption re-entry passed with real application databases.')
print('ACL/cloud checks were substituted; payload files were test placeholders.')
print('Evidence:', work)
'@
$probe | & $python -I -
if ($LASTEXITCODE -ne 0) { throw 'First-run disposable proof failed.' }
