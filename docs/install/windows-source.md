# Windows source installation (in progress)

This installation uses Python and Calibre installed separately. It does not bundle either program.

1. Install 64-bit Python 3.13 and Calibre for Windows. If Calibre is installed outside its default location, add its installation folder to PATH. ImageMagick and kepubify are needed for the features that use them and can be installed separately.
2. Open PowerShell in the application source directory and run `./scripts/windows-install.ps1`. If Node.js and npm are installed, setup also builds the newer web interface; otherwise the classic interface is used. To add the newer interface later, install Node.js and rerun setup.
3. Run `./scripts/windows-start.ps1` and open the displayed local address. Configure your Calibre library through the web interface.

On first run the application uses its existing `admin` / `admin123` login. Change that password before allowing network access. The start script listens on the local computer by default; use `-ListenAddress 0.0.0.0` only when you intend to allow network access.

Configuration is stored in `%LOCALAPPDATA%\Calibre-Web-NextGen`. The start script displays the ingest folder. The Windows worker imports stable files from that folder after a library has been configured. If an import is interrupted or fails, it stops and leaves `unfinished-ingest.json` in the configuration directory for review; the original and working copy are retained. Check `background-ingest.log` and the library before deciding whether to retry; after resolving the interrupted import, remove the marker to resume. After a forced stop, make sure the import process has ended before restarting.

This source-based path is still in development: direct OPDS acquisition has not yet been verified through a complete Calibre import. Newznab and external download-client integrations are unavailable on Windows. The experimental installer is not required.

To update, update the source checkout and rerun `./scripts/windows-install.ps1`. To remove the application, delete its `.venv` and source checkout. Configuration and the default ingest folder are kept separately. Check where your Calibre library and other data are stored first: if you put them inside the source checkout, move or back them up before deleting it.

The earlier M0/M1/M2/M3 documents describe historical experiments. The target is the existing application on Windows, using a normal Python environment and existing external tools; bundling Calibre or building an installer framework is not a prerequisite.
