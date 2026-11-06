"""Exercise partial verification and active cancellation in the local M0 owner."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

from windows_m0_activation import LocalOwner
from windows_m0_client import request_action
from windows_m0_conversion import LibraryConversionDispatcher
from windows_m0_coordinator import CoordinatorBusy
from windows_m0_scheduler import IngestScheduler


def main():
    helper, library = (Path(value).resolve(strict=True) for value in sys.argv[1:3])
    with tempfile.TemporaryDirectory(dir=helper.parent) as name:
        state = Path(name).resolve()
        environment = {'SystemRoot': os.environ['SystemRoot'], 'PATH': os.environ['SystemRoot']}
        with LocalOwner(helper, state) as local:
            owner = local.owner
            rejected = owner.enqueue('preflight-fixture', 'convert-library', {})
            try:
                owner.start(rejected, sys.executable, ['-I', '-c', 'pass'], cwd=state,
                            environment=environment, writer_lock_dir=state, preflight=lambda: False)
            except ValueError:
                pass
            else:
                raise AssertionError('failed preflight was launched')
            assert owner.ledger.get(rejected)['state'] == 'pending'
            assert owner.ledger.cancel_pending(rejected)
            fixture = SimpleNamespace(owner=owner, library=library, database=library / 'metadata.db',
                                      work_root=state, _check_settings=lambda: None)
            dispatcher = LibraryConversionDispatcher(fixture)
            intent = {'target': 'kepub', 'expected': [2147483647], 'sources': []}
            partial = owner.enqueue('partial-fixture', 'convert-library', intent)
            owner.start(partial, sys.executable, ['-I', '-c', 'pass'], cwd=state,
                        environment=environment, writer_lock_dir=state)
            deadline = time.monotonic() + 20
            while True:
                try:
                    dispatcher.finish(partial)
                    break
                except CoordinatorBusy:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('partial fixture did not exit')
                    time.sleep(.05)
            assert owner.ledger.get(partial)['state'] == 'review'
            # The fixture's no-op child cannot have changed the library.
            owner.reconcile(partial, outcome='completed', evidence='no-op proof child exited',
                            verify=lambda _record, _outcome: True)

            active = owner.enqueue('cancel-fixture', 'convert-library', intent)
            attempt = state / ('convert-library-' + active + '-1')
            attempt.mkdir()
            cooperative_environment = dict(environment, CWNG_NATIVE_CANCEL_FILE=str(attempt / 'cancel'))
            code = ("import os,pathlib,time,sys; p=pathlib.Path(os.environ['CWNG_NATIVE_CANCEL_FILE']); "
                    "exec('while not p.exists():\\n time.sleep(.05)'); "
                    "(p.parent/'settled').write_text('yes'); time.sleep(.3); sys.exit(2)")
            owner.start(active, sys.executable, ['-I', '-c', code],
                        cwd=state, environment=cooperative_environment, writer_lock_dir=state)
            scheduler = IngestScheduler.__new__(IngestScheduler)
            scheduler.owner, scheduler.started, scheduler.active = owner, True, active
            scheduler.draining = scheduler.storage_paused = scheduler.aborting = False
            scheduler.resume_pending = False
            scheduler.active_dispatcher, scheduler.started_at = dispatcher, time.monotonic()
            scheduler.cancel_requested_at = None
            scheduler.clock, scheduler.max_job_seconds = time.monotonic, 60
            scheduler.watcher = SimpleNamespace(close=lambda: None, poll=lambda **_kwargs: None)
            scheduler.lifecycle = SimpleNamespace(drain=lambda: [])
            scheduler._check_storage = lambda: None
            scheduler.metadata_events = None
            assert request_action(state, 'cancel-convert-library')['cancelled'] == 1
            outcome = scheduler.step(timeout=0)
            deadline = time.monotonic() + 20
            while outcome in ('recovery', 'running'):
                if time.monotonic() >= deadline:
                    raise TimeoutError('cancelled generation was not fenced')
                time.sleep(.05)
                outcome = scheduler.step(timeout=0)
            assert outcome == 'review'
            assert owner.ledger.get(active)['state'] == 'review'
            assert (attempt / 'settled').is_file()
            print('Partial conversion and active cancellation remain in review: PASS')


if __name__ == '__main__':
    main()
