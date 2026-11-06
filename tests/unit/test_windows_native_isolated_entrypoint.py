"""The packaged entrypoint must import its sibling modules in isolated mode."""

import os
from pathlib import Path
import subprocess

import pytest


def test_launcher_help_runs_from_unrelated_directory_in_isolated_mode(tmp_path):
    runtime = os.environ.get('CWNG_NATIVE_TEST_RUNTIME')
    if os.name != 'nt' or not runtime:
        pytest.skip('requires the private Windows runtime')
    python = Path(runtime) / 'Scripts' / 'python.exe'
    launcher = Path(__file__).resolve().parents[2] / 'scripts' / 'windows_native_launcher.py'
    env = dict(os.environ, PYTHONHOME=str(tmp_path / 'hostile-home'),
               PYTHONPATH=str(tmp_path / 'hostile-path'))
    result = subprocess.run([str(python), '-I', str(launcher), '--help'], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '--state' in result.stdout
