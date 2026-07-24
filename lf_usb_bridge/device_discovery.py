"""Discovery of candidate Liquid Foot+ serial devices."""

from __future__ import annotations

import re

from dataclasses import dataclass
from pathlib import PurePath
from typing import Iterable

from serial.tools import list_ports

FTDI_VENDOR_ID = 0x0403
REWRITTEN_FTDI_PRODUCT_ID = 0x6015


class DeviceDiscoveryError(RuntimeError):
    """Raised when automatic LF+ serial-device discovery cannot succeed."""


@dataclass(frozen=True)
class SerialDevice:
    port: str
    description: str
    manufacturer: str | None
    product: str | None
    serial_number: str | None
    vid: int | None
    pid: int | None
    location: str | None = None
    hwid: str | None = None


_FTDI_PRODUCT_KEYWORDS = (
    "ft232",
    "ft2232",
    "ft4232",
    "ft-x",
    "ft231x",
)
_LF_METADATA_KEYWORDS = (
    "liquid foot",
    "liquidfoot",
    "lf+",
    "lf plus",
    "famc",
)
_USBSERIAL_IDENTITY = re.compile(r"usbserial[-_.]?([^/]+)$", re.IGNORECASE)


def enumerate_devices() -> list[SerialDevice]:
    """Return all visible serial devices and the USB metadata pyserial exposes."""

    devices: list[SerialDevice] = []

    for port in list_ports.comports():
        devices.append(
            SerialDevice(
                port=port.device,
                description=port.description or "",
                manufacturer=port.manufacturer,
                product=getattr(port, "product", None),
                serial_number=getattr(port, "serial_number", None),
                vid=port.vid,
                pid=port.pid,
                location=getattr(port, "location", None),
                hwid=getattr(port, "hwid", None),
            )
        )

    return devices


def is_ftdi_device(device: SerialDevice) -> bool:
    """Return whether device metadata identifies an FTDI serial adapter."""

    if device.vid == FTDI_VENDOR_ID:
        return True

    manufacturer = (device.manufacturer or "").lower()
    if "ftdi" in manufacturer:
        return True

    product_text = " ".join(
        filter(
            None,
            (
                device.description,
                device.product,
                device.hwid,
                device.port,
            ),
        )
    ).lower()

    return "usbserial" in product_text or any(
        keyword in product_text
        for keyword in _FTDI_PRODUCT_KEYWORDS
    )


def is_rewritten_ftdi_device(device: SerialDevice) -> bool:
    """Return whether USB IDs match the standard identity used after rewriting."""

    return (
        device.vid == FTDI_VENDOR_ID
        and device.pid == REWRITTEN_FTDI_PRODUCT_ID
    )


def has_lf_identity(device: SerialDevice) -> bool:
    """Return whether non-generic metadata identifies a Liquid Foot product."""

    descriptive_text = " ".join(
        filter(
            None,
            (
                device.description,
                device.manufacturer,
                device.product,
            ),
        )
    ).lower()
    if any(keyword in descriptive_text for keyword in _LF_METADATA_KEYWORDS):
        return True

    serial_number = (device.serial_number or "").strip().lower()
    if len(serial_number) >= 3 and serial_number.startswith("lf"):
        return True

    path_match = _USBSERIAL_IDENTITY.search(device.port)
    if path_match is None:
        return False

    path_serial = path_match.group(1).lower()
    return len(path_serial) >= 3 and path_serial.startswith("lf")


def is_compatible_device(device: SerialDevice) -> bool:
    """Return whether available evidence makes this a safe LF+ candidate.

    The standard FTDI 0403:6015 identity is not unique to Liquid Foot hardware,
    so FTDI identity alone is deliberately insufficient for auto-selection.
    """

    return has_lf_identity(device) and (
        is_rewritten_ftdi_device(device)
        or is_ftdi_device(device)
    )


def _normalised_alias(port: str) -> str:
    name = PurePath(port).name.lower()
    for prefix in ("cu.", "tty."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _same_physical_device(left: SerialDevice, right: SerialDevice) -> bool:
    if left.serial_number and right.serial_number:
        if left.serial_number.casefold() == right.serial_number.casefold():
            return True

    if left.location and right.location:
        if left.location.casefold() == right.location.casefold():
            return True

    return _normalised_alias(left.port) == _normalised_alias(right.port)


def _path_preference(device: SerialDevice) -> tuple[int, int, str]:
    path = device.port.lower()
    if path.startswith("/dev/cu.usbserial-"):
        path_rank = 0
    elif path.startswith("/dev/cu."):
        path_rank = 1
    elif "usbserial" in path:
        path_rank = 2
    elif path.startswith("/dev/tty."):
        path_rank = 3
    else:
        path_rank = 4

    metadata_count = sum(
        value not in (None, "")
        for value in (
            device.description,
            device.manufacturer,
            device.product,
            device.serial_number,
            device.vid,
            device.pid,
            device.location,
            device.hwid,
        )
    )
    return path_rank, -metadata_count, path


def _merge_aliases(aliases: list[SerialDevice]) -> SerialDevice:
    preferred = min(aliases, key=_path_preference)

    def first_value(attribute: str):
        preferred_value = getattr(preferred, attribute)
        if preferred_value not in (None, ""):
            return preferred_value
        return next(
            (
                getattr(alias, attribute)
                for alias in aliases
                if getattr(alias, attribute) not in (None, "")
            ),
            preferred_value,
        )

    return SerialDevice(
        port=preferred.port,
        description=first_value("description") or "",
        manufacturer=first_value("manufacturer"),
        product=first_value("product"),
        serial_number=first_value("serial_number"),
        vid=first_value("vid"),
        pid=first_value("pid"),
        location=first_value("location"),
        hwid=first_value("hwid"),
    )


def deduplicate_devices(
    devices: Iterable[SerialDevice],
) -> list[SerialDevice]:
    """Collapse cu/tty and other aliases for the same physical USB device."""

    groups: list[list[SerialDevice]] = []
    for device in devices:
        matching_group = next(
            (
                group
                for group in groups
                if any(
                    _same_physical_device(device, existing)
                    for existing in group
                )
            ),
            None,
        )
        if matching_group is None:
            groups.append([device])
        else:
            matching_group.append(device)

    return [_merge_aliases(group) for group in groups]


def rank_devices(
    devices: Iterable[SerialDevice],
) -> list[SerialDevice]:
    """Return deduplicated devices with compatible LF+ candidates first."""

    return sorted(
        deduplicate_devices(devices),
        key=lambda device: (
            not is_compatible_device(device),
            not is_rewritten_ftdi_device(device),
            not is_ftdi_device(device),
            _path_preference(device),
        ),
    )


def compatible_devices(
    devices: Iterable[SerialDevice] | None = None,
) -> list[SerialDevice]:
    """Return only deduplicated candidates safe for automatic LF+ selection."""

    available = enumerate_devices() if devices is None else devices
    return [
        device
        for device in rank_devices(available)
        if is_compatible_device(device)
    ]


def discover_device(
    devices: Iterable[SerialDevice] | None = None,
) -> SerialDevice:
    """Select the best compatible LF+ device or raise a clear discovery error."""

    candidates = compatible_devices(devices)
    if candidates:
        return candidates[0]

    raise DeviceDiscoveryError(
        "no compatible Liquid Foot+ USB serial device found; "
        "connect the rewritten FTDI device or pass --serial-port explicitly"
    )
