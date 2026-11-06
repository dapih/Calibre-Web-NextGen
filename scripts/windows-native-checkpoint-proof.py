"""Exercise a checkpoint of a fresh disposable real native profile.

This creates a new fixture via the first-run-to-launch proof and keeps it in the
OS temporary directory. It does not open or modify an existing user library.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    parser.add_argument('--prepared-crash', action='store_true',
                        help='Also exercise prepared-only process death using a synthetic payload')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    runtime = root / '.m0/n1-runtime-locked/Scripts/python.exe'
    result = subprocess.run([str(runtime), '-I', str(root / 'scripts/windows-native-first-run-to-launch-proof.py'),
                             '--helper', str(Path(args.helper).resolve(strict=True))],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', check=True)
    lines = [line.removeprefix('Disposable proof: ').strip() for line in result.stdout.splitlines()
             if line.startswith('Disposable proof: ')]
    if len(lines) != 1:
        raise RuntimeError('first-run proof did not identify its disposable profile')
    work = Path(lines[0]).resolve(strict=True)
    state = work / 's'
    recovery = work / 'r'
    recovery.mkdir()
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    subprocess.run(['icacls', str(recovery), '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
                    '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                    '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    sys.path.insert(0, str(root / 'scripts'))
    from windows_native_checkpoint import cancel_prepared, create_checkpoint, maintenance_session, verify_checkpoint
    from windows_native_launcher import _read_profile
    controller = _read_profile(state)
    with maintenance_session(controller) as session:
        checkpoint = create_checkpoint(controller, recovery, payload_identity='disposable-proof-only', session=session)
        manifest = verify_checkpoint(checkpoint, expected_payload_identity='disposable-proof-only')
        if manifest['scope'] != 'state-and-db':
            raise RuntimeError('unexpected checkpoint scope')
    print('Verified disposable stopped-profile checkpoint:', checkpoint)
    print('Payload identity is a fixture label; no installer or rollback claim.')
    if args.prepared_crash:
        program = work / 'synthetic-program'
        payload = program / 'versions' / '1.0.0'
        (payload / 'scripts').mkdir(parents=True)
        (program / 'active-version.txt').write_text('1.0.0\n', encoding='ascii')
        (program / 'CWNG.exe').write_bytes(b'synthetic bootstrap')
        (payload / 'native-package.json').write_text(json.dumps({'schema': 1, 'repository': 'dapih/Calibre-Web-NextGen',
            'version': '1.0.0', 'revision': 0, 'architecture': 'x64'}), encoding='utf-8')
        worker = payload / 'scripts' / 'windows_native_web.py'
        worker.write_text('# synthetic fixture; never executed\n', encoding='utf-8')
        source = r'''
import os, pathlib, sys
root, state, recovery, program = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root / 'scripts'))
from windows_native_checkpoint import maintenance_session, prepare_maintenance
from windows_native_launcher import _read_profile
controller = _read_profile(state)
controller.web_entrypoint = program / 'versions/1.0.0/scripts/windows_native_web.py'
with maintenance_session(controller) as session:
    prepare_maintenance(controller, recovery, program, session=session)
    os._exit(0)
'''
        subprocess.run([str(runtime), '-I', '-c', source, str(root), str(state), str(recovery), str(program)],
                       cwd=root, timeout=120, check=True)
        if not (state / 'maintenance-journal.json').is_file():
            raise RuntimeError('prepared journal was not preserved after process death')
        from windows_m0_coordinator import CoordinatorBusy
        try:
            controller.start()
        except CoordinatorBusy:
            pass
        else:
            controller.close()
            raise RuntimeError('normal startup was not fenced by maintenance journal')
        original_worker = controller.web_entrypoint
        controller.web_entrypoint = worker
        cancel_prepared(controller, program, recovery)
        controller.web_entrypoint = original_worker
        try:
            if controller.start() is not None:
                raise RuntimeError('unexpected duplicate owner after cancel')
            if not controller.readiness.verify():
                raise RuntimeError('application did not become ready after verified cancel')
        finally:
            controller.close()
        print('Verified process-death re-entry and real application restart; synthetic payload was never executed.')


if __name__ == '__main__':
    main()
