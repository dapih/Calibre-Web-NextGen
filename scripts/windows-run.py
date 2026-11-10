"""Run the web app and its Windows background workers."""
import os
from pathlib import Path
import sys
import threading

# -I ignores inherited Python paths. Always load this checkout, including after
# moving it, rather than a source path recorded by an editable installation.
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from windows_portable import portable_environment
from windows_tray_control import tray_control


def main():
    with portable_environment(ROOT):
        from cps import web_server
        web_server.defer_restart = True
        try:
            with tray_control(web_server):
                run()
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        restart = web_server.restart
    # windows-start.ps1 reserves 75 for a requested restart. Its original
    # command runs again only after both workers and running.lock are released.
    sys.exit(75 if restart else 0)


def run():
    from windows_background_ingest import BackgroundIngest
    from windows_source_metadata import BackgroundMetadata

    state = Path(os.environ['CALIBRE_DBPATH'])
    inbox = Path(os.environ['CWA_INGEST_FOLDER'])
    writer_lock = threading.Lock()
    ingest = BackgroundIngest(inbox, state, writer_lock=writer_lock)
    metadata = BackgroundMetadata(writer_lock=writer_lock)
    ingest.start()
    metadata.start()
    try:
        from cps.main import main as run_web
        run_web()
    finally:
        metadata.stop()
        ingest.stop()


if __name__ == '__main__':
    main()
