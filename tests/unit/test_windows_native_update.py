import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_update import InvalidUpdate, fetch_update, validate_update, verify_installer


def manifest():
    return {'repository': 'dapih/Calibre-Web-NextGen', 'channel': 'stable',
            'version': '4.2.0', 'revision': 1, 'architecture': 'x64',
            'minimum_windows_build': 22000, 'asset_name': 'CWNG-Setup.exe',
            'url': 'https://github.com/dapih/Calibre-Web-NextGen/releases/download/v4.2.0/CWNG-Setup.exe',
            'sha256': hashlib.sha256(b'installer').hexdigest()}


def validate(value):
    return validate_update(value, repository='dapih/Calibre-Web-NextGen', channel='stable',
                           current_version='4.1.62', current_revision=0, windows_build=22631)


def test_checks_installer_bytes(tmp_path):
    candidate = validate(manifest())
    path = tmp_path / 'setup.exe'
    path.write_bytes(b'installer')
    assert verify_installer(path, candidate)
    path.write_bytes(b'tampered')
    assert not verify_installer(path, candidate)


@pytest.mark.parametrize('field,value', [
    ('repository', 'somewhere/else'), ('channel', 'prerelease'),
    ('architecture', 'arm64'), ('minimum_windows_build', 30000),
    ('version', '4.2.0rc1'), ('revision', True),
    ('asset_name', 'else.exe'), ('sha256', 'x' * 64),
    ('url', 'https://example.com/CWNG-Setup.exe'),
    ('url', 'https://github.com/dapih/Calibre-Web-NextGen/releases/download/v9.9.9/CWNG-Setup.exe'),
])
def test_rejects_ineligible_or_mismatched_candidate(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(InvalidUpdate):
        validate(data)


def test_fetches_only_selected_fork_channel():
    import json
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(manifest()).encode()
    class Opener:
        def open(self, url, timeout):
            assert url == 'https://raw.githubusercontent.com/dapih/Calibre-Web-NextGen/windows-updates/stable.json'
            assert timeout == 10
            return Response()
    with patch('windows_native_update.build_opener', return_value=Opener()):
        candidate = fetch_update(repository='dapih/Calibre-Web-NextGen', channel='stable',
                                 current_version='4.1.62', current_revision=0, windows_build=22631)
    assert candidate.version == '4.2.0'


@pytest.mark.parametrize('version', ['4.1.62', '4.1.0'])
def test_same_or_older_release_is_up_to_date(version):
    data = manifest()
    data['version'] = version
    data['revision'] = 0
    data['url'] = f'https://github.com/dapih/Calibre-Web-NextGen/releases/download/v{version}/CWNG-Setup.exe'
    assert validate(data) is None
