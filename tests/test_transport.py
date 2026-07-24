from __future__ import annotations

import pytest
import serial

from lf_usb_bridge.transport import (
    SerialConfig,
    SerialTransport,
    TransportError,
    TransportOpenError,
    TransportReadError,
    TransportWriteError,
)


class FakeSerial:
    def __init__(
        self,
        incoming: bytes = b"",
        *,
        is_open: bool = True,
        short_write: int | None = None,
    ) -> None:
        self._incoming = bytearray(incoming)
        self._is_open = is_open
        self.short_write = short_write
        self.writes: list[bytes] = []
        self.close_count = 0
        self.read_error: Exception | None = None
        self.write_error: Exception | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def in_waiting(self) -> int:
        if self.read_error is not None:
            raise self.read_error
        return len(self._incoming)

    def read(self, size: int = 1) -> bytes:
        if self.read_error is not None:
            raise self.read_error

        result = bytes(self._incoming[:size])
        del self._incoming[:size]
        return result

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error

        payload = bytes(data)
        self.writes.append(payload)

        if self.short_write is not None:
            return self.short_write

        return len(payload)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.close_count += 1
        self._is_open = False


def test_serial_config_defaults():
    config = SerialConfig(port="/dev/test")

    assert config.port == "/dev/test"
    assert config.baudrate == 230400
    assert config.timeout == 0.2
    assert config.write_timeout == 1.0


def test_empty_port_is_rejected():
    with pytest.raises(ValueError):
        SerialTransport(SerialConfig(port=""))


def test_injected_open_serial_is_available():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    assert transport.is_open


def test_open_is_idempotent_for_open_injected_serial():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    transport.open()
    transport.open()

    assert transport.is_open


def test_closed_injected_serial_cannot_be_reopened():
    fake = FakeSerial(is_open=False)
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with pytest.raises(TransportOpenError):
        transport.open()


def test_read_available_returns_waiting_bytes():
    fake = FakeSerial(bytes.fromhex("90 3c 64"))
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    assert transport.read_available() == bytes.fromhex("90 3c 64")
    assert transport.read_available() == b""


def test_read_available_honours_maximum():
    fake = FakeSerial(b"abcdef")
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    assert transport.read_available(max_bytes=3) == b"abc"
    assert transport.read_available(max_bytes=3) == b"def"


def test_invalid_read_limit_is_rejected():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with pytest.raises(ValueError):
        transport.read_available(max_bytes=0)


def test_write_sends_complete_payload():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )
    payload = bytes.fromhex("f0 00 00 7c 0f 0f cf f7")

    transport.write(payload)

    assert fake.writes == [payload]


def test_empty_write_is_ignored():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    transport.write(b"")

    assert fake.writes == []


def test_short_write_raises():
    fake = FakeSerial(short_write=2)
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with pytest.raises(TransportWriteError, match="incomplete serial write"):
        transport.write(b"abcd")


def test_read_error_is_wrapped():
    fake = FakeSerial()
    fake.read_error = serial.SerialException("read exploded")
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with pytest.raises(TransportReadError, match="read exploded"):
        transport.read_available()


def test_write_error_is_wrapped():
    fake = FakeSerial()
    fake.write_error = serial.SerialException("write exploded")
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with pytest.raises(TransportWriteError, match="write exploded"):
        transport.write(b"abc")


def test_read_requires_open_transport():
    transport = SerialTransport(SerialConfig(port="/dev/test"))

    with pytest.raises(TransportError, match="not open"):
        transport.read_available()


def test_nonempty_write_requires_open_transport():
    transport = SerialTransport(SerialConfig(port="/dev/test"))

    with pytest.raises(TransportError, match="not open"):
        transport.write(b"abc")


def test_close_is_idempotent():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    transport.close()
    transport.close()

    assert fake.close_count == 1
    assert not transport.is_open


def test_context_manager_closes_transport():
    fake = FakeSerial()
    transport = SerialTransport(
        SerialConfig(port="/dev/test"),
        serial_instance=fake,
    )

    with transport as active:
        assert active.is_open

    assert not transport.is_open
    assert fake.close_count == 1


def test_real_open_failure_is_wrapped(monkeypatch):
    def fail_serial(**kwargs):
        raise serial.SerialException("device unavailable")

    monkeypatch.setattr(serial, "Serial", fail_serial)

    transport = SerialTransport(
        SerialConfig(port="/dev/nonexistent")
    )

    with pytest.raises(TransportOpenError, match="device unavailable"):
        transport.open()

    assert not transport.is_open
