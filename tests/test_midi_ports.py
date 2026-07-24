from __future__ import annotations

import mido
import pytest

from lf_usb_bridge.midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    MACOS_VIRTUAL_PORT_NAME,
    WINDOWS_INPUT_ENDPOINT_NAME,
    WINDOWS_OUTPUT_ENDPOINT_NAME,
    MidiEndpointAdapter,
    MidiEndpointMode,
    MidiPortError,
    MidiPortOpenError,
    MidiPortSendError,
    bytes_to_message,
    message_to_bytes,
)


class FakeInputPort:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.closed = False
        self.close_count = 0

    def emit(self, message: mido.Message) -> None:
        self.callback(message)

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


class FakeOutputPort:
    def __init__(self) -> None:
        self.closed = False
        self.close_count = 0
        self.messages: list[mido.Message] = []
        self.send_error: Exception | None = None

    def send(self, message: mido.Message) -> None:
        if self.send_error is not None:
            raise self.send_error

        self.messages.append(message.copy())

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


class FakeMidiBackend:
    def __init__(self) -> None:
        self.input_port: FakeInputPort | None = None
        self.output_port = FakeOutputPort()
        self.opened_input_names: list[str] = []
        self.opened_output_names: list[str] = []
        self.input_options: list[dict] = []
        self.output_options: list[dict] = []
        self.input_error: Exception | None = None
        self.output_error: Exception | None = None

    def open_input(self, name: str, callback, **kwargs):
        self.opened_input_names.append(name)
        self.input_options.append(kwargs)

        if self.input_error is not None:
            raise self.input_error

        self.input_port = FakeInputPort(callback)
        return self.input_port

    def open_output(self, name: str, **kwargs):
        self.opened_output_names.append(name)
        self.output_options.append(kwargs)

        if self.output_error is not None:
            raise self.output_error

        return self.output_port


@pytest.fixture(autouse=True)
def fake_physical_midi_inventory(monkeypatch):
    monkeypatch.setattr(
        "lf_usb_bridge.midi_ports.list_input_names",
        lambda: ["Computer to LF+"],
    )
    monkeypatch.setattr(
        "lf_usb_bridge.midi_ports.list_output_names",
        lambda: ["LF+ to Computer"],
    )


def make_adapter(
    backend: FakeMidiBackend,
    received: list[bytes] | None = None,
) -> MidiEndpointAdapter:
    sink = received if received is not None else []

    return MidiEndpointAdapter(
        input_name="Computer to LF+",
        output_name="LF+ to Computer",
        on_input=sink.append,
        open_input=backend.open_input,
        open_output=backend.open_output,
    )


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("90 3c 64"),
        bytes.fromhex("c2 0a"),
        bytes.fromhex("f8"),
        bytes.fromhex("f0 7d 01 02 f7"),
    ],
)
def test_raw_midi_round_trip(data):
    message = bytes_to_message(data)

    assert message_to_bytes(message) == data


def test_empty_message_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        bytes_to_message(b"")


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("90 3c"),
        bytes.fromhex("90 80 00"),
        bytes.fromhex("f0 7d 01"),
    ],
)
def test_invalid_complete_message_is_rejected(data):
    with pytest.raises(ValueError, match="invalid MIDI message"):
        bytes_to_message(data)


def test_empty_endpoint_names_are_rejected():
    backend = FakeMidiBackend()

    with pytest.raises(ValueError, match="input_name"):
        MidiEndpointAdapter(
            input_name="",
            output_name="Output",
            on_input=lambda data: None,
            open_input=backend.open_input,
            open_output=backend.open_output,
        )

    with pytest.raises(ValueError, match="output_name"):
        MidiEndpointAdapter(
            input_name="Input",
            output_name="",
            on_input=lambda data: None,
            open_input=backend.open_input,
            open_output=backend.open_output,
        )


def test_open_creates_both_endpoints():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    adapter.open()

    assert adapter.is_open
    assert backend.opened_output_names == ["LF+ to Computer"]
    assert backend.opened_input_names == ["Computer to LF+"]
    assert backend.output_options == [{}]
    assert backend.input_options == [{}]



