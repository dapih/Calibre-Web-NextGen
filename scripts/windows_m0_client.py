"""Submit native application actions to the local authenticated owner."""
from pathlib import Path
import time

from windows_m0_activation import _pipe_name, _profile
from windows_m0_control import request


def request_action(state_dir, operation, *, book_id=None, job_id=None):
    if operation not in ('convert-library', 'cancel-convert-library', 'conversion-status', 'refresh-ingest',
                         'manual-enforce', 'cancel-manual-enforce', 'manual-enforce-status',
                         'epub-fixer', 'cancel-epub-fixer', 'epub-fixer-status', 'job-status'):
        raise ValueError('unsupported native action')
    state = _profile(state_dir)
    key = (Path(state) / 'control.key').read_bytes()
    if len(key) != 32:
        raise ValueError('invalid control key')
    message = {'operation': operation}
    if operation == 'epub-fixer':
        message['book_id'] = book_id
    if operation == 'job-status':
        message['job_id'] = job_id
    deadline = time.monotonic() + 5
    while True:
        try:
            result = request(_pipe_name(state), key, message)
            break
        except OSError as error:
            if getattr(error, 'winerror', None) != 231 or time.monotonic() >= deadline:
                raise
            time.sleep(.05)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result
