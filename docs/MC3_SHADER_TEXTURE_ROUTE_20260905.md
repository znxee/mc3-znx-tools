# MC3 PS2 - shader and texture route, rebuilt from retail

MC3 Textures and Shaders · 2026-09-05 · SLUS_213.55 / CRC B3FD5361.

## Result for City MODS and City MODS 2

There is already enough information to build an indexed texture and a basic
city shader without transplanting a car shader. The blocker observed does not
prove that the engine rejects resident textures, nor that a new VU
microprogram is needed. There are independent mistakes in the files and in the
builder.

1. **447/510 dumps in `output/la_tex_pck` have the wrong file name.**
   The internal name at TexturePS2+0x78 (`tNNN`) and `output/la_tex_mapa.tsv`
   identify the right source. After that fix, the **510 palettes, 510
   dimensions and 510 mipmap counts** match their sources.
   `native_texture_map.tsv` gives the corrected association, without renaming
   anything.
2. **This fork's previous PPF has to be replaced.** Comparing by the right
   identity, its 87 8-bpp mipmaps match the engine; of the 878 4-bpp ones, 877
   differ. 507/510 palettes differ too. The rebuilt encoder now reproduces
   **965/965 payloads and 510/510 palettes**, byte for byte.
3. **The builder shares serialised objects that get their constructor run
   several times.** In the snapshots `losangeles_downtown_tex.pck`, `_resid.pck`
   and `_rt2.pck`, there are 9,180 non-null slots for 510 shaders: 8,670 extra
   visits. A second call relocates `shader+0x08` again. Resident does not remove
   this problem; it happens before page recycling even comes into it.
4. **`LIGACAO_RUNTIME` (the runtime binding path) also copies `shader+0x08`
   without rebasing it for the copy.** The path without ORDEM_TEXTURA (texture
   order) does that fixup; the runtime branch does not. So it can point at
   another object or out of the PCK, on top of the sharing above.
5. **Do not rule out the resident route or blame the token for shifted
   fields.** The retail code at 0x2BA830 explicitly accepts resident. It does not
   interpret the incoming token to choose offsets, nor does it request pages.

The new candidate is `candidate/losangeles_midnight_clear.ppf`:

- 36,413,440 bytes; 522 pages; 965 chunks; 510 textures.
- SHA-256 `b2543bdc1ceabba305b707d48837070e8226e69fb6a2593b0967e9bd1ee7eb12`.
- It keeps the previous layout; it only replaces pixels and CLUTs. Table,
  headers, sizes, padding and terminators stay byte for byte the same.
- **It was not installed or tested in game. It requires rebuilding the PCK**
  with the right identity and descriptors, without duplicate constructor visits.
- `fileId=123` in the manifest is the observed profile, not a universal constant.

## Evidence, coverage and limitations

**420 routines** were exported, each one with Hex-Rays pseudocode and R5900
assembly, to `decompiled/XXXXXXXX.c` and `.asm`. `route_index.json` holds
aliases, calls, xrefs, data tables and vtables. Zero Hex-Rays export failures.
A private copy of the IDA database was used; the shared database and runner
were not changed. The set includes helper and boundary functions: 420 does not
mean 420 functions exclusively about shaders.

The pseudocode is not the original source code nor C ready to recompile.
Hex-Rays drops arguments in indirect calls and keeps MMI/GIF instructions as
assembly. For example, at 0x2B3600 the factory's fourth argument is the array's
u16 count, confirmed in `002B3600.asm`, although it is missing from the
decompiled prototype. The names carried over in the TSVs are hints, not ABI
proof.

The texture encoder has an independent test against the game's own output.
The object and streaming route was rebuilt statically; **no fixed run all the
way to the screen was observed in this investigation**. The exact reason for
each old infinite loading cannot be deduced from its duration alone.

## Route map

