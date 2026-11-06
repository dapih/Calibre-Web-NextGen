"""A surviving native converter must not write into the user's library."""

import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows parent-death behavior')
def test_surviving_converter_writes_only_private_staging(tmp_path):
    library = tmp_path / 'library'
    state = tmp_path / 'state'
    library.mkdir()
    state.mkdir()
    (library / 'book.txt').write_text('input', encoding='utf-8')
    ready, release, done = (tmp_path / name for name in ('ready', 'release', 'done'))
    script = r'''
import os, pathlib, subprocess, sys
from cps import helper
from cps.tasks import convert
library, state, ready, release, done = map(pathlib.Path, sys.argv[1:])
os.environ['CWNG_NATIVE_STATE_DIR'] = str(state)
convert.config.get_book_path = lambda: str(library)
convert.config.config_kepubifypath = None
convert.config.config_embed_metadata = False
convert.get_temp_dir = lambda: str(state)
task = convert.TaskConvert.__new__(convert.TaskConvert)
def fake_converter(path, old_ext, new_ext, has_cover):
    code = "import pathlib,sys,time; out,ready,release,done=map(pathlib.Path,sys.argv[1:]); ready.write_text(str(out));\nwhile not release.exists(): time.sleep(.02)\nout.write_text('converted'); done.write_text('done')"
    child = subprocess.Popen([sys.executable, '-c', code, path + new_ext, str(ready), str(release), str(done)])
    child.wait(timeout=20)
    return child.returncode, None
task._convert_calibre = fake_converter
task._convert_native_staged(str(library/'book'), '.txt', '.mobi', False)
'''
    parent = subprocess.Popen([sys.executable, '-c', script, str(library), str(state),
                               str(ready), str(release), str(done)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            if parent.poll() is not None:
                stdout, stderr = parent.communicate()
                pytest.fail(f'conversion did not start: {stdout!r} {stderr!r}')
            if time.monotonic() >= deadline:
                pytest.fail('conversion did not start')
            time.sleep(.05)
        output = Path(ready.read_text())
        assert output.is_relative_to(state)
        parent.kill()
        parent.wait(timeout=10)
        release.touch()
        deadline = time.monotonic() + 10
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert done.exists()
        assert output.read_text() == 'converted'
        assert not (library / 'book.mobi').exists()
        assert (library / 'book.txt').read_text(encoding='utf-8') == 'input'
    finally:
        release.touch()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)


@pytest.mark.skipif(os.name != 'nt', reason='Windows native Drive coordination')
def test_native_drive_conversion_holds_writer_lock_for_materialize_and_cleanup(tmp_path, monkeypatch):
    import cps.helper  # Establish the application's normal import order.
    from cps.tasks import convert

    monkeypatch.setenv('CWNG_NATIVE_MODE', '1')
    monkeypatch.setattr(convert.config, 'config_use_google_drive', True, raising=False)
    monkeypatch.setattr(convert.config, 'get_book_path', lambda: str(tmp_path))
    held = []

    @contextmanager
    def writer_lock(_directory):
        assert not held
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(convert.content_server.ownership, 'operation', writer_lock)
    database = SimpleNamespace(get_book=lambda _: SimpleNamespace(path='book', title='Test'),
                               get_book_format=lambda *_: SimpleNamespace(name='input'),
                               session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(convert.db, 'CalibreDB', lambda **_: database)

    class DriveFile:
        def GetContentFile(self, path):
            assert held
            Path(path).write_bytes(b'drive input')

    monkeypatch.setattr(convert.gdriveutils, 'getFileFromEbooksFolder',
                        lambda _path, name: None if name == 'cover.jpg' else DriveFile())
    uploaded = []
    monkeypatch.setattr(convert.gdriveutils, 'updateGdriveCalibreFromLocal',
                        lambda: uploaded.append(bool(held)))
    task = convert.TaskConvert(str(tmp_path / 'book' / 'input'), 1, 'conversion',
                               {'old_book_format': 'TXT', 'new_book_format': 'EPUB'}, None)
    def convert_locked():
        assert held and (tmp_path / 'book' / 'input.txt').read_bytes() == b'drive input'
        return 'input.epub'
    monkeypatch.setattr(task, '_convert_ebook_format_locked', convert_locked)
    monkeypatch.setattr(task, '_handleSuccess', lambda: None)
    task.run(None)
    assert uploaded == [True]
    assert not (tmp_path / 'book' / 'input.txt').exists()
    assert not held
