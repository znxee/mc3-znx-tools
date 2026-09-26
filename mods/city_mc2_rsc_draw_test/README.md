# city_mc2_rsc_draw_test

Experimental direct-RSC renderer for the MC3 PS2 modloader. The `.mod` reads
PMD0 geometry from `host0:/mc2_losangeles.rsc`, flattens the original DMA/VIF
transfers, and submits VCLQ packets through `AddVCLCall` at the live `SetCamera`
hook (`0x257AEC`). It does not call the empty MC3 `mcCity::ModelDraw` stub and
does not extract geometry into `.vcl` files.

The active all-city package contains 294 world-baked components (476 PMD0
packets) plus 534 PMD0 packets used by 429 instanced model types. `RINS` maps
all 723 CMI0 models and 8,500 `.hood` placements (trees, signs, street
furniture, etc.). The runtime has now drawn the full geometry/placement set in
PCSX2. The RSC renderer now uses the original MC2 textures for its 1,068
nonzero handles; CPVS vertex colours and several visual defects remain separate
work, so this is still an experimental renderer.

`preview_models.json` remains the smaller nine-PMD observatory/park/ramp test.
Use `make_rsc_manifest.py` to rebuild that preview, or `make_rsc_city.py` for
the all-static-components manifest currently staged in HostFS.

## Default city renderer, and what the installer copies

This module is the default in `$MC3_HOSTFS/mc3boot.ini`
(`city_mc2_rsc_draw_test.mod = defer`, `modcity_page_pool.mod = shim`,
`city_mc2_scene.mod = 0`). Since 2026-09-26 it reads **only original MC2
files** - nothing generated from MC2 data ships with the mod. Whoever installs
it copies from their own MC2 PS2 disc, with the original names:

    host0:/mc2/losangeles/        <- assets/resource/losangeles/
        losangeles.rsc  losangeles.rnt
        <time>_<weather>_cpvs.rsc  <time>_<weather>_cpvs.rnt   (9 of each)
        textures_<time>_<weather>.rsc                          (9)
    host0:/mc2/losangeles/city/   <- assets/city/losangeles/
        losangeles.lvl  l_*.hood                               (13 hoods)

        ambients_<time>.rsc  ambients_<time>.rnt               (dawn, dusk, midnight)
    and in city/: losangeles.prop

The variant loaded follows `[boot] time` / `weather` (default
`midnight_clear`). **Nothing else**: since 2026-09-26 the VU bootstrap is
built at run time (below), so `mc2_rsc_vu_bootstrap.vcl` is no longer read.

Collision comes from the same `losangeles.rsc` too: `modcity_bound.mod = shim`
builds ModCity's `_bnd` body at load time from its two BND0 blocks, and
`modcity_bnd.pck` is not installed any more (see `../modcity_bound/README.md`).

### VU bootstrap built at run time

The microcode is MC3's own: `rv1_code_main` (EE 0x610A68, three 2048-byte
pieces 0x808 apart, last 1424) and the body of `rv1_code` (0x612120, four
pieces, last 1496), exactly as the game keeps them for its DMA uploads -
checked byte for byte against the ELF. `build_bootstrap` writes MPG chunks of
255 instructions (VU 0 and VU 1000), the VU1 data block (viewport q8/q9,
identity q12..15, GIF templates q35..37 with ABE, q42 = 0.8), the prefill of
VU 100..767 and the GS state (TEX1, CLAMP, TEST_1 0x5100F, ALPHA_1 0x44).
Identical to the old `.vcl` except the view matrix (rewritten every frame)
and the default palette (never needed: the cpvs one is sent every frame).
Marker `RBOT <bytes> <checksum>`, 25120 / 0x3BDEE7A7.

### MC2 props from the original files (2026-09-26)