```text
.tex texture source
  gfxLoadImageAll 526538 -> gfxLoadTexImage 524928 -> ReadPalette 524878
  TexturePS2(name) 2B7088 -> Init 2B7498
    -> normalisation / mips -> index and CLUT conversion -> chunks

.shadert source + parameters
  CityShaderFactory::Create 595DE8
    -> basic / complex / instance / specialised class
    -> Load / template compilation -> state program + parameters

Serialised PCK
  resource loader 430BB8 / 430EA8
  City MapRoot ctor 259160
    -> shader group ctor 2B3600
    -> CityShaderFactory::ResourcePageIn 597E28
       -> Basic 2AFF20 / Complex 2B2A08 / Instance 2B5B58 / subclasses
       -> TextureFactory::ResourcePageIn 2BA718
       -> TexturePS2(resource) 2BA830

Texture used by the drawing
  Touch 2B8C40 / Bind 2B8768 -> PageIn 42CA90 if needed
  PageFile mount 42CCD0 -> Init 42D030
  Page::Load 42B690 -> Streamer::Read 4320E0 -> Worker 431E00
  TexturePS2::Place 2B7F28 -> GIF/DMA upload of pixels and CLUT
  Bind -> TEX1 / CLAMP / TEX0 / MIPTBP1

Shader state
  Complex::Bind 2B2168 -> Pass::Bind 2B1F90 -> Data 2B1BF0
  State::DoFlush 2AD228 -> GS registers / render state
  existing microprogram / VIF geometry -> drawing
```

`0x430BB8` belongs to the PCK resource route, it is **not** the PPF's
datPageFile::Load.

## 1. What is inside a shader

An MC3 shader is a family of C++ objects that controls renderer state,
textures, parameters and passes. The bytes in a PCK are not simply the
`.shadert` text compiled into a GLSL/HLSL shader.

Every shader starts with `u32 token/vtable; u32 flags`. In the City,
`flags & 0x7F` picks the constructor at **0x597E28**:

| Type | Construction at page-in | Main content |
|---|---|---|
| 0 | 2AFF20 | Basic: texture pointer at +0x08; 12 logical bytes |
| 1 | 2B2A08 | Complex: first Pass at +0x08; 48-byte object |
| 2 | 2B5B58 | Instance: parameters after +0x08 and template index in the flags |
| 0x10 | Instance + vtable 635C18 | City window |
| 0x11 | Instance + vtable 635BC0 | City road |
| 0x13 | Complex + vtable 635B68 | City flare class |
| 0x14 | 597FF0 | Animation |

Types outside that list fall into an infinite loop in the City dispatcher.
**It skips null slots**. The generic dispatcher 2AF9E8 and the vehicle one
3B4800 do not handle nulls the same way; do not use one in place of the other
because they look alike.

`0x595DE8` picks classes from the name: default, ped and city_facade can use
Basic; city_window and city_road use specialised classes; other names use
Instance or Complex. The class and the parameters have to agree.

The relevant directory is `ASSETS/shaderlib/city/`, not just `shaderlib/`.
The original `city_facade.shadert` warns that its states are no longer used:
the defaults are in the code. So editing that text does not show that a Basic
loaded from a PCK will change the drawing.

### Basic and object identity

The runtime ctor 2AFAF0 zeroes the texture, and the base ctor 2AEA50 writes the
type. The Basic Load at 2AFC68 gets the texture through the factory; it does
not compile the states of the default.shadert file into that object.
The serialised ctor 2AFF20 installs the vtable and calls the texture factory
with `&shader->texture`, count=1. The writer's alignment may be 16 bytes, but
that does not turn the 12-byte logical header into a new 16-byte class.

Each object walked must get the resource constructor **once**. Duplicating only
the pointer array is not enough. In a real `_tex` example:

```text
serialised shader  = 06802290
shader.texture     = 068022A0
simulated base     = 01000000; delta = -05800000
first visit        = 010022A0  (valid)
second visit       = FB8022A0  (invalid)
```

Sharing a runtime texture that is already constructed is not the same
operation as sharing a serialised object that will be rebuilt repeatedly.
Reference textures (types 2 and 3) exist precisely with other semantics; they
must not be treated as 0xA0-byte TexturePS2.

