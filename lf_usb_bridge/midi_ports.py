"""MIDI endpoint adapter built on mido and python-rtmidi."""

from __future__ import annotations

import sys

from collections.abc import Callable
from enum import Enum
from typing import Protocol

import mido

MACOS_VIRTUAL_PORT_NAME = "LF+ USB MIDI"
WINDOWS_INPUT_ENDPOINT_NAME = "LF+ IN PORT"
WINDOWS_OUTPUT_ENDPOINT_NAME = "LF+ OUT PORT"

# Backward-compatible defaults for callers that create virtual endpoints.
# CoreMIDI distinguishes source and destination direction independently, so
# both macOS virtual endpoints intentionally share one device-style name.
DEFAULT_VIRTUAL_INPUT_NAME = MACOS_VIRTUAL_PORT_NAME
DEFAULT_VIRTUAL_OUTPUT_NAME = MACOS_VIRTUAL_PORT_NAME


class MidiPortError(RuntimeError):
    """Base error for MIDI endpoint failures."""


class MidiPortOpenError(MidiPortError):
    """Raised when a MIDI input or output cannot be opened."""


class MidiPortSendError(MidiPortError):
    """Raised when sending MIDI to the computer fails."""


RawMidiCallback = Callable[[bytes], None]


class MidiEndpointMode(str, Enum):
    """How the adapter obtains its computer-facing MIDI endpoints."""

    VIRTUAL = "virtual"
    EXISTING = "existing"


class InputPortLike(Protocol):
    @property
    def closed(self) -> bool:
        ...

    def close(self) -> None:
        ...


class OutputPortLike(Protocol):
    @property
    def closed(self) -> bool:
        ...

    def send(self, message: mido.Message) -> None:
        ...

    def close(self) -> None:
        ...


def list_input_names() -> list[str]:
    """Return currently available MIDI input endpoint names."""
    return list(mido.get_input_names())


def list_output_names() -> list[str]:
    """Return currently available MIDI output endpoint names."""
    return list(mido.get_output_names())


def resolve_existing_port_name(
    expected_name: str,
    available_names: list[str],
) -> str | None:
    """Resolve a configured endpoint against names reported by the MIDI backend."""

    if expected_name in available_names:
        return expected_name

    if not sys.platform.startswith("win"):
        return None

    prefix = f"{expected_name} "
    matches = [
        name
        for name in available_names
        if name.startswith(prefix) and name[len(prefix):].isdigit()
    ]

    if len(matches) == 1:
        return matches[0]

    return None


def message_to_bytes(message: mido.Message) -> bytes:
    """Convert one mido message into its complete raw MIDI representation."""
    return bytes(message.bytes())


def bytes_to_message(data: bytes | bytearray | memoryview) -> mido.Message:
    """Convert one complete raw MIDI message into a mido message."""
    payload = bytes(data)

    if not payload:
        raise ValueError("MIDI message must not be empty")

    try:
        return mido.Message.from_bytes(list(payload))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid MIDI message: {payload.hex(' ')}") from exc


class MidiEndpointAdapter:
    """Own one computer-facing MIDI input and one computer-facing MIDI output."""

    def __init__(
        self,
        *,
        input_name: str = DEFAULT_VIRTUAL_INPUT_NAME,
        output_name: str = DEFAULT_VIRTUAL_OUTPUT_NAME,
        on_input: RawMidiCallback,
        mode: MidiEndpointMode | str = MidiEndpointMode.EXISTING,
        open_input: Callable[..., InputPortLike] = mido.open_input,
        open_output: Callable[..., OutputPortLike] = mido.open_output,
    ) -> None:
        if not input_name:
            raise ValueError("input_name must not be empty")

        if not output_name:
            raise ValueError("output_name must not be empty")

        try:
            endpoint_mode = MidiEndpointMode(mode)
        except ValueError as exc:
            raise ValueError(f"unsupported MIDI endpoint mode: {mode!r}") from exc

        self._input_name = input_name
        self._output_name = output_name
        self._on_input = on_input
        self._mode = endpoint_mode
        self._open_input = open_input
        self._open_output = open_output

        self._input_port: InputPortLike | None = None
        self._output_port: OutputPortLike | None = None

    @property
    def input_name(self) -> str:
        return self._input_name

    @property
    def output_name(self) -> str:
        return self._output_name

    @property
    def mode(self) -> MidiEndpointMode:
        return self._mode

    @property
    def is_open(self) -> bool:
        return (
            self._input_port is not None
            and self._output_port is not None
            and not self._input_port.closed
            and not self._output_port.closed
        )

    def open(self) -> None:
        """Open both MIDI endpoints.

        Calling open while both endpoints are already open has no effect.
        """
        if self.is_open:
            return

        self.close()

        open_options: dict[str, bool] = {}
        resolved_input_name = self._input_name
        resolved_output_name = self._output_name

        if self._mode is MidiEndpointMode.VIRTUAL:
            open_options["virtual"] = True
        else:
            input_names = list_input_names()
            output_names = list_output_names()

            resolved_input_name = resolve_existing_port_name(
                self._input_name,
                input_names,
            )
            resolved_output_name = resolve_existing_port_name(
                self._output_name,
                output_names,
            )

            if resolved_input_name is None:
                raise MidiPortOpenError(
                    f"MIDI input {self._input_name!r} was not found; "
                    f"available inputs: {input_names!r}"
                )

            if resolved_output_name is None:
                raise MidiPortOpenError(
                    f"MIDI output {self._output_name!r} was not found; "
                    f"available outputs: {output_names!r}"
                )

        try:
            output_port = self._open_output(
                resolved_output_name,
                **open_options,
            )
        except Exception as exc:
            raise MidiPortOpenError(
                f"could not open {self._mode.value} MIDI output "
                f"{resolved_output_name!r}: {exc}"
            ) from exc

        try:
            input_port = self._open_input(
                resolved_input_name,
                callback=self._handle_input,
                **open_options,
            )
        except Exception as exc:
            try:
                output_port.close()
            finally:
                raise MidiPortOpenError(
                    f"could not open {self._mode.value} MIDI input "
                    f"{resolved_input_name!r}: {exc}"
                ) from exc

        self._output_port = output_port
        self._input_port = input_port

    def send_to_computer(
        self,
        data: bytes | bytearray | memoryview,
    ) -> None:
        """Send one complete LF+ MIDI message to the selected computer output."""
        if not self.is_open or self._output_port is None:
            raise MidiPortError("MIDI endpoints are not open")

        message = bytes_to_message(data)

        try:
            self._output_port.send(message)
        except Exception as exc:
            raise MidiPortSendError(
                f"could not send MIDI to {self._output_name!r}: {exc}"
            ) from exc

    def close(self) -> None:
        """Close both MIDI endpoints. Safe to call repeatedly."""
        input_port = self._input_port
        output_port = self._output_port

        self._input_port = None
        self._output_port = None

        if input_port is not None:
            try:
                if not input_port.closed:
                    input_port.close()
            except Exception:
                pass

        if output_port is not None:
            try:
                if not output_port.closed:
                    output_port.close()
            except Exception:
                pass

    def _handle_input(self, message: mido.Message) -> None:
        """Receive computer MIDI and publish it as raw bytes."""
        self._on_input(message_to_bytes(message))

    def __enter__(self) -> "MidiEndpointAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()