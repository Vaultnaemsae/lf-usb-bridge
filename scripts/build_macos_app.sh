#!/usr/bin/env bash

set -euo pipefail

APP_NAME="LF+ USB Bridge"
BUNDLE_ID="com.vaultnaemsae.lfusbbridge"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICON_PATH="$ROOT_DIR/assets/app-icon/LF_USB_Bridge.icns"
ENTRY_POINT="$ROOT_DIR/app.py"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
SPEC_FILE="$ROOT_DIR/build/pyinstaller/$APP_NAME.spec"
APP_PATH="$DIST_DIR/$APP_NAME.app"

CODESIGN_ARGS=()

if [[ -n "${DEVELOPER_ID_APPLICATION:-}" ]]; then
    CODESIGN_ARGS=(
        --codesign-identity "$DEVELOPER_ID_APPLICATION"
    )
fi

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
rm -rf "$BUILD_DIR" "$DIST_DIR" "$SPEC_FILE"

echo "Building $APP_NAME..."
.venv/bin/python -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "LF+ USB Bridge" \
    --specpath "$ROOT_DIR/build/pyinstaller" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --icon "$ICON_PATH" \
    --hidden-import "mido.backends.rtmidi" \
    --collect-all "rtmidi" \
    --hidden-import "serial.tools.list_ports" \
    ${CODESIGN_ARGS[@]+"${CODESIGN_ARGS[@]}"} \
    "$ENTRY_POINT"

echo
echo "Build complete:"
echo "$APP_PATH"
