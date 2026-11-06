"""Test-only inherited-handle adapter; never install as a product entrypoint.

Copy into the disposable old payload's runtime before its inventory is built.
The test parent is responsible for proving the handle came from its share=0
open and for passing only that handle via the explicit inheritance allowlist.
"""
from pathlib import Path
import importlib.util
import sys

runtime = Path(sys.executable).resolve(strict=True).parent.parent
if not Path(__file__).resolve(strict=True).is_relative_to(runtime):
    raise RuntimeError('test adapter must be inside the disposable private runtime')
spec = importlib.util.spec_from_file_location('windows_native_maintenance_recovery',
    runtime.parent / 'scripts' / 'windows_native_maintenance_recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from windows_native_checkpoint import recover_guarded_activation
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--state', required=True)
parser.add_argument('--program-root', required=True)
parser.add_argument('--recovery', required=True)
parser.add_argument('--inherited-gate', type=int, required=True)
args = parser.parse_args()
module._require_private_origins()
recover_guarded_activation(args.state, args.program_root, args.recovery,
                           _inherited_gate=args.inherited_gate)
