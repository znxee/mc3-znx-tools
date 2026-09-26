# Inventory: what else can go through `pck_forge`

What the tool does is always the same: **the game reads a raw file, builds the
graph in memory, and that graph is dumped and becomes a `.pck`**. For a format
to take that route it needs three things:

1. **a runtime reader** that accepts the raw file;
2. **a packed equivalent** the game also knows how to load (otherwise there is
   nothing to convert to);
3. **a graph reachable from a root**, because that is how
   `mc3_heap_to_pck.py` cuts the result out of the heap.

Measured in `$MC3_HOSTFS/ASSETS` (an untouched retail extract has almost the
same counts; where it differs, it is noted).

## 1. Already proven

| format | count | size | reader | tool |
|---|---|---|---|---|
| `.mesh` | 356 | 7.5 MB | `rmcModel::Create` 0x002A8CA0 (`.mesh` branch) | `pck_forge` / `mesh_pck_dump` |
| `.tex` | 2231 | 21.3 MB | `mcTextureFactory::Create` 0x003B5CA0 | `tex_pck_dump` |

`.mesh` is the route documented in this session, with the whole of LA
reconverted (298 components, 202,067 vertices). `.tex` was already used by
another fork: `mc3_city_build --textures` takes exactly "the folder of
`.tex.pck` the GAME built".

## 2. The candidate worth the most, and nobody has tried it yet

| format | count | size | reader |
|---|---|---|---|
| `.bnd` | 46 | 0.02 MB | `phBoundGeometry::LoadAscii(const char*, Vector3 const*)` **0x004796B0** |

**Collision.** `.bnd` is text in the same dialect as `.mod`
(`version: 1.10` / `type: geometry`), and `LoadAscii` is the exact analogue of
`rmcModel::Create`: it takes a path and returns the built graph. There are also
tokenizer readers for the other volume shapes:

    phBoundBox::Load_v110          0x0046D8F0
    phBoundComposite::Load_v110    0x00475888
    phBoundGeometry::Load_v110     0x00478CB0
    phBoundGeometry::LoadGeomStats 0x00478B68

The packed equivalent exists and is used all the time: `*_bnd.pck`. Today the
collision is written **by hand** (octants that are not halving, leaf ranges that
overlap) - exactly the kind of work the route through the game spares, as it
spared the VIF emitter.

Only 46 files ship with the game, but the number that matters is the input one:
any new collision you want to generate becomes a text `.bnd`.

## 3. Candidates with a reader, but no confirmed packed target

| format | count | size | reader | what is missing |
|---|---|---|---|---|
| `.occlude` | 6 | 1.32 MB | `mcOccluderSystem::LoadPolys/LoadGeoms/LoadHeader` 0x0039F218/358/6F8 | occluders live OUTSIDE the `.pck`; it is not known whether a packed form exists |
| `.graph` | 6 | 1.98 MB | AI rail network (`aiRailNetwork`) | same |
| `.shadert` | 146 | 0.10 MB | its own grammar | retail shaders are baked into the ELF, not into a `.pck` |
| `.mod` | 779 | 32.8 MB | `gfxModelBase::LoadMod` 0x001EBB78; `rmcModel::Create` `.mod` branch | **measured: it does not work** - the `.mod` branch builds the skeleton and stops (packets with a zero pointer and size). Convert `.mod` -> `.mesh` first |

## 4. What does NOT go in: configuration read directly

They are text and have a reader (`datAsciiTokenizer`), but the game consumes
them as configuration data - there is no packed version to generate.

| format | count | size | what it is |
|---|---|---|---|
| `.rac` | 1332 | 56.9 MB | race definition |
| `.carcfg` | 1339 | 4.5 MB | vehicle configuration |
| `.rinf` | 1336 | 0.35 MB | race information |
| `.ui` | 1116 | 1.6 MB | screens |
| `.csv` | 526 | 0.9 MB | tables |
| `.camtrackcs` | 496 | 0.6 MB | cutscene camera |
| `.vehicle` | 49 | 0.67 MB | vehicle parts list |

## 5. Already binary and already packed

`.pck` (8559, 1878 MB), `.ppf` (51, 1079 MB), `.rsm` (2078, 171 MB),
`.bnk` (352, 58.8 MB), `.ian` (245, 55 MB), `.anim` (1597, 18.8 MB).
Nothing to do along this route.

