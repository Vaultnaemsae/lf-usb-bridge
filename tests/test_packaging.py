from __future__ import annotations

import os
import subprocess
import sys
import tomllib
import importlib.util
import runpy
from types import SimpleNamespace
import pytest

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


def test_macos_packaging_script_uses_checked_in_spec():
    script = ROOT / "scripts" / "build_macos_app.sh"
    contents = script.read_text()

    assert os.access(script, os.X_OK)
    assert '.venv/bin/python -m PyInstaller' in contents
    assert 'SPEC_FILE="$ROOT_DIR/scripts/lf_usb_bridge_macos.spec"' in contents
    assert '"$SPEC_FILE"' in contents
    assert 'BUILD_DIR="$ROOT_DIR/build/macos"' in contents
    assert '--workpath "$BUILD_DIR"' in contents
    assert "\ncodesign " not in contents
    assert "notary" not in contents.lower()


def test_macos_bundle_is_menu_bar_only_before_signing(monkeypatch):
    pytest.importorskip("PyInstaller.utils.hooks")
    calls = {}

    def capture(name):
        def build(*args, **kwargs):
            calls[name] = kwargs
            return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        return build

    monkeypatch.setenv("DEVELOPER_ID_APPLICATION", "test signing identity")
    monkeypatch.setattr(
        "PyInstaller.utils.hooks.collect_all",
        lambda name: ([('rtmidi-data', '.')], [('rtmidi-binary', '.')], ['rtmidi']),
    )
    runpy.run_path(
        str(ROOT / "scripts/lf_usb_bridge_macos.spec"),
        init_globals={
            "SPECPATH": str(ROOT / "scripts"),
            **{name: capture(name) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")},
        },
    )

    assert calls["EXE"]["console"] is False
    assert calls["EXE"]["codesign_identity"] == "test signing identity"
    assert calls["BUNDLE"]["info_plist"]["LSUIElement"] is True
    assert calls["BUNDLE"]["info_plist"]["CFBundleShortVersionString"] == "1.0.1"
    assert calls["BUNDLE"]["info_plist"]["CFBundleVersion"] == "1.0.1"
    assert calls["BUNDLE"]["bundle_identifier"] == "com.vaultnaemsae.lfusbbridge"
    assert Path(calls["BUNDLE"]["icon"]).is_file()
    assert "mido.backends.rtmidi" in calls["Analysis"]["hiddenimports"]
    assert "serial.tools.list_ports" in calls["Analysis"]["hiddenimports"]
    assert "rtmidi" in calls["Analysis"]["hiddenimports"]
    assert any(Path(src).name == "LF_USB_Bridge_1024.png" and Path(src).is_file()
               for src, _ in calls["Analysis"]["datas"])


def test_pyinstaller_is_an_optional_packaging_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packaging = project["project"]["optional-dependencies"]["packaging"]

    assert any(requirement.startswith("pyinstaller") for requirement in packaging)
