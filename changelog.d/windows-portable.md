### Added

- **Windows installations can move with their settings and default ingest folder.** Local setup supplies Python, Calibre and supporting tools, while explicit legacy copying preserves existing data.

### Fixed

- **First-run browser login reaches library configuration.** Unconfigured installations no longer loop between login and the library page.
- **Deep Windows library folders work with the included integration.** Calibre uses short directory junctions without drive letters; saved library locations remain real paths.
- **Restart waits for Windows background work to finish.** The launcher releases its instance lock before relaunching with isolated imports and UTF-8.
- **Windows networking and download notices appear in French and Dutch.** The notices explain the native Windows network limitations and point users to OPDS when a download client is unavailable.
