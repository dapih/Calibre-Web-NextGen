"""Exercise a disposable installed payload with real setup and Flask startup.

Run with the *installed* private Python, not the source-tree runtime. This is a
backend integration proof; the interactive setup/tray and release gates remain
separate. It leaves the disposable test data under the user's temporary folder.
"""
import argparse
import csv
import ctypes
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload', required=True)
    args = parser.parse_args()
    root = Path(args.payload).resolve(strict=True)
    runtime = root / 'runtime'
    helper = root / 'cwng-native.dll'
    calibre = root / 'calibre'
    kepubify = root / 'kepubify/kepubify.exe'
    magick = root / 'imagemagick'
    if Path(sys.prefix).resolve() != runtime or not Path(sys.executable).resolve().is_relative_to(runtime):
        raise RuntimeError('invoke this proof with the installed private interpreter')
    if not all(path.exists() for path in (helper, calibre, kepubify, magick)):
        raise RuntimeError('the installed payload is incomplete')

    work = Path(tempfile.mkdtemp(prefix='cwng-installed-proof-'))
    short = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(work), short, len(short)):
        raise ctypes.WinError()
    work = Path(short.value)
    state, ingest, destination = (work / name for name in ('s', 'i', 'd'))
    for directory in (state, ingest, destination):
        directory.mkdir()
    (state / 'config').mkdir()
    system = Path(os.environ['SystemRoot']) / 'System32'
    whoami = subprocess.check_output([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([whoami.strip()]))[1]
    subprocess.run([str(system / 'icacls.exe'), str(state), '/inheritance:r', '/remove:g',
                    '*S-1-5-32-545', '*S-1-5-11', '/grant:r', '*' + sid + ':(OI)(CI)F',
                    '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    subprocess.run([str(system / 'icacls.exe'), str(state / 'config'), '/reset'], check=True, capture_output=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
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
    subprocess.run([str(runtime / 'Scripts/python.exe'), '-I', '-B', '-c', setup, str(root), str(state),
                    str(helper), str(runtime), str(calibre), str(kepubify), str(magick),
                    str(destination / 'l'), str(ingest), str(port)], cwd=state, env=env, check=True)
    sys.path.insert(0, str(root / 'scripts'))
    from windows_native_launcher import _read_profile
    controller = _read_profile(state)
    try:
        assert controller.start() is None
        print('Installed private Flask/owner/scheduler:', controller.step(.1), flush=True)
        controller.pause()
        assert controller.start() is None
        print('Installed private controller stopped and restarted.', flush=True)
    finally:
        controller.close()
    print('Disposable proof:', work, flush=True)


if __name__ == '__main__':
    main()
