"""Bounded parser for raw MIDI bytes received from the LF+ USB-serial stream."""

from __future__ import annotations


_DATA_LENGTH = {
    0x8: 2,  # Note Off
    0x9: 2,  # Note On
    0xA: 2,  # Poly Pressure
    0xB: 2,  # Control Change
    0xC: 1,  # Program Change
    0xD: 1,  # Channel Pressure
    0xE: 2,  # Pitch Bend
}

LF_TO_COMPUTER_REALTIME = frozenset((0xF8, 0xFA, 0xFB, 0xFC, 0xFE))

_FAMC_PROTOCOL_PREFIXES = (
    bytes.fromhex("f000007c"),
    bytes.fromhex("f005007c"),
    bytes.fromhex("f009"),
)


def is_famc_protocol_frame(frame: bytes) -> bool:
    """Return whether a complete SysEx frame belongs to the LF+ control protocol."""
    return any(frame.startswith(prefix) for prefix in _FAMC_PROTOCOL_PREFIXES)


def is_forwardable_sysex(frame: bytes) -> bool:
    """Return whether a frame is valid musical SysEx and not LF+ protocol traffic."""
    return (
        len(frame) >= 2
        and frame[0] == 0xF0
        and frame[-1] == 0xF7
        and all(value < 0x80 for value in frame[1:-1])
        and not is_famc_protocol_frame(frame)
    )


class RawMidiStreamParser:
    """Incrementally decode MIDI from fragmented serial byte chunks."""

    def __init__(self, max_sysex_bytes: int = 1024):
        if max_sysex_bytes < 2:
            raise ValueError("max_sysex_bytes must be at least 2")

        self.max_sysex_bytes = max_sysex_bytes
        self.reset()

    def reset(self) -> None:
        self._running_status: int | None = None
        self._pending = bytearray()
        self._in_sysex = False
        self._sysex = bytearray()

    @property
    def buffered_byte_count(self) -> int:
        return len(self._pending) + len(self._sysex)

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[bytes]:
        messages: list[bytes] = []

        for value in bytes(chunk):
            if self._in_sysex:
                if value == 0xF7:
                    if len(self._sysex) < self.max_sysex_bytes:
                        self._sysex.append(value)
                        frame = bytes(self._sysex)

                        if is_forwardable_sysex(frame):
                            messages.append(frame)

                    self._leave_sysex()
                    continue

                if value == 0xF0:
                    self._sysex = bytearray([value])
                    continue

                if len(self._sysex) >= self.max_sysex_bytes - 1:
                    self._leave_sysex()
                else:
                    self._sysex.append(value)

                continue

            if value >= 0xF8:
                if value in LF_TO_COMPUTER_REALTIME:
                    messages.append(bytes([value]))
                continue

            if value == 0xF0:
                self._running_status = None
                self._pending.clear()
                self._in_sysex = True
                self._sysex = bytearray([value])
                continue

            if 0x80 <= value <= 0xEF:
                self._start_channel_status(value)
                continue

            if value >= 0xF1:
                self._running_status = None
                self._pending.clear()
                continue

            if self._running_status is None:
                continue

            self._pending.append(value)
            needed = _DATA_LENGTH[self._running_status >> 4]

            if len(self._pending) == needed:
                messages.append(bytes([self._running_status, *self._pending]))
                self._pending.clear()

        return messages

    def _start_channel_status(self, status: int) -> None:
        self._running_status = status
        self._pending.clear()

    def _leave_sysex(self) -> None:
        self._in_sysex = False
        self._sysex.clear()
        self._running_status = None
        self._pending.clear()
