# Rebuild the experimental Windows wheel with private-only DLL/database loading.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$source = Join-Path $cache 'magic-alternative\python_magic_standalone-0.4.29-py2.py3-none-win_amd64.whl'
if (-not (Test-Path -LiteralPath $source)) { $source = Join-Path $cache 'security-wheels\python_magic_standalone-0.4.29-py2.py3-none-win_amd64.whl' }
$python = Join-Path $cache 'python\python.exe'
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne 'EE9767392B66D0D63BC2EC6C79930308585F5B5BCB7F749EB7B68FA1E5BBDFC2') { throw 'Unexpected source wheel hash.' }
$output = Join-Path $cache 'magic-repaired'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$target = Join-Path $output 'python_magic_standalone-0.4.29+cwng.m0-cp313-none-win_amd64.whl'
$oldSource = [Environment]::GetEnvironmentVariable('M0_WHEEL_SOURCE', 'Process')
$oldTarget = [Environment]::GetEnvironmentVariable('M0_WHEEL_TARGET', 'Process')
try {
    $env:M0_WHEEL_SOURCE = $source; $env:M0_WHEEL_TARGET = $target
    $build = @'
import base64, csv, hashlib, io, os, stat, zipfile
from pathlib import PurePosixPath

def digest(data):
    return 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()

with zipfile.ZipFile(os.environ['M0_WHEEL_SOURCE']) as z:
    entries = [i for i in z.infolist() if not i.is_dir()]
    names = [i.filename for i in entries]
    if len(names) != len(set(names)):
        raise ValueError('duplicate wheel member')
    for i in entries:
        p = PurePosixPath(i.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in i.filename or stat.S_ISLNK(i.external_attr >> 16):
            raise ValueError('unsafe wheel member: ' + i.filename)
    data = {i.filename: z.read(i) for i in entries}
records = [n for n in names if n.endswith('.dist-info/RECORD')]
if len(records) != 1: raise ValueError('unexpected RECORD count')
record = records[0]
if record + '.jws' in names or record + '.p7s' in names: raise ValueError('signed wheel')
rows = list(csv.reader(io.StringIO(data[record].decode(), newline='')))
if len(rows) != len(names) or {r[0] for r in rows} != set(names) or len({r[0] for r in rows}) != len(rows):
    raise ValueError('invalid RECORD members')
for row in rows:
    if len(row) != 3: raise ValueError('invalid RECORD row')
    name, h, size = row
    if name == record:
        if h or size: raise ValueError('invalid RECORD self entry')
    elif h != digest(data[name]) or size != str(len(data[name])):
        raise ValueError('RECORD mismatch: ' + name)

loader = data['magic/loader.py'].decode().replace('\r\n', '\n')
old = 'def load_lib():\n    exc = []\n'
new = '''def load_lib():
    if sys.platform == "win32":
        private = os.path.join(here, "libmagic-1.dll")
        if not os.path.isfile(private):
            raise ImportError("private libmagic DLL is missing")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        kernel.GetModuleFileNameW.restype = ctypes.c_ulong
        bundled = ("libmagic-1.dll", "libsystre-0.dll", "libtre-5.dll", "libiconv-2.dll", "libintl-8.dll")
        def check_origins():
            for name in bundled:
                handle = kernel.GetModuleHandleW(name)
                if not handle:
                    continue
                path = ctypes.create_unicode_buffer(32768)
                if not kernel.GetModuleFileNameW(handle, path, len(path)):
                    raise ImportError("cannot verify loaded DLL origin")
                expected = os.path.normcase(os.path.realpath(os.path.join(here, name)))
                actual = os.path.normcase(os.path.realpath(path.value))
                if actual != expected:
                    raise ImportError("foreign DLL already loaded: " + name)
        check_origins()
        # Search the DLL directory and Windows system32, never CWD or PATH.
        result = ctypes.CDLL(private, winmode=0x00000900)
        check_origins()
        return result
    exc = []
'''
if loader.count(old) != 1: raise ValueError('unexpected loader source')
data['magic/loader.py'] = loader.replace(old, new).encode()
init = data['magic/__init__.py'].decode().replace('\r\n', '\n')
old = '        if magic_file is None and not os.environ.get("MAGIC"):\n'
new = '''        if sys.platform == "win32" and magic_file is None:
            magic_file = os.path.join(os.path.dirname(__file__), "magic.mgc")
            if not os.path.isfile(magic_file):
                raise MagicException("private magic database is missing")
        if magic_file is None and not os.environ.get("MAGIC"):
'''
if init.count(old) != 1: raise ValueError('unexpected wrapper source')
data['magic/__init__.py'] = init.replace(old, new).encode()
wheel_metadata = [n for n in names if n.endswith('.dist-info/WHEEL')]
if len(wheel_metadata) != 1: raise ValueError('unexpected WHEEL count')
wheel_name = wheel_metadata[0]
wheel = data[wheel_name]
old_tag = b'Tag: cp312-cp312-win_amd64\n'
if wheel.count(old_tag) != 1: raise ValueError('unexpected upstream wheel tag')
data[wheel_name] = wheel.replace(old_tag, b'Tag: cp313-none-win_amd64\n')
old_info = record.rsplit('/', 1)[0]
if not old_info.endswith('-0.4.29.dist-info'): raise ValueError('unexpected distribution name')
new_info = old_info.replace('-0.4.29.dist-info', '-0.4.29+cwng.m0.dist-info')
metadata_name = old_info + '/METADATA'
data[metadata_name] = data[metadata_name].replace(b'\r\n', b'\n')
old_version = b'Version: 0.4.29\n'
if data[metadata_name].count(old_version) != 1: raise ValueError('unexpected package version')
data[metadata_name] = data[metadata_name].replace(old_version, b'Version: 0.4.29+cwng.m0\n', 1)
data = {(new_info + name[len(old_info):] if name.startswith(old_info + '/') else name): content
        for name, content in data.items()}
record = new_info + '/RECORD'
names = list(data)
text = io.StringIO(newline='')
writer = csv.writer(text, lineterminator='\n')
for name in sorted(n for n in names if n != record):
    writer.writerow((name, digest(data[name]), len(data[name])))
writer.writerow((record, '', ''))
data[record] = text.getvalue().encode()
with zipfile.ZipFile(os.environ['M0_WHEEL_TARGET'], 'w', compression=zipfile.ZIP_STORED) as out:
    for name in sorted(names):
        info = zipfile.ZipInfo(name, (2026, 6, 22, 20, 26, 40))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        out.writestr(info, data[name])
print('Repaired wheel SHA256:', hashlib.sha256(open(os.environ['M0_WHEEL_TARGET'], 'rb').read()).hexdigest())
'@
    $build | & $python -I -
    if ($LASTEXITCODE -ne 0) { throw 'Wheel repair failed.' }
} finally {
    foreach ($pair in @(@('M0_WHEEL_SOURCE',$oldSource), @('M0_WHEEL_TARGET',$oldTarget))) {
        if ($null -eq $pair[1]) { Remove-Item -LiteralPath ('Env:' + $pair[0]) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $pair[0]) -Value $pair[1] }
    }
}
