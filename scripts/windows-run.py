"""Run the web app and its Windows background workers."""
import os
from pathlib import Path
import threading

from windows_background_ingest import BackgroundIngest
from windows_source_metadata import BackgroundMetadata


def main():
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
