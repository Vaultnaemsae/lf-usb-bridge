from __future__ import annotations

import sys
import time
from contextlib import contextmanager

from PySide6.QtCore import QObject, QPropertyAnimation, Signal, Qt
from PySide6.QtWidgets import QLabel

from lf_usb_bridge.device_discovery import SerialDevice
from lf_usb_bridge.gui import (
    CONNECTED_MESSAGE,
    CONNECTING_MESSAGE,
    NO_DEVICE_MESSAGE,
    READY_MESSAGE,
    BridgeSession,
    ConnectionState,
    EDITOR_REPOSITORY_URL,
    MainWindow,
    SetupDialog,
)
from lf_usb_bridge.midi_ports import (
    DEFAULT_VIRTUAL_INPUT_NAME,
    DEFAULT_VIRTUAL_OUTPUT_NAME,
    WINDOWS_INPUT_ENDPOINT_NAME,
    WINDOWS_OUTPUT_ENDPOINT_NAME,
)


def make_device(
    port: str,
    serial_number: str,
    description: str = "FTDI USB Serial Port",
) -> SerialDevice:
    return SerialDevice(
        port=port,
        description=description,
        manufacturer="FTDI",
        product="FT231X USB UART",
        serial_number=serial_number,
        vid=0x0403,
        pid=0x6015,
    )


def process_until(qapp, predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


@contextmanager
def managed_window(qapp, *args, **kwargs):
    window = MainWindow(*args, **kwargs)
    try:
        yield window
    finally:
        if window._session.is_active:
            window._session.shutdown()
        window.close()
        qapp.processEvents()


class FakeSession(QObject):
    state_changed = Signal(str)
    status_changed = Signal(str)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.is_active = False
        self.start_ports: list[str] = []
        self.stop_count = 0
        self.shutdown_count = 0

    def start(self, serial_port: str) -> bool:
        if self.is_active:
            return False
        self.is_active = True
        self.start_ports.append(serial_port)
        self.state_changed.emit(ConnectionState.CONNECTING.value)
        self.status_changed.emit(CONNECTING_MESSAGE)
        return True

    def stop(self) -> bool:
        if not self.is_active:
            return False
        self.stop_count += 1
        self.status_changed.emit("Stopping connection")
        return True

    def connect_successfully(self) -> None:
        self.state_changed.emit(ConnectionState.CONNECTED.value)
        self.status_changed.emit(CONNECTED_MESSAGE)

    def fail(self, message: str) -> None:
        self.is_active = False
        self.state_changed.emit(ConnectionState.ERROR.value)
        self.status_changed.emit(message)
        self.finished.emit()

    def finish_cleanly(self) -> None:
        self.is_active = False
        self.state_changed.emit(ConnectionState.DISCONNECTED.value)
        self.status_changed.emit("Connection stopped")
        self.finished.emit()

    def shutdown(self) -> bool:
        self.shutdown_count += 1
        if self.is_active:
            self.stop()
            self.finish_cleanly()
        return True


def test_initial_no_device_state(qapp):
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [], session=session) as window:
        assert window.connection_state is ConnectionState.DISCONNECTED
        assert window.device_selector.count() == 0
        assert window.device_label.text() == NO_DEVICE_MESSAGE
        assert window.device_label.isVisibleTo(window.device_container)
        assert not window.device_selector.isVisibleTo(window.device_container)
        assert window.status_label.text() == NO_DEVICE_MESSAGE
        assert window.status_label.isHidden()
        assert not window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()
        assert window.refresh_button.isEnabled()
        assert window.refresh_button.text() == "Rescan"
        assert window.refresh_button.accessibleName() == "Rescan devices"

        if sys.platform.startswith("win"):
            assert window.midi_input_label.text() == WINDOWS_INPUT_ENDPOINT_NAME
            assert window.midi_output_label.text() == WINDOWS_OUTPUT_ENDPOINT_NAME
        else:
            assert window.midi_input_label.text() == DEFAULT_VIRTUAL_INPUT_NAME
            assert window.midi_output_label.text() == DEFAULT_VIRTUAL_OUTPUT_NAME


