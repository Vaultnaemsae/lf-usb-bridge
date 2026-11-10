from __future__ import annotations

import sys
import time
from contextlib import contextmanager

import pytest

from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, Signal, Qt
from PySide6.QtWidgets import QLabel, QSystemTrayIcon

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
    MenuBarController,
    SetupDialog,
    configure_desktop,
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
        window.deleteLater()
        qapp.sendPostedEvents(window, QEvent.Type.DeferredDelete)


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


@contextmanager
def managed_menu_bar(qapp, window):
    controller = MenuBarController(qapp, window)
    window.menu_bar_resident = True
    try:
        yield controller
    finally:
        qapp.removeEventFilter(controller)
        qapp.aboutToQuit.disconnect(controller.tray.hide)
        window.controls_changed.disconnect(controller.update_menu)
        window.menu_bar_resident = False
        controller.tray.hide()
        controller.deleteLater()
        qapp.sendPostedEvents(controller, QEvent.Type.DeferredDelete)


def test_menu_bar_controls_work_while_window_stays_hidden(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        with managed_menu_bar(qapp, window) as controller:
            assert not window.isVisible()
            assert controller.start_action.isEnabled()
            assert not controller.stop_action.isEnabled()

            controller.start_action.trigger()
            assert session.start_ports == [device.port]
            assert "Connecting" in controller.status_action.text()
            assert not controller.start_action.isEnabled()
            assert not controller.rescan_action.isEnabled()
            assert controller.stop_action.isEnabled()

            session.connect_successfully()
            assert "Connected" in controller.status_action.text()
            controller.stop_action.trigger()
            assert session.stop_count == 1
            session.finish_cleanly()
            assert controller.start_action.isEnabled()
            assert controller.rescan_action.isEnabled()
            assert not window.isVisible()


def test_menu_bar_rescan_and_failure_remain_accessible_without_window(qapp):
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    responses = iter(([], [device]))
    session = FakeSession()
    with managed_window(qapp, discover=lambda: next(responses), session=session) as window:
        with managed_menu_bar(qapp, window) as controller:
            assert not controller.start_action.isEnabled()
            assert controller.detail_action.text() == NO_DEVICE_MESSAGE
            controller.rescan_action.trigger()
            assert controller.start_action.isEnabled()
            controller.start_action.trigger()
            session.fail("Serial device is already in use")
            assert "Error" in controller.status_action.text()
            assert controller.detail_action.text() == "Serial device is already in use"
            assert "Serial device is already in use" in controller.tray.toolTip()
            assert controller.start_action.isEnabled()
            assert not window.isVisible()


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_close_hides_controls_and_preserves_active_menu_bar_session(
    qapp, monkeypatch, connected, platform_name,
):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        with managed_menu_bar(qapp, window) as controller:
            controller.show_action.trigger()
            window.start_bridge()
            if connected:
                session.connect_successfully()
            assert window.isVisible()
            assert not window.close()
            assert not window.isVisible()
            assert session.is_active
            assert session.stop_count == 0
            controller.show_action.trigger()
            assert window.isVisible()
            assert session.start_ports == [device.port]


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_menu_bar_quit_cleans_session_before_application_exit(
    qapp, monkeypatch, connected, platform_name,
):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    state_at_quit = []
    monkeypatch.setattr(qapp, "quit", lambda: state_at_quit.append(session.is_active))
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        with managed_menu_bar(qapp, window) as controller:
            window.start_bridge()
            if connected:
                session.connect_successfully()
            controller.quit_action.trigger()
            controller.quit()
            assert state_at_quit == [False]
            assert session.shutdown_count == 1
            assert session.stop_count == 1
            assert not window.menu_bar_resident
            assert window.close()


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_application_quit_event_cleans_hidden_menu_bar_session(qapp, monkeypatch, platform_name):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    device = make_device("/dev/cu.usbserial-LF123456", "LF123456")
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [device], session=session) as window:
        with managed_menu_bar(qapp, window) as controller:
            window.start_bridge()
            assert not controller.eventFilter(qapp, QEvent(QEvent.Type.Quit))
            assert not session.is_active
            assert not window.menu_bar_resident
            assert session.shutdown_count == 1


