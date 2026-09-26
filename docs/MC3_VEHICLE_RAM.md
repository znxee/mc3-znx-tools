# MC3 PS2 vehicle package - raw PCK and relocated RAM

This note records only fields proven by the retail `vp_350z_04.pck`, the
attached PCSX2 EE dump, and named retail functions matched to the October 2004
alpha symbols. Unknown fields stay unknown.

## Raw `vp_*.pck` HexPat

`mc3_vehicle_pck.hexpat` applies this same typed graph directly to an unopened
PS2 vehicle package. It reads the 0x80-byte header, accepts only main vehicle
type `0x0038E030` version 1, and places `VehicleBodyPrefix` at file `0x80`.
Serialized pointers are translated with:

```text
file_offset = serialized_va - header.base_va + 0x80
valid range = [header.base_va, header.base_va + header.body_size)
```

This range check is important: nulls, `0xCDCDCDCD`, runtime-only fields and
external references remain visible as raw words but never create false child
objects at offset zero. A `*.mesh.pck` (type 22) consequently shows its header
without being mistaken for the main package.

Raw class words are displayed as **serialized class tokens**, not vtables.
They are build residue and are replaced during `Load()`. Shader dispatch uses
the stable rule from the retail loader, `shader_flags & 0x7F`: type 0 opens the
direct-texture `rmcShaderBasic` layout; types 1..0x42 open the common complex
pass and the proven derived tails (`CarDecalMulti`, Normal, Chrome,
TaillightCasing, Distort and CarpaintNoVinyl). This makes the pattern portable
across the three serialized-token families already observed.

The Pattern Language CLI loaded the full graph successfully for all **117 main
`vp_*.pck` packages** in the available retail set, including base cars,
`vp_d_*`, motorcycles, police vehicles and DLC slots. It also passes
`lint_hexpat.py`. The detached shader inspector, when needed, takes a raw file
offset through `INSPECT_FILE_OFFSET`; it is disabled by default to prevent
overlapping annotations.

## Capture and address rule

- PCK: `vp_350z_04.pck`, body VA `0x06800000`, type `0x0038E030`, version 1.
- Body size: `0x4E0E0` bytes.
- EE body address: `0x01773680`.
- Relocation delta: `0x01773680 - 0x06800000 = -0x0508C980`.
- `eeMemory.bin` is a direct EE address image: file offset equals EE address.

For an address inside the serialized body:

```text
runtime = 0x01773680 + (serialized_va - 0x06800000)
runtime = 0x01773680 + (pck_file_offset - 0x80)
pck_file_offset = 0x80 + (runtime - 0x01773680)
```

The complete body remains contiguous. Internal pointers use the fixed delta;
serialized vtable tokens do not. The loader replaces tokens with real retail
ELF vtables.

## Package prefix

The loaded image starts at PCK file offset `0x80`.

| PCK file | body relative | meaning | loaded value/address |
|---:|---:|---|---:|
| `0x80` | `+0x00` | flags/version-like byte | unchanged `0xCDCDCD01` |
| `0x84` | `+0x04` | top-level item count | 13 |
| `0x88` | `+0x08` | `rmcCarModelType*` | `0x017736D0` |
| `0x8C` | `+0x0C` | second `phBoundGeometry*` | `0x017B8760` |
| `0x90` | `+0x10` | `phMaterial*` | `0x017B8A60` |
| `0x94` | `+0x14` | `mcCarStuckTune*` | `0x017B8AE0` |
| `0x98` | `+0x18` | `mcPlayerInputTune*` | `0x017B8A90` |
| `0x9C` | `+0x1C` | `mcDriverTune*` | `0x017B8B20` |
| `0xA0` | `+0x20` | `mcCarDamageTune*` | `0x017B8B70` |
| `0xA4` | `+0x24` | `mcCarModelTune*` | `0x017B8C10` |
| `0xA8` | `+0x28` | vehicle audio root | `0x017B8C90` |
| `0xAC` | `+0x2C` | runtime-only `mcCarCustom*` | `0x0156B0A0` |
| `0xB0` | `+0x30` | `mcCarCamShakeTune*` | `0x017B8C50` |
| `0xB4` | `+0x34` | stock `mcCarSimTune*` | `0x017C0BD0` |
| `0xB8` | `+0x38` | upgraded `mcCarSimTune*` | `0x017C0FA0` |
| `0xBC` | `+0x3C` | stock `mcGyroTune*` | `0x017C1370` |
| `0xC0` | `+0x40` | upgraded `mcGyroTune*` | `0x017C1450` |
| `0xC4` | `+0x44` | `mcCarTuneZone*` | `0x017C1530` |
| `0xC8` | `+0x48` | `mcCarModsTune*` | `0x017C1590` |

The object reached from file `0x88` starts at file `0xD0`, serialized VA
`0x06800050`, EE `0x017736D0`. Function `0x307400` is named
`rmcCarModelType::rmcCarModelType(datResource&, mcCarCustom const*)`. Its call
to `mcCullableType::mcCullableType(datResource&)` fixes the base subobject whose
vtable is at object `+0x28`: file token `0x007A2390` becomes retail vtable
`0x00627FD0`.

Its `mcCullableType` prefix is now partly named from the class methods:

| relative | meaning | 350Z runtime |
|---:|---|---:|
| `+0x04` | owner/type registry pointer | `0x014BF8B0` |
| `+0x08` | interned type name | internal relocated pointer |
| `+0x10/+0x14` | intrusive active-type links | null in this capture |
| `+0x18` | head of active `mcCullable` instance list | `0x01572A78` |
| `+0x28` | vtable | `0x00627FD0` |

`mcCullableType::SetName` writes `+0x08`; `MakeActive` links the type through
`+0x04/+0x10/+0x14`; `AddToActive`, `RemoveFromActive` and
`RenderAllInstances` all use `+0x18` as the live instance-list head. This
explains the two non-mesh zero-to-pointer changes at the start of the object.

### Complete shader-local binding table (`rmcCarModelType+0x2C..+0xC3`)

EE `0x017736FC` is PCK file `0xFC`, body `+0x7C`, and
`rmcCarModelType+0x2C`. It begins a packed table of **76 `u16` indices** and
ends at `+0xC3` (EE `0x01773793` in this capture). Most indices address the
owning body `rmcShaderGroup`; some address separately allocated wheel,
brake-caliper and masked-chrome local buffers. They are not light intensities
and are not skeleton-node indices. Consumers reject `0xFFFF`, otherwise
multiply the index by four and add the appropriate local-buffer pointer.

A census resolved each serialized non-`FFFF` value against the linked local
list of 88 retail vehicle packages. The runtime-only entries were then named
from their `LookupLocal` strings and consumers in the ELF:

| type range | slots | local names / role | source |
|---:|---:|---|---|
| `+0x2C..+0x3A` | 0..7 | `tlights_on`, `blights_on`, `thirdbrake_on`, `headlights_on`, `taillight_lights_on`, `coloredglass_headlightval`, `coloredglass_brakelightval`, `reverselight_lights_on` | serialized body locals |
| `+0x3C..+0x42` | 8..11 | `coplights1_on`, `coplights2_on`, `coplightbar_on`, `coplightbaridx` | police shader classes and update consumers |
| `+0x44..+0x4A` | 12..15 | `CarPaintEnvScale`, `CarPaintFresnelMin/Max/Scale` | serialized body locals |
| `+0x4C..+0x52` | 16..19 | `WinFresnelEnvScale`, `WinFresnelMin/Max/Scale` | serialized body locals |
| `+0x54..+0x5C` | 20..24 | `tintR/G/B/A`, `WindowsBroken` | serialized body locals |
| `+0x5E..+0x64` | 25..28 | `caliperR/G/B/A` | runtime `drwShaderBrakeCaliper::Load` (`0x3029B0`) |
| `+0x66` | 29 | reserved | `FFFF` in all 88 PCKs; no consumer found |
| `+0x68..+0x70` | 30..34 | `shadowR/G/B/A`, `isAdditive` | serialized body locals |
| `+0x72..+0x74` | 35..36 | `HoodShaderIdx`, `TaillightCasingShaderIdx` | serialized body locals |
| `+0x76..+0x7A` | 37..39 | `brakeTemp`, caliper `rimSpeed`, wheel `rimSpeed` | runtime caliper/wheel lookups |
| `+0x7C..+0x82` | 40..43 | `trim_color_r/g/b/a` | serialized body locals |
| `+0x84..+0x8A` | 44..47 | `chrome_color_r/g/b/a` | serialized body locals |
| `+0x8C..+0x92` | 48..51 | `side_exhaust_chrome_color_r/g/b/a` | serialized body locals |
| `+0x94..+0x9A` | 52..55 | exhaust masked-chrome `chrome_color_r/g/b/a` | runtime `drwShaderMaskedChrome::Load` (`0x302AB8`) |
| `+0x9C..+0xA2` | 56..59 | `rim_chrome_color_r/g/b/a` | runtime `rmcCarModelType::ReloadWheelShaderVars` (`0x302840`) |
| `+0xA4..+0xAA` | 60..63 | `rim_insert_color_r/g/b/a` | same runtime lookup |
| `+0xAC..+0xB4` | 64..68 | `DecalShaderIdx`, `DecalShaderTintR/G/B/A` | serialized body locals |
| `+0xB6..+0xBC` | 69..72 | reserved | `FFFF` in all 88 PCKs; no consumer found |
| `+0xBE..+0xC2` | 73..75 | `CoplightRFlasher`, `CoplightLFlasher`, `sprocketSpeed` | police packages / motorcycle packages |