## The order I would follow

1. **`.bnd`** - direct ASCII reader, a packed target the game loads on every
   boot, and it replaces the manual octant work. It is the same leap `.mesh`
   made.
2. **`.occlude` and `.graph`** - first answer whether a packed form exists; if
   not, there is nothing to convert to.
3. **`.tex`** - it already works, but it is worth measuring in the standalone
   state (25 MB free against the frontend's 1.4 MB), which is where
   `pck_forge` runs.

## How to run a new format

`pck_forge` today calls `rmcModel::Create` with `model/pNNN.mesh`. For another
format one line changes: the function and the path. The rest - a private heap
at the high address, the dump over SIO, cutting by root - is already generic.
See `mods/pck_forge/pck_forge.cpp` and `tools/mc3_pck_forge.py`.

---

# Addendum (2026-09-21): readers of the `.aib .hood .lvl .occlude .prop .pdef .pvc .mcenvmap .phys .light_data .traffic .tune .vehicle .density` extensions

Measured on retail (SLUS-21355) and checked on the alpha. "No code" = the
string exists but **no instruction references it**: IDA finds no xref, and a
raw `lui`+`addiu` sweep (wide window, with two controls that DO FIND
`prop_race` and `prop_template`) does not either. That holds for retail; on the
alpha, which has symbols, IDA finds no xref either.

| ext | reader in retail | form | what it produces | goes into `pck_forge`? |
|---|---|---|---|---|
| `.aib` | `aiRailNetwork::LoadRailNetwork` 0x503798 / `GetExtents` 0x503860 | **tagged chunk binary** (`TaggedStream::ReadTag` + `switch`), `$/city/<n>/<n>_city.aib` | AI objects on the heap | no: the raw file already is the format read at runtime |
| `.occlude` | `mcOccluderSystem::LoadDataset` 0x39EE40, `LoadExternalGeomDataset` 0x39ED50; `LoadHeader` 0x39F6F8 **requires `version: 3`** (`!= 3` fails) | text (`datBaseTokenizer`); MC2 is `version: 2` (`num_primitives:` + `verts:`), MC3 is 3 (`num_geoms:` + `num_primitives:`) | polyhedra/polygons in the occlusion system | no: runtime only; **the MC2 file is refused as it is**, it needs a v2->v3 converter |
| `.pdef` | `mcPropType::LoadTypeData(name,"pdef")` 0x395460, via `mcPropBase::Init` 0x38D538 and `mcBanger::Init` 0x387A88 | `prop_template` text (`proptype_index:`, `numlods:`, `lod{}`, `sphere:`, `numlights:`, `light{name: type: color: glow: cone: flare: reflection: matrix:}`, `numstates:`), `props/` folder | `mcPropType`; **names a `.mesh` per LOD** (`%s_%d.mesh`, `%s_0_%s.mesh`) | the `.pdef` no; the `.mesh` files it names yes |
| `.phys` | `phArchetype::Load(name, phBound*, "phys")` 0x4D8750 and `mcArchetypeBreak::Load` 0x2BF1F8 | text, `tune/phys/<name>.phys` | a `phArchetype` bound to a `phBound` | no: configuration |
| `.density` | `InitializeTokenizer` 0x230D70 + `mcAmbientDistrib::LoadVehicleDensity` 0x230E38/0x231560 | text (`Large`/`Small`), `tune/traffic/<city>/<city>_<cond>.density` | traffic pool; each type becomes `aiAmbientType::Init(name,n)` | no: configuration |
| `.vehicle` | `aiAmbientType` (parFileIO registry: type `vehicle`, folder `tune/traffic`) | text | traffic vehicle type | no: configuration |
| `.traffic` | **not an extension**: it is the `tune/traffic` folder (`.density` + `.vehicle`); `aiAmbientTrafficData::LoadLevelTraffic` 0x21AC58 | - | - | - |
| `.tune` | **not an extension**: it is the root of everything (`tune/<folder>/<name>.<type>`); the `parFileIO` base reads `tune/<city>_<cond>.parfileio` | text | see the registry below | no |
| `.lvl` | **no code in MC3** (reader removed). Tokens: `lvl numhoods: hood name: extents_min extents_max dimensions props`. **`nopvs` is NOT a file token**: in MC2 it is a command-line flag (`sub_39E228("nopvs")`, the `datArgParser`). The live reader is in MC2 PS2: `sub_267770` (0x267770, 199 decompiled lines) | text, MC2 | - | reader removed |
| `.hood` | **there is no `.hood` file reader in MC3.** `hood` shows up as a token of the `.lvl` block (no code) and in `drwShaderHood::Load` 0x3B61A8; neighbourhoods live in the hood table of the city `.pck` | - | - | no |
| `.pvc` | **no code.** `pvc` sits between `CarCfg` and `{ }` in a string pool | - | - | - |
| `.light_data` | **CORRECTED: it has a live reader.** `mcLightData::FileIO(datParser&)` 0x595B18 (+ `Init(name, Vector3, Vector3)` 0x595A60, `GetDirName` = `tune//lightdata`). The string had no xref because parFileIO's `GetTypeName` uses it internally. The fields `FileIO` reads (`type color intensity decay_rate spot_angle spot_dropoff draw_glow glow_offset glow_color draw_cone cone_size cone_intensity cone_offset cone_color draw_flare flare_offset flare_color flare_size reflection_color`) match MC2's 119 `.light_data` files; only `position`/`direction` stay out, because MC3 receives them as parameters in `Init` | parFileIO text | `mcLightData` | no: configuration |
| `.mcenvmap` | **does not exist** in either build (0 occurrences). The closest: `__city_envmap__`, the `envmap.shadert` shader and `fx_*_envmap.tex` | - | - | - |

Also without code: the prop placement block `prop_type_count: type: matrix:
emin: emax: cpvindex: startframe:` (next to `prop_race`, `prop_autox` and
`$/resources/prop/%s_%s_%s_%s`, those three WITH xrefs in
`mcPropManagerRaceData::InitRaceProp` 0x393AC0 and
`mcRaceBase::LoadRaceBangers`).

## The parFileIO registry

98 classes expose `GetTypeName()` (extension) and `GetDirName()` (folder). It
is the mechanism behind the tail of `tune/` extensions (`.carcfg`,
`.mcgarage`, `.mccarcustom`, `.mcglow`, `.mcfbglowparams`, `.damage`,
`.vehicle`...). Saved to `output/parfileio_registry_retail.txt`, with the
caveat that 4 lines come out wrong because of inheritance/thunks. It does not
go into `pck_forge`: it is configuration read directly, with no packed form.

## Conclusion for `pck_forge`

From this list **no new format goes in**. The only point of contact is `.pdef`,
which points to `.mesh`. The candidate still worth the most is `.bnd`
(`phBoundGeometry::LoadAscii` 0x4796B0). The useful finding of this round is a
different one: `.lvl` and the props block have their keys recorded in the
executable but the code was removed, which documents the format without there
being anything to revive.

## What MC2 adds (2026-09-21)

MC2 PC ships on disk files of **every** extension above, including the three
MC3 does not even distribute: `.pvc` (4), `.light_data` (119), `.mcenvmap` (26);
plus `.lvl` 4, `.hood` 31, `.pdef` 374, `.phys` 453, `.density` 12, `.vehicle` 44,
`.aib` 5, `.occlude` 3, `.xmod` 9902, `.cc` 2482. All text, except `.pvc` and
`.aib`. The format can be read from the sample, without decompiling.

- `.aib`: both MC3's and MC2's open with **`TSV1`** (TaggedStream v1). Same container.
- `.pvc`: binary (1.4 MB for `losangeles.pvc`), precomputed visibility; MC3 does not consume it.
- `.mcenvmap`: `type: a / mcEnvMap { m_renderAlpha }`. MC3 does not have the class:
  `mcCityEnvMap::LoadCondition` 0x562450 builds the name `fx_envmapbk_%s_%s` directly.
- `.pdef`: MC2's is the OLD format (`name:` / `lods:` / `sphere:`); MC3's reader
  wants `prop_template` (`proptype_index:`, `numlods:`, `lod{}`...). It needs converting.
- The block `prop_type_count: emin: cpvindex: startframe:` **does not exist in MC2**
  (neither in `testapp.exe` nor in the PS2 ELF): it is new in MC3, so MC2 does not recover it.
