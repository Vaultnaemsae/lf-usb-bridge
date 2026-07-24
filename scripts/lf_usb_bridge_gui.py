"""PyInstaller entry point for the LF+ USB Bridge macOS application."""

from lf_usb_bridge.gui import main


if __name__ == "__main__":
    raise SystemExit(main())