def test_platform_endpoint_names_are_kept_separate():
    assert MACOS_VIRTUAL_PORT_NAME == "LF+ USB MIDI"
    assert DEFAULT_VIRTUAL_INPUT_NAME == MACOS_VIRTUAL_PORT_NAME
    assert DEFAULT_VIRTUAL_OUTPUT_NAME == MACOS_VIRTUAL_PORT_NAME
    assert WINDOWS_INPUT_ENDPOINT_NAME == "LF+ IN PORT"
    assert WINDOWS_OUTPUT_ENDPOINT_NAME == "LF+ OUT PORT"


def test_virtual_open_uses_one_device_style_name_for_both_directions():
    backend = FakeMidiBackend()
    adapter = MidiEndpointAdapter(
        on_input=lambda data: None,
        mode=MidiEndpointMode.VIRTUAL,
        open_input=backend.open_input,
        open_output=backend.open_output,
    )

    adapter.open()

    assert backend.opened_input_names == ["LF+ USB MIDI"]
    assert backend.opened_output_names == ["LF+ USB MIDI"]


def test_virtual_open_creates_named_ports_with_virtual_flag():
    backend = FakeMidiBackend()
    adapter = MidiEndpointAdapter(
        on_input=lambda data: None,
        mode=MidiEndpointMode.VIRTUAL,
        open_input=backend.open_input,
        open_output=backend.open_output,
    )

    adapter.open()

    assert adapter.mode is MidiEndpointMode.VIRTUAL
    assert backend.opened_input_names == [DEFAULT_VIRTUAL_INPUT_NAME]
    assert backend.opened_output_names == [DEFAULT_VIRTUAL_OUTPUT_NAME]
    assert backend.input_options == [{"virtual": True}]
    assert backend.output_options == [{"virtual": True}]


def test_open_is_idempotent():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    adapter.open()
    adapter.open()

    assert backend.opened_output_names == ["LF+ to Computer"]
    assert backend.opened_input_names == ["Computer to LF+"]


def test_computer_input_is_forwarded_as_raw_bytes():
    backend = FakeMidiBackend()
    received: list[bytes] = []
    adapter = make_adapter(backend, received)

    adapter.open()
    assert backend.input_port is not None

    backend.input_port.emit(
        mido.Message("control_change", channel=2, control=7, value=100)
    )

    assert received == [bytes.fromhex("b2 07 64")]


def test_send_to_computer_uses_output_port():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    adapter.open()
    adapter.send_to_computer(bytes.fromhex("90 3c 64"))

    assert len(backend.output_port.messages) == 1
    assert backend.output_port.messages[0].bytes() == [0x90, 0x3C, 0x64]


def test_send_requires_open_adapter():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    with pytest.raises(MidiPortError, match="not open"):
        adapter.send_to_computer(bytes.fromhex("90 3c 64"))


def test_output_open_failure_is_wrapped():
    backend = FakeMidiBackend()
    backend.output_error = RuntimeError("output unavailable")
    adapter = make_adapter(backend)

    with pytest.raises(MidiPortOpenError, match="output unavailable"):
        adapter.open()

    assert not adapter.is_open


def test_input_open_failure_closes_output():
    backend = FakeMidiBackend()
    backend.input_error = RuntimeError("input unavailable")
    adapter = make_adapter(backend)

    with pytest.raises(MidiPortOpenError, match="input unavailable"):
        adapter.open()

    assert backend.output_port.closed
    assert backend.output_port.close_count == 1
    assert not adapter.is_open


def test_send_failure_is_wrapped():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    adapter.open()
    backend.output_port.send_error = RuntimeError("send unavailable")

    with pytest.raises(MidiPortSendError, match="send unavailable"):
        adapter.send_to_computer(bytes.fromhex("90 3c 64"))


def test_close_is_idempotent():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    adapter.open()
    assert backend.input_port is not None

    adapter.close()
    adapter.close()

    assert backend.input_port.close_count == 1
    assert backend.output_port.close_count == 1
    assert not adapter.is_open


def test_context_manager_closes_endpoints():
    backend = FakeMidiBackend()
    adapter = make_adapter(backend)

    with adapter as active:
        assert active.is_open

    assert not adapter.is_open
    assert backend.input_port is not None
    assert backend.input_port.closed
    assert backend.output_port.closed

