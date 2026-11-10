"""Verify the target architecture, bundled discovery code, and GUI startup."""

import argparse
import json
import struct
import subprocess
import sys
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def verify(bundle: Path) -> dict:
    executable = bundle / "LF+ USB Bridge.exe"
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError("Missing Windows executable signature")
        stream.seek(0x3C)
        stream.seek(struct.unpack("<I", stream.read(4))[0])
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError("Missing PE header")
        machine = struct.unpack("<H", stream.read(2))[0]
    if machine != 0x8664:
        raise RuntimeError(f"Expected x64 executable; PE machine is {machine:#x}")
    archive = CArchiveReader(str(executable))
    embedded = next(name for name in archive.toc if name.startswith("PYZ"))
    pyz = archive.open_embedded_archive(embedded)
    modules = pyz.toc
    required = [
        "lf_usb_bridge.device_discovery", "lf_usb_bridge.serial_devices_dialog",
        "lf_usb_bridge.gui", "serial.tools.list_ports_windows", "mido.backends.rtmidi",
    ]
    missing = [name for name in required if name not in modules]
    if missing:
        raise RuntimeError(f"Missing bundled modules: {missing}")
    # Exercise the code actually embedded in the executable, not an installed
    # source checkout. These records never enumerate or open a real serial port.
    module = types.ModuleType("_verified_lf_discovery")
    sys.modules[module.__name__] = module
    try:
        exec(pyz.extract("lf_usb_bridge.device_discovery"), module.__dict__)
        scenarios = [
            (0x87C0, None, True),
            (0x6015, None, False),
            (0x6015, "LF123456", True),
            (0x87C1, "LF123456", False),
        ]
        for pid, serial, expected in scenarios:
            device = module.SerialDevice(
                "COM7", "USB Serial Port", "FTDI", None, serial, 0x0403, pid,
            )
            if module.is_compatible_device(device) != expected:
                raise RuntimeError(f"Bundled discovery regression for PID {pid:#x}")
    finally:
        del sys.modules[module.__name__]
    if not (bundle / "_internal/assets/app-icon/LF_USB_Bridge_1024.png").is_file():
        raise RuntimeError("Missing GUI icon")
    result = subprocess.run(
        [str(executable), "--smoke-test"], capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        raise RuntimeError(f"Packaged GUI failed: {result.returncode}\n{result.stderr}")
    return {
        "architecture": "x64", "required_modules": required,
        "bundled_discovery_scenarios": len(scenarios), "gui_smoke_exit": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle), indent=2))
