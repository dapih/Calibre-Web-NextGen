"""Essential restart and data-preservation coverage for the M0 ledger."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def test_interrupted_intent_requires_reconciliation(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    from windows_m0_ledger import Ledger, LedgerStateError

    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "book.epub"
    source.write_bytes(b"disposable input")
    script = textwrap.dedent("""
        import os, sys
        sys.path.insert(0, sys.argv[1])
        from windows_m0_ledger import Ledger
        ledger = Ledger(sys.argv[2])
        job = ledger.enqueue('request-1', 'ingest', {'source': sys.argv[3]})
        ledger.start(job, 'old-generation')
        os._exit(23)
    """)
    result = subprocess.run([sys.executable, "-c", script, str(scripts), str(database), str(source)])
    assert result.returncode == 23

    ledger = Ledger(database)
    job = ledger.enqueue("request-1", "ingest", {"source": str(source)})
    assert ledger.get(job)["state"] == "running"
    with pytest.raises(LedgerStateError):
        ledger.enqueue("request-1", "ingest", {"source": "different"})
    # The controller must fence old descendants before invoking this transition.
    assert ledger.review_interrupted() == 1
    assert [item["id"] for item in ledger.list_jobs("review")] == [job]
    with pytest.raises(LedgerStateError):
        ledger.start(job, "new-generation")
    assert source.read_bytes() == b"disposable input"
    ledger.reconcile(job, outcome="retry", evidence="no committed book found")
    assert [item["id"] for item in ledger.list_jobs("pending")] == [job]
    ledger.start(job, "new-generation")
    with pytest.raises(LedgerStateError):
        ledger.complete(job, "old-generation", "wrong owner")
    ledger.complete(job, "new-generation", "verified library commit")
    assert Ledger(database).get(job)["state"] == "completed"
    assert source.read_bytes() == b"disposable input"


def test_metadata_debounce_survives_restart_and_preserves_newer_change(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    from windows_m0_ledger import Ledger

    database = tmp_path / 'ledger.sqlite'
    ledger = Ledger(database)
    job = ledger.debounce_metadata(42, 10_000)
    observed = ledger.get(job)['intent']
    assert ledger.debounce_metadata(42, 20_000) == job
    assert not ledger.promote_metadata(job, observed, {'book_id': 42, 'title': 'old'})
    restarted = Ledger(database)
    latest = restarted.get(job)
    assert latest['state'] == 'pending'
    assert latest['intent'] == {'book_id': 42, 'due_ns': 20_000}
    assert restarted.promote_metadata(job, latest['intent'], {'book_id': 42, 'title': 'new'})
    assert restarted.get(job)['operation'] == 'enforce'


def test_restart_budget_survives_controller_restart(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    from windows_m0_ledger import Ledger
    from windows_m0_supervisor import RestartBudget

    database = tmp_path / 'ledger.sqlite'
    ledger = Ledger(database)
    assert [ledger.restart_state('control', failure=True, now_ns=n)[1]
            for n in (1, 2, 3)] == [2, 10, 30]
    assert Ledger(database).restart_state('control', failure=True, now_ns=4) == (True, None)
    assert RestartBudget(ledger=Ledger(database), role='control').paused
    ledger.restart_state('control', reset=True, now_ns=5)
    assert not RestartBudget(ledger=Ledger(database), role='control').paused
    assert ledger.restart_state('control', failure=True, now_ns=6)[1] == 2
    assert ledger.restart_state('control', failure=True, now_ns=300_000_000_006)[1] == 2


def test_reconciled_retry_clears_previous_cancellation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    from windows_m0_ledger import Ledger

    ledger = Ledger(tmp_path / 'ledger.sqlite')
    job = ledger.request_conversion()
    ledger.start(job, 'generation')
    assert ledger.cancel_conversion() == 1
    assert ledger.cancellation_requested(job)
    assert ledger.review_interrupted() == 1
    ledger.reconcile(job, outcome='retry', evidence='old worker fenced; retry verified safe')
    assert ledger.get(job)['state'] == 'pending'
    assert not Ledger(ledger.path).cancellation_requested(job)