@pytest.mark.parametrize(
    "platform_name, tray_available",
    [("darwin", False), ("win32", False), ("linux", True)],
)
def test_desktop_window_fallback(qapp, monkeypatch, platform_name, tray_available):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: tray_available)
    with managed_window(qapp, discover=lambda: [], session=FakeSession()) as window:
        try:
            assert configure_desktop(qapp, window) is None
            assert window.isVisible()
            assert not window.menu_bar_resident
            assert qapp.quitOnLastWindowClosed()
        finally:
            qapp.setQuitOnLastWindowClosed(False)


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
def test_desktop_launches_with_accessible_menu_bar_or_tray(qapp, monkeypatch, platform_name):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    with managed_window(qapp, discover=lambda: [], session=FakeSession()) as window:
        controller = configure_desktop(qapp, window)
        try:
            assert controller is not None
            assert controller.tray.isVisible()
            assert controller.tray.icon().isMask() == (platform_name == "darwin")
            assert not controller.tray.icon().isNull()
            assert window.isVisible() == (platform_name == "win32")
            assert window.menu_bar_resident
            assert not qapp.quitOnLastWindowClosed()
            assert not window.close()
            assert not window.isVisible()
            assert controller.tray.isVisible()
            controller.show_action.trigger()
            assert window.isVisible()
        finally:
            if controller is not None:
                qapp.removeEventFilter(controller)
                qapp.aboutToQuit.disconnect(controller.tray.hide)
                window.controls_changed.disconnect(controller.update_menu)
                controller.tray.hide()
                controller.deleteLater()
                qapp.sendPostedEvents(controller, QEvent.Type.DeferredDelete)
            window.menu_bar_resident = False


@pytest.mark.parametrize("platform_name", ["darwin", "win32"])
@pytest.mark.parametrize(
    "reason",
    [
        QSystemTrayIcon.ActivationReason.Trigger,
        QSystemTrayIcon.ActivationReason.DoubleClick,
        QSystemTrayIcon.ActivationReason.Context,
    ],
)
def test_tray_activation_uses_platform_conventions(qapp, monkeypatch, platform_name, reason):
    monkeypatch.setattr("lf_usb_bridge.gui.sys.platform", platform_name)
    with managed_window(qapp, discover=lambda: [], session=FakeSession()) as window:
        with managed_menu_bar(qapp, window) as controller:
            controller.tray.activated.emit(reason)
            should_show = platform_name == "win32" and reason != QSystemTrayIcon.ActivationReason.Context
            assert window.isVisible() == should_show
            if should_show:
                window.showMinimized()
                controller.tray.activated.emit(reason)
                assert not window.isMinimized()
                assert window.isVisible()


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


def test_serial_ports_dialog_reports_exclusions_without_selecting(qapp):
    from lf_usb_bridge.serial_devices_dialog import SerialDevicesDialog

    shared = SerialDevice("COM7", "USB Serial Port (COM7)", "FTDI", None, None,
                          0x0403, 0x6015, hwid="USB VID:PID=0403:6015")
    router = SerialDevice("COM8", "FAMC LR Series", "FAMC", None, None, 0x0403, 0x87C1)
    dialog = SerialDevicesDialog(scan=lambda: [shared, router])
    try:
        assert dialog.port_selector.count() == 2
        assert dialog.selected_device is None
        assert not dialog.use_button.isEnabled()
        dialog.accept()
        assert dialog.result() == 0
        details = dialog.details.toPlainText()
        assert "COM7" in details and "COM8" in details
        assert "0403:6015" in details
        assert "LF-prefixed" in details
        assert "not verified" in details
        assert "Hardware ID: USB VID:PID=0403:6015" in details
        dialog.port_selector.setCurrentIndex(0)
        assert dialog.use_button.isEnabled()
        dialog.use_button.click()
        assert dialog.selected_device == shared
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_serial_ports_dialog_clears_stale_ports_after_scan_failure(qapp):
    from lf_usb_bridge.serial_devices_dialog import SerialDevicesDialog

    device = make_device("COM7", "LF123456")
    responses = iter(([device], RuntimeError("USB enumeration unavailable"), []))

    def scan():
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    dialog = SerialDevicesDialog(scan=scan)
    try:
        dialog.port_selector.setCurrentIndex(0)
        dialog.rescan_button.click()
        assert dialog.selected_device is None
        assert dialog.devices == []
        assert not dialog.use_button.isEnabled()
        assert "USB enumeration unavailable" in dialog.details.toPlainText()
        dialog.rescan_button.click()
        assert "No serial ports" in dialog.details.toPlainText()
        assert "COM port" in dialog.details.toPlainText()
    finally:
        dialog.close()


