"""Exercise the native launcher through an independently compiled child process."""
import os
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows native bootstrap')
def test_bootstrap_passes_private_paths_and_sanitizes_environment(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    compiler = repo / '.m0/mingw64/bin/gcc.exe'
    if not compiler.is_file():
        pytest.skip('private Windows C compiler is unavailable')
    root = tmp_path / 'CWNG Ω'
    payload = root / 'versions' / '1.2.3'
    child = payload / 'runtime' / 'pythonw.exe'
    child.parent.mkdir(parents=True)
    (root / 'active-version.txt').write_text('1.2.3\n', encoding='ascii')
    fake_source = tmp_path / 'fake.c'
    fake_source.write_text(r'''
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, PWSTR c, int d) {
    int n; wchar_t **v = CommandLineToArgvW(GetCommandLineW(), &n);
    FILE *f = _wfopen(L"observed.txt", L"w, ccs=UTF-8");
    if (!v || !f) return 3;
    for (int i = 0; i < n; ++i) fwprintf(f, L"%ls\n", v[i]);
    wchar_t x[4096]; DWORD got = GetEnvironmentVariableW(L"PYTHONPATH", x, 4096);
    fwprintf(f, L"PYTHONPATH=%ls\n", got ? x : L"<absent>");
    got = GetEnvironmentVariableW(L"LOCALAPPDATA", x, 4096);
    fwprintf(f, L"LOCALAPPDATA=%ls\n", got ? x : L"<absent>");
    fclose(f);
    int result = 0;
    for (int i = 0; i < n; ++i) {
        if (!wcscmp(v[i], L"--exit-error")) result = 3;
        if (!wcscmp(v[i], L"--linger")) {
            while (GetFileAttributesW(L"release.txt") == INVALID_FILE_ATTRIBUTES) Sleep(50);
        }
    }
    LocalFree(v); return result;
}
''', encoding='utf-8')
    compile_flags = ['-std=c11', '-municode', '-mwindows', '-static']
    compiled_child = tmp_path / 'fake.exe'
    subprocess.run([str(compiler), *compile_flags, '-o', str(compiled_child), str(fake_source), '-lshell32'], check=True)
    shutil.copy2(compiled_child, child)
    bootstrap = root / 'CWNG.exe'
    compiled_bootstrap = tmp_path / 'bootstrap.exe'
    subprocess.run([str(compiler), *compile_flags, '-DCWNG_BOOTSTRAP_TEST', '-Wall', '-Wextra', '-Werror', '-o', str(compiled_bootstrap),
                    str(repo / 'scripts/windows-native-bootstrap.c'), '-lshell32', '-luser32', '-ladvapi32'], check=True)
    shutil.copy2(compiled_bootstrap, bootstrap)
    state = str(tmp_path / 'Personal Ω state') + '\\'
    env = dict(os.environ, PYTHONPATH='hostile-modules', LOCALAPPDATA=str(tmp_path))
    subprocess.run([str(bootstrap), '--state', state], env=env, check=True, timeout=10)
    output = payload / 'observed.txt'
    deadline = time.monotonic() + 10
    content = ''
    while time.monotonic() < deadline:
        if output.exists():
            content = output.read_text(encoding='utf-8-sig')
            if 'LOCALAPPDATA=' in content:
                break
        time.sleep(.05)
    lines = content.splitlines()
    assert lines[0] == str(child)
    assert lines[1:4] == ['-I', '-B', str(payload / 'scripts/windows_native_launcher.py')]
    assert lines[4:14] == ['--helper', str(payload / 'cwng-native.dll'),
                           '--runtime', str(payload / 'runtime'),
                           '--calibre', str(payload / 'calibre'),
                           '--kepubify', str(payload / 'kepubify/kepubify.exe'),
                           '--magick', str(payload / 'imagemagick')]
    assert lines[14:16] == ['--state', str(Path(state))]
    assert lines[16:] == ['PYTHONPATH=<absent>', 'LOCALAPPDATA=' + env['LOCALAPPDATA']]
    sys.path.insert(0, str(repo / 'scripts'))
    try:
        from windows_native_profile import require_private_directory
        assert require_private_directory(state) == Path(state).resolve()
        assert require_private_directory(Path(state) / 'config') == (Path(state) / 'config').resolve()
    finally:
        sys.path.pop(0)
    output.unlink()
    (Path(state) / 'maintenance-journal.json').write_text('{}', encoding='utf-8')
    blocked = subprocess.run([str(bootstrap), '--state', state], env=env, timeout=10)
    assert blocked.returncode != 0
    assert not output.exists()
    (Path(state) / 'maintenance-journal.json').unlink()

    # The child must retain the shared gate even if its bootstrap is killed.
    process = subprocess.Popen([str(bootstrap), '--state', state, '--linger'], env=env)
    release = payload / 'release.txt'
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    lock_file = str(Path(state) / 'bootstrap-maintenance.lock')
    try:
        deadline = time.monotonic() + 10
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert output.exists()
        process.kill()
        process.wait(timeout=5)
        handle = kernel.CreateFileW(lock_file, 0xC0000000, 0, None, 3, 0x80, None)
        assert handle == invalid and ctypes.get_last_error() == 32
        handle = kernel.CreateFileW(str(tmp_path / 'CWNG-install.lock'), 0xC0000000, 0, None, 3, 0x80, None)
        assert handle == invalid and ctypes.get_last_error() == 32
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        handle = kernel.CreateFileW(lock_file, 0xC0000000, 0, None, 3, 0x80, None)
        if handle != invalid:
            kernel.CloseHandle(handle)
            break
        time.sleep(.05)
    else:
        pytest.fail('maintenance gate remained held after the child exited')

    # A running maintenance transaction must prevent new bootstrap children.
    output.unlink(missing_ok=True)
    exclusive = kernel.CreateFileW(lock_file, 0xC0000000, 0, None, 3, 0x80, None)
    assert exclusive != invalid
    try:
        blocked = subprocess.run([str(bootstrap), '--state', state], env=env, timeout=10)
        assert blocked.returncode != 0
        assert not output.exists()
    finally:
        kernel.CloseHandle(exclusive)

    # A broken private interpreter must not be reported as a successful start.
    failed_child = subprocess.run([str(bootstrap), '--state', state, '--exit-error'], env=env, timeout=10)
    assert failed_child.returncode != 0

    # The experimental installer must never point at an existing production
    # profile, even when a caller tries to override its state argument.
    proof_bootstrap = tmp_path / 'proof.exe'
    subprocess.run([str(compiler), *compile_flags, '-DCWNG_BOOTSTRAP_TEST', '-DCWNG_INSTALLER_PROOF',
                    '-Wall', '-Wextra', '-Werror', '-o', str(proof_bootstrap),
                    str(repo / 'scripts/windows-native-bootstrap.c'), '-lshell32', '-luser32', '-ladvapi32'], check=True)
    installed_proof = root / 'CWNG-proof.exe'
    shutil.copy2(proof_bootstrap, installed_proof)
    local = tmp_path / 'local-app-data'
    local.mkdir()
    proof_env = dict(env, LOCALAPPDATA=str(local))
    output.unlink(missing_ok=True)
    rejected = subprocess.run([str(installed_proof), '--state', state], env=proof_env, timeout=10)
    assert rejected.returncode != 0
    assert not output.exists()
    subprocess.run([str(installed_proof)], env=proof_env, check=True, timeout=10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if output.exists() and 'LOCALAPPDATA=' in output.read_text(encoding='utf-8-sig'):
            break
        time.sleep(.05)
    assert '--state\n' + str(local / 'CWNG-Installer-Proof') in output.read_text(encoding='utf-8-sig')

    # Check a junction before creating any profile files through its target.
    target = tmp_path / 'external-target'
    junction = tmp_path / 'junction'
    target.mkdir()
    create = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/cmd.exe'),
                             '/c', 'mklink', '/J', str(junction), str(target)],
                            capture_output=True, timeout=10)
    if create.returncode == 0:
        blocked = subprocess.run([str(bootstrap), '--state', str(junction / 'profile')], env=env, timeout=10)
        assert blocked.returncode != 0
        assert not (target / 'profile').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native bootstrap')
