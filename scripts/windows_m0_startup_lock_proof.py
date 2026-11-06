"""Exercise native writer reentry and exclusion before app startup."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from windows_m0_ledger import Ledger


def main():
    if os.name != 'nt':
        raise RuntimeError('this proof requires Windows')
    work = Path(tempfile.mkdtemp(prefix='startup-lock-', dir=ROOT / '.m0')).resolve()
    state, config = work / 'state', work / 'config'
    state.mkdir()
    config.mkdir()
    other_config = work / 'other-config'
    other_config.mkdir()
    Ledger(state / 'runtime-ledger.sqlite')
    os.environ.update(CWNG_NATIVE_MODE='1', CWNG_NATIVE_STATE_DIR=str(state),
                      CWA_METADATA_LOCK_DIR=str(config), CALIBRE_DBPATH=str(config))
    from cps.services.calibre_db_lock import metadata_db_write_lock
    spec = importlib.util.spec_from_file_location('standalone_guard', ROOT / 'cps' / 'calibre_server_guard.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    failures = []

    def contender():
        try:
            with metadata_db_write_lock(timeout=.2, poll_interval=.01):
                failures.append('unrelated execution acquired a held lock')
        except TimeoutError:
            pass

    with metadata_db_write_lock(timeout=2) as held:
        with guard.operation(str(config), timeout=2) as nested:
            assert nested == held, 'standalone loader did not reuse same-execution lock'
        try:
            with metadata_db_write_lock(lock_dir=str(other_config), timeout=.2):
                raise AssertionError('different lock directory was treated as reentrant')
        except RuntimeError:
            pass
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        child_code = ("import importlib.util,sys; "
                      "s=importlib.util.spec_from_file_location('lock',sys.argv[1]); "
                      "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                      "exec(\"try:\\n with m.metadata_db_write_lock(timeout=.2,poll_interval=.01): pass\\n"
                      "except TimeoutError: sys.exit(0)\\nsys.exit(1)\")")
        result = subprocess.run([sys.executable, '-I', '-c', child_code,
                                 str(ROOT / 'cps' / 'services' / 'calibre_db_lock.py')],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr

    async def task_case():
        with metadata_db_write_lock(timeout=2):
            await asyncio.create_task(async_contender())

    async def async_contender():
        contender()

    asyncio.run(task_case())
    assert not failures, failures
    del os.environ['CWNG_NATIVE_STATE_DIR']
    try:
        with metadata_db_write_lock(timeout=.2):
            raise AssertionError('missing recovery state admitted a writer')
    except RuntimeError:
        pass
    print('Native writer lock: cross-loader reentry, different-path refusal, thread/task/process exclusion '
          'and missing-state refusal passed')


if __name__ == '__main__':
    main()
