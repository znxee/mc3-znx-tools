# city_mc2_rsc_draw_test

Experimental direct-RSC renderer for the MC3 PS2 modloader. The `.mod` reads
PMD0 geometry from the active MC2 city under `host0:/mc2/<city>/`, flattens the
original DMA/VIF transfers, and submits VCLQ packets through `AddVCLCall` at
the live `SetCamera` hook (`0x257AEC`). It does not call the empty MC3
`mcCity::ModelDraw` stub and does not extract geometry into `.vcl` files.

One binary supports Los Angeles (MC3 city 5) and Paris (city 6), selected at
runtime through `payload/mc2_city_config.h`. Paris contains 1,493 PMD0 records,
699 CMI0 records (270 components plus 429 instanced types), 8,195 `.hood`
placements and 11,921 PCP references. The validated dawn/clear run resolved
178 component chains and 7,460 instance chains; 4,460 instances needed PMD0
pieces not referenced by their PCP0. The renderer uses the original MC2
textures, CPVS colours and props for either city. It remains experimental.

`preview_models.json` remains the smaller nine-PMD observatory/park/ramp test.
Use `make_rsc_manifest.py` to rebuild that preview, or `make_rsc_city.py` for
the all-static-components manifest currently staged in HostFS.

## Default city renderer, and what the installer copies

This module is the default in `$MC3_HOSTFS/mc3boot.ini`
(`city_mc2_rsc_draw_test.mod = defer`, `losangeles_page_pool.mod = shim`,
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
    host0:/mc2/paris/             <- assets/resource/paris/
        paris.rsc  paris.rnt
        <time>_<weather>_cpvs.rsc/.rnt                          (9 of each)
        textures_<time>_<weather>.rsc                           (9)
        ambients_<time>.rsc/.rnt                                (3 of each)
    host0:/mc2/paris/city/        <- assets/city/paris/
        paris.lvl  p_*.hood  paris.prop  paris.aib              (7 hoods)
    host0:/mc2/tune/phys/          <- assets/tune/phys/   (prop masses, 453 files)

The variant loaded follows `[boot] time` / `weather` (default
`midnight_clear`). **Nothing else**: since 2026-09-26 the VU bootstrap is
built at run time (below), so `mc2_rsc_vu_bootstrap.vcl` is no longer read.

Collision comes from the same active city's `.rsc` too:
`losangeles_bound.mod = shim` builds the `_bnd` body at load time from its two
BND0 blocks. Neither `losangeles_bnd.pck` nor `paris_bnd.pck` is installed (see
`../losangeles_bound/README.md`).

### When it runs, and giving the heap back (2026-09-27)

The module only works while a race (or its replay) in a supported MC2 city is
loaded: mcRaceConfig current (0x619B10) +0 is 5 or 6 and gGameState
(*0x6199F0) +4 is 2/3
(ChangeState 0x1A55D0: 1 boot, 2 game, 3 replay, 4 movie, 5 garage, 6 race
editor, 7 front end, 8 quit). The front end draws a live city behind its menus
and the module used to start there, fail its arena (texture_error 5, final)
and stay dead for the race that followed.

Every block it takes goes through `r_alloc`/`r_free` (a table of up to 2560
blocks). `release_all` closes the streams, frees them all and zeroes State, so
the module is as freshly loaded. It runs from `frontend_hook`, on the jal to
mcGameState::EnterStateMC3Frontend (0x1A5660): no SetCamera runs between a
race and the front end loading its background city, and San Diego alone asks
for 8.9 MB. The same hook turns the current/next race configs of an added city
(4 and up) back to San Diego: the front end reloads the last race's city and
aims its menu camera through tune/ui/<lang>/menucamera.ui, which only knows
sd/atlanta/detroit/tokyo. Markers `RREL <blocks> <largest free>`,
`RFEN <city found> <blocks>`. Memory checks use `mc3_heap_largest` (largest
free block), not the cursor-top gap.

### Paris validation (2026-09-28)

Paris uses `_p_inst_` names in its CPVS table; treating the prefix as the LA
literal `_l_inst_` left every instance chain unresolved. The prefix now comes
from the shared city configuration. Markers from the final Arcade load:
`RCPN 0xB2 0x1D24`, `RCOV 0xB2 0xB2`, `RCOI 0x1D24 0x116C`, `RPRP 0x58 0x78`
and `RPLD`. The shared texture bank has only one texture with non-zero alpha,
`texture/p_roads_sidewalk01.tex`, and its maximum alpha is 0x0E; TCC remains
zero as it is for Los Angeles.

### Spectator camera and the race tour (2026-09-27)

`[boot] mc2s = N` (diagnostic): the camera chases the AI opponents, N seconds
each, instead of the player - whole races can run unattended and be
photographed along every route. Same technique as the Orbit mode of
AlgumCorrupto's freecam (CinematicClub,
FreeCam/OpponentCam.cpp): every car is an instance with vtable 0x00627630,
Matrix34 at +0x10; the player's own (*(player+20)+0x18) is skipped. The heap is
scanned for them when the list is empty, stale or at each switch. Look-at from
7 m behind and 2.5 m above. Marker `RSPC <cars> <switches>`.
`mc3_race_tour.py` (tools folder) runs a list of races with it - race_nofe
straight into each one, time/weather/type from the .locinf, a screenshot every
few seconds, a contact sheet and the log per race - and restores the .ini.

