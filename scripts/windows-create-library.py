"""Create an empty Calibre library for the Windows installation."""
import argparse
from pathlib import Path
import sys
from subprocess import SubprocessError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from windows_source_library import default_library_path, prepare_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=default_library_path(ROOT))
    args = parser.parse_args()
    directory = Path(args.directory).expanduser().absolute()
    try:
        directory, created = prepare_library(ROOT, directory, create=True)
    except (OSError, RuntimeError, ValueError, SubprocessError) as error:
        if getattr(error, 'stderr', None):
            print(error.stderr.decode('utf-8', errors='replace'), file=sys.stderr)
        raise SystemExit(str(error)) from error
    if not created:
        raise SystemExit(f'A library already exists at {directory}. It has been left unchanged.')
    print(f'Library created: {directory}')
    print("Select this directory in CWNG's library setup or Database Configuration.")


if __name__ == '__main__':
    main()
