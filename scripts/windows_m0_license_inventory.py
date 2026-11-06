"""Inventory declared licenses in the hash-locked experimental Windows wheels.

Declarations and included license filenames are evidence for review, not a
legal conclusion about bundled native libraries or redistribution rights.
"""
from email import policy
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / 'scripts' / 'windows-m0-runtime-requirements.txt'
WHEELHOUSES = [ROOT / '.m0' / name for name in ('n1-wheelhouse-locked', 'magic-repaired', 'drive-repaired', 'security-wheels')]
OUTPUT = ROOT / '.m0' / 'windows-m0-wheel-licenses.json'


def main():
    locked = {}
    for line in LOCK.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([^\s]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})', line)
        if not match or match[3] in locked:
            raise ValueError('unsupported or duplicate lock entry: ' + line)
        locked[match[3]] = (match[1], match[2])
    records = []
    seen = set()
    for path in (path for directory in WHEELHOUSES for path in sorted(directory.glob('*.whl'))):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest not in locked or digest in seen:
            continue
        seen.add(digest)
        with zipfile.ZipFile(path) as archive:
            files = archive.namelist()
            metadata = [name for name in files if name.endswith('.dist-info/METADATA') and name.count('/') == 1]
            vendored_metadata = [name for name in files if name.endswith('.dist-info/METADATA') and name.count('/') > 1]
            if len(metadata) != 1:
                raise ValueError('wheel has missing or ambiguous metadata: ' + path.name)
            info = BytesParser(policy=policy.compat32).parsebytes(archive.read(metadata[0]))
            name, version = locked[digest]
            normalize = lambda value: re.sub(r'[-_.]+', '-', value).lower()
            if normalize(info['Name']) != normalize(name) or info['Version'] != version:
                raise ValueError('wheel metadata disagrees with lock: ' + path.name)
            license_files = [name for name in files if '.dist-info/' in name and
                             ('/licenses/' in name.lower() or
                              re.search(r'/(license|copying|notice)(\.[^/]*)?$', name, re.I))]
            records.append({'name': name, 'version': version, 'wheel': path.name, 'sha256': digest,
                            'license_expression': info.get('License-Expression'),
                            'license_declared': info.get('License'),
                            'classifiers': info.get_all('Classifier', []),
                            'license_files': license_files, 'vendored_metadata_files': vendored_metadata})
    if seen != set(locked):
        raise ValueError('wheelhouse is missing locked wheels')
    OUTPUT.write_text(json.dumps({'scope': 'wheel metadata, not legal review', 'wheels': records},
                                 indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    missing = sum(not (r['license_expression'] or r['license_declared'] or
                       any(c.startswith('License ::') for c in r['classifiers'])) for r in records)
    print(f'Inventoried {len(records)} verified wheels; {missing} lack a license declaration in metadata: {OUTPUT}')


if __name__ == '__main__':
    main()