`city/losangeles.prop` lists 96 templates and 5695 placements (matrix +
template name). Each template is a PRP0 in `ambients_<time>.rsc` (found by
name in its `.rnt`): +0 MOD0 of LOD 0, +4 MOD0 of LOD 2, +0x08..0x10 cull
sphere centre, +0x14 radius. A MOD0: +0 low 16 = materials = packets, +4
material offsets -> {texture handle, colour offset}, +8 packet offsets ->
{offset, qwc}; each packet is a ready VIF stream (positions, ST, vertex
colours, MSCAL 0). In `ambients` "this container" is handle bit **15**; a
material handle with neither bit is the shared bank, 0 is white. Entries are
read with Seek only when used (602 KB of 2.3 MB). Drawn after the city like
an instance: matrix, then per packet the texture and a REF of the packet.
MC2's own rules keep the cost down: props whose template has `Far: 0` end at
250 units, LOD 2 from 100 units. At the spawn 47 props per frame (601 without
the rules, 30 -> 25 FPS), ~30 FPS. Loaded: 75 of 96 templates (the rest have
no PRP0 - race-only or never placed), 5577 of 5695 placements. Composite
parts, animation and physics are not imported. Markers `RPRP` (types ok <<16
| types, rejected <<16 | placements), `RPRB`, `RDPR` (drawn, total), `RPRE`
(error); `[boot] mc2q = 0` hides them.

### Runtime catalog (2026-09-26)

`mc2_rsc_manifest.bin`, `mc2_rsc_instances.bin` and `mc2_rsc_cpvs_map.bin`
are no longer read; the same data is derived at load time, and matches them
exactly (723 models, 1010 PMD0s, 8500 placements, 239 component + 7045
instance CPVS chains, 503 PCP + 174 PMD draws at the spawn):

1. `losangeles.rnt` (RNT0: +4 count, +0x18 records {absolute string offset,
   fourcc, handle}) names the CMI0s; each CMI0 (64 bytes: centre, radius, 4
   slots of 2 handles, near LOD = slots 0/1, else 2/3) is read with
   `Stream::Seek` (0x3996C0) before the pass, since CMI0s are interleaved with
   the PMD0s they name. Targets are sorted by PMD index. Marker `RCAT`.
2. The geometry/texture pass as before.
3. `city/losangeles.lvl` lists the hoods; each `.hood` is streamed 64 KB a
   frame through a line reader: `instance_component` blocks give type (matched
   lowercased to the CMI0 names by hash), extension, 3 rotation rows, position
   and emin/emax (radius = |emax-emin|/2 + 0.03). Models used by a placement
   become instance templates. Marker `RHOO` (placements, rejected).
4. `<tod>_cpvs.rnt`: every `..._0_main` PCP0; `<owner>_l_inst_<type><ext>`
   names a placement by (type, extension) - unique in LA - and the rest are
   components. Marker `RCPN` (components, placements matched).
5. After the cpvs chains are loaded, a component's records come from its own
   refs (for 153 of 239 they are NOT the model's near PMDs, so names alone
   would be wrong): lowest record draws it, all covered skip their PMD0.
   Marker `RCOV`.

Pitfalls met: a function pointer in a `.mod` is not relocated (the call went
to address 0); closing a game stream twice also jumps to 0; float literals go
through a `noinline` helper, or the packer rejects two `.rodata` loads sharing
one `lui`. `make_rsc_city.py` / `make_rsc_cpvs_map.py` remain as offline
cross-checks only.

### Textures from the original files, with mipmaps (2026-09-25)

`mc2_rsc_texture_bank.bin` is **no longer read**. The sequential pass over
`losangeles.rsc` that loads the PMD0s now also keeps every TEX0/MIP0/PAL0
entry (planned from the directory, 2.9 MB), and `textures_<time>_<weather>.rsc`
(the shared bank, 1 MB) is read whole after it; 554 images, deduplicated by
MIP0+PAL0. MIP0: `+0x08` level offsets, `+0x28` 5 = 8 bpp / 6 = 4 bpp and
levels - 1, `+0x48` u16 level sizes in 256-byte blocks, `+0x58` u8 TBW per
level. Each level is the memory image of a PSMCT32 rectangle, `(w/2)x(h/2)`
words for 8 bpp and the block table for 4 bpp (the 16x16 4 bpp level 0 is 128
packed bytes, sent as PSMT4), so it is uploaded as is, like MC2 did: a 112-byte
arena header (`NOP NOP FLUSH DIRECT 6`, A+D BITBLTBUF..TRXDIR, IMAGE tag) and
the texels REF'd from the loaded file with `DIRECT qwc` in the REF's own VIF
code (data quadword aligned; no DIRECT is ever split mid-data by a tag). The
switch sends TEX0, TEX1, MIPTBP1/2 and TEXFLUSH; TEX1 is MC2's own, read from
an MC2 savestate: LCM 0, MMAG linear, MMIN 4, L 0, K -3.5, MXL per texture.
Level layouts were checked offline (level 1 decoded vs level 0 downscaled, all
formats) and in game (A/B with `[boot] mc2m = 0`, level 0 only: differences
follow fine texture detail, strongest far away). At the spawn: 410 uploads,
2.8 MB per frame, 0 dropped, ~30 FPS; free heap after everything 5.3 MB with
`modcity_page_pool`. Markers `RTEX` (images, bytes kept), `RDTE` (texture error:
2/3 plan/memory, 4 read, 5 arena memory, 6/7/8 shared bank open/memory/format,
9 tables).