### Complex: pointer closure and state program

`2B2A58` relocates a Pass: program at +0, Data at +8 and next Pass at +0x1C.
A Pass is 0x20 bytes. `2B2AC8` relocates Data's four pointers: texture array
(+0), value arrays (+4/+8) and auxiliary graph (+0xC). The byte Data+0x10
counts textures; they get the ctor through the factory. `2B2B80` handles the
auxiliary graph's internal pointers. Transplanting only the first header loses
that closure, even if the first texture is right.

In the **Complex**'s already compiled program, 2B1F90 reads:

```text
opcode == FF: end
state = opcode & 3F
value = next byte, or a little-endian u32 if opcode & C0 != 0
stateCurrent[state] = value
updates dirtyMask by comparing stateCommitted[state]
```

This format is not the same intermediate vector as the **Template**'s.
2B4CB8 reads `.shadert`; 2B4BB8 writes opcode + float value pairs, and 2B4C20
handles literal values or `%parameter`. Textures are parameterised commands.
2B4530 evaluates the template; do not interpret every float as a pointer.

| Template operation | Internal ID observed |
|---|---:|
| cull / lighting / fogblend | 0 / 1 / 2 |
| blendset or ps2blend | 3 |
| alphafunc / alpharef | 4 / 5 |
| depthfunc / depthwrite | 6 / 7 |
| basecolor | 8, four components |
| colorwrite / destalphatest | 9 / 10 |
| alphablend / failalpha / smoothshade | 11 / 12 / 13 |
| ps2microcode | 16 |
| nextpass | 32 |
| texcombine / texgen+texsrc | 65 / 66 |
| scales / scalet / slides / slidet | 80 / 81 / 82 / 83 |
| texture %N | 192 |

This is evidence for a compiler, not a complete implementation of one.
Platform conditionals, enum combinations, dynamic graph, draw buckets and
per-pass values have to be preserved. The native parser has error paths that
loop forever, including a template not found at 2B4438.

### Instance and the window/road cases

At 2B3908 / 2B5B58, the flags hold:

```text
bits  0..6  type
bits  7..11 field derived from template+17 (draw bucket)
bits 15..21 template index
bits 22..24 number of parameters (0..7)
bits 25..31 mask of the parameters that are textures
```

Only parameters marked as textures get the texture ctor. The rest are numeric
values. The index depends on the active table `70FB60`; it is not a global
material identifier portable across any resource. Although the field has seven
bits, the Lookup at 2B43C0 walks only 32 entries in the retail implementation
inspected; do not assume 128 templates are available.

City window uses two textures and an RGB tint; City road uses two textures and
two scales. Their `.shadert` keep old commented-out versions and mention a
two-pass optimisation of the basic functionality. Check the drawing code
5964C8 / 596BF0, do not copy a commented-out version of the template.

## 2. TEX source and native encoder

`524928` reads a **14-byte** header: seven u16. +0/+2 are width/height;
`40 00 40 00` means 64x64, not a magic. +4 is the format, +6 the level count.
In this corpus: formats 1/14 are 8 bpp, 15/16 are 4 bpp, the palette followed
by the linear levels. Other loader/direct-colour options were not covered by
the delivered encoder.

`524878` reads **B,G,R,A**, writes **R,G,B,floor(A*scale/255)**.
The initial value of `byte_61C294` in retail is **128**. So 255 becomes 128,
not 127; the intermediate rounding matters too. That does not mean every PS2
alpha in any context must be blindly clamped to 128. This is the conversion
specific to the TEX loader analysed here.

`2B7088` loads the image and normalises proportions/byte limit, rejecting
certain dimensions/formats and creating an 8x8 fallback when it fails. The
existence of an object does not prove the wanted image loaded. In the sample
corrected by identity, all 510 sources kept their dimensions and level count.

`2B7498` picks up to four levels (initial global 615FFC=4, with an extra
minimum-size condition at 616000) and sets up the descriptors from the image
actually loaded. The minimum condition uses the next image and the root image's
height; do not replace it with a generic mip rule without checking the code. Do
not reuse metadata from a texture with another name.

