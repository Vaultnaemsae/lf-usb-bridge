"""Lifecycle controller for the Liquid Foot+ USB MIDI bridge."""

from __future__ import annotations

import time

from collections.abc import Callable
from enum import Enum, auto
from typing import Protocol

from .midi_stream import RawMidiStreamParser
from .protocol import (
    STOP_STREAM_FRAME,
    HANDSHAKE_FRAME,
    INITIALISE_FRAME,
    START_STREAM_FRAME,
    is_identification_reply,
)

class BridgeError(RuntimeError):
    """Base error for bridge lifecycle failures."""


class BridgeStartError(BridgeError):
    """Raised when bridge startup cannot be completed."""


class BridgeStopError(BridgeError):
    """Raised when bridge shutdown cannot be completed cleanly."""


class BridgeState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    FAILED = auto()


class TransportLike(Protocol):
    @property
    def is_open(self) -> bool:
        ...

    def open(self) -> None:
        ...

    def read_available(self, max_bytes: int = 4096) -> bytes:
        ...

    def write(self, data: bytes | bytearray | memoryview) -> None:
        ...

    def close(self) -> None:
        ...


MidiOutputCallback = Callable[[bytes], None]


class BridgeController:
    """Compose serial transport, LF+ protocol, and MIDI stream parsing."""

    def __init__(
        self,
        transport: TransportLike,
        midi_output: MidiOutputCallback,
        *,
        parser: RawMidiStreamParser | None = None,
        read_limit: int = 4096,
    ) -> None:
        if read_limit < 1:
            raise ValueError("read_limit must be at least 1")

        self._transport = transport
        self._midi_output = midi_output
        self._parser = parser or RawMidiStreamParser()
        self._read_limit = read_limit
        self._state = BridgeState.STOPPED

    @property
    def state(self) -> BridgeState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is BridgeState.RUNNING

    def start(self) -> None:
        """Open the serial connection and start the LF+ live MIDI stream."""
        if self._state is BridgeState.RUNNING:
            return

        if self._state not in (BridgeState.STOPPED, BridgeState.FAILED):
            raise BridgeStartError(
                f"cannot start bridge while state is {self._state.name}"
            )

        self._state = BridgeState.STARTING
        self._parser.reset()

        try:
            self._transport.open()

            reply = b""
            for attempt in range(6):
                self._transport.write(HANDSHAKE_FRAME)
                reply = self._drain_serial_input(idle_timeout=0.4, overall_timeout=1.2)
                if reply:
                    self._transport.write(INITIALISE_FRAME)
                    self._drain_serial_input(idle_timeout=0.3, overall_timeout=0.6)
                    break
                if attempt < 5:
                    time.sleep(1.5)

            if not reply or not is_identification_reply(reply):
                try:
                    self._transport.write(STOP_STREAM_FRAME)
                    time.sleep(0.2)
                finally:
                    self._transport.close()
                raise RuntimeError("LF+ identification handshake failed")

            self._transport.write(START_STREAM_FRAME)

        except Exception as exc:
            self._state = BridgeState.FAILED

            try:
                self._transport.close()
            except Exception:
                pass

            raise BridgeStartError(f"bridge startup failed: {exc}") from exc

        self._state = BridgeState.RUNNING

    def _drain_serial_input(
        self,
        *,
        idle_timeout: float,
        overall_timeout: float,
    ) -> bytes:
        buffer = bytearray()
        started = time.monotonic()
        last_rx = started

        while time.monotonic() - started < overall_timeout:
            chunk = self._transport.read_available(self._read_limit)
            if chunk:
                buffer.extend(chunk)
                last_rx = time.monotonic()
            elif buffer and time.monotonic() - last_rx >= idle_timeout:
                break
            else:
                time.sleep(0.01)

        return bytes(buffer)

    def poll(self) -> int:
        """Process one available serial chunk.

        Returns the number of complete MIDI messages forwarded to the computer.
        """
        if self._state is not BridgeState.RUNNING:
            return 0

        chunk = self._transport.read_available(self._read_limit)
        if not chunk:
            return 0

        messages = self._parser.feed(chunk)

        for message in messages:
            self._midi_output(message)

        return len(messages)

    def send_midi(self, message: bytes | bytearray | memoryview) -> None:
        """Forward one computer MIDI message to the LF+ serial stream."""
        if self._state is not BridgeState.RUNNING:
            raise BridgeError("bridge is not running")

        payload = bytes(message)
        if not payload:
            return

        self._transport.write(payload)

    def stop(self) -> None:
        """Stop the LF+ stream and close the serial connection."""
        if self._state is BridgeState.STOPPED:
            return

        if self._state is BridgeState.STOPPING:
            return

        self._state = BridgeState.STOPPING
        stop_error: Exception | None = None

        try:
            if self._transport.is_open:
                self._transport.write(STOP_STREAM_FRAME)
                time.sleep(0.2)
        except Exception as exc:
            stop_error = exc

        try:
            self._transport.close()
        except Exception as exc:
            if stop_error is None:
                stop_error = exc

        self._parser.reset()
        self._state = BridgeState.STOPPED

        if stop_error is not None:
            raise BridgeStopError(
                f"bridge shutdown failed: {stop_error}"
            ) from stop_error

    def __enter__(self) -> "BridgeController":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
