# MC3 telemetry -> PCSX2 PINE -> ReShade

`mc3_telemetry.mod` publishes a versioned 48-byte snapshot at EE address
`0x0061C990`. It is independent of the digital speedometer and draws nothing.

The PC side contains two readers of the same ABI:

- `mc3_pine.py` prints the values and is the quickest diagnostic.
- `mc3_telemetry.addon64` exposes them as ReShade uniform sources.

## Current local installation

ReShade 6.8.0 with full add-on support is installed for DXGI/D3D12 at:

`$HOME\OneDrive\PCSX2 v1.7 dev`

The installed files are:

- `dxgi.dll` (official ReShade loader)
- `mc3_telemetry.addon64`
- `reshade-shaders\Shaders\MC3Telemetry.fx`

PINE is enabled in `$MC3_PCSX2_DATA\inis\PCSX2.ini`. The test effect is
enabled in `ReShadePreset.ini`. Start MC3 and the thin bar at the top should be
green when telemetry is connected; its length follows speed and its color
shifts toward red as RPM rises. Press `Home` to open the ReShade overlay.

This requires the **full add-on support** ReShade build. The normal build skips
external `.addon64` files.

## First test: PINE reader

1. In PCSX2, enable `Settings > Advanced > Enable PINE Server`, keep port
   `28011`, then restart PCSX2.
2. Keep `core.mod = 1` and `mc3_telemetry.mod = defer` in the HostFS INI.
3. Enter free roam and run:

```powershell
python mc3_pine.py --once
python mc3_pine.py
```

The continuous view should react to speed, RPM and gear. `Ctrl+C` stops it.

For a PC-only protocol test, with no emulator running:

```powershell
python test_pine.py
```

## ReShade sources exposed by the add-on

Effects can declare uniforms with these `source` annotations:

| Source | Type | Meaning |
|---|---|---|
| `mc3_connected` | float | `1` while valid PINE telemetry is arriving |
| `mc3_speed_kmh` | float | speed in km/h |
| `mc3_rpm` | float | engine RPM |
| `mc3_gear` | int | `-1=R`, `0=N`, `1..7` forward |
| `mc3_flags` | uint | validity bits from the guest mailbox |
| `mc3_velocity` | float3 | world velocity XYZ |

`MC3Telemetry.fx` is intentionally a small proof of concept. Other effects can
consume the same sources for motion blur strength, color grading, vignette,
camera-speed reactions or debug overlays without changing the PS2 module.

The PCSX2 PINE server serves one connected client at a time. While the ReShade
add-on is loaded it owns that connection, so `mc3_pine.py` is a diagnostic
alternative rather than a second simultaneous monitor. Temporarily rename the
installed `.addon64` and restart PCSX2 if the command-line reader is needed.

## Build

Run `build_addon.cmd` from a Visual Studio 2022 command environment. The result
is `output\mc3_telemetry.addon64`. The build uses the official ReShade headers
under `vendor\reshade`.

## ABI v1

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | u32 | magic `MC3T` (`0x4D433354`) |
| `+0x04` | u16/u16 | version `1`, size `48` |
| `+0x08` | u32 | sequence; odd while writing, even when stable |
| `+0x0C` | u32 | valid bits: player, velocity, engine, transmission |
| `+0x10` | f32 | speed in km/h |
| `+0x14` | f32 | engine RPM |
| `+0x18` | s32 | gear: `-1=R`, `0=N`, `1..7` forward |
| `+0x1C` | f32[3] | world velocity XYZ |
| `+0x28` | u32 | player EE address, diagnostic only |
| `+0x2C` | u32 | reserved, zero in v1 |

Readers must compare `sequence` before and after a snapshot and reject odd or
changed values. Unknown versions/sizes must be rejected rather than guessed.