### Indices, transfer and sampling are different things

With swizzle enabled (615FE1=1), without the image bypass flag 0x400000, and a
payload of at least 256 bytes:

| Sampling | Conversion routine | Transfer to the GS |
|---|---|---|
| PSMT8, W×H | 522DB8 | PSMCT32, W/2 × H/2 |
| PSMT4, W=H | 522F00 | PSMCT32, W/2 × H/4 |
| PSMT4, W≠H | 522F00 | PSMCT32, W/4 × H/2 |

The conversion writes into a GS memory representation and reads it back as
PSMCT32. The routines 522990 / 522B60 / 5227F0 and their tables were
reimplemented in **native_texture_codec.py**. For smaller payloads, the
no-swizzle path keeps the sampling dimensions and PSM in the transfer.
The test covers the formats and dimensions of the corpus, not arbitrary
dimensions.

A 256-colour CLUT swaps indices 8..15 with 16..23 in each group of 32:
`j=(i & ~0x18) | ((i>>1)&8) | ((i<<1)&16)`. A 16-colour CLUT does not use that
swap.

## 3. TexturePS2 and chunk layout

TexturePS2 is **0xA0 bytes**, 32-bit little-endian addresses.

| Offset | Field / contract |
|---|---|
| +00 | serialised token / runtime vtable |
| +04 / +06 | class byte / u16 refcount |
| +08 | four 16-byte MipDesc |
| +48 | four 12-byte datChunkRef |
| +78 | optional pointer to the name; relocate or zero it deliberately |
| +7C | upload epoch; Init uses the current epoch XOR 80000000 |
| +80 | u16 CLUT offset in **quadwords from the last mip's payload** |
| +84 | CLUT256: 1 for 8 bpp, 0 for 4 bpp |
| +85 | clamp: source bit0 for U, source bit16 for bit2 V |
| +86 / +87 | mip count / first mip loaded in the GS |
| +88 | TEX1 |
| +90 / +98 | TEX0 / MIPTBP1 caches, rebuilt by Place |

MipDesc:

```text
+0 u16 = log2(uploadW) | log2(uploadH)<<5 | uploadPSM<<10
+2 u16 = runtime TBP
+4 u16 = ceil(pixelBytes / 256)
+6 u16 = blocks from this mip to the last + CLUT blocks
+8 u64 = sampling TEX0, ORIGINAL mip dimensions
```

`native_mip_descriptor_hex` in the candidate manifest provides those 16 bytes
with an initial TBP of zero. Do not confuse the upload PSM/dimensions with
TEX0. The CLUT takes one GS block for 4 bpp or four for 8 bpp, but +80 measures
**qwords**, not those blocks: normally `lastPixelBytes / 16`.

datChunkRef:

```text
+0 u32 ptr
+4 u32 offset inside the page
+8 u32 pageWord = (fileId << 23) | pageIndex
```

Resident: fileId=0 and a valid serialised ptr. Streamed: fileId≠0, ptr may come
as zero and will be resolved by PageIn. `2BA830` zeroes the ptr of streamed
refs; on active resident ones, it adds the delta and writes the chunk's back
pointers. There is no PageIn inside that constructor, nor a read of the old
token to shift the other fields. Keeping tokens coherent is still good hygiene;
changing the token alone does not fix identity, type, size or relocation.

Relevant chunk:

```text
+04 page owner / resident sentinel
+08 pointer to the owning datChunkRef
+0C total aligned size
+14 u16 type metadata
+16 u16 tag
+18 release callback, normally zero
+90 start of the pixels
```

`42AF30` allocates an aligned size; Init asks for `0x100 + pixelBytes + CLUT`
(CLUT only on the last mip). The payload starts at +0x90, so there is slack at
the end. Do not put the image at +0x100 just because that number shows up in
the allocation.

