# The October 2004 alpha's linker map, and what it names in retail

The alpha build in
`$MC3_ALPHA\`
ships `MC.MAP`, the linker map the retail executable does not have. Everything
below is read out of that file by `mc3_alpha_map.py`, or carried across to
retail by `mc3_symbols.py`. Nothing here is inferred from byte patterns.

## Reading the map

The map is columnar and the column a name starts in is what says whether it is
an output section, an input section, an object file or a symbol:

```text
Address  Size     Align Out     In      File    Symbol
col 0    col 9    17    24      32      40      48
```

Two traps, both of which cost measurable accuracy if you parse by regex instead
of by column:

- **`<default>` is not a name.** It appears in the input-section column for an
  output section that has no named input section, and in the file column for an
  anonymous chunk. Taken literally it swallows the whole of `.vutext` and a
  fifth of `.spad`, and the VU microcode disappears from the census.
- **Not everything in the file column is an object.** `COMMON`, `Collect2` and
  the Sony SDK archive members (`libkernl.a(sifcmd.o)`) sit there too. A regex
  that classifies by "does it contain `.obj`" counts 719 of those as symbols.

**The map holds 19556 symbols, not 20275.** The larger figure, which is in the
older fork notes, is the loose-regex count and includes those 719 non-symbols.

| section | symbols | |
|---|---|---|
| `.text` | 14208 | ordinary functions |
| `.gnu.linkonce.t*` | 1948 | one per inline/template function |
| `.data` | 1251 | |
| `.gnu.linkonce.d*` | 1042 | vtables and per-class static data |
| `.bss` | 752 | |
| `.rodata` | 219 | |
| `.spad` | 61 | scratchpad at `0x70000000`, plus linker constants |
| `.vutext` | 38 | VU microcode DMA chains |
| `.DVP.overlay*` | 35 | the VU code itself, paged |

`mc3_alpha_map.py export` writes `alpha_units.tsv`, `alpha_data.tsv`,
`alpha_spad.tsv` and `alpha_vu.tsv`.

## Named globals in retail

`mc3_symbols.py` used to name only functions. The `dados` pass names globals:

```bash
python mc3_symbols.py dados alpha.tsv retail.tsv symbols/nomes*.tsv \
    --out symbols/names_data.tsv
