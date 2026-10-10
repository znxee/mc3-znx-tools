# Convention for added cities (MC3 Remix)

Every new city (MC2, PSP or any other) goes into the **same game** as the
others. Memory, the city table and the way back to the menu are **shared**. A
city that does not follow the rules below does not only break itself: it breaks
the **next** city the player picks.

The rules come from freezes measured on 2026-10-04 and from the Next Race
reload investigated on 2026-10-06 and 2026-10-09.

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
- **A direct reload must free the buffers before the native load too.** Next
  Race goes straight to another race without visiting the frontend. In the
  Remix, `0x358468` queues event 6, `0x1A5340` → `0x1A76C0` dispatches state 2
  and JAL `0x1A5620` calls `EnterStateGame` (`0x1A5700`). Do not confuse event 6
  with state 6 (`EnterStateRaceSelect`, JAL `0x1A5670`).
  - `UpdateConfig` (`0x1A6798`) calls `0x1AAA38`, which creates a 1,126,400-byte
    `LoadHeap` at the top. In LA PSP, resident buffers left only ~404 KB and the
    load failed before reaching the StartRace hook.
  - The hook at `0x1A5620`, in `city_original_maps`, covers the PSP slots 8
    and 9 and, since 2026-10-09, the MC2 cities (5, 6, 7) too: it calls
    `MMRL`, restores the props flag, frees the renderer/streams and emits
    `RNXR <city> <room>`. The MC2 renderer is loaded again by `preload_city`
    at StartRace, as after the frontend. Without it, Los Angeles's Next Race
    printed "not enough main heap memory for memtop heap" and ran on a null
    heap (TLB 0x3AFE40). The kit's `city_mc2_rsc_draw_test` has the same hook.
    Since 2026-10-09 the PSP renderer is rebuilt before the loading ends,
    following the cycle below. Compose handlers if another module needs the
    same JAL.
  - Other renderers must check their own life cycle before applying the same
    release.
- **PSP loading (slots 8/9, 2026-10-09):** `city_original_maps` also owns the
  JALs `0x1AA184`, `0x1BE490`, `0x1AAD5C` and `0x1A585C`. The existing
  `0x1A5620` hook starts the state from the destination NEXT config, outside
  the release guard of the CURRENT config. The first two new JALs postpone
  `0x1AAE28`, which writes the transition-out flag `thread+0x4004`. The draw
  hook keeps the SWF and adds the status; the last one prepares the PSP data
  before `StopLoading` (`0x1AAB08`). Compose handlers, never duplicate owners
  of these JALs. Rebuild with the current `psp_loading.h`. `0x1A5728` still
  belongs to `city_psp_race_rules` for forced vehicle selection; the renderer
  does not replace it.
  - While the LoadHeap is alive, prepare resident data and a `0xC0000` arena,
    without sending any PSP drawing. After joining the thread and freeing the
    LoadHeap, replace that arena with the adaptive double buffer, build the
    EE/visibility caches and only then let the (already intercepted)
    `StartRace` run. Keep no new font/atlas allocation in the heap: reuse an
    existing font and release its reference after joining the thread.
  - A failed load blocks `StartRace` and queues the native event 20 back to the
    frontend. `PLRD` = data/simple arena ready; `PLDB` = double buffer/caches;
    `PLST` precedes the race and `PLFR` the first draw. `pslg=0` turns off the
    PSP gate and `pslt=0` only the text, in `[boot]`. Do not use these markers
    to claim every event/weather was tested.
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

- sirens (0x615740): only entries 0..3 exist; entry 4 holds code pointers and
  entry 5 holds the float 0.5 where the name goes. Two readers:
  - the bank, `0x209B98`, called at `0x1BDE24` and `0x1BDF30`;
  - the sound constructor, `0x209AD0(sound, city)`, called at `0x2CFE18`
    (`mcCarAudio::Init`) and `0x216820`. Its guard is `city >= 6`, so only LA
    (5) got through: Los Angeles's Next Race stopped in `strcpy 0x432E58`
    reading `0x3F000000` (fixed 2026-10-09).

  Both live in `city_psp_slots` (Alpha 3.2 Remix) and `city_paris_slot7`
  (kit): PSP and Tokyo MC2 use entry 3; the other cities from 4 up use 0;
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

