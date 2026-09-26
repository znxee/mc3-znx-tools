# Mouse and keyboard for freecam.mod

After booting with `freecam.mod = defer`, open `start_freecam.cmd`.
Requires Python 3, Windows and PINE enabled with its connection free. The camera
is processed on the PS2 by the module; this program only sends the PC controls.

Focus PCSX2 and press P to turn it on. WASD moves, R/F up/down, the mouse looks,
Shift speeds up, Ctrl slows down, Q/E roll, O levels, Z/X adjust the FOV,
M/N adjust the speed, T smooths, H repositions. L releases the mouse;
End or Ctrl+C in the terminal closes the helper. Alt-Tab releases the controls.

To check without controlling anything:

```powershell
python freecam_input.py --status
```

Another port: `start_freecam.cmd --port 28012`.
PCSX2 has to use the same port. The old ReShade telemetry add-on takes the only
28011 connection; just unticking the effect does not release that reader.
Turn the reader off and restart the emulator, or pick another port for the
freecam (the old add-on's telemetry stops receiving until the port is restored).

The program checks the foreground process before reading input, does not log
keys and does not connect to external servers. The mod receives held keys and
cumulative deltas, so it does not depend on Windows autorepeat.
Without the helper the freecam can also be driven with the game's USB keyboard
and the arrow keys to look. See `../../mods/freecam/README.md`.

Port by modderG, 2026-09-05; original camera by AlgumCorrupto (Paulo).
GPL-3.0, LICENSE.md included.
