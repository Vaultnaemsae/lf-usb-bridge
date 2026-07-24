from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(["lf-usb-bridge-tests"])
    app.setQuitOnLastWindowClosed(False)
    yield app

    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
