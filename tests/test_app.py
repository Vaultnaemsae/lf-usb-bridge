from __future__ import annotations

from collections.abc import Callable

import pytest

from lf_usb_bridge.app import (
    AppConfig,
    ApplicationError,
    parse_args,
    run_application,
    select_midi_mode,
    select_serial_port,
)
from lf_usb_bridge.bridge import BridgeStartError
from lf_usb_bridge.device_discovery import SerialDevice
from lf_usb_bridge.midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    MidiEndpointMode,
    MidiPortOpenError,
)


class FakeTransport:
    def __init__(self, config, events: list[str]) -> None:
        self.config = config
        self.events = events

    def close(self) -> None:
        self.events.append("transport.close")


class FakeBridge:
    def __init__(
        self,
        transport: FakeTransport,
        midi_output: Callable[[bytes], None],
        events: list[str],
    ) -> None:
        self.transport = transport
        self.midi_output = midi_output
        self.events = events
        self.start_error: Exception | None = None
        self.poll_error: BaseException | None = None
        self.received: list[bytes] = []

    def start(self) -> None:
        self.events.append("bridge.start")
        if self.start_error is not None:
            raise self.start_error

    def poll(self) -> int:
        self.events.append("bridge.poll")
        if self.poll_error is not None:
            raise self.poll_error
        return 0

    def stop(self) -> None:
        self.events.append("bridge.stop")

    def send_midi(self, message: bytes) -> None:
        self.received.append(bytes(message))


class FakeMidiAdapter:
    def __init__(self, events: list[str], **kwargs) -> None:
        self.events = events
        self.input_name = kwargs["input_name"]
        self.output_name = kwargs["output_name"]
        self.mode = kwargs["mode"]
        self.on_input = kwargs["on_input"]
        self.open_error: Exception | None = None
        self.sent: list[bytes] = []

    def open(self) -> None:
        self.events.append("midi.open")
        if self.open_error is not None:
            raise self.open_error

    def send_to_computer(self, message: bytes) -> None:
        self.sent.append(bytes(message))

    def close(self) -> None:
        self.events.append("midi.close")


class Harness:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.transport: FakeTransport | None = None
        self.bridge: FakeBridge | None = None
        self.midi: FakeMidiAdapter | None = None
        self.bridge_start_error: Exception | None = None
        self.bridge_poll_error: BaseException | None = None
        self.midi_open_error: Exception | None = None

    def transport_factory(self, config) -> FakeTransport:
        self.events.append("transport.create")
        self.transport = FakeTransport(config, self.events)
        return self.transport

    def bridge_factory(
        self,
        transport: FakeTransport,
        midi_output: Callable[[bytes], None],
    ) -> FakeBridge:
        self.events.append("bridge.create")
        self.bridge = FakeBridge(transport, midi_output, self.events)
        self.bridge.start_error = self.bridge_start_error
        self.bridge.poll_error = self.bridge_poll_error
        return self.bridge

    def midi_factory(self, **kwargs) -> FakeMidiAdapter:
        self.events.append("midi.create")
        self.midi = FakeMidiAdapter(self.events, **kwargs)
        self.midi.open_error = self.midi_open_error
        return self.midi


def run_with_harness(
    config: AppConfig,
    harness: Harness,
    *,
    system_name: str,
    stop_requested: Callable[[], bool] = lambda: True,
    discover: Callable[[], SerialDevice] | None = None,
) -> None:
    discover_device = discover or (
        lambda: SerialDevice(
            "/dev/cu.usbserial-LF123456",
            "USB Serial",
            "FTDI",
            "FT231X USB UART",
            "LF123456",
            0x0403,
            0x6015,
        )
    )
    run_application(
        config,
        system_name=system_name,
        discover=discover_device,
        transport_factory=harness.transport_factory,
        bridge_factory=harness.bridge_factory,
        midi_factory=harness.midi_factory,
        sleep=lambda interval: harness.events.append(f"sleep:{interval}"),
        stop_requested=stop_requested,
        status=lambda message: None,
    )


@pytest.mark.parametrize(
    ("system_name", "requested", "expected"),
    [
        ("Darwin", None, MidiEndpointMode.VIRTUAL),
        ("Windows", None, MidiEndpointMode.EXISTING),
        ("Linux", None, MidiEndpointMode.EXISTING),
        ("Darwin", MidiEndpointMode.EXISTING, MidiEndpointMode.EXISTING),
        ("Windows", MidiEndpointMode.VIRTUAL, MidiEndpointMode.VIRTUAL),
    ],
)
def test_midi_mode_selection_does_not_require_host_os(
    system_name,
    requested,
    expected,
):
    assert select_midi_mode(system_name, requested) is expected


