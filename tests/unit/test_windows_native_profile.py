"""Exercise the profile ACL gate using real disposable Windows ACLs."""
import csv
import os
from pathlib import Path
import subprocess
import sys

import pytest

if os.name != 'nt':
    pytest.skip('Windows profile ACLs', allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_native_profile import require_private_directory


def test_private_profile_rejects_inherited_and_independent_broad_access(tmp_path):
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    profile = tmp_path / 'profile'
    profile.mkdir()
    def acl(*args):
        subprocess.run(['icacls', *map(str, args)], check=True, capture_output=True)
    acl(profile, '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
        '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F',
        '*S-1-3-4:(OI)(CI)F')
    child = profile / 'control.key'
    child.write_text('fixture', encoding='ascii')
    assert require_private_directory(profile) == profile.resolve()
    acl(profile, '/grant', '*S-1-1-0:(OI)(CI)(IO)R')
    with pytest.raises(PermissionError):
        require_private_directory(profile)
    acl(profile, '/remove:g', '*S-1-1-0')
    acl(child, '/inheritance:r', '/grant', '*S-1-1-0:R')
    with pytest.raises(PermissionError):
        require_private_directory(profile)