## 5. Frontend: flat menu, no city behind it

Since 2026-10-05, leaving an added city for the frontend shows the menu **flat
on the screen**, like the pause menu. The menu is no longer a panel standing in
the world, and the city behind it is not drawn.

- **How the game does it:** the frontend draws the menus and the Flash
  backgrounds (`bg_*.swf`) into a 512×512 texture of `mcUIProjector`
  (0x615708).
  - `TakeDownScreen` (0x203FC8) pastes that texture on a quad with 4 corners in
    the world.
  - The corners come from `mc3FeView` per retail city (`sub_322260`, `city < 4`
    only). An added city kept the constructor's default (690, 6..11,
    479.7..487.9), and only a camera aimed there showed the menu.
- **What `city_frontend_2d.mod` does** (`defer`, in the Alpha 3.2 Remix):
  - before `TakeDownScreen` (jal 0x1A2E2C), it puts the 4 corners on a
    rectangle in front of the camera that covers the screen;
  - it uses the camera the game restores (0x6BE7E0) and the projection of the
    saved viewport (0x6BE818);
  - it does not draw the city (`mcPlayerManager::Draw`, jal 0x1A2AF4) and
    clears the screen to black instead;
  - it puts the original corners back after the draw.
- **Rule:** a new city does **not** create its own camera or menu panel in the
  world. It uses this module, which applies to any city index ≥ 4.
  - The renderers' `fe_transition` (built-in camera) stays only for `fe2d = 0`
    and for the LAN lobby with `lank = 1`.
  - On the San Diego path (`RFEM`, `fesd = 1`), the city behind the menu is San
    Diego, retail, and the normal 3D frontend stays.
- **The empty city is not reloaded:** on the normal path (`RFEE`) both configs
  stay equal, `EnterStateMC3Frontend` loads nothing and the race's city only
  stays in memory, undrawn.
  - Running the frontend with **no** city at all is not supported by the game:
    it reloads layer 0 whenever it is missing, and `nocity` has no files.
- **`[boot]` keys:** `fe2d = 0` brings back the panel in the world; `fe2w = 1`
  keeps the flat menu but draws the city behind it.
- **Marker:** `RF2D <city> <near × 1000>`, once per frontend visit.

## 6. Required test before publishing

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

   Needs `menu_state.mod` (`defer`) enabled in the install. PINE takes ONE
   client: a PCSX2 the user left open holds port 28011, and the driver then
   uses 28012 by itself; a script that forces `--pine-port 28011` fails to
   bind ("PINE: Error while binding to socket").
3. **In the PCSX2 log:** no `TLB Miss` and no "not enough main heap memory". On
   a real PS2 a TLB miss freezes the console even when PCSX2 keeps going.
4. **Forced exit through the San Diego path:** with `--boot fesd=1` the new
   city must come back to the Arcade (this tests rule 2). The forced test must
   also run **Next Race on every visit to every city of the route**, before Go
   to Menus, repeat visits included. `city_cycle_next.py --fesd` requires at
   least one advance per visit and ignores the `--psp-only-next` filter. Record
   the previous/next race name, same city, game in state 2 and the LoadHeap
   given back (zero); a missing advance or one without a race change fails the
   gate. Baseline failures stay recorded failures; they never skip this step
   silently.
5. **Flat menu:** on the way back from each new city through the normal path,
   the log has `RF2D <city>`, and a `shot` after `until row:players` shows the
   menu covering the screen over black (section 5).
6. **Next Race in the cities whose load was added or changed:** before Go to
   Menus, open the pause menu, select Next Race and wait for the next race:

   ```
   "push start state:2" "select next_race" "press cross" \
       "until state:1 180" "menu" "wait 20"
   ```

   Check the new race name at `*0x619B10 + 104` (`peek 0x619B10 104 108 ...`
   in the driver), city/time, game in state 2, `*0x6144C0 == 0` after the load
   and a clean log. Repeat with a change of time and then of city. The results
   button uses the same callback in the Remix; static equivalence does not
   claim coverage of every race/mode.