This accounts for 71 semantic slots and five genuine reserved slots. On the
350Z, runtime loading changes `+0x5E..+0x64` from `FFFF` to `0,1,2,3`,
`+0x76` to 4, `+0x7A` to 0, `+0x94..+0x9A` to `1,2,3,4`, and
`+0x9C..+0xA2` to `1,2,3,4`. The source PCK correctly keeps these streamed
bindings as `FFFF`; a writer must not bake the capture-specific runtime
indices back into the file.

`rmcCarModel::Update` passes `type+0x2C` to the light and colour helpers.
For example, `rmcCarModel::SetTaillightVal` (`0x2F7800`) reads slots 4 and 0;
the brake helper (`0x2F77A8`) reads slots 1, 2 and 6; and the headlight helper
at `0x2F7840` reads slots 3 and 5. This also exposed two misleading names from
the cross-build neighbour match: the routine labelled `SetReverselightVal`
at `0x2F7730` actually writes `CoplightRFlasher`; the real reverse-light
consumer is `0x2F7780`, which reads slot 7.

The semantic slots are stable, but the numbers are not: they are compiled
indices into the vehicle's particular local list. Copying this table while
reordering or replacing that list makes the model write light values into the
wrong shader variables. A shader transplant must preserve the local ordering
or rebuild every serialized body binding index. Runtime-only wheel/caliper
bindings must remain `FFFF` and be allowed to resolve after streaming.

The next byte, EE `0x01773794` / type `+0xC4`, is not part of the table. Five
runtime-selected resource pointers occupy `+0xC4..+0xD4`; `+0xCC` feeds the
wheel-local reload, `+0xD0` the brake-caliper loader, and `+0xD4` the
masked-chrome loader. The supplied capture contains respectively
`0x0155C1F0`, `0x0155C330` and `0x0155C470` in those three proven fields.

Within `rmcCarModelType`:

| relative | absolute PCK file | meaning |
|---:|---:|---|
| `+0xD8` | `0x1A8` | `drwShaderModel*` |
| `+0xDC` | `0x1AC` | skeleton header pointer |
| `+0x2AFC...` | from object base | 11 customization category arrays |
| `+0x3174...` | from object base | category counts |

`rmcCarModelType::AddNamesToPartDatabase` at retail `0x302D10` proves that the
category entries are skeleton node indices, not mesh IDs.

### Rear-bumper exhaust anchors (`rmcCarModelType+0xE8..+0x2DF`)

The supplied range EE `0x017737B8..0x01773A0B` maps to
`rmcCarModelType+0xE8..+0x33B`, PCK file `0x1B8..0x40B`. None of its bytes is
relocated: the RAM capture and source PCK are byte-identical throughout this
range.

The first `0x1F8` bytes are a fixed-capacity array of 21 records. Each 24-byte
record is two `Vector3` positions:

```text
struct RearBumperExhaustPair {
    Vector3 exhaust_0;
    Vector3 exhaust_1;
};
RearBumperExhaustPair rear_bumper_exhaust[21];
```

This is code- and data-proven. Retail function `0x2FC2D8`, aligned with the
alpha symbol `rmcCarModel::UpdateExhaustDamagePos`, computes the address as:

```text
type + 0xE8 + custom->rear_bumper * 24 + exhaust_index * 12
                      ^ +0x118C                 ^ 0 or 1
```

On the 350Z, every one of the 42 vectors is bit-for-bit equal to one skeleton
anchor. Record zero matches `exst0/exst1`; the other records match pairs such
as `bumr3_ext0/1`, `bumr4_ext0/1`, through `bumr28_ext0/1`. The apparent
left/right order is not stable, so the fields are named after the game's
`ext0/ext1` slots rather than geometric side. The function moves the active
exhaust matrices toward these undamaged anchors as rear-zone damage changes.

Across 117 canonical vehicle packages, the number of populated records is
0, 1, 2, 6, 7, 8 or 21. Whenever the table is populated, its count equals the
rear-bumper customization count (the front-bumper and side-skirt arrays share
that same kit index/count). Unused capacity is all zero. This field must be
copied or regenerated together with the rear-bumper skeleton entries; copying
only meshes can leave exhaust effects at another bumper's position.

The remainder of the requested range is:

| relative | size | meaning | evidence |
|---:|---:|---|---|
| `+0x2E0` | `0x48` | 18 reserved `u32` | zero in source and RAM, 117/117 packages |
| `+0x328` | 4 | skeleton node/matrix count | equals `SkeletonHeader::count`, 117/117 |
| `+0x32C` | 4 | nitro/emitter count | 0 in 97 packages, 1 in one, 2 in 19 |
| `+0x330` | `0x0C` | three reserved `u32` | zero in source and RAM, 117/117 packages |

`rmcCarModelType::ComputeMatrixRemapTable` (`0x3024C8`) uses `+0x328` as its
loop limit. `rmcCarModel::Init` (`0x2F6C48`) uses the same value to allocate
two `(count+31)/32` bone bitsets. It duplicates the count in the skeleton
header and therefore must be updated whenever the skeleton is rebuilt. The
three tail words at `+0x330..+0x338` remain reserved; the following range
identified the consumer and payload controlled by `+0x32C`.

### Nitro/emitter records (`rmcCarModelType+0x33C..+0x1710`)

The supplied range EE `0x01773A0C..0x01774DE0` begins at
`rmcCarModelType+0x33C`, PCK file `0x40C`, and reaches the first byte of the
dword at type `+0x1710`, PCK file `0x17E0`. On the loaded stock 350Z this
whole window is byte-identical to the PCK: it contains serialized data and
literal padding, not a runtime allocation or relocation table.

A census of all 117 canonical root `vp_*.pck` packages separates the large
padding regions from two real fixed-capacity structures:

| relative | size | meaning | evidence |
|---:|---:|---|---|
| `+0x33C` | `0x54` | reserved/padding | `CD` in source and loaded 350Z |
| `+0x390` | `0x60` | two serialized `Matrix34` emitter transforms | populated exactly when `+0x32C` is 1 or 2 |
| `+0x3F0` | `0x11A0` | reserved/padding | `CD` in 117/117 packages |
| `+0x1590` | `0x60` | 24 skeleton-node IDs | unused entries are `FFFFFFFF` |
| `+0x15F0` | `0x120` | reserved/padding | `CD` in 117/117 packages |
| `+0x1710` | 4 (start only) | first count in the following custom-exhaust emitter table | indexed read in particle code; full table is mapped below |

The relationship is exact: `+0x32C` is zero in 97 packages, two in 19, and
one in `vp_ninja_03`. The first one or two IDs at `+0x1590` resolve through
each package's own skeleton to names such as `nitro_0`, `nitro_1`, `nitro0`,
`nitro1`, or `nos_0/nos_1`; the Zonda uses the generic names `ext_0/ext_1`.
Examples include Aprilia IDs 15/16, Cadillac Sixteen IDs 49/48, Gallardo IDs
38/39, Ninja ID 14 and SLR IDs 49/48. The two 0x30-byte records at `+0x390`
are ordinary 3x4 transforms and occur in precisely the same vehicle set.

Retail `mcCarParticles::UpdateExhaustParticles(Vector4&)` at `0x2ED858`
proves how the fallback ID table is consumed. When the two customization
count paths are inactive, it examines exactly `type+0x1590` and `+0x1594`,
skips `FFFFFFFF`, multiplies each node ID by `0x30`, and copies that live
skeleton matrix for the particle emitter:

