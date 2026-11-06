"""Local integration proof using real setup, ACLs, cloud checks and controller.

Creates and retains a disposable profile under the operating-system temporary
directory. Run with the M0 private runtime; no existing user library is opened.
This exercises the setup backend, not the interactive setup window.
"""
import argparse
import ctypes
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    runtime = (root / '.m0/n1-runtime-locked').resolve(strict=True)
    helper = Path(args.helper).resolve(strict=True)
    calibre = (root / '.m0/calibre-proof/extracted/Calibre Portable/Calibre').resolve(strict=True)
    kepubify = (root / '.m0/kepubify-proof/kepubify-windows-64bit.exe').resolve(strict=True)
    magick = (root / '.m0/imagemagick-proof/dynamic/app').resolve(strict=True)
    # The path is created by this script and is deliberately not removed: it
    # contains the disposable proof evidence and no user's existing books.
    work = Path(tempfile.mkdtemp(prefix='cwng-m1-'))
    short = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(work), short, len(short)):
        raise ctypes.WinError()
    work = Path(short.value)
    state, ingest, destination = (work / name for name in ('s', 'i', 'd'))
    for directory in (state, ingest, destination):
        directory.mkdir()
    (state / 'config').mkdir()
    import csv
    user = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([user.strip()]))[1]
    subprocess.run(['icacls', str(state), '/inheritance:r', '/remove:g', '*S-1-5-32-545', '*S-1-5-11',
                    '/grant:r', '*' + sid + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F',
                    '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    # Ensure the already-created config directory inherits the restricted ACL.
    subprocess.run(['icacls', str(state / 'config'), '/reset'], check=True, capture_output=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    system = Path(os.environ['SystemRoot']) / 'System32'
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    env.update({'PATH': os.pathsep.join(map(str, (magick, calibre, system))),
                'HOME': str(state), 'USERPROFILE': str(state), 'APPDATA': str(state),
                'LOCALAPPDATA': str(state), 'TEMP': str(state), 'TMP': str(state),
                'CALIBRE_DBPATH': str(state / 'config'), 'CWA_DB_PATH': str(state / 'config'),
                'CWNG_TEST_PASSWORD': 'fixture-only-password'})
    setup = """
import os,pathlib,sys
root,state,helper,runtime,calibre,kepubify,magick,library,ingest,port=sys.argv[1:]
sys.path[:0]=[str(pathlib.Path(root)),str(pathlib.Path(root)/'scripts')]
from windows_native_new_library import complete_new_setup
complete_new_setup(state=state,helper=helper,runtime=runtime,calibre=calibre,kepubify=kepubify,
 magick=magick,library=library,ingest=ingest,username='fixture-admin',
 password=os.environ.pop('CWNG_TEST_PASSWORD'),port=int(port),storage_not_synced=True)
"""
    python = runtime / 'Scripts/python.exe'
    subprocess.run([str(python), '-I', '-c', setup, str(root), str(state), str(helper), str(runtime),
                    str(calibre), str(kepubify), str(magick), str(destination / 'l'), str(ingest), str(port)],
                   cwd=state, env=env, check=True)
    sys.path.insert(0, str(root / 'scripts'))
    from windows_native_launcher import _read_profile
    controller = _read_profile(state)
    try:
        assert controller.start() is None
        print('Configured profile, real first-run storage/ACL checks, Flask readiness and scheduler:',
              controller.step(.1), flush=True)
        controller.pause()
        assert controller.start() is None
        print('Configured profile stopped and restarted successfully.', flush=True)
    finally:
        controller.close()
    print('Disposable proof:', work, flush=True)


if __name__ == '__main__':
    main()
