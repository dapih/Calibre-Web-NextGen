"""Enforcement completion must not hide failed durable publication."""
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from windows_m0_dispatch import EnforcementDispatcher


def test_single_enforcement_refuses_completion_when_output_flush_fails(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    book_dir = library / 'author' / 'book'
    book_dir.mkdir(parents=True)
    database = library / 'metadata.db'
    database.write_bytes(b'database')
    book = book_dir / 'book.epub'
    with zipfile.ZipFile(book, 'w') as archive:
        archive.writestr('META-INF/container.xml',
                         '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr('book.opf',
                         '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                         '<metadata><dc:title>Title</dc:title></metadata></package>')
    dispatcher = EnforcementDispatcher.__new__(EnforcementDispatcher)
    dispatcher.library = library
    dispatcher.database = database
    dispatcher.owner = SimpleNamespace(ledger=SimpleNamespace(get=lambda _: {
        'operation': 'enforce', 'intent': {'book_id': 1, 'title': 'Title'}}))
    dispatcher._book = lambda _: (book_dir, book, 'Title')
    monkeypatch.setattr(os, 'fsync', lambda _: (_ for _ in ()).throw(OSError('flush failed')))
    with pytest.raises(OSError, match='flush failed'):
        dispatcher.verify('job')