```text
node_id = type->nitro_emitter_skeleton_id[i];
if (node_id != 0xFFFFFFFF)
    emitter_matrix = live_bone_matrices[node_id];
```

The same function reads `type+0x1710 + selection*4` in one customized path.
Consequently `+0x1710` is not a standalone flag or enum: it is element zero
of the 24-entry custom-exhaust count table mapped in the following range.
Another path uses a separate count table at `type+0x3194` and emitter IDs
selected from `type+0x2E94`.

### Custom-exhaust counts and legacy matrices (`+0x1710..+0x296F`)

EE `0x01774DE0..0x0177603F` maps exactly to
`rmcCarModelType+0x1710..+0x296F`, PCK file `0x17E0..0x2A3F`, a `0x1260`-byte
window. It is again byte-identical between the stock 350Z PCK and RAM image.
The boundaries close arithmetically and through the retail consumer:

```text
+0x1710  u32 emitter_count[24]                 // 0x60 bytes
+0x1770  Matrix34 legacy_matrix[24][4]         // 0x1200 bytes
+0x2970  u32 skeleton_node_id[24][4]           // next range
```

`mcCarCustom+0x11E8` selects the row and byte `+0x11EC` says that the custom
exhaust path is active. This replaces the earlier provisional
"headlights/special" label. It is independently confirmed by three users of
the pair: `mcCarParticles::UpdateExhaustParticles` chooses the count and node
IDs, `rmcCarModel::UpdateExhaustDamagePos` suppresses the stock rear-bumper
anchor adjustment while the byte is set, and retail `0x2FC678` uses the same
choice to modify the selected exhaust skeleton matrix.

The runtime indexing in `UpdateExhaustParticles` is:

```text
choice = custom->custom_exhaust;                       // +0x11E8
count  = type->custom_exhaust_emitter_count[choice];  // +0x1710
node   = type->custom_exhaust_node_id[choice][i];     // +0x2970
live   = live_bone_matrices[node];                    // stride 0x30
```

The table has capacity for 24 choices and four emitters per choice. Across 117
canonical packages there are only six distinct count vectors:

- 83 vehicles have no rows populated;
- 20 have only stock row zero populated (19 with two emitters, one with one);
- 14 have rows 0 through 12 populated;
- all populated counts are 1, 2 or 4; the only four-emitter row is choice 8
  of `vp_z28_81`.

There are 403 count-declared emitter entries in total. Their IDs at `+0x2970`
resolve to skeleton names such as `nitro_0/1`, `nos_0/1`, `exh_00/01` and
`ext_00/01`, and every unused ID slot is `FFFFFFFF`.

The intervening `+0x1770` block looks structurally like one `Matrix34` for
each possible emitter, but it carries no vehicle-specific transform data:
all 11,232 matrices (`117 * 24 * 4`) are the exact same 3x4 identity matrix.
That includes all 403 entries whose counts are active. A whole-ELF immediate
scan finds the count base `+0x1710` and ID base `+0x2970` in the particle
function, but no instruction directly addresses `+0x1770`. It is therefore
exposed in the HexPat as a legacy matrix table, not claimed as an active
runtime source. Its identity contents should be preserved when rebuilding a
package until a non-retail build demonstrates another use.

### Customization tail and fixed skeleton IDs (`+0x2970..+0x31BF`)

The supplied EE range `0x01776040..0x0177688F` is exactly
`rmcCarModelType+0x2970..+0x31BF`, or PCK file `0x2A40..0x328F`. The PCK and
RAM bytes are identical throughout it except for the vtable and two pointers
inside the embedded `carWindowDamage` object, which undergo ordinary
load-in-place relocation.

The range begins with the 24-by-4 node-ID counterpart to the custom-exhaust
count table:

```text
count = type->custom_exhaust_emitter_count[choice];   // +0x1710
node  = type->custom_exhaust_skeleton[choice][i];     // +0x2970
```

Across 117 canonical packages, every one of the 2,808 rows satisfies
`count <= 4` and `count == number of non-FFFFFFFF IDs`. There are 403 live IDs
in total. The next three fixed IDs are `slipstream_left`, `slipstream_right`
and `headlight_cone` at `+0x2AF0/+0x2AF4/+0x2AF8`. The first two names are
proved by all retail skeletons (`sst0/1`, `ss_0/1`, `slipstream0/1`), although
no Remix instruction directly addresses those two serialized slots. The cone
ID is read by `rmcCarModel::Render` (`0x2FD868`).

`rmcCarModelType::AddNamesToPartDatabase` (`0x302D10`) supplies the exact
category semantics. It walks `count` IDs, resolves each through the skeleton's
0x44-byte node table, and calls the named `mcCarPartDb::Add*Name` method. This
also corrects the old Parts editor: spoiler was omitted, exhaust was mislabeled,
and `body` was incorrectly allowed to overwrite the following brush-guard list.

| category | ID array | capacity | count | registration method |
|---|---:|---:|---:|---|
| front bumper | `+0x2AFC` | 24 | `+0x3174` | `AddFrontBumperName` |
| rear bumper | `+0x2B5C` | 24 | `+0x3178` | `AddRearBumperName` |
| side skirt | `+0x2BBC` | 24 | `+0x317C` | `AddSideSkirtName` |
| spoiler | `+0x2C1C` | 24 | `+0x3180` | `AddSpoilerName` |
| hood | `+0x2C7C` | 24 | `+0x3184` | `AddHoodName` |
| scoop/blower | `+0x2CDC` | 24 | `+0x3188` | `AddBlowerName` |
| headlights | `+0x2DD4` | 24 | `+0x318C` | `AddHeadlightName` |
| taillights | `+0x2E34` | 24 | `+0x3190` | `AddTaillightName` |
| car-file exhaust | `+0x2F54` | 24 | `+0x3198` | `AddCarFileExhaustName` |
| body/chop top | `+0x2FB4` | **8** | `+0x31A0` | `AddBodyName` |
| brush guard | `+0x2FD4` | 24 | `+0x31A4` | `AddBrushGuardName` |
| front grille/louvers | `+0x3034` | 24 | `+0x31A8` | `AddFrontGrillName` |
| one-shot kit/mud flaps | `+0x3094` | 24 | `+0x31AC` | `AddOneShotKitName` |
| wheelie bar | `+0x30F4` | 24 | `+0x31B0` | `AddWheelieBarName` |

All 1,638 category lists (`117 * 14`) have `count == filled entries`. The body
capacity is not inferred from its observed maximum: its eight entries end at
`+0x2FD3`, immediately before the independently consumed brush-guard array.

Several skeleton tables in between are runtime helpers rather than menu
categories:

- `+0x2D3C[24]` is indexed by the same blower selection as `+0x2CDC`.
  `rmcCarModel::UpdateBlower` uses it for the animated valve/scoop node.
- `+0x2D9C..+0x2DAC` are fixed trunk, neon-shadow, front-fork, rear-axle and
  steering IDs. `UpdateTrunk`, `MakeAxles`, `Render` and `Update` consume them.
- `+0x2DB0..+0x2DBF` is `FFFFFFFF` in 117/117 packages.
- `+0x2DC0..+0x2DD0` is one popup-spoiler root plus four flap IDs, consumed by
  `rmcCarModel::UpdatePopupSpoilers`.
- `+0x2E94` contains 24 pairs of rear-bumper exhaust node IDs. `+0x3194` is the
  total number of non-`FFFFFFFF` u32 IDs, not the number of populated choices;
  the relation holds exactly in 117/117 packages. The stock 350Z has 42.
- `+0x3154[2]` resolves to `engine_smk_0/1` (with spelling variants) and is
  selected by `mcCarParticles::DrawEngineFire` (`0x2EFDE8`).

At `+0x315C` sits an inline `carWindowDamage` object with runtime vtable
`0x00627FF8`. Its `+0x0C` pointer leads to 0x60-byte records, `+0x14` is the
window count, and `carWindowDamage::FileIO` (`0x301958`) proves record fields
`width +0x00`, `height +0x04`, `position +0x40`, and `rotation +0x4C`. Retail
packages contain zero, three or four windows; the 350Z contains four. The
remaining words are `reserved_319C = 0` and three `FFFFFFFF` sentinels at
`+0x31B4..+0x31BC`.

### Animated parts, wheels and special rendering (`+0x31C0..+0x388F`)

