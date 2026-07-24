from __future__ import annotations

import pytest

from lf_usb_bridge.bridge import (
    BridgeController,
    BridgeError,
    BridgeStartError,
    BridgeState,
    BridgeStopError,
)
from lf_usb_bridge.protocol import (
    HANDSHAKE_FRAME,
    INITIALISE_FRAME,
    START_STREAM_FRAME,
    STOP_STREAM_FRAME,
)

IDENTIFICATION_REPLY = bytes.fromhex("f0 05 00 7c f7")

EXPECTED_STARTUP_WRITES = [
    HANDSHAKE_FRAME,
    INITIALISE_FRAME,
    START_STREAM_FRAME,
]


class FakeTransport:
    def __init__(self, incoming: bytes = b"") -> None:
        self._is_open = False
        self.incoming = bytearray(incoming)
        self._pending_reads: list[bytes] = []
        self._streaming = False
        self.writes: list[bytes] = []
        self.open_count = 0
        self.close_count = 0
        self.open_error: Exception | None = None
        self.read_error: Exception | None = None
        self.write_error: Exception | None = None
        self.close_error: Exception | None = None

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self.open_count += 1

        if self.open_error is not None:
            raise self.open_error

        self._is_open = True

    def read_available(self, max_bytes: int = 4096) -> bytes:
        if self.read_error is not None:
            raise self.read_error

        if self._pending_reads:
            return self._pending_reads.pop(0)

        if not self._streaming:
            return b""

        result = bytes(self.incoming[:max_bytes])
        del self.incoming[:max_bytes]
        return result

    def write(self, data) -> None:
        if self.write_error is not None:
            raise self.write_error

        payload = bytes(data)
        self.writes.append(payload)

        if payload == HANDSHAKE_FRAME:
            self._pending_reads.append(IDENTIFICATION_REPLY)
        elif payload == START_STREAM_FRAME:
            self._streaming = True

    def close(self) -> None:
        self.close_count += 1

        if self.close_error is not None:
            raise self.close_error

        self._is_open = False


def test_initial_state_is_stopped():
    controller = BridgeController(FakeTransport(), lambda message: None)

    assert controller.state is BridgeState.STOPPED
    assert not controller.is_running


def test_invalid_read_limit_is_rejected():
    with pytest.raises(ValueError):
        BridgeController(
            FakeTransport(),
            lambda message: None,
            read_limit=0,
        )


def test_start_opens_transport_and_sends_startup_sequence():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()

    assert controller.is_running
    assert transport.open_count == 1
    assert transport.writes == EXPECTED_STARTUP_WRITES


def test_start_is_idempotent_while_running():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    controller.start()

    assert transport.open_count == 1
    assert transport.writes == EXPECTED_STARTUP_WRITES


def test_start_failure_closes_transport_and_sets_failed_state():
    transport = FakeTransport()
    transport.write_error = RuntimeError("startup write failed")
    controller = BridgeController(transport, lambda message: None)

    with pytest.raises(BridgeStartError, match="startup write failed"):
        controller.start()

    assert controller.state is BridgeState.FAILED
    assert transport.close_count == 1
    assert not transport.is_open


def test_poll_does_nothing_when_stopped():
    transport = FakeTransport(bytes.fromhex("90 3c 64"))
    received: list[bytes] = []
    controller = BridgeController(transport, received.append)

    assert controller.poll() == 0
    assert received == []


def test_poll_forwards_complete_messages():
    transport = FakeTransport(
        bytes.fromhex("90 3c 64 b0 07 7f")
    )
    received: list[bytes] = []
    controller = BridgeController(transport, received.append)

    controller.start()
    forwarded = controller.poll()

    assert forwarded == 2
    assert received == [
        bytes.fromhex("90 3c 64"),
        bytes.fromhex("b0 07 7f"),
    ]


def test_poll_preserves_fragmented_message_state():
    transport = FakeTransport(bytes.fromhex("90 3c"))
    received: list[bytes] = []
    controller = BridgeController(transport, received.append)

    controller.start()

    assert controller.poll() == 0

    transport.incoming.extend(bytes.fromhex("64"))

    assert controller.poll() == 1
    assert received == [bytes.fromhex("90 3c 64")]


def test_poll_filters_lf_protocol_sysex():
    transport = FakeTransport(
        bytes.fromhex("f0 00 00 7c 0f 0f c9 00 00 00 00 f7")
    )
    received: list[bytes] = []
    controller = BridgeController(transport, received.append)

    controller.start()

    assert controller.poll() == 0
    assert received == []


def test_send_midi_writes_to_transport():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    controller.send_midi(bytes.fromhex("90 3c 64"))

    assert transport.writes == [
        *EXPECTED_STARTUP_WRITES,
        bytes.fromhex("90 3c 64"),
    ]


def test_empty_midi_message_is_ignored():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    controller.send_midi(b"")

    assert transport.writes == EXPECTED_STARTUP_WRITES


def test_send_midi_requires_running_bridge():
    controller = BridgeController(FakeTransport(), lambda message: None)

    with pytest.raises(BridgeError, match="not running"):
        controller.send_midi(bytes.fromhex("90 3c 64"))


def test_stop_sends_stop_frame_and_closes():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    controller.stop()

    assert controller.state is BridgeState.STOPPED
    assert not controller.is_running
    assert transport.writes == [
        *EXPECTED_STARTUP_WRITES,
        STOP_STREAM_FRAME,
    ]
    assert transport.close_count == 1
    assert not transport.is_open


def test_stop_is_idempotent():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    controller.stop()
    controller.stop()

    assert transport.close_count == 1
    assert transport.writes.count(STOP_STREAM_FRAME) == 1


def test_stop_closes_even_when_stop_frame_write_fails():
    transport = FakeTransport()
    controller = BridgeController(transport, lambda message: None)

    controller.start()
    transport.write_error = RuntimeError("stop write failed")

    with pytest.raises(BridgeStopError, match="stop write failed"):
        controller.stop()

    assert controller.state is BridgeState.STOPPED
    assert transport.close_count == 1


def test_context_manager_runs_complete_lifecycle():
    transport = FakeTransport()

    with BridgeController(transport, lambda message: None) as controller:
        assert controller.is_running

    assert transport.writes == [
        *EXPECTED_STARTUP_WRITES,
        STOP_STREAM_FRAME,
    ]
    assert transport.close_count == 1
