"""Build local, experimental PyDrive2/oauth2client wheels for M0."""

import base64
import csv
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parent.parent
WHEELHOUSE = ROOT / '.m0' / 'n1-wheelhouse-locked'
OUTPUT = ROOT / '.m0' / 'drive-repaired'
BACKEND = Path(__file__).with_name('windows_m0_oauth2_crypt.py')


def digest(data):
    return 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()


def repair(filename, expected, old_version, new_version, replace):
    source = WHEELHOUSE / filename
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError(f'unexpected wheel hash: {filename}')
    with zipfile.ZipFile(source) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in entries]
        if len(names) != len(set(names)):
            raise ValueError('duplicate wheel member')
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('unsafe wheel member')
        data = {item.filename: archive.read(item) for item in entries}
    records = [name for name in names if name.endswith('.dist-info/RECORD')]
    if len(records) != 1:
        raise ValueError('unexpected RECORD count')
    record = records[0]
    if record + '.jws' in names or record + '.p7s' in names:
        raise ValueError('signed wheel cannot be modified')
    rows = list(csv.reader(io.StringIO(data[record].decode(), newline='')))
    if len(rows) != len(names) or len({row[0] for row in rows}) != len(rows) or {row[0] for row in rows} != set(names):
        raise ValueError('invalid RECORD members')
    for row in rows:
        if len(row) != 3:
            raise ValueError('invalid RECORD row')
        name, hash_value, size = row
        if name == record:
            if hash_value or size:
                raise ValueError('invalid RECORD self entry')
        elif hash_value != digest(data[name]) or size != str(len(data[name])):
            raise ValueError(f'RECORD mismatch: {name}')
    old_info = record.rsplit('/', 1)[0]
    if not old_info.endswith('-' + old_version + '.dist-info'):
        raise ValueError('unexpected wheel version')
    new_info = old_info.replace('-' + old_version + '.dist-info', '-' + new_version + '.dist-info')
    data = {(new_info + name[len(old_info):] if name.startswith(old_info + '/') else name): content
            for name, content in data.items()}
    record = new_info + '/RECORD'
    metadata_name = new_info + '/METADATA'
    metadata = data[metadata_name].decode().replace('\r\n', '\n')
    old = f'Version: {old_version}\n'
    if metadata.count(old) != 1:
        raise ValueError('unexpected metadata version')
    metadata = metadata.replace(old, f'Version: {new_version}\n', 1)
    data[metadata_name] = replace(metadata, data).encode()
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    for name in sorted(name for name in data if name != record):
        writer.writerow((name, digest(data[name]), len(data[name])))
    writer.writerow((record, '', ''))
    data[record] = output.getvalue().encode()
    OUTPUT.mkdir(exist_ok=True)
    target = OUTPUT / filename.replace('-' + old_version + '-', '-' + new_version + '-')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(data):
            info = zipfile.ZipInfo(name, (2026, 6, 22, 20, 26, 40))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data[name])
    print(target.name, hashlib.sha256(target.read_bytes()).hexdigest())


def oauth(metadata, data):
    data['oauth2client/_openssl_crypt.py'] = BACKEND.read_bytes()
    return metadata.replace('Version: 4.1.3+cwng.m0\n',
                            'Version: 4.1.3+cwng.m0\nRequires-Dist: cryptography>=49,<51\n', 1)


def drive(metadata, data):
    for old, new in [
        ('Requires-Dist: cryptography <44\n', 'Requires-Dist: cryptography>=49,<51\n'),
        ('Requires-Dist: pyOpenSSL <=24.2.1,>=19.1.0\n', ''),
        ('Requires-Dist: oauth2client >=4.0.0\n', 'Requires-Dist: oauth2client==4.1.3+cwng.m0\n'),
    ]:
        if metadata.count(old) != 1:
            raise ValueError(f'unexpected PyDrive2 metadata: {old}')
        metadata = metadata.replace(old, new, 1)
    return metadata


if __name__ == '__main__':
    repair('oauth2client-4.1.3-py2.py3-none-any.whl',
           'b8a81cc5d60e2d364f0b1b98f958dbd472887acaf1a5b05e21c28c31a2d6d3ac',
           '4.1.3', '4.1.3+cwng.m0', oauth)
    repair('PyDrive2-1.21.3-py3-none-any.whl',
           '843a304f500e71508162807001f5e19487f272e8ff5648f43582bd24c6250200',
           '1.21.3', '1.21.3+cwng.m0', drive)
