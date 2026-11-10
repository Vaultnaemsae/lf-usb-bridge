"""Read-only serial discovery details and explicit manual port selection."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from .device_discovery import (
    SerialDevice, automatic_selection_issue, enumerate_devices, rank_devices,
)


class SerialDevicesDialog(QDialog):
    """List every visible serial port without opening any of them."""

    def __init__(
        self,
        *,
        scan: Callable[[], list[SerialDevice]] = enumerate_devices,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan
        self.devices: list[SerialDevice] = []
        self.setWindowTitle("Serial ports — LF+ USB Bridge")
        self.resize(650, 470)
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Choose the port belonging to your Liquid Foot+ if automatic discovery "
            "did not recognise it. Selecting a port does not connect; click Start "
            "in the main window when ready."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.port_selector = QComboBox()
        self.port_selector.setAccessibleName("Manual serial port")
        self.port_selector.setPlaceholderText("Choose a serial port…")
        layout.addWidget(self.port_selector)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Serial discovery details")
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.clicked.connect(self.rescan)
        buttons.addWidget(self.rescan_button)
        buttons.addStretch()
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        self.use_button = QPushButton("Use selected port")
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)
        self.port_selector.currentIndexChanged.connect(
            lambda: self.use_button.setEnabled(self.selected_device is not None)
        )
        self.rescan()

    @property
    def selected_device(self) -> SerialDevice | None:
        device = self.port_selector.currentData(Qt.ItemDataRole.UserRole)
        return device if isinstance(device, SerialDevice) else None

    def accept(self) -> None:
        if self.selected_device is not None:
            super().accept()

    def rescan(self) -> None:
        previous = self.selected_device
        self.port_selector.clear()
        self.devices = []
        self.use_button.setEnabled(False)
        try:
            self.devices = rank_devices(self._scan())
        except Exception as exc:
            self.details.setPlainText(f"Could not enumerate serial ports: {exc}")
            return
        reports = []
        selected_index = -1
        for index, device in enumerate(self.devices):
            issue = automatic_selection_issue(device)
            status = f"Manual selection only: {issue}" if issue else "Recognised LF+"
            self.port_selector.addItem(
                f"{device.port} — {device.description or 'Serial port'}", device
            )
            if previous is not None and device.port == previous.port:
                selected_index = index
            vid = f"{device.vid:04X}" if device.vid is not None else "Unknown"
            pid = f"{device.pid:04X}" if device.pid is not None else "Unknown"
            reports.append("\n".join((
                f"Port: {device.port}",
                status,
                f"VID:PID: {vid}:{pid}",
                f"Description: {device.description or 'Unknown'}",
                f"Manufacturer: {device.manufacturer or 'Unknown'}",
                f"Product: {device.product or 'Unknown'}",
                f"Serial: {device.serial_number or 'Unknown'}",
                f"Hardware ID: {device.hwid or 'Unknown'}",
            )))
        self.port_selector.setCurrentIndex(selected_index)
        self.use_button.setEnabled(self.selected_device is not None)
        self.details.setPlainText("\n\n".join(reports) or (
            "No serial ports are visible. On Windows, check Device Manager for "
            "a COM port and a working FTDI VCP driver for the device's hardware ID. "
            "A USB device without a COM port cannot be selected here."
        ))