def test_connection_heading_is_removed_and_window_is_compact(qapp):
    with managed_window(qapp, discover=lambda: [], session=FakeSession()) as window:
        assert not hasattr(window, "connection_heading")
        assert not window.findChildren(QLabel, "connectionHeading")
        assert window.minimumWidth() == 350
        assert window.minimumHeight() == 210
        assert window.width() <= 380
        assert window.height() <= 238


def test_one_device_uses_single_line_static_label(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    with managed_window(
        qapp,
        discover=lambda: [device],
        session=FakeSession(),
    ) as window:
        expected = f"{device.description} — {device.serial_number}"
        assert window.selected_device == device
        assert window.device_selector.count() == 1
        assert window.device_selector.currentText() == expected
        assert window.device_label.text() == expected
        assert not window.device_label.wordWrap()
        assert window.device_label.textFormat() == Qt.TextFormat.PlainText
        assert window.device_label.isVisibleTo(window.device_container)
        assert not window.device_selector.isVisibleTo(window.device_container)
        assert window.device_label.toolTip() == (
            f"{device.description}\n"
            f"Serial: {device.serial_number}\n"
            f"Port: {device.port}"
        )
        assert window.device_label.sizePolicy().verticalPolicy().name == "Fixed"
        assert window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()


def test_multiple_devices_use_combo_box_and_allow_selection(qapp):
    preferred = make_device("/dev/cu.usbserial-LF111111", "LF111111")
    alternate = make_device("/dev/cu.usbserial-LF222222", "LF222222")
    with managed_window(
        qapp,
        discover=lambda: [preferred, alternate],
        session=FakeSession(),
    ) as window:
        assert window.selected_device == preferred
        assert window.device_selector.isVisibleTo(window.device_container)
        assert not window.device_label.isVisibleTo(window.device_container)
        assert window.device_selector.isEnabled()
        assert window.device_selector.sizePolicy().horizontalPolicy().name == "Expanding"

        window.device_selector.setCurrentIndex(1)
        assert window.selected_device == alternate


def test_connected_state_uses_static_device_label(qapp):
    devices = [
        make_device("/dev/cu.usbserial-LF111111", "LF111111"),
        make_device("/dev/cu.usbserial-LF222222", "LF222222"),
    ]
    session = FakeSession()
    with managed_window(qapp, discover=lambda: devices, session=session) as window:
        window.start_bridge()
        session.connect_successfully()

        assert window.connection_state is ConnectionState.CONNECTED
        assert window.device_label.isVisibleTo(window.device_container)
        assert not window.device_selector.isVisibleTo(window.device_container)
        assert window.device_label.text() == window.device_selector.currentText()


def test_normal_status_message_is_hidden_through_success_and_stop(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        assert window.status_label.text() == READY_MESSAGE
        assert window.status_label.isHidden()

        window.start_button.click()
        assert session.start_ports == [device.port]
        assert window.connection_state is ConnectionState.CONNECTING
        assert window.status_label.text() == CONNECTING_MESSAGE
        assert window.status_label.isHidden()
        assert not window.start_button.isEnabled()
        assert window.stop_button.isEnabled()
        assert not window.refresh_button.isEnabled()

        session.connect_successfully()
        assert window.connection_state is ConnectionState.CONNECTED
        assert window.status_label.text() == CONNECTED_MESSAGE
        assert window.status_label.isHidden()
        assert window.stop_button.isEnabled()

        window.stop_button.click()
        assert session.stop_count == 1
        session.finish_cleanly()
        assert window.connection_state is ConnectionState.DISCONNECTED
        assert window.status_label.text() == "Connection stopped"
        assert window.status_label.isHidden()
        assert window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()


def test_worker_failure_shows_error_message_and_allows_retry(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        window.start_bridge()
        session.fail("LF+ identification timed out")

        assert window.connection_state is ConnectionState.ERROR
        assert window.status_label.text() == "LF+ identification timed out"
        assert not window.status_label.isHidden()
        assert window.start_button.isEnabled()
        assert not window.stop_button.isEnabled()

        window.start_bridge()
        assert session.start_ports == [device.port, device.port]
        session.finish_cleanly()
        assert window.status_label.isHidden()


def test_discovery_failure_shows_error_message(qapp):
    def fail_discovery():
        raise RuntimeError("USB discovery failed")

    with managed_window(
        qapp,
        discover=fail_discovery,
        session=FakeSession(),
    ) as window:
        assert window.connection_state is ConnectionState.ERROR
        assert "USB discovery failed" in window.status_label.text()
        assert not window.status_label.isHidden()


def test_refresh_detects_new_device_and_hides_normal_message(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    responses = iter(([], [device]))
    with managed_window(
        qapp,
        discover=lambda: next(responses),
        session=FakeSession(),
    ) as window:
        assert window.status_label.text() == NO_DEVICE_MESSAGE
        assert window.status_label.isHidden()
        window.refresh_button.click()

        assert window.selected_device == device
        assert window.start_button.isEnabled()
        assert window.status_label.text() == READY_MESSAGE
        assert window.status_label.isHidden()


def test_auto_connect_has_its_own_uncluttered_full_width_row(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    with managed_window(
        qapp,
        discover=lambda: [device],
        session=FakeSession(),
    ) as window:
        row = window.findChild(QObject, "autoConnectRow")
        assert row is not None
        assert window.findChild(QObject, "autoConnectSeparator") is None
        assert window.auto_connect_checkbox.parent() is row
        assert row.layout().contentsMargins().top() >= 4


def test_disabled_button_text_remains_readable(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        window.start_bridge()
        assert not window.refresh_button.isEnabled()
        assert not window.start_button.isEnabled()
        refresh_style = window.refresh_button.styleSheet()
        assert "#AEB4B8" in refresh_style or "#636A70" in refresh_style
        assert "#34383B" in refresh_style or "#E4E7E9" in refresh_style


def test_status_and_button_colours_are_bright(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        assert "#0A84FF" in window.refresh_button.styleSheet()
        assert "#20C66B" in window.start_button.styleSheet()
        assert "#E5484D" in window.stop_button.styleSheet()

        window.start_bridge()
        connecting_colour = window.state_label.palette().color(
            window.state_label.foregroundRole()
        )
        assert connecting_colour.name().upper() == "#FFB000"

        session.connect_successfully()
        connected_colour = window.state_label.palette().color(
            window.state_label.foregroundRole()
        )
        assert connected_colour.name().upper() == "#20C66B"
        assert (
            window._state_pulse_animation.state()
            == QPropertyAnimation.State.Running
        )


def test_repeated_start_stop_from_window(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        for _ in range(2):
            window.start_bridge()
            session.connect_successfully()
            window.stop_bridge()
            session.finish_cleanly()

        assert session.start_ports == [device.port, device.port]
        assert session.stop_count == 2


def test_close_requests_stop_and_waits_for_cleanup(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    window = MainWindow(discover=lambda: [device], session=session)
    try:
        window.show()
        window.start_bridge()
        session.connect_successfully()

        assert not window.close()
        assert session.stop_count == 1
        assert window.isVisible()

        session.finish_cleanly()
        process_until(qapp, lambda: not window.isVisible())
    finally:
        if session.is_active:
            session.shutdown()
        window.close()
        qapp.processEvents()


def test_application_quit_boundary_waits_for_session(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        window.start_bridge()
        window.shutdown_for_quit()

        assert session.shutdown_count == 1
        assert session.stop_count == 1
        assert not session.is_active


def test_worker_factory_failure_transitions_session_to_error(qapp):
    created_ports: list[str] = []

    class InertWorker:
        def __init__(self, serial_port: str) -> None:
            created_ports.append(serial_port)
            raise RuntimeError("worker creation failed")

    session = BridgeSession(worker_factory=InertWorker)

    assert not session.start("/dev/cu.usbserial-LF123456")
    assert session.state is ConnectionState.ERROR
    assert created_ports == ["/dev/cu.usbserial-LF123456"]


def test_setup_dialog_opens_at_readable_default_size(qapp):
    dialog = SetupDialog(device=None, platform_name="darwin")
    try:
        assert dialog.width() == 650
        assert dialog.height() == 470
        assert dialog.minimumWidth() == 600
        assert dialog.minimumHeight() == 440
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_explains_exact_windows_endpoint_names(qapp):
    dialog = SetupDialog(device=None, platform_name="win32")
    try:
        text = dialog.midi_setup_label.text()
        input_name = dialog.findChild(
            QLabel, "setupMidiInputName"
        )
        output_name = dialog.findChild(
            QLabel, "setupMidiOutputName"
        )

        assert input_name is not None
        assert output_name is not None
        assert input_name.text() == "LF+ IN PORT"
        assert output_name.text() == "LF+ OUT PORT"
        assert "loopMIDI" in text
        assert "Windows MIDI Services" in text
        assert "Rescan" in text
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_makes_port_directions_unambiguous(qapp):
    dialog = SetupDialog(device=None, platform_name="darwin")
    try:
        assert dialog.findChild(
            QLabel, "setupMidiInputDirection"
        ).text() == "Computer → Liquid Foot+"
        assert dialog.findChild(
            QLabel, "setupMidiOutputDirection"
        ).text() == "Liquid Foot+ → Computer"
        assert "created automatically" in dialog.midi_setup_label.text()
        assert "when the bridge starts" in dialog.midi_setup_label.text()
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_links_to_sung_le_editor_and_eeprom_wizard(qapp):
    dialog = SetupDialog(device=None, platform_name="darwin")
    try:
        reference = dialog.findChild(QLabel, "setupDeviceReference")

        assert reference is not None
        assert EDITOR_REPOSITORY_URL in reference.text()
        assert "Sung Le" in reference.text()
        assert "FAMC Liquid Foot+ Editor" in reference.text()
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_status_completes_usb_connection_sentence(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    dialog = SetupDialog(device=device, platform_name="darwin")
    try:
        headings = [
            label
            for label in dialog.findChildren(QLabel)
            if label.text() == "Your USB serial connection is:"
        ]
        assert len(headings) == 1
        heading = headings[0]
        assert dialog.device_status_label.text() == "Ready"
        dialog.show()
        qapp.processEvents()
        heading_pos = heading.mapTo(dialog, heading.rect().topLeft())
        status_pos = dialog.device_status_label.mapTo(
            dialog,
            dialog.device_status_label.rect().topLeft(),
        )
        assert status_pos.x() >= heading_pos.x() + heading.width()
        assert status_pos.x() - (heading_pos.x() + heading.width()) <= 12
        assert abs(status_pos.y() - heading_pos.y()) <= 2
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_reports_missing_serial_device_clearly(qapp):
    dialog = SetupDialog(device=None, platform_name="darwin")
    try:
        assert dialog.device_status_label.text() == "Not Available"
        assert "no compatible" in dialog.findChild(
            QLabel, "setupDeviceSafetyNote"
        ).text().lower()
        assert "USB serial mode" in dialog.findChild(
            QLabel, "setupDeviceSafetyNote"
        ).text()
    finally:
        dialog.close()
        qapp.processEvents()


def test_setup_dialog_reports_detected_serial_device(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    dialog = SetupDialog(device=device, platform_name="darwin")
    try:
        assert "Ready" in dialog.device_status_label.text()
        text = dialog.device_details_label.text()
        assert device.port in text
        assert device.serial_number in text
        assert "0x6015" in text
    finally:
        dialog.close()
        qapp.processEvents()


def test_main_window_has_setup_button_without_changing_compact_width(qapp):
    with managed_window(qapp, discover=lambda: [], session=FakeSession()) as window:
        assert window.setup_button.text() == "Setup…"
        assert window.setup_button.isEnabled()
        assert window.width() <= 380


def test_setup_dialog_uses_plain_layout_without_clipping_cards(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    dialog = SetupDialog(device=device, platform_name="darwin")
    try:
        assert dialog.findChild(QObject, "setupDeviceCard") is None
        assert dialog.findChild(QObject, "setupMidiCard") is None
        assert dialog.findChild(QObject, "setupSectionSeparator") is not None
        assert dialog.findChild(QObject, "setupDeviceGrid") is not None
        assert dialog.findChild(QObject, "setupMidiGrid") is not None
        assert (
            dialog.findChild(
                QLabel, "setupDeviceDetailValue0"
            ).text()
            == "LF123456"
        )
        assert (
            dialog.findChild(
                QLabel, "setupDeviceDetailValue1"
            ).text()
            == device.port
        )
        assert (
            dialog.findChild(
                QLabel, "setupMidiInputName"
            ).alignment()
            & Qt.AlignmentFlag.AlignLeft
        )
        assert (
            dialog.findChild(
                QLabel, "setupMidiOutputName"
            ).alignment()
            & Qt.AlignmentFlag.AlignLeft
        )
    finally:
        dialog.close()
        qapp.processEvents()