def test_main_window_recognises_stock_windows_lf_without_text_identity(qapp):
    device = SerialDevice("COM7", "USB Serial Port", None, None, None, 0x0403, 0x87C0)
    with managed_window(qapp, discover=lambda: [device], session=FakeSession()) as window:
        assert window.selected_device == device
        assert window.start_button.isEnabled()


def test_setup_manual_port_selection_requires_start_and_survives_rescan(qapp, monkeypatch):
    from lf_usb_bridge.serial_devices_dialog import SerialDevicesDialog

    generic = SerialDevice("COM7", "USB Serial Port", "FTDI", None, None, 0x0403, 0x6015)
    available = [generic]
    session = FakeSession()

    def choose_port(dialog):
        assert dialog.selected_device is None
        dialog.port_selector.setCurrentIndex(0)
        dialog.use_button.click()
        return dialog.result()

    monkeypatch.setattr(SetupDialog, "exec", lambda dialog: 2)
    monkeypatch.setattr(SerialDevicesDialog, "exec", choose_port)
    with managed_window(qapp, discover=lambda: available, session=session) as window:
        assert window.selected_device is None
        window.show_setup_dialog()
        assert window.selected_device == generic
        assert "manual" in window.device_selector.currentText()
        assert session.start_ports == []
        # Even a queued launch callback must not open this unverified port.
        window.auto_connect_checkbox.blockSignals(True)
        window.auto_connect_checkbox.setChecked(True)
        window.auto_connect_checkbox.blockSignals(False)
        window._auto_connect_if_possible()
        qapp.processEvents()
        assert session.start_ports == []
        window.refresh_devices()
        assert window.selected_device == generic
        window.start_button.click()
        assert session.start_ports == ["COM7"]
        session.finish_cleanly()
        available.clear()
        window.refresh_devices()
        assert window.selected_device is None
        assert not window.start_button.isEnabled()
        available.append(generic)
        window.refresh_devices()
        assert window.selected_device is None


def test_cancelling_manual_selection_keeps_automatic_selection(qapp, monkeypatch):
    from lf_usb_bridge.serial_devices_dialog import SerialDevicesDialog

    known = make_device("COM8", "LF123456")
    generic = SerialDevice("COM7", "USB Serial Port", "FTDI", None, None, 0x0403, 0x6015)

    def cancel(dialog):
        dialog.port_selector.setCurrentIndex(1)
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(SerialDevicesDialog, "exec", cancel)
    with managed_window(qapp, discover=lambda: [generic, known], session=FakeSession()) as window:
        assert window.device_selector.count() == 1
        window.show_serial_ports_dialog()
        assert window.selected_device == known
        assert window.device_selector.count() == 1


def test_serial_ports_cannot_be_changed_during_connection(qapp, monkeypatch):
    from lf_usb_bridge.serial_devices_dialog import SerialDevicesDialog

    def unexpected_dialog(dialog):
        raise AssertionError("must not allow port changes while connecting")

    monkeypatch.setattr(SerialDevicesDialog, "exec", unexpected_dialog)
    session = FakeSession()
    with managed_window(qapp, discover=lambda: [make_device("COM7", "LF123456")],
                        session=session) as window:
        window.start_bridge()
        window.show_serial_ports_dialog()
        assert session.start_ports == ["COM7"]