def test_macos_startup_and_shutdown_ordering():
    harness = Harness()
    stop_values = iter((False, True))

    run_with_harness(
        AppConfig(serial_port="/dev/cu.lf"),
        harness,
        system_name="Darwin",
        stop_requested=lambda: next(stop_values),
    )

    assert harness.midi is not None
    assert harness.midi.mode is MidiEndpointMode.VIRTUAL
    assert harness.midi.input_name == DEFAULT_VIRTUAL_INPUT_NAME
    assert harness.midi.output_name == DEFAULT_VIRTUAL_OUTPUT_NAME
    assert harness.events == [
        "transport.create",
        "bridge.create",
        "midi.create",
        "midi.open",
        "bridge.start",
        "bridge.poll",
        "sleep:0.005",
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_application_connects_both_midi_directions():
    harness = Harness()

    run_with_harness(
        AppConfig(serial_port="/dev/cu.lf"),
        harness,
        system_name="Darwin",
    )

    assert harness.bridge is not None
    assert harness.midi is not None

    harness.midi.on_input(bytes.fromhex("90 3c 64"))
    harness.bridge.midi_output(bytes.fromhex("b0 07 7f"))

    assert harness.bridge.received == [bytes.fromhex("90 3c 64")]
    assert harness.midi.sent == [bytes.fromhex("b0 07 7f")]


def test_windows_uses_named_existing_ports():
    harness = Harness()

    run_with_harness(
        AppConfig(
            serial_port="COM7",
            midi_input="LF+ Send",
            midi_output="LF+ Receive",
        ),
        harness,
        system_name="Windows",
    )

    assert harness.midi is not None
    assert harness.midi.mode is MidiEndpointMode.EXISTING
    assert harness.midi.input_name == "LF+ Send"
    assert harness.midi.output_name == "LF+ Receive"


def test_existing_mode_requires_both_named_ports():
    harness = Harness()

    with pytest.raises(ApplicationError, match="both --midi-input"):
        run_with_harness(
            AppConfig(serial_port="COM7", midi_input="LF+ Send"),
            harness,
            system_name="Windows",
        )

    assert harness.events == []


def test_explicit_serial_port_skips_discovery():
    def fail_discovery() -> SerialDevice:
        raise AssertionError("discovery must not run")

    assert (
        select_serial_port("/dev/cu.explicit", discover=fail_discovery)
        == "/dev/cu.explicit"
    )


def test_discovered_serial_port_is_used():
    harness = Harness()
    discovered = SerialDevice(
        "COM9",
        "Liquid Foot+",
        "FAMC",
        "LF+",
        "LF987654",
        0x0403,
        0x6015,
    )

    run_with_harness(
        AppConfig(
            midi_input="LF+ Send",
            midi_output="LF+ Receive",
        ),
        harness,
        system_name="Windows",
        discover=lambda: discovered,
    )

    assert harness.transport is not None
    assert harness.transport.config.port == "COM9"


def test_ctrl_c_still_runs_complete_cleanup():
    harness = Harness()
    harness.bridge_poll_error = KeyboardInterrupt()

    run_with_harness(
        AppConfig(serial_port="/dev/cu.lf"),
        harness,
        system_name="Darwin",
        stop_requested=lambda: False,
    )

    assert harness.events[-3:] == [
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_bridge_start_failure_closes_every_resource():
    harness = Harness()
    harness.bridge_start_error = BridgeStartError("handshake failed")

    with pytest.raises(BridgeStartError, match="handshake failed"):
        run_with_harness(
            AppConfig(serial_port="/dev/cu.lf"),
            harness,
            system_name="Darwin",
        )

    assert harness.events[-4:] == [
        "bridge.start",
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_midi_open_failure_closes_every_resource():
    harness = Harness()
    harness.midi_open_error = MidiPortOpenError("MIDI unavailable")

    with pytest.raises(MidiPortOpenError, match="MIDI unavailable"):
        run_with_harness(
            AppConfig(serial_port="/dev/cu.lf"),
            harness,
            system_name="Darwin",
        )

    assert "bridge.start" not in harness.events
    assert harness.events[-4:] == [
        "midi.open",
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_argument_parser_supports_explicit_existing_configuration():
    config = parse_args(
        [
            "--serial-port",
            "COM4",
            "--midi-mode",
            "existing",
            "--midi-input",
            "LF Send",
            "--midi-output",
            "LF Receive",
        ]
    )

    assert config == AppConfig(
        serial_port="COM4",
        midi_mode=MidiEndpointMode.EXISTING,
        midi_input="LF Send",
        midi_output="LF Receive",
    )