The supplied RAM interval `0x01776890..0x01776F5F` is
`rmcCarModelType+0x31C0..+0x388F`, or PCK file `0x3290..0x395F`. It begins
exactly after the three sentinels above. The geometry-independent animation
tables through `+0x380F` remain byte-identical after loading; the tail then
mixes relocated pointers, runtime class state and four unchanged floats.

| type relative | size | field | consumer/proof |
|---:|---:|---|---|
| `+0x31C0` | `24 * 0x30` | blower closed/base `Matrix34[24]` | `rmcCarModel::UpdateBlower` (`0x2FA378`) indexes it with the selected blower |
| `+0x3640` | 4 | popup-headlight target X angle, radians | `UpdatePopupHeadlights` (`0x2FBAB0`) smooths toward it and rotates up to four IDs from `+0x2DD4` |
| `+0x3644` | `0x5C` | reserved/exporter residue | no direct reader in the car module |
| `+0x36A0` | `5 * 0x0C` | popup-spoiler XYZ rotations | three `Matrix34::RotateLocal*` calls per live piece |
| `+0x36DC` | `5 * 0x0C` | popup-spoiler open positions | interpolated into matrix translation |
| `+0x3718` | 8 | reserved | separates vectors from aligned matrices |
| `+0x3720` | `5 * 0x30` | popup-spoiler closed/base matrices | copied to the live skeleton before rotation/translation |
| `+0x3810` | 4 | `WheelRecord*` | relocated array, stride `0x1C` |
| `+0x3814` | 4 | wheel count | 4 on cars, 2 on motorcycles |
| `+0x3818` | 4 | runtime `mc::CarDescription*` | overwritten from `mc::LookupCar`, used by motorcycle/fire checks |
| `+0x381C` | 8 | inline `atBitSet` | one neon/shadow skeleton bit in every retail package |
| `+0x3824` | `8 * 4` | wheel side-A pointers | negative-X wheels 0/2 on cars; both wheels on bikes |
| `+0x3844` | `8 * 4` | wheel side-B pointers | positive-X wheels 1/3 on cars; null on bikes |
| `+0x3864` | 4 | `mcSpotlightTuneParams*` | constructed by `parFileIO`, runtime vtable `0x00623FD8` |
| `+0x3868` | 4 | three-slot special `rmcShaderGroup*` | handed to the shader resource manager by the type constructor |
| `+0x386C` | 4 | `distort.shadert` | assigned as the active shader during the distortion/back-buffer pass |
| `+0x3870` | 4 | usually `rider_stealth.shadert` | assigned in a second special render pass |
| `+0x3874` | 4 | `rider_chrome.shadert` | third member of the special group |
| `+0x3878` | `4 * 4` | base LOD distances | `(30, 50, 100, 110)` in 117/117 packages |
| `+0x3888` | 4 | `rmVehOccludeDataSet*` | relocated, then consumed by `rmVehOcclude` |
| `+0x388C` | 4 | padding | `CDCDCDCD` |

`rmcCarModel::UpdatePopupSpoilers` (`0x2FBC00`) resolves the root plus four
flap IDs at `+0x2DC0`, copies each `+0x3720 + 0x30*i` matrix, applies the three
angles at `+0x36A0 + 0x0C*i`, then interpolates the translation toward
`+0x36DC + 0x0C*i`. Only six retail vehicles have a live popup-spoiler root;
unused entries may contain `CDCDCDCD` or stale exporter bytes and must be
ignored according to the skeleton-ID sentinel. Popup-headlight angle is
absent (`CDCDCDCD`) in 106 packages and populated in 11.

Each `WheelRecord` is exactly:

```text
+0x00 u32 ordinal
+0x04 u32 axle_skeleton_id
+0x08 u32 wheel_skeleton_id
+0x0C u32 suspension_or_arm_skeleton_id
+0x10 Vector3 position
```

The 117-package census has no invalid record pointer or node ID. All 102 cars
have four records and pointer layout A=`(0,2)`, B=`(1,3)`; all 15 motorcycles
have two records in A=`(0,1)` and an empty B array. `mcCarSim::MakeWheels`,
`MakeSuspensions`, `rmcCarModel::UpdateWheels`, `UpdatePanels` and `DrawType`
all consume this same topology. The position is static vehicle-type data: no
bumper-selection index participates. Making a wheel move only for one bumper
therefore needs runtime code to select another position (or another complete
model type); it cannot reuse the existing rear-bumper exhaust table without a
hook.

The inline `atBitSet` is `{u32 words; u16 word_count; u16 bit_count}`. Its
dimensions match the skeleton count in 117/117 packages and exactly one bit is
set in each: a node whose name belongs to the `neon`/`shadow` family. On the
350Z it is bit 15, node `neon`.

The special shader group contains three slots in every package. Direct name
resolution gives `distort.shadert` at `+0x386C` in 117/117 and
`rider_chrome.shadert` at `+0x3874` in 117/117. `+0x3870` resolves to
`rider_stealth.shadert` in 115; `vp_d_charger_06` and `vp_phaeton_04` instead
reach `car_decal_chrome.shadert` storage. This is why the HexPat labels the
field but keeps the exception in its comment. Serialized vtable tokens still
must not be used as cross-package class IDs; they vary by source module/build.

#### `drwShaderDistort` (`0x017B74A0`)

The supplied address is the start of the 350Z's `distort.shadert` object, not
a new vtable: runtime vtable `0x0062E588` was already known. The factory at
`0x003B3850` allocates exactly `0x38` bytes for this class. The common
`rmcShaderComplex` portion occupies `+0x00..+0x2F`; the derived tail is:

| offset | field |
|---:|---|
| `+0x30` | u8 index of `DistortEnvScale` |
| `+0x31` | u8 index of `DistortFresnelMin` |
| `+0x32` | u8 index of `DistortFresnelMax` |
| `+0x33` | u8 index of `DistortFresnelScale` |
| `+0x34..+0x37` | padding (`CDCDCDCD`) |

The alpha `MC.MAP` closes the retail methods as `Load=0x003C38C8`,
`Draw=0x003C3A10` and `Bind=0x003C3AB0`. `Load` sets type `0x3D`, registers
the four names and writes their resolved indices to the tail. Unlike
`drwShaderCarpaintNoVinyl::Draw`, the retail `Distort::Draw/Bind` path does
not read those indices directly; they are retained class metadata rather than
four values gathered by this implementation on every draw.

The first pass has three textures, not one pointer: colour, light map and
environment map. On the 350Z their slots resolve to `__metalflake__`, the
resident `noalpha` texture and `__envmap__`. The pass uses microcode index 10
(`distort_dc`), state program
`01 01 06 02 03 00 09 0F 04 01 FF`, texture count 3, reflect texgen/source
`0x30`, `modulate2x` combine 2 and state mask `0x25A`. Its transform contains
the four constant 0.5 waveforms written by the source `distort.shadert`.

This is invariant across all 352 complete vehicle packages: type `0x3D`, mask
`0x25A`, indices `00 01 02 03`, padding `CDCDCDCD` and the same state program.
Every serialized object has exactly two incoming pointers - the special shader
group slot and the `rmcCarModelType+0x386C` alias. The vtable residue still
varies by module (`0x007A94E8` 271 times, `0x007AA7F0` 75 and `0x007A96E8`
6), so it is not a portable type token.

`rmcCarModel::Render` (`0x2FD868`) copies the four `+0x3878` values into
`drwShaderModel+0x20`, scales them for model size, applies a view-angle factor
to the last three and then compares camera distance to choose H/M/L/VL. This
explains the live 350Z values `(30, 54.659, 109.318, 120.250)`: they are the
base `(30, 50, 100, 110)` after the current frame's angular scale, not distinct
serialized tuning.

`rmVehOccludeDataSet` is a 12-byte object: signed type/version, group count and
group-array pointer. Its constructor walks `group_count` records with stride
`0x10`; each begins with the inherited `rmConvexSpaceData` fields and relocates
its 32-byte plane array. All 117 packages carry a non-null dataset pointer.
The 102 cars use type 2 and 4..11 groups; the 15 motorcycles use the empty
type `-1`, count-zero form.

### Live `mcCarCustom`

PCK file `0xAC` contains `0xCDCDCDCD`, but the loaded image contains
`0x0156B0A0`, whose vtable `0x006276C0` identifies `mcCarCustom`. It is external
runtime state, not part of the load-in-place body, and is the object passed to
`rmcCarModelType` to choose the active pieces.

`rmcCarModelType::ComputeMatrixRemapTable` (`0x3024C8`) compares its part arrays
against these `mcCarCustom` fields. On the stock 350Z capture:

