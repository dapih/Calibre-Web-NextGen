"""Cross-language recovery inventory prototype; it cannot authorize launch."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows native verifier')
def test_native_inventory_rejects_changed_missing_and_extra_entries(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    compiler = repo / '.m0/mingw64/bin/gcc.exe'
    if not compiler.is_file():
        pytest.skip('private compiler is unavailable')
    verifier = tmp_path / 'verify.exe'
    subprocess.run([str(compiler), '-std=c11', '-municode', '-static', '-Wall', '-Wextra', '-Werror',
                    str(repo / 'scripts/windows-native-recovery-inventory.c'), '-lbcrypt', '-o', str(verifier)], check=True)
    sys.path.insert(0, str(repo / 'scripts'))
    try:
        from windows_native_recovery_inventory import build_inventory
        payload = tmp_path / 'payload'
        (payload / 'Ω' / 'empty').mkdir(parents=True)
        original = payload / 'Ω' / 'text.txt'
        original.write_bytes(b'original')
        manifest = tmp_path / 'inventory.bin'
        manifest.write_bytes(build_inventory(payload))
        expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
        def check():
            return subprocess.run([str(verifier), str(payload), str(manifest), expected], timeout=20).returncode
        assert check() == 0
        original.write_bytes(b'changed')
        assert check() != 0
        original.write_bytes(b'original')
        (payload / 'extra').write_bytes(b'extra')
        assert check() != 0
        (payload / 'extra').unlink()
        original.unlink()
        assert check() != 0
        original.write_bytes(b'original')
        original_manifest = manifest.read_bytes()
        manifest.write_bytes(original_manifest + b'garbage')
        assert check() != 0
        # Even a digest matching a malformed record cannot authorize it.
        expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert check() != 0
        manifest.write_bytes(original_manifest)
        expected = hashlib.sha256(original_manifest).hexdigest()
        link = payload / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(tmp_path)], capture_output=True)
        if result.returncode == 0:
            assert check() != 0
            os.rmdir(link)
        alias = tmp_path / 'alias'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(payload.parent)], capture_output=True)
        if result.returncode == 0:
            via_alias = alias / payload.name
            assert subprocess.run([str(verifier), str(via_alias), str(manifest), expected], timeout=20).returncode != 0
            with pytest.raises(ValueError, match='reparse point'):
                build_inventory(via_alias)
            os.rmdir(alias)
        # NTFS named streams do not appear in ordinary directory iteration.
        for stream_path in (str(original) + ':hidden', str(payload / 'Ω') + ':hidden', str(payload) + ':hidden'):
            with open(stream_path, 'wb') as stream:
                stream.write(b'hidden')
            assert check() != 0
            with pytest.raises(ValueError, match='named stream'):
                build_inventory(payload)
            os.remove(stream_path)
        external_link = tmp_path / 'external-hardlink'
        try:
            os.link(original, external_link)
        except OSError:
            pass  # The test volume may not support creating hard links.
        else:
            assert check() != 0
            with pytest.raises(ValueError, match='hard-linked'):
                build_inventory(payload)
            external_link.unlink()
        deep = payload
        for _ in range(20):
            deep = deep / 'd'
            deep.mkdir()
        manifest.write_bytes(build_inventory(payload))
        expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert check() == 0
    finally:
        sys.path.pop(0)
