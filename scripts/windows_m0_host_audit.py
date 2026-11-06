"""Compare relevant host settings around a representative private-tool run."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import winreg


ROOT = Path(__file__).resolve().parent.parent


def environment_registry(hive, key):
    with winreg.OpenKey(hive, key) as handle:
        count = winreg.QueryInfoKey(handle)[1]
        values = [winreg.EnumValue(handle, index) for index in range(count)]
    # Hash in memory: user environment values may include secrets.
    return hashlib.sha256(repr(sorted(values, key=lambda value: value[0].casefold())).encode('utf-8')).hexdigest()


def tree_fingerprint(root):
    """Record names, sizes and modification times, without reading user files."""
    if not root.exists():
        return {'exists': False}
    entries = []
    def on_error(error):
        raise error
    for directory, subdirectories, filenames in os.walk(root, followlinks=False, onerror=on_error):
        for name in sorted(subdirectories + filenames):
            path = Path(directory) / name
            stat = path.lstat()
            entries.append((str(path.relative_to(root)), stat.st_mode, stat.st_size, stat.st_mtime_ns))
    digest = hashlib.sha256(repr(sorted(entries)).encode('utf-8')).hexdigest()
    return {'exists': True, 'entries': len(entries), 'fingerprint': digest}


def snapshot():
    appdata = Path(os.environ['APPDATA'])
    local = Path(os.environ['LOCALAPPDATA'])
    return {
        'user_environment': environment_registry(winreg.HKEY_CURRENT_USER, r'Environment'),
        'machine_environment': environment_registry(
            winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
        'user_python_packages': tree_fingerprint(appdata / 'Python'),
        'user_calibre_settings': tree_fingerprint(appdata / 'calibre'),
        'user_calibre_cache': tree_fingerprint(local / 'calibre'),
    }


def main():
    before = snapshot()
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts' / 'windows_m0_calibre_origin.py')],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=120)
    after = snapshot()
    changed = [key for key in before if before[key] != after[key]]
    evidence = {'scope': 'environment registry and Python/Calibre user trees; file metadata fingerprints',
                'before': before, 'after': after, 'changed': changed, 'tool_exit': result.returncode,
                'tool_output': result.stdout.strip()}
    output = ROOT / '.m0' / 'windows-m0-host-audit.json'
    output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    if result.returncode:
        raise RuntimeError('private-tool proof failed: ' + result.stderr[-2000:])
    if changed:
        raise RuntimeError('host settings changed: ' + ', '.join(changed))
    print('Host environment and user Python/Calibre trees unchanged:', output)
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