### Asphalt and MC2's blending (2026-09-25)

Roads, sidewalks and crosswalks were drawn but invisible or dark. Three causes,
values read from the GS and VU1 memory of an MC2 savestate:

- MC2 blends everything: PRIM has ABE and ALPHA_1 is `(Cs-Cd)*As+Cd`, TEST_1
  is alpha NOTEQUAL 0 failing to FB only, Z GEQUAL. The bootstrap now sets
  ABE in the VU GIF templates (data[35..37]) and ALPHA_1 0x44 / TEST_1
  0x5100F, so layers such as `fx_reflection_01` over the asphalt blend.
- The ground textures of the shared bank (`l_roads_ashphalt*`, sidewalks)
  have alpha 0 in every CLUT entry; the channel carries nothing. With TCC=1
  they blended to nothing. A texture whose whole CLUT alpha is 0 now switches
  with TCC=0 (alpha from the vertex); any other keeps TCC=1.
  Revised 2026-09-26: eight more ground textures of the shared bank
  (`l_roads_crosswalk04`, ghetto/freeway asphalt, two sidewalks) have a faint
  CLUT alpha, at most 0x1F - not opacity either; the crosswalks drew almost
  transparent (dark bands at the crossings). In the shared bank TCC=1 now needs
  some alpha >= 0x40; in the city container any non-zero alpha keeps TCC=1.
  Checked from above at `l_hollywood_int_10` (-253, -589).
- data[42], the colour of models without CPVS (program 8), was an orange test
  value (0.95, 0.65, 0.25) from `mc2piece.bin`; MC2's own is a neutral 0.8.

Checked with a camera put over `l_industrial_rd_17` (asphalt with lane
markings, from above and at street level; captures
`Y:/temp/asfalto_20260925/`). The ModCity spawn is inside the
convention-centre block, where there is no street.

Diagnostic `[boot]` keys added: `mc2x`/`mc2y`/`mc2z` put the game camera at a
world point, `mc2d = 1` makes it look straight down, `mc2w = 1` draws every
texture white, `mc2m = 0` level 0 only, `mc2r` draw radius. Marker `RCAM`
prints the camera X/Z.

### CPVS (per-vertex lighting)

MC2 draws a lit model with a PCP0 chain from the cpvs file, not with its PMD0:
the PCP0 carries the colours inline and `ref`s geometry payloads inside the
PMD0s. The runtime now does the same:

- PMD0 blocks are kept **raw**, as stored in the RSC. The low half of each DMA
  tag is rewritten to `VIF NOP (IMMEDIATE = the tag's QWC), NOP`, so the block
  is a valid VIF stream and a PCP0 `ref` is simply `block + offset`. Segments
  (cut after each texture CALL) and PCP0 tags are sent as REF tags written by
  `emit_ref`, a copy of `lowPsxGfx::AddVCLCall` that puts the chain's own two
  VIF codes in the tag (the VIF1 channel runs with TTE, as in MC2).
- A `ref` names its PMD0 only modulo 4096 (LA has 4436 entries). The runtime
  takes the candidate whose own tag right before the offset carries the same
  QWC (that is why the NOP keeps it); first-fit on bounds sent 60 of LA's
  10,190 refs to the wrong PMD and the VU into garbage.