def test_real_interpreter_keeps_gate_after_bootstrap_dies(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    compiler = repo / '.m0/mingw64/bin/gcc.exe'
    runtime = repo / '.m0/python'
    packages = repo / '.m0/n1-runtime-locked/Lib/site-packages'
    if not compiler.is_file() or not (runtime / 'pythonw.exe').is_file():
        pytest.skip('private Windows compiler and M0 runtime are required')
    sys.path.insert(0, str(packages))
    try:
        import psutil
    except ImportError:
        pytest.skip('the M0 runtime does not contain psutil')
    finally:
        sys.path.pop(0)
    root = tmp_path / 'installed'
    payload = root / 'versions/1.0'
    runtime_copy = payload / 'runtime'
    runtime_copy.mkdir(parents=True)
    for name in ('pythonw.exe', 'python313.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        shutil.copy2(runtime / name, runtime_copy / name)
    (runtime_copy / 'pyvenv.cfg').write_text('home = ' + str(runtime) + '\n', encoding='utf-8')
    (payload / 'scripts').mkdir()
    (payload / 'scripts/windows_native_launcher.py').write_text(
        'import os,pathlib,time\n'
        'here=pathlib.Path(__file__).resolve().parent.parent\n'
        '(here/"ready.txt").write_text(str(os.getpid()))\n'
        'while not (here/"release.txt").exists(): time.sleep(.05)\n', encoding='utf-8')
    (root / 'active-version.txt').write_text('1.0', encoding='ascii')
    bootstrap = root / 'CWNG.exe'
    subprocess.run([str(compiler), '-std=c11', '-municode', '-mwindows', '-static', '-DCWNG_BOOTSTRAP_TEST',
                    '-o', str(bootstrap), str(repo / 'scripts/windows-native-bootstrap.c'),
                    '-lshell32', '-luser32', '-ladvapi32'], check=True)
    state = tmp_path / 'private'
    process = subprocess.Popen([str(bootstrap), '--state', str(state)],
                               env=dict(os.environ, LOCALAPPDATA=str(tmp_path)))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    lock = str(state / 'bootstrap-maintenance.lock')
    release = payload / 'release.txt'
    try:
        ready = payload / 'ready.txt'
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail('bootstrap exited before the real interpreter became ready')
            time.sleep(.05)
        assert ready.exists(), 'real interpreter did not become ready'
        python_process = psutil.Process(int(ready.read_text()))
        assert python_process.parent().pid == process.pid, 'payload launched a redirector, not the real interpreter'
        process.kill()
        process.wait(timeout=5)
        assert python_process.is_running(), 'real interpreter did not survive launcher termination'
        handle = kernel.CreateFileW(lock, 0xC0000000, 0, None, 3, 0x80, None)
        if handle != invalid:
            kernel.CloseHandle(handle)
        assert handle == invalid and ctypes.get_last_error() == 32, 'live Python lost its maintenance gate'
    finally:
        release.touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if 'python_process' in locals():
            try:
                python_process.wait(timeout=10)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                python_process.kill()