`mc2s = Nt` watches the AMBIENT TRAFFIC cars instead (aiAmbientTrafficData
+304, 560 bytes each, position +100 and current rail pointer +264), `Ntf` also
puts the player 30 m above the watched one. `RSPT` reports the active count and
selected ordinal, `RSPR` reports its current rail/flags/width, and `RSPA`
reports how many active cars are on bit-3 pedestrian rails. Traffic only gets
a body near the player, so from afar the camera shows the road without the car.

`mc2s = Nf` also makes the player's car hang 30 m above the watched opponent
every frame (`mc3_race_tour.py --follow`), moved the way the AI respawn moves
cars: aiBrain::TeleportCar 0x3FC600 reads only brain+12 -> opponent and
opponent+36 -> car, so a fake two-word brain pointing at *(player+20) works.
Every 2 s the watched car's aiOpponent state is logged (aiOpponentManager
0x618B68: count +0, array +8, 1136 bytes each; aiStuck at +116 with state +8,
count +20, really-stuck +92; aiBrain at +120, state type +8). Markers
`RFOL/RFOY/RFPL` (opponent and player positions), `RAIS/RAIP/RAIU` for the
watched car, and `RAAS/RAAP/RAAU` for every active opponent. The latter
remain available without `--follow`, so a camera tour can detect a stalled
car while the camera watches another one. `analyze_paris_ai_tours.py` groups
these per-opponent positions and aiStuck reports across a tour log.

`mc2s = Nc` (CHASE, `mc3_race_tour.py --chase`, 2026-09-29): the player's car
is put 14 m behind the watched opponent on the ground every frame and the
game's own camera draws. Use it for anything about what MC3 culls: in
mcGame::PreDraw (0x1A14B0) the occluder viewer and the cull frustum come from
the LOCAL PLAYER's camera, not from the camera this renderer is handed, so in
`N` / `Nf` the occlusion is judged from wherever the player's car is. With an
empty `<city>.occlude` that goes unnoticed; with occluders loaded the watched
car vanishes whenever it is outside the player camera's frustum.
Only in spectator runs, every 30 frames: `ROCC <racer << 24 | visible> <x << 16
| z>` per mcPlayerManager (0x61B1E0) racer (rmcCarModel::CullUpdate 0x2F6278
stores IsAABoxOccluded == 0 at model+64; only the player is a racer there -
opponents and traffic are culled by DualFrustumVisCheck), `ROCV/ROCY` the
occluder viewer position.

### Light reflections: the frame alpha is the reflectivity mask (2026-09-29)

MC3 draws the light reflections on the wet road (headlights, traffic, city
lights, sun/moon - mcGlow::drawAllBufferedReflections) in pass 4 of
mcCullableMgr::Render into the city env map's target, and
mcCityEnvMap::CopyReflectedObjectsToFrame (0x561F18, called at 0x24AB80) pastes
it with ALPHA 0x58 = Cs * Ad + Cd: multiplied by the alpha already in the frame.
That alpha is how much each surface reflects. MC2's palette has 0x80 in every
entry, so every MC2 wall and road was a perfect mirror - reflections through
walls and far too strong on the road, even though the tune values
(tune/<city>_<tod>.parfileio: m_roadShiniess, m_reflection*Mod) are tokyo's.
Now, after the city and the props (reflect_mask_pass): the frame's alpha is
cleared to 0 by a full-screen sprite (FRAME FBMSK 0x00FFFFFF = alpha only, no
depth test or write), then the `_0_refl` ground is drawn again, alpha only,
with a palette whose alpha is `road_alpha()` ([boot] mc2a, default 10 - the
user's pick against vanilla; first was 78 =
m_roadShiniess 0.61 x 128) and depth test GEQUAL without Z write - so only the
ground really visible reflects; walls, props, leaves don't. Colours, blending
and depth are untouched ([boot] mc2m = 0 turns the pass off). A first try that
wrote the mask with the vertex alpha itself (alpha-1 palette, FIX blend)
broke every cut-out texture - leaves, palms, pylons, wires lost their depth.
Also ZBUF_1 with Z write on for
the city (`mc2z = 0` to disable) and back to MC3's state after.
Tried and left OFF (`mc2e = 1`): drawing the city after the env map block
(hook at 0x24ABC0, after DrawSky/DebugDraw) - it hides reflections too, but
paints over MC3 objects already drawn and upsets MC3's state even with
rmcState::Dirty/DoFlush/UpdateLightMatrices after it.
Debugging tip: a savestate holds the free camera: search EE RAM for a
Matrix34 whose position is the one on screen, then `mc3_cam_tour.py
--view=eye,target` (it now boots the city's Arcade cruise, which has traffic).

### MC2's own reflections: texture wetness and glow cards (2026-09-30)

MC2 reflects the same way MC3 does. Its frame's DMA list (walked in an MC2
savestate from D1_TADR back to the first tag, 8162 tags) ends with pastes at
ALPHA 0x58 = Cs * Ad + Cd and 0x54 = (Cs - Cd) * Ad + Cd, FRAME with the alpha
masked and depth test ALWAYS: the frame alpha is the reflectivity there too.
Two things feed it that the port ignored:

- **Texture wetness.** The ground textures of the shared bank
  (`textures_<tod>_<weather>.rsc`) carry in their alpha how wet each texel is,
  per weather. LA, CLUT alpha max / mean: clear asphalt 0 / 0, cloudy 29 / 7,
  rainy 104 / 25; rainy sidewalks 128 / 30; ghetto asphalt rainy 128 / 40 - the
  puddles. That is why those textures looked like "alpha 0 = colour only"
  (`tcc = 0` for drawing, which stays). `reflect_mask_pass` now draws the
  `_0_refl` ground a second time with TCC=1 on every texture switch
  (`bind_switch` appends the TEX0 with TCC set, CLUT not reloaded) and a
  palette whose alpha is `wet_scale()` (`[boot] mc2u`, 0..128, default 128 =
  as MC2; 0 = off), alpha test GREATER `road_alpha()` with KEEP: the frame
  alpha becomes max(mc2a, wetness). Clear weather looks as before; in the rain
  the puddles mirror.
- **Glow cards.** 68 models / 530 instances in LA (37 in Paris) named
  `*cardblk*` are vertical cards (`fx_reflection_01`, grey glow on black, alpha
  = luminance) standing 1.4-20 m BELOW the ground under shop windows: the
  windows mirrored. Drawn with the city they were hidden under the ground - or,
  where our ground has holes, showed at full strength (Paris). Now they are
  kept out of the normal pass (`cards[]`, `card_model` bits set by the catalog
  from the name) and drawn at the end of `reflect_mask_pass` like MC2 pastes:
  FRAME FBMSK 0xFF000000, TEST 0x3000F (alpha != 0, Z ALWAYS, no Z write),
  ALPHA 0x58. They add light only through the reflective ground actually
  visible. `[boot] mc2k = 0` turns them off (not mc2c: the old city_mc2_scene reads that one) (and they are not drawn at all).

No back-face culling in the VU program: the cards show from both sides.

### `_0_refl` chains colour the ground pieces (2026-09-29)

The `<tod>_cpvs.rnt` names two LOD 0 chains per model, `_0_main` and
`_0_refl`. The second is not a second pass over the same geometry: it colours
the OTHER near pieces - road deck, sidewalks, gutters - that no `_main`
references (LA: 309 pieces, 199 components and 1011 instances; Paris: 343, 208
and 1024). Same format and program (MSCAL 10). Without them that ground was
drawn with the flat colour: a bright seam against the lit asphalt beside it.
Both are read now (refl in a second pass after every main); instances keep the
`_refl` index in `inst_refl[]` and the pieces it covers in bits 20..23 of
`instance_word` (drawn flat if the chain cannot be queued). Markers:
`RCPN <component chains> <refl << 16 | instance mains>`, `RCOI <instances with a
chain> <pieces left out << 16 | with a refl>`. About 210 KB more of the cpvs
file is kept (LA 2.1 MB). `mc2_cpv_probe.py --city C x z` lists what stands at
a point and which of its pieces get no colour.

### Instances: pieces their CPVS chain leaves out (2026-09-27)

An instance's `_0_main` PCP0 does not always draw its whole model. In 100 of
LA's instanced types (1265 of the 7045 instances with a PCP0) it takes only
the first near PMD0; the others have no PCP0 at all and MC2 draws them
straight from the PMD0. They are the model's ground: freeway and road decks
(`8lanefwy_*`, `4laneroad_*`, `2laneroad_*`), warehouse yards, office
parking. The renderer drew the PCP0 alone, so that ground vanished - the
background colour showed through - as soon as the CPVS was in (seen by the
user: asphalt there during the load, gone after). `cover_instances()` walks
each instance's PCP0 refs once and keeps, in bits 16..19 of `instance_word`,
the model pieces it leaves out; they are drawn flat after the PCP0. Marker
`RCOI <instances with a PCP0> <of those, with pieces left out>` (LA: 7045 /
1265). Measured with `[boot] mc2p` 0/1/2/3 at the same camera points: only the
instance CPVS (2) lost the ground; components were already handled per piece.

