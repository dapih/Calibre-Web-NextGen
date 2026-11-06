"""Contained conversion wrapper that records a durable successful return."""

import json
import os
from pathlib import Path
import re
import runpy
import sys
import uuid

from calibre_library_target import require_native_writer_lease
from windows_m0_inbox import _flush_directory


def main(script, job_id, attempt_number, log_path):
    """Run the actual converter and publish a marker only after its zero exit."""
    generation = os.environ.get('CWNG_NATIVE_JOB_GENERATION', '')
    if (not re.fullmatch('nj1-[0-9a-f]{32}', generation) or
            uuid.UUID(hex=job_id).hex != job_id or
            not attempt_number.isascii() or not attempt_number.isdigit() or int(attempt_number) < 1):
        raise ValueError('invalid conversion attempt identity')
    script = Path(script).resolve(strict=True)
    log_path = Path(log_path).resolve()
    attempt = log_path.parent
    if (not attempt.is_dir() or log_path.name != 'worker.log' or
            attempt.name != 'convert-library-' + job_id + '-' + attempt_number):
        raise ValueError('invalid conversion attempt directory')
    require_native_writer_lease()
    with log_path.open('a', encoding='utf-8', buffering=1) as output:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = sys.stderr = output
            sys.argv = [str(script)]
            try:
                runpy.run_path(str(script), run_name='__main__')
            except SystemExit as error:
                if error.code not in (0, None):
                    raise
            else:
                raise RuntimeError('converter returned without its explicit exit status')
            output.flush()
            os.fsync(output.fileno())
            marker = {'schema': 1, 'job_id': job_id, 'generation': generation, 'attempt': int(attempt_number)}
            temporary = attempt / ('.conversion-complete-' + uuid.uuid4().hex)
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(marker, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, attempt / 'conversion-complete.json')
            _flush_directory(attempt)
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == '__main__':
    main(*sys.argv[1:])
