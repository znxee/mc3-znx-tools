# Convention for added cities (MC3 Remix)

Every new city (MC2, PSP or any other) goes into the **same game** as the
others. Memory, the city table and the way back to the menu are **shared**. A
city that does not follow the rules below does not only break itself: it breaks
the **next** city the player picks.

Every rule comes from a freeze measured on 2026-10-04.

Who follows it: every fork (Fork City MODS, MCLA PSP and any other). Whoever
changes a rule records it in `FORKS.md`.

## 1. Registration and names

- **Slots** (table of 10 records of 76 bytes):

  | Slot | City | Code |
  |---|---|---|
  | 0–3 | retail | — |
  | 4 | nocity | — |
  | 5 | losangeles | m |
  | 6 | paris | p |
  | 7 | tokyo_mc2 | k |
  | 8 | la_psp | q |
  | 9 | tokyo_psp | v |

  A new slot means growing the table, in both registrars.
- **Internal name:** lowercase, no spaces (the `.loc`/`.locinf` files are read
  word by word) and different from the retail ones. That is why it is
  `tokyo_mc2` and not `tokyo`.
- **On-screen name:** comes from the string tables (`CM_<name>`, and `<name>`
  in `mcloadstrings`).
- **One-letter code:** unique. It names the hoods (`k_bh`...) and the loading
  screens (`*_k.pck`).
- **One registrar per install:**
  - `city_psp_slots` in the Alpha 3.2 Remix (10 slots);
  - `city_paris_slot7` + `losangeles_arcade` in the older ISO (8 slots).

  Both patch the same limits with different values.

## 2. The city `.pck` shell

- **Build it with the current `mc3_city_build.py`** (synthetic shell). Since
  2026-10-04 it writes the `new[]` cookie before the hood array, with count 0.
  - `mcCity`'s destructor walks that count when the city is unloaded whole.
  - Garbage there freezes in `mcHood::DeleteModels` 0x25D418.
- **Donor:** an MC2 city shell that is already installed (for example
  `paris_<time>_<weather>.pck`), with `--material 0`.
  - With Atlanta as the donor, 72 texture references come along, and the
    `rmcTextureReference` destructor (0x2B6AB0) read garbage on unload.
- **Check:** `python mc3_city_check.py "$MC3_HOSTFS"` must exit with status 0,
  without `FAIL`. The 9 shells (3 times × 3 weathers) are required.

## 3. Memory: the top of the heap belongs to everyone

The frontend creates the NetHeap (300 KB) and the LoadHeap (1.1 MB) from the
**top** of the main heap, above the allocator cursor. The cursor only goes down
when the topmost block is freed. A forgotten live block near the top = a menu
that does not come back: "not enough main heap memory for memtop heap", then a
null heap.

- **Nothing per city stays alive after the race.** Every block a module
  allocates while in the city (textures, tables, streams, buffers) is freed in
  the frontend hook:
  - the renderers' `frontend_hook`;
  - or a `release` function exported in the registry, which the hook calls.
    Example: the minimap's `MMRL`.

  A cache that lasts the whole session is forbidden. That was the case of the
  minimap textures: 3 blocks of 64 KB stuck at the top after LA, Paris and
  Tokyo MC2.
- **Minimum room:** after the renderer is released, the top needs at least
  `0x180000` free (`FE_EMPTY_ROOM`).
  - Below that, the renderer takes the San Diego path (`RFEM`/`RFEN`) and
    unloads the whole city; that is why rule 2 matters.
  - Log markers: `RREL <blocks> <largest free>`, `RREG <free at top> <heap>`.
- **Texture page pool:** the hook at 0x1A94A4 (city_original_memory /
  losangeles_page_pool) cuts the pool to 8 pages. When the 2 MB reserve does
  not fit, it gives 0 pages (`RPPR`) instead of a negative count.
- **Own arrays standing in for game arrays** carry the `new[]` cookie:
  - `vec_new(16 + size)`, count at +0, pointer at +16;
  - the game's destructor reads `ptr-16` and frees `ptr-16`.

  Example: the route table of `mc2_aib_compat`.
- **Renderer margin:** `HEAP_MARGIN` 0x50000 still applies to the large blocks.

## 4. Keep the game from reading past 4-city tables

Executable tables with 4 entries (sd, atlanta, detroit, tokyo) that a new city
has to avoid or intercept:

- sirens (0x615740), already handled;
- HUD map data (0x6BDE20), through the minimap;
- the menu camera movement (`MoveFECamera`, `slti 4`).

A new city reuses these interceptions instead of adding others.

### 4.1. Collision materials belong to the target game too

- The count and the IDs of the bound's material table must be valid in the MC3
  physics library that is installed. Translate the IDs and counts of the source
  game; do not copy its count just because the names look equivalent.
- The consumer walks **every** declared entry when saving/restoring materials,
  including an entry no polygon uses. For Geometry/Octree/Quadtree bounds, the
  count is at `bound+5` and the table at `bound+112`. `GetMaterialCount`
  0x5CD928 and `GetMaterial` 0x4789D8 feed `mcLevelSimple::RestoreMaterials`
  0x2C0BC0.
- Case measured on 2026-10-04: MCLA has 30 classes in `physicslist.txt`, MC3
  has 29. The PSP translator already mapped the IDs to 0..28, but emitted a
  table 0..29 with count 30. Switching from LA PSP to Tokyo PSP,
  `GetMaterial(29)` returned null and the restore froze in block 0x2C0DD0. The
  emitted table now has 29 entries. Do not fix this by skipping a null material
  in the consumer.

## 5. Required test before publishing

1. `python mc3_city_check.py "$MC3_HOSTFS"`, with status 0.
2. **Ordered Race cycle** through every Arcade city, going out to the frontend
   between them. The new city goes in **after** LA and Paris; the problem shows
   up on the 3rd custom city.

   ```
   python mc3_pcsx2_drive.py --elf "$MC3_HOSTFS/mc3boot.elf" --skip-fmv \
       --add-mod "frontend_no_timeout.mod = defer" --out <folder> \
       "until state:41 120" "push cross havefun" "push cross career" "select arcade" \
       "push cross row:players" \
       (for each city:) "select =<city> right 12" "press down" "select =ordered_race right 8" \
       "push cross row:time_of_day" "push cross start" "press cross" "until state:1 240" "wait 20" \
       "push start state:2" "select go_to_menus" "press cross" "until state:6 10" "select yes up 3" \
       "press cross" "until row:players 180"
   ```

   Needs `menu_state.mod` (`defer`) enabled in the install.
3. **In the PCSX2 log:** no `TLB Miss` and no "not enough main heap memory". On
   a real PS2 a TLB miss freezes the console even when PCSX2 keeps going.
4. **Forced exit through the San Diego path:** with `--boot fesd=1` the new
   city must come back to the Arcade (this tests rule 2).
