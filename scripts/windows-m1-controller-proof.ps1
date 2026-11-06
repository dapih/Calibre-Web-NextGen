# Exercise the real configured-profile web/owner/scheduler lifecycle using a
# stopped, disposable M0 fixture. No user collection may be supplied.
param(
    [Parameter(Mandatory=$true)][string]$Runtime,
    [Parameter(Mandatory=$true)][string]$NativeHelper,
    [Parameter(Mandatory=$true)][string]$Fixture
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cacheRoot = (Resolve-Path -LiteralPath (Join-Path $repoRoot '.m0')).Path
$fixtureRoot = (Resolve-Path -LiteralPath $Fixture).Path
if (-not $fixtureRoot.StartsWith($cacheRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Fixture must be a disposable stopped M0 fixture inside .m0.'
}
$pythonPath = Join-Path (Resolve-Path -LiteralPath $Runtime).Path 'Scripts/python.exe'
$helperPath = (Resolve-Path -LiteralPath $NativeHelper).Path
$proof = @'
import ctypes, json, os, pathlib, re, shutil, socket, sqlite3, sys, tempfile
import requests
from werkzeug.security import generate_password_hash
root, fixture, helper, runtime = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root / 'scripts'))
from windows_native_controller import NativeController
from windows_m0_inbox import _volume_identity
work = pathlib.Path(tempfile.mkdtemp(prefix='m1real', dir=root / '.m0'))
short = ctypes.create_unicode_buffer(32768)
if not ctypes.windll.kernel32.GetShortPathNameW(str(work), short, len(short)):
    raise ctypes.WinError()
work = pathlib.Path(short.value)
shutil.copytree(fixture / 'lib', work / 'lib')
shutil.copytree(fixture / 'cfg', work / 'cfg')
for name in ('state', 'incoming', 'tmp'):
    (work / name).mkdir()
app = work / 'cfg/app.db'
db = sqlite3.connect(app)
try:
    db.execute('UPDATE settings SET config_calibre_dir=?, config_calibre_split=0, config_kobo_sync=1',
               (str(work / 'lib'),))
    admin = db.execute('SELECT name FROM user WHERE role & 1 = 1 LIMIT 1').fetchone()[0]
    db.execute('UPDATE user SET password=? WHERE name=?', (generate_password_hash('m1-proof-password'), admin))
    db.commit()
finally:
    db.close()
database_info = (work / 'lib/metadata.db').stat()
(work / 'state/setup-complete.json').write_text(json.dumps({
    'schema': 1, 'library': str((work / 'lib').resolve()),
    'ingest': str((work / 'incoming').resolve()), 'app_db': str(app.resolve()),
    'library_volume': list(_volume_identity(work / 'lib')),
    'ingest_volume': list(_volume_identity(work / 'incoming')),
    'state_volume': list(_volume_identity(work / 'state')),
    'library_database': [database_info.st_dev, database_info.st_ino]}), encoding='utf-8')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
