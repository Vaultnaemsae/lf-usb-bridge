# LF+ USB Bridge

<img width="256" height="256" alt="icon_256x256" src="https://github.com/user-attachments/assets/5323b653-6c66-40dc-b2b3-be8a1fa26fc4" />

Enable modern USB MIDI support for FAMC Liquid Foot+ controllers on macOS
and Windows, without using the LF+ Editor. Formerly, the LF+ Editor-generated
MIDI-over-USB bridge was not available on Windows.

LF+ USB Bridge is a desktop application that connects directly to a
Liquid Foot+ controller over USB serial and presents it to your computer
as standard MIDI ports. This allows the controller to work with modern
DAWs, MIDI utilities, and community tools without relying on legacy
drivers.

**Independent project:** LF+ USB Bridge is an independent community project
and is not affiliated with FAMC.

This project came to life thanks to Sung Le's FAMC Liquid Foot+ Editor,
a project to reverse engineer and modernize the old FAMC LF+ Editor software,
to which I contributed some assistance with duplicating the original MIDI
Bridge.

> The bridge needs a working USB serial port. On Windows, an original FAMC
> driver may provide one at `0403:87C0`. Controllers prepared with Sung Le's
> Device Connection Setup use `0403:6015`. That shared FTDI ID also needs
> Liquid Foot metadata for automatic discovery.

Get it here: https://github.com/sungle-spec/famc-liquid-foot-editor-builds/releases

------------------------------------------------------------------------

# Features

-   🎛️ Modern USB MIDI bridge for Liquid Foot+ controllers
-   🎹 Works with DAWs and MIDI applications
-   🖥️ Native GUI for macOS and Windows
-   🍎 Signed and notarized macOS releases
-   🔄 Automatic device discovery
-   🔌 Safe connection lifecycle
-   Background operation with a macOS menu bar / Windows system tray icon
-   🧪 Comprehensive automated test suite
-   🛠️ Open source

------------------------------------------------------------------------

# Downloads

Latest release:

https://github.com/Vaultnaemsae/lf-usb-bridge/releases/latest

| Platform   | Status       |
|------------|--------------|
| macOS      | ✅ Supported |
| Windows 11 | ✅ Supported |

------------------------------------------------------------------------

# Supported Platforms

## macOS

-   Apple Silicon Macs (the v1.0.1 download is arm64)
-   Automatic virtual MIDI ports
-   Runs in the menu bar; no open window or Dock icon required

Port name:

``` text
LF+ USB MIDI
```

## Windows

Windows (x64/ARM) requires user-created loopback MIDI endpoints.

Closing the controls window keeps the bridge running in the system tray.

Use loopMIDI or Windows MIDI Service to create these exact ports (original FAMC-prescribed names) before starting the bridge:

``` text
LF+ IN PORT
LF+ OUT PORT
```

------------------------------------------------------------------------

# Typical Workflow

``` text
Liquid Foot+
      │
 USB Serial
      │
LF+ USB Bridge
      │
 Standard MIDI
      │
DAW / Editor / MIDI Apps
```

------------------------------------------------------------------------

# Compatible Software

The bridge has been designed for use with software including:

-   MIDI utilities
-   DAWs / Hosts
-   MIDI monitoring tools

------------------------------------------------------------------------

# Getting Started

## Requirements

-   Compatible Liquid Foot+ controller
-   USB connection
-   Python 3.11+ (source builds only)

### macOS

Nothing extra is required.

### Windows

Create:

``` text
LF+ IN PORT
LF+ OUT PORT
```

using loopMIDI or Windows MIDI Services.

------------------------------------------------------------------------

# Using LF+ USB Bridge

1.  Connect the controller.
2.  Launch the application.
3.  Select the detected controller.
4.  Verify the MIDI ports.
5.  Click **Start**.
6.  Select the bridge MIDI ports inside your software.

On macOS, launch the app and click its MIDI socket icon in the menu bar.
Choose **Start** to connect, or **Show Controls…** to select a controller,
view setup information, and enable **Auto-connect on launch**. Closing the
controls window keeps the bridge running.

