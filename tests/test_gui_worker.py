from __future__ import annotations

import threading
import time

from collections.abc import Callable

from lf_usb_bridge.bridge import BridgeStartError
from PySide6.QtCore import (
    QObject,
    Qt,
    Signal,
    Slot,
    qInstallMessageHandler,
)

from lf_usb_bridge.device_discovery import SerialDevice
from lf_usb_bridge.gui import BridgeSession, ConnectionState, MainWindow
from lf_usb_bridge.gui_worker import BridgeWorker
from lf_usb_bridge.midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    MidiEndpointMode,
)


def wait_until(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


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
        harness: "WorkerHarness",
    ) -> None:
        self.transport = transport
        self.midi_output = midi_output
        self.harness = harness

    def start(self) -> None:
        self.harness.events.append("bridge.start")
        if self.harness.start_error is not None:
            raise self.harness.start_error

    def poll(self) -> int:
        self.harness.poll_thread_ids.add(threading.get_ident())
        self.harness.poll_count += 1
        return 0

    def stop(self) -> None:
        self.harness.events.append("bridge.stop")

    def send_midi(self, message: bytes) -> None:
        self.harness.computer_messages.append(bytes(message))


class FakeMidiAdapter:
    def __init__(self, harness: "WorkerHarness", **kwargs) -> None:
        self.harness = harness
        self.input_name = kwargs["input_name"]
        self.output_name = kwargs["output_name"]
        self.mode = kwargs["mode"]
        self.on_input = kwargs["on_input"]

    def open(self) -> None:
        self.harness.events.append("midi.open")

    def send_to_computer(self, message: bytes) -> None:
        self.harness.computer_output.append(bytes(message))

    def close(self) -> None:
        self.harness.events.append("midi.close")


class WorkerHarness:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.poll_thread_ids: set[int] = set()
        self.poll_count = 0
        self.start_error: Exception | None = None
        self.adapters: list[FakeMidiAdapter] = []
        self.transports: list[FakeTransport] = []
        self.computer_messages: list[bytes] = []
        self.computer_output: list[bytes] = []
        self.worker_destroy_thread_ids: list[int] = []

    def transport_factory(self, config) -> FakeTransport:
        transport = FakeTransport(config, self.events)
        self.transports.append(transport)
        return transport

    def bridge_factory(
        self,
        transport: FakeTransport,
        midi_output: Callable[[bytes], None],
    ) -> FakeBridge:
        return FakeBridge(transport, midi_output, self)

    def midi_factory(self, **kwargs) -> FakeMidiAdapter:
        adapter = FakeMidiAdapter(self, **kwargs)
        self.adapters.append(adapter)
        return adapter

    def worker_factory(self, serial_port: str) -> BridgeWorker:
        worker = BridgeWorker(
            serial_port,
            transport_factory=self.transport_factory,
            bridge_factory=self.bridge_factory,
            midi_factory=self.midi_factory,
            poll_interval=0.001,
        )
        worker.destroyed.connect(
            self.record_worker_destroyed,
            Qt.ConnectionType.DirectConnection,
        )
        return worker

    def record_worker_destroyed(self, object_=None) -> None:
        del object_
        self.worker_destroy_thread_ids.append(threading.get_ident())


class TrackingBridgeSession(BridgeSession):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.references_at_thread_finished: list[tuple[bool, bool]] = []

    @Slot()
    def _on_thread_finished(self) -> None:
        self.references_at_thread_finished.append(
            (self._worker is not None, self._thread is not None)
        )
        super()._on_thread_finished()


class LateSignalWorker(QObject):
    connected = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, serial_port: str) -> None:
        super().__init__()
        self.serial_port = serial_port
        self._stop_requested = threading.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        self.connected.emit(self.serial_port)
        self._stop_requested.wait(1.0)
        self.connected.emit("late connected signal")
        self.failed.emit("late failure signal")
        self.finished.emit()


def test_worker_runs_off_gui_thread_and_cleans_up_in_order(qapp):
    harness = WorkerHarness()
    session = TrackingBridgeSession(worker_factory=harness.worker_factory)
    states: list[str] = []
    finished: list[bool] = []
    main_thread_id = threading.get_ident()
    session.state_changed.connect(states.append)
    session.finished.connect(lambda: finished.append(True))

    assert session.start("/dev/cu.usbserial-LF123456")
    assert not session.start("/dev/cu.usbserial-LF999999")
    wait_until(
        qapp,
        lambda: session.state is ConnectionState.CONNECTED
        and harness.poll_count > 0,
    )

    assert harness.poll_thread_ids
    assert main_thread_id not in harness.poll_thread_ids
    assert session.stop()
    wait_until(qapp, lambda: bool(finished))

    assert not session.is_active
    assert session.references_at_thread_finished == [(True, True)]
    assert harness.worker_destroy_thread_ids
    assert all(
        thread_id in harness.poll_thread_ids
        for thread_id in harness.worker_destroy_thread_ids
    )
    assert states == ["Connecting", "Connected", "Disconnected"]
    assert harness.events == [
        "midi.open",
        "bridge.start",
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]
    assert harness.adapters[0].input_name == DEFAULT_VIRTUAL_INPUT_NAME
    assert harness.adapters[0].output_name == DEFAULT_VIRTUAL_OUTPUT_NAME
    assert harness.adapters[0].mode is MidiEndpointMode.VIRTUAL


