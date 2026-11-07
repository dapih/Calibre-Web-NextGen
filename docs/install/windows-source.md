# Windows portable source installation

Run the existing application on Windows x64 without Docker, WSL or a global Python/Calibre installation. Setup needs internet access and downloads the tools into the application folder. It does not change the system PATH.

1. Put the source in a writable folder and run `install-windows.cmd`.
2. Run `start-windows.cmd` and open the displayed local address.
3. Configure a Calibre library through the web interface. To create a library, use the included `.cwng-runtime\calibre\calibre.exe`. Keep the library path short: the included Calibre rejects Windows library paths of 89 characters or more. A short external library folder is useful when the source checkout is deeply nested.

The existing first-run login is `admin` / `admin123`. Change that password before allowing network access. The launcher listens on the local computer by default. `start-windows.cmd -ListenAddress 0.0.0.0` allows network access when intended.

Until a library is configured, browser login uses the classic login and database configuration pages. After configuration, the new web interface is the default.

## Where files live

| Folder | Contents |
| --- | --- |
| `.cwng-runtime` | Standalone Python, application dependencies, Calibre, ImageMagick and kepubify |
| `.cwng-data\config` | Application databases, keys, logs, recovery markers and conversion scratch space |
| `.cwng-data\ingest` | Default ingest folder; displayed on launch |
| `.cwng-data\calibre-config` | Settings for the included Calibre tools |
| `.cwng-data\location.json` | Previous application location, used to update internal paths after a move |

The runtime uses its own Python directly, without a virtual environment or an editable source installation. Setup removes Python build metadata and frontend/Node build directories it creates; pre-existing developer directories remain.

## Existing Windows source installations

Older versions used `%LOCALAPPDATA%\Calibre-Web-NextGen` for configuration and `%USERPROFILE%\CWNG-Ingest` for ingest. If these exist and the portable installation has no location record, an ordinary launch stops with an explanation.

Stop the old application and its import processes, then run:

```bat
start-windows.cmd -ImportLegacyData
```

This copies both old folders into `.cwng-data`. It retains the originals, refuses to merge or overwrite existing local config/ingest folders, and keeps selected external Calibre libraries at their existing locations. It adjusts configuration references into the copied folders. Check the copied installation before deciding whether to remove the old profile folders or an old unused `.venv` yourself.

If copying fails, the original folders remain. If local config/ingest folders already exist, review them before retrying; the launcher deliberately does not choose between two sets of data.

## Moving, updating and removal

Stop the application and wait for imports to finish before moving it. Move the entire application folder, including the hidden `.cwng-runtime` and `.cwng-data` folders, then run `start-windows.cmd` from the new location. Moving an installed folder does not require setup or a download. The launcher updates known filesystem settings that pointed inside the old application folder, including an internal library and tool paths. External library paths stay external; those libraries must still be available at their configured paths. Calibre's library path-length limit still applies at the destination.

A linked Git worktree also contains Git administrative references outside its folder. Use `git worktree move` when moving the development checkout and preserving Git operations. An application-only copy can run independently of Git; retain `cps`, `scripts`, `.cwng-runtime`, `.cwng-data`, `start-windows.cmd`, `VERSION`, `LICENSE` and `dirs.json`. Keep the full source checkout if you want to update or rebuild it with setup.

To update, stop the application, back up `.cwng-data` and your library, update the source and rerun `install-windows.cmd`. Existing configuration and ingest data are retained. To remove the installation, stop it and delete its folder. **That also deletes any config, pending ingest and libraries inside it**; back up or move those first. External libraries and the retained legacy profile folders are not removed by setup or launch.

## Interrupted imports

The Windows worker imports stable files after a library has been configured. A failed or interrupted import stops the worker and leaves `unfinished-ingest.json` in `.cwng-data\config`; the original and working copy are retained. Check `background-ingest.log` and the library before deciding whether to retry. After resolving the interrupted import, remove the marker to resume. After a forced stop, make sure the import process has ended before restarting. Configuration and ingest paths in the recovery marker are adjusted when their parent installation moves.
