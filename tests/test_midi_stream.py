import pytest

from lf_usb_bridge.midi_stream import (
    RawMidiStreamParser,
    is_famc_protocol_frame,
    is_forwardable_sysex,
)


def test_complete_channel_message():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0x90, 60, 100])) == [
        bytes([0x90, 60, 100])
    ]


def test_fragmented_channel_message():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0x90, 60])) == []
    assert parser.feed(bytes([100])) == [
        bytes([0x90, 60, 100])
    ]


def test_running_status():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0xB0, 7, 100, 11, 64])) == [
        bytes([0xB0, 7, 100]),
        bytes([0xB0, 11, 64]),
    ]


def test_program_change_running_status():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0xC2, 10, 11, 12])) == [
        bytes([0xC2, 10]),
        bytes([0xC2, 11]),
        bytes([0xC2, 12]),
    ]


def test_realtime_does_not_break_running_status():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0x90, 60, 0xF8, 100])) == [
        bytes([0xF8]),
        bytes([0x90, 60, 100]),
    ]


def test_supported_realtime_messages():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0xF8, 0xFA, 0xFB, 0xFC, 0xFE])) == [
        bytes([0xF8]),
        bytes([0xFA]),
        bytes([0xFB]),
        bytes([0xFC]),
        bytes([0xFE]),
    ]


def test_system_reset_is_filtered():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes([0xFF])) == []


def test_forwardable_sysex():
    parser = RawMidiStreamParser()
    frame = bytes.fromhex("f07d010203f7")

    assert is_forwardable_sysex(frame)
    assert parser.feed(frame) == [frame]


def test_fragmented_sysex():
    parser = RawMidiStreamParser()

    assert parser.feed(bytes.fromhex("f07d01")) == []
    assert parser.feed(bytes.fromhex("0203f7")) == [
        bytes.fromhex("f07d010203f7")
    ]


@pytest.mark.parametrize(
    "frame",
    [
        bytes.fromhex("f000007c0f0fc900000000f7"),
        bytes.fromhex("f005007c0102f7"),
        bytes.fromhex("f00901f7"),
    ],
)
def test_famc_protocol_frames_are_filtered(frame):
    parser = RawMidiStreamParser()

    assert is_famc_protocol_frame(frame)
    assert not is_forwardable_sysex(frame)
    assert parser.feed(frame) == []


def test_malformed_nested_sysex_restarts():
    parser = RawMidiStreamParser()

    result = parser.feed(bytes.fromhex("f07d01f07d02f7"))

    assert result == [bytes.fromhex("f07d02f7")]


def test_sysex_buffer_is_bounded():
    parser = RawMidiStreamParser(max_sysex_bytes=8)

    parser.feed(bytes([0xF0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))

    assert parser.buffered_byte_count <= 8


def test_reset_clears_partial_state():
    parser = RawMidiStreamParser()

    parser.feed(bytes([0x90, 60]))
    assert parser.buffered_byte_count == 1

    parser.reset()

    assert parser.buffered_byte_count == 0
    assert parser.feed(bytes([100])) == []


def test_invalid_sysex_limit():
    with pytest.raises(ValueError):
        RawMidiStreamParser(max_sysex_bytes=1)
