param([Parameter(Mandatory=$true)][string]$Helper, [switch]$TestOnlyBypassAclAndCloud, [switch]$TestOnlyRetainExtra, [switch]$TestOnlyCancel)
# The fixture lives under the source program tree and substitutes only that
# overlap check, private ACL, and Cloud Files eligibility. Calibre and both
# application databases are real; this is not an installed-payload proof.
$ErrorActionPreference = 'Stop'
if (-not $TestOnlyBypassAclAndCloud) { throw 'This fixture requires explicit test-only substitutions.' }
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$work = Join-Path $root ('.m0/n-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
$state = Join-Path $work 's'
$config = Join-Path $state 'config'
$ingest = Join-Path $work 'i'
$target = Join-Path $work 'l'
New-Item -ItemType Directory -Force -Path $config,$ingest | Out-Null
$env:CWA_DB_PATH = $config
$env:CALIBRE_DBPATH = $config
$env:TEST_NEW_ROOT = $root
$env:TEST_NEW_STATE = $state
$env:TEST_NEW_INGEST = $ingest
$env:TEST_NEW_TARGET = $target
$env:TEST_NEW_HELPER = (Resolve-Path -LiteralPath $Helper).Path
$env:TEST_NEW_RETAIN_EXTRA = if ($TestOnlyRetainExtra) { '1' } else { '0' }
$env:TEST_NEW_CANCEL = if ($TestOnlyCancel) { '1' } else { '0' }
$python = Join-Path $root '.m0/n1-runtime-locked/Scripts/python.exe'
$probe = @'
import json, os, pathlib, sqlite3, sys, threading, time
root = pathlib.Path(os.environ['TEST_NEW_ROOT'])
state = pathlib.Path(os.environ['TEST_NEW_STATE'])
sys.path[:0] = [str(root), str(root / 'scripts')]
import windows_native_first_run as first, windows_native_setup_db as db, windows_native_new_library as new
first.require_private_directory = db.require_private_directory = new.require_private_directory = lambda p: pathlib.Path(p).resolve(strict=True)
first._cloud_check = lambda p: None
original_overlap = first._overlap
def fixture_overlap(a, b):
    return False if a == root or b == root else original_overlap(a, b)
first._overlap = new._overlap = fixture_overlap
if os.environ['TEST_NEW_RETAIN_EXTRA'] == '1':
    actual_copy = new.complete_copy_setup
    def copy_with_extra(**kwargs):
        result = actual_copy(**kwargs)
        (kwargs['source'] / 'user-added.txt').write_text('preserve me', encoding='utf-8')
        return result
    new.complete_copy_setup = copy_with_extra
options = dict(state=state, helper=os.environ['TEST_NEW_HELPER'],
    runtime=root/'.m0/n1-runtime-locked', calibre=root/'.m0/calibre-proof/extracted/Calibre Portable/Calibre',
    kepubify=root/'.m0/kepubify-proof/kepubify-windows-64bit.exe', magick=root/'.m0/imagemagick-proof/extracted',
    library=os.environ['TEST_NEW_TARGET'], ingest=os.environ['TEST_NEW_INGEST'], username='testowner',
    password='NotARealPassword123!', port=8083, storage_not_synced=True)
if os.environ['TEST_NEW_CANCEL'] == '1':
    from windows_m0_job import generation_exists
    from windows_native_setup import SetupCancelled
    event = threading.Event()
    stop = threading.Event()
    def cancel_worker():
        record_path = state / 'new-library-stage.json'
        while not stop.wait(.005):
            if record_path.exists():
                try:
                    generation = json.loads(record_path.read_text(encoding='utf-8'))['generation']
                    if generation_exists(options['helper'], generation):
                        event.set()
                        return
                except (OSError, ValueError, KeyError):
                    pass
    watcher = threading.Thread(target=cancel_worker)
    watcher.start()
    try:
        try:
            new.complete_new_setup(**options, cancel_event=event)
        except SetupCancelled:
            pass
        else:
            raise AssertionError('canceled Calibre staging must not complete')
        assert event.is_set() and not (state / 'setup-complete.json').exists()
    finally:
        stop.set()
        watcher.join()
result = new.complete_new_setup(**options)
library = pathlib.Path(result['library'])
with sqlite3.connect(library / 'metadata.db') as connection:
    assert connection.execute('select count(*) from books').fetchone()[0] == 0
with sqlite3.connect(state / 'config' / 'app.db') as connection:
    from werkzeug.security import check_password_hash
    name, password = connection.execute('select name,password from user where role & 1 = 1').fetchone()
    assert name == 'testowner' and check_password_hash(password, 'NotARealPassword123!')
assert (state / 'setup-complete.json').is_file()
assert not (state / 'setup-in-progress.json').exists()
stages = list(state.parent.glob('.cwng-new-*'))
if os.environ['TEST_NEW_RETAIN_EXTRA'] == '1':
    assert len(stages) == 1 and (stages[0] / 'user-added.txt').read_text(encoding='utf-8') == 'preserve me'
    assert (state / 'new-library-stage.json').exists()
else:
    # A safely interrupted attempt retains its old untrusted partial stage.
    assert len(stages) == (1 if os.environ['TEST_NEW_CANCEL'] == '1' else 0)
    assert not (state / 'new-library-stage.json').exists()
print('Real private Calibre created an empty library; application databases and setup marker verified.')
print('ACL/cloud and program-overlap checks were substituted for the disposable source-tree fixture.')
print('Evidence:', state.parent)
'@
$probe | & $python -I -
if ($LASTEXITCODE -ne 0) { throw "Disposable new-library proof failed; inspect $work" }