```

There is no new matching machinery. The `tokens` pass has always paired alpha
data with retail data on its way to naming functions - that is how it reaches
the silent `datResource` cluster - and then discarded the pairing. `dados` keeps
it and joins it to the linker map. Both now share `_comatch()`; the refactor was
checked by regenerating the `tokens` output and diffing it byte for byte.

**1013 retail globals named**, in `symbols/names_data.tsv`, which is now a
layer in `CAMADAS`. A further 136 pairings land *inside* a named symbol rather
than on its start; those go to `nomes_dados_interior.tsv` as `<symbol>+0xNN` and
are deliberately **not** a layer - they are only as good as the assumption that
the struct kept its shape across a year of development.

### How far to trust it

Three tests, none of which the matcher could see (it works on reference
topology only and never reads a name):

- **Against other forks' independent measurements.** `0x0070E56C` comes back as
  `rmcTextureFactory::sm_Instance`, which is the global the mcCity `+0x1D4`
  work identified from a savestate. `0x006D5560` comes back as
  `datArgParser::ArgHash`, which is the address the retail arg-parser fills and
  nobody reads.
- **Class agreement**: 77.8% of the judgeable names share a class prefix with a
  function that references them. Most of the remainder are correct anyway -
  `mcMemCard::sForceSave` is referenced by `mcFeMemCard::UpdateSavegameSelect`.
- **Library agreement**: 90.5% of globals belong to the same `.lib` as a
  function that references them, against **4.9-7.6% for the same names shuffled
  among themselves**. The residual is mostly legitimate cross-library use
  (`swfFILE::PlaySound` in `flash.lib`, touched by `mcaudio.lib`).

### Globals worth knowing about

```text
00615B08  mcCullableMgr::s_pCullableMgr        00615D74  rmcDrawable::s_ShaderGroup
00615B0C  mcCullableMgr::s_nDrawPasses         00615D78  rmcDrawable::s_ShaderGroupCount
00615B44  mcCity::s_fCellSize                  00615D80  rmcModel::sm_ForceShader
00618574  mcOccluderSystem::sm_instance        00615E48  rmcShaderFactory::sm_Instance
00618578  mcOccluderSystem::m_version          00615E98  rmcShader::sm_TemplatePath
00619494  mcCityLifeSimulator::ms_poCityLifeSim 00615F98 rmcShader::sm_MicrocodeCount
0061631C  g_VehBoundBoxCullScale               0061C2B8  MainMicrocode
0070E56C  rmcTextureFactory::sm_Instance       0070E574  datPageManager::sm_Instance
0070E500  memMemoryAllocator::sm_Current       006D5560  datArgParser::ArgHash
```

`mcCity::s_fCellSize` sits at `0x00615B44`, four bytes past the city global the
city forks call `dword_615B40`.

## VU microcode

This answers "the microcode has no MPG tag, where do I look". In the alpha it
is a **DMA chain** in its own `.vutext` segment - the upload is built by the
chain and handed to the VIF, so there is no MPG tag stamped in the data to find.
Each program appears twice: as that chain in `.vutext`, and as VU-local code in
a `.DVP.overlay` section paged in `0x800`-byte chunks, bracketed by
`X_CodeStart`/`X_CodeEnd`.

| program | ELF range | ELF size | VU size | object |
|---|---|---|---|---|
| `pointlight_ci` | `005A97B0..005AC5E0` | 2E30 | 2DF0 | `pointlight_ci.obj` |
| `pointlight_dc` | `005AC5E0..005AFA90` | 34B0 | 3468 | `pointlight_dc.obj` |
| `DrawInst` | `005AFA90..005AFE30` | 3A0 | 388 | `drawinst.obj` |
| `shadowvu1` | `005AFE30..005B1480` | 1650 | 1628 | `shadow.obj` |
| `ptxDrawUcode` | `005B1480..005B2AB0` | 1630 | 1608 | `ptxdraw.obj` |
| `occludeTestBound` | `005B2AB0..005B2E10` | 360 | 348 | `occludeTestBound.obj` |
| `noise_cn_lighttexgen_dc` | `005B2E10..005B5EA0` | 3090 | 3048 | |
| `noise_cn_pointlight_dc` | `005B5EA0..005B8FD0` | 3130 | 30E8 | |
| `noise_pointlight_dc` | `005B8FD0..005BC000` | 3030 | 2FF0 | |
| `skinned_fresnel` | `005BC000..005BE110` | 2110 | 20D8 | |
| `texgen_fresnel_dc` | `005BE110..005C0190` | 2080 | 2048 | |
| `carpaint_3pass_dc` | `005C0190..005C2580` | 23F0 | 23B8 | |
| `city_reflect_ci` | `005C2580..005C52E0` | 2D60 | 2D20 | |
| `cpv_distort_vtx_dc` | `005C52E0..005C8310` | 3030 | 2FF0 | |
| `distort_dc` | `005C8310..005CA420` | 2110 | 20D8 | |
| `light_texgen_dc` | `005CA420..005CC4E0` | 20C0 | 2088 | |
| `rv1_code_main` | `005CC4E0..005CDA90` | 15B0 | - | `main.obj` |
| `rv1_code` | `005CDA90..005CFA30` | 1FA0 | - | `rv1.obj` |
| `rv1_code_drawptx` | `005CFA30..005CFE60` | 430 | - | `drawptx.obj` |

Three addresses are named inside the microprograms, VU-local:
`occludeTestBound+0000 OccludeTestSphere`, `occludeTestBound+0120 vu0_sincos`,
`rv1_code+1F80 rv1_BindNextState`.

### Carried to retail

**What was already known before this note, and is not a finding here:** that the
retail executable is not stripped and keeps `.vutext`, `.DVP.ovlytab` and 97
overlay sections; that there are 21 microprograms; that `ovlystrtab` holds
section names rather than program names; that `mcTextureFactory::mcTextureFactory`
(`0x3B5750`) registers twelve of them by name; and that `mc3_vu.py` already
disassembles all of it, 21791 instructions of 21791 in both pipes. Use
`mc3_vu.py` for anything to do with reading the code - `--disasm ID`,
`--coverage`, `--dump DIR`.

**What this adds is the other nine names.** Those nine have no registration
function because they are not shader microcode: each is uploaded by whichever
subsystem owns it. `python mc3_alpha_map.py vuretail` reaches them, and the
names are now in `mc3_vu.py`'s own table, so its listing comes out 21 of 21.

The route works because three things line up:

1. **The overlay id is stable across builds.** Section names are
   `.DVP.overlay..<vma>.<id>.<line>.<page>`, and every one of the alpha's 19 ids
   is present in retail, which has 21. The sections are `SHT_NOBITS` - they
   reserve VU instruction memory and carry no bytes - and their total size per
   id is the program's VU code size. Against the alpha's `_CodeStart`/`_CodeEnd`
   that matches **16 of 16** where the map gives a size.
2. **`.vutext` is walkable.** Each chained program opens with a source-chain DMA
   tag `0x6000xxxx` whose low 16 bits are the qword count of the rest, so one
   read gives the length and the next tag's position. Verified on the alpha,
   where MC.MAP gives the true boundaries: **16 of 16 exact**. The remaining
   `rv1_*` blocks are not chains and sit at the tail.
3. **The chain size checks against the overlay size.** The difference is always
   `0x10 + 8 x pages` - one 16-byte DMA tag for the program, plus 8 bytes of
   VIFcode per MPG packet, each packet carrying one 0x800 page of 256
   instructions. That is why the pages are 0x800.

The walk accounts for the whole segment: `005E9610..006143E0`, 21 programs, zero
bytes left over.

| retail range | size | program |
|---|---|---|
| `005E9610..005EC4F0` | 2EE0 | `pointlight_ci` |
| `005EC4F0..005EF9A0` | 34B0 | `pointlight_dc` |
| `005EF9A0..005EFD40` | 3A0 | `DrawInst` |
| `005EFD40..005F1390` | 1650 | `shadowvu1` |
| `005F1390..005F29C0` | 1630 | `ptxDrawUcode` |
| `005F29C0..005F2D10` | 350 | `occludeTestBound` |
| `005F2D10..005F5DA0` | 3090 | `noise_cn_lighttexgen_dc` |
| `005F5DA0..005F8ED0` | 3130 | `noise_cn_pointlight_dc` |
| `005F8ED0..005FBF00` | 3030 | `noise_pointlight_dc` |
| `005FBF00..005FE010` | 2110 | `skinned_fresnel` |
| `005FE010..00600210` | 2200 | **`specular2_dc`** - new in retail |
| `00600210..00602290` | 2080 | `texgen_fresnel_dc` |
| `00602290..00604640` | 23B0 | **`carbon_fiber_dc`** - new in retail |
| `00604640..00606B00` | 24C0 | `carpaint_3pass_dc` |
| `00606B00..00609860` | 2D60 | `city_reflect_ci` |
| `00609860..0060C890` | 3030 | `cpv_distort_vtx_dc` |
| `0060C890..0060E9A0` | 2110 | `distort_dc` |
| `0060E9A0..00610A60` | 20C0 | `light_texgen_dc` |
| `00610A60..00612010` | 15B0 | `rv1_code_main` |
| `00612010..00613FB0` | 1FA0 | `rv1_code` |
| `00613FB0..006143E0` | 430 | `rv1_code_drawptx` |

All 21 are in `symbols/names_manual.tsv` with a `vu_` prefix, so
`mc3_symbols.py busca city_reflect` finds them.

### The names come from retail, not from the alpha

The overlay-id route above is only half of it. `mcTextureFactory::mcTextureFactory`
(`0x003B5750`) registers the shader microcode, and the name and the address are
the two arguments of one call:

```asm
003b599c  li   $a0, aCarbonFiberDc   # "carbon_fiber_dc"
003b59a0  jal  rmcShader__AddMicrocode_char_const__ptr__unsigned_int_const__ptr
003b59a4  li   $a1, dword_602290     # the DMA chain, in the delay slot
```

That names 12 of the 21 out of retail's own `.rodata`, with no reference to the
alpha at all - and it **agrees with the overlay-id route on all 10 they share,
with zero disagreements.** Two independent routes, same answer.

It is also what names the two programs the alpha does not have. Retail's
microcode string table has exactly two entries the alpha's lacks,
`carbon_fiber_dc` and `specular2_dc`, and the call sites say which chain each
one is - the answer is not the order you would guess, since `specular2_dc` comes
first in `.vutext` and second in the string table.

The nine that `AddMicrocode` does not register are named by the overlay id, and
each one's consumer agrees with the name: `occludeTestBound` is referenced by
`mcOccluderSystem::PrepOccluders_VU0`, `DrawInst` by `mcInstBucket::Draw`,
`ptxDrawUcode` by `ptxMicrocode`, `rv1_code` by `lowPsxGfx::DoMicrocode` and by
`MainMicrocode` - the global at `0x0061C2B8` that the `dados` layer named.
`shadowvu1` is the only one with no direct xref.

## Scratchpad

The full SPR layout, from `mc3_alpha_map.py spad`:

```text
70000000  30  rmcState::sm_World          70000270  40  gfxState::sm_World
70000030  30  rmcState::sm_Camera         700002B0  40  gfxState::sm_Camera
70000060  48  rmcState::sm_CurrentStates  700002F0  40  gfxState::sm_View
700000A8  48  rmcState::sm_FlushedStates  70000330  40  gfxState::sm_Modelview
700000F0   4  rmcState::sm_Dirty          70000370  40  gfxState::sm_Composite
700000F8   C  rmcState::sm_CurrentTexStates 700003B0 40 gfxState::sm_FullComposite
70000108   C  rmcState::sm_FlushedTexStates 700003F0  4 gfxState::sm_Texture
70000114   4  rmcState::sm_TexDirty       700003F4   4  gfxState::sm_Material
70000118   1  rmcState::sm_LightMatrixDirty 700003F8 1  gfxState::sm_CompositeDirty
7000011C   4  rmcState::sm_TexMatrixIdentity
70000120  18  ptxS/ptxT/ptxC/ptxCpv/ptxTex/ptxVtx
70000140 100  sm_ScratchPadFastCullMtx    700003FC  2C  vgl* (12 words)
70000240   1  g_bEnvmapAvailable          70000430  80  gfxViewport::sm_FastCullMtx
70000244   4  MCOCCLUDER                  700004B0 A04  unused
70000248   4  gp_AsyncVU0_OccludeResult   70000EB4   -  _spadend
7000024C  14  lowPsxGfx::sm_Head/Stop/Base/Other/InFrame
70000260   4  gfxStaticGeneration
```

Memory layout constants carried in the same section:

```text
_mem_bottom  00100000    _stack       00180000    _stack_size  00020000
_mem_top     02000000    _mfifo_start 00100000    _mfifo_size  00080000
_rostart     005EE37C    _roend       006283C4    _gp          006303F0
_end         006B73EC    _heap_size   FFFFFFFF
```

## The `.pck` schema, read out of the game's own constructors

`mc3_schema.py`. A `.pck` is a serialized object graph and the code that reads
it back is each class's `X::X(datResource&)` constructor, so that constructor is
the specification. Two shapes carry all of it:

```c
if (field) field += base;                       // that offset is a POINTER
for (i < *(u16*)(this+K)) Ctor(ptr + i*S, res)  // K is a COUNT driving an
                                                // array of stride S
