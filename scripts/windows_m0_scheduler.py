"""Experimental single-writer scheduler for the native ingest proof."""

import time
import sqlite3

from windows_m0_coordinator import CoordinatorBusy
from windows_m0_inbox import _volume_identity
from windows_m0_lifecycle import WindowsLifecycle
from windows_m0_metadata_events import MetadataEvents
from windows_m0_manual_enforce import ManualEnforcementDispatcher
from windows_m0_conversion import LibraryConversionDispatcher
from windows_m0_epub_fixer import EpubFixerDispatcher
from windows_m0_ledger import LedgerStateError
from windows_m0_watcher import IngestWatcher


class IngestScheduler:
    """Connect the native watcher, durable queue and contained ingest worker.

    The caller owns coordinator lifetime, profile security and verified runtime
    configuration. Ambiguous outcomes stop dispatch and remain for review.
    """

    def __init__(self, coordinator, inbox, dispatcher, *, enforcer=None, clock=time.monotonic, wall_clock_ns=time.time_ns, retry_seconds=2.0,
                 max_job_seconds=1800.0):
        if (inbox.coordinator is not coordinator or dispatcher.owner is not coordinator or
                (enforcer is not None and enforcer.owner is not coordinator)):
            raise ValueError('scheduler components must share one coordinator')
        if not 0 < retry_seconds <= 60:
            raise ValueError('invalid retry interval')
        if not 0 < max_job_seconds <= 86400:
            raise ValueError('invalid job timeout')
        self.owner = coordinator
        self.inbox = inbox
        self.dispatchers = {'ingest': dispatcher, 'convert-library': LibraryConversionDispatcher(dispatcher),
                            'epub-fixer': EpubFixerDispatcher(dispatcher)}
        if enforcer is not None:
            self.dispatchers['enforce'] = enforcer
            self.dispatchers['manual-enforce'] = ManualEnforcementDispatcher(
                coordinator, enforcer.runtime, enforcer.library, enforcer.environment,
                verify_endpoint=enforcer._verify_endpoint)
        self.metadata_events = (MetadataEvents(coordinator.ledger, enforcer.environment, enforcer.cwa_db,
                                               clock_ns=wall_clock_ns) if enforcer is not None else None)
        self.clock = clock
        self.wall_clock_ns = wall_clock_ns
        self.retry_seconds = retry_seconds
        self.max_job_seconds = max_job_seconds
        self.watcher = IngestWatcher(inbox, lambda _job_id: None, clock=clock)
        self.active = None
        self.active_dispatcher = None
        self.started_at = None
        self.cancel_requested_at = None
        self.aborting = False
        self.next_retry = 0.0
        self.draining = False
        self.storage_paused = False
        self.storage_error = None
        self.started = False
        self.lifecycle = WindowsLifecycle()
        self.resume_pending = False

    def metadata_changed(self, book_id, *, quiet_seconds=2.0):
        """Persist a metadata notification and extend its quiet deadline."""
        if not self.started or self.draining:
            raise CoordinatorBusy('scheduler is not accepting notifications')
        if 'enforce' not in self.dispatchers:
            raise ValueError('metadata enforcement is unavailable')
        if not 0 < quiet_seconds <= 3600:
            raise ValueError('invalid metadata quiet period')
        return self.owner.ledger.debounce_metadata(book_id, self.wall_clock_ns() + int(quiet_seconds * 1e9))

    def start(self):
        if self.started:
            raise RuntimeError('scheduler already started')
        # Fencing precedes admission. Existing review records never auto-replay.
        if self.owner.ledger.list_jobs('running'):
            self.owner.fence_interrupted()
        if self.owner.ledger.list_jobs('review'):
            raise CoordinatorBusy('review is required before starting ingest')
        self.lifecycle.start()
        try:
            self.watcher.start()  # Arms the OS notification before the first scan.
        except BaseException:
            self.lifecycle.close()
            raise
        self.started = True

    def _check_storage(self):
        if not self.inbox.source_dir.is_dir() or not self.inbox.stage_dir.is_dir():
            raise OSError('ingest or staging directory is unavailable')
        if (_volume_identity(self.inbox.source_dir) != self.inbox._source_volume or
                _volume_identity(self.inbox.stage_dir) != self.inbox._stage_volume):
            raise OSError('ingest or staging volume changed')
        dispatcher = self.dispatchers['ingest']
        if hasattr(dispatcher, 'library_volume'):
            if _volume_identity(dispatcher.library) != dispatcher.library_volume:
                raise OSError('library volume changed')
            info = dispatcher.database.stat()
            if (info.st_dev, info.st_ino) != dispatcher.database_identity:
                raise OSError('library database identity changed')

    def _pause_storage(self, reason):
        self.storage_paused = True
        self.storage_error = reason
        if self.active is not None:
            return self.stop_safely()
        self.finish_current()
        return 'storage'

    def step(self, timeout=1.0):
        if not self.started:
            raise RuntimeError('scheduler is not started')
        events = self.lifecycle.drain()
        if 'error' in events:
            self.resume_pending = False
            return self._pause_storage('Windows lifecycle notification failed')
        if 'shutdown' in events:
            self.resume_pending = False
            return self.stop_safely()
        if 'resume' in events or ('device' in events and self.storage_paused):
            self.resume_pending = True
        if 'suspend' in events or ('resume' in events and not self.storage_paused):
            # An interrupted writer has an ambiguous outcome even when the
            # source is present again after resume; it must be reviewed.
            return self._pause_storage('Windows suspended or resumed')
        if self.resume_pending and self.storage_paused:
            try:
                self.resume_storage()
            except (OSError, ValueError, CoordinatorBusy, sqlite3.Error):
                # Missing/replaced storage and ambiguous work stay paused.
                pass
            else:
                self.resume_pending = False
        if not self.draining:
            try:
                self._check_storage()
                self.watcher.poll(timeout=min(timeout, 1.0))
                if self.metadata_events is not None and not self.owner.ledger.list_jobs('review'):
                    self.metadata_events.scan()
            except (OSError, ValueError, sqlite3.Error) as error:
                return self._pause_storage(str(error))
        elif timeout:
            time.sleep(min(timeout, 1.0))
        if self.active is not None:
            if self.owner.ledger.cancellation_requested(self.active):
                record = self.owner.ledger.get(self.active)
                if record and record['operation'] == 'convert-library' and hasattr(self.active_dispatcher, 'request_cancel'):
                    if self.cancel_requested_at is None:
                        try:
                            self.active_dispatcher.request_cancel(self.active)
                            self.cancel_requested_at = self.clock()
                        except (OSError, ValueError):
                            return self.stop_safely()
                    if self.clock() - self.cancel_requested_at >= 60:
                        return self.stop_safely()
                else:
                    return self.stop_safely()
            if self.clock() - self.started_at >= self.max_job_seconds:
                return self.stop_safely()
            try:
                self.active_dispatcher.finish(self.active)
            except CoordinatorBusy:
                return 'draining' if self.draining else 'running'
            state = self.owner.ledger.get(self.active)['state']
            self.active = None
            self.active_dispatcher = None
            self.started_at = None
            self.cancel_requested_at = None
            if state != 'completed':
                return 'review'
        if self.owner.ledger.list_jobs('running'):
            if self.aborting:
                try:
                    self.owner.fence_interrupted()
                except CoordinatorBusy:
                    return 'recovery'
            else:
                return 'recovery'
        if self.owner.ledger.list_jobs('review'):
            return 'review'
        if self.draining:
            return 'storage' if self.storage_paused else 'stopped'
        if self.clock() < self.next_retry:
            return 'waiting'
        pending = self.owner.ledger.list_jobs('pending')
        if not pending:
            return 'idle'
        # A quiet-period notification must not block unrelated ready work.
        job = next((item for item in pending if item['operation'] != 'metadata-debounce'
                    or self.wall_clock_ns() >= item['intent'].get('due_ns', 0)), None)
        if job is None:
            return 'waiting'
        if job['operation'] == 'refresh-ingest':
            try:
                self.watcher.scan()
            except (OSError, ValueError) as error:
                return self._pause_storage(str(error))
            self.owner.ledger.complete_refresh(job['id'])
            return 'waiting'
        if job['operation'] == 'manual-enforce-request':
            manual = self.dispatchers.get('manual-enforce')
            if manual is None:
                self.owner.ledger.review_pending(job['id'], 'manual enforcement is unavailable')
                return 'review'
            try:
                intent = manual.prepare()
            except OSError:
                self.next_retry = self.clock() + self.retry_seconds
                return 'waiting'
            except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
                self.owner.ledger.review_pending(job['id'], 'manual enforcement cannot be prepared: ' + str(error))
                return 'review'
            if not self.owner.ledger.prepare_manual_enforcement(job['id'], intent):
                return 'waiting'
            job = self.owner.ledger.get(job['id'])
        if job['operation'] == 'convert-library-request':
            try:
                intent = self.dispatchers['convert-library'].prepare()
            except OSError:
                self.next_retry = self.clock() + self.retry_seconds
                return 'waiting'
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                self.owner.ledger.review_pending(job['id'], 'library conversion cannot be prepared')
                return 'review'
            if not self.owner.ledger.prepare_conversion(job['id'], intent):
                return 'waiting'
            job = self.owner.ledger.get(job['id'])
        if job['operation'] == 'epub-fixer-request':
            try:
                intent = self.dispatchers['epub-fixer'].prepare(job['intent']['book_id'])
            except OSError:
                self.next_retry = self.clock() + self.retry_seconds
                return 'waiting'
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                self.owner.ledger.review_pending(job['id'], 'EPUB repair cannot be prepared')
                return 'review'
            if not self.owner.ledger.prepare_epub_fixer(job['id'], job['intent'], intent):
                return 'waiting'
            job = self.owner.ledger.get(job['id'])
        if job['operation'] == 'metadata-debounce':
            enforcer = self.dispatchers.get('enforce')
            if enforcer is None:
                self.owner.ledger.review_pending(job['id'], 'metadata enforcement is unavailable')
                return 'review'
            if self.wall_clock_ns() < job['intent']['due_ns']:
                return 'waiting'
            try:
                intent = enforcer.prepare(job['intent']['book_id'])
            except OSError:
                self.next_retry = self.clock() + self.retry_seconds
                return 'waiting'
            except (ValueError, TypeError, KeyError):
                self.owner.ledger.review_pending(job['id'], 'metadata intent cannot be prepared')
                return 'review'
            if not self.owner.ledger.promote_metadata(job['id'], job['intent'], intent):
                return 'waiting'
            job = self.owner.ledger.get(job['id'])
        dispatcher = self.dispatchers.get(job['operation'])
        if dispatcher is None:
            self.owner.ledger.review_pending(job['id'], 'unsupported pending operation')
            return 'review'
        try:
            dispatcher.start(job['id'])
        except LedgerStateError:
            # A local cancellation can win the race between preparation and
            # claim. Only retry dispatch if no running intent was committed.
            current = self.owner.ledger.get(job['id'])
            if current and current['state'] == 'completed':
                return 'idle'
            return 'recovery'
        except (ValueError, TypeError):
            current = self.owner.ledger.get(job['id'])
            if current and current['state'] == 'pending':
                self.owner.ledger.review_pending(job['id'], 'invalid or unsupported ingest intent')
                return 'review'
            return 'recovery'
        except (OSError, CoordinatorBusy):
            # Retry only if no running claim was committed. Once claimed, the
            # recovery fence and explicit reconciliation own the outcome.
            current = self.owner.ledger.get(job['id'])
            if current and current['state'] == 'pending':
                self.next_retry = self.clock() + self.retry_seconds
                return 'waiting'
            return 'recovery'
        self.active = job['id']
        self.active_dispatcher = dispatcher
        self.started_at = self.clock()
        self.cancel_requested_at = None
        return 'running'

    def finish_current(self):
        """Stop admitting new jobs; callers keep stepping until stopped."""
        if not self.started:
            raise RuntimeError('scheduler is not started')
        if not self.draining:
            self.watcher.close()
            self.draining = True

    def resume_storage(self):
        """Recheck the original storage before watching or admitting work again."""
        if not self.started or not self.storage_paused:
            raise RuntimeError('storage is not paused')
        if self.active is not None or self.owner.ledger.list_jobs('running') or self.owner.ledger.list_jobs('review'):
            raise CoordinatorBusy('interrupted work must be reconciled before resume')
        self._check_storage()
        dispatcher = self.dispatchers['ingest']
        if hasattr(dispatcher, 'library_volume'):
            dispatcher._check_settings()
            if dispatcher._verify_endpoint(dispatcher.port) is not True:
                raise OSError('application endpoint ownership changed')
        self.watcher.close()  # Retry any incomplete cancellation before replacing its buffers.
        self.inbox._observed.clear()
        watcher = IngestWatcher(self.inbox, lambda _job_id: None, clock=self.clock)
        watcher.start()
        self.watcher = watcher
        self.draining = False
        self.storage_paused = False
        self.storage_error = None
        self.aborting = False
        self.resume_pending = False

    def stop_safely(self):
        """Stop admission and interrupt the worker without replaying its intent.

        Recovery remains blocked while a named job exists; once fenced the
        interrupted intent requires operation-specific review.
        """
        try:
            self.finish_current()
        except (OSError, TimeoutError) as error:
            # Still stop the writer if the watcher's cancellation failed. Keep
            # the watcher object alive until close can safely finish cancellation.
            self.draining = True
            self.storage_error = str(error)
        self.aborting = True
        if self.active is not None:
            self.owner.stop_current(self.active)
            self.active = None
            self.active_dispatcher = None
            self.started_at = None
            self.cancel_requested_at = None
        try:
            self.owner.fence_interrupted()
        except CoordinatorBusy:
            return 'recovery'
        return 'review' if self.owner.ledger.list_jobs('review') else 'stopped'

    def close(self):
        """Stop watching; the caller decides whether to drain or stop the job."""
        if self.started:
            try:
                self.watcher.close()
            finally:
                self.lifecycle.close()
                self.started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
