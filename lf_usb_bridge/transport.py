"""Serial transport for the Liquid Foot+ USB MIDI bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import time

import serial


class TransportError(RuntimeError):
    """Base error for serial transport failures."""


class TransportOpenError(TransportError):
    """Raised when the serial port cannot be opened."""


class TransportReadError(TransportError):
    """Raised when reading from the serial port fails."""


class TransportWriteError(TransportError):
    """Raised when writing to the serial port fails."""


@runtime_checkable
class SerialLike(Protocol):
    """Minimum serial-port interface required by SerialTransport."""

    @property
    def is_open(self) -> bool:
        ...

    @property
    def in_waiting(self) -> int:
        ...

    def read(self, size: int = 1) -> bytes:
        ...

    def write(self, data: bytes) -> int:
        ...

    def flush(self) -> None:
        ...

    def reset_input_buffer(self) -> None:
        ...

    @property
    def dtr(self) -> bool:
        ...

    @dtr.setter
    def dtr(self, value: bool) -> None:
        ...

    @property
    def rts(self) -> bool:
        ...

    @rts.setter
    def rts(self, value: bool) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class SerialConfig:
    """Configuration used to open the LF+ FTDI serial connection."""

    port: str
    baudrate: int = 230400
    timeout: float = 0.2
    write_timeout: float = 1.0


class SerialTransport:
    """Own and operate one LF+ serial connection."""

    def __init__(
        self,
        config: SerialConfig,
        *,
        serial_instance: SerialLike | None = None,
    ) -> None:
        if not config.port:
            raise ValueError("config.port must not be empty")

        self._config = config
        self._serial = serial_instance

    @property
    def config(self) -> SerialConfig:
        return self._config

    @property
    def is_open(self) -> bool:
        return self._serial is not None and bool(self._serial.is_open)

    def open(self) -> None:
        """Open the configured serial port.

        Calling open on an already-open transport has no effect.
        """
        if self.is_open:
            return

        if self._serial is not None:
            raise TransportOpenError(
                "injected serial instance is closed and cannot be reopened"
            )

        try:
            self._serial = serial.Serial(
                port=self._config.port,
                baudrate=self._config.baudrate,
                timeout=self._config.timeout,
                write_timeout=self._config.write_timeout,
            )
            self._serial.dtr = True
            self._serial.rts = True
            time.sleep(0.3)
            self._serial.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            self._serial = None
            raise TransportOpenError(
                f"could not open serial port {self._config.port!r}: {exc}"
            ) from exc

    def read_available(self, max_bytes: int = 4096) -> bytes:
        """Read up to max_bytes currently waiting on the serial port."""
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")

        port = self._require_open()

        try:
            waiting = int(port.in_waiting)
            if waiting <= 0:
                return b""

            return bytes(port.read(min(waiting, max_bytes)))
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportReadError(f"serial read failed: {exc}") from exc

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Write one complete byte sequence to the serial port."""
        payload = bytes(data)
        if not payload:
            return

        port = self._require_open()

        try:
            written = int(port.write(payload))
            port.flush()
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportWriteError(f"serial write failed: {exc}") from exc

        if written != len(payload):
            raise TransportWriteError(
                f"incomplete serial write: wrote {written} of {len(payload)} bytes"
            )

    def close(self) -> None:
        """Close the serial connection. Safe to call repeatedly."""
        port = self._serial
        if port is None:
            return

        try:
            if port.is_open:
                port.close()
        finally:
            self._serial = None

    def _require_open(self) -> SerialLike:
        if not self.is_open or self._serial is None:
            raise TransportError("serial transport is not open")

        return self._serial

    def __enter__(self) -> "SerialTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