```

A field the constructor never touches is dead weight in the file. A field it
does relocate must hold a real offset. And a count written too low is worse than
a crash: the walk simply stops early, the rest of the array keeps raw file
offsets, and the geometry draws from whatever sits at that address in low
memory.

```bash
python mc3_schema.py dump      # decompile every ctor (needs IDA)
python mc3_schema.py extract   # -> output/alpha2004/schema.tsv
python mc3_schema.py show mcHood
python mc3_schema.py selftest
```

**226 constructors, 1058 fields.** 172 stamp a vtable, 140 build children, 122
relocate pointers, 38 have u16 counts, 15 have 32-bit counts, 97 read a field
they never relocate, and 30 are leaves.

The alpha is what makes it possible: without it these are 226 anonymous
`sub_XXXXXX` and there is no way to say which type each one serializes.

### Two parser bugs found by checking against the source

Both were caught by reading the decompiled text of a type whose contract had
been worked out by hand, and both would have shipped silently:

- **The vtable is not always at +0.** `mcCullable` stamps its at `+0x10`,
  `mcCityModel` at `+0x10`, `mcCityModelType` at `+0x28`. A pattern anchored at
  offset zero reports "no vtable" for all of them.
- **Counts are not always u16.** `mcHood` counts its models with 32-bit fields.
  A parser that only knew the u16 shape said it had no counts at all, while it
  has two.

`selftest` now pins both shapes against contracts derived by hand, so a future
change to the parser that loses them fails loudly.

### What it confirms, independently

The vtable addresses come out matching what the savestate work found by a
completely different route, and the offsets are new:

| type | vtable | at offset |
|---|---|---|
| `mcCityModel` | `0x006250C0` | **+0x10** |
| `mcInstCityModel` | `0x006251B8` | **+0x10** |
| `mcCityModelType` | `0x00625098` | **+0x28** |
| `rmcModelGeom` | `0x00626D98` | +0x00 |
| `rmcShaderGroup` | `0x00626F20` | +0x00 |
| `mcCity` | `0x00625228` | +0x00 |

`mcCity+0x0C` comes out as a relocated pointer whose children are
`rmcShaderGroup` at **stride 16** - the shader-group array, matching the
16-byte `{vtable, array, u16, u16}` layout measured from RAM.

### The mesh contract, and the stacking bug

```text
rmcModelGeom
  +0x00  vtable   stamped 0x00626D98 - the file value is ignored
  +0x08  u16      NUMBER OF MATERIAL GROUPS
  +0x0A  u16      number of rmcModelCpv
  +0x10  ptr  ->  array of <+0x08> rmcGeometry, 8 bytes each   [+= base if != 0]
  +0x14  ptr  ->  array of <+0x0A> rmcModelCpv,  4 bytes each   [+= base if != 0]

