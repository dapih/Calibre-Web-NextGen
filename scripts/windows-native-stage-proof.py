"""Assemble an unsigned, isolated local Windows installer proof payload.

This consumes the existing M0 evidence directory. It is not a release builder:
the inputs and their redistribution/security obligations still need review.
The output is always a new directory under .m0 and is never deleted here.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
M0 = ROOT / '.m0'


def copytree(source, destination, *, exclude_root_scripts=False):
    excluded = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pdb')

    def ignored(directory, names):
        result = set(excluded(directory, names))
        # Venv/pip wrapper executables can embed the build machine's absolute
        # Python path. The package creates only the direct worker binaries below.
        if exclude_root_scripts and Path(directory) == source:
            result.add('Scripts')
        return result

    shutil.copytree(source, destination, ignore=ignored)


def main():
    if os.name != 'nt':
        raise RuntimeError('the disposable proof must be assembled on Windows')
    inputs = {
        'python': M0 / 'python',
        'packages': M0 / 'n1-runtime-locked/Lib/site-packages',
        'assets': M0 / 'asset-stage/cps',
        'frontend': M0 / 'frontend-proof/build/cps/static/app',
        'calibre': M0 / 'calibre-proof/extracted/Calibre Portable/Calibre',
        'magick': M0 / 'imagemagick-proof/dynamic/app',
        'kepubify': M0 / 'kepubify-proof/kepubify-windows-64bit.exe',
        'gcc': M0 / 'mingw64/bin/gcc.exe',
    }
    for name, path in inputs.items():
        if not path.exists():
            raise FileNotFoundError(f'missing proof input {name}: {path}')
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip().removeprefix('v')
    if not version or any(c not in '0123456789.' for c in version) or '..' in version:
        raise ValueError('VERSION is not a valid numeric proof version')
    work = Path(tempfile.mkdtemp(prefix='installer-proof-', dir=M0))
    payload = work / 'payload'
    payload.mkdir()
    print('Preparing disposable payload:', work, flush=True)
    copytree(ROOT / 'cps', payload / 'cps')
    copytree(ROOT / 'scripts', payload / 'scripts')
    for name in ('cps.py', 'dirs.json', 'pyproject.toml', 'VERSION'):
        shutil.copy2(ROOT / name, payload / name)
    copytree(inputs['frontend'], payload / 'cps/static/app')
    shutil.copy2(inputs['assets'] / 'static/koplugin.zip', payload / 'cps/static/koplugin.zip')
    for catalog in (inputs['assets'] / 'translations').rglob('*.mo'):
        target = payload / 'cps/translations' / catalog.relative_to(inputs['assets'] / 'translations')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(catalog, target)
    print('Copying private runtime and tools...', flush=True)
    # Use real PBS interpreter executables, not venv redirectors: redirectors
    # do not propagate the maintenance-gate handle to the actual interpreter.
    copytree(inputs['python'], payload / 'runtime', exclude_root_scripts=True)
    shutil.copytree(inputs['packages'], payload / 'runtime/Lib/site-packages', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pdb'))
    (payload / 'runtime/Scripts').mkdir(exist_ok=True)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python313.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        shutil.copy2(inputs['python'] / name, payload / 'runtime/Scripts' / name)
    copytree(inputs['calibre'], payload / 'calibre')
    copytree(inputs['magick'], payload / 'imagemagick')
    (payload / 'kepubify').mkdir()
    shutil.copy2(inputs['kepubify'], payload / 'kepubify/kepubify.exe')
    subprocess.run([str(inputs['gcc']), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-shared',
                    '-static-libgcc', '-o', str(payload / 'cwng-native.dll'),
                    str(ROOT / 'scripts/windows-m0-native-helper.c')], check=True)
    bootstrap = work / 'CWNG.exe'
    subprocess.run([str(inputs['gcc']), '-std=c11', '-municode', '-mwindows', '-Wall', '-Wextra',
                    '-Werror', '-static', '-DCWNG_INSTALLER_PROOF', '-o', str(bootstrap),
                    str(ROOT / 'scripts/windows-native-bootstrap.c'), '-lshell32', '-luser32', '-ladvapi32'], check=True)
    identity = {'schema': 1, 'repository': 'dapih/Calibre-Web-NextGen', 'version': version,
                'revision': 0, 'architecture': 'x64'}
    (payload / 'native-package.json').write_text(json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    # Both executable paths must resolve the private Python and package trees.
    check_env = {key: value for key, value in os.environ.items()
                 if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    check_env.update({'PATH': str(Path(os.environ['SystemRoot']) / 'System32'),
                      'PYTHONHOME': r'C:\invalid-host-python', 'PYTHONPATH': r'C:\invalid-host-modules'})
    for executable in ('runtime/python.exe', 'runtime/Scripts/python.exe'):
        subprocess.run([str(payload / executable), '-I', '-B', '-c',
                        'import flask,magic,wand,tkinter,sqlalchemy,psutil,sys; print("Proof runtime:",sys.prefix)'],
                       cwd=work, env=check_env, check=True)
    inventory = {}
    for path in sorted(payload.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                inventory[path.relative_to(payload).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (work / 'payload-sha256.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    print('Payload:', payload, flush=True)
    print('Bootstrap:', bootstrap, flush=True)
    print('Version:', version, flush=True)
    print('Inventory:', work / 'payload-sha256.json', flush=True)
    print('This is experimental proof material; it is not approved for distribution.', flush=True)


if __name__ == '__main__':
    main()