- `mc2_rsc_cpvs_map.bin` (`RCPM`, `make_rsc_cpvs_map.py`, 38 KB) says which
  LOD 0 `_main` PCP0 draws each record/placement: 239 components, 7,045 of the
  8,500 placements; the rest keep the flat PMD0 path. PCP0 numbering is
  identical in all nine variants.
- The cpvs file is read a 256 KB slice per frame after the geometry, keeping
  only the PCP0s the map uses and the CPP0 palette (1.9 of 3.0 MB); the
  palette is unpacked to VU 768 every frame after the bootstrap.
- Texture switches are sent only when the TEX0 in effect changes (MC2 repeats
  the CALL after nearly every batch; each switch has a FLUSH), and texture
  handles are found by binary search (a linear scan per CALL cost ~0.3 ms of
  EE per building: 30 -> 19 FPS).

At the ModCity spawn: 503 PCP0 draws + 174 PMD0 draws, 0 failures, 303
uploads, ~30 FPS, `midnight_clear` and `dusk_clear` checked. Captures:
`Y:/temp/cpvs_20260925/` (`test8`, `dusk`).

**Memory is the limit.** The game's allocator does not return NULL when full:
it prints `Heap (null) overrun` and stops. Measured free heap: 9.4 MB when the
module starts, 2.3 MB after geometry, 0.43 MB after CPVS. Every large block now
asks first (`heap_room`, 320 KB margin) and fails with an error marker instead;
without room for CPVS the city is drawn flat (`RCPE 7`). To make room the
instance matrices are kept as bare 64-byte matrices (the FLUSH/UNPACK travels
in the REF tag), per-frame packets are written through the uncached alias
(`| 0x20000000`) and emitted directly (no call list), and the upload arena is
420 events. `RHMN` reports the lowest free heap seen.

**Freeze when driving fast (fixed 2026-09-25).** At speed the frame reached
~970 CPVS chains and more uploads than the arena holds; the runtime then cut
the chain or piece in the middle, the VU was left with half a batch and the GIF
hung ("GS packet size exceeded VU memory size"): all game threads waiting,
EE in the idle thread, no more frames (user savestate at 235 km/h). Now a draw
is started only if the arena can take every upload it may need (`RDSK` counts
skipped draws); once started it is always sent whole, and if an upload still
fails the texture in effect stays. Arena packets lost their VCLQ header
(256 bytes per upload, 498 uploads in the same memory). Reproduced and checked
parked at the spawn with `[boot] mc2r = 3000` (draw radius, diagnostic):
1.5 million overflow messages before, none after, frames keep coming.

Markers: `RCPS` (cpvs entries, bytes kept), `RCPE` (cpvs error: 1 open, 2
size, 3/7 no memory, 5 directory, 6 no palette, 8-10 read), `RCME` (map
error), `RDCP` (`pcp<<16|pmd` draws, failed chains), `RHEP` (free heap at
load steps), `RHMN`. Diagnostic `[boot]` keys: `mc2p` (bit 0 components, bit 1
instances use CPVS; default 3, `0` = flat), `mc2n` (max CPVS chains per frame).

## HostFS files and build

- `mc2/losangeles/losangeles.rsc`: original geometry and CMI/PMD data (the
  module reads this path now; `mc2_losangeles.rsc` at the HostFS root is the
  same file, kept for the generator commands below).
- `mc2/losangeles/*_cpvs.rsc`: the original CPVS files.
- `mc2_rsc_cpvs_map.bin`: `RCPM` v1, regenerate with the manifest/instances:
  `python make_rsc_cpvs_map.py <assets/resource/losangeles/losangeles.rsc> mc2_rsc_manifest.bin mc2_rsc_instances.bin OUT...`
- `mc2_rsc_manifest.bin`: `RSCM` v2. The 16-byte header is
  magic/version/total-bytes/count; each 28-byte record contains PMD index,
  dense model ID, static/instance flag, and CMI centre/radius. Records are
  sorted by PMD index for sequential RSC reads. The runtime allows 1,536 PMDs.
