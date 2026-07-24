import pytest

from lf_usb_bridge.protocol import (
    BridgeCommand,
    HANDSHAKE_FRAME,
    INITIALISE_FRAME,
    START_STREAM_FRAME,
    STARTUP_SEQUENCE,
    STOP_STREAM_FRAME,
    build_command,
    is_bridge_command_frame,
)


def test_handshake_frame():
    assert HANDSHAKE_FRAME == bytes.fromhex(
        "f0 00 00 7c 0f 0f c9 00 00 00 00 f7"
    )


def test_initialise_frame():
    assert INITIALISE_FRAME == bytes.fromhex(
        "f0 00 00 7c 0f 0f ca f7"
    )


def test_start_stream_frame():
    assert START_STREAM_FRAME == bytes.fromhex(
        "f0 00 00 7c 0f 0f cf f7"
    )


def test_stop_stream_frame():
    assert STOP_STREAM_FRAME == bytes.fromhex(
        "f0 00 00 7c 0f 0f cc f7"
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            BridgeCommand.HANDSHAKE,
            bytes.fromhex("f0 00 00 7c 0f 0f c9 00 00 00 00 f7"),
        ),
        (
            BridgeCommand.INITIALISE,
            bytes.fromhex("f0 00 00 7c 0f 0f ca f7"),
        ),
        (
            BridgeCommand.START_STREAM,
            bytes.fromhex("f0 00 00 7c 0f 0f cf f7"),
        ),
        (
            BridgeCommand.STOP_STREAM,
            bytes.fromhex("f0 00 00 7c 0f 0f cc f7"),
        ),
    ],
)
def test_build_command(command, expected):
    assert build_command(command) == expected


def test_startup_sequence_order():
    assert STARTUP_SEQUENCE == (
        HANDSHAKE_FRAME,
        INITIALISE_FRAME,
        START_STREAM_FRAME,
    )


@pytest.mark.parametrize(
    "frame",
    [
        HANDSHAKE_FRAME,
        INITIALISE_FRAME,
        START_STREAM_FRAME,
        STOP_STREAM_FRAME,
    ],
)
def test_known_bridge_frames_are_recognised(frame):
    assert is_bridge_command_frame(frame)


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        bytes.fromhex("f0 00 00 7c 0f 0f cb f7"),
        bytes.fromhex("f0 00 00 7c 0f 0f cf 00 f7"),
        bytes.fromhex("90 3c 64"),
    ],
)
def test_unknown_frames_are_rejected(frame):
    assert not is_bridge_command_frame(frame)


def test_build_command_rejects_invalid_type():
    with pytest.raises(TypeError):
        build_command(0xCF)
