# mc3 ZNX tools

A toolkit for reverse engineering and modding **Midnight Club 3: DUB Edition
Remix** on the PlayStation 2 (SLUS-21355), with side tools for Midnight Club 2
and Midnight Club: L.A. It collects the tools, file-format notes and C++ probe
modules written across the project's working branches ("forks") into one place.

The modloader itself (`mc3boot`: boot ELF, `mc3_inject.py`, `mc3_mkmod.py`,
`mc3_pnach.py`, the core payload and its headers) lives in its own repository
and is **not** part of this kit. Several tools and every C++ module here depend
on it; see [Requirements](#requirements).

> No game data is included. Every tool reads files you extract from your own
> copy of the game.

## Layout

| folder | what is in it |
|---|---|
| `tools/` | the Python tools: containers (`.pck`, `.ppf`, `.bnd`), meshes, textures, shaders, vehicles, traffic, cities, collision, flash UI, audio banks, ELF/IDA/symbol helpers, PCSX2 and ISO helpers. Start with `tools/mc3_hub.py`, a launcher that lists the tools and what each one does |
| `tools/experimental/` | libraries the finished tools import (`mc2_to_mc3.py`) and one-question probes kept for their findings - see its README |
| `tools/symbols/` | function and global names carried from the October 2004 alpha build's `MC.MAP` to the retail ELF, as layered `.tsv` files (`mc3_symbols.py`) |
| `tools/bnk_tool/` | Sony 989SND/SCREAM `.bnk` parser, MUX/DEMUX and engine-audio preview, used by `mc3_audio_studio.py` and `bnkmux.py` |
| `tools/freecam_input/` | PC mouse/keyboard helper for the `freecam` module, over PINE |
| `tools/reshade_telemetry/` | PINE client for the `mc3_telemetry` module's mailbox |
| `tools/ghidra_scripts/` | two small Ghidra exporters |
| `audio/` | music/stream tools: RSTM conversion, the music manager, engine-sound curve editors |
| `docs/` | format notes and ImHex patterns (`*.hexpat`) for city and vehicle `.pck` files, both on disk and live in RAM, plus `lint_hexpat.py` |
| `mods/` | C++ source of runtime modules for the modloader: diagnostic probes, the freecam, widescreen, HUD, city-porting experiments |
| `research/` | one-off analysis scripts from the city-porting work, kept as a record of how results were obtained |

Most tools document themselves: the module docstring explains the format or
the finding behind the tool, and `python TOOL.py --help` lists its options.

## Requirements

- **Python 3.9 or newer** (developed on 3.12). Most tools use only the standard
  library.
- Optional packages, needed only by the tools that use them:
  `Pillow` (textures, previews), `numpy` and `matplotlib` (plots),
  `capstone` (MIPS disassembly helpers), `reportlab` (`mc3_docs.py`),
  `zstandard` (reading PCSX2 savestates), `pyelftools`, `dearpygui`
  (`audio/mc3_music_manager_dpg.py`).
- **mc3boot** (separate repository) for everything that talks to the running
  game: `mc3_inject.py` (savestate readers, payload injection) and the module
  build script. Clone it next to this repository or point `MC3BOOT` at it.
- **C++ modules** build with the ps2dev toolchain (`mips64r5900el-ps2-elf-g++`)
  through mc3boot's `mods/build_mod.sh`: copy a module folder from `mods/` into
  `$MC3BOOT/mods/` and run `sh build_mod.sh NAME [HostFS dir]` there. The
  modules include `../../payload/mc3_mod.h`, which lives in mc3boot.
- `audio/mc3_music_core.py` also needs Edness's `MclHash.py` and `strtbl.py`
  on the Python path; they are not redistributed here.
- Optional external programs: PCSX2 (with PINE enabled for the live tools),
  IDA Pro 9 with IDAPython on Python <= 3.9 (`mc3_ida.py`), ImHex (to open the
  `.hexpat` files), vgmstream-cli and psxavenc (audio conversion).

## Paths and environment variables

The tools carry no personal paths. Where a default location is needed it comes
from an environment variable, and every tool also accepts explicit paths on the
command line.

| variable | meaning |
|---|---|
| `MC3_HOSTFS` | the PCSX2 HostFS folder the game boots from (holds `mc3boot.elf`, `mc3boot.ini`, `ASSETS/`) |
| `MC3_ASSETS` | an untouched retail `ASSETS` folder, used as a clean reference |
| `MC3_ELF` | the retail executable, `SLUS_213.55` |
| `MC3BOOT` | a checkout of the mc3boot repository |
| `MC3_PCSX2` | the PCSX2 executable (`pcsx2-qt.exe`) |
| `MC3_PCSX2_DATA` | the PCSX2 data folder (holds `sstates/`, `logs/`) |
| `MC3_PCSX2_INI`, `MC3_PCSX2_DEBUG` | PCSX2 ini file and debug profile, for `mc3_pcsx2.py` |
| `MC3_IDA` | the IDA Pro install folder |
| `MC3_ALPHA` | the October 2004 alpha build folder (the one with `MC.MAP`) |
| `MC2_PC_ASSETS` | Midnight Club 2 PC `assets_p` folder |
| `MC2_PS2_ASSETS` | Midnight Club 2 PS2 `assets` folder |
| `MC3_ISO_SOURCE` | the folder `mc3_build_iso_assets.py` builds `ASSETS.DAT` from |
| `MC3_WORK` | a scratch/work folder some research scripts read from and write to |
| `MC3_RIM_IMPORTER` | folder of the (separate) Rim Importer & Explorer, for the hub |

Generated files go to `tools/output/` (see `tools/mc3_output.py`), which is
ignored by git.

## Status

This is research code. The file formats are documented as far as they were
measured, and the notes say where something is a hypothesis rather than a
measurement. Tools that write game files make backups or refuse to overwrite,
but always keep copies of your own files.

`tools/mc3_selftest.py` re-implements the patched game functions and checks the
patches against the ELF; run it after changing anything it covers.

## Licence

GPL-3.0, see `LICENSE`. Third-party material and credits are listed in
`CREDITS.md`.
