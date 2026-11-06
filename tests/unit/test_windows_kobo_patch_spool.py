"""Kobo annotation recovery spool works on native Windows."""

import importlib
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native spool')


def test_spool_stages_and_records_dispatch_outcome_on_windows(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    root = tmp_path / 'private' / 'patch-spool'
    monkeypatch.setattr(spool, '_spool_root', lambda: root)
    body = b'{"updatedAnnotations":[{"id":"native-1"}]}'
    ticket = spool.stage_patch(raw_body=body, entitlement_id='book-1', user_id=17,
                               origin_device_id='reader-1')
    assert ticket is not None
    assert ticket.path.is_file()
    record = spool.load_spooled_patch(ticket.path)
    assert record['body'] == body
    assert record['dispatch_status'] == 'staged'
    assert ticket.mark_dispatch_outcome('dispatch_completed')
    assert spool.load_spooled_patch(ticket.path)['dispatch_status'] == 'dispatch_completed'


def test_windows_spool_retains_retried_body_and_rejects_stale_outcome(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    args = dict(raw_body=b'{"updatedAnnotations":[]}', entitlement_id='book-1',
                user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    second = spool.stage_patch(**args)
    assert first is not None and second is not None
    assert first.spool_id == second.spool_id
    assert first.attempt_id != second.attempt_id
    assert not first.mark_dispatch_outcome('dispatch_completed')
    assert spool.load_spooled_patch(second.path)['dispatch_status'] == 'staged'
    assert second.mark_dispatch_outcome('dispatch_completed')


def test_windows_open_completed_record_does_not_erase_later_failed_attempt(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    args = dict(raw_body=b'{"updatedAnnotations":[]}', entitlement_id='book-1',
                user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    assert first and first.mark_dispatch_outcome('dispatch_completed')
    with first.path.open('rb'):
        retry = spool.stage_patch(**args)
        assert retry is not None
        assert retry.mark_dispatch_outcome('dispatch_refused')
        candidates = list(spool.iter_replay_candidates())
        assert retry.path in candidates
        assert spool.load_spooled_patch(retry.path)['body'] == args['raw_body']


def test_windows_locked_completed_record_is_not_evicted_to_exceed_spool_limit(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    monkeypatch.setattr(spool, 'MAX_FILES', 1)
    args = dict(raw_body=b'{}', entitlement_id='book-1', user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    assert first and first.mark_dispatch_outcome('dispatch_completed')
    with first.path.open('rb'):
        assert spool.stage_patch(**args) is None
        assert first.path.is_file()
        assert len(list(first.path.parent.glob('patch-*.json.gz'))) == 1


def test_windows_repeated_retry_with_completed_record_open_reuses_pending_record(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    monkeypatch.setattr(spool, 'MAX_FILES', 2)
    args = dict(raw_body=b'{}', entitlement_id='book-1', user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    assert first and first.mark_dispatch_outcome('dispatch_completed')
    with first.path.open('rb'):
        attempts = []
        for _ in range(3):
            ticket = spool.stage_patch(**args)
            assert ticket is not None
            assert ticket.mark_dispatch_outcome('dispatch_refused')
            attempts.append(ticket.spool_id)
            assert spool.load_spooled_patch(ticket.path)['attempt_count'] == len(attempts)
        assert len(set(attempts)) == 1
        assert len(list(first.path.parent.glob('patch-*.json.gz'))) == 2
        assert len(list(spool.iter_replay_candidates())) == 1


def test_windows_newest_completed_duplicate_is_reused_when_older_one_is_open(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    monkeypatch.setattr(spool, 'MAX_FILES', 2)
    args = dict(raw_body=b'{}', entitlement_id='book-1', user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    assert first and first.mark_dispatch_outcome('dispatch_completed')
    with first.path.open('rb'):
        second = spool.stage_patch(**args)
        assert second and second.mark_dispatch_outcome('dispatch_completed')
        third = spool.stage_patch(**args)
        assert third is not None and third.spool_id == second.spool_id
        assert third.mark_dispatch_outcome('dispatch_refused')
        assert len(list(first.path.parent.glob('patch-*.json.gz'))) == 2
        assert list(spool.iter_replay_candidates()) == [third.path]


def test_windows_newest_open_and_full_quota_preserves_existing_records(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    monkeypatch.setattr(spool, 'MAX_FILES', 2)
    args = dict(raw_body=b'{}', entitlement_id='book-1', user_id=17, origin_device_id='reader-1')
    first = spool.stage_patch(**args)
    assert first and first.mark_dispatch_outcome('dispatch_completed')
    with first.path.open('rb'):
        second = spool.stage_patch(**args)
        assert second and second.mark_dispatch_outcome('dispatch_completed')
    with second.path.open('rb'):
        third = spool.stage_patch(**args)
        assert third is None
        assert len(list(first.path.parent.glob('patch-*.json.gz'))) == 2
        assert spool.load_spooled_patch(first.path)['dispatch_status'] == 'dispatch_completed'
        assert spool.load_spooled_patch(second.path)['dispatch_status'] == 'dispatch_completed'


def test_windows_spool_flush_failure_never_reports_a_staged_ticket(tmp_path, monkeypatch):
    spool = importlib.import_module('cps.services.kobo_patch_spool')
    monkeypatch.setattr(spool, '_spool_root', lambda: tmp_path / 'private' / 'patch-spool')
    def fail_flush(_path):
        raise OSError('simulated directory flush failure')
    monkeypatch.setattr(spool, '_fsync_directory', fail_flush)
    ticket = spool.stage_patch(raw_body=b'{}', entitlement_id='book-1', user_id=17,
                               origin_device_id='reader-1')
    assert ticket is None
