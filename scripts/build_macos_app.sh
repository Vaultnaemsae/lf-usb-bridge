#!/usr/bin/env bash

set -euo pipefail

APP_NAME="LF+ USB Bridge"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICON_PATH="$ROOT_DIR/assets/app-icon/LF_USB_Bridge.icns"
ENTRY_POINT="$ROOT_DIR/app.py"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build/macos"
SPEC_FILE="$ROOT_DIR/scripts/lf_usb_bridge_macos.spec"
APP_PATH="$DIST_DIR/$APP_NAME.app"

cd "$ROOT_DIR"

if [[ ! -f "$ENTRY_POINT" ]]; then
    echo "Missing entry point: $ENTRY_POINT"
    exit 1
fi

if [[ ! -f "$ICON_PATH" ]]; then
    echo "Missing icon: $ICON_PATH"
    exit 1
fi

echo "Running tests..."
.venv/bin/python -m pytest

echo "Cleaning previous build..."
rm -rf "$BUILD_DIR" "$APP_PATH" "$DIST_DIR/$APP_NAME"

echo "Building $APP_NAME..."
.venv/bin/python -m PyInstaller \
    --noconfirm \
    --clean \
    --workpath "$BUILD_DIR" \
    --distpath "$DIST_DIR" \
    "$SPEC_FILE"

echo
echo "Build complete:"
echo "$APP_PATH"
