"""Create an empty app-local Calibre library, including at long Windows paths."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from calibre_library_target import local_library_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=str(ROOT / '.cwng-data' / 'library'))
    args = parser.parse_args()
    directory = Path(args.directory).expanduser().absolute()
    if (directory / 'metadata.db').exists():
        raise SystemExit(f'A library already exists at {directory}. It has been left unchanged.')
    if directory.exists() and any(directory.iterdir()):
        raise SystemExit('Choose an empty directory for the new library.')
    binary = ROOT / '.cwng-runtime' / 'calibre' / 'calibredb.exe'
    if not binary.is_file():
        raise SystemExit('Run install-windows.cmd first.')
    directory.mkdir(parents=True, exist_ok=True)
    config = ROOT / '.cwng-data' / 'calibre-config'
    config.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'CALIBRE_CONFIG_DIRECTORY': str(config),
                   'CALIBRE_CACHE_DIRECTORY': str(ROOT / '.cwng-data' / 'calibre-cache')}
    subprocess.run([str(binary), 'list', '--with-library', local_library_path(directory)],
                   env=environment, check=True)
    print(f'Library created: {directory}')
    print('Select this directory on the Database Configuration page.')


if __name__ == '__main__':
    main()
