# frontend_mod_status.mod

Shows "Mods loaded: N" in the bottom left corner of the Midnight Club 3 Dub
Edition Remix frontend, SLUS-21355. It does not add a selectable option and
does not draw in freeroam or in the pause menu.

## Enable

In the HostFS mc3boot.ini, inside [mods]:

```ini
core.mod = 1
frontend_mod_status.mod = defer
```

Restart the game through mc3boot.elf, without restoring a savestate older
than this version. To remove it, set frontend_mod_status.mod = 0 and restart.

It counts the boot's loaded (MC3T, 0x0061CA60+12) plus the core's loaded
(MC3R, 0x0061CAC0+12), including defer and defer_once. It includes the core
itself and this counter; it ignores disabled entries and load failures.
It does not count per-frame runs nor only the hookless callbacks.
"Loaded" does not guarantee each mod works.

Tested configuration: 5 from the boot + 3 defer = 8.

## Compatibility

- Guarded hook on the JAL at 0x001A2E2C: 0x0C080FF2, delay slot 0x8E445708.
- Calls the original compositor 0x00203FC8 before the text. The menu uses a
  texture on an animated 3D panel; drawing inside RenderMenus would make the
  counter move/tilt with that panel.
- Saves/restores the viewport through 0x00527B50, using the UI viewport at
  0x0070E508.
- Does not change RenderMenus, PopulateMenu, freecam, HUD or the core API/trace.
- If another mod changes the same JAL/delay slot, it does not overwrite the hook.
- Looks up the resident font smallspace at 0x006BE848 through 0x0042DD60,
  without GetFont/refcount, without its own per-frame allocation or a cached
  font. arial12, although it is the global default name, was not resident in
  the frontend.
- Diagnostic state local to the module, with no new fixed address reserved.
- Local UTF-16 text and cursor; immediate drawing with a null buffer pointer.
  The game's float ABI kept in assembly, f12-f15.
- Layout in code: x=20, y=406, scale=0.5, white. The final position follows the
  game's viewport; it injects no aspect changes.

## Build and checks

In MSYS2, with the PS2 toolchain and Python 3 on PATH, using the mc3boot build
script:

```sh
sh $MC3BOOT/mods/build_mod.sh frontend_mod_status $MC3_HOSTFS
cd mods/frontend_mod_status
mips64r5900el-ps2-elf-ld -q --entry=0 --defsym=STATUS_LINK_BASE=0x01008120 -T mod.ld frontend_mod_status.o -o frontend_mod_status.verify.elf
python verify_relocation.py
```

test_count.cpp is a native C++ test: it validates traces and text 0..1000, the
buffer limit and the 9/10, 99/100 transitions. verify_relocation.py compares
every relocated byte with an independent link at 0x01008120 and checks the
HostFS copy. It can read an old test savestate, if there is one, using the
toolkit's zstd reader.

PCSX2 runtime on a copied profile/memory cards, PINE 28012: counter 8, zero
font misses, over 4600 draws; a savestate capture confirmed the text straight,
fixed and readable at the bottom. The user's original session was not
restarted. Navigating every screen/returning from freeroam still needs testing.
Final caption in English at the user's request; the text change passed the
native tests and the relocation comparison again. An earlier capture was in
Portuguese.

Outputs: output/mods/frontend_mod_status.mod and $MC3_HOSTFS/frontend_mod_status.mod.