- `mc2_rsc_instances.bin`: `RINS` v1, with 723 model rows, 8,500 placement
  bounds, and one 96-byte matrix VCLQ packet per placement. Keep it paired
  with the matching RSCM v2 and `.mod`.
- `mc2_rsc_texture_bank.bin`: `RCTX` **v2** (2026-09-25; the runtime rejects
  v1 with `RDTE 4`). 2,874,384 bytes: 1,069 handles including handle 0,
  deduplicated to 524 images (10,296 texel + 698 CLUT blocks in total, largest
  image 260). v2 holds **no VRAM address**: texel/CLUT data are ready VCLQ
  packets (`NOP, DIRECT n` + IMAGE), and the address-carrying parts are
  templates (the A+D header of each transfer with DBP 0, and TEX0 with
  TBP/CBP 0). Layout in the docstring of `make_rsc_full_texture_bank.py`.
  Regenerate it **together** with the manifest and instance bank;
  `mc2_rsc_target_texture.vcl` is not used.

  **Why v2 / the coloured strip at the bottom.** v1 put the textures at fixed
  blocks `0x1500..0x3fff`, a range free in the standalone MC2 scene but not in
  game. Savestate at the ModCity spawn: MC3 renders into `FRAME FBP 0x40`
  (blocks `0x800..0x1600`, 512x448 PSMCT32), Z16 at `ZBP 0xB0`
  (`0x1600..0x1D00`), display CT16 at 0, its own per-frame texture ring from
  base `0x1D01` (`0x70FBF0`) up to top `0x3580` (`0x70FBE8`), render targets
  above the top. `0x1500..0x1600` is the last 32-line row of the render target
  (the strip), and the rest overlapped Z, MC3's textures and render targets.
  The runtime now uploads through MC3's ring the same way `rmcTexturePS2`
  does: allocate from `0x70FBEC`; if it does not fit below the top, restart at
  the base and increment the generation at `0x700005C0` (MC3 re-uploads any
  texture whose `+0x7C` stamp is older; `FlushTexMem` 0x52C7D8 does the same
  every frame). `AddVCLCall` writes REF tags into the same scratchpad packet
  (`0x700005AC`) that MC3's texture binds use, so allocation order is GS order.
  Each piece is split at its texture CALLs into segments; per frame, pass 1
  walks the draws in order, uploads an image the first time it is needed since
  the last wrap and writes its header/TEX0 packets into one of two arenas
  (the VIF reads the previous frame's REFs asynchronously from the MFIFO);
  `FlushCache(0)`, then pass 2 issues the calls. `RDVR` reports
  `wraps<<16|dropped` and `ring current<<16|calls`. At the spawn: 306 uploads
  (291 images, 15 re-sent after one wrap), 1,958,720 bytes, 0 dropped, 7,625
  calls, same 593 instances / 863 PMD draws, ~30 FPS; the bottom strip is gone
  (mean saturation of rows 645..690 64.3 -> 5.7). Captures:
  `Y:/temp/vram_ring_20260925/`.
- `mc2_rsc_vu_bootstrap.vcl`: VU program, view-projection data and GS state.
  The generator uses native MC3 VU1 Z constants `-2047.96875` and
  `2047.96875` with `GEQUAL`, correcting the old MC2-vs-traffic depth bias.
- `city_mc2_rsc_draw_test.mod`: runtime module.

`make_rsc_city.py` needs the original asset-tree layout: the source RSC must
sit in `assets/resource/<city>/` with its `.hood` files in the sibling
`assets/city/<city>/`. A HostFS-only copy of the RSC is not sufficient to
regenerate the all-city placement bank. It now generates the real texture bank,
not the former white-alias bank. If only the texture bank needs regeneration,
the existing RSCM and HostFS RSC are enough:

```powershell
python "mods\city_mc2_rsc_draw_test\make_rsc_full_texture_bank.py" `
  "$MC3_HOSTFS\mc2_losangeles.rsc" `
  "$MC3_HOSTFS\mc2_rsc_manifest.bin" `
  "tools/output\mods\mc2_rsc_texture_bank.bin" `
  "$MC3_HOSTFS\mc2_rsc_texture_bank.bin"
```

For the smaller neighborhood preview, generate both files after editing
`preview_models.json`:

```powershell
python "mods\city_mc2_rsc_draw_test\make_rsc_manifest.py" `
  "$MC3_HOSTFS\mc2_losangeles.rsc" `
  "mods\city_mc2_rsc_draw_test\preview_models.json" `
  "tools/output\mods" `
  "$MC3_HOSTFS"
```

The preview generator resolves each named model's PMDs from CMI0 and rejects
duplicate/missing PMDs and texture-bank overflow. Both generators sort PMDs
into RSC directory order so the runtime can keep one sequential stream open
instead of rereading the RSC from the beginning for every piece.
To rebuild the module, from `$MC3BOOT/mods` in the configured PS2 toolchain:

```sh
sh build_mod.sh city_mc2_rsc_draw_test /y/MC3HostFS
```

To regenerate the unchanged VU bootstrap:

```powershell
python "mods\city_mc2_rsc_draw_test\make_vu_bootstrap.py" `
  "$MC3_HOSTFS\mc2piece.bin" `
  "tools/output\mods\mc2_rsc_vu_bootstrap.vcl" `
  "$MC3_HOSTFS\mc2_rsc_vu_bootstrap.vcl"
```

Keep `city_mc2_scene.mod = 0` and `city_mc2_rsc_draw_test.mod = defer` in
`$MC3_HOSTFS/mc3boot.ini`: both modules own the same camera hook. Do not use
`shim` for this build: its 155 KB `.mod` exceeds the boot shim reader's 64 KB
buffer and is rejected as truncated. `defer` is safe here because SetCamera is
a continuous hook; core installs it on the first frame. Keep `rscw = 1` under
`[boot]` for original MC2 world coordinates, and leave `rins` unset (enabled by
default). Move freecam into MC2 coordinates to inspect other districts. Remove
`rscw` or set it to `0` for the old camera-relative preview. The runtime reads
the RSC directory once, advances in PMD-index order, loads at most four PMDs
per frame, and caps each forward skip at 16 KiB per frame. Ready models are
sphere-culled at `800 + CMI radius` and checked against the camera's four side
planes and positive-W plane. This is not cell/PVS streaming, and large
bounding spheres can still admit off-screen geometry.

`RDMF` reports manifest count, sampled `RDRW` records report loaded PMDs,
`RANC` reports placement, and `RDAD` gives visible PMDs submitted per frame.
`RSCS`/`RSCD`/`RSCE` report RSC streaming start/completion/error. `RDME`,
`RDER`, `RDBE`, and `RDTE` report manifest, geometry, bootstrap, and texture
errors. The PMD parser uses a temporary 64 KiB ceiling but compacts each
successful packet to its actual size before keeping it in game memory.

## Validation and limits

The nine-PMD neighborhood preview reached 110 seconds in PCSX2 headless;
`RDAD` reported all nine visible blocks without VIF errors/traps. Captures and
logs for that stage are under
`Y:/Atomic Chat/modderG_iso_work/rsc_manifest_20260924/`.

The latest PCSX2 run loaded all 1,010 PMDs, validated 723 models/8,500
placements, completed the RSC scan in about 23 seconds, and continued to 150
seconds at `SES=5` (ModCity). `RDAD` remained active; at the fixed camera it
reported 1,904 visible instances and 2,373 visible PMD draws. No renderer,
VIF, or exception error appeared. The seven early TLB misses at
`0x3f3ae148` match the previously documented baseline. Captures and log are
under `Y:/Atomic Chat/modderG_iso_work/rsc_allcity_20260924/current_mod_defer_test/`
and `$MC3_PCSX2_DATA/logs/cli_teste.txt`.

The user's freecam inspection says the current geometry appears aligned; this
does not yet validate every district or join. The 2026-09-25 RSC texture test
restored real materials by packing texture and CLUT allocations together across
the whole reserved VRAM range (instead of splitting it at `0x3800`), sharing
identical images, and sending only visible PMD uploads each frame. PCSX2 reached
`SES=5`, `RSCD=1010`, and 75 seconds without a new RSC/VIF/exception error;
the fixed spawn showed textured walls, windows and signage near 30 FPS.
Screenshot and log: `Y:/Atomic Chat/modderG_iso_work/rsc_real_textures_20260925/test1/`.
This does not fix the dark cutouts/road appearance or the coloured VRAM artifact
along the bottom of the image. The full bank is resident in EE memory, and
the PS2-hardware VRAM lifetime/performance still needs validation.

On 2026-09-25 the runtime sphere/frustum test was corrected to take its
positive-W and side-plane coefficients from the columns of the row-vector
view-projection matrix. The previous code mixed contiguous rows and could
reject visible models as the camera turned. At the ModCity spawn, the full
renderer changed from 2,373 submitted PMD draws / about 16k PCSX2 primitives
to 863 submitted draws / about 46k primitives; the newly selected geometry
fills several of the large gaps. Both runs reached `SES=5`, loaded all 1,010
PMDs, and had no renderer error. The paired screenshots and logs are in
`Y:/Atomic Chat/modderG_iso_work/rsc_cull_planes_20260925/`.

Some dark triangular cuts remain. With instances disabled, 79 static PMDs
passed the corrected frustum; a temporary diagnostic submitted all 476 static
PMDs and the cuts stayed in almost the same places. A second diagnostic changed
the bootstrap's unused VU vertex-slot fill and also left the pattern nearly
unchanged. Reversing the draw order of the 79 static PMDs likewise kept the
pattern. All diagnostics were removed; the original bootstrap was restored.
The remaining cuts therefore need investigation inside the submitted PMD/VIF
geometry, VU clipping, or GS draw state. The `city_mc2_scene` tiled renderer is
a separate path and was not changed by this fix.

A further 2026-09-25 A/B called MC3's own
`gfxViewport::PrepFastSphereVisCheck`/`FastSphereVisCheck` on the MC2 bounds
from this hook (after the game's `SetCamera`). At the same spawn it selected
79 static PMDs, exactly the corrected custom count, and with instances enabled
593 instances / 863 PMD draws, again the corrected count. The dark cuts were
still visible in both captures. Native-culler diagnostic code and bootargs were
removed, and source, INI and `.mod` hashes match their pre-test backups.
Captures/logs are in
`Y:/Atomic Chat/modderG_iso_work/native_culling_20260925/`.

CPVS colour support was continued in the separate `city_mc2_scene` tiled-VCL
renderer, not in this direct-RSC prototype. Its cached shared PMD packets do
not preserve per-instance CPV streams: CPVS indices and VU addresses are reused
between batches, requiring colour data to be interleaved for each draw. For
CPVS testing, enable `city_mc2_scene.mod = shim` and disable this module because
both own `SetCamera`. The tiled VCL generator now uses native MC3 depth
constants (`q[8].z=-2047.96875`, `q[9].z=2047.96875`, with q[9].x/y unchanged at
`2048.0`); it no longer has the old `-2000`/`2048` mapping.

**Dark cuts fixed 2026-09-25.** Their cause was the bootstrap's clipper
GIFtag templates, not culling, draw order or VU prefill: `data[36]`/`data[37]`
were copies of the strip template `data[35]`, while the `rv1_code` clipper
emits clipped polygons as fans (native MC3 uses PRIM 5 there). The installed
`mc2_rsc_vu_bootstrap.vcl` differs from the previous one in exactly those two
bytes, and `make_vu_bootstrap.py` now produces it. At the ModCity spawn the
large wedge in the upper left is gone with the same ~46.6k primitives;
comparison `Y:/temp/clip_fan_20260925/compare_rsc_t075.png`. See
`city_mc2_scene/README.md` for the mechanism and `patch_clip_template.py`.

Still absent from this direct-RSC module: PCP/CPVS per-vertex colour chains,
collision, cell/PVS culling, and PS2-hardware validation. The coloured VRAM
strip is fixed here (RCTX v2 + MC3 ring, above) but NOT in the tiled
`city_mc2_scene` renderer, whose 49 bundles still carry fixed `0x1500..`
addresses. Unverified on hardware: the VIF `DIRECT` data in every VCLQ packet
of this module (as in v1) starts 8 bytes into a quadword, which PCSX2 accepts. For the latest CPVS/native-depth status and
test configuration, see `city_mc2_scene/README.md`.
