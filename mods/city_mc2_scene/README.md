# MC2 Los Angeles scene in MC3 (experimental)

`city_mc2_scene.mod` renders MC2 Los Angeles geometry above the empty ModCity
kit. This is a *visual scene*, not a playable city conversion: collision,
roads, traffic, props, gameplay zones and minimap are not imported. The
freecam can inspect the scene in native MC2 world coordinates. Real PS2
hardware has not yet been tested.

The original MC2 RSC is at
`$MC2_PS2_ASSETS/resource/losangeles/losangeles.rsc`.
`build_tiles.py` and `pack_scene_queue.py` turn its hood pieces into 49
`mc2t_XXZZ.vcl` bundles on a 500-unit grid, with 400-unit radius per bundle.
The bundles are generated data (98,254,416 bytes total), staged under
`$MC3_HOSTFS/` and backed up in
`Y:/Atomic Chat/modderG_iso_work/mc2_all_tiles/`. `manifest.json` gives the
piece count and size of each tile. The cross-tile offline audit covered all
8,740 unique piece AABBs from the near-LOD source set.

The VCL generator now carries MC2 CPVS vertex colours into each tile packet:
it resolves selected PCP0 colour batches and the CPP0 palette, preserving the
matching geometry/colour ordering per draw. Source audit found 8,153 matched
PCP0/PMD batches; assets without a CPVS colour pass remain flat. `_emissive`
passes and reflection alternatives are not selected yet. The 49 current
bundles contain 21,712 tile-piece placements, of which 16,929 have CPV data;
the largest bundle is 5,147,608 bytes, within the runtime's 5 MiB buffer.
This is implemented in the per-tile VCL path: CPVS indices and VU addresses
are reused between batches, so colours cannot be uploaded once and shared
safely by the separate cached-PMD direct-RSC renderer.

**Clipper templates (fixed 2026-09-25).** The dark triangular cuts that moved
with the camera were not culling: they were the `rv1_code` clipper (+0x608).
It emits every polygon it clips as a triangle *fan* on a copy of VU1
`data[36]` or `data[37]` (picked by bit 0x4000 of `data[34].x`). The generator
copied the *strip* template `data[35]` into both, so each clipped quad drew
`(v1 v2 v3)` instead of `(v0 v2 v3)`: one hole and one stray triangle wherever
geometry crossed the screen edge. Native MC3 VU1 memory (freeroam savestate)
has PRIM 5 (fan) in 36/37 against 4 (strip) in 35. `mc2_vu_blob.clip_template`
now writes fans; `patch_clip_template.py` fixes already built bundles in place
(2 bytes per file, idempotent, VIF-aligned blocks only):

```
python "mods/city_mc2_scene/patch_clip_template.py" "$MC3_HOSTFS/mc2t_*.vcl"
```

HostFS tiles and their source copy
`Y:/Atomic Chat/modderG_iso_work/cpv_native_depth_20260924/all_tiles/` (the
CPVS generation actually staged; `mc2_all_tiles/` is the older pre-CPVS set)
are patched and identical. Before/after captures at the ModCity spawn:
`Y:/temp/clip_fan_20260925/` (`compare_t075_topleft.png`); PRIM count and the
seven baseline TLB misses are unchanged.

**Known: the coloured strip at the bottom is this renderer's texture uploads.**
The bundles upload to fixed blocks from `0x1500` (`mc2_scene_blob.TEX_FIRST`),
which in game is the last 32-line row of MC3's render target
(`0x800..0x1600`), then its Z16 buffer (`0x1600..0x1D00`) and MC3's own
texture ring (`0x1D01..0x3580`). The direct-RSC renderer fixed this on
2026-09-25 by allocating through MC3's ring (see its README, "Why v2"); doing
the same here means cutting the tile packets at their texture switches as that
module does, since the bundles bake TEX0/BITBLTBUF addresses.

The VU depth mapping is aligned to native MC3 values: q[8].z is
`-2047.96875`, q[9].z is `2047.96875`, and q[9].x/y remain `2048.0`.

