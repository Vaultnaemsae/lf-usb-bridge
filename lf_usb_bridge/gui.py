"""PySide6 macOS application shell for the LF+ USB Bridge."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path

from PySide6.QtCore import (
    QEventLoop,
    QEvent,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSettings,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .device_discovery import SerialDevice, compatible_devices
from .gui_worker import BridgeWorker, concise_error_message
from .midi_ports import (
    MACOS_VIRTUAL_PORT_NAME,
    WINDOWS_INPUT_ENDPOINT_NAME,
    WINDOWS_OUTPUT_ENDPOINT_NAME,
)
logger = logging.getLogger(__name__)

APP_NAME = "LF+ USB Bridge"
NO_DEVICE_MESSAGE = "No compatible LF+ device found"
ICON_PATH = Path("assets/app-icon/LF_USB_Bridge_1024.png")
READY_MESSAGE = "Ready to connect to a Liquid Foot+ device."
CONNECTING_MESSAGE = "Connecting to Liquid Foot+ device…"
CONNECTED_MESSAGE = "Connected to Liquid Foot+ device."
EDITOR_REPOSITORY_URL = (
    "https://github.com/sungle-spec/famc-liquid-foot-usb-editor"
)


class ConnectionState(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ERROR = "Error"


STATUS_COLOURS = {
    ConnectionState.DISCONNECTED: (QColor("#B3261E"), QColor("#FF6961")),
    ConnectionState.CONNECTING: (QColor("#FFB000"), QColor("#FFB000")),
    ConnectionState.CONNECTED: (QColor("#20C66B"), QColor("#20C66B")),
    ConnectionState.ERROR: (QColor("#B3261E"), QColor("#FF6961")),
}


class ElidingComboBox(QComboBox):
    """Draw the selected device with native middle elision when space is tight."""

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)

        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        option.currentText = self.fontMetrics().elidedText(
            option.currentText,
            Qt.TextElideMode.ElideMiddle,
            text_rect.width(),
        )
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class BridgeSession(QObject):
    """Coordinate one worker and QThread from the GUI thread."""

    state_changed = Signal(str)
    status_changed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        worker_factory: Callable[[str], BridgeWorker] = BridgeWorker,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._thread: QThread | None = None
        self._worker: BridgeWorker | None = None
        self._state = ConnectionState.DISCONNECTED
        self._had_error = False
        self._stop_requested = False
        self._worker_signals_enabled = False

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._thread is not None

    def start(self, serial_port: str) -> bool:
        """Start one session, rejecting duplicate start requests."""

        if self.is_active:
            return False

        try:
            worker = self._worker_factory(serial_port)
        except Exception as exc:
            logger.exception("Could not create LF+ bridge worker")
            self._had_error = True
            self._set_state(ConnectionState.ERROR)
            self.status_changed.emit(concise_error_message(exc))
            return False

        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.connected.connect(self._on_connected)
        worker.failed.connect(self._on_failed)

        # Ownership and teardown are intentionally linear:
        #
        # 1. BridgeWorker finishes bridge/MIDI/serial cleanup on its QThread.
        # 2. Its finished signal queues deleteLater back to that same thread.
        # 3. QObject destruction, still on the worker thread, requests quit.
        # 4. QThread.finished reaches this GUI-thread session.
        # 5. The QThread QObject is deferred-deleted on the GUI thread.
        # 6. Only QThread.destroyed releases the final session references.
        #
        # Quitting on worker.finished would let the event loop exit before the
        # deferred worker deletion was processed.
        worker.finished.connect(
            worker.deleteLater,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.destroyed.connect(
            thread.quit,
            Qt.ConnectionType.DirectConnection,
        )
        thread.finished.connect(self._on_thread_finished)
        thread.destroyed.connect(self._on_thread_destroyed)

        self._worker = worker
        self._thread = thread
        self._had_error = False
        self._stop_requested = False
        self._worker_signals_enabled = True
        self._set_state(ConnectionState.CONNECTING)
        self.status_changed.emit(CONNECTING_MESSAGE)
        thread.start()
        return True

    def stop(self) -> bool:
        """Request cooperative worker shutdown."""

        worker = self._worker
        if worker is None:
            return False

        if self._stop_requested:
            return True

        self._stop_requested = True
        self._worker_signals_enabled = False
        worker.request_stop()
        self.status_changed.emit("Stopping connection")
        return True

    def shutdown(
        self,
        timeout_ms: int | None = None,
    ) -> bool:
        """Stop and wait for the worker thread during application termination."""

        thread = self._thread
        if thread is None:
            return True

        self.stop()

        # Keep the GUI event queue alive so QThread.finished, deferred QThread
        # deletion, and QThread.destroyed all run through the same normal path.
        wait_loop = QEventLoop(self)
        timeout_timer: QTimer | None = None
        self.finished.connect(wait_loop.quit)

        if timeout_ms is not None:
            timeout_timer = QTimer(wait_loop)
            timeout_timer.setSingleShot(True)
            timeout_timer.timeout.connect(wait_loop.quit)
            timeout_timer.start(timeout_ms)

        if self.is_active:
            wait_loop.exec()

        try:
            self.finished.disconnect(wait_loop.quit)
        except RuntimeError:
            pass
        wait_loop.deleteLater()

        stopped = not self.is_active
        if not stopped:
            logger.error("LF+ bridge worker did not exit before timeout")
        return stopped

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        self.state_changed.emit(state.value)

    @Slot(str)
    def _on_connected(self, serial_port: str) -> None:
        if not self._accept_worker_signal():
            return

        self._set_state(ConnectionState.CONNECTED)
        self.status_changed.emit(CONNECTED_MESSAGE)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if not self._accept_worker_signal():
            return

        self._worker_signals_enabled = False
        self._had_error = True
        self._set_state(ConnectionState.ERROR)
        self.status_changed.emit(message)

    def _accept_worker_signal(self) -> bool:
        return (
            self._worker_signals_enabled
            and self._thread is not None
        )

    @Slot()
    def _on_thread_finished(self) -> None:
        # The worker C++ object has already been destroyed safely in its own
        # thread. Keep the Python wrapper until this signal is delivered, then
        # release it. Keep the QThread wrapper until QThread.destroyed.
        self._worker = None
        thread = self._thread
        if thread is not None:
            thread.deleteLater()

    @Slot()
    def _on_thread_destroyed(self) -> None:
        self._thread = None
        self._worker = None
        self._worker_signals_enabled = False
        self._stop_requested = False

        if not self._had_error:
            self._set_state(ConnectionState.DISCONNECTED)
            self.status_changed.emit("Connection stopped")

        self.finished.emit()


class SetupDialog(QDialog):
    """Show the two prerequisites for using the standalone bridge."""

    def __init__(
        self,
        *,
        device: SerialDevice | None,
        platform_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._platform_name = platform_name or sys.platform
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.setModal(True)
        self.resize(650, 470)
        self.setMinimumSize(600, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(8)

        heading = QLabel("Setup Information")
        heading.setObjectName("setupHeading")
        heading_font = QFont(self.font())
        heading_font.setWeight(QFont.Weight.Bold)
        heading_font.setPointSizeF(heading_font.pointSizeF() + 1.5)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        usb_header = QHBoxLayout()
        usb_header.setContentsMargins(0, 0, 0, 0)
        usb_header.setSpacing(6)
        usb_header.addWidget(self._section_heading("Your USB serial connection is:"))

        if device is None:
            status_text = "Not Available"
            status_colour = "#FF6961"
            detail_rows = ()
            guidance_text = (
                "No compatible LF+ serial device was found. LF+ USB Bridge "
                "does not modify the FTDI EEPROM; use Device Connection Setup "
                "in Sung Le’s editor to enable USB serial mode."
            )
        else:
            serial_number = (device.serial_number or "Unknown").strip()
            pid_text = f"0x{device.pid:04X}" if device.pid is not None else "Unknown"
            status_text = "Ready"
            status_colour = "#20C66B"
            detail_rows = (
                ("Serial", serial_number),
                ("Port", device.port),
                ("USB product ID", pid_text),
            )
            guidance_text = (
                "LF+ USB Bridge requires the FAMC Liquid Foot+'s FTDI chip to be modified using Sung Le's Device Connection Setup Tool. The process is safe and reversible."
            )

        self.device_status_label = QLabel(status_text)
        self.device_status_label.setObjectName("setupDeviceStatus")
        status_font = QFont(self.font())
        status_font.setWeight(QFont.Weight.DemiBold)
        self.device_status_label.setFont(status_font)
        self.device_status_label.setStyleSheet(f"color: {status_colour};")
        self.device_status_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        usb_header.addWidget(self.device_status_label)
        usb_header.addStretch(1)
        layout.addLayout(usb_header)

        device_grid = QGridLayout()
        device_grid.setObjectName("setupDeviceGrid")
        device_grid.setContentsMargins(0, 2, 0, 0)
        device_grid.setHorizontalSpacing(16)
        device_grid.setVerticalSpacing(3)
        device_grid.setColumnMinimumWidth(0, 112)
        device_grid.setColumnStretch(1, 1)

        detail_text_parts: list[str] = []
        for row, (name, value) in enumerate(detail_rows):
            name_label = QLabel(name)
            name_label.setObjectName(f"setupDeviceDetailName{row}")
            name_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            value_label = QLabel(value)
            value_label.setObjectName(f"setupDeviceDetailValue{row}")
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            device_grid.addWidget(name_label, row, 0)
            device_grid.addWidget(value_label, row, 1)
            detail_text_parts.append(f"{name}: {value}")
        layout.addLayout(device_grid)

        self.device_details_label = QLabel("\n".join(detail_text_parts), self)
        self.device_details_label.setObjectName("setupDeviceDetails")
        self.device_details_label.hide()

        safety_note = QLabel(guidance_text)
        safety_note.setObjectName("setupDeviceSafetyNote")
        safety_note.setWordWrap(True)
        layout.addWidget(safety_note)

        self.device_reference_label = QLabel(
            f'<a href="{EDITOR_REPOSITORY_URL}">'
            "Get Sung Le’s FAMC Liquid Foot+ Editor on GitHub →</a>"
        )
        self.device_reference_label.setObjectName("setupDeviceReference")
        self.device_reference_label.setTextFormat(Qt.TextFormat.RichText)
        self.device_reference_label.setOpenExternalLinks(True)
        self.device_reference_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.device_reference_label.setWordWrap(True)
        layout.addWidget(self.device_reference_label)

        tool_path = QLabel("Under the Hardware menu, select Device Connection Setup.")
        tool_path.setObjectName("setupDeviceToolPath")
        tool_path_font = QFont(self.font())
        tool_path_font.setPointSizeF(max(8.0, tool_path_font.pointSizeF() - 0.5))
        tool_path.setFont(tool_path_font)
        layout.addWidget(tool_path)

        layout.addSpacing(4)
        layout.addWidget(self._separator())
        layout.addSpacing(4)

        if self._is_windows:
            midi_heading_text = "Windows MIDI ports"
            midi_intro_text = (
                "Create these exact port names with loopMIDI or Windows MIDI "
                "Services, then click Rescan in the main window. Once created, "
                "they will appear in compatible MIDI applications."
            )
            input_endpoint_name = WINDOWS_INPUT_ENDPOINT_NAME
            output_endpoint_name = WINDOWS_OUTPUT_ENDPOINT_NAME
        else:
            midi_heading_text = "Virtual MIDI ports"
            midi_intro_text = (
                "These ports are created automatically when the bridge starts "
                "and will appear in compatible MIDI applications."
            )
            input_endpoint_name = MACOS_VIRTUAL_PORT_NAME
            output_endpoint_name = MACOS_VIRTUAL_PORT_NAME

        midi_header = QHBoxLayout()
        midi_header.setContentsMargins(0, 0, 0, 0)
        midi_header.addWidget(self._section_heading(midi_heading_text))
        midi_header.addStretch(1)
        layout.addLayout(midi_header)

        self.midi_setup_label = QLabel(midi_intro_text)
        self.midi_setup_label.setObjectName("setupMidiInstructions")
        self.midi_setup_label.setWordWrap(True)
        layout.addWidget(self.midi_setup_label)

        endpoint_grid = QGridLayout()
        endpoint_grid.setObjectName("setupMidiGrid")
        endpoint_grid.setContentsMargins(0, 2, 0, 0)
        endpoint_grid.setHorizontalSpacing(16)
        endpoint_grid.setVerticalSpacing(3)
        endpoint_grid.setColumnMinimumWidth(0, 150)
        endpoint_grid.setColumnStretch(1, 1)

        endpoint_font = QFont(self.font())
        endpoint_font.setWeight(QFont.Weight.DemiBold)

        input_name = QLabel(input_endpoint_name)
        input_name.setObjectName("setupMidiInputName")
        input_name.setFont(endpoint_font)
        input_name.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        input_direction = QLabel("Computer → Liquid Foot+")
        input_direction.setObjectName("setupMidiInputDirection")

        output_name = QLabel(output_endpoint_name)
        output_name.setObjectName("setupMidiOutputName")
        output_name.setFont(endpoint_font)
        output_name.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        output_direction = QLabel("Liquid Foot+ → Computer")
        output_direction.setObjectName("setupMidiOutputDirection")

        endpoint_grid.addWidget(input_name, 0, 0)
        endpoint_grid.addWidget(input_direction, 0, 1)
        endpoint_grid.addWidget(output_name, 1, 0)
        endpoint_grid.addWidget(output_direction, 1, 1)
        layout.addLayout(endpoint_grid)

        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 4, 0, 0)
        footer.addStretch(1)
        close_button = QPushButton("Close")
        close_button.setObjectName("setupCloseButton")
        close_button.setMinimumWidth(82)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    def _section_heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("setupSectionHeading")
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        return label

    def _separator(self) -> QFrame:
        separator = QFrame()
        separator.setObjectName("setupSectionSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        return separator

    @property
    def _is_windows(self) -> bool:
        return self._platform_name.startswith("win")


class MainWindow(QMainWindow):
    """Compact single-window bridge controls and status."""

    def __init__(
        self,
        *,
        discover: Callable[[], list[SerialDevice]] = compatible_devices,
        session: BridgeSession | None = None,
    ) -> None:
        super().__init__()
        self._discover = discover
        self._session = session or BridgeSession(parent=self)
        self._state = ConnectionState.DISCONNECTED
        self._devices: list[SerialDevice] = []
        self._close_pending = False
        self._settings = QSettings("Vaultnaemsae", APP_NAME)
        self._preferred_device_serial = self._settings.value(
            "preferredDeviceSerial",
            "",
            type=str,
        )

        self.setWindowTitle(APP_NAME)
        self.resize(380, 222)
        self.setMinimumSize(350, 210)
        self._build_ui()

        self._session.state_changed.connect(self._set_connection_state)
        self._session.status_changed.connect(self._set_status)
        self._session.finished.connect(self._on_session_finished)

        self.refresh_devices()
        QTimer.singleShot(0, self._auto_connect_if_possible)

    @property
    def connection_state(self) -> ConnectionState:
        return self._state

    @property
    def selected_device(self) -> SerialDevice | None:
        device = self.device_selector.currentData(Qt.ItemDataRole.UserRole)
        return device if isinstance(device, SerialDevice) else None

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(7)

        connection_group = QGroupBox(central)
        connection_group.setObjectName("connectionGroup")
        connection_layout = QFormLayout(connection_group)
        connection_layout.setContentsMargins(10, 9, 10, 9)
        connection_layout.setHorizontalSpacing(8)
        connection_layout.setVerticalSpacing(5)
        connection_layout.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.DontWrapRows
        )
        connection_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        connection_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.state_label = QLabel(ConnectionState.DISCONNECTED.value)
        self.state_label.setObjectName("stateLabel")
        state_font = QFont(self.font())
        state_font.setWeight(QFont.Weight.Bold)
        self.state_label.setFont(state_font)

        self._state_opacity_effect = QGraphicsOpacityEffect(self.state_label)
        self.state_label.setGraphicsEffect(self._state_opacity_effect)
        self._state_pulse_animation = QPropertyAnimation(
            self._state_opacity_effect,
            b"opacity",
            self,
        )
        self._state_pulse_animation.setDuration(900)
        self._state_pulse_animation.setStartValue(1.0)
        self._state_pulse_animation.setEndValue(0.55)
        self._state_pulse_animation.setEasingCurve(
            QEasingCurve.Type.InOutSine
        )
        self._state_pulse_animation.setLoopCount(-1)

        self.device_container = QWidget(connection_group)
        self.device_container.setObjectName("deviceContainer")
        device_layout = QHBoxLayout(self.device_container)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(0)

        self.device_label = QLabel(NO_DEVICE_MESSAGE, self.device_container)
        self.device_label.setObjectName("deviceLabel")
        self.device_label.setWordWrap(False)
        self.device_label.setTextFormat(Qt.TextFormat.PlainText)
        self.device_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.device_selector = ElidingComboBox(self.device_container)
        self.device_selector.setObjectName("deviceSelector")
        self.device_selector.setAccessibleDescription("Selected LF+ device")
        self.device_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.device_selector.setMinimumContentsLength(0)
        self.device_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.device_selector.view().setTextElideMode(
            Qt.TextElideMode.ElideMiddle
        )
        self.device_selector.currentIndexChanged.connect(
            self._on_device_selection_changed
        )
        device_layout.addWidget(self.device_label)
        device_layout.addWidget(self.device_selector)

        if sys.platform.startswith("win"):
            midi_input_name = WINDOWS_INPUT_ENDPOINT_NAME
            midi_output_name = WINDOWS_OUTPUT_ENDPOINT_NAME
        else:
            midi_input_name = MACOS_VIRTUAL_PORT_NAME
            midi_output_name = MACOS_VIRTUAL_PORT_NAME

        self.midi_input_label = QLabel(midi_input_name)
        self.midi_input_label.setObjectName("midiInputLabel")
        self.midi_output_label = QLabel(midi_output_name)
        self.midi_output_label.setObjectName("midiOutputLabel")

        value_font = QFont(self.font())
        for value_widget in (
            self.device_label,
            self.device_selector,
            self.midi_input_label,
            self.midi_output_label,
        ):
            value_widget.setFont(value_font)

        connection_layout.addRow("Status:", self.state_label)
        connection_layout.addRow("Device:", self.device_container)
        connection_layout.addRow("MIDI Input:", self.midi_input_label)
        connection_layout.addRow("MIDI Output:", self.midi_output_label)

        auto_connect_row = QWidget(connection_group)
        auto_connect_row.setObjectName("autoConnectRow")
        auto_connect_layout = QHBoxLayout(auto_connect_row)
        auto_connect_layout.setContentsMargins(0, 5, 0, 1)
        auto_connect_layout.setSpacing(0)

        self.auto_connect_checkbox = QCheckBox("Auto-connect on launch")
        self.auto_connect_checkbox.setObjectName("autoConnectCheckbox")
        self.auto_connect_checkbox.setChecked(
            self._settings.value("autoConnect", False, type=bool)
        )
        self.auto_connect_checkbox.toggled.connect(
            self._on_auto_connect_toggled
        )
        auto_connect_layout.addWidget(self.auto_connect_checkbox)
        auto_connect_layout.addStretch()
        connection_layout.addRow(auto_connect_row)
        connection_group.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        outer_layout.addWidget(connection_group)

        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        self.status_label.hide()
        outer_layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        self.setup_button = QPushButton("Setup…")
        self.setup_button.setObjectName("setupButton")
        self.setup_button.setAccessibleName("Open bridge setup")
        self.setup_button.setToolTip("Show device and MIDI endpoint setup requirements")
        self.refresh_button = QPushButton("Rescan")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setAccessibleName("Rescan devices")
        self.refresh_button.setToolTip("Rescan for compatible LF+ devices")
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("startButton")
        self.start_button.setDefault(True)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")

        for button in (
            self.setup_button,
            self.refresh_button,
            self.start_button,
            self.stop_button,
        ):
            button.setFixedWidth(76)
            button.setMinimumHeight(30)

        self.setup_button.clicked.connect(self.show_setup_dialog)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.start_button.clicked.connect(self.start_bridge)
        self.stop_button.clicked.connect(self.stop_bridge)

        button_layout.addWidget(self.setup_button)
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        outer_layout.addLayout(button_layout)

        self.setCentralWidget(central)
        self._apply_button_styles()
        self._apply_information_palette()
        self._apply_status_colour()
        self._update_status_pulse()
        self._update_device_presentation()
        self._update_controls()

    @Slot()
    def show_setup_dialog(self) -> None:
        dialog = SetupDialog(device=self.selected_device, parent=self)
        dialog.exec()

    @Slot()
    def refresh_devices(self) -> None:
        """Re-run compatible-device discovery without auto-connecting."""

        if self._session.is_active:
            return

        previous_port = (
            self.selected_device.port
            if self.selected_device is not None
            else None
        )

        discovery_error: str | None = None
        try:
            devices = list(self._discover())
        except Exception as exc:
            logger.exception("LF+ device discovery failed")
            devices = []
            discovery_error = concise_error_message(exc)

        self._devices = devices
        self.device_selector.blockSignals(True)
        self.device_selector.clear()

        selected_index = 0
        preferred_serial = self._preferred_device_serial.strip()

        for index, device in enumerate(devices):
            description = device.description.strip()
            serial_number = (device.serial_number or "").strip()

            if description and description.lower() != "n/a":
                label = (
                    f"{description} — {serial_number}"
                    if serial_number
                    else f"{description} — {device.port}"
                )
            else:
                label = serial_number or device.port

            self.device_selector.addItem(label, device)

            if preferred_serial and serial_number == preferred_serial:
                selected_index = index
            elif not preferred_serial and device.port == previous_port:
                selected_index = index

        if devices:
            self.device_selector.setCurrentIndex(selected_index)
            self._update_device_tooltip()
            if self._state is ConnectionState.ERROR:
                self._set_connection_state(
                    ConnectionState.DISCONNECTED.value
                )
            self._set_status(READY_MESSAGE)
        elif discovery_error is not None:
            self._set_connection_state(ConnectionState.ERROR.value)
            self._set_status(discovery_error)
        else:
            self._set_connection_state(ConnectionState.DISCONNECTED.value)
            self._set_status(NO_DEVICE_MESSAGE)

        self.device_selector.blockSignals(False)
        self._update_device_presentation()
        self._update_controls()

    @Slot()
    def _on_device_selection_changed(self) -> None:
        device = self.selected_device
        if device is not None:
            self._preferred_device_serial = (
                device.serial_number or ""
            ).strip()
            self._settings.setValue(
                "preferredDeviceSerial",
                self._preferred_device_serial,
            )
            self._settings.sync()

        self._update_device_tooltip()
        self._update_device_presentation()
        self._update_controls()

    @Slot(bool)
    def _on_auto_connect_toggled(self, enabled: bool) -> None:
        self._settings.setValue("autoConnect", enabled)
        self._settings.sync()

    @Slot()
    def _auto_connect_if_possible(self) -> None:
        if self._close_pending:
            return

        if not self.auto_connect_checkbox.isChecked():
            return

        if self._session.is_active:
            return

        if self.selected_device is None:
            return

        self.start_bridge()

    def _update_device_tooltip(self) -> None:
        device = self.selected_device
        if device is None:
            self.device_selector.setToolTip("")
            return

        description = device.description.strip()
        serial_number = (device.serial_number or "").strip()
        tooltip_lines = []

        if description and description.lower() != "n/a":
            tooltip_lines.append(description)
        if serial_number:
            tooltip_lines.append(f"Serial: {serial_number}")
        tooltip_lines.append(f"Port: {device.port}")

        self.device_selector.setToolTip("\n".join(tooltip_lines))

    @Slot()
    def start_bridge(self) -> None:
        device = self.selected_device
        if device is None:
            return

        self._preferred_device_serial = (
            device.serial_number or ""
        ).strip()
        self._settings.setValue(
            "preferredDeviceSerial",
            self._preferred_device_serial,
        )
        self._settings.sync()

        self._session.start(device.port)
        self._update_controls()

    @Slot()
    def stop_bridge(self) -> None:
        self._session.stop()
        self._update_controls()

    @Slot(str)
    def _set_connection_state(self, state: str) -> None:
        if self._close_pending:
            return

        self._state = ConnectionState(state)
        self.state_label.setText(self._state.value)
        self._apply_status_colour()
        self._update_status_pulse()
        self._update_device_presentation()
        if self._state is not ConnectionState.ERROR:
            self.status_label.hide()
        self._update_controls()

    @Slot(str)
    def _set_status(self, message: str) -> None:
        if self._close_pending:
            return

        self.status_label.setText(message)
        self.status_label.setVisible(
            self._state is ConnectionState.ERROR and bool(message)
        )

    @Slot()
    def _on_session_finished(self) -> None:
        if self._close_pending:
            QTimer.singleShot(0, self.close)
            return

        self._update_controls()

    def _update_controls(self) -> None:
        active = self._session.is_active
        has_device = self.selected_device is not None
        can_start = (
            has_device
            and not active
            and self._state
            in (ConnectionState.DISCONNECTED, ConnectionState.ERROR)
        )
        can_stop = (
            active
            and self._state
            in (ConnectionState.CONNECTING, ConnectionState.CONNECTED)
        )

        self.start_button.setEnabled(can_start)
        self.stop_button.setEnabled(can_stop)
        self.setup_button.setEnabled(not active)
        self.refresh_button.setEnabled(not active)
        self.device_selector.setEnabled(
            not active and self.device_selector.count() > 1
        )

    def _update_device_presentation(self) -> None:
        count = self.device_selector.count()
        use_selector = (
            count > 1
            and self._state not in (
                ConnectionState.CONNECTING,
                ConnectionState.CONNECTED,
            )
        )

        device = self.selected_device
        if device is None:
            display_text = NO_DEVICE_MESSAGE
        else:
            display_text = self.device_selector.currentText()

        self.device_label.setText(display_text)
        self.device_label.setToolTip(self.device_selector.toolTip())
        self.device_label.setVisible(not use_selector)
        self.device_selector.setVisible(use_selector)

    def _apply_button_styles(self) -> None:
        is_dark = (
            self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        )
        if is_dark:
            disabled_background = "#34383B"
            disabled_foreground = "#AEB4B8"
            disabled_border = "#4A5054"
        else:
            disabled_background = "#E4E7E9"
            disabled_foreground = "#636A70"
            disabled_border = "#C5CACD"

        base = f"""
            QPushButton {{
                color: white;
                border: none;
                border-radius: 5px;
                padding: 4px 9px;
                font-weight: 600;
            }}
            QPushButton:disabled {{
                color: {disabled_foreground};
                background: {disabled_background};
                border: 1px solid {disabled_border};
            }}
        """
        self.setup_button.setStyleSheet(f"""
            QPushButton {{
                border-radius: 5px;
                padding: 4px 9px;
                font-weight: 600;
                color: {"#F2F3F4" if is_dark else "#25282A"};
                background: {"#444A4E" if is_dark else "#E7EAEC"};
                border: 1px solid {"#596064" if is_dark else "#C7CCCF"};
            }}
            QPushButton:hover {{
                background: {"#50575B" if is_dark else "#DDE1E3"};
            }}
            QPushButton:pressed {{
                background: {"#383D40" if is_dark else "#CED3D6"};
            }}
            QPushButton:disabled {{
                color: {disabled_foreground};
                background: {disabled_background};
                border: 1px solid {disabled_border};
            }}
        """)
        self.refresh_button.setStyleSheet(base + """
            QPushButton:enabled { background: #0A84FF; }
            QPushButton:enabled:hover { background: #3399FF; }
            QPushButton:enabled:pressed { background: #006EDC; }
        """)
        self.start_button.setStyleSheet(base + """
            QPushButton:enabled { background: #20C66B; }
            QPushButton:enabled:hover { background: #35D47C; }
            QPushButton:enabled:pressed { background: #159D52; }
        """)
        self.stop_button.setStyleSheet(base + """
            QPushButton:enabled { background: #E5484D; }
            QPushButton:enabled:hover { background: #EE5E63; }
            QPushButton:enabled:pressed { background: #BE3439; }
        """)

    def _update_status_pulse(self) -> None:
        should_pulse = self._state in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
        )
        if not should_pulse:
            self._state_pulse_animation.stop()
            self._state_opacity_effect.setOpacity(1.0)
            return

        if (
            self._state_pulse_animation.state()
            != QPropertyAnimation.State.Running
        ):
            self._state_pulse_animation.start()

    def _apply_status_colour(self) -> None:
        light_colour, dark_colour = STATUS_COLOURS[self._state]
        window_colour = self.palette().color(QPalette.ColorRole.Window)
        colour = dark_colour if window_colour.lightness() < 128 else light_colour
        palette = QPalette(self.state_label.palette())
        palette.setColor(QPalette.ColorRole.WindowText, colour)
        self.state_label.setPalette(palette)

    def _apply_information_palette(self) -> None:
        palette = QPalette(self.device_selector.palette())
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                role,
                palette.color(QPalette.ColorGroup.Active, role),
            )
        self.device_selector.setPalette(palette)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if hasattr(self, "state_label") and event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._apply_information_palette()
            self._apply_button_styles()
            self._apply_status_colour()

    def shutdown_for_quit(self) -> None:
        """Final blocking cleanup boundary for application-level quit."""

        self._session.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._session.is_active:
            self._close_pending = True
            self._session.stop()
            event.ignore()
            return

        self._state_pulse_animation.stop()
        event.accept()


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lf-usb-bridge-gui",
        description="Launch the LF+ USB Bridge macOS application.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="construct and close the GUI offscreen, then exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the GUI or run its non-interactive smoke test."""

    args = create_argument_parser().parse_args(argv)
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    app = QApplication.instance()
    owns_application = app is None
    if app is None:
        app = QApplication([sys.argv[0]])

    app.setApplicationName(APP_NAME)

    resource_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
    )

    app_icon = QIcon(str(resource_root / ICON_PATH))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    discover = (lambda: []) if args.smoke_test else compatible_devices
    window = MainWindow(discover=discover)

    if not app_icon.isNull():
        window.setWindowIcon(app_icon)

    app.aboutToQuit.connect(window.shutdown_for_quit)

    if args.smoke_test:
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        print("LF+ USB Bridge GUI smoke test passed")
        return 0

    window.show()
    if not owns_application:
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
