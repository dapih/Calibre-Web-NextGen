"""Run one contained ingest and record its job-bound successful outcome."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
import uuid

from calibre_library_target import require_native_writer_lease
from windows_m0_inbox import _flush_directory
from windows_m0_origin import install as install_origin_trace


def main(script, source, job_id, attempt_number, expected_digest, expected_size, log_path):
    generation = os.environ.get('CWNG_NATIVE_JOB_GENERATION', '')
    if (not re.fullmatch('nj1-[0-9a-f]{32}', generation) or
            uuid.UUID(hex=job_id).hex != job_id or not attempt_number.isascii() or
            not attempt_number.isdigit() or int(attempt_number) < 1 or
            not re.fullmatch('[0-9a-f]{64}', expected_digest) or
            not expected_size.isascii() or not expected_size.isdigit() or int(expected_size) <= 0):
        raise ValueError('invalid ingest attempt identity')
    script = Path(script).resolve(strict=True)
    if script != Path(__file__).with_name('ingest_processor.py').resolve(strict=True):
        raise ValueError('ingest worker must run the bundled processor')
    source = Path(source).resolve(strict=True)
    log_path = Path(log_path).resolve()
    attempt = log_path.parent
    prefix = 'ingest-' + job_id + '-' + attempt_number + '-'
    if (not attempt.is_dir() or log_path.name != 'worker.log' or
            not re.fullmatch(re.escape(prefix) + '[0-9a-f]{32}', attempt.name) or
            source.parent != attempt or source.name != 'input' + source.suffix):
        raise ValueError('invalid ingest attempt directory')
    if source.stat().st_size != int(expected_size):
        raise ValueError('working input size changed')
    with source.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != expected_digest:
            raise ValueError('working input digest changed')
    require_native_writer_lease()
    install_origin_trace(str(script))
    observed = []

    def remember_books(book_ids):
        if (observed or not isinstance(book_ids, list) or not book_ids or
                any(type(book) is not int or book <= 0 for book in book_ids) or
                len(book_ids) != len(set(book_ids))):
            raise ValueError('invalid ingest completion book IDs')
        observed.extend(book_ids)

    with log_path.open('a', encoding='utf-8', buffering=1) as output:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = sys.stderr = output
            namespace = runpy.run_path(str(script), run_name='__cwng_native_ingest__')
            result = namespace['main'](str(source), native_completion=remember_books)
            if type(result) is not int:
                raise RuntimeError('ingest processor returned an invalid status')
            if result != 0:
                raise SystemExit(result)
            output.flush()
            os.fsync(output.fileno())
            if not observed:
                # Skipped or quarantined input is not evidence of an import.
                return
            marker = {'schema': 1, 'job_id': job_id, 'generation': generation,
                      'attempt': int(attempt_number), 'sha256': expected_digest,
                      'size': int(expected_size), 'book_ids': sorted(observed),
                      'mode': 'original-epub-no-backup'}
            temporary = attempt / ('.ingest-complete-' + uuid.uuid4().hex)
            with temporary.open('x', encoding='utf-8') as file:
                json.dump(marker, file, separators=(',', ':'))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, attempt / 'ingest-complete.json')
            _flush_directory(attempt)
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == '__main__':
    main(*sys.argv[1:])