| `mcCarCustom` relative | selection | value |
|---:|---|---:|
| `+0x1184` | front bumper | 0 |
| `+0x118C` | rear bumper | 0 |
| `+0x1194` | side skirts | 0 |
| `+0x119C` | brush guard/top family (provisional name) | 0 |
| `+0x11A0` | grille/louvers | 0 |
| `+0x11A8` | wing/spoiler | 0 |
| `+0x11E8` | custom exhaust choice | 0 |
| `+0x11EC` | custom exhaust enabled byte | 0 |
| `+0x11F0` | additional 24-slot family, not named yet | 0 |
| `+0x11F8` | hood | 0 |
| `+0x1200` | scoop/blower | -1 (none) |
| `+0x12BC` | taillights | 0 |

The exact offset pairing is code-proven. The remaining provisional labels need a
second savestate with known non-stock selections before being promoted to final
names. This is now an easy differential test: capture the same car after
changing one part and rerun the probe.

## `mcSpotlightTuneParams` (`0x01777100..0x01777163`)

The vtable requested at `0x01777100` is runtime `0x00623FD8`, identified from
its virtual name (`"mcSpotlight"`), the alpha `MC.MAP`, and the retail
constructor/FileIO pair as **`mcSpotlightTuneParams`**. Its owner is not the
object that happens to follow it in memory: the stable path is
`rmcCarModelType+0x3864 -> mcSpotlightTuneParams`. In the 350Z raw PCK it starts
at file `0x3B00`; the serialized class token is `0x0079DF68` and is replaced by
the runtime vtable while loading.

| Relative | Type | FileIO name / meaning | 350Z value |
|---:|---|---|---:|
| `+0x00` | `u32` | class token / runtime vtable | `0x00623FD8` in RAM |
| `+0x04` | pointer | config name | `"default"` |
| `+0x08` | `u32` | zero | 0 |
| `+0x0C` | `u32` | preserved CD residue | `0xCDCDCDCD` |
| `+0x10` | `float` | `colorr` | `0.004981` |
| `+0x14` | `float` | `colorg` | `0.159272` |
| `+0x18` | `float` | `colorb` | `0.0` |
| `+0x1C` | `float` | `brightness` | `0.0` |
| `+0x20` | `float` | `xscale` | `1.0` |
| `+0x24` | `float` | `yscale` | `1.0` |
| `+0x28` | `float` | `zscale` | `1.0` |
| `+0x2C` | `float` | `xOffset` | `0.0` |
| `+0x30` | `float` | `yOffset` | `0.149996` |
| `+0x34` | `float` | `zOffset` | `1.259996` |
| `+0x38` | `float` | `m_hiBeamYScale` | `1.007972` |
| `+0x3C` | byte + pad | runtime/legacy byte, then three CD bytes | `0, CD CD CD` |
| `+0x40` | `float` | `sharpness` | `0.909981` |
| `+0x44` | `float` | `falloff` | `1.049986` |
| `+0x48` | `float` | `m_aimTweak` | `-0.239991` |
| `+0x4C/+0x50` | `u32[2]` | runtime/legacy, no retail reader found | 0, 0 |
| `+0x54` | `float` | `m_hiBeamTweak` | `0.199984` |
| `+0x58/+0x5C` | `u32[2]` | runtime/legacy, no retail reader found | 0, 0 |
| `+0x60` | `s32` | draw-time cone intensity/state scalar | 11 |

The exact retail routines are constructor `0x001D64F0`, `Load` `0x001D65E0`
and `FileIO` `0x001D6650`. `mcSpotlight::draw(Matrix34&,int)` at `0x001D78D8`
reads the three offsets, while the main draw at `0x001D86E8` reads sharpness,
falloff and `+0x60`. The last word is **not** the number of cone segments: the
segment count comes from separate static state initialized by
`mcSpotlight::initStaticData` (`0x001D6978`).

A census of all 117 canonical retail `vp_*` packages found this exact 0x64-byte
object in 117/117, always named `default`, with `+0x08=0`, `+0x0C=CDCDCDCD`,
`+0x3C..+0x3F=00 CD CD CD`, zero at `+0x4C/+0x50/+0x58/+0x5C`, and
`+0x60=11`. The serialized class residue has three values (`0x0079DF68` in 90,
`0x0079F270` in 25 and `0x0079E168` in 2), proving again that a writer must
preserve/detect the object by graph role rather than hardcode its first word.
The Performance tab now exposes its 15 named floats and draw intensity.

## `drwShaderModel` root

The pointer stored at PCK `0x1A8` is serialized `0x06803AF0`, which maps to PCK
file `0x3B70` and EE `0x01777170`. Token `0x007A9420` becomes vtable
`0x0062E4C0`, whose named methods identify `drwShaderModel`.

| root relative | field | 350Z loaded value |
|---:|---|---:|
| `+0x00` | vtable | `0x0062E4C0` (`drwShaderModel`) |
| `+0x04` | byte count + u16 load flags | high flag gains `0x8000` |
| `+0x08` | internal resource pointer | `0x01778E50` |
| `+0x10` | high LOD | `0x0178E290` |
| `+0x14` | medium LOD | `0x017A5890` |
| `+0x18` | low LOD | `0x017B0D40` |
| `+0x1C` | very-low LOD | null |
| `+0x20..+0x2C` | four LOD switch distances | 30.0, 54.659, 109.318, 120.250 |
| `+0x30..+0x3C` | four bucket masks | `0x7F`, `0x7F`, `0x21`, `-1` |
| `+0x40` | cull center, `Vector3` | `(0, 0, -0.0584)` |
| `+0x4C` | cull radius | about `2.9966` |
| `+0x50` | box minimum, `Vector3` | zero in this model |
| `+0x5C` | box maximum, `Vector3` | zero in this model |
| `+0x68` | skeleton header | `0x0178E2A4` |
| `+0x6C` | edge model | null |
| `+0x78` | physical bound | `0x017B1AB0` |
| `+0x80` | `drwBonyData*` | `0x01777200` |

The names are code-backed:

- `rmcLodGroup::LoadLod` initializes the four floats at group `+0x10` (root
  `+0x20`). `rmcCarModel::Render` then overwrites them from the type's base
  distances at `+0x3878`, scaled for the current model and camera angle.
- `rmcLodGroup::ComputeBucketMask` writes group `+0x20` (root `+0x30`), and
  `rmcLodGroup::GetBucketMask` reads that array.
- `drwShaderModel::LoadSkel` writes root `+0x68`.
- `drwShaderModel::LoadBounds` writes root `+0x78`.
- The bound's file token `0x007AE4B0` becomes vtable `0x00633010`, identified
  as `phBoundGeometry`. The car collision is therefore embedded in the main
  vehicle PCK, separate from visual meshes.
- The object at root `+0x80` is relocated by
  `drwBonyData::drwBonyData(datResource&)` (`0x3B2C40`).

### Vehicle `phBoundGeometry` (`0x017B1AB0`, requested tail `0x017B1AF8..0x017B1D97`)

The requested range starts at `phBoundGeometry+0x48`, not at an object
boundary. The object itself starts at RAM `0x017B1AB0`, PCK `0x3E4B0`, and is
reached by the single pointer at `drwShaderModel+0x78` (`0x017771E8`). Its
runtime vtable `0x00633010` was already known; the new result is the complete
`0x80`-byte object and both arrays that follow it.

| Relative | Size | Meaning | 350Z |
|---:|---:|---|---:|
| `+0x04..+0x07` | 4 bytes | type, material count, has offset, has CG offset | `3,2,1,1` |
| `+0x08/+0x14` | 2 x `Vec3` | AABB min/max | exact vertex extents |
| `+0x20` | `Vec3` | `phBound::SetOffset` value | `(-0.00038,0.665,0.06055)` |
| `+0x2C` | `Vec3` | center-of-gravity offset | `(0.2840,0.6759,-0.1555)` |
| `+0x38/+0x3C` | 2 floats | sphere radius / radius from origin | `2.2595 / 2.9272` |
| `+0x48` | `u16` | runtime bound flags | PCK `0`, RAM `0xFDE8` |
| `+0x4C/+0x50` | 2 x `u32` | vertex / polygon count | `14 / 14` |
| `+0x54` | `u16` | edge count | `0` |
| `+0x58..+0x64` | 4 words | runtime polyhedron state | reset to `-1,0,-1,0` |
| `+0x68/+0x6C` | 2 pointers | `Vec3[]` / `phPolygon[]` | `0x017B1CF0 / 0x017B1B30` |
| `+0x70` | pointer | global material-index table | `0x01779C28 -> [0,0]` |
| `+0x74` | `s32` | default material override | `-1`, use table |