On Windows, the controls window opens on launch. Closing it (including Alt+F4)
hides it to the system tray and keeps the MIDI connection running. Click the
tray icon to reopen the controls, or right-click for the menu. Windows may put
the icon under the taskbar’s hidden-icons arrow; drag it onto the taskbar’s
notification area to keep it visible.

On either platform, the icon menu provides **Show Controls…**, **Start**,
**Stop**, **Rescan Devices**, and connection status. Choose **Stop** to disconnect
while leaving the app running, or **Quit LF+ USB Bridge** to exit and release
the MIDI and USB connections. Closing the controls does not stop a connection
that is starting or already running.

If no menu bar/system tray is available, the app uses a normal window and
closing it exits after releasing its connections.

Auto-connect is off by default. Enable it in the controls window if desired.

------------------------------------------------------------------------

# Troubleshooting

## No controller detected

-   Reconnect USB
-   Click Refresh
-   Verify FTDI identity
-   Close any application already using the controller

Open **Setup… → Serial ports…** to inspect all visible serial ports, their
USB IDs and serial metadata, and the reason a port was excluded from automatic
discovery. Select your controller's port and choose **Use selected port**, then
click **Start**. Manual selection does not connect or qualify the device; the
normal LF+ identification handshake still runs when you start the bridge.
Manual selections last for this application session while the port remains
visible, and are never used for automatic connection on launch.

On Windows, if no COM port is shown, check Device Manager for the device's
Hardware IDs and a working FTDI VCP driver matching those IDs. The bridge
enumerates serial ports, so changing its matching rules cannot expose a USB
device that has no COM port. Missing loopback MIDI ports are a separate Start
error and do not hide the controller from USB serial discovery.

Automatic discovery recognises the original LF+ Series ID `0403:87C0` directly.
The archived FAMC drivers label `87C1` as LR Series, `87C2` as X Series, and
`87C3–87C6` as unspecified FAMC devices; those IDs are not automatically selected
as LF+ controllers.

## COM port already in use (Windows)

Only one application can own the serial port.

Example:

``` text
PermissionError(13, 'Access is denied')
```

Usually means another process already owns the controller.

## No MIDI ports

### macOS

Look for:

``` text
LF+ USB MIDI
```

### Windows

Confirm:

``` text
LF+ IN PORT
LF+ OUT PORT
```

------------------------------------------------------------------------

# Running from Source

``` bash
git clone https://github.com/Vaultnaemsae/lf-usb-bridge.git
cd lf-usb-bridge
```

## macOS

``` bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python app.py
```

## Windows

``` powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python .\app.py
```

------------------------------------------------------------------------

# Development

Run tests:

``` bash
python -m pytest
```

Smoke test:

``` bash
python -m lf_usb_bridge.gui --smoke-test
```

Build Windows:

``` powershell
.\scripts\build_windows_app.ps1 -PythonExecutable .\.venv\Scripts\python.exe -BuildLabel 1.0.1
```

Build macOS:

``` bash
./scripts/build_macos_app.sh
```

Signed release:

``` bash
DEVELOPER_ID_APPLICATION="Developer ID Application: Example Developer (TEAMID)" \
NOTARY_KEYCHAIN_PROFILE="LFUSBBridgeNotary" \
./scripts/release_macos_app.sh
```

------------------------------------------------------------------------

# Project Status

Validated:

-   ✅ macOS build and notarization
-   ✅ macOS virtual MIDI
-   ✅ Windows GUI
-   ✅ Windows endpoint support
-   ✅ LF+ serial transport

------------------------------------------------------------------------

## License

LF+ USB Bridge is released under the [MIT License](LICENSE).

See [Third-Party Notices](THIRD_PARTY_NOTICES.md) for licenses and attribution relating to bundled or required third-party software.

## Support

LF+ USB Bridge is free and open source.

If it has been useful to you and you'd like to support future development, you can buy me a coffee:

☕ https://buymeacoffee.com/vaultnaemsae

Thank you for your support!