rmcGeometry (8 bytes)
  +0x00  ptr  ->  array of <+0x04> packet descriptors, 8 bytes  [+= base if != 0]
  +0x04  u16      packet count

mcHood
  +0x00  ptr      [+= base if != 0]
  +0x04  count32  number of mcCityModel
  +0x08  ptr  ->  array of mcCityModel
  +0x0C  count32  number of mcInstCityModel
  +0x10  ptr  ->  array of mcInstCityModel
```

The relocation walk is driven **entirely by those counts**. With `+0x08 = 1` and
15 real groups, only group 0 gets its packet-array pointer fixed; groups 1..14
keep raw file offsets, and a file offset read as an EE address lands in low
memory. That is the mechanical cause of the reported "objects piled into a
narrow band", and it is a file-level check: `mesh+0x08` must equal the group
table's own prefix.

## The source tree

888 translation units in 90 directories, split between the shared engine and the
game: `c:\soft\age\src\` (Angel Game Engine - `gfx`, `rmcore`, `physics`,
`phbound`, `mesh`, `data`, `net2`, `veh_dyna`, ...) and `c:\soft\mc3\src\`
(`mccar`, `mcmenu`, `mcai`, `mchud`, `mctraffic`, `mclevel`, `mcprop`, ...).
`mc3_alpha_map.py tree` prints it; `units` groups symbols by translation unit.

## What was tried and did not work

Recorded so nobody pays for it twice.

- **Stopping the neighbour pass at translation-unit boundaries.** The reasoning
  was sound - the pass walks outward from an anchor naming whatever sits beside
  it, and the "compiler emits methods in source order" argument does not span
  files, so a run that crosses into the next `.cpp` is unjustified. Measured
  against ground truth (the string layer held out of the anchors and used as the
  answer key): **99.4% correct without the boundary, 99.5% with it**, at a cost
  of 1102 names. It buys nothing. `neighbours --units` still offers it; the
  default is unchanged.
- **Using shared data references as a correctness test.** Tempting, because it
  judges the functions that cite no strings, which is most of them. It is not a
  correctness test: run against the string layer - the most reliable one here,
  independently verified 19 of 20 against other forks' measurements - it reports
  a 30.2% "error rate". What it actually measures is how much a function's
  global references changed between the two builds, and it is circular for the
  `token` layer, which was built from exactly that signal.

The useful by-product of the first item: **the neighbour layer, the one everyone
including this fork assumed was the weakest, is 99.4% accurate on held-out
ground truth.**
