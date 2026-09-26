# Proper Widescreen mod

Runtime-selectable 4:3, 16:9 and 21:9 for **Midnight Club 3: DUB Edition
Remix**, SLUS-21355, retail ELF CRC `60A42FF5`.

The widescreen MIPS/HUD work is by **SuperType1/Remco**. Their two PNACH files
are not redistributed in this kit: to regenerate the tables, put them
byte-for-byte in `sources/` (`16x9.pnach`, `21x9.pnach`). `generate_profiles.py` validates and
turns them into `profiles.generated.h`; the C++ module only applies those tables
and supplies reversible selection. Their 16:9 file is explicitly the HUD half,
so the generator also adds the two 3D projection words from the toolkit's
existing `Video/Widescreen 16:9` group. No injected instruction was
transcribed by hand.

Those two words are only the seed used the next time `gfxPipeline::SetRes`
runs; changing them alone does not update a camera that already exists. The
menu companion therefore also hooks the two calls to
`gfxViewport::Perspective` in `camViewCS::Draw` (`0x0031F2C0` and
`0x0031F2DC`) and supplies the selected aspect to the live projection matrix
every frame. A small assembly bridge preserves the game's `f12`-`f15` method
ABI, which differs from the free-function ABI used by the PS2SDK compiler.

## Modules

- `proper_widescreen.mod` is the 28 KB patch engine. Load it as `defer`, so
  `core.mod` places it in game-owned heap memory and calls it every frame.
- `proper_widescreen_menu.mod` is the 1.5 KB frontend/camera companion. Load it as
  `1`, because it must chain the main-menu vtable during initialization.

The menu row reads `Aspect Ratio: 16:9` initially. Pressing it cycles:

`16:9 -> 21:9 -> 4:3 -> 16:9`

Selection lasts for the running game session. It is deliberately not written
into the player's save file.

The module changes MC3's HUD and 3D projection, but code running on the PS2
cannot change PCSX2's presentation setting. Match PCSX2's output/window aspect
to the selected profile (or use Stretch with a window of that aspect); otherwise
the corrected frame will still be displayed with the emulator's old ratio.

The menu companion chains the previous `mcMenuModeSelect::PopulateMenu` handler,
so `menu_row.mod` and its **Mod City** row remain present. Its init hook is
`0x001A0F38`; the existing menu and city modules own `0x001A0F30` and
`0x001A0F40` respectively.

## Loading

```ini
[mods]
core.mod = 1
proper_widescreen.mod = defer
proper_widescreen_menu.mod = 1

[patches]
Video/Widescreen 16:9 = 0
```

Disable the standalone Proper Widescreen PNACH group too. Running either the
old modloader `Video/Widescreen 16:9` group or an external widescreen PNACH at
the same time makes two writers fight over projection, HUD and code-cave words.
The updated `peds.mod` is built with that legacy fixed group disabled as well;
changing only the INI is insufficient for an older `peds.mod`, whose compiled
defaults continue running every frame.

`digital_speedometer.mod` may remain `defer_once` in any position. It bootstraps
from the stable coordinate-helper call at `0x0027A3DC`, then redirects the whole
`hudGear::Display` function at `0x0027A080`. Older builds hooked `0x0027A470`,
which these tables rewrite every frame and therefore depended on a losing
load-order race. Rebuild/update the speedometer instead of changing it to
`defer` merely to force it after the HUD engine.

The two generated modules are copied to `output/mods/` and to the HostFS root.
`output/release/mc3boot.ini` intentionally remains a clean, no-mod template.

## Rebuilding the tables

From this directory:

```sh
python generate_profiles.py \
  --elf $MC3_HOSTFS/slus_213.55.ELF \
  --16x9 sources/16x9.pnach \
  --21x9 sources/21x9.pnach \
  --out profiles.generated.h
```

The generator accepts only aligned EE `word` writes. Duplicate writes must have
the same value. For 4:3 it builds a union of every touched address, restores ELF
words from the retail executable, and restores the PNACH cave below
`0x001A0000` to zero.

Then build both modules from `$MC3BOOT/mods/`:

```sh
sh build_mod.sh proper_widescreen /y/MC3HostFS
sh build_mod.sh proper_widescreen_menu /y/MC3HostFS
```

The engine rechecks the selected table like PCSX2 `patch=1`, but compares before
writing and flushes the EE data/instruction caches only after a real change.
