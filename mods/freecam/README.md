# Freecam - CinematicClub for the modloader

Ported by modderG on 2026-09-05 from the FreeCam by AlgumCorrupto (Paulo), in
CinematicClub (old version). GPL-3.0 licence, included in LICENSE.md. It keeps
free movement, roll and FOV; it uses the game camera's live matrix. The
orbit/opponent modes and the vehicle teleport of the old DLL are not part of
this free-camera module.

## Use

For SLUS-21355 (MC3 Remix USA), in the `[mods]` section of the HostFS INI:

```ini
core.mod = 1
freecam.mod = defer
```

Start `$MC3_HOSTFS/mc3boot.elf` without loading an old savestate.
The camera starts normal; press P once the 3D scene is loaded.
`defer_once` does not work: the controls need the per-frame callback.
When the camera changes / another scene loads, the mod releases the camera by
itself. To remove it completely: `freecam.mod = 0` and restart without a
savestate. The freecam does not pause the simulation; keep its keys free of
PCSX2 virtual pad bindings so you do not drive the car at the same time.

| Key | Function |
|---|---|
| P | Turn the freecam on/off |
| W / S | Forward / back |
| A / D | Left / right |
| R / F | Up / down along the camera axis |
| Arrows | Look, no mouse needed |
| Shift / Ctrl | Fast / slow movement |
| Q / E | Roll |
| O | Level the roll |
| Z / X | Raise / lower the FOV (10 to 120 degrees) |
| M / N | Raise / lower the movement speed |
| T | Toggle movement smoothing |
| H | Reset to the game's current camera |

While the freecam is on, the camera's world position shows in the top left
corner as X, Y and Z, with one decimal place. The block disappears with the
freecam. It is drawn after the HUD and does not depend on
digital_speedometer.mod. The coordinate uses matrix.pos, exactly the transform
the hook hands back to the game.

## USB keyboard and the held-key fix

The native keyboard needs PCSX2's `hidkbd` USB device. The mod only reads
state; it does not change the keyboard mode nor consume menu events.

The first version read `0x70F238`, but that bitmap only holds new/repeat
events. That made W produce a single step. The fixed version reads the USB
library's persistent list at `0x6F8EC8`: modifiers +4, count +8, u16 USB codes
at +12. `sceUsbKb` updates that list while processing a report at `0x540890`.
Holding the key works between reports and releasing it removes the key; it
does not depend on text repeat. P/T/H use the press edge, the other functions
use the held state.

## Optional mouse

The native port does not use the old DLL's Win32 calls. For Windows mouse and
keyboard, run `tools/freecam_input/start_freecam.cmd` after starting the game.
The helper only sends input over PINE; the `.mod` stays in charge of the
camera. P turns it on, L releases/grabs the mouse, End closes the helper.
Alt-Tab frees the cursor and stops sending commands.

PINE has to be enabled and free. The `mc3_telemetry.addon64` add-on of this
install takes the only connection on port 28011 even when the visual effect is
unticked. Turn that reader off in ReShade and restart, or use another PINE port
and pass `--port 28012` to the helper. Changing the port stops that old add-on
from reading telemetry; the depth buffer and the other effects are
independent. No permanent ReShade/PCSX2 setting was changed by this port.
Without the helper, the native keyboard controls still work.

## Integration and checks

* Hook `0x001A157C`: JAL from `0x515790` to a local gateway; delay slot kept.
* Hook `0x0031F2A4`: FOV read -> JAL to a stub that keeps f12/the original when
  inactive and filters the camera component. Delay slot kept.
* Hooks 0x001A24D0/0x001A2618/0x001A2740: the three original JALs to
  mcGame::Draw go through a wrapper that calls the original drawing first and
  overlays the coordinates only when the freecam is active. The three
  `move a0,s1` delay slots are kept and each branch uses only one per frame.
* The spaceout-bold font is acquired once through the asset manager. Drawing
  uses a local UTF-16 cursor, the f12-f15 ABI and saves/restores the UI
  viewport.
* The two perspective JALs used by `proper_widescreen_menu` at
  `0x31F2C0/0x31F2DC` stay available for the widescreen.
* It checks the original instructions and refuses hooks owned by something
  else.
* Matrix and state live in the module itself; it does not use the DLL's fixed
  caves (`0x1A001C/0x1A0150`) nor ReShade's MC3T mailbox.
* PINE ABI: target of the JAL at `0x1A157C`, +8 magic MC3F, +12 state pointer,
  +16 version 1, +20 public header size 64. Total state 216 bytes.
* Host keys use a confirmed snapshot, the mouse uses cumulative counters, with
  an odd/even sequence and a watchdog of 0.5 emulated seconds.
* Float constants go through `fc_float` to avoid interleaved HI16/LO16 that the
  v2 packer cannot pair. Do not change that without checking the relocated
  binary against the linker.

Build in MSYS2 with the toolchain/Python on PATH, using the mc3boot build
script: `sh $MC3BOOT/mods/build_mod.sh freecam $MC3_HOSTFS`.
Binaries in `output/mods/freecam.mod` and `$MC3_HOSTFS/freecam.mod`.

Checks done: camera turned on/off in PCSX2 through PINE input, 15.4 units of
movement, rotation, FOV from 65 to 77 degrees, frames advancing; the user also
confirmed the camera works. For the keyboard fix: a test of 240 held frames
without new events, release, P once per press, W+D+Shift, arrows and FOV. Tests
of 10000 rotations and 30/120 fps equivalence; relocations compared byte for
byte with the linker at 0x01008120. The hold fix still needs confirming with a
physical keyboard after a reboot.

Coordinate overlay validated in an isolated runtime: the three draw hooks were
installed, the freecam toggled on/off, the font loaded once and the draw counter
advanced. matrix.pos (384.5, 24.7, 343.9) was formatted as X/Y/Z with one
decimal place; the capture confirmed margin, order and legibility in
widescreen. When inactive, draw_coordinates returns before emitting text.
test_coords.cpp covers sign, zero, 9/10 and 99/100 digits, clamping and the
buffer limit.
