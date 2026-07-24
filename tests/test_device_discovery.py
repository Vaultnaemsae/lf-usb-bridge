from types import SimpleNamespace

import pytest

from lf_usb_bridge.device_discovery import (
    DeviceDiscoveryError,
    compatible_devices,
    deduplicate_devices,
    discover_device,
    enumerate_devices,
    has_lf_identity,
    is_compatible_device,
    is_ftdi_device,
    is_rewritten_ftdi_device,
    rank_devices,
    SerialDevice,
)


def test_ftdi_detection():
    dev = SerialDevice(
        port="/dev/cu.test",
        description="USB Serial Port",
        manufacturer="FTDI",
        product="FT232R",
        serial_number=None,
        vid=0x0403,
        pid=0x6001,
    )

    assert is_ftdi_device(dev)


def test_non_ftdi():
    dev = SerialDevice(
        port="/dev/cu.test",
        description="Bluetooth",
        manufacturer="Apple",
        product=None,
        serial_number=None,
        vid=None,
        pid=None,
    )

    assert not is_ftdi_device(dev)


def test_rank_prefers_ftdi():
    bluetooth = SerialDevice(
        "/dev/a",
        "Bluetooth",
        "Apple",
        None,
        None,
        None,
        None,
    )

    ftdi = SerialDevice(
        "/dev/b",
        "USB Serial",
        "FTDI",
        None,
        None,
        0x0403,
        0x6001,
    )

    ranked = rank_devices([bluetooth, ftdi])

    assert ranked[0] == ftdi


def test_enumeration(monkeypatch):

    ports = [
        SimpleNamespace(
            device="/dev/cu.test",
            description="USB Serial",
            manufacturer="FTDI",
            product="FT232R",
            serial_number="123",
            vid=0x0403,
            pid=0x6001,
        )
    ]

    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: ports,
    )

    result = enumerate_devices()

    assert len(result) == 1
    assert result[0].port == "/dev/cu.test"
    assert result[0].manufacturer == "FTDI"


def test_generic_usb_serial_description_is_not_enough():
    dev = SerialDevice(
        port="/dev/cu.generic",
        description="USB Serial Device",
        manufacturer="Generic",
        product="Serial Adapter",
        serial_number=None,
        vid=0x1234,
        pid=0x5678,
    )

    assert not is_ftdi_device(dev)


def test_ftdi_vendor_id_is_recognised_without_text_metadata():
    dev = SerialDevice(
        port="/dev/cu.ftdi",
        description="Serial Adapter",
        manufacturer=None,
        product=None,
        serial_number=None,
        vid=0x0403,
        pid=0x6001,
    )

    assert is_ftdi_device(dev)


def test_rewritten_lf_identity_is_compatible():
    dev = SerialDevice(
        port="/dev/cu.usbserial-LFXOYDP0",
        description="FTDI USB Serial Port",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="LFXOYDP0",
        vid=0x0403,
        pid=0x6015,
    )

    assert is_rewritten_ftdi_device(dev)
    assert has_lf_identity(dev)
    assert is_compatible_device(dev)


def test_unrelated_rewritten_pid_ftdi_is_not_auto_selected():
    unrelated = SerialDevice(
        port="/dev/cu.usbserial-DK0ABC12",
        description="FTDI USB Serial Port",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="DK0ABC12",
        vid=0x0403,
        pid=0x6015,
    )
    lf_device = SerialDevice(
        port="/dev/cu.usbserial-LF123456",
        description="FTDI USB Serial Port",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="LF123456",
        vid=0x0403,
        pid=0x6015,
    )

    assert not is_compatible_device(unrelated)
    assert compatible_devices([unrelated, lf_device]) == [lf_device]
    assert rank_devices([unrelated, lf_device])[0] == lf_device


def test_discovery_deduplicates_aliases_and_prefers_macos_cu_path():
    tty_alias = SerialDevice(
        port="/dev/tty.usbserial-LF123456",
        description="USB Serial",
        manufacturer=None,
        product=None,
        serial_number="LF123456",
        vid=0x0403,
        pid=0x6015,
        location="1-2",
    )
    cu_alias = SerialDevice(
        port="/dev/cu.usbserial-LF123456",
        description="USB Serial",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="LF123456",
        vid=0x0403,
        pid=0x6015,
        location="1-2",
    )

    result = deduplicate_devices([tty_alias, cu_alias])

    assert len(result) == 1
    assert result[0].port == "/dev/cu.usbserial-LF123456"
    assert result[0].manufacturer == "FTDI"


def test_discover_device_reports_no_compatible_candidate():
    unrelated = SerialDevice(
        port="/dev/cu.usbserial-DK0ABC12",
        description="FTDI USB Serial Port",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="DK0ABC12",
        vid=0x0403,
        pid=0x6015,
    )

    with pytest.raises(DeviceDiscoveryError, match="--serial-port"):
        discover_device([unrelated])


@pytest.mark.parametrize(
    "product",
    [
        "FT232R USB UART",
        "FT2232H Dual HS USB-UART/FIFO",
        "FT4232H Quad HS USB-UART",
    ],
)
def test_known_ftdi_product_names_are_recognised(product):
    dev = SerialDevice(
        port="/dev/cu.ftdi",
        description=product,
        manufacturer=None,
        product=product,
        serial_number=None,
        vid=None,
        pid=None,
    )

    assert is_ftdi_device(dev)
