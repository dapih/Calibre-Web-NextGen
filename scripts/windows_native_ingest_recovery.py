"""Offline completion verification for an interrupted single-file ingest.

The caller holds exclusive profile ownership and has fenced earlier work.
Only future attempts with an explicit job-bound success receipt qualify. A
missing receipt never establishes whether an import committed or is safe to
retry; transformed output is deliberately left for review.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import uuid

from windows_m0_inbox import _flush_directory, _locked_source
from windows_m0_job import generation_exists


def confirm_ingest(controller, owner, database, job_id, verify_storage):
    """Complete only an exact-byte, independently verified ingest result."""
    if not callable(verify_storage):
        raise ValueError('a storage identity verifier is required')
    database = Path(database).resolve(strict=True)
    state = Path(controller.state).resolve(strict=True)
    library = Path(controller.library).resolve(strict=True)
    inbox = Path(controller.ingest).resolve(strict=True)
    stage_root = (state / 'staged').resolve(strict=True)
    work_root = (state / 'ingest-work').resolve(strict=True)
    if not stage_root.is_relative_to(state) or not work_root.is_relative_to(state):
        raise ValueError('ingest evidence escapes private state')

    def verify(record, outcome):
        generation, attempt_no = record.get('generation'), record.get('attempts')
        if (outcome != 'completed' or record.get('operation') != 'ingest' or
                not isinstance(generation, str) or not re.fullmatch('nj1-[0-9a-f]{32}', generation) or
                generation_exists(controller.helper, generation) or type(attempt_no) is not int or attempt_no < 1):
            return False
        try:
            if uuid.UUID(hex=job_id).hex != job_id:
                return False
        except (ValueError, TypeError, AttributeError):
            return False
        intent = record.get('intent')
        if not isinstance(intent, dict) or set(intent) != {'source', 'staged', 'sha256', 'size'}:
            return False
        digest, size = intent['sha256'], intent['size']
        if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
                type(size) is not int or size <= 0 or not isinstance(intent['source'], str) or
                not isinstance(intent['staged'], str)):
            return False
        source, staged = Path(intent['source']), Path(intent['staged'])
        if (not source.is_absolute() or not staged.is_absolute() or
                source.suffix.lower() != '.epub' or staged.suffix.lower() != '.epub'):
            return False
        resolved_source = source.resolve(strict=True)
        stage_info = staged.stat(follow_symlinks=False)
        staged = staged.resolve(strict=True)
        if (not resolved_source.is_relative_to(inbox) or not staged.is_relative_to(stage_root) or
                not staged.is_file() or getattr(stage_info, 'st_file_attributes', 0) & 0x400 or
                stage_info.st_nlink != 1 or stage_info.st_size != size or
                any(Path(str(source) + suffix).exists() for suffix in ('.cwa.json', '.cwa.failed.json')) or
                owner.ledger.cancellation_requested(job_id)):
            return False
        prefix = 'ingest-' + job_id + '-' + str(attempt_no) + '-'
        candidates = []
        for attempt in work_root.glob(prefix + '*'):
            if not re.fullmatch(re.escape(prefix) + '[0-9a-f]{32}', attempt.name):
                continue
            info = attempt.stat(follow_symlinks=False)
            if (getattr(info, 'st_file_attributes', 0) & 0x400 or not attempt.is_dir() or
                    attempt.resolve(strict=True).parent != work_root):
                return False
            marker = attempt / 'ingest-complete.json'
            try:
                marker_info = marker.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (getattr(marker_info, 'st_file_attributes', 0) & 0x400 or
                    not marker.is_file() or marker_info.st_size > 16384):
                return False
            try:
                receipt = json.loads(marker.read_text(encoding='utf-8'))
            except (ValueError, UnicodeDecodeError):
                return False
            candidates.append((attempt, marker, receipt))
        if len(candidates) != 1:
            return False
        attempt, marker, receipt = candidates[0]
        if (not isinstance(receipt, dict) or set(receipt) !=
                {'schema', 'job_id', 'generation', 'attempt', 'sha256', 'size', 'book_ids', 'mode'} or
                type(receipt['schema']) is not int or receipt['schema'] != 1 or
                receipt['job_id'] != job_id or receipt['generation'] != generation or
                type(receipt['attempt']) is not int or receipt['attempt'] != attempt_no or
                receipt['sha256'] != digest or type(receipt['size']) is not int or receipt['size'] != size or
                receipt['mode'] != 'original-epub-no-backup'):
            return False
        books = receipt['book_ids']
        if (not isinstance(books, list) or not books or
                any(type(book) is not int or book <= 0 for book in books) or
                len(set(books)) != len(books)):
            return False
        if Path(verify_storage()).resolve(strict=True) != database:
            return False
        outputs = []
        with _locked_source(source, inbox, resolved_source) as stream:
            if os.fstat(stream.fileno()).st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                return False
            with staged.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    return False
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    return False
                ids = [row[0] for row in db.execute('SELECT book FROM identifiers WHERE type=? AND val=?',
                       ('cwng_ingest_sha256_' + digest, digest))]
                if len(ids) != len(books) or set(ids) != set(books):
                    return False
                for book_id in books:
                    rows = db.execute('SELECT books.path,data.name,data.format,data.uncompressed_size '
                                      'FROM books JOIN data ON books.id=data.book WHERE books.id=?',
                                      (book_id,)).fetchall()
                    matches = []
                    for book_path, name, fmt, stored_size in rows:
                        if not isinstance(book_path, str) or not isinstance(name, str) or not isinstance(fmt, str):
                            return False
                        parts = PurePosixPath(book_path)
                        if (parts.is_absolute() or not parts.parts or any(part in ('', '.', '..') or
                                ':' in part or '\\' in part for part in parts.parts) or
                                not name or any(ch in name for ch in '/\\:') or not fmt.isascii() or not fmt.isalnum()):
                            return False
                        if fmt.upper() != 'EPUB':
                            continue
                        path = library.joinpath(*parts.parts, name + '.' + fmt.lower())
                        try:
                            path = path.resolve(strict=True)
                            if (path.is_relative_to(library) and path.is_file() and type(stored_size) is int and
                                    stored_size == size == path.stat().st_size):
                                with path.open('rb') as file:
                                    if hashlib.file_digest(file, 'sha256').hexdigest() == digest:
                                        matches.append(path)
                        except OSError:
                            continue
                    if len(matches) != 1:
                        return False
                    outputs.extend(matches)
            durable = [database, staged, marker, *outputs]
            wal = database.with_name(database.name + '-wal')
            if wal.exists():
                durable.append(wal)
            directories = {state, work_root, attempt, stage_root, database.parent}
            for path in durable:
                with path.open('r+b') as file:
                    os.fsync(file.fileno())
                directories.add(path.parent)
            for output in outputs:
                parent = output.parent
                while parent != library:
                    if not parent.is_relative_to(library):
                        return False
                    directories.add(parent)
                    parent = parent.parent
                directories.add(library)
            for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
                _flush_directory(directory)
            return Path(verify_storage()).resolve(strict=True) == database

    owner.reconcile(job_id, outcome='completed',
                    evidence='job-bound ingest and exact stored format verified offline', verify=verify)
    return 'The completed ingest was verified. Other reviewed work remains paused.'