magick = root / '.m0/imagemagick-proof/dynamic/app'
calibre = root / '.m0/calibre-proof/extracted/Calibre Portable/Calibre'
env = {k: v for k, v in os.environ.items() if k.upper() in
       {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT', 'PROGRAMDATA'}}
env.update({'PATH': os.pathsep.join(map(str, (magick, calibre, pathlib.Path(os.environ['SystemRoot']) / 'System32'))),
            'HOME': str(work / 'cfg'), 'USERPROFILE': str(work / 'cfg'), 'APPDATA': str(work / 'cfg'),
            'LOCALAPPDATA': str(work / 'cfg'), 'TEMP': str(work / 'tmp'), 'TMP': str(work / 'tmp'),
            'CALIBRE_DBPATH': str(work / 'cfg'), 'CALIBRE_CONFIG_DIRECTORY': str(work / 'cfg'),
            'CALIBRE_CACHE_DIRECTORY': str(work / 'cfg/calibre-cache'), 'CWA_DB_PATH': str(work / 'cfg'),
            'CWA_APP_DB_PATH': str(app), 'CWA_INGEST_FOLDER': str(work / 'incoming'),
            'CWA_CALIBRE_LIBRARY_DIR': str(work / 'lib'), 'CWA_TMP_CONVERSION_DIR': str(work / 'tmp'),
            'CWA_METADATA_LOCK_DIR': str(work / 'cfg'), 'CWNG_CALIBRE_TOOLS': str(calibre),
            'CWNG_KEPUBIFY_PATH': str(root / '.m0/kepubify-proof/kepubify-windows-64bit.exe'),
            'MAGICK_HOME': str(magick), 'MAGICK_CONFIGURE_PATH': str(magick),
            'MAGICK_CODER_MODULE_PATH': str(magick / 'modules/coders')})
args = dict(helper=helper, state=work / 'state', runtime=runtime, library=work / 'lib',
            ingest=work / 'incoming', port=port, environment=env,
            web_entrypoint=root / 'scripts/windows_native_web.py', ready_timeout=45)
owner = NativeController(**args)
try:
    with sqlite3.connect(app) as db:
        db.execute('UPDATE settings SET config_use_https=1')
        db.commit()
    try:
        owner.start()
        raise AssertionError('native startup accepted unsupported HTTPS settings')
    except ValueError as error:
        assert 'HTTPS' in str(error), str(error)
    assert owner.process is None and not owner.generation_file.exists()
    with sqlite3.connect(app) as db:
        assert db.execute('SELECT config_use_https FROM settings').fetchone()[0] == 1
        db.execute('UPDATE settings SET config_use_https=0')
        db.commit()
    assert owner.start() is None
    unauthenticated = requests.Session()
    unauthenticated.trust_env = False
    kobo = unauthenticated.patch('http://127.0.0.1:' + str(port) + '/api/v3/content/test/annotations',
                                 data=b'{}', timeout=10)
    assert kobo.status_code == 401, ('Kobo annotation route unavailable or unauthenticated', kobo.status_code)
    unauthenticated.close()
    session = requests.Session()
    session.trust_env = False
    base = 'http://127.0.0.1:' + str(port)
    login = session.get(base + '/login', timeout=10)
    token = re.search(r'name="csrf_token"\s+value="([^"]+)"', login.text)
    assert token, 'login CSRF token missing'
    response = session.post(base + '/login', timeout=10, allow_redirects=False,
                            data={'username': admin, 'password': 'm1-proof-password', 'csrf_token': token.group(1)})
    assert response.status_code in (302, 303), response.status_code
    response = session.get(base + '/get_update_status', timeout=10)
    assert response.status_code == 200 and response.text == '', 'source update status still exposed'
    response = session.post(base + '/get_updater_status', timeout=10,
                            headers={'X-CSRFToken': token.group(1)}, data={'start': 'True'})
    assert response.status_code == 200 and response.text == '', 'source update route still enabled'
    response = session.get(base + '/admin/view', timeout=30)
    assert response.status_code == 200, response.status_code
    assert 'Check for updates from the CWNG tray.' in response.text
    assert 'id="updateNowDialog"' not in response.text and 'id="perform_update"' not in response.text
    response = session.get(base + '/admin/config', timeout=30)
    assert 'Change the update channel from the CWNG tray.' in response.text
    assert 'id="config_updatechannel"' not in response.text
    assert response.status_code == 200, response.status_code
    assert 'HTTPS is not yet supported in native Windows mode' in response.text
    assert 'LDAP and OAuth login are not yet supported in native Windows mode' in response.text
    assert 'id="config_certfile"' not in response.text and 'id="config_keyfile"' not in response.text
    assert 'id="config_use_https"' not in response.text
    with sqlite3.connect(app) as db:
        before = db.execute('SELECT config_use_https, config_certfile, config_keyfile, config_login_type FROM settings').fetchall()
    for fields in ({'config_use_https': 'on'}, {'config_certfile': 'fixture.pem'},
                   {'config_keyfile': 'fixture.key'}, {'config_login_type': '1'}, {'config_login_type': '2'}):
        response = session.post(base + '/admin/ajaxconfig', timeout=10,
                                headers={'X-CSRFToken': token.group(1)}, data=fields)
        assert response.status_code == 400, response.text
    for body in ({'ssl': {'use_https': True}}, {'ssl': {'certfile': 'fixture.pem'}},
                 {'ssl': {'keyfile': 'fixture.key'}}, {'login_type': 1}, {'login_type': 2},
                 {'login_type': '02'}, {'login_type': '+2'}, {'login_type': ' 2 '}, {'login_type': 2.0}):
        response = session.post(base + '/api/v1/admin/security', timeout=10,
                                headers={'X-CSRFToken': token.group(1)}, json=body)
        assert response.status_code == 400 and response.json()['error']['message']
    response = session.get(base + '/api/v1/admin/security', timeout=10)
    assert response.status_code == 200, response.text
    assert response.json()['native_http_only'] is True
    assert [item['id'] for item in response.json()['login_types']] == [0]
    with sqlite3.connect(app) as db:
        assert db.execute('SELECT config_use_https, config_certfile, config_keyfile, config_login_type FROM settings').fetchall() == before
    session.close()
    print('Authenticated legacy updater routes and Docker update UI are disabled.')
    print('First scheduler state:', owner.step(.1))
    owner.pause()
    assert isinstance(NativeController(**args).start(), dict)
    assert owner.start() is None
    print('Second scheduler state:', owner.step(.1))
    active = owner.scheduler.active
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(1, False, owner.process.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.TerminateProcess(handle, 23):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    if owner.process.wait(10000) is None:
        raise TimeoutError('web process did not exit after injected crash')
    try:
        owner.step(.1)
    except RuntimeError as error:
        assert 'web process is unavailable' in str(error), str(error)
    else:
        raise AssertionError('lost web process did not stop work')
    owner.pause()
    if active:
        record = owner.local.owner.ledger.get(active)
        assert record['state'] == 'review', record
        try:
            owner.start()
        except Exception as error:
            assert 'review' in str(error), str(error)
        else:
            raise AssertionError('ambiguous work did not block restart')
    print('Injected web crash stopped admission and retained interrupted work for review.')
finally:
    owner.close()
print('Real web readiness, owner reuse, stop/start and scheduler passed:', work)
'@
$proof | & $pythonPath -I - $repoRoot $fixtureRoot $helperPath (Resolve-Path -LiteralPath $Runtime).Path
if ($LASTEXITCODE -ne 0) { throw 'M1 configured-profile proof failed.' }
