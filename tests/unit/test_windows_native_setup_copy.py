import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import windows_native_setup as setup


class CopyStoppedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'library'
        with closing(sqlite3.connect(self.source / 'metadata.db')) as db:
            db.execute('CREATE TABLE books (title TEXT)')
            db.execute("INSERT INTO books VALUES ('Original')")
            db.commit()
        (self.source / 'Author').mkdir()
        (self.source / 'Author' / 'book.epub').write_bytes(b'original book')

    def tearDown(self):
        self.temp.cleanup()

    def test_copy_publishes_library_and_preserves_source(self):
        original = (self.source / 'metadata.db').read_bytes()
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertEqual((self.source / 'metadata.db').read_bytes(), original)
        self.assertEqual((self.destination / 'Author' / 'book.epub').read_bytes(), b'original book')
        with closing(sqlite3.connect(self.destination / 'metadata.db')) as db:
            self.assertEqual(db.execute('SELECT title FROM books').fetchall(), [('Original',)])
        self.assertFalse((self.root / '.library.cwng-copy').exists())
        self.assertFalse((self.root / '.library.cwng-copy.json').exists())

    def test_interrupted_copy_is_unpublished_and_resumes(self):
        original_copy = setup._replace_copy

        def interrupted(source, target, parent, cancel_event=None):
            if source.name == 'book.epub':
                raise OSError('simulated interruption')
            return original_copy(source, target, parent)

        with patch.object(setup, '_replace_copy', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'simulated interruption'):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.root / '.library.cwng-copy.json').exists())
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertEqual((self.destination / 'Author' / 'book.epub').read_bytes(), b'original book')

    def test_requires_confirmation_and_refuses_existing_destination(self):
        with self.assertRaises(ValueError):
            setup.copy_stopped_library(self.source, self.destination)
        self.destination.mkdir()
        (self.destination / 'keep').write_text('keep')
        with self.assertRaises(FileExistsError):
            setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertEqual((self.destination / 'keep').read_text(), 'keep')

    def test_resumption_revalidates_previously_copied_files(self):
        original_copy = setup._replace_copy

        def interrupt_after_book(source, target, parent, cancel_event=None):
            original_copy(source, target, parent)
            if source.name == 'book.epub':
                raise OSError('interrupted after copy')

        with patch.object(setup, '_replace_copy', side_effect=interrupt_after_book):
            with self.assertRaises(OSError):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        (self.source / 'Author' / 'book.epub').write_bytes(b'updated book')
        with closing(sqlite3.connect(self.source / 'metadata.db')) as db:
            db.execute("UPDATE books SET title = 'Updated'")
            db.commit()
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertEqual((self.destination / 'Author' / 'book.epub').read_bytes(), b'updated book')
        with closing(sqlite3.connect(self.destination / 'metadata.db')) as db:
            self.assertEqual(db.execute('SELECT title FROM books').fetchone(), ('Updated',))

    def test_resumption_refuses_files_no_longer_in_source(self):
        original_copy = setup._replace_copy

        def interrupt_after_book(source, target, parent, cancel_event=None):
            original_copy(source, target, parent)
            if source.name == 'book.epub':
                raise OSError('interrupted after copy')

        with patch.object(setup, '_replace_copy', side_effect=interrupt_after_book):
            with self.assertRaises(OSError):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        (self.source / 'Author' / 'book.epub').unlink()
        with self.assertRaisesRegex(ValueError, 'file absent from the source'):
            setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.root / '.library.cwng-copy' / 'Author' / 'book.epub').exists())

    def test_marker_publication_failure_is_retryable(self):
        rename = setup.os.rename
        marker = self.root / '.library.cwng-copy.json'

        def fail_marker(source, target):
            if Path(target) == marker:
                raise OSError('interrupted marker publication')
            return rename(source, target)

        with patch.object(setup.os, 'rename', side_effect=fail_marker):
            with self.assertRaisesRegex(OSError, 'marker publication'):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertFalse(marker.exists())
        self.assertFalse(self.destination.exists())
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertTrue((self.destination / 'metadata.db').exists())

    def test_resume_after_publish_verifies_destination_before_cleanup(self):
        marker = self.root / '.library.cwng-copy.json'
        unlink = Path.unlink

        def fail_marker(path, *args, **kwargs):
            if path == marker:
                raise OSError('interrupted marker cleanup')
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_marker):
            with self.assertRaisesRegex(OSError, 'marker cleanup'):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertTrue(marker.exists())
        self.assertTrue(self.destination.exists())
        result = setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertEqual(result, self.destination)
        self.assertFalse(marker.exists())

    def test_resume_after_publish_refuses_modified_destination(self):
        marker = self.root / '.library.cwng-copy.json'
        unlink = Path.unlink

        def fail_marker(path, *args, **kwargs):
            if path == marker:
                raise OSError('interrupted marker cleanup')
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_marker):
            with self.assertRaises(OSError):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        (self.destination / 'Author' / 'book.epub').write_bytes(b'changed after publication')
        with self.assertRaises(FileExistsError):
            setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertTrue(marker.exists())
        self.assertEqual((self.destination / 'Author' / 'book.epub').read_bytes(), b'changed after publication')

    def test_ready_marker_can_be_retained_through_verified_reentry(self):
        marker = self.root / '.library.cwng-copy.json'
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True, retain_marker=True)
        self.assertTrue(marker.exists())
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True, retain_marker=True)
        self.assertTrue(marker.exists())
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertFalse(marker.exists())

    def test_resume_refuses_replaced_source_volume(self):
        with patch.object(setup, '_snapshot_database', side_effect=OSError('interruption')):
            with self.assertRaises(OSError):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        actual_volume = setup._volume_identity

        def replaced_volume(path):
            if Path(path) == self.source:
                return ('different-volume', 999)
            return actual_volume(path)

        with patch.object(setup, '_volume_identity', side_effect=replaced_volume):
            with self.assertRaisesRegex(ValueError, 'another source or destination'):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
        self.assertFalse(self.destination.exists())

    def test_cooperative_cancel_keeps_copy_resumable(self):
        cancel = threading.Event()
        original_copy = setup._replace_copy

        def cancel_after_book(source, target, parent, cancel_event=None):
            original_copy(source, target, parent, cancel_event)
            if source.name == 'book.epub':
                cancel.set()

        with patch.object(setup, '_replace_copy', side_effect=cancel_after_book):
            with self.assertRaises(setup.SetupCancelled):
                setup.copy_stopped_library(self.source, self.destination, source_stopped=True, cancel_event=cancel)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.root / '.library.cwng-copy.json').exists())
        self.assertEqual((self.source / 'Author' / 'book.epub').read_bytes(), b'original book')
        cancel.clear()
        setup.copy_stopped_library(self.source, self.destination, source_stopped=True, cancel_event=cancel)
        self.assertEqual((self.destination / 'Author' / 'book.epub').read_bytes(), b'original book')

    def test_committed_wal_is_included_without_modifying_source(self):
        db = sqlite3.connect(self.source / 'metadata.db')
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("INSERT INTO books VALUES ('In WAL')")
            db.commit()
            original = {p.name: p.read_bytes() for p in self.source.glob('metadata.db*')}
            setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
            self.assertEqual({p.name: p.read_bytes() for p in self.source.glob('metadata.db*')}, original)
            with closing(sqlite3.connect(self.destination / 'metadata.db')) as copied:
                self.assertEqual(copied.execute('SELECT title FROM books ORDER BY title').fetchall(),
                                 [('In WAL',), ('Original',)])
        finally:
            db.close()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows case-insensitive paths')
    def test_case_preserved_database_sidecars_are_not_published(self):
        database = self.source / 'Metadata.db'
        (self.source / 'metadata.db').rename(database)
        db = sqlite3.connect(database)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("INSERT INTO books VALUES ('WAL record')")
            db.commit()
            self.assertTrue((self.source / 'Metadata.db-wal').exists())
            setup.copy_stopped_library(self.source, self.destination, source_stopped=True)
            copied_names = {path.name.casefold() for path in self.destination.iterdir()}
            self.assertFalse(copied_names & {'metadata.db-wal', 'metadata.db-shm', 'metadata.db-journal'})
            with closing(sqlite3.connect(self.destination / 'metadata.db')) as copied:
                self.assertEqual(copied.execute('SELECT title FROM books ORDER BY title').fetchall(),
                                 [('Original',), ('WAL record',)])
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
