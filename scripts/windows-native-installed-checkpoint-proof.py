"""Capture a checkpoint from a named disposable installed-package proof only.

The installed files must be under this worktree's .m0 and the profile must be
a `cwng-installed-proof-*` directory in the operating-system temporary folder.
This does not activate, migrate, restore or open a user's existing collection.
"""
import argparse
import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', required=True)
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    program = Path(args.program).resolve(strict=True)
    state = Path(args.state).resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    work = state.parent
    if (not program.is_relative_to((root / '.m0').resolve(strict=True)) or
            not work.is_relative_to(temporary) or not work.name.startswith('cwng-installed-proof-') or
            state.name != 's'):
        raise ValueError('only a disposable installed-package proof profile is permitted')
    recovery = work / ('checkpoint-proof-' + uuid.uuid4().hex)
    recovery.mkdir()
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    subprocess.run(['icacls', str(recovery), '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
                    '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                    '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    sys.path.insert(0, str(root / 'scripts'))
    from windows_native_checkpoint import create_installed_checkpoint, maintenance_session, verify_checkpoint
    from windows_native_launcher import _read_profile
    controller = _read_profile(state)
    version = (program / 'active-version.txt').read_text(encoding='ascii').strip()
    controller.web_entrypoint = program / 'versions' / version / 'scripts' / 'windows_native_web.py'
    with maintenance_session(controller) as session:
        checkpoint = create_installed_checkpoint(controller, recovery, program, session=session)
        manifest = verify_checkpoint(checkpoint)
        if not manifest['payload_identity'].startswith('sha256:'):
            raise RuntimeError('installed payload identity was not captured')
    print('Verified capture-only checkpoint of disposable installed payload and profile:', checkpoint)
    print('Publisher authenticity, release compatibility and rollback remain unverified.')


if __name__ == '__main__':
    main()
