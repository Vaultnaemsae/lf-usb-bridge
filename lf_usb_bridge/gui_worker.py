"""Worker-thread lifecycle for the LF+ USB Bridge application."""

from __future__ import annotations

import logging
import platform
import re
import threading

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Signal, Slot

from .bridge import BridgeController
from .midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    WINDOWS_INPUT_ENDPOINT_NAME,
    WINDOWS_OUTPUT_ENDPOINT_NAME,
    MidiEndpointAdapter,
    MidiEndpointMode,
    MidiPortOpenError,
    list_input_names,
    list_output_names,
)
from .transport import SerialConfig, SerialTransport

logger = logging.getLogger(__name__)

DEFAULT_GUI_POLL_INTERVAL = 0.005


def concise_error_message(error: Exception) -> str:
    """Convert implementation exceptions into short user-facing status text."""

    detail = str(error).strip()
    lowered = detail.lower()

    if "identification" in lowered:
        return "LF+ identification timed out"
    if "midi input" in lowered:
        return f"Could not open {DEFAULT_VIRTUAL_INPUT_NAME}"
    if "midi output" in lowered:
        return f"Could not open {DEFAULT_VIRTUAL_OUTPUT_NAME}"
    if "resource busy" in lowered or "device busy" in lowered:
        return "Serial device is already in use"

    return detail or type(error).__name__


def resolve_numbered_midi_name(
    logical_name: str,
    available_names: Sequence[str],
) -> str:
    """Resolve a Windows MIDI endpoint with a backend-added number suffix."""

    if logical_name in available_names:
        return logical_name

    pattern = re.compile(
        rf"^{re.escape(logical_name)}(?:\s+\d+)+$"
    )
    matches = [name for name in available_names if pattern.fullmatch(name)]

    if len(matches) == 1:
        return matches[0]

    if not matches:
        available = ", ".join(repr(name) for name in available_names) or "none"
        raise MidiPortOpenError(
            f"MIDI endpoint {logical_name!r} was not found; "
            f"available endpoints: {available}"
        )

    raise MidiPortOpenError(
        f"multiple MIDI endpoints match {logical_name!r}: "
        + ", ".join(repr(name) for name in matches)
    )


def production_midi_configuration() -> tuple[str, str, MidiEndpointMode]:
    """Select the production GUI MIDI endpoints for the current platform."""

    if platform.system() == "Windows":
        input_name = resolve_numbered_midi_name(
            WINDOWS_INPUT_ENDPOINT_NAME,
            list_input_names(),
        )
        output_name = resolve_numbered_midi_name(
            WINDOWS_OUTPUT_ENDPOINT_NAME,
            list_output_names(),
        )
        return input_name, output_name, MidiEndpointMode.EXISTING

    return (
        DEFAULT_VIRTUAL_INPUT_NAME,
        DEFAULT_VIRTUAL_OUTPUT_NAME,
        MidiEndpointMode.VIRTUAL,
    )


class BridgeWorker(QObject):
    """Own one complete bridge session on a dedicated Qt thread."""

    connected = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        serial_port: str,
        *,
        transport_factory: Callable[[SerialConfig], SerialTransport] = SerialTransport,
        bridge_factory: Callable[..., BridgeController] = BridgeController,
        midi_factory: Callable[..., MidiEndpointAdapter] = MidiEndpointAdapter,
        poll_interval: float = DEFAULT_GUI_POLL_INTERVAL,
    ) -> None:
        super().__init__()
        if not serial_port:
            raise ValueError("serial_port must not be empty")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")

        self._serial_port = serial_port
        self._transport_factory = transport_factory
        self._bridge_factory = bridge_factory
        self._midi_factory = midi_factory
        self._poll_interval = poll_interval
        self._stop_requested = threading.Event()
        self._running = False

    @property
    def serial_port(self) -> str:
        return self._serial_port

    def request_stop(self) -> None:
        """Request cooperative shutdown; safe to call from the GUI thread."""

        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        """Open, poll, and close one bridge session on the worker thread."""

        if self._running:
            return

        self._running = True
        transport: SerialTransport | None = None
        bridge: BridgeController | None = None
        midi_adapter: MidiEndpointAdapter | None = None
        failure_reported = False

        try:
            transport = self._transport_factory(
                SerialConfig(port=self._serial_port)
            )

            def send_to_computer(message: bytes) -> None:
                if midi_adapter is None:
                    raise RuntimeError("MIDI adapter is not available")
                midi_adapter.send_to_computer(message)

            bridge = self._bridge_factory(transport, send_to_computer)

            # Keep injected test/fake factories on the original generic virtual
            # path. Only the real production adapter performs platform-specific
            # endpoint selection.
            if self._midi_factory is MidiEndpointAdapter:
                input_name, output_name, mode = production_midi_configuration()
            else:
                input_name = DEFAULT_VIRTUAL_INPUT_NAME
                output_name = DEFAULT_VIRTUAL_OUTPUT_NAME
                mode = MidiEndpointMode.VIRTUAL

            midi_adapter = self._midi_factory(
                input_name=input_name,
                output_name=output_name,
                mode=mode,
                on_input=bridge.send_midi,
            )

            midi_adapter.open()
            if not self._stop_requested.is_set():
                bridge.start()

            if not self._stop_requested.is_set():
                self.connected.emit(self._serial_port)

            while not self._stop_requested.is_set():
                bridge.poll()
                self._stop_requested.wait(self._poll_interval)
        except Exception as exc:
            logger.exception("LF+ bridge worker failed")
            if not self._stop_requested.is_set():
                failure_reported = True
                self.failed.emit(concise_error_message(exc))
        finally:
            cleanup_error: Exception | None = None

            if bridge is not None:
                try:
                    bridge.stop()
                except Exception as exc:
                    logger.exception("Could not stop LF+ bridge cleanly")
                    cleanup_error = exc

            if midi_adapter is not None:
                try:
                    midi_adapter.close()
                except Exception as exc:
                    logger.exception("Could not close LF+ MIDI ports cleanly")
                    if cleanup_error is None:
                        cleanup_error = exc

            if transport is not None:
                try:
                    transport.close()
                except Exception as exc:
                    logger.exception("Could not close LF+ serial transport cleanly")
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and not failure_reported:
                self.failed.emit(concise_error_message(cleanup_error))

            self._running = False
            self.finished.emit()