A paged chunk has an owner back pointer. Several independent refs to the same
`(fileId,page,offset)` overwrite that owner and are dangerous during recycling.
That is a second problem, distinct from the duplicate ctor visit.
Reference wrappers can share an already resolved object with other semantics;
do not forbid all image reuse because of this rule.

## 4. PPF, streaming and possible infinite waits

pf05 header: magic +0; u32 pageCount +4; u32 stride +8; u32 entries at +0xC.
The mount 42CCD0 reads 0x3000 bytes of header on the path inspected; your
writer must enforce `12+4*pageCount <= 0x3000` (at most 3069 entries on that
path).

**The entry is a packed u32, not two independent u16:**

```text
offsetBytes = (entry & 0x7FFFF) << 11
readBytes   = ((entry >> 19) & 0x1FFE) << 10
            = (entry >> 20) * 0x800
```

Bit 19 takes no part in these computations. The old two-u16 arrangement
happens to match the small files of this experiment, but loses offset bits
above 128 MiB. A constant read size is not an error by itself.

Measured control: the previous PPF, 522 pages / 965 chunks, no chain error;
vanilla Atlanta, 835 pages / 9041 chunks, no error either. That validates the
structure, not the palette, the swizzle, the descriptors or the PCK that
references it.

`42D030` builds tables and opens a Streamer handle. `42CA90` extracts fileId
and pageIndex, resolves ptr=pageBase+offset, updates chunk+4/+8 and
priorities. There are few range checks in that code; the writer has to provide
them.

`42B690` creates a semaphore, calls **4320E0**, ignores the return value and
waits. 4320E0 returns zero if the handle is not valid or if the 16-request
queue is full. On those paths it does not queue work that would signal that
semaphore. So there is a static mechanism for waiting with no completion.
**It was not measured which of those paths happened in the old tests.**

The worker 431E00 reads in blocks of up to 65 sectors (0x20800 bytes), by CD or
by seek/read of the file backend. When done it signals the semaphore. A short
read, whose return value it does not check here, does not automatically mean
an infinite wait: it can finish and leave incomplete data.

On release, `42BE50` walks through chunk+0xC until size=0, calls the +18
callback if there is one and zeroes the ref pointed to by +8. A valid
terminator and the right owner are the exporter's obligations, not optional
details.

### Minimal instrumentation if it still hangs after the fixes

Log, without changing the behaviour at first:

1. Entry/exit of 597E28 and 2BA830: address, group/slot, type, count and
   pointers before/after. The same instance must not reappear at page-in.
2. 42CCD0/42D030: real `.ppf` name, fileId, count, stride, returned handle.
3. 42B690: fileId/page, packed entry, offset, size, buffer and semaphore.
4. 4320E0: return value, validated handle, queue depth; 431E00: request
   taken, read returned and semaphore signalled.
5. 2B8768/2B7F28: mips, refs, upload descriptor and CLUT offset.

Do not install a patch that forces the semaphore or ignores a failure without
understanding the buffer's lifetime: unblocking the loading may just turn the
wait into use of invalid memory.

## 5. Bind and final upload

`2B8C40 Touch` prioritises the last mip and then the larger ones according to
LOD. `2B8BB8 IsResident` tests the last mip, it does not prove all of them are
ready. `2B8768 Bind` uses the global mode 615FEC, the epoch and possible
proxies/fallbacks; it calls Place through the texture's +0x24 slot. There are
paths that show a fallback without crashing, so "the object exists" and "Bind
returned" do not prove the right texture.

`2B7F28 Place` computes the GS budget from the cumulative blocks, selects
mips, emits BITBLTBUF/TRXREG/TRXDIR transfers and a DMA REF of `chunk+0x90`.
The size comes from uploadW×uploadH×bpp(uploadPSM), not from the texture name.
The CLUT is read from `lastChunk+0x90+16*clutOffset`; a CLUT256 transfer is
16×16×32 bits and CLUT16 is 8×2×32 bits. It updates the caches and the epoch.

