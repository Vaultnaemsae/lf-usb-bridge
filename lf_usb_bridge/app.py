"""Command-line application for the Liquid Foot+ USB MIDI bridge."""

from __future__ import annotations

import argparse
import platform
import signal
import sys
import threading
import time

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .bridge import BridgeController, BridgeError
from .device_discovery import (
    DeviceDiscoveryError,
    SerialDevice,
    discover_device,
)
from .midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    MidiEndpointAdapter,
    MidiEndpointMode,
    MidiPortError,
)
from .transport import SerialConfig, SerialTransport, TransportError

DEFAULT_POLL_INTERVAL = 0.005


class ApplicationError(RuntimeError):
    """Raised for invalid or unsupported application configuration."""


@dataclass(frozen=True)
class AppConfig:
    """Validated command-line configuration."""

    serial_port: str | None = None
    midi_mode: MidiEndpointMode | None = None
    midi_input: str | None = None
    midi_output: str | None = None
    poll_interval: float = DEFAULT_POLL_INTERVAL


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the bridge command-line parser."""

    parser = argparse.ArgumentParser(
        prog="lf-usb-bridge",
        description="Bridge Liquid Foot+ USB serial MIDI to computer MIDI ports.",
    )
    parser.add_argument(
        "--serial-port",
        metavar="PATH",
        help="serial device path; skips automatic LF+ discovery",
    )
    parser.add_argument(
        "--midi-mode",
        choices=[mode.value for mode in MidiEndpointMode],
        help="MIDI endpoint mode (default: virtual on macOS, existing elsewhere)",
    )
    parser.add_argument(
        "--midi-input",
        metavar="NAME",
        help=(
            "computer-to-LF+ input name "
            "(required in existing mode; customises virtual mode)"
        ),
    )
    parser.add_argument(
        "--midi-output",
        metavar="NAME",
        help=(
            "LF+-to-computer output name "
            "(required in existing mode; customises virtual mode)"
        ),
    )
    parser.add_argument(
        "--poll-interval",
        metavar="SECONDS",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=argparse.SUPPRESS,
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> AppConfig:
    """Parse command-line arguments into application configuration."""

    namespace = create_argument_parser().parse_args(argv)
    if namespace.poll_interval < 0:
        raise ApplicationError("--poll-interval must not be negative")

    mode = (
        MidiEndpointMode(namespace.midi_mode)
        if namespace.midi_mode is not None
        else None
    )
    return AppConfig(
        serial_port=namespace.serial_port,
        midi_mode=mode,
        midi_input=namespace.midi_input,
        midi_output=namespace.midi_output,
        poll_interval=namespace.poll_interval,
    )


def select_midi_mode(
    system_name: str,
    requested_mode: MidiEndpointMode | None,
) -> MidiEndpointMode:
    """Resolve endpoint mode without probing or silently falling back."""

    if requested_mode is not None:
        return requested_mode

    if system_name == "Darwin":
        return MidiEndpointMode.VIRTUAL

    return MidiEndpointMode.EXISTING


def select_serial_port(
    explicit_port: str | None,
    *,
    discover: Callable[[], SerialDevice] = discover_device,
) -> str:
    """Use an explicit serial path or select one compatible discovered device."""

    if explicit_port is not None:
        port = explicit_port.strip()
        if not port:
            raise ApplicationError("--serial-port must not be empty")
        return port

    return discover().port


def _endpoint_names(
    config: AppConfig,
    mode: MidiEndpointMode,
) -> tuple[str, str]:
    if mode is MidiEndpointMode.VIRTUAL:
        return (
            config.midi_input or DEFAULT_VIRTUAL_INPUT_NAME,
            config.midi_output or DEFAULT_VIRTUAL_OUTPUT_NAME,
        )

    if not config.midi_input or not config.midi_output:
        raise ApplicationError(
            "existing MIDI mode requires both --midi-input and --midi-output"
        )

    return config.midi_input, config.midi_output


def run_application(
    config: AppConfig,
    *,
    system_name: str | None = None,
    discover: Callable[[], SerialDevice] = discover_device,
    transport_factory: Callable[[SerialConfig], SerialTransport] = SerialTransport,
    bridge_factory: Callable[..., BridgeController] = BridgeController,
    midi_factory: Callable[..., MidiEndpointAdapter] = MidiEndpointAdapter,
    sleep: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
    status: Callable[[str], None] = print,
) -> None:
    """Construct, run, and cleanly tear down the complete bridge application."""

    active_system = system_name or platform.system()
    mode = select_midi_mode(active_system, config.midi_mode)
    serial_port = select_serial_port(config.serial_port, discover=discover)
    input_name, output_name = _endpoint_names(config, mode)

    transport = transport_factory(SerialConfig(port=serial_port))
    midi_adapter: MidiEndpointAdapter | None = None

    def send_to_computer(message: bytes) -> None:
        if midi_adapter is None:
            raise ApplicationError("MIDI adapter is not available")
        midi_adapter.send_to_computer(message)

    bridge = bridge_factory(transport, send_to_computer)
    midi_adapter = midi_factory(
        input_name=input_name,
        output_name=output_name,
        mode=mode,
        on_input=bridge.send_midi,
    )

    status(f"Serial: {serial_port}")
    status(
        f"MIDI ({mode.value}): input {input_name!r}, output {output_name!r}"
    )

    try:
        midi_adapter.open()
        bridge.start()
        status("LF+ USB Bridge running. Press Ctrl+C to stop.")

        try:
            while not stop_requested():
                bridge.poll()
                sleep(config.poll_interval)
        except KeyboardInterrupt:
            status("Stopping LF+ USB Bridge.")
    finally:
        try:
            bridge.stop()
        finally:
            try:
                midi_adapter.close()
            finally:
                transport.close()

    status("LF+ USB Bridge stopped.")


@contextmanager
def termination_requested() -> Iterator[Callable[[], bool]]:
    """Translate SIGINT/SIGTERM into a loop stop request and restore handlers."""

    requested = threading.Event()
    previous_handlers: dict[int, signal.Handlers] = {}

    def request_stop(signum, frame) -> None:
        del signum, frame
        requested.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)

    try:
        yield requested.is_set
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application and return a process exit code."""

    try:
        config = parse_args(argv)
        with termination_requested() as should_stop:
            run_application(config, stop_requested=should_stop)
    except (
        ApplicationError,
        BridgeError,
        DeviceDiscoveryError,
        MidiPortError,
        TransportError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0