### Camera tour and collision drop test (2026-09-27)

`[boot] mc2t = N`: the camera shows each point of `host0:/mc2_camtour.txt`
("eye xyz target xyz" per line) for N seconds, in a loop. Marker
`RTOU <index> <x|z>`. `mc3_cam_tour.py` (tools) writes the file from
`mc2_ground_coverage.py`'s clusters or `--at` points, boots once, and names
each screenshot after the point it shows (matched through RTOU).

`[boot] mc2g = F`: once the city is loaded, the player's car is dropped 1.5 m
above each point of `host0:/mc2_droptest.txt` ("x ground_y z") and left F
frames; `RDRP <index> <final y*16>`, `RDRX <x> <z>`, `RDRE` at the end.
`mc3_drop_test.py` (tools) runs it and classifies ok / fell / moved / high.

`mc2_ground_coverage.py` (tools) is the offline side: collision ground vs
drawn ground (near LOD, components + instances) on a 2 m grid, texture state
as the renderer resolves it, `coverage.png`, `clusters.tsv` (ground not drawn),
`no_collision.tsv` (lowest drawn surface with no collision) and
`drop_points.txt`. LA: every texture handle resolves (0 rejected), 15 cells of
handle 0. The big "not drawn" clusters that stay empty with and without CPVS
have no MC2 geometry in any LOD (block interiors); the 233 "no collision"
clusters are the same in MC2's own BND0 (hills, under-freeway strips) - the
drop test confirms a car falls through them.

### Pins: marking problems while driving (2026-09-27)

USB keyboard (the snapshot freecam reads; number row, no clash with freecam):
`1` missing texture (MC2 cone), `2` missing collision (stop sign), `3` other
(freeway barrel); with Shift the pin goes on the wall ahead (2.6 m, 0.8 m up),
without it on the ground 4 m ahead; Backspace removes the last. Every change
rewrites `host0:/mc2_pins.txt` through Stream::Create 0x3992B0 /
Stream::Write 0x399538 (the HostFS is writable), and the table in State starts
with "MC2PINS1", so a savestate holds them too. The pins are drawn as their
MC2 prop and reloaded from the file when the city loads (they survive a
restart; delete the file to start over). `mc3_pins.py` (tools) lists them from
the file or a savestate, draws them on MC2's LA map and writes an mc2t camera
tour that photographs each one. Markers `RPIN`, `RPIT`, `RPIL`, `RPIE`.

### Pins per city (2026-09-29)

LA and Paris share the renderer, so the pins are per city: LA's stay in
`host0:/mc2_pins.txt` (the file the first pins were written to), Paris's go to
`host0:/mc2_pins_paris.txt`, chosen by `active_city()` through
`mc2_city_pins()` of `payload/mc2_city_config.h`. The pin props are also per
city (`mc2_city_pin_prop`): LA's cone / stop sign / freeway barrel, Paris's
construction cone / street sign / bench (Paris's construction barrier is in
`paris.prop` but not in the ambients files, so it never loads). Before this the
Paris pins were invisible (the `l_prop_*` names do not exist in `paris.prop`)
and the two cities' coordinates would have mixed in one file.
`mc3_pins.py --city paris` and `mc3_cam_tour.py --city paris` follow.

### From the player's first pins (2026-09-27)

18 pins (16 texture, 2 collision), photographed one by one with the camera
tour, gave three fixes:
- **Ghetto sidewalks drawn invisible.** `l_roads_sidewalkghetto0` is the one
  texture of LA's shared bank with CLUT alpha above 0x40 (up to 0x80), and
  99.4% of its texels have alpha 0: a mask, not a cut-out. With TCC=1 the
  sidewalks of `6laneroad_g_*`, `4laneroad_g_*` and the ghetto blocks drew
  transparent (background colour, same with the CPVS off). The shared bank is
  now always TCC=0.
