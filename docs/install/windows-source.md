# Windows portable source installation

Run the existing application on Windows x64 without Docker, WSL or a global Python/Calibre installation. Setup needs internet access and downloads the tools into the application folder. It does not change the system PATH.

1. Put the source in a writable folder and run `install-windows.cmd`.
2. Run `start-windows.cmd` and open the displayed local address.
3. Sign in and choose an option on **Set up your Calibre library**.
   - **Create a new library** uses the default `calibre-library` folder beside the CWNG installation or a custom path you enter or browse to. For example, `C:\Books\CWNG` uses `C:\Books\calibre-library` by default. CWNG creates missing folders. If the destination already contains `metadata.db`, CWNG connects that library. If it contains other files but no `metadata.db`, choose an empty or new folder.
   - **Select an existing library** accepts the library folder or its `metadata.db` file.

The existing first-run login is `admin` / `admin123`. Change that password before allowing network access. The launcher listens on the local computer by default. `start-windows.cmd -ListenAddress 0.0.0.0` allows network access when intended.

After setup, CWNG opens the main web interface. If a library that was previously configured cannot be opened, **Database Configuration** shows troubleshooting guidance.

## Installer and app launcher

Download the Windows x64 asset ending in `-installer.exe` from the [fork's releases](https://github.com/dapih/Calibre-Web-NextGen/releases) and run it in a writable folder. It manages the `CWNG` folder beside it. After you confirm a new installation, it checks the fork's published Windows releases, verifies the selected asset against its GitHub SHA-256, and prepares the app, bundled tools, and browser interface. It opens the app after a successful install and then exits.

To install manually, extract the Windows ZIP into a new, writable folder named `CWNG`, run `install-windows.cmd`, and open `CWNG.exe`. The installer can manage that copy later when run from the parent folder.

If the app is already installed, the installer offers **Reinstall**, **Uninstall**, and **Cancel**. It also offers **Open CWNG** when the app is running. Reinstall checks for the latest release, asks whether to retain `.cwng-data`, and preserves internal library files. If you select an internal library, CWNG uses it after reinstalling. Preserved reading progress is tied to the original library, so changing to a different library in the installer requires choosing fresh settings. For installs started from a console, shut down the console instance before reinstalling or uninstalling.

For everyday use, run `CWNG.exe` inside the installed folder. It checks for the runtime and required files before starting. Left-click its tray icon to open the home page. Right-click for **Open CWNG**, **Restart**, and **Quit**. Restart opens the home page when the server is ready. Windows passes the URL to your default browser, which controls tab and focus behavior. Run `CWNG.exe --background` to start without opening the browser. Startup, restart, and quit show a progress window, and the launcher log is in `.cwng-data\windows-launcher.log`.

## Restarting and stopping

Wait for uploads, imports, and conversions to finish. From the tray, choose **Restart** or **Quit**. You can also open `http://127.0.0.1:8083/admin/view` as an administrator and choose **Restart** or **Shutdown** near the bottom. Restart waits for the Windows background workers and instance lock to finish before starting a replacement with the same isolated Python and UTF-8 options.

After shutdown, open the installed `CWNG.exe` or run `start-windows.cmd` to start again. For a console launch, Ctrl+C also stops the app. Closing the browser leaves the application running.

## Where files live

| Folder | Role | Contents |
| --- | --- | --- |
| `cps/`, `scripts/`, `frontend/` | Source | Server, setup and launch scripts, and frontend source |
| `cps/static/app/` | Generated | Built browser assets served by CWNG |
| `.cwng-runtime/` | Replaceable installation files | Stripped standalone Python, application dependencies, Calibre, ImageMagick and kepubify |
| `.cwng-data/config/` | Local data | Application databases, keys, logs, recovery markers and conversion scratch space |
| `.cwng-data/ingest/` | Local data | Default ingest folder shown at launch |
| `.cwng-data/calibre-config/` | Local data | Settings for the included Calibre tools |
| `../calibre-library/` | Local data | Default location for a new Calibre library, beside the installation |
| `.cwng-data/location.json` | Local data | Previous application location, used to update internal paths after a move |
| `library/`, `.cwng-data/library/`, or a selected path | Local data | An existing library can stay at its current location |

Git tracks the source and setup recipes. It ignores generated browser assets, the installed runtime, local data, the launcher binary, and the root-level `calibre-library/` and `library/` folders used by older or custom installations. Configuration can contain credentials, and libraries contain personal files. Keep libraries outside `.cwng-runtime/` so setup can safely replace tools. To keep development files separate from the instance you use, run setup from a dedicated copy of the source.

The runtime uses its own Python directly, without a virtual environment or an editable source installation. Setup removes the Python build metadata and frontend/Node build directories it creates. Pre-existing developer directories remain.

## Long Windows library paths

When the real library path exceeds 75 characters, the Windows integration first checks whether Windows provides a short name that fits Calibre's limit. This lets Calibre create notes and book directories directly in the library. Otherwise it uses a short junction under `%LOCALAPPDATA%\CWNG-Paths`. The web interface and saved settings retain the real library location. Windows resolves the short name or junction again after a move.

You can also create an empty library from the command line with `create-windows-library.cmd "D:\Books\My Library"`. With no path, the command uses `calibre-library` beside the CWNG installation. On systems that reject creating directories through junctions, choose a shorter library path if Windows cannot supply a short enough direct name. The bundled Calibre programs still apply their own path checks when opened directly.

## Existing Windows source installations

Older versions used `%LOCALAPPDATA%\Calibre-Web-NextGen` for configuration and `%USERPROFILE%\CWNG-Ingest` for ingest. If these exist and the portable installation has no location record, an ordinary launch stops with an explanation.

Stop the old application and its import processes, then run:

```bat
start-windows.cmd -ImportLegacyData
```

This copies both old folders into `.cwng-data`. It retains the originals, refuses to merge or overwrite existing local config/ingest folders, and keeps selected external Calibre libraries at their existing locations. It adjusts configuration references into the copied folders. Check the copied installation before deciding whether to remove the old profile folders or an old unused `.venv` yourself.

If copying fails, the original folders remain. If local config/ingest folders already exist, review them before retrying; the launcher deliberately does not choose between two sets of data.

## Moving, updating and removal

Wait for imports to finish and stop the application before moving it. Move the entire application folder, including `.cwng-runtime`, `.cwng-data`, and any library inside it, then open the installed `CWNG.exe` or run `start-windows.cmd` from the new location. The launcher updates known filesystem settings that pointed inside the old application folder, including an internal library and tool paths. External libraries, including the default sibling folder, keep their saved paths. If you also move one of those libraries, select its new location in **Database Configuration**. Windows Calibre aliases are regenerated from the destination path.

A linked Git worktree also contains Git administrative references outside its folder. Use `git worktree move` when moving the development checkout and preserving Git operations. An application-only copy can run independently of Git; retain `cps`, `scripts`, `.cwng-runtime`, `.cwng-data`, `start-windows.cmd`, `VERSION`, `LICENSE` and `dirs.json`. Keep the full source checkout if you want to update or rebuild it with setup.

To update an installer-managed copy, run the Windows installer and choose **Reinstall**. If you have the older `cwng-installer.exe`, download a new installer once from the latest release tagged `cwng-windows-v…` and save it beside your existing `CWNG` folder. The older installer only finds releases with `windows-v…` tags. Choose to retain `.cwng-data` to keep your accounts, reading progress, settings, and pending ingest.

For a source checkout, stop the application, back up `.cwng-data` and your library, update the source, and rerun `install-windows.cmd`. Source setup checks the installed tool receipts and stages replacements when pins change.

### Uninstall on Windows

Run the Windows installer and choose **Uninstall**. It asks separately about retaining `.cwng-data` and each detected internal library. Empty default library folders are omitted. Retained files stay inside `CWNG` and are reused on a later install. A clean uninstall removes `CWNG`. The sibling `calibre-library` and other libraries outside `CWNG` remain in place.

For source or older installations, or to remove the folder manually, follow these steps.

1. **Stop CWNG.** Wait for uploads, imports, and conversions to finish. If you used `CWNG.exe`, choose **Quit** in the system tray and wait for its icon to disappear. For a console launch, use Admin **Shutdown** or press Ctrl+C in the console and wait for the launcher to exit. If an import was interrupted, follow the [recovery guidance](#interrupted-imports) before discarding its files.
2. **Save the data you want to keep.** After shutdown, copy `.cwng-data` to a location outside the app folder to keep settings, accounts, reading progress, pending ingest, and the bundled Calibre settings. Back up or move any library inside the app folder. Copy the whole library folder, including `metadata.db` and the book files. The default sibling `calibre-library` and other external libraries remain at their existing paths. The old profile folders retained by [legacy import](#existing-windows-source-installations) also stay separate.
3. **Remove the installation.** Delete the stopped `CWNG` folder, or the folder you used for a dedicated source installation. If you want to keep the source checkout, remove `.cwng-runtime`, `cps\static\app`, and `.cwng-data` from that checkout after saving the data you want. You can also remove a local `CWNG.exe`, the separate setup executable, and any shortcuts you created. If Windows reports that the folder is in use, verify that CWNG and its import workers have exited and close terminals or editors using the folder before retrying.

#### Optional long-path cache cleanup

An installation that needed a long-path fallback can leave junctions in `%LOCALAPPDATA%\CWNG-Paths`. Other CWNG installations can share this cache. You can leave it in place. To clean it up, list the junctions and check each `Target` before choosing a junction used only by the installation you removed.

```powershell
$cache = Join-Path $env:LOCALAPPDATA 'CWNG-Paths'
Get-ChildItem -LiteralPath $cache -Directory -Force |
    Where-Object LinkType -eq 'Junction' |
    Select-Object Name, Target
```

Replace `<junction-name>` with the verified name from the list. This deletes only the junction entry and leaves its target library in place.

```powershell
$alias = Get-Item -LiteralPath (Join-Path $cache '<junction-name>') -Force
if ($alias.LinkType -eq 'Junction') { $alias.Delete() }
```

## Interrupted imports

The Windows launcher sets `.cwng-data\ingest` as its portable default and uses the existing ingest processor.

The Windows worker imports stable files after a library has been configured. A failed or interrupted import stops the worker and leaves `unfinished-ingest.json` in `.cwng-data\config`; the original and working copy are retained. Check `background-ingest.log` and the library before deciding whether to retry. After resolving the interrupted import, remove the marker to resume. After a forced stop, make sure the import process has ended before restarting. Configuration and ingest paths in the recovery marker are adjusted when their parent installation moves.

## Building and publishing Windows releases

Bring stable upstream releases into the fork and review urgent upstream fixes individually. The fork's `main` accepts direct fast-forward pushes. Use a PR when you want review.

The **Windows package and smoke test** check is optional for updates to `main`. On PRs that change package inputs or code, it builds the ZIP, installs and starts CWNG, and imports a book. For documentation-only PRs, it checks the changed paths. Changes to the README bundled in the ZIP run the full smoke check.

Source setup reads the shared Python, Calibre and kepubify versions from the root `Dockerfile`. When an upstream pin changes, add the verified SHA-256 for the corresponding Windows artifact to [`scripts/windows-tool-checksums.psd1`](../../scripts/windows-tool-checksums.psd1). Setup requires those checksums before provisioning and verifies each download before extraction. ImageMagick and build-only Node use their own Windows pins.

To build locally, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows-build-launcher.ps1` from a Windows checkout. It writes `cwng-installer.exe`, `CWNG.exe`, and `cwng-windows.zip` to `dist\windows`. The ZIP contains the app launcher, a Windows-only `README.md`, and the tool versions in `scripts/windows-tool-versions.psd1`.

Run [**Windows package**](https://github.com/dapih/Calibre-Web-NextGen/actions/workflows/windows-release.yml) on `main`. A blank tag runs the install and upgrade smoke checks without creating a draft. To prepare a release, enter `cwng-windows-vX.Y.Z-rN` using the app version in `VERSION`. Increment `N` for another build of that version. The workflow checks the upgrade from the last published Windows ZIP, then creates a draft titled **CWNG for Windows vX.Y.Z-rN** with these assets.

- `cwng-windows-vX.Y.Z-rN-installer.exe`
- `cwng-windows-vX.Y.Z-rN.zip`

Use the pending Windows changelog entries to write the draft notes. Review the title, filenames, and notes, then publish the draft on GitHub. GitHub locks the assets after publication. Archive the entries under the published tag and date. [Windows changelog instructions](../../changelog.d/README.md#windows-release-fragments)
