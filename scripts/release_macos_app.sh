#!/usr/bin/env bash

set -euo pipefail

APP_NAME="LF+ USB Bridge"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="$ROOT_DIR/dist/$APP_NAME.app"
RELEASE_DIR="$ROOT_DIR/release"
NOTARY_UPLOAD="$RELEASE_DIR/$APP_NAME-notarization.zip"
FINAL_ZIP="$RELEASE_DIR/$APP_NAME-macOS.zip"

# Required environment variables:
#   DEVELOPER_ID_APPLICATION
#     Example: Developer ID Application: Example Developer (TEAMID)
#
#   NOTARY_KEYCHAIN_PROFILE
#     Name created once with:
#     xcrun notarytool store-credentials "LFUSBBridgeNotary" \
#       --apple-id "YOUR_APPLE_ID" \
#       --team-id "YOUR_TEAM_ID" \
#       --password "APP_SPECIFIC_PASSWORD"

: "${DEVELOPER_ID_APPLICATION:?Set DEVELOPER_ID_APPLICATION to your Developer ID Application certificate name.}"
: "${NOTARY_KEYCHAIN_PROFILE:?Set NOTARY_KEYCHAIN_PROFILE to your notarytool keychain profile name.}"

cd "$ROOT_DIR"

echo "Building and signing $APP_NAME..."
DEVELOPER_ID_APPLICATION="$DEVELOPER_ID_APPLICATION" \
    "$ROOT_DIR/scripts/build_macos_app.sh"

if [[ ! -d "$APP_PATH" ]]; then
    echo "Build did not produce the expected app:"
    echo "$APP_PATH"
    exit 1
fi

echo "Checking signing identity..."
security find-identity -v -p codesigning |
    grep -F "$DEVELOPER_ID_APPLICATION" >/dev/null || {
        echo "Developer ID Application certificate not found in the keychain:"
        echo "$DEVELOPER_ID_APPLICATION"
        exit 1
    }

echo "Preparing release directory..."
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

echo "Verifying Developer ID signature..."
codesign \
    --verify \
    --deep \
    --strict \
    --verbose=2 \
    "$APP_PATH"

codesign \
    --display \
    --verbose=4 \
    "$APP_PATH"

echo "Creating notarization archive..."
/usr/bin/ditto \
    -c \
    -k \
    --keepParent \
    "$APP_PATH" \
    "$NOTARY_UPLOAD"

echo "Submitting to Apple notarization service..."
xcrun notarytool submit \
    "$NOTARY_UPLOAD" \
    --keychain-profile "$NOTARY_KEYCHAIN_PROFILE" \
    --wait

echo "Stapling notarization ticket..."
xcrun stapler staple "$APP_PATH"

echo "Validating stapled ticket..."
xcrun stapler validate "$APP_PATH"

echo "Assessing with Gatekeeper..."
spctl \
    --assess \
    --type execute \
    --verbose=4 \
    "$APP_PATH"

echo "Creating final distributable archive..."
  COPYFILE_DISABLE=1 /usr/bin/ditto \
    -c \
    -k \
    --keepParent \
    "$APP_PATH" \
    "$FINAL_ZIP"

echo
echo "Release complete:"
echo "$FINAL_ZIP"
