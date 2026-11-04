# Native Windows runtime and automation feasibility

Date: 2026-11-04. Scope: [Establish native Windows runtime and automation feasibility](https://github.com/dapih/Calibre-Web-NextGen/issues/2), Windows 11 x64, desktop browser plus system tray, one self-contained offline installer executable. Source baseline: [`f342724c25e82bbca580c04786154e0dd71c4811`](https://github.com/dapih/Calibre-Web-NextGen/tree/f342724c25e82bbca580c04786154e0dd71c4811).

## Finding and evidence limits

**A private native runtime is a credible candidate, but the current application is not a complete native Windows product.** The HTTP server and common database locks already contain Windows branches; the required ingest and metadata automation still depend on Linux orchestration and several incompatible Python operations. The principal dependency uncertainty is building or replacing the pinned `faust-cchardet` for CPython 3.13. Nothing here demonstrates native end-to-end operation, an installer, or dependency resolution. [server], [file-lock] [ingest], [metadata-dispatch] [pins]

Evidence separates **verified artifacts**, **source observations**, **inferences**, and **unexecuted gates**. Research used source review, Context7/official documentation, PyPI metadata and in-memory wheel ZIP inspection. No dependencies/tools were installed, compiled or launched; no native filesystem or device test was run. Artifacts do not establish a working combined environment.

Proposed offline contract: installation/local workflows need no downloads; internet metadata/store integrations may report unavailable without blocking local books, manual metadata enforcement or LAN sync. This distinction requires acceptance testing.

## Required workflow matrix

| Workflow | Source observation / verified prerequisite | Native conclusion and remaining gate |
| --- | --- | --- |
| Library, browser reader | Windows listener and console-hiding branches exist. Official Python 3.13.14 x64 embeddable artifact exists. [main], [server] [Python release](https://www.python.org/downloads/release/python-31314/) | **Inference:** plausible after packaging. Verify fresh/existing library, persistence, EPUB/PDF/comic reading, offline assets. Tray supervision is additional work. |
| Ingest | Processor unconditionally imports `fcntl`; its lock uses `flock`/`fchmod`. The shell service owns watching, startup sweep, file stability, deduplication, retries, busy retry timers, timeouts and post-batch follow-up. [ingest] [ingest-service] | **Blocked as shipped.** Replace platform operations and preserve the service's behavioral contract. Polling helper is a candidate input source, not a replacement for queue/lifecycle semantics. [watch-fallback] |
| Conversion / KEPUB | Web conversion uses configured binaries/Windows subprocess handling; scripts invoke bare `ebook-convert`, `calibredb`, `kepubify`, `ebook-meta`/`ebook-polish`. [convert], [subprocess], [ingest], [enforcer] | **Integration untested.** Bundle complete private tools; test both conversion paths, cancellation, malformed inputs, originals and plugin-free defaults. |
| Metadata updates and enforcement | Flask updates and background enforcement are separate. Shell detector feeds a dispatcher; dispatcher calls `os.geteuid()` and `python3`, then uses `select` on stdin. Enforcer has an unguarded `geteuid` ownership check. [metadata-service], [metadata-dispatch], [enforcer] | **Blocked as shipped.** Windows `select` accepts sockets, not pipe/file descriptors; the exception fallback can block on `readline`, delaying pending debounce work. Preserve serialized enforcement and quiet-period dispatch with no later input. [Python select](https://docs.python.org/3.13/library/select.html) |
| OPDS | Flask blueprint independent of managed content server. [main], [opds] | **Inference:** no Linux daemon required. Verify authentication, catalog/cover/acquisition URLs from LAN. |
| Kobo sync | Main application registers Kobo; allowed `curl-cffi` Windows ABI3 artifact exists. [main], [kobo] [artifact](https://pypi.org/pypi/curl-cffi/0.6.4/json) | **Inference:** independent of managed content server. Test first/incremental sync, private KEPUB conversion and progress/annotations on device. |
| KOReader sync | Main application registers sync routes; Docker generates `cps/static/koplugin.zip`. [main], [koreader], [docker] | **Inference:** package plugin; test pairing/delivery/progress/annotations. Launcher must support LAN access alongside local browser use. [server] |
| Startup, maintenance, recovery | `cps.main` starts in-app tasks, not the external ingest or metadata services. s6 additionally runs initialization, library setup, checksum backfill, process recovery, preview-cache cleanup and nightly backup/zip handling. [main], [s6-units] | **Missing native lifecycle.** Assign each task a native startup/scheduling owner, ordering, readiness, error reporting and shutdown/recovery policy. Linux ownership migration is not a Windows task to copy verbatim. |
| Managed Calibre Content Server; LDAP | Content server returns unsupported for `os.name == 'nt'`; LDAP requirements are explicitly excluded on Windows. [content-server], [pins] | **Retain documented optional limitations** unless separately approved. These exclusions do not remove OPDS/Kobo/KOReader from the required surface. |

## Python 3.13 binary dependencies

The repository specifies ranges, not a Windows lock. Its Docker pins Python **3.13.14**, Calibre **9.11.0**, kepubify **v4.0.4** and installs Linux system packages; Linux wheel-index success does not establish Windows readiness. Use ordinary GIL-enabled CPython x64, not the distinct free-threaded ABI, for the first candidate. [pins], [docker] [Python Windows distributions](https://docs.python.org/3.13/using/windows.html)

Inspected direct versions below satisfy current ranges. CP313/ABI3 Windows filenames establish artifact availability; complete resolution/imports remain a gate.

| Dependency / current range | Verified Windows artifact or gap |
| --- | --- |
| Pillow `>=11,<13` | [`pillow-12.0.0-cp313-cp313-win_amd64.whl`](https://pypi.org/pypi/Pillow/12.0.0/json) |
| lxml `>=4.9.1,<5.4` on 3.13 | [`lxml-5.3.2-cp313-cp313-win_amd64.whl`](https://pypi.org/pypi/lxml/5.3.2/json) |
| SQLAlchemy `<2.1`; Tornado `<6.6` | [SQLAlchemy 2.0.44 CP313](https://pypi.org/pypi/SQLAlchemy/2.0.44/json); [Tornado 6.5.2 CP39 ABI3](https://pypi.org/pypi/tornado/6.5.2/json) |
| netifaces-plus `>=0.12,<0.13` | [0.12.3](https://pypi.org/pypi/netifaces-plus/0.12.3/json) and [0.12.5](https://pypi.org/pypi/netifaces-plus/0.12.5/json) have CP313 wheels; do not freeze the lower bound 0.12.0, which lacks one. |
| regex `<2025.3.20`; PyYAML `<6.1` | [regex 2024.11.6](https://pypi.org/pypi/regex/2024.11.6/json); [PyYAML 6.0.3](https://pypi.org/pypi/PyYAML/6.0.3/json): CP313. |
| cryptography `>=39,<45`; CFFI transitive | [cryptography 44.0.3 CP37/CP39 ABI3](https://pypi.org/pypi/cryptography/44.0.3/json); [CFFI 1.17.1 CP313](https://pypi.org/pypi/cffi/1.17.1/json). |
| gevent `<24.12`; greenlet `<3.2` | [gevent 24.11.1](https://pypi.org/pypi/gevent/24.11.1/json), [greenlet 3.1.1](https://pypi.org/pypi/greenlet/3.1.1/json): CP313. gevent's Windows dependency includes CFFI; do not audit only top-level imports. |
| python-Levenshtein `<0.28`; compiled Levenshtein/RapidFuzz | [Levenshtein 0.27.1](https://pypi.org/pypi/Levenshtein/0.27.1/json) and its dependency [RapidFuzz 3.13.0](https://pypi.org/pypi/rapidfuzz/3.13.0/json): CP313. |
| curl-cffi `>=0.6,<0.7` | [`curl_cffi-0.6.4-cp38-abi3-win_amd64.whl`](https://pypi.org/pypi/curl-cffi/0.6.4/json); verify bundled curl native dependencies and HTTPS behavior. |
| **faust-cchardet `>=2.1.18,<2.1.20`** | **No CP313 Windows wheel in either allowed release.** [2.1.18](https://pypi.org/pypi/faust-cchardet/2.1.18/json), [2.1.19 files/source](https://pypi.org/pypi/faust-cchardet/2.1.19/json). Requires **build-time** source-wheel probe or dependency decision, never user/global compiler installation. Source-build success remains unknown. |
| py7zr `>=1,<2` and compiled archive dependencies | [py7zr 1.0.0](https://pypi.org/pypi/py7zr/1.0.0/json) actually requires `pyppmd>=1.1,<1.3`, unlike the broad 1.x comment in pyproject. Compatible CP313 artifact examples exist for [pyppmd 1.2.0](https://pypi.org/pypi/pyppmd/1.2.0/json), [pybcj 1.0.6](https://pypi.org/pypi/pybcj/1.0.6/json), [inflate64 1.0.3](https://pypi.org/pypi/inflate64/1.0.3/json), [pyzstd 0.17.0](https://pypi.org/pypi/pyzstd/0.17.0/json), [Brotli 1.2.0](https://pypi.org/pypi/Brotli/1.2.0/json); ABI3 examples for [pycryptodomex 3.23.0](https://pypi.org/pypi/pycryptodomex/3.23.0/json) and [psutil 7.0.0](https://pypi.org/pypi/psutil/7.0.0/json). These are candidates, not one resolved lock. |
| Other compiled transitives | [zope.interface 7.2](https://pypi.org/pypi/zope.interface/7.2/json), [rpds-py 0.22.3](https://pypi.org/pypi/rpds-py/0.22.3/json): CP313 examples, not exhaustive resolution. |
| python-magic plus Windows python-magic-bin | [python-magic 0.4.27](https://pypi.org/pypi/python-magic/0.4.27/json) and [python-magic-bin 0.4.14](https://pypi.org/pypi/python-magic-bin/0.4.14/json) wheels **both contain `magic/__init__.py`**; bin also supplies `magic/libmagic/libmagic.dll` and `magic.mgc`. ZIP directories were inspected without importing/installing. Resolve file ownership/selection deliberately and verify the chosen wrapper/DLL/data together; a generic Python tag is not a Python 3.13 runtime test. |

## Native tools and complete assets

| Component | Verified support / source | Packaging implication, still untested |
| --- | --- | --- |
| Calibre 9.11.0 | Exact release offers Windows x64 installer and Portable installer. [release archive](https://download.calibre-ebook.com/9.11.0/) Portable is documented as self-contained on Windows 10+. [portable documentation](https://calibre-ebook.com/download_portable) | Preserve the complete private distribution, including its own runtime/plugins/resources; do not import Calibre's private Python libraries into CWNG. Configure every CLI call, not only the web converter. `CALIBRE_CONFIG_DIRECTORY`, `CALIBRE_TEMP_DIR`, `CALIBRE_CACHE_DIRECTORY` provide per-process state controls. [Calibre environment variables](https://manual.calibre-ebook.com/customize.html#environment-variables) |
| kepubify v4.0.4 | Official release API lists `kepubify-windows-64bit.exe`. [exact release assets](https://api.github.com/repos/pgaskin/kepubify/releases/tags/v4.0.4) | Configure the absolute private executable across web conversion and ingest; Program Files/PATH autodetection is insufficient for isolation. [binary detection], [ingest] |
| Wand / ImageMagick | Wand 0.6.13 documents x64 DLL builds, matching Python architecture and `MAGICK_HOME`. [Wand installation](https://docs.wand-py.org/en/0.6.13/guide/install.html) ImageMagick offers Windows dynamic builds; its portable archives are **static**, not equivalent to the DLL build Wand expects. [official downloads](https://imagemagick.org/download/) | Select/package a complete app-local DLL-capable distribution, coders, policies, delegates and data; validate discovery without registry/global installation. Do not assume the portable CLI archive satisfies Wand. Set variables only in CWNG/child environments. |
| Ghostscript; UnRAR | Official [Ghostscript downloads](https://ghostscript.com/releases/gsdnld.html) list Windows 64-bit; [RARLAB addons](https://www.rarlab.com/rar_add.htm) provide command-line Windows UnRAR. | Explicitly validate private Ghostscript delegate lookup if PDF rasterization needs it, and UnRAR lookup for CBR/comic paths. Record format limitations individually if optional; bundling availability alone establishes neither integration nor redistribution clearance. |
| MSVC native runtime | Microsoft documents local deployment beside an application executable. [deployment choices](https://learn.microsoft.com/en-us/cpp/windows/choosing-a-deployment-method?view=msvc-170) | Audit each EXE/PYD/DLL's imports; ship permitted app-local runtime files where required. Do not silently require a global VC runtime installer. Preserve notices and an update inventory; app-local deployment requires application-owned servicing. |
| Browser/translation/plugin assets | Docker builds Vite SPA into `cps/static/app`, compiles `.po` into `.mo` via setup, creates KOReader plugin ZIP. `pyproject` package discovery includes `cps*`, not the root scripts tree. [docker], [setup], [pins], [vite] | Build at release time and include SPA lazy chunks/workers/fonts, classic static files, templates, compiled catalogs, plugin ZIP, root scripts, SQL/resources/version data. Verify the actual distribution inventory; `include-package-data=true` alone does not prove completeness. No Node, gettext, shell or ZIP utility should be required on the end user's host. |

## Ranked portability blockers

1. **Required workers cannot start unchanged.** Guard/replace `fcntl`, `grp`/`pwd`, `geteuid`, `fchmod`, ownership commands and ingest recovery's `O_DIRECTORY`. `service_user` fails at import before “missing account” handling can help. [ingest], [service-user], [enforcer], [metadata-dispatch]
2. **s6 behavior is product functionality.** Replace init/readiness ordering, watchers, durable retries, dedup, deadlines, batch follow-up, recovery, scheduled cleanup/backups and status reporting. A native launcher that only runs `cps` omits required workflows. Existing polling can be reused conceptually, but its shell pipeline cannot. [s6-units], [ingest-service], [metadata-service], [main]
3. **Path normalization is unsafe for this platform.** `app_paths` and `constants` use native `Path.is_absolute()` but prepend `/` and normalize through `posixpath`. **Code inference:** drive-qualified `C:\Books` becomes `/C:\Books`; forward-slash drive and UNC spellings are also suspect, drive-root rejection only compares to `/`, and POSIX defaults are not fully qualified Windows paths. No Windows path probe was run. Specify one native path contract, identical across workers/web/config, with drive/UNC/reparse/root handling, spaces, Unicode, reserved names, case and long-path gates. [app-paths], [constants]
4. **Offline binary closure is unproved.** Resolve `faust-cchardet`, overlapping `magic` files, complete transitive wheels, tool DLL/resources and static assets before calling packaging feasible. This is build/release work, not a host setup step. [pins] [PyPI faust source/files](https://pypi.org/pypi/faust-cchardet/2.1.19/json)
5. **Python isolation does not isolate tools or their state.** Scripts use bare tools/`python3` and inherit environment copies; web autodetection looks in global Program Files and PATH. Specify an absolute private tool registry plus controlled per-tool environments and DLL policies. [ingest], [metadata-dispatch], [enforcer], [binary detection]
6. **Lock/restart/cancellation semantics remain incomplete.** Shared `file_lock` has a Windows `msvcrt` implementation, but ingest has its own POSIX lock. `script_lock` has no Windows guard, and deliberately considers every foreign positive PID alive on Windows: a crashed worker can strand a stale lock. Converter process-group setup/kill is correctly **guarded to POSIX**, so the Windows risk is orphan descendants and incomplete shutdown, not an unconditional `killpg` call. Define and test crash recovery, re-entry, file replacement under open handles and all-writer exclusion. [file-lock], [script-lock], [ingest]

## Candidate architecture implications — decision remains open

**A. Private embeddable CPython plus tray supervisor.** Official application-local ZIP, vendored packages, `python313._pth` restricted imports/environment/registry lookup or isolated initialization. Preserve private scripts and absolute interpreter; explicitly service assets/dependencies/runtime. [Python distribution](https://docs.python.org/3.13/using/windows.html#the-embeddable-package) [isolated initialization](https://github.com/python/cpython/blob/v3.13.9/Doc/c-api/init_config.rst)

**B. Frozen application plus tray supervisor.** PyInstaller changes Windows DLL lookup through `SetDllDirectoryW`, affecting children; child-launch sanitization and one-file restart/extraction handling are necessary. Script workers need explicit entrypoints or a separate private interpreter: frozen `sys.executable` is the bootloader, not Python. Same workflow ports, plus hooks/data collection. [subprocess/restart](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html) [runtime identity](https://pyinstaller.org/en/stable/runtime-information.html#using-sys-executable-and-sys-argv-0)

Neither candidate passed gates here. The required single offline installer `.exe` can contain a private runtime tree; its installed layout is an architecture implementation choice.

Whichever candidate is selected, its reviewable isolation contract should require:

- No global Python/package installation, activation, persistent PATH edits or reuse of host Calibre. Resolve code/import paths before importing application packages; ignore hostile `PYTHONHOME`, `PYTHONPATH`, user-site and current-working-directory packages. A venv alone does not address subprocess/DLL lookup.
- Absolute EXE paths and separate private directories for CWNG's Python extensions, Calibre, ImageMagick and other tools. Avoid flattening conflicting Qt/OpenSSL/runtime DLL families into one directory. Record actual loaded module paths, not just configured locations: Windows can search dependent DLLs by module name even when the initial DLL was specified absolutely. [Microsoft DLL search rules](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)
- Use explicit extension/ctypes DLL directories with managed lifetime where appropriate; `os.add_dll_directory` controls those dependency searches, not arbitrary child EXE discovery. Child processes need their own environment/search policy. [Python DLL API](https://docs.python.org/3.13/library/os.html#os.add_dll_directory)
- Construct child environments with Windows essentials/private overrides; scrub conflicting Python/Calibre/Qt/ImageMagick variables. Preserve intentional proxy/certificate choices. Use private config/cache/temp/policies/delegates; no host application settings writes.
- Separate immutable payload from writable state; share configuration across workers. [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) are a candidate for owned descendant cleanup.

## Smallest future executable gates

Implementation gates, **not completed tests**, in order:

1. **Binary closure:** lock versions/hashes, resolve faust and `magic`, inventory tools/assets. Clean offline Windows 11 x64 target without Python/Calibre must import compiled modules, MIME detection/Wand and run every private tool's version command without host setup.
2. **Isolation/paths:** unrelated CWD, spaces/Unicode install, conflicting global installations, poisoned `PYTHONHOME`/`PYTHONPATH`/PATH/CWD and tool variables. Record actual imports/DLLs/EXEs within private/system roots; host settings/PATH unchanged. Exercise both path resolvers with drive/root/slash/Unicode/long/UNC forms; explicitly reject deferred UNC support.
3. **Complete small workflow:** disposable NTFS library; fresh/existing state, slow-copy EPUB ingest, one conversion/KEPUB, metadata/cover edit followed by one enforcement pass without another event. Restart; verify originals/DB/queue. Readers/translations work without WAN; OPDS works over LAN.
4. **Recovery:** second instance/contention, crash during conversion/overwrite/queue publication, restart, cancel descendants, tray exit, open-handle replacement/concurrent writers. Assert bounded retries, recoverable state, no stale locks/lost originals/orphans/unrelated process termination.
5. **Devices:** actual Kobo/KOReader first/incremental sync, transfer, progress/annotations across restart using packaged plugin/private tools; verify LAN URLs/readiness alongside browser launch.

Runtime architecture should decide interpreter/supervisor/distribution shape, faust build versus dependency change, and initial format/network-share limits. No extra research ticket is needed unless the chosen candidate cannot answer these gates. Required workflows remain required.

## Immutable repository sources

[pins]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/pyproject.toml
[docker]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/Dockerfile
[main]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/main.py
[server]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/server.py#L100
[file-lock]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/services/file_lock.py
[ingest]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/ingest_processor.py
[metadata-dispatch]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/metadata_change_dispatch.py
[service-user]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/service_user.py
[enforcer]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/cover_enforcer.py
[script-lock]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/script_lock.py
[app-paths]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/app_paths.py#L124
[constants]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/constants.py#L67
[binary detection]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/config_sql.py#L1040
[convert]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/tasks/convert.py
[subprocess]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/subproc_wrapper.py
[opds]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/opds.py
[kobo]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/kobo.py
[koreader]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/progress_syncing/protocols/kosync.py
[content-server]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/cps/content_server.py#L130
[ingest-service]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run
[metadata-service]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/root/etc/s6-overlay/s6-rc.d/metadata-change-detector/run
[s6-units]: https://github.com/dapih/Calibre-Web-NextGen/tree/f342724c25e82bbca580c04786154e0dd71c4811/root/etc/s6-overlay/s6-rc.d
[watch-fallback]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/watch_fallback.py
[setup]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/scripts/setup-cwa.sh
[vite]: https://github.com/dapih/Calibre-Web-NextGen/blob/f342724c25e82bbca580c04786154e0dd71c4811/frontend/vite.config.ts