The two arrays account for the range exactly. `0x017B1B30..0x017B1CEF` is
`14 * 0x20` bytes of `phPolygon`; `0x017B1CF0..0x017B1D97` is `14 * 12`
bytes of vertices. Every polygon stores unit normal, geometric area, four
vertex `u16` values and four neighbour `u16` values. `vertex[3] == 0` together
with `neighbour[3] == FFFF` marks a triangle. This bound contains four
triangles and ten quads; all indices are in range and every neighbour link is
reciprocal.

`phBoundGeometry(datResource&)` closes the pointer set: the
`phBoundPolyhedron` base relocates `+0x68` and `+0x6C`, then
`phBoundGeometry` relocates `+0x70`. `CalculateExtents` reads all vertices to
rebuild the AABB and radii, while `CalculatePolyNormals` walks the `0x20`-byte
records and recalculates their normal/area from the vertex array.

There really are **two** vehicle bounds. Package root `+0x0C` reaches another
`phBoundGeometry` at `0x017B8760`; it is not an alias of model `+0x78`. Across
all 352 HostFS vehicle packages both objects exist, are always distinct, and
their serialized vertices/polygons are byte-identical. After loading, the
package-level copy is mutable: on this capture four vertices, twelve polygon
planes, AABB and CG offset differ, and its materials are `[29,29]` with
default 29, whereas the model copy remains `[0,0]` with default `-1`. The
HexPat therefore expands both objects independently instead of deduplicating
them. All 704 measured bounds have valid vertex indices and reciprocal
neighbour topology. Their serialized vtable residue has three module values,
so `0x007AE4B0` is not universal.

### Bone-tag `HashEntry` (`0x017771F4..0x017771FF`)

This 12-byte interval is PCK file `0x3BF4..0x3BFF`. It is **not**
`drwShaderModel+0x84..+0x8F`: the model ends immediately after its
`drwBonyData*` at `+0x80`, for a total size of `0x84`. The following allocation
happens to be one generic RAGE `HashEntry`:

| EE address | entry relative | PCK value | loaded value | meaning |
|---:|---:|---:|---:|---|
| `0x017771F4` | `+0x00` | `0x06803D48` | `0x017773C8` | name pointer -> `"arm_l0"` |
| `0x017771F8` | `+0x04` | `3` | `3` | stored data, bone-tag index plus one |
| `0x017771FC` | `+0x08` | null | null | next entry in this hash bucket |

`HashTable::Access` (`0x42DD60`) hashes the requested string, selects a bucket,
walks `HashEntry+0x08`, compares the string at `+0x00`, and returns `+0x04`.
`rmcCarModelType::GetIndexForTag` (`0x302488`) explicitly subtracts one from a
nonzero result. Therefore this entry maps `arm_l0` to effective index `2`; the
stored `3` reserves zero to mean “not found”.

The only reference to this entry is at EE `0x01777564`, the `following` field
of the entry named `spoiler2`. That does not make `arm_l0` a child of the
spoiler: both strings hash to bucket 38 and are merely collision-chain
neighbours.

The ownership path is:

```text
drwShaderModel+0x80 -> drwBonyData
drwBonyData+0x00    -> bone-tag HashTable
HashTable+0x10      -> 101 HashBucket pointers
HashBucket          -> HashEntry{name, skeleton_index_plus_one, following}
```

The generic `HashTable` header is 0x18 bytes: fixed-name capacity and two mode
bytes, optional fixed-entry storage, bucket count, entry count, bucket-array
pointer and kill-list link. `HashTable(datResource&)` relocates the bucket
array, every entry name and every `following` pointer. Its caller-provided
callback relocates `data` only when the value is above `0x400`; the bone-tag
table deliberately keeps small 1-based integers unchanged.

Traversing the same path in all 117 canonical packages finds 117 valid
bone-tag tables, always with 101 buckets, containing 15,005 entries in total.
Every stored entry count matches the number reached through the chains; there
are no invalid links or cycles, and the longest chain has six entries.
`arm_l0` is present in 116 of 117 and, when present, always hashes to bucket 38;
`vp_d_g35_05` is the sole package without that tag. Its stored index varies with
each vehicle's skeleton. The 350Z value is the `3 -> 2` mapping above.

The HexPat now gives this caller-specific table its own `BoneTagEntry` type, so
ImHex displays `stored 3 -> skeleton index 2` directly and labels the next link
as a hash collision. `mc3_pck_embed.Pck.bone_tags()` exposes the same graph as
`(name, skeleton_index, stored_index_plus_one, bucket, file_offset)`, while
`bone_tag_map()` returns a name-to-index dictionary. That split matters for the
next MC:LA PSP conversion: read the PSP hierarchy with `skel_nodes()`, then map
semantic tags into the PS2 destination by name; do not transplant a source
array index or interpret the hash chain as a bone hierarchy.

## HLOD, MLOD and LLOD after loading

All three table headers become vtable `0x00626D08`, identified as
`rmcLodPaged`. Their three parallel arrays remain: mesh pointers, name pointers
and u32 skeleton IDs.

| LOD | serialized VA | EE address | slots | state |
|---|---:|---:|---:|---|
| HLOD | `0x0681AC10` | `0x0178E290` | 118 | 11 embedded, 6 external stock, 101 empty |
| MLOD | `0x06832210` | `0x017A5890` | 5 | all embedded |
| LLOD | `0x0683D6C0` | `0x017B0D40` | 1 | embedded |

The six HLOD mesh slots that are zero in the file and populated by the loader:

| slot | skeleton ID | EE mesh | stock part |
|---:|---:|---:|---|
| 7 | 19 | `0x017C5380` | spoiler |
| 28 | 41 | `0x017C7C00` | hood |
| 39 | 53 | `0x017C8A00` | side skirts |
| 60 | 75 | `0x017C9700` | front bumper |
| 81 | 113 | `0x017CEA00` | rear bumper |
| 103 | 178 | `0x017D2500` | tail lights |

Every injected object starts with retail vtable `0x00626D98`, identified as
`rmcModelGeom`. This is direct runtime proof of the external-slot rule: a zero
mesh pointer plus a name asks `rmcLodPaged` to page the named `.mesh.pck`, then
replaces the zero slot with the resulting `rmcModelGeom*`.

The ID is not an enable flag. `rmcLodPaged::Touch` (`0x2A70A0`) calls the model
factory whenever the mesh pointer is null and the name pointer is non-null,
passing the ID unchanged. `rmcModelFactory::Create` (`0x2A8718`) forwards to
`rmcModel::Create` (`0x2A8CA0`), which opens/deserializes the mesh before storing
the ID byte in the resulting model. ID 0 is therefore valid and attaches the
mesh to skeleton node 0, `vroot`. A failed experiment with six ID-0 shell slots
was misleading because one named member was absent: one failed model makes
`Touch` return false and can leave the whole LOD non-resident.

The main body ends at EE `0x017C1760`; external parts live outside it. They must
not be reverse-mapped through the main body's fixed relocation delta.

## Skeleton integrity

The skeleton header is at PCK file `0x1ACA4`, EE `0x0178E2A4`. Its list is at
PCK file `0x3E7C0`, EE `0x017B1DC0`; it has 250 nodes of 0x44 bytes.

The file/RAM comparison produced:

- 250/250 names identical;
- 250/250 node IDs identical;
- 250/250 anchor vectors byte-identical;
- 250/250 `next`, `child` and `parent` pointers valid after relocation.

Thus the loaded skeleton is not rebuilt. It is relocated in place. External
stock parts attach to it by the HLOD slot's u32 skeleton ID.

## Whole-body comparison

VIF packet payloads were excluded before scanning aligned words, because vertex
bytes frequently look like in-range pointers.

| class | aligned u32 words |
|---|---:|
| unchanged | 43,658 |
| exact internal relocation | 2,725 |
| genuine zero-to-runtime pointer | 8 |
| other runtime changes | 697 |
| packet words deliberately skipped | 32,840 |

The eight serialized-zero-to-pointer fields are the six external HLOD meshes
plus the `mcCullableType` owner/registry pointer at `rmcCarModelType+0x04` and
active instance-list head at `+0x18`. The runtime `mcCarCustom*` is a ninth
important injected pointer, but its serialized word is `0xCDCDCDCD` padding,
not zero. Nine zero-to-`0x200`
changes found by the first pass are scalar state, not pointers; the reusable
probe excludes low addresses for this reason.

