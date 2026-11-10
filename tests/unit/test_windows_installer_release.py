"""The setup theme must reach both release layouts through the real installer."""
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher build")]
ROOT = Path(__file__).resolve().parents[2]
OVERRIDE = "cps/static/css/caliBlur_override.css"
STYLE = "cps/static/css/library-setup-panels.css"


def _run(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_release_contains_readable_setup_panel_styles(tmp_path):
    system = Path(os.environ["SystemRoot"])
    compiler = system / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    powershell = system / "System32/WindowsPowerShell/v1.0/powershell.exe"
    _run(powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         ROOT / "scripts/windows-build-launcher.ps1", "-OutputPath", tmp_path / "CWNG.exe")

    # The original release embedded its source ZIP in the EXE. A minimal
    # fixture drives that same extraction path without relying on GitHub.
    fixture_zip = tmp_path / "legacy.zip"
    original_css = "/* legacy theme */\n"
    with ZipFile(fixture_zip, "w") as archive:
        archive.writestr(OVERRIDE, original_css)
        archive.writestr("scripts/windows_tray_control.py", "")
    stub = tmp_path / "Legacy.cs"
    stub.write_text("public sealed class Legacy {}", encoding="utf-8")
    legacy = tmp_path / "legacy.dll"
    _run(compiler, "/nologo", "/target:library", f"/out:{legacy}", f"/resource:{fixture_zip},CWNG.Source", stub)

    harness = tmp_path / "Extract.cs"
    harness.write_text('''
using System;
using System.IO;
using System.Reflection;
static class Extract
{
    static void Main(string[] args)
    {
        Directory.CreateDirectory(args[2]);
        var method = Assembly.LoadFrom(args[0]).GetType("WindowsInstaller").GetMethod(
            "ExtractRelease", BindingFlags.Static | BindingFlags.NonPublic);
        method.Invoke(null, new object[] { args[1], args[2], Boolean.Parse(args[3]), args[4] });
    }
}
''', encoding="utf-8")
    extractor = tmp_path / "extract.exe"
    _run(compiler, "/nologo", "/target:exe", "/platform:x64", f"/out:{extractor}", harness)
    installer = tmp_path / "cwng-installer.exe"

    modern = tmp_path / "modern-installed"
    _run(extractor, installer, tmp_path / "cwng-windows.zip", modern, "true", "windows-v4.1.62-r2")
    style = (ROOT / STYLE).read_text(encoding="utf-8").strip()
    assert (modern / OVERRIDE).read_text(encoding="utf-8").startswith('@import url("library-setup-panels.css");')
    assert (modern / STYLE).read_text(encoding="utf-8").strip() == style

    old = tmp_path / "legacy-installed"
    _run(extractor, installer, legacy, old, "false", "windows-v4.1.62")
    installed = (old / OVERRIDE).read_text(encoding="utf-8")
    assert installed.startswith(original_css)
    assert installed.strip().endswith(style)