def test_startup_failure_transitions_to_error_and_exits_thread(qapp):
    harness = WorkerHarness()
    harness.start_error = BridgeStartError(
        "bridge startup failed: LF+ identification handshake failed"
    )
    session = BridgeSession(worker_factory=harness.worker_factory)
    statuses: list[str] = []
    finished: list[bool] = []
    session.status_changed.connect(statuses.append)
    session.finished.connect(lambda: finished.append(True))

    assert session.start("/dev/cu.usbserial-LF123456")
    wait_until(qapp, lambda: bool(finished))

    assert session.state is ConnectionState.ERROR
    assert not session.is_active
    assert "LF+ identification timed out" in statuses
    assert harness.events == [
        "midi.open",
        "bridge.start",
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_one_hundred_repeated_start_stop_cycles_have_safe_qobject_teardown(qapp):
    harness = WorkerHarness()
    session = TrackingBridgeSession(worker_factory=harness.worker_factory)
    finished_count = 0
    qt_messages: list[str] = []

    def count_finished() -> None:
        nonlocal finished_count
        finished_count += 1

    def capture_qt_message(message_type, context, message) -> None:
        del message_type, context
        qt_messages.append(message)

    session.finished.connect(count_finished)
    previous_handler = qInstallMessageHandler(capture_qt_message)
    try:
        for expected_count in range(1, 101):
            assert session.start("/dev/cu.usbserial-LF123456")
            wait_until(
                qapp,
                lambda: session.state is ConnectionState.CONNECTED,
            )
            assert session.stop()
            wait_until(qapp, lambda: finished_count == expected_count)
            assert not session.is_active
    finally:
        qInstallMessageHandler(previous_handler)

    assert harness.events.count("midi.open") == 100
    assert harness.events.count("bridge.start") == 100
    assert harness.events.count("bridge.stop") == 100
    assert harness.events.count("midi.close") == 100
    assert harness.events.count("transport.close") == 100
    assert len(harness.worker_destroy_thread_ids) == 100
    assert session.references_at_thread_finished == [(True, True)] * 100
    assert not any(
        "shared QObject was deleted directly" in message
        or "QThread: Destroyed while thread" in message
        for message in qt_messages
    )


def test_application_shutdown_waits_for_worker_exit(qapp):
    harness = WorkerHarness()
    session = BridgeSession(worker_factory=harness.worker_factory)

    assert session.start("/dev/cu.usbserial-LF123456")
    wait_until(
        qapp,
        lambda: session.state is ConnectionState.CONNECTED,
    )

    assert session.shutdown(timeout_ms=1_000)
    wait_until(qapp, lambda: not session.is_active)

    assert harness.events[-3:] == [
        "bridge.stop",
        "midi.close",
        "transport.close",
    ]


def test_late_worker_status_signals_are_ignored_after_stop(qapp):
    session = BridgeSession(worker_factory=LateSignalWorker)
    states: list[str] = []
    statuses: list[str] = []
    finished: list[bool] = []
    session.state_changed.connect(states.append)
    session.status_changed.connect(statuses.append)
    session.finished.connect(lambda: finished.append(True))

    assert session.start("/dev/cu.usbserial-LF123456")
    wait_until(qapp, lambda: session.state is ConnectionState.CONNECTED)
    assert session.stop()
    wait_until(qapp, lambda: bool(finished))

    assert states == ["Connecting", "Connected", "Disconnected"]
    assert statuses == [
        "Connecting to Liquid Foot+ device…",
        "Connected to Liquid Foot+ device.",
        "Stopping connection",
        "Connection stopped",
    ]


def test_close_while_connected_waits_for_real_thread_teardown(qapp):
    device = SerialDevice(
        port="/dev/cu.usbserial-LF123456",
        description="FTDI USB Serial Port",
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number="LF123456",
        vid=0x0403,
        pid=0x6015,
    )
    harness = WorkerHarness()
    session = TrackingBridgeSession(worker_factory=harness.worker_factory)
    window = MainWindow(discover=lambda: [device], session=session)
    window.show()
    window.start_bridge()
    wait_until(qapp, lambda: session.state is ConnectionState.CONNECTED)

    assert not window.close()
    assert window.isVisible()
    wait_until(
        qapp,
        lambda: not session.is_active and not window.isVisible(),
    )

    assert session.references_at_thread_finished == [(True, True)]
    assert len(harness.worker_destroy_thread_ids) == 1