Bind emits TEX1 (14), CLAMP (08), TEX0 (06) and MIPTBP1 (34), IDs in hex.
The shader state goes through 2AD228, including ALPHA/TEST and other
combinations. Transparency depends on the CLUT **and** blend/test/colorwrite/VU;
the right alpha alone does not define the look of glass, a window, a decal or a
lens.

## 6. Creation recipe for the City

For LA's first opaque texture, use Basic (type 0), not a car glass shader or an
Instance with a template index inherited from another context.

1. Pin an explicit `MC2 material -> (group,slot) -> TEX name` association.
   Keep the order used in the geometry. Do not associate names with roots by
   sorting heap addresses: the mip/CLUT/name allocation interleaves other
   blocks.
2. Consume the right TEX source, convert BGRA/alpha and indices with the
   delivered codec. Or consume a dump through `native_texture_map.tsv`; never
   trust the current names in `la_tex_pck`.
3. Build descriptors and refs of the **same** texture. In the old `_tex`, 36
   objects have an active mip without a ref precisely because names/metadata
   were mixed.
4. Emit a distinct Basic and TexturePS2 per loader visit. If several groups
   are needed, group/reduce the slots used or study Reference wrappers; do not
   share the raw Basic pointer across the 18 groups.
5. For streamed, give each chunk/ref an exclusive owner. If you duplicate
   objects with independent refs, duplicate their chunks or use a reference
   route that is really supported. The candidate has one set of chunks per
   texture, **not** 18 copies for 18 independent owners.
6. For resident, fix up the pointers against the real base of the source PCK
   and include chunks, payload and name. Never sweep every u32 looking for
   values that look like pointers; floats, UVs and pixels can coincide.
7. Run the structural validator and compare the descriptors with the
   manifest; do a cold test boot with one texture before scaling up. An old
   savestate can keep objects, refs and epochs from another build.

More elaborate shaders can be created using the MC3 grammar and the existing
classes. **Converting the MC2 shader automatically is not required**: the
intent of the MC2 material can be mapped to Basic/CityWindow/CityRoad etc.
To invent a class/effect outside the existing renderer, a complete VU/VIF
contract and the matching patches are still missing - that was not finished
and is not needed to put the city's diffuse texture on screen.

## Files and reproduction

Shared package: `output/shader_texture_route_20260905/` in the toolkit (the
scripts are in `research/city/shader_route_20260904/`).

- `route_index.json` and `decompiled/`: 420 routines and assembly evidence.
- `native_texture_codec.py`: rebuilt 4/8 bpp encoder; `codec_verification.json`.
- `audit_identity.py`, `identity_audit.json`, `native_texture_map.tsv`: the
  right identity of the 510 dumps by `tNNN`, validated by palette bytes.
- `audit_city_graph.py`, `city_graph_audit.json`: slots, ctor visits, pointers
  and chunks. It walks every City slot; the texture closure is limited to
  Basic and discriminates Reference, it is not a universal PCK checker.
- `rebuild_ppf_native.py`, `candidate/`, `candidate_verification.json`: the
  separate PPF candidate. The script refuses to overwrite an existing output.
- `verify_candidate.py`: compares the final PPF and the manifest's 965
  descriptors directly with the native dumps, without using the encoder to
  rebuild the expected result.
- `shader_texture_contract.h`: 32-bit wire layouts with static_asserts; they
  are not native pointers of a 64-bit Windows program.
- `inspect_contract.py` and `texture_contract_audit.json`: the first comparison
  **by external name**, kept as a diagnostic record; its mismatches include
  swapped names. Use `identity_audit.json` for conclusions.
- `package_manifest.json`: hashes of the published files and main sources.

Examples (PowerShell, run in the package folder):

```powershell
python .\audit_identity.py
python .\native_texture_codec.py
python .\audit_city_graph.py --city 'path\city.pck' --ppf 'path\city.ppf'
```

The scripts assume the toolkit's local paths. The IDA runners `run_ida.py`,
`ida_export_route.py` and `ida_export_complete.py` document the extraction in
a private folder; they are not needed to use the codec. The package does not
include the private .i64 database.

No file in the HostFS or in the clean retail extract was changed in this
investigation.
