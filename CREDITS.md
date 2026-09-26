# Credits

This kit stands on work by many people in the Midnight Club modding and PS2
homebrew communities. Where a tool ports, reuses or was informed by someone
else's work, it says so in its own header too.

## Code and data carried into this kit

- **AlgumCorrupto (Paulo)** - the FreeCam of *CinematicClub*, ported to the
  modloader as `mods/freecam` (GPL-3.0, licence in `mods/freecam/LICENSE.md`
  and `tools/freecam_input/LICENSE.md`); the *RAGE-SWF-research* and
  swf-image-tools work behind the flash `.pck` and texture readers
  (`tools/mc3_tex.py`, `tools/mc3_flash.py`); the city C header and RAM dump
  used to check the city structures.
- **SuperType1 / Remco** - the widescreen MIPS/HUD work. `mods/proper_widescreen`
  is generated from their PNACH files (`generate_profiles.py` ->
  `profiles.generated.h`); the C++ module only applies those tables and makes
  the choice selectable at runtime. The PNACH files themselves are not
  redistributed here.
- **vgmstream** - `tools/bnk_tool/spu_pitch.py` is translated from vgmstream's
  `src/util/spu_utils.c`; the `.bnk` (Sony 989SND/SCREAM) parser follows
  vgmstream's `bnk_sony` reader. vgmstream-cli is also used as an external
  decoder by the audio tools.
- **psxavenc** (spicyjpeg) - used as the external PS-ADPCM encoder by
  `audio/mc3_rstm_convert.py`.

## Research and formats this kit relies on

- **AlgumCorrupto, Thiekus, ZNX and offlbruno** - the flash texture logic and
  the palette work that `mc3_tex.py` builds on.
- **offlbruno** - the `.ppf` and city ImHex patterns that were the starting
  point for the `.ppf` and city format notes.
- **ZNXee / mc3rxx / offlbruno** - `mesh_psptops2`, the vehicle mesh converter
  whose layout the city mesh writer follows.
- **Victor (Mid Engine) and RibeiroG** - the rim.ppf work and the *Rim Importer
  & Explorer* (a separate program; `mc3_hub.py` only launches it).
- **TahmidAlam-git** - `mesh_to_obj.py` in *MC3_extraction_tools*, for the Xbox
  mesh layout (`tools/mc3_mesh_convert.py`).
- **jtxredline** - *mc2-map-toolkit*, which settled how Midnight Club 2 `.xmod`
  strips index their packets (`tools/experimental/mc2_to_mc3.py`).
- **zKiwi** - the *io_AngelStudios_mesh* Blender add-on, for the `.mesh` text
  format.
- **Edness** - `MclHash.py` and `strtbl.py` (hashes and `.strtbl` string
  tables). `audio/mc3_music_core.py` uses them; they are not included here.

## Symbols

The names in `tools/symbols/` are derived from the retail executable
(SLUS-213.55) and from the linker map (`MC.MAP`) shipped with the October 2004
alpha build of Midnight Club 3, matched across builds by `tools/mc3_symbols.py`.
No game code or data is included.

## Tools used

PCSX2 (and its PINE interface), IDA Pro with Hex-Rays, Ghidra, ImHex, the
ps2dev toolchain, and Python.

Midnight Club is a trademark of Take-Two Interactive / Rockstar Games. This is
an unofficial, non-commercial fan project and is not affiliated with or
endorsed by them.