The mod hooks SetCamera at `0x257AEC`. Every frame it chooses a tile from the
camera X/Z coordinates, updates the view-projection matrix, culls piece AABBs
and submits VCL packets through the game's `lowPsxGfx::AddVCLCall`. It reserves
one 5 MiB game-heap buffer and reuses it for every tile. Switching pauses
submission for eight frames so previously queued packets can drain; this can
cause a brief visual pop. Missing grid cells are sparse/outside the hood and
return an open error if visited. Do not enable this together with another mod
owning the same SetCamera hook (notably `city_mc2_queue.mod`).

For a temporary HostFS test, install the empty ModCity kit and set
`city_mc2_scene.mod = shim` under `[mods]` in `$MC3_HOSTFS/mc3boot.ini`.
`freecam.mod = defer` is useful to inspect the scene. `[boot] mc2l = 0`
submits all visible pieces; a positive value limits the count. Restore the
shared INI after testing. Do not place this experimental module in
`output/release` or assume the HostFS data can be read from the current ISO.

`[boot] mc2c = 0` disables only this mod's per-piece frustum culling;
`mc2c = 1` enables it. With culling disabled, every piece in the loaded tile
is submitted, so GPU cost can rise substantially in dense tiles. The current
diagnostic build uses `mc2c = 0`. The culler reads the live projection matrix,
but its old fixed 1/64-screen-size cutoff was not FOV-aware and could discard
small/distant pieces; keep culling off until that cutoff is corrected and
validated at the user's wider FOV.

Regenerate tiles with Python 3.12:

```
python "mods/city_mc2_scene/build_tiles.py" \
  "$MC2_PS2_ASSETS/resource/losangeles/losangeles.rsc" \
  "Y:/Atomic Chat/modderG_iso_work/mc2_all_tiles"
```

Build the module using the PS2 toolchain:

```
cd "mods"
sh build_mod.sh city_mc2_scene /y/MC3HostFS
```

`output/mods/city_mc2_scene.mod` and `$MC3_HOSTFS/city_mc2_scene.mod`
contain the same build. The 49 bundles must also be present at HostFS root.
Diagnostics in the PCSX2 log: `QLOD` reports tile/size/piece count,
`QSCN` reports visible/sent pieces and frames, `QERR` indicates a load error.

Verification on 2026-09-23: five visual samples (west/south/east/north/center)
in `Y:/Atomic Chat/modderG_iso_work/scene_tiles_runtime_20260923/` and a
49-tile PINE freecam traversal in
`Y:/Atomic Chat/modderG_iso_work/scene_tiles_all_runtime_20260923/`.
The latter logged all 49 unique tiles, kept the camera/game frame advancing
through the last tile, and logged no `QERR`, heap overrun or exception.

CPVS/native-depth test on 2026-09-24: all 49 regenerated bundles passed the
offline VCL/CPV audit and were copied to HostFS. PCSX2 headless reached
`SES=5`, streamed camera tile `0x33`, and continued reporting active `QSCN`
without `QERR`, renderer exception, or heap-overrun messages. The screenshot
capture was inconclusive, so CPVS colour appearance still needs in-game visual
confirmation. HostFS test config uses `city_mc2_scene.mod = shim` and
`city_mc2_rsc_draw_test.mod = 0`; the pre-test HostFS tiles and INI are backed
up under `Y:/Atomic Chat/modderG_iso_work/cpv_native_depth_20260924/before_hostfs/`.
The first dynamic build instead hit `Heap (null) overrun` when allocating a
new 4 MiB tile; the fixed-buffer build is the verified one.

No-cull diagnostic on 2026-09-24: the same startup tile (`0x33`, 672 pieces)
previously submitted 39 pieces with the custom frustum culler; with `mc2c=0`,
all 672 are submitted. PCSX2 reached `SES=5` and continued to 45 seconds with
no mod error or exception. This isolates the custom culler as a likely source
of missing visible geometry, but the visual effect on asphalt/corners still
needs the user's in-game comparison. Tile `0x33` has 458 texture handles and
only three generation-time omissions, so the VRAM cap cannot explain broad
asphalt gaps in that tile. Backup and log:
`Y:/Atomic Chat/modderG_iso_work/culling_diagnostic_20260924/`.
