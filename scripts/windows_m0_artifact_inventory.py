"""Hash the actual local M0 runtime, tools and staged static assets.

This is a proof inventory, not a release manifest or license determination.
"""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.m0'


def inventory(label, root):
    root = root.resolve(strict=True)
    if not root.is_dir() or not root.is_relative_to(CACHE.resolve(strict=True)):
        raise ValueError('inventory root must be an existing private proof directory')
    records = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('unexpected link in artifact tree: ' + str(path))
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        records.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
                        'sha256': digest})
    if not records:
        raise ValueError('empty artifact tree: ' + str(root))
    return {'label': label, 'root': str(root), 'files': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--output', default=str(CACHE / 'windows-m0-artifact-inventory.json'))
    options = parser.parse_args()
    roots = [
        ('python-base', CACHE / 'python'),
        ('runtime', Path(options.runtime)),
        ('calibre', CACHE / 'calibre-proof' / 'extracted' / 'Calibre Portable' / 'Calibre'),
        ('imagemagick', CACHE / 'imagemagick-proof' / 'dynamic' / 'app'),
        ('assets', CACHE / 'asset-stage' / 'cps' / 'static'),
        ('translations', CACHE / 'translation-repaired'),
    ]
    evidence = {'scope': 'local M0 proof artifacts, not a release manifest',
                'trees': [inventory(label, root) for label, root in roots]}
    executable = CACHE / 'kepubify-proof' / 'kepubify-windows-64bit.exe'
    with executable.open('rb') as stream:
        evidence['kepubify'] = {'path': str(executable.resolve()), 'size': executable.stat().st_size,
                                'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    destination = Path(options.output).resolve()
    if not destination.is_relative_to(CACHE.resolve(strict=True)):
        raise ValueError('inventory output must remain in the private proof directory')
    destination.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Inventoried ' + ', '.join(f"{tree['label']}={len(tree['files'])}" for tree in evidence['trees']))
    print(destination)


if __name__ == '__main__':
    main()