## ImHex tree shared with the city work

The vehicle RAM pattern now reuses the parts of `mc3_city_ram.hexpat` that
describe shared engine classes or solve generic sparse-pointer problems; no
city-specific `mcCity` layout was copied.

- `LodPaged.meshes` is an array of `MeshSlot` wrappers. Null slots remain
  visible, while each non-null embedded or externally paged stock part opens
  as an `rmcModelGeom` in the same tree.
- A mesh can expose its material-index table, group table and `BlockEntry`
  list with `EXPAND_MESH_GROUPS`. This fixes the previous pattern, which
  defined `Mesh`, `GroupEntry` and `BlockEntry` but never instantiated them.
- `drwShaderModel+0x08` opens as the shared `rmcShaderGroup` class. Its sparse
  shader slots and linked list of local parameters are represented directly.
  On this capture the group is at `0x01778E50`, has vtable `0x00626F20`, 49
  shader slots and 41 locals beginning at `0x01779004` (`shadowR`).
- The city's later `rmcShaderComplex` work corrected an important mistake in
  the first vehicle pattern: complex shader `+0x08` is the program pointer of
  an inline 0x20-byte pass, not a direct texture pointer. Pass `+0x08` points
  to a *texture-pointer slot* (one extra indirection), and pass `+0x1C` links
  the next pass. Derived classes are recognized dynamically because virtual
  slot 3 equals `rmcShaderComplex::SetRemapInfo` (`0x002B0318`), instead of by
  maintaining a partial vtable list.
- This rule classifies 48 of the 49 live 350Z shaders as complex or derived;
  the remaining one is `rmcShaderBasic`. It opens 56 passes. All 53 non-empty
  byte programs terminate cleanly at opcode `0xFF`; the other three passes
  are deliberately empty. Eight shaders have two passes. The decoded names
  and vtables include car paint, windows, coloured glass, chrome, carbon
  fibre, rubber, head/brake/reverse/tail lights, hood and decals.
- The shared texture dispatch is now complete enough to avoid crossing object
  boundaries: type 2 `rmcTextureReference` is exactly 0x10 bytes and forwards
  through its target at `+0x0C`; type 0 `rmcTexturePS2` is 0xA0 bytes with four
  `MipDesc`, TEX0/TEX1, GS block addresses, clamp and mip count. The 350Z
  validates both layouts independently: live texture `window` at `0x01779900`
  decodes as 8x8 PSMT4, while `vp_350z_04_trim` at `0x01779F90` decodes as
  256x256 PSMT8.
- The detached address `0x0178C110` (PCK `0x18B10`) is a complete
  `drwShaderCarpaintNoVinyl`, reached normally from the
  `drwShaderTaillightCasing` object at `0x0178C0C0`. Casing `+0x30` points to
  the three-entry vector at `0x0178C100`: NoVinyl at `0x0178C110`,
  CarbonFiber at `0x0178C150`, and Chrome at `0x0178C190`. Thus adjacency is
  not the ownership rule; the explicit vector is.
- `drwShaderCarpaintNoVinyl` is exactly `0x34` bytes. It is the common
  `rmcShaderComplex` through `+0x2F`, followed by four `u8` local-parameter
  indices: `CarPaintEnvScale`, `CarPaintFresnelMin`,
  `CarPaintFresnelMax`, and `CarPaintFresnelScale`. On this 350Z they are
  `14,15,16,17`. `Load` at `0x003B6EF8` resolves those names; `Draw` at
  `0x003B7040` indexes four floats and uploads the resulting Fresnel vector.
  The live object has type `0x3E`, mask `0xB0A`, and vtable `0x0062E910`.
- A HostFS census found 66 casing-owned NoVinyl children in the parsable
  vehicle variants. All 66 agree on type `0x3E` and mask `0xB0A`; their four
  indices vary because each shader group's local list has a different order.
  Their file tokens split between `0x007A98C0` (60) and `0x007AABC8` (6), so
  those tokens remain build residue. The factory also proves that runtime
  vtable `0x0062F8E8` belongs to `licenseplate.shadert`, correcting the old
  HexPat label that called it NoVinyl.
- The latest detached address, `0x01779390`, is the start of the 350Z's
  `drwShaderCarDecalNormal`, not an unrelated allocation.  It is reached from
  `rmcShaderGroup` slot 4, which is a `drwShaderCarDecalMulti` at
  `0x01779340`; its field at `+0x48` points to the normal shader. PCK file
  offset is `0x5D90` and the complete derived object is exactly `0x40` bytes;
  the next shader begins at `0x017793D0`.
- The object is `rmcShaderComplex` through `+0x2F`: inline pass at `+0x08`,
  template `ConstString` at `+0x28`, and render-state presence mask at `+0x2C`.
  The serialized name points to `car_decal.shadert`; construction from
  `datResource` deliberately clears it in this live capture.  The mask is
  `0x21A`, bits 1/3/4/9, exactly matching the compact program
  `01 01 03 00 09 07 04 01 FF` (`lighting`, `blend`, `alphafunc`,
  `colorwrite`).  `rmcShaderComplex::Draw` uses that mask to save and restore
  only the affected scratchpad render-state words.
- `drwShaderCarDecalNormal+0x30` is an RGBA float vector.  `Load` initializes
  `(1,1,1,0.8)`.  `Draw` packs RGB into the low 24 bits, rounds and halves the
  alpha into the high byte, writes the resulting AABBGGRR colour at
  `0x70000080`, calls the base draw and restores `0x80FFFFFF`.  The attached
  RAM has `(0.8,0.8,0.8,0.8)`, proving RGB can be changed after loading; it is
  active state rather than serialized padding.
- A census of the 117 canonical vehicle packages found 97 valid
  `car_decal.shadert` objects.  All 97 agree on flags `0x206`, program,
  state mask and default colour.  Their serialized vtable residue has three
  build/module values (`0x007AA528`, `0x007AB830`, `0x007AA728`), reinforcing
  that writers must identify the class semantically and never copy a vtable
  token as a universal constant.  Twenty packages have no `car_decal` slot.
- The adjacent object at `0x017793D0` (PCK `0x5DD0`, size `0x40`) is
  `drwShaderCarDecalChrome`.  It is the second implementation inside the same
  slot-4 resource: `resource+0x48` selects the normal decal and `+0x4C` selects
  chrome.  Its PCK name is `car_decal_chrome.shadert`, live vtable is
  `0x0062F418`, and its class-specific draw is `0x003BB070`.
- Chrome has two mandatory `ShaderPass` records.  The first is inline at
  `+0x08` and its `following` at `+0x24` reaches `0x01779490`.  Its programs
  decode as `r1=01 r3=00000000 r9=08 r4=01` and
  `r1=01 r3=00010054 r9=0F`; these exactly implement the two passes in
  `car_decal_chrome.shadert`.  The second pass also owns the texture-transform
  block at `pass+0x14`.  The chrome draw dereferences that block and updates
  its `scales`/`scalet` waveforms before drawing, so neither the pass nor its
  parameter pointer may be dropped by a transplant.
- Chrome's tail again begins at `+0x30`, but it must not reuse the normal
  decal's RGBA labels.  `+0x30/+0x34/+0x38` are RGB and are packed with fixed
  alpha `0x80`.  `+0x3C` is a reflection-to-white interpolation factor used
  while uploading the environment/reflection matrix and setting chrome
  Fresnel parameters.  `Load` defaults to `(1,1,1,0.2)`; this capture has
  `(0.8,0.8,0.8,1.0)`.  A census found the same two programs, `0x21A` mask,
  two-pass topology and default tail in all 97 vehicles that have `car_decal`.
  Serialized chrome vtables again have three module-specific values
  (`0x007AA4C8`, `0x007AB7D0`, `0x007AA6C8`).
- LOD resource-name pointers and skeleton node names open as null-terminated
  strings instead of remaining bare addresses.
- The shader and texture layouts were reorganized after visual inspection in
  ImHex. `TextureAny` now owns its vtable/type header once and appends either a
  PS2 or reference body; it no longer overlays a second complete structure at
  the same address. `Shader` similarly owns its inline `ShaderPass`, template
  name, state mask and derived-class tail, so its displayed size is now the
  real `0x30`, `0x40` or `0x50` instead of the old apparent 12 bytes.
