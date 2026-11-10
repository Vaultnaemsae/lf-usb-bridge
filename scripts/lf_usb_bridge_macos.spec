# -*- mode: python ; coding: utf-8 -*-
"""Build the macOS menu bar app, applying bundle metadata before signing."""

import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
rtmidi_datas, rtmidi_binaries, rtmidi_imports = collect_all("rtmidi")

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=rtmidi_binaries,
    datas=rtmidi_datas + [
        (str(root / "assets/app-icon/LF_USB_Bridge_1024.png"), "assets/app-icon"),
    ],
    hiddenimports=["mido.backends.rtmidi", "serial.tools.list_ports"] + rtmidi_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LF+ USB Bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=os.environ.get("DEVELOPER_ID_APPLICATION") or None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LF+ USB Bridge",
)
app = BUNDLE(
    coll,
    name="LF+ USB Bridge.app",
    icon=str(root / "assets/app-icon/LF_USB_Bridge.icns"),
    bundle_identifier="com.vaultnaemsae.lfusbbridge",
    info_plist={
        "LSUIElement": True,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
    },
)
