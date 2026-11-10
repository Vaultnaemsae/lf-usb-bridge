LF+ USB Bridge - Windows x64 LF 12+ discovery test
Build: LF12-test-20261110

Extract the entire ZIP, then open LF+ USB Bridge.exe inside the extracted
LF+ USB Bridge folder. Keep the _internal folder beside the application.
This is a test build for Windows 11, packaged as an unsigned application.

1. Connect and power your Liquid Foot+. Close other apps using its USB port.
2. Open LF+ USB Bridge. Check whether your controller appears automatically.
3. If it does not, open Setup... then Serial ports.... This view lists every
   visible serial port and explains why automatic discovery excluded it.
4. If your controller's COM port is listed, select it, choose Use selected
   port, then click Start in the main window. Manual selection alone never
   opens the port. The normal LF+ identification handshake runs on Start.
5. Windows also needs the existing loopback MIDI endpoints named exactly
   LF+ IN PORT and LF+ OUT PORT, created with loopMIDI or Windows MIDI Services.
6. Test the controller in your usual MIDI application, then click Stop.

Please report:
- Whether the controller appeared automatically, or required manual selection.
- The text in Setup... > Serial ports... (it can be selected and copied).
- Any error shown after clicking Start, and whether MIDI worked both ways.
- Your Windows version and controller model.

If no COM port is listed, check Device Manager for the controller's Hardware
IDs and driver status. The bridge cannot enumerate a device without a serial
port. This test build does not install drivers or change the FTDI EEPROM.

Changes in this test build:
- Recognises the original LF+ Series ID 0403:87C0 with generic Windows metadata.
- Provides explicit manual selection for ports with incomplete LF+ identity.
- Excludes other documented FAMC product IDs from automatic LF+ selection.

Windows startup and software checks do not establish LF 12+ hardware operation.
This package is provided to collect that hardware test result.
