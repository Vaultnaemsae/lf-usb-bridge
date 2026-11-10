# -*- mode: python ; coding: utf-8 -*-
"""Build the Windows x64 standalone application on Windows."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

if sys.platform != "win32":
    raise RuntimeError("Build this spec with Windows x64 Python.")

root = Path(SPECPATH).parent
rtmidi_datas, rtmidi_binaries, rtmidi_imports = collect_all("rtmidi")
a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=rtmidi_binaries,
    datas=rtmidi_datas + [
        (str(root / "assets/app-icon/LF_USB_Bridge_1024.png"), "assets/app-icon"),
    ],
    hiddenimports=[
        "mido.backends.rtmidi", "serial.tools.list_ports",
        "serial.tools.list_ports_windows",
    ] + rtmidi_imports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True, name="LF+ USB Bridge", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False,
    icon=str(root / "assets/app-icon/LF_USB_Bridge.ico"),
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, name="LF+ USB Bridge",
)
