"""Offline completion verification for a fenced whole-library conversion.

Callers must hold the exclusive coordinator and verify the setup-bound library,
ingest and private-state identities before calling this function. This module
never retries work or infers a successful conversion from a process exit alone.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from windows_m0_conversion import LibraryConversionDispatcher
from windows_m0_dispatch import IngestDispatcher
from windows_m0_inbox import _flush_directory
from windows_m0_job import generation_exists


def confirm_conversion(controller, owner, database, job_id, verify_storage):
    """Complete a reviewed conversion only when its finished outcome is proven."""
    if not callable(verify_storage):
        raise ValueError('a storage identity verifier is required')
    database = Path(database).resolve(strict=True)
    state = Path(controller.state).resolve(strict=True)
    library = Path(controller.library).resolve(strict=True)
    app_db = Path(controller.environment['CWA_APP_DB_PATH']).resolve(strict=True)
    cwa_db = (Path(controller.environment['CWA_DB_PATH']) / 'cwa.db').resolve(strict=True)
    work_root = (state / 'ingest-work').resolve(strict=True)
    if not work_root.is_relative_to(state):
        raise ValueError('conversion evidence escapes private state')

    class OfflineIngest:
        _check_settings = IngestDispatcher._check_settings

    ingest = OfflineIngest()
    ingest.owner = owner
    ingest.library = library
    ingest.database = database
    ingest.app_db = app_db
    ingest.cwa_db = cwa_db
    verifier = LibraryConversionDispatcher(ingest)

    def verify(record, outcome):
        if (outcome != 'completed' or record.get('operation') != 'convert-library' or
                not isinstance(record.get('generation'), str) or
                generation_exists(controller.helper, record['generation']) or
                type(record.get('attempts')) is not int or record['attempts'] < 1):
            return False
        try:
            if uuid.UUID(hex=job_id).hex != job_id:
                return False
        except (ValueError, TypeError, AttributeError):
            return False
        intent = record.get('intent')
        if not isinstance(intent, dict) or set(intent) != {'target', 'expected', 'sources'}:
            return False
        target, expected, sources = intent['target'], intent['expected'], intent['sources']
        if (target not in ('epub', 'kepub') or not isinstance(expected, list) or not expected or
                any(type(book) is not int or book <= 0 for book in expected) or
                len(set(expected)) != len(expected) or not isinstance(sources, list) or not sources):
            return False
        for source in sources:
            if (not isinstance(source, dict) or set(source) != {'path', 'sha256'} or
                    not isinstance(source['path'], str) or not isinstance(source['sha256'], str) or
                    not re.fullmatch('[0-9a-f]{64}', source['sha256'])):
                return False
        attempt = work_root / ('convert-library-' + job_id + '-' + str(record['attempts']))
        if not attempt.is_dir() or not attempt.resolve(strict=True).is_relative_to(work_root):
            return False
        if owner.ledger.cancellation_requested(job_id) or (attempt / 'cancel').exists():
            return False
        marker_path = attempt / 'conversion-complete.json'
        if not marker_path.is_file() or not marker_path.resolve(strict=True).is_relative_to(attempt.resolve(strict=True)):
            return False
        try:
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeDecodeError):
            return False
        if (not isinstance(marker, dict) or type(marker.get('schema')) is not int or
                type(marker.get('attempt')) is not int or
                marker != {'schema': 1, 'job_id': job_id, 'generation': record['generation'],
                           'attempt': record['attempts']}):
            return False
        log = attempt / 'worker.log'
        if not log.is_file() or not log.resolve(strict=True).is_relative_to(attempt.resolve(strict=True)):
            return False
        with log.open('rb') as stream:
            # A final success summary can coexist with a swallowed backup
            # error. Read the whole bounded log so an earlier failed retention
            # cannot be hidden by a long successful run.
            stream.seek(0, os.SEEK_END)
            if stream.tell() > 64 * 1024 * 1024:
                return False
            stream.seek(0)
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        if any('[convert-library]: ERROR -' in line or
               '[convert-library]: WARN - Could not prepare backup directories' in line for line in lines):
            return False
        footer = f'[convert-library]: Library conversion complete! {len(expected)} books converted! Exiting now...'
        nonempty = [line.strip() for line in lines if line.strip()]
        summaries = [line for line in nonempty if '[convert-library]: Library conversion complete' in line]
        if (not summaries or not summaries[-1].endswith(footer) or not nonempty or
                not nonempty[-1].startswith('NextGen Convert Library Service - Run Ended: ')):
            return False
        if verifier.verify(job_id) is not True:
            return False

        # Verify the recorded target belongs to each intended book and its
        # metadata agrees with the actual file before acknowledging completion.
        durable = [database]
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            for book_id in expected:
                rows = db.execute(
                    'SELECT books.path,data.name,data.format,data.uncompressed_size FROM books '
                    'JOIN data ON data.book=books.id WHERE books.id=? AND LOWER(data.format)=?',
                    (book_id, target),
                ).fetchall()
                if len(rows) != 1:
                    return False
                book_path, name, fmt, size = rows[0]
                path = verifier._path(book_path, name, fmt)
                if type(size) is not int or size != path.stat().st_size:
                    return False
                durable.append(path)
        wal = database.with_name(database.name + '-wal')
        if wal.exists():
            durable.append(wal)
        directories = {work_root, attempt, database.parent}
        for path in durable:
            with path.open('r+b') as stream:
                stream.flush()
                os.fsync(stream.fileno())
            directories.add(path.parent)
        for directory in directories:
            _flush_directory(directory)
        return Path(verify_storage()).resolve(strict=True) == database

    owner.reconcile(job_id, outcome='completed',
                    evidence='conversion results and retained source formats verified offline', verify=verify)
    return 'The completed conversion was verified. Other reviewed work remains paused.'
