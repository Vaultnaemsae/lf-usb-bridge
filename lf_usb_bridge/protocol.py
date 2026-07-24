"""Liquid Foot+ USB MIDI bridge protocol frames."""

from __future__ import annotations

from enum import Enum


FAMC_MANUFACTURER_ID = bytes((0x00, 0x00, 0x7C))
LF_DEVICE_ID = bytes((0x0F, 0x0F))


class BridgeCommand(Enum):
    """Commands used to open and close the LF+ live USB MIDI stream."""

    HANDSHAKE = 0xC9
    INITIALISE = 0xCA
    START_STREAM = 0xCF
    STOP_STREAM = 0xCC


def build_command(command: BridgeCommand) -> bytes:
    """Build the exact SysEx frame for one LF+ bridge command."""
    if not isinstance(command, BridgeCommand):
        raise TypeError("command must be a BridgeCommand")

    payload = bytearray(
        (
            0xF0,
            *FAMC_MANUFACTURER_ID,
            *LF_DEVICE_ID,
            command.value,
        )
    )

    if command is BridgeCommand.HANDSHAKE:
        payload.extend((0x00, 0x00, 0x00, 0x00))

    payload.append(0xF7)
    return bytes(payload)


HANDSHAKE_FRAME = build_command(BridgeCommand.HANDSHAKE)
INITIALISE_FRAME = build_command(BridgeCommand.INITIALISE)
START_STREAM_FRAME = build_command(BridgeCommand.START_STREAM)
STOP_STREAM_FRAME = build_command(BridgeCommand.STOP_STREAM)


STARTUP_SEQUENCE = (
    HANDSHAKE_FRAME,
    INITIALISE_FRAME,
    START_STREAM_FRAME,
)


def is_identification_reply(data: bytes | bytearray | memoryview) -> bool:
    """Return whether data contains a complete LF+ identification SysEx reply."""
    payload = bytes(data)
    start = payload.find(b"\xF0\x05\x00\x7C")
    if start < 0:
        return False
    return payload.find(b"\xF7", start + 4) >= 0


def is_bridge_command_frame(data: bytes | bytearray | memoryview) -> bool:
    """Return whether data is one of the four known LF+ bridge command frames."""
    frame = bytes(data)

    return frame in {
        HANDSHAKE_FRAME,
        INITIALISE_FRAME,
        START_STREAM_FRAME,
        STOP_STREAM_FRAME,
    }
