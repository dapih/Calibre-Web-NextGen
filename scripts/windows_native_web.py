"""Configured-profile web process launched by the native controller."""

import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'scripts'))
    from windows_m0_job import current_process_in_generation
    from windows_m0_readiness import readiness_middleware

    helper = os.environ['CWNG_NATIVE_HELPER']
    generation = os.environ['CWNG_NATIVE_WEB_GENERATION']
    if os.environ.get('CWNG_NATIVE_MODE') != '1' or not current_process_in_generation(helper, generation):
        raise RuntimeError('native web process must be launched in its controller job')
    library = Path(os.environ['CWA_CALIBRE_LIBRARY_DIR']).resolve(strict=True)
    app_db = Path(os.environ['CWA_APP_DB_PATH']).resolve(strict=True)
    key = bytes.fromhex(os.environ['CWNG_NATIVE_READY_KEY'])
    if len(key) != 32 or not (library / 'metadata.db').is_file():
        raise ValueError('invalid native profile readiness configuration')
    dll_handles = []
    for variable in ('MAGICK_HOME', 'MAGICK_CODER_MODULE_PATH'):
        dll_handles.append(os.add_dll_directory(str(Path(os.environ[variable]).resolve(strict=True))))

    # Keep the primary application listener on loopback. A separate direct
    # listener below serves only explicitly consented Private-network peers.
    sys.argv = ['cps.py', '-p', str(app_db), '-i', '127.0.0.1', '-m']
    import cps
    from cps.main import _start_runtime_tasks, register_blueprints

    # Native updates are user-run installers. Never start the source-ZIP
    # updater thread, including when an existing web setting enables it.
    cps.updater_thread.start = lambda: None
    app = cps.create_app()
    register_blueprints(app)
    from windows_native_http import install_http_only_guard
    install_http_only_guard(app)
    # Hide the legacy source updater controls and reject their status/start
    # route in native mode. The native installer update flow is separate.
    from cps import admin
    admin.feature_support['updater'] = False
    # The existing update notice targets the container distribution. Do not
    # contact that feed or show its Docker instructions in the Windows app.
    from cps import render_template
    render_template.cwa_update_notification = lambda: None
    admin.cwa_get_update_indicator = lambda: (False, '')
    app.jinja_env.globals['cwng_native_mode'] = True

    def library_status():
        configured = Path(cps.config.config_calibre_dir).resolve(strict=True)
        if configured != library or cps.calibre_db.session_factory is None:
            return False, ''
        info = (configured / 'metadata.db').stat()
        return bool(info.st_ino), f'{info.st_dev}:{info.st_ino}'

    from windows_native_lan import NativeLanService, local_web_ready, network_status_middleware
    lan = None
    app.wsgi_app = readiness_middleware(app.wsgi_app, key, library_status)
    app.wsgi_app = network_status_middleware(
        app.wsgi_app, key, lambda: lan.status() if lan is not None else {'listening': [], 'error': None})
    cps.web_server.app = app
    _start_runtime_tasks(app)
    import logging
    try:
        lan = NativeLanService(app, Path(os.environ['CWNG_NATIVE_STATE_DIR']),
                               int(os.environ['CWA_PORT_OVERRIDE']),
                               ready=lambda: local_web_ready(int(os.environ['CWA_PORT_OVERRIDE']), key, library_status),
                               ssl_options=cps.web_server.ssl_args,
                               proxy_login_header=lambda: cps.config.config_reverse_proxy_login_header_name)
        lan.start()
    except Exception:
        logging.getLogger(__name__).exception('Native LAN listener could not start; local service remains available')
    try:
        return 0 if cps.web_server.start() else 1
    finally:
        if lan is not None:
            lan.close()
        for handle in dll_handles:
            handle.close()


if __name__ == '__main__':
    raise SystemExit(main())
