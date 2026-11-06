"""Compare an installed experimental payload to its build-time SHA-256 inventory.

This local proof checks exact bytes and file membership; it does not authenticate
the build inventory and is not an update or release-verification mechanism.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--inventory', required=True, type=Path)
    args = parser.parse_args()
    root = args.payload.resolve(strict=True)
    expected = json.loads(args.inventory.read_text(encoding='utf-8'))
    if not isinstance(expected, dict) or not expected:
        raise ValueError('the proof inventory is missing or invalid')
    actual = {}
    for file in sorted(root.rglob('*')):
        info = file.lstat()
        if getattr(info, 'st_file_attributes', 0) & 0x400 or file.is_symlink():
            raise ValueError(f'reparse point in installed proof payload: {file}')
        if file.is_file():
            relative = file.relative_to(root).as_posix()
            with file.open('rb') as stream:
                actual[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
    expected_names, actual_names = set(expected), set(actual)
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    changed = sorted(name for name in expected_names & actual_names if expected[name] != actual[name])
    if missing or extra or changed:
        raise RuntimeError(f'payload differs: missing={missing[:10]}, extra={extra[:10]}, changed={changed[:10]}')
    print('Exact proof payload files verified:', len(actual), flush=True)


if __name__ == '__main__':
    main()
