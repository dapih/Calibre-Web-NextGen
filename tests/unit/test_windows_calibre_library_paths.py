"""Calibre path adaptation stays at the command boundary, not saved settings."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("windows_calibre_paths_test", ROOT / "cps/calibre_library_target.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("arguments", [
    ["--with-library", "long-library"],
    ["--library-path", "long-library"],
    ["--with-library=long-library"],
    ["--library-path=long-library"],
])
def test_vendor_library_arguments_use_alias_without_changing_the_original(monkeypatch, arguments):
    monkeypatch.setattr(policy, "local_library_path", lambda value: "short-library")
    original = [Path("calibredb.exe"), "list", *arguments]
    adapted = policy.calibredb_command(original, policy.LibraryTarget([], None))
    assert "long-library" in " ".join(map(str, original))
    assert "long-library" not in " ".join(map(str, adapted))
    assert adapted[-1].endswith("short-library")
    assert adapted[:2] == original[:2]


def test_remote_library_address_is_preserved():
    address = "http://127.0.0.1:8080/#library"
    assert policy.local_library_path(address) == address
    assert policy.local_library_path("") == ""


def test_existing_short_windows_name_is_used_before_creating_a_junction(monkeypatch):
    real = "C:\\" + "deep folder\\" * 8 + "books"
    short = r"C:\DEEPFO~1\books"
    path = SimpleNamespace(abspath=lambda value: value, isdir=lambda value: value == real,
                           samefile=lambda left, right: (left, right) == (short, real))
    monkeypatch.setattr(policy, "os", SimpleNamespace(name="nt", path=path, fspath=str, environ={}))
    monkeypatch.setattr(policy, "_short_windows_path", lambda value: short, raising=False)
    assert policy.local_library_path(real) == short


def test_server_id_matches_the_alias_used_by_calibre(monkeypatch):
    monkeypatch.setattr(policy, "local_library_path", lambda value: "/cache/short-library")
    assert policy.library_id("/some/long/library") == "short-library"
