# Windows portable source installation

Run the existing application on Windows x64 without Docker, WSL or a global Python/Calibre installation. Setup needs internet access and downloads the tools into the application folder. It does not change the system PATH.

1. Put the source in a writable folder and run `install-windows.cmd`.
2. Run `start-windows.cmd` and open the displayed local address.
3. Select an existing Calibre library on the Database Configuration page. To create an empty library, run `create-windows-library.cmd`; its default is `.cwng-data\library`. You can also pass a folder, for example `create-windows-library.cmd "D:\Books\My Library"`. Select the printed directory on the configuration page. The helper refuses to replace an existing library or use a nonempty folder.

The existing first-run login is `admin` / `admin123`. Change that password before allowing network access. The launcher listens on the local computer by default. `start-windows.cmd -ListenAddress 0.0.0.0` allows network access when intended.

Until a library is configured, browser login uses the classic login and database configuration pages. After configuration, the new web interface is the default.

## Restarting and stopping

Wait for uploads/conversions to finish, then open `http://127.0.0.1:8083/admin/view` as an administrator. Near the bottom, choose **Restart** or **Shutdown** and confirm. Restart waits for the Windows background workers and instance lock to finish before the launcher starts a replacement with the same isolated Python and UTF-8 options. Refresh the browser when it is ready. Legacy data copying runs only on the first launch, not on each restart.

After Shutdown, run `start-windows.cmd` to start again. If you started it in a visible console, Ctrl+C also stops it. Closing the browser leaves the application running.

## Where files live

| Folder | Role | Contents |
| --- | --- | --- |
| `cps/`, `scripts/`, `frontend/` | Source | Server, setup and launch scripts, and frontend source |
| `cps/static/app/` | Generated | Built browser assets served by CWNG |
| `.cwng-runtime/` | Replaceable installation files | Stripped standalone Python, application dependencies, Calibre, ImageMagick and kepubify |
| `.cwng-data/config/` | Local data | Application databases, keys, logs, recovery markers and conversion scratch space |
| `.cwng-data/ingest/` | Local data | Default ingest folder shown at launch |
| `.cwng-data/calibre-config/` | Local data | Settings for the included Calibre tools |
| `.cwng-data/library/` | Local data | Default location for a library made by the creation helper |
| `.cwng-data/location.json` | Local data | Previous application location, used to update internal paths after a move |
| `library/` or a selected path | Local data | An existing library can stay at its current location |

Git tracks the source and setup recipes. It ignores generated browser assets, the installed runtime, local data and a root-level `library/`. Setup downloads the pinned tools and installs the application's Python dependencies. Configuration can contain credentials, and libraries contain personal files. Keep libraries outside `.cwng-runtime/` so setup can safely replace tools. To keep development files separate from the instance you use, run setup from a dedicated copy of the source.

The runtime uses its own Python directly, without a virtual environment or an editable source installation. Setup removes the Python build metadata and frontend/Node build directories it creates. Pre-existing developer directories remain.

## Long Windows library paths

The Windows integration gives Calibre a short directory junction when the real library path exceeds 75 characters. These aliases are cached under `%LOCALAPPDATA%\CWNG-Paths`; they point to the actual folders and contain no book copies or application configuration. The web interface and saved settings retain the real library location. Aliases are derived from the current location and recreated as needed after a move. No drive letter or system-wide setting is needed.

Use `create-windows-library.cmd` to create a library in a deep folder. The bundled Calibre programs remain unchanged, so opening them directly outside this integration still uses Calibre's own path checks. Ordinary Windows filesystem/component limits still apply. An unusually long Windows user-cache path also needs an available Windows short name; the adapter reports an error if it cannot obtain a short enough cache path.

## Existing Windows source installations

Older versions used `%LOCALAPPDATA%\Calibre-Web-NextGen` for configuration and `%USERPROFILE%\CWNG-Ingest` for ingest. If these exist and the portable installation has no location record, an ordinary launch stops with an explanation.

Stop the old application and its import processes, then run:

```bat
start-windows.cmd -ImportLegacyData
```

This copies both old folders into `.cwng-data`. It retains the originals, refuses to merge or overwrite existing local config/ingest folders, and keeps selected external Calibre libraries at their existing locations. It adjusts configuration references into the copied folders. Check the copied installation before deciding whether to remove the old profile folders or an old unused `.venv` yourself.

If copying fails, the original folders remain. If local config/ingest folders already exist, review them before retrying; the launcher deliberately does not choose between two sets of data.

## Moving, updating and removal

Stop the application and wait for imports to finish before moving it. Move the entire application folder, including `.cwng-runtime`, `.cwng-data` and any library inside it, then run `start-windows.cmd` from the new location. Moving an installed folder requires only the launcher. The launcher updates known filesystem settings that pointed inside the old application folder, including an internal library and tool paths. Libraries stored elsewhere must still be available at their configured paths. Windows Calibre aliases are regenerated from the destination path.

A linked Git worktree also contains Git administrative references outside its folder. Use `git worktree move` when moving the development checkout and preserving Git operations. An application-only copy can run independently of Git; retain `cps`, `scripts`, `.cwng-runtime`, `.cwng-data`, `start-windows.cmd`, `VERSION`, `LICENSE` and `dirs.json`. Keep the full source checkout if you want to update or rebuild it with setup.

To update, stop the application, back up `.cwng-data` and your library, update the source and rerun `install-windows.cmd`. Setup checks the installed asset receipt for each tool and stages replacements before swapping them into `.cwng-runtime`. The first run with this setup refreshes tools installed before receipts were introduced. Existing configuration, ingest data and libraries stay in place.

Python, Calibre and kepubify use the shared version pins from the root `Dockerfile`. When merging an upstream pin change, add the verified SHA-256 for the corresponding Windows artifact to [`scripts/windows-tool-checksums.psd1`](../../scripts/windows-tool-checksums.psd1). Setup reports a missing checksum before touching the runtime. ImageMagick and build-only Node use their own Windows pins.

To remove the installation, stop it and delete its folder. Back up or move any config, pending ingest and libraries stored inside it first.

## Interrupted imports

The Windows worker imports stable files after a library has been configured. A failed or interrupted import stops the worker and leaves `unfinished-ingest.json` in `.cwng-data\config`; the original and working copy are retained. Check `background-ingest.log` and the library before deciding whether to retry. After resolving the interrupted import, remove the marker to resume. After a forced stop, make sure the import process has ended before restarting. Configuration and ingest paths in the recovery marker are adjusted when their parent installation moves.
