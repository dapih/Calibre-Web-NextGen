# Windows installation (in progress)

Setup downloads Python, Calibre and the supporting tools into the application folder. It does not require a global Python or a separate Calibre installation, and does not change the system PATH. An internet connection is needed during setup.

1. Run `install-windows.cmd` from the application folder. Setup downloads the required components.
2. Run `start-windows.cmd`, open the displayed local address and configure your Calibre library through the web interface. If you do not have a library yet, create one with the included `.cwng-runtime\calibre\calibre.exe` first.

On first run the application uses its existing `admin` / `admin123` login. Change that password before allowing network access. The start script listens on the local computer by default; use `-ListenAddress 0.0.0.0` only when you intend to allow network access.

Configuration is stored in `%LOCALAPPDATA%\Calibre-Web-NextGen`. The start script displays the ingest folder. The Windows worker imports stable files after a library has been configured. If an import is interrupted or fails, it stops and leaves `unfinished-ingest.json` in the configuration directory for review; the original and working copy are retained. Check `background-ingest.log` and the library before deciding whether to retry; after resolving the interrupted import, remove the marker to resume. After a forced stop, make sure the import process has ended before restarting.

This installation is still in development: direct OPDS acquisition has not yet been verified through a complete Calibre import. Newznab and external download-client integrations are unavailable on Windows.

To update, update the source checkout and rerun `install-windows.cmd`. To remove the application, delete its `.venv`, `.cwng-runtime` and source checkout. Configuration and the default ingest folder are kept separately. Check where your Calibre library and other data are stored first: if you put them inside the source checkout, move or back them up before deleting it.

The earlier M0/M1/M2/M3 documents describe historical experiments. The target is the existing application on Windows, without a bundled all-in-one installer or a separate global runtime requirement.
