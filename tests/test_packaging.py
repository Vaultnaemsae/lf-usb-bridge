from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import importlib.util

from pathlib import Path

from lf_usb_bridge.gui import main as gui_main


ROOT = Path(__file__).resolve().parents[1]


def test_cli_module_entry_point_is_preserved():
    result = subprocess.run(
        [sys.executable, "-m", "lf_usb_bridge", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "lf-usb-bridge" in result.stdout
    assert "--serial-port" in result.stdout


def test_gui_module_entry_point_has_help():
    result = subprocess.run(
        [sys.executable, "-m", "lf_usb_bridge.gui", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "lf-usb-bridge-gui" in result.stdout
    assert "--smoke-test" in result.stdout


def test_packaged_entry_point_uses_the_same_gui_lifecycle():
    entry_path = ROOT / "scripts" / "lf_usb_bridge_gui.py"
    spec = importlib.util.spec_from_file_location(
        "lf_usb_bridge_packaged_entry_test",
        entry_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main is gui_main


def test_macos_packaging_script_is_reproducible_and_windowed():
    script = ROOT / "scripts" / "build_macos_app.sh"
    contents = script.read_text()

    assert os.access(script, os.X_OK)
    assert '.venv/bin/python -m PyInstaller' in contents
    assert '--windowed' in contents
    assert '--name "LF+ USB Bridge"' in contents
    assert '--specpath "$ROOT_DIR/build/pyinstaller"' in contents
    assert '--hidden-import "mido.backends.rtmidi"' in contents
    assert '--collect-all "rtmidi"' in contents
    assert 'if [[ -n "${DEVELOPER_ID_APPLICATION:-}" ]]; then' in contents
    assert '--codesign-identity "$DEVELOPER_ID_APPLICATION"' in contents
    assert "\ncodesign " not in contents
    assert "notary" not in contents.lower()


def test_pyinstaller_is_an_optional_packaging_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packaging = project["project"]["optional-dependencies"]["packaging"]

    assert any(requirement.startswith("pyinstaller") for requirement in packaging)