- **Palms and trees had no collision.** `FixedObject: 1` props were skipped
  when they have no `.phys` (palms, most trees) or a cull sphere over 8 m
  (the canopy: palm 10.9 m, tree_04 9.6 m). Fixed props no longer need a
  `.phys`, and props named *tree*/*palm* collide as a 0.45 m trunk.
- **Prop reactions never reached the car.** `mcRacer::ObstacleVelocity` is
  phInertialCS+64, a field the body derives from its momentum every step, so
  writing it did nothing - that is also why the car only "slowed" in the
  earlier prop tests. Now phInertialCS::SetVelocity (0x57C108); a head-on hit
  (within ~45 degrees) stops the car with a small bounce, a glancing one
  loses the velocity into the prop; and when the car is inside the trunk
  (full throttle gives ~2 m/s back every frame) it is moved to the edge with
  aiBrain::TeleportCar and a fake brain. Driving into the pinned palm and tree
  at full throttle: stopped at 2.5 m from both trunks (before: through at
  90 km/h). Markers `RPSO <prop | type<<16> <speed>`, `RPSP <car x> <z>`
  (first 40 hits).

### AI floor (2026-09-27)

aiOpponent::Update (0x4034A0) begins with a fell-out-of-the-world test: a car
lower than the aiRouter box's min y - 10 skips the whole AI and its brain puts
it back on the path, every frame. The router (aiRailNetwork::sm_pinstance
0x61B170, +88) was not loaded for Los Angeles - its box stayed 0, so the floor was
y = -10 - and LA's PCH under the Santa Monica bluffs is at y = -16.
(2026-09-28: NOT "never loaded in game", as written first - Tokyo's loads. With
MC2's aib the stream died at the first traffic control; `mc2_aib_compat`
fixes that and the router now loads LA's box. `fix_ai_floor` stays as a
guard; it finds the same value.) Every
opponent that took the California Incline stayed glued to the shortcut node
(-826, -16, -1170) for the rest of the race (4 MC2 races). Not the player's
distance: with the player following them 30 m above, same result. The
renderer sets the box's min y to MC2's own (losangeles.aib, aiRouter tag
0x4205: -16.31), so the floor is -26.3 as in MC2. Marker `RFLR <old> <new>`.
The car that never moves in some races is MC2's cop (`vp_impalass_cop_s_96`,
DisabledAtStart 1, NonCompetitorAi, no route) - parked by design.

### Loaded under the loading screen (2026-09-27)

The load (catalog, geometry, textures, hoods, CPVS, props) is `load_step()`,
a bounded slice of work. The camera hook still calls it once per frame, but
`start_race_hook` - on the jal to mcRaceMgr::StartRace at 0x1A58F0, the last
call of mcGameState::EnterStateGame 0x1A5700 - runs it in a loop until
`load_finished()` first. The city, its layer and the cars are loaded by then,
and the loading screen is drawn by mcLoadingThread, not by this thread, so it
stays up while we load. Measured through the Arcade: the whole city in 3.4 s
(it took ~20 s one slice per frame), everything on screen at the countdown;
after Go to Menus and back, the same. Marker `RPLD <slices> <finished>`.
LIMIT: the module must stay `= defer` - mc3boot reads every `= shim` module
into a 64 KB buffer at boot to claim its hook sites, and this one is ~450 KB
(EARL counted one shim short, no hook landed). As `defer` it arrives on the
first frame, which on the Arcade route is the front end, before any race. Only
a `nofe` boot straight into a race still loads its FIRST race one slice per
frame.

### Props against the car (2026-09-27)

The MC2 props are drawn by this module and the MC3 prop manager does not know
them, so the knock is done here (`step_prop_physics`, every frame in Los Angeles).
The player comes from mcPlayer::GetPlayer(0) 0x514530: mcRacer::ObstacleVelocity
0x51B850 is the velocity, mcRacer::GetPosition 0x51B810 answers the car
instance's Matrix34 (instance +0x10, the one AlgumCorrupto's FlyHack reads;
the translation is its 4th row, +36). Per template of losangeles.prop:
`GfxOnly: 1` or no .phys (steam, smoke) - no collision; `Drivable: 1` - none;
`FixedObject: 1` or mass >= 1000 - solid: the velocity into it is taken away
(with a little bounce) for thin ones, big ones (containers, ramps) are left
alone; everything else moves: an elastic hit (2 m_car / (m_car + m_prop), 70%,
m_car 1500), tips over its base towards the hit (90 degrees), falls, lands at
its base height and slides with friction to rest; the car keeps
1 - 0.6 m/(1500 + m) of its speed. Masses from MC2's own tune/phys/<prop>.phys.
Up to 48 props move at once; a knocked prop stays where it lies until the city
is reloaded. Breakparts of MC2 are not reproduced (the whole prop falls);
particles are MC3's since 2026-09-30 (next section). Still to do: MC3 break
parts and sounds.
Markers `RPHT <prop> <count>`, `RPH2 <type | mass << 16> <speed>`,
`RPRS <prop> <angle>` at rest, `RCAR` car x/z. Diagnostic `[boot] mc2h = 1`
lines the three nearest movable props up 12/20/28 m ahead of the start.

### MC3 particles on the MC2 props (2026-09-30)

The props stay the MC2 ones, read from the .rsc; their effects are now MC3's
(`step_prop_fx`, `fx_on_hit`). An MC2 template names its effects as parts,
`name: <template>_particle_<effect>`; `prop_part` maps each effect to the MC3
prop particle rule of the same kind (`fx_map()`: newspaper -> detroit's
newstand paper, trash/trashbag/dumpster, wood_splinters -> bench or pallet
splinters, splinter_tree + leaf_tree -> tree splinters, dust and dead leaves,
leaf_bush, mail/letters, coin, firehydrant -> the hydrant spray, steam and
smokestack -> steam, cherubpee/fountain -> the fountain spray, sparks on a
telephone pole -> wood + dust). MC2 effects with no MC3 counterpart in
detroit's set (glass, gas pump, poo, fruit, skulls, light-pole sparks) throw
nothing.

The rules are the retail ones: MC3 compiles them into the props packs, and
`mc3_prop_ptx.py` writes detroit's as text `tune/effects/<rule>.ptx`
(field table and offsets in its docstring; the text reloads byte-identical to
the compiled rule). Each rule is built in this module's memory
(mcPropParticleBirthRule ctor 0x38FFC8, name at +4, parFileIO::Load
0x55F3D8), not through LoadWithHash - its global hash would outlive the
city's heap - and textured with SetTexture(rule, s_pPropParticleTex, 8, 8).
They are thrown by the game's own prop particle system,
mcPropManager::s_pSwPtx 0x617F6C (swPropPtxSystem, 256 particles, created by
mcLayerCity::Load in every city, drawn by mcSwPtx::Draw from mcPlayer::Draw),
with BlastTransformed 0x1F42D8 at the rule's m_position through the prop's
matrix, count = m_emitRate as AdjustPropEmissionCount does it. For cities 5
and 6 the game loads the atlas `d_shared_particle` from the city's texture
folder: the installer copies detroit's there.

- hit (movable prop, or a solid one once per prop / 1.5 s): every rule that
  is not always on, when speed x 1500 kg reaches m_minForceToSpawn;
- m_sprayAfterBroken (hydrant): keeps spraying m_spewTimeLimit (10 s) where
  it stood, up to 8 at once;
- m_alwaysOn (steam vents, fountains): every frame within 70 m of the
  camera, at most 6 per frame; templates with particles and no model are now
  placed (not drawn) for this - LA 19 emitters, Paris 54;
- m_bRecieveAmbientLighting: colours times the city ambient (mcCity +0xE0)
  lifted by m_emissive, for that blast only (the nearest-light term of
  EmitParticles is left out - MC2's lights are not mcLights).

Two traps. The system's byte +52 (the hydrant flag, copied into every
particle) is built as 0xCD and only EmitParticles ever clears it: left set,
Update 0x1F3850 stops every particle within 3 m of a car - the newspapers
from the player's own hit hung invisible under the car. It is written on
every blast. And release_all resets the system (PTX_RESET 0x1F4538, all
particles dead) before our rules are freed, since each particle points at its
rule. `[boot] mc2f = 0` turns the particles off (was mc2x, which with mc2y/mc2z places the camera); `mc2h = 2` lines up the
nearest hydrant, steam vent (4 m aside) and trash prop instead. Markers
`RPFX <rules used> <rules loaded>`, `RPF2 <ambient emitters> <atlas>`,
`RPFB <prop | fx count << 24> <blasts>`. Measured: LA newspapers and cans fly,
a knocked hydrant keeps 168 spray particles up with 39 steam particles
beside it; Paris cans from a trash bin. Sounds and MC3 break parts are not
done yet.

### MC2's sky and MC2's prop lights as MC3 lights (2026-09-30)

**Why there was no sky.** The city .pck we build has no sky: its
mcSkyHatClass (mcCity+0x190) loads `skyhat_<city>_<tod>_<weather>.parfileio`
but its eight layer models (+0x34..+0x50, rmcModel*, drawn by
mcSkyHatClass::Draw 0x25C238 with the sky shader group mcCity+0x10) are null
and that group has 0 slots (retail 10-12), so MC3 only clears to
m_clearColor. MC2's own sky was never installed: it lives in
`resource/<city>/<time>_<weather>.rsc` (222 KB), MOD0 `sky_0` (the dome, radius
100 m, y -4..42, five textures `l_shared_sky_*`) plus `sky_1..sky_6` cloud
layers when rainy, textures in the same container (handles 0xC0xx). Now
`step_sky` reads it whole (container 3 for tex_entry/make_slot) and
`queue_sky` draws every MOD0 layer first in draw_city, at the camera's
position, depth test ALWAYS and no Z write. `[boot] mc2b = 0` turns it off;
marker `RSKY <layers> <bytes>`, `RSKE <why>` on failure. The installer must
copy the nine `<time>_<weather>.rsc/.rnt` per city to `host0:/mc2/<city>/`.

**Lights.** An MC2 prop's lights are compiled into its PRP0: +0x18 count,
0xC0-byte records from +0x20 holding MC2's `tune/lightdata/<prop>_<n>.light_data`
(offsets in `step_lights`' comment, checked field by field). MC3's
mcLightData (176 bytes, FileIO 0x595B18, dir `tune//lightdata`) is the same
class with the same field names, so each record becomes one; each placed
lamp gets an mcLight (init 0x259988, as mcPropLightData::CreateLights) in
mcLightManager's grid (AddLightToGrid 0x259658) - MC3 lights cars and
particles from it - and every frame the lamps within 160 m go through what
mcPropType::Render does with prop lights: 0x259FF0 (glow, cone, flare) and
0x25A1F8 (road reflection, ground height in $f12). Both only fill mcGlow's
buffers (350 a frame), so calling them from the SetCamera hook works. A
knocked-down lamp goes dark. TRAP: this toolchain passes a float second
argument in $f13; the game expects $f12 - `light_reflect` calls by hand.
Measured: LA 30 light types / 3627 lamps, Paris 18 / 4052; Paris midnight
at 57-59 fps. `[boot] mc2l = 0` turns them off; markers `RLGT`, `RLGX`.
Released from the grid in release_all while the manager is the same.

### Time and weather from the race; sky before the palette (2026-09-30)

The renderer took time of day and weather from `[boot] time/weather`, so an
Arcade race picked at dawn in the menu still drew midnight's CPVS, textures,
props and sky (player's savestate: race config tod 0 / weather 0, boot key
midnight). `cond_time()`/`cond_weather()` now read mcRaceConfig current
(0x619B10) +4/+8 through the game's name tables 0x619B48 (dawn, midnight,
dusk) and 0x619B58 (clear, cloudy, rainy); the boot keys are only the
fallback. And the first sky build drew the sky between the CPVS palette
upload and the city: the sky's MOD0 packets unpack over the VU memory the
palette sits in, so the city took vertex colours from sky data - yellow,
blue and magenta patches that changed with the camera. The sky now goes
right after the bootstrap, before the palette (A/B at the player's camera:
patches with the old order, none with the new).

### Glow cards: per-card distance in the VU (2026-09-30)

A card instance is a whole block of shop windows (130-270 m, one PMD of
7-10 batches spanning 40-115 m each), so the CPU cannot drop single cards.
The card pass now uploads its own view-projection to VU q[4..7]
(`card_vp`, UNPACK to 4, the normal one re-sent after the cards): the depth
row becomes z' = (1 + e/D) d - e (d = view depth = w, e = 0.5 m), inside
[-w, w] only for d < D, so rv1's CLIP drops every card triangle beyond D.
Checked both ways (the inverse row kept only what lay beyond D). The CPU
keeps a block while its sphere comes within D (distance - radius). D =
`[boot] mc2k = N` (10..2000), default 200; `mc2k = 0` still turns the cards
off. What the cards are: windows mirrored deep under the road, so a far
block paints the near road at grazing angles and the cards of the shop you
are passing show little - reflections are seen from a distance and fade as
you reach the shop, as in MC2. The first version (distance to the block's
centre, 60 m) dropped whole blocks while driving beside them.

### Frame budget (2026-09-30)

The player's fast flicker on Hollywood's avenues, invisible in screenshots
and in PCSX2's own video capture: frames that miss the 30 fps deadline are
held a third vblank, which flips the interlaced field. Measured with the EE
cycle counter (COP0 Count) around set_camera_hook - markers `RCYC <avg>
<max>`, `RCY2 <physics K | fx K> <lights K | draw K>`, `RCY3/RCY4` for
draw_city's parts - on that street (a 30 fps frame is 9.83 M cycles):
before 2.4-2.9 M a frame, after 1.2-1.7 M. What it was:
- the props loop called props_enabled() - a boot-arg string lookup - in its
  condition, 5700 times a frame (~0.9 M);
- update_model_matrix asked world_mode() (same lookup) for every component;
- queue_pcp resolved every CPVS REF again each frame (~10 000 in LA); now
  resolved once and kept in the tag (bit 24 of the tag's low word,
  REF_RESOLVED; bit 31 of MC2's handle could not be the flag - it holds a
  12-bit index + 1, and using it sent garbage REFs: DMA errors);
- sphere_visible rebuilt the frustum planes from the matrix on every call;
  now frustum_update() once a frame;
- the prop lights: all lamps within 160 m went through MC3's glow and
  reflection code, in view or not; now in view, 120 m, 48 a frame (with
  them on, 120 frames had taken up to 4.79 s instead of 4.00).
`mc2g = Fd` (drop test "drive" mode) starts a drive anywhere: the car put
at the point facing its velocity and thrown once, the game's camera left
alone. Boot keys renamed: particles `mc2f` (was mc2x), sky `mc2b` (was
mc2y) - mc2x/mc2y/mc2z together already place the camera.

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
`losangeles_page_pool`. Markers `RTEX` (images, bytes kept), `RDTE` (texture error:
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
`out/asfalto_20260925/`). The Los Angeles spawn is inside the
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

At the Los Angeles spawn: 503 PCP0 draws + 174 PMD0 draws, 0 failures, 303
uploads, ~30 FPS, `midnight_clear` and `dusk_clear` checked. Captures:
`out/cpvs_20260925/` (`test8`, `dusk`).

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
  game. Savestate at the Los Angeles spawn: MC3 renders into `FRAME FBP 0x40`
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
  `out/vram_ring_20260925/`.
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
python "tools/modloader\mods\city_mc2_rsc_draw_test\make_rsc_full_texture_bank.py" `
  "$MC3_HOSTFS\mc2_losangeles.rsc" `
  "$MC3_HOSTFS\mc2_rsc_manifest.bin" `
  "tools/output\mods\mc2_rsc_texture_bank.bin" `
  "$MC3_HOSTFS\mc2_rsc_texture_bank.bin"
```

For the smaller neighborhood preview, generate both files after editing
`preview_models.json`:

```powershell
python "tools/modloader\mods\city_mc2_rsc_draw_test\make_rsc_manifest.py" `
  "$MC3_HOSTFS\mc2_losangeles.rsc" `
  "tools/modloader\mods\city_mc2_rsc_draw_test\preview_models.json" `
  "tools/output\mods" `
  "$MC3_HOSTFS"
```

The preview generator resolves each named model's PMDs from CMI0 and rejects
duplicate/missing PMDs and texture-bank overflow. Both generators sort PMDs
into RSC directory order so the runtime can keep one sequential stream open
instead of rereading the RSC from the beginning for every piece.
To rebuild the module, from `modloader/mods` in the configured PS2 toolchain:

```sh
sh build_mod.sh city_mc2_rsc_draw_test $MC3_HOSTFS
```

To regenerate the unchanged VU bootstrap:

```powershell
python "tools/modloader\mods\city_mc2_rsc_draw_test\make_vu_bootstrap.py" `
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
`$MC3_WORK/rsc_manifest_20260924/`.

The latest PCSX2 run loaded all 1,010 PMDs, validated 723 models/8,500
placements, completed the RSC scan in about 23 seconds, and continued to 150
seconds at `SES=5` (Los Angeles). `RDAD` remained active; at the fixed camera it
reported 1,904 visible instances and 2,373 visible PMD draws. No renderer,
VIF, or exception error appeared. The seven early TLB misses at
`0x3f3ae148` match the previously documented baseline. Captures and log are
under `$MC3_WORK/rsc_allcity_20260924/current_mod_defer_test/`
and `$MC3_PCSX2_DATA/logs/cli_teste.txt`.

The user's freecam inspection says the current geometry appears aligned; this
does not yet validate every district or join. The 2026-09-25 RSC texture test
restored real materials by packing texture and CLUT allocations together across
the whole reserved VRAM range (instead of splitting it at `0x3800`), sharing
identical images, and sending only visible PMD uploads each frame. PCSX2 reached
`SES=5`, `RSCD=1010`, and 75 seconds without a new RSC/VIF/exception error;
the fixed spawn showed textured walls, windows and signage near 30 FPS.
Screenshot and log: `$MC3_WORK/rsc_real_textures_20260925/test1/`.
This does not fix the dark cutouts/road appearance or the coloured VRAM artifact
along the bottom of the image. The full bank is resident in EE memory, and
the PS2-hardware VRAM lifetime/performance still needs validation.

On 2026-09-25 the runtime sphere/frustum test was corrected to take its
positive-W and side-plane coefficients from the columns of the row-vector
view-projection matrix. The previous code mixed contiguous rows and could
reject visible models as the camera turned. At the Los Angeles spawn, the full
renderer changed from 2,373 submitted PMD draws / about 16k PCSX2 primitives
to 863 submitted draws / about 46k primitives; the newly selected geometry
fills several of the large gaps. Both runs reached `SES=5`, loaded all 1,010
PMDs, and had no renderer error. The paired screenshots and logs are in
`$MC3_WORK/rsc_cull_planes_20260925/`.

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
`$MC3_WORK/native_culling_20260925/`.

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
bytes, and `make_vu_bootstrap.py` now produces it. At the Los Angeles spawn the
large wedge in the upper left is gone with the same ~46.6k primitives;
comparison `out/clip_fan_20260925/compare_rsc_t075.png`. See
`city_mc2_scene/README.md` for the mechanism and `patch_clip_template.py`.

Still absent from this direct-RSC module: PCP/CPVS per-vertex colour chains,
collision, cell/PVS culling, and PS2-hardware validation. The coloured VRAM
strip is fixed here (RCTX v2 + MC3 ring, above) but NOT in the tiled
`city_mc2_scene` renderer, whose 49 bundles still carry fixed `0x1500..`
addresses. Unverified on hardware: the VIF `DIRECT` data in every VCLQ packet
of this module (as in v1) starts 8 bytes into a quadword, which PCSX2 accepts. For the latest CPVS/native-depth status and
test configuration, see `city_mc2_scene/README.md`.