- `drwShaderCarDecalMulti` is now complete through `+0x4F`: variants array,
  active variant, count, the five `DecalShader*` local indices, and explicit
  normal/chrome child pointers. Consequently both `0x01779390` and
  `0x017793D0` appear through the normal tree at
  `vehicle/model_type/model/shaders/slot[4]/shader`, without a raw byte window.
- `ShaderPass+0x0C/+0x10/+0x14/+0x18` are no longer unnamed zero/flag words.
  They are the `texframe` waveform, `user0` waveform, texture transform and
  four packed stage bytes (`texture_count`, texgen/source, combine, reserved).
  State programs open as recursively decoded commands, with opcode names and
  one-byte versus four-byte payloads visible in the pattern tree. Texture
  transform fields expose runtime matrix, scale, slide and rotation waveform
  pointers.
- Pointer formatting and expansion now share the same EE-range check.
  `0xCDCDCDCD`, sentinels and small scalar values are displayed as `raw`, not
  falsely formatted as EE addresses, and they cannot instantiate a child at
  address zero. Material/group/name/skeleton arrays also require all of their
  source pointers to be valid before opening.
- Known aliases are no longer expanded twice by default. Wheel-side pointers
  remain visible but their targets are controlled by `EXPAND_WHEEL_ALIASES =
  false`; the authoritative `wheel_records` array is expanded once. The three
  direct special-shader pointers are expanded only if their containing shader
  group is absent. `lod_very_low`, previously omitted, is now opened normally.
- The raw `InspectionWindow` was removed because it painted reachable bytes a
  second time and reduced every field to anonymous `bytes[n]`. For an actually
  detached shader, set `SHOW_TYPED_SHADER_INSPECTOR = true` and change
  `INSPECT_ADDRESS`; leave it false for normal browsing to avoid overlap.
- Fields still labelled `unknown_*` or `reserved_*` are deliberate. They have
  a stable offset/width but no confirmed consumer yet; the cleanup does not
  replace missing evidence with descriptive guesses.

### `mcPlayerInputTune` (`0x017B8A90`)

This address was already reached from `VehicleBody+0x18` (disk header pointer
at `0x98`) and its runtime vtable `0x00634A50` was already named. The object is
exactly `0x3C` bytes; the config string begins immediately after it at
`0x017B8ACC`. Its PCK offset is `0x45490`.

| offset | field | confirmed consumer |
|---:|---|---|
| `+0x0C` | `SSSThreshold` | loaded by `FileIO`; runtime use may be copied during initialization |
| `+0x10` | `SSSValue` | loaded by `FileIO`; runtime use may be copied during initialization |
| `+0x14` | legacy constant `6.705` | none |
| `+0x18` | `WheelRange` | `mcPlayerInput::UpdateInput` (`0x0050CE68`) |
| `+0x1C` | `FrtWhlAmpDiv` | `mcPlayerInput::UpdateEffects` (`0x0050E6A0`) |
| `+0x20` | `FrtWhlAmpExp` | `UpdateEffects`, exponent passed to the response curve |
| `+0x24` | `ConstantMag` | `UpdateEffects`, force-feedback constant magnitude |
| `+0x28` | `SpringMag` | `UpdateEffects`, spring magnitude |
| `+0x2C` | `DamperMag` | `UpdateEffects`, damper magnitude |
| `+0x30` | `SteerRate` | `UpdateInput` |
| `+0x34` | `HandbrakeRate` | `UpdateInput` |
| `+0x38` | `DriftRate` | `UpdateInput` |

Retail `mcPlayerInputTune::FileIO` is `0x0050F788`; it names all eleven fields
present in the Xbox `.vehinput` source and deliberately jumps over `+0x14`.
The value at that gap is not random RAM: it is float `6.705` (`0x40D68F5C`)
in 117/117 canonical retail packages, is unchanged after loading, and already
occupies the same offset in the October 2004 alpha package, whose class was
smaller. It is absent from the text source and no caller of
`mcPlayerInput::GetTune` (`0x0050CC30`) reads it. The HexPat therefore exposes
it as `legacy_constant_6_705`, not as editable steering behavior.

The controller path also explains the integer fields. `UpdateEffects` turns
`ConstantMag`, `SpringMag` and `DamperMag` into calls to the PS2 force-feedback
API after shaping wheel motion with `FrtWhlAmpDiv/Exp`. `UpdateInput` consumes
`WheelRange` separately and uses the three rate values to smooth steering,
handbrake and drift inputs. The package layout was already complete; this pass
only replaced the misleading anonymous `reserved_14` label.

### Coverage imported from the existing `vp_*.pck` tools

The RAM pattern now exposes the structures that the toolkit had already proved
while reading or editing vehicle files. This is intentionally a convergence of
the readers, not a second independent interpretation of the same bytes:

- `mc3_pck_embed.py`, `mc3_pck_manager.py` and `mc3_mesh_convert.py` provide
  the LOD slot/name/ID tables, skeleton nodes, mesh material/group tables and
  block lists. Every `BlockEntry` now follows its packet and dispatches the two
  vehicle tags (`98 00 02 6C`, `C5 01 02 6C`) and the traffic tag
  (`98 00 02 60`). Position V3-16, UV V2-16, colour V4-8 and normal V3-8 arrays
  are visible separately; the low bit of `x_with_adc` is the ADC strip-restart
  flag. The calculation uses the real `vertex_count` and four-byte UNPACK
  padding, never a raw tag scan.
- The shared texture reader reaches all four `MipDesc`/`MipData` records. With
  `EXPAND_TEXTURE_PIXELS = true`, each resident `pixel_buffer` opens its
  0x90-byte allocation header, indexed pixels and the CLUT after the final mip.
  It is off by default because byte-per-pixel patterns are the largest optional
  part of the tree.
- `mc3_perf.py` supplies the typed `VehInput`, `VehDamage`, `VehModel`, stock
  and upgraded `VehSim`, `VehGyro`, `VehZone` and `VehMods` paths. The two
  `VehSim` records open wheel, engine, transmission, drivetrain/freetrain,
  axles, aero, fluid, nitro, SSTurbo, hydraulics and AI subobjects from the
  pointer table at `+0x54`. On the attached 350Z this is 36 sections and 512
  named editable fields, the same count reported by the Manager.
- Upgrade lists are selected by the first `u16 category` in each target, not
  by pointer-slot order. The 350Z stores category 3 Suspension before category
  2 Brake. The HexPat uses the same category dispatch and per-shop-item caps as
  `vehmods_decode`, so a list cannot run through the next contiguous list while
  looking for the later `CDCD` sentinel.
- `bnk_tool/pck.py` supplies the vehicle-audio root and all named Level slots.
  The known TurboBlower/Engine/Exhaust targets open as `AudioBlockFull`: bank,
  eight allocated RPM/sample ranges, warble/boost values, EngineRevSound and
  five curve pointers. Common targets open as 22-float profiles. Setting
  `EXPAND_AUDIO_CURVES = true` also opens the eight allocated nine-float rows
  of every curve. In this capture the same reader finds 13 full blocks, five
  common profiles and 17 auxiliary pointers. The auxiliary internals remain a
  one-word header because that tool only discovers their strings/floats by a
  conservative heuristic; unstable offsets were not promoted to field names.

The package's 0x80-byte disk header is not represented because `eeMemory.bin`
starts this tree at the already loaded body. PSP and Xbox layouts are likewise
kept out of this PS2 RAM pattern. The `mesh+0x14` pointer remains visible, but
it is not expanded for these vehicle meshes: all embedded 350Z meshes have
`slot_count == 0`; the populated per-block attribute chain currently belongs
to city meshes and would be misleading here.

The vehicle set is small enough that mesh objects, mesh groups, shaders and
locals are enabled by default; each has a top-level switch if a later capture
becomes too large. Skeleton expansion remains off because it adds 250 nodes,
and wheel aliases remain off because they point to records already displayed.

The recursive local list requires `#pragma eval_depth 256` (the ImHex default
of 32 stops before all 41 entries). The pattern passes both the Pattern
Language CLI against the attached 350Z `eeMemory.bin` and `lint_hexpat.py`.

## Reusable probe

```powershell
python mc3_vehicle_ram.py `
  "$MC3_HOSTFS\ASSETS\resources\vehicle\vp_350z_04\vp_350z_04.pck" `
  "$MC3_PCSX2_DATA\sstates\SLUS-21355 (B3FD5361).05\eeMemory.bin" `
  --base 0x01773680 --details
```

`--range 0x80:0x200` prints any PCK file-offset range beside its EE counterpart.
`--json` emits the full machine-readable report. The tool is read-only.
