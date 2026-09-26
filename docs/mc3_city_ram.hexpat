#pragma description Midnight Club 3 (PS2) - LIVE city in RAM (eeMemory.bin)
#pragma endian little
#pragma pattern_limit 8000000
#pragma array_limit 200000
#pragma eval_depth 256

import std.core;
import std.mem;
import std.string;

// =====================================================================
// WHY READ THE RAM AND NOT THE .pck
// =====================================================================
//
// In the file, the class field holds a TOKEN - a number like 0x0079FF88 that
// the loader swaps for the real vtable. A token cannot be resolved: it is not
// in the ELF, and each city uses a different set (tokyo uses the same ones as
// atlanta shifted by +0x488). In RAM the tokens have already become vtables,
// the pointers are already relocated, and the file offset IS the EE address.
//
// Open `eeMemory.bin` from a savestate WITH the city loaded.
//
//     offset in eeMemory.bin  ==  EE address  (0x00000000 .. 0x01FFFFFF)
//
// The root comes by itself from the global pointer at 0x00615B40. To pin
// another city by hand, replace MCCITY below with a literal - San Diego, for
// example, lands at 0x007E5B80.
//
// Companion: mc3_city.hexpat, which reads the .pck on disk.
// =====================================================================

u32 MCCITY = std::mem::read_unsigned(0x00615B40, 4);

// =====================================================================
// WHICH SAVESTATE EACH NUMBER COMES FROM
// =====================================================================
//
// The numbers in this file come from two different dumps and THEY ARE NOT THE
// SAME CITY. You can tell which one without relying on the file name, because
// the grid and the group count are a signature:
//
//     atlanta   3968 cells (64x62)     18 groups of 1329 slots
//     sd        4473 cells (71x63)     15 groups of 1516 slots
//     tokyo     6210 (90x69)           detroit   7200 (96x75)
//
// Checked against the retail .pck files themselves, not deduced.
//
// Where the note does not say which one it came from, it is because the
// measurement is the same in both - the texture header, the class sizes, the
// block arithmetic. Where it does say, the number IS that city's and does not
// carry over: atlanta has 1151 mcShaderCityWindow and sd has NONE; the city
// texture array has 4 filled slots in atlanta and 11 in sd.
//
// EVERYTHING ON. It fits within the pragma limits, but it is not free - the
// real cost, measured on ATLANTA (see the "WHICH SAVESTATE" note just above):
//
//   EXPAND_SHADERS    18 groups x 1329 slots = 23922, of which 4105 are filled.
//                     Those 4105 reach 5256 distinct textures. In sd it is
//                     15 x 1516 = 22740 with 4020 filled, the same order.
//   EXPAND_PIXELS     and here is the weight: of those 5256, 377 levels are
//                     resident and add up to 1.68 MILLION bytes. With both on
//                     that becomes ~1.7 million patterns of pixels alone,
//                     against ~150 thousand for the whole rest of the shader
//                     path.
//
// So: if ImHex gets sluggish, the switch to turn off FIRST is EXPAND_PIXELS,
// not EXPAND_SHADERS. Turning the pixels off drops nine tenths of the cost and
// you still see shader, texture, mip and CLUT - you only miss the index data.
bool EXPAND_COMPONENTS = true;
bool EXPAND_INSTANCES  = true;    // up to 1559 per hood in San Diego
bool EXPAND_MESHES     = true;
// OFF by default on purpose: the one-byte-per-vertex vectors are 37023
// arrays and 1.03 MB, and on their own they blow the 8 M pattern_limit. Turn
// on when looking at the +0x14 chain, preferably with EXPAND_PIXELS off.
bool EXPAND_VERTEX_BYTES = false;
bool EXPAND_SHADERS    = true;    // 18 groups x 1329 slots, 4105 filled
bool EXPAND_CELLS      = true;    // 4473 cells in San Diego
bool EXPAND_PIXELS     = true;    // the header and the pixels of each level

fn ptr(u128 a) {
    if (a < 0x00100000) {
        return 0;
    }
    if (a > 0x01FFFFFF) {
        return 0;
    }
    return a;
};

fn fmt_ptr(u32 a) {
    if (a == 0) {
        return "null";
    }
    return std::format("{:08X}", a);
};

// =====================================================================
// THE CLASSES, TAKEN FROM THE VTABLE
// =====================================================================
//
// These are not guesses. The vtable in RAM points into the ELF, and `symbols/`
// has 10946 named addresses coming from the MC.MAP of the October 2004 alpha
// build. mc3_vtables.py does vtable -> method -> name. The addresses below are
// from the retail SLUS-213.55 ELF and held the same in atlanta and San Diego.
//
// What each class gave that is useful:
//
//   mcCityModel      +0x18 Render(mcPassTypes)     the "passes" are the groups
//                    +0x10 mcCullable::GetCullCenter
//                    +0x1C mcCullable::CalcLOD     picks among the 10 slots
//                    +0x28 InitExtents(Vector3 const&, Vector3 const&)
//   rmcModelGeom     Draw / DrawCpv / DrawSkinned / Load(mshMesh&, bool, char*)
//   rmcTextureReference  GetName, Download, GetDownloadSize, Bind,
//                    GetWidth, GetHeight, GetMipMapCount, SetClamp, SetFilter
//   mcShaderCityWindow   the lit windows of the buildings
//   mcShaderTexAnimation animated texture

fn vt(u32 v) {
    if (v == 0x00625228) { return "parFileIO  (city root)"; }
    if (v == 0x00625268) { return "mcSkyHatClass"; }
    if (v == 0x00625238) { return "parFileIO  (mcSkyHatClass)"; }
    if (v == 0x006252A8) { return "mcStarField"; }
    if (v == 0x006250F0) { return "mcCityModelClass"; }
    if (v == 0x006251E8) { return "mcInstCityModelClass"; }
    if (v == 0x00625098) { return "mcCityModelType"; }
    if (v == 0x00625190) { return "mcInstCityModelType"; }
    if (v == 0x006250C0) { return "mcCityModel"; }
    if (v == 0x006251B8) { return "mcInstCityModel"; }
    if (v == 0x00626D98) { return "rmcModelGeom"; }
    if (v == 0x00627280) { return "rmcTexturePS2"; }
    if (v == 0x00627040) { return "rmcTextureReference"; }
    if (v == 0x00626FC8) { return "rmcDictionaryReference"; }
    if (v == 0x00626E70) { return "rmcShaderBasic"; }
    if (v == 0x00626EC8) { return "rmcShaderComplex"; }
    if (v == 0x00626F30) { return "rmcShaderInstance"; }
    if (v == 0x00626F20) { return "rmcShaderGroup"; }
    if (v == 0x00635BC0) { return "rmcShaderInstance (2)"; }
    if (v == 0x00635C18) { return "mcShaderCityWindow"; }
    if (v == 0x00635B10) { return "mcShaderTexAnimation"; }
    if (v == 0) { return "null"; }
    return std::format("vtable {:08X} (unknown)", v);
};

// =====================================================================
// TILE BOX
// =====================================================================
//
//   mcCity::CalcAbsCellXZ    0x257CC8  floor(x/40), floor(z/40)
//   mcCity::CalcRelCellXZ    0x257C60  minus the origin at +0x274/+0x278
//   mcCity::CellIndexFromPos 0x257BE8  rel_z * W + rel_x, and -1 off the grid
//
// Off the grid the object is simply NOT inserted (CellGrid::Insert 0x259658
// returns without doing anything). It vanishes with no crash and no warning.

struct TileBox {
    u8 x0;
    u8 x1;
    u8 z0;
    u8 z1;
} [[static]];

// =====================================================================
// rmcModelGeom - the mesh
// =====================================================================

struct BlockEntry {
    u32 packet     [[format("fmt_ptr")]];
    u16 qwc;                    // packet = qwc * 16 bytes
    u16 vertex_count;
};

struct GroupEntry {
    u32 list       [[format("fmt_ptr")]];
    u16 count;                  // how many BlockEntry
    u16 pad;

    if (EXPAND_MESHES && list != 0 && count > 0 && count <= 4096) {
        BlockEntry blocks[count] @ ptr(list);
    }
};

// ---------------------------------------------------------------------
// +0x14: TWO LEVELS DOWN TO ONE BYTE PER VERTEX
//
// This field, which I used to call `per_block`, is the root of 1.0 MB of the
// pck - the largest block still unmapped. The chain:
//
//     mesh+0x14 -> u32 slot[slot_count]                (cookie = slot_count)
//       slot[k] -> { counts, vectors }[group_count]    (cookie = group_count)
//          counts  -> { u32 nblocks, u32 vertices[nblocks] }
//          vectors -> u32 per block
//                       each one -> u8 data[vertices[j]], aligned to 16
//
// CHECKED on sd_dusk_clear.pck, without a single exception in any test:
//   1327/1327   level 1 cookie == slot_count  (mesh+0x0A)
//   5411/5411   level 2 cookie == group_count (mesh+0x08)
//   24396/24396 the `counts` record reproduces EXACTLY the vertex_count of
//               that group's BlockEntry, in the same order
//   12627/12627 consecutive vectors sit "vertices rounded up to 16" apart
//
// That is 37023 vectors and 1,033,150 bytes of payload. The values go from
// 0x00 to 0xE7 and 226 of the 256 show up.
//
// WHAT THE BYTE MEANS is a hypothesis, not a measurement: MapRoot carries two
// 256-colour palettes (+0x290 live and +0x1290 source, all 256 entries filled
// in both), and a per-vertex index into a 256 palette is exactly how vertex
// lighting is baked. `slot_count` would then be the number of variants. I did
// not find the consumer in the ELF; until I do, this stays a likely reading
// and not a fact.
//
// THIS REPLACES what I wrote here before. The earlier version said mesh+0x14
// pointed straight at a byte vector; it was one level short and the "3388 of
// 3388" only measured the one-block-per-group case.
// ---------------------------------------------------------------------

struct CountsPerBlock {
    u32 nblocks;
    u32 vertices[nblocks];      // equal to the BlockEntry vertex_count values
};

struct ByteVector {
    u32 address [[format("fmt_ptr")]];
    if (EXPAND_VERTEX_BYTES && address != 0) {
        u8 index[parent.counts_rec.vertices[std::core::array_index()]] @ ptr(address);
    }
};

struct EntryPerGroup {
    u32 counts  [[format("fmt_ptr")]];
    u32 vectors [[format("fmt_ptr")]];

    if (EXPAND_MESHES && counts != 0) {
        CountsPerBlock counts_rec @ ptr(counts);
        if (vectors != 0 && counts_rec.nblocks > 0 && counts_rec.nblocks <= 4096) {
            ByteVector vector[counts_rec.nblocks] @ ptr(vectors);
        }
    }
};

struct VertexSlot {
    u32 address [[format("fmt_ptr")]];
    if (EXPAND_MESHES && address != 0) {
        EntryPerGroup group[parent.group_count] @ ptr(address);
    }
};

struct Mesh {
    u32 vtable [[format("vt")]];
    u32 unk_04;
    u16 group_count;                        // zeroing it erases the mesh
    u16 slot_count;
    u32 material_table [[format("fmt_ptr")]];   // u16[group_count], indexes the
                                                // rmcShaderGroup array
    u32 group_table    [[format("fmt_ptr")]];
    u32 per_block      [[format("fmt_ptr")]];   // -> VertexSlot[slot_count]
    // +18 and +1C are a PAIR of pointers to two records {1, size, data} of
    // EQUAL size to each other. Only 31 of the 1756 meshes of
    // sd_dusk_clear.pck have that pair, and the size matches neither groups,
    // blocks nor vertices - still unexplained.
    u32 pair_18 [[format("fmt_ptr")]];
    u32 pair_1c [[format("fmt_ptr")]];

    if (EXPAND_MESHES && group_count > 0 && group_count <= 64) {
        u16        material_index[group_count] @ ptr(material_table);
        GroupEntry groups[group_count]         @ ptr(group_table);
    }
    if (EXPAND_MESHES && per_block != 0 && slot_count > 0 && slot_count <= 4096) {
        VertexSlot slot[slot_count] @ ptr(per_block);
    }
};

// =====================================================================
// mcCityModelType / mcInstCityModelType
// =====================================================================

struct ComponentData {
    u32   unk_00;
    u32   unk_04 [[format("fmt_ptr")]];
    u32   unk_08 [[format("fmt_ptr")]];
    u32   chain_next [[format("fmt_ptr")]];   // +0x0C  chain walked by
                                              // CullableTypeFixup (0x24AE58)
    u32   unk_10 [[format("fmt_ptr")]];
    u32   unk_14;
    u32   owner_array [[format("fmt_ptr")]];  // +0x18
    u8    unk_1c[0x0C];
    u32   type_vtable [[format("vt")]];       // +0x28
    float centre_x;                           // +0x2C sphere centre, world
    float centre_y;
    float centre_z;
    float radius;                             // +0x38
    u32   lod[10] [[format("fmt_ptr")]];      // +0x3C  one Mesh per level;
                                              // an empty slot in the middle is normal
    u32   unk_64 [[format("fmt_ptr")]];
    u32   unk_68 [[format("fmt_ptr")]];
    u32   unk_6c [[format("fmt_ptr")]];
    char  name[20];                           // +0x70
    u32   unk_84;
    u32   unk_88;
    u32   unk_8c;

    if (EXPAND_MESHES && lod[0] != 0) {
        Mesh lod0 @ ptr(lod[0]);
    }
};

// =====================================================================
// mcCityModel (28 bytes)  and  mcInstCityModel (96 bytes)
// =====================================================================

// The u32 at +0x08 is NOT an opaque flags word. It is four bytes, and two of
// them drive the drawing:
//
//   +0x0A  shader group index. mcCityModelType::Render (0x0024C380) does
//          lbu a1, 0xa(obj); sll a1, a1, 4; addu a1, mcCity->0x0C, a1.
//          Measured: 0..17 in the three 18-group cities, never above.
//   +0x0B  pass mask (mcPassTypes). The draw loop does
//          lbu v0, 0xb(obj); and v0, v0, <requested pass>; if zero, skip.
//          Values seen: 8, 64, 2, 10, 72, 12, 74, 4, 66, 14 - bits 1,2,3,6.

struct CityModel {
    u32     next [[format("fmt_ptr")]];
    u32     prev [[format("fmt_ptr")]];
    u8      unk_08;
    u8      unk_09;
    u8      shader_group;                 // +0x0A index into mcCity+0x0C
    u8      pass_mask;                    // +0x0B mcPassTypes
    u32     data [[format("fmt_ptr")]];
    u32     vtable [[format("vt")]];
    TileBox tile;
    float   radius;

    if (EXPAND_COMPONENTS) {
        ComponentData cd @ ptr(data);
    }
};

// Instances are chained BY TYPE: +0x00 points to another one of the SAME type
// and +0x04 is the previous one, with 0 at the head. Measured on atlanta: 3377
// of 3608 chained, 231 chains, none crossing types. Mixing types in one chain
// leaves the game loading forever.
struct InstCityModel {
    u32     next [[format("fmt_ptr")]];
    u32     prev [[format("fmt_ptr")]];
    u8      unk_08;
    u8      unk_09;
    u8      shader_group;                 // +0x0A index into mcCity+0x0C
    u8      pass_mask;                    // +0x0B mcPassTypes
    u32     data [[format("fmt_ptr")]];
    u32     vtable [[format("vt")]];
    TileBox tile;
    float   radius;
    u32     chain_index;                  // +0x1C position in the chain
    float   matrix[12];                   // +0x20 Matrix34, row by row
    float   sphere_centre[3];             // +0x50
    u32     pad_5c;

    if (EXPAND_INSTANCES) {
        ComponentData cd @ ptr(data);
    }
};

// =====================================================================
// HOOD - it is the neighbourhood's name AND its texture
// =====================================================================
//
// The block pointed to by hood+0x00 is not a neighbourhood descriptor: it is
// an rmcTexturePS2 (vtable 00627280) with the short name at the start. In San
// Diego all six are rmcTexturePS2; in atlanta one of them is
// rmcTextureReference. So each neighbourhood carries its own atlas.

// =====================================================================
// rmcTexturePS2  -  the texture header, decoded through the accessors
// =====================================================================
//
// The small methods of vtable 00627280 say where each thing lives, and they
// are short enough to leave no doubt:
//
//   GetName        005BDD60   return this->0x78;
//   GetWidth       005BDD68   ld this->0x10; >>26 & 0xF; 1 << that
//   GetHeight      005BDD88   ld this->0x10; >>30 & 0xF; 1 << that
//   GetMipMapCount 005BDDA8   return byte this->0x86;
//   GetClamp       002B8F40   byte this->0x85, bit 0 = U, bit 2 = V
//   SetClamp       002B8F30   writes the same byte
//   GetFilter      002B8FE8   ld this->0x88; bits 6..8  (MMIN)
//
// The 64-bit `ld` at +0x10 with TW at 26..29 and TH at 30..33 identifies the
// field without ambiguity: it is the GS TEX0 REGISTER. And the one at +0x88,
// with the minification filter at 6..8, is TEX1.
//
//   +0x10  TEX0 (u64)  TBP0 0..13, TBW 14..19, PSM 20..24, TW 26..29,
//                      TH 30..33, TCC 34, TFX 35..36, CBP 37..50,
//                      CPSM 51..54, CSM 55, CSA 56..60, CLD 61..63
//   +0x78  const char* name  (0 when the texture is anonymous)
//   +0x85  byte  clamp: bit 0 = U, bit 2 = V
//   +0x86  byte  mip count
//   +0x88  TEX1 (u64)  MMIN at 6..8
//
// CHECKED on San Diego's six hood textures: TEX0 decodes to 64x64 and
// 128x128, PSM 0x13 (PSMT8), 3 mips - sane values, and the same decoder applied
// to the mcCity+0x1D4 texture gives 256x256 PSMT8.

fn fmt_tex1(u128 v) {
    u128 id = (v >> 21) & 0x7FF;
    if (id == 0) {
        return std::format("TEX1 {:016X}  no proxy", v);
    }
    return std::format("TEX1 {:016X}  proxy id {} (index {})", v, id, id - 1);
};

fn level_bytes(u128 tex0) {
    u128 w = 1 << ((tex0 >> 26) & 0xF);
    u128 h = 1 << ((tex0 >> 30) & 0xF);
    u128 psm = (tex0 >> 20) & 0x1F;
    if (psm == 0x14) {
        return w * h / 2;
    }
    if (psm == 0x13) {
        return w * h;
    }
    return 0;
};

fn clut_entries(u128 tex0) {
    u128 psm = (tex0 >> 20) & 0x1F;
    if (psm == 0x14) {
        return 16;
    }
    if (psm == 0x13) {
        return 256;
    }
    return 0;
};

fn fmt_tex0(u128 v) {
    return std::format("{}x{} PSM {:02X} TBP0 {} TBW {} CBP {}",
        1 << ((v >> 26) & 0xF), 1 << ((v >> 30) & 0xF), (v >> 20) & 0x1F,
        v & 0x3FFF, (v >> 14) & 0x3F, (v >> 37) & 0x3FFF);
};

//
// +0x04 IS THE TEXTURE CLASS DISCRIMINATOR, with four values and one
// constructor per value. rmcTextureFactoryPS2::ResourcePageIn (0x002BA718)
// loads s5=1, s4=2, s3=3 and dispatches:
//
//     +0x04 == 0  ->  rmcTexturePS2::rmcTexturePS2(datResource&)           0x2BA830
//     +0x04 == 1  ->  no constructor, the slot is skipped
//     +0x04 == 2  ->  rmcTextureReference::rmcTextureReference(...)        0x2B6E10
//     +0x04 == 3  ->  rmcDictionaryReference::rmcDictionaryReference(...)  0x2B6EC8
//
// and rmcTextureReference::rmcTextureReference also reads the byte and treats
// 3 separately. It is the same vocabulary mc3_city_build already tested in the
// shader sub-object (`d[o+0x14] in (0,1,2,3)`) without knowing what it was.
//
// CENSUS IN SAN DIEGO, following shader+0x08 in the 15 groups: 4020 distinct
// objects, of which 2379 rmcTexturePS2 (+0x04 = 0) and 1621
// rmcTextureReference (+0x04 = 2); 20 dropped because in some shader classes
// +0x08 is not a texture. Of the type-0 ones, 753 distinct proxy ids for 848
// entries in the TextureProxy, 89%.
//
// TYPE 3 MATTERS: rmcDictionaryReference is the class that resolves a texture
// through a DICTIONARY. So the .pck can declare "look me up in the dictionary"
// instead of loading the texture - which is exactly the PPF path.
//
// +0x05 is CD filler and +0x06 is a u16 that is 1 in every texture measured
// (a refcount, probably).

//
// +0x06 TO +0x0F DEPEND ON THE CLASS - the same space means different things
// depending on the type byte at +0x04.
//
// rmcTexturePS2 (type 0):
//   +0x06  u16, 1 in every texture measured (a refcount, probably)
//   +0x08  code derived from the SIZE of the base level. It is a pure function
//          of (TW, TH, PSM), without a single exception in 2379 samples, and in
//          fact only of the number of BLOCKS - PSMT4 at one size gives the same
//          code as PSMT8 at half the area, because they take the same bytes:
//              1 block  -> 99     2 -> 100    4 -> 132    8 -> 133
//             16        -> 165   32 -> 166   64 -> 198  128 -> 199
//            256        -> 231
//   +0x0C  u16  blocks of the BASE LEVEL.  2379 of 2379 match
//          width * height * bpp / 8 / 256.
//   +0x0E  u16  TOTAL blocks: base + mips + CLUT, with a 4-block CLUT for
//          PSMT8 and 1 for PSMT4. 2379 of 2379 match. Written by
//          rmcTexturePS2::Init(char const*, gfxImage*) (0x002B7498) with sh.
//
// rmcTextureReference (type 2) - IT IS ONLY 0x10 BYTES, plus the name when it
// lives alongside. The class holds nothing of the texture: it is a WRAPPER that
// redispatches. Every virtual function of it does the same thing:
//
//     lw   a0, 12(a0)          ; this->target
//     bnel a0, zero, ...
//     lw   v1, 0(a0)           ; the target's vtable
//     lw   v0, N(v1)           ; the SAME slot that was called on it
//     jalr v0
//
// GetWidth takes 0x30 of the target, GetHeight 0x34, GetMipMapCount 0x38,
// IsPlaced 0x1C - always the equivalent slot. The only ones that do not
// redispatch are GetName and the destructor.
//
//   +0x08  NAME POINTER. rmcTextureReference::GetName (0x002B6B38) has two
//          instructions and no more: `jr ra` with `lw v0, 8(a0)` in the delay
//          slot. (In the FILE this field is 0 - the fixup happens at load.)
//   +0x0C  POINTER TO THE WRAPPED TEXTURE, the redispatch target. It is also
//          what rmcTextureReference::GetTexturePtr (0x002B66B8) returns.
//          Measured: 1621 of 1621 San Diego references point to a real
//          texture.
//
// THE SIZE: of the 350 references in atlanta's RAM, 232 have the name INLINE
// right after the header - their +0x08 points to their own +0x10 - and the
// other 118 point to an outside name and take EXACTLY 0x10. You can see it with
// the naked eye: at 00751xxx they are stacked every 0x10.
//
//   Practical consequence: DO NOT read +0x10 of a reference as a field. In the
//   one at 007FF000 what is there is 0x00000012 followed by CD, and it neither
//   points nor is pointed to by anyone - it is heap leftovers, not data.
//
//   The only code that reads +0x10 and +0x12 as a pair of u16 is the
//   datResource constructor (0x002B6EC8), which uses the pair as a key into a
//   12-byte table at 0x006BE6F8 and ONLY THEN writes the runtime vtable. So it
//   is the SERIALISED key, consumed at load and overwritten later by the name.
//
// That is why the shader path's TextureAny is minimal: reading +0x0C as a
// block count in a reference would give a meaningless number, and reading it
// as a pointer in a PS2 texture would give an invalid address.

struct ClutEntry {
    u8 r;
    u8 g;
    u8 b;
    u8 a;
} [[static]];

// The block MipData.buffer points to: 0x90 bytes of header and then the
// PIXELS. The proof is rmcTexturePS2::Init (0x002B7498):
//
//     lw    v1, 0x48(this)         ; the level's data
//     addiu s0, v1, 0x90           ; destination
//     lhu   v0, 4(img)             ; height
//     mult  v0, a2                 ; * width
//     mflo  a2
//     jal   memcpy                 ; 0x00432B20
//     lw    a1, 0xc(img)           ; source: the gfxImage pixels
//
// Checked on the root texture: the payload at +0x90 has 256 distinct byte
// values with 1 dominating (22940 of 65536) - 8-bit indices of a 256x256
// PSMT8, as its TEX0 says.
//
// Of the header, three fields are settled, measured on the 577 resident
// levels of the atlanta dump:
//
//   +0x00  ZERO in 577 of 577.
//   +0x08  pointer BACK to the MipData[level] entry that points here.
//          577 of 577 (before, I had checked only 5).
//   +0x0C  THE SIZE OF THE ALLOCATION, in bytes:
//
//              0x100 + pixels + (CLUT, if this is the LAST level)
//
//          563 of 577. The 14 that disagree are all PSMT4 and all exactly
//          64 bytes over - one extra PSMT4 CLUT - so the shape of the rule is
//          right and the exception is one of counting, not of layout.
//
//          Note the 0x100: the useful header is 0x90 and the pixels start
//          there, but the allocation reserves 0x100. That leaves 0x70 of slack
//          at the END of the block, which is that tail of CD after the CLUT.
//
// And that is how the texture chain in memory is walked: the blocks tend to
// sit back to back, so `ptr + (+0x0C)` lands on the next one. On atlanta's
// root texture, 007E7F00 + 0x10500 = 007F8400, which is the next texture (and
// the same holds in sd). But this is allocator opportunism and not a rule: of
// the 168 pairs of consecutive levels, only 92 are contiguous.
//
// +0x04 points to a shared node in the 0x0196xxxx range, repeated across
// levels, and is null on the root texture. +0x14 varies (14, 28, 32, 1...)
// without following size or level. From +0x18 to +0x8F it is never written:
// it stays CD.
struct MipBuffer {
    u32 zero_00;                                  // +0x00  0 in 577 of 577
    u32 shared_node [[format("fmt_ptr")]];        // +0x04
    u32 owner [[format("fmt_ptr")]];              // +0x08  -> the MipData that points here
    u32 size;                                     // +0x0C  0x100 + pixels + clut
    u32 unk_10;
    u16 unk_14;
    u16 sentinel;                                // +0x16  always FFFF
    padding[0x78];                               // +0x18 .. +0x8F, never written
    // from here on come width*height bytes of pixels
};

// One descriptor per mip level. The byte at +0x86 says how many of the four
// are in use; the rest are zeroed.
//
// WHAT I CALLED `size_code` IS TWO u16, and the upper one is the more useful:
// IT IS THE LEVEL'S ADDRESS IN GS MEMORY, in 256-byte blocks. Two twin
// textures give it away, 007F8400 and 007FAF40 - same class, same dimensions,
// same flags, same TEX1, and still the field differs:
//
//     007F8400   level 0 = 27D0.00A6   level 1 = 262E.0085   +0x82 = 2636
//     007FAF40   level 0 = 270A.00A6   level 1 = 2639.0085   +0x82 = 2641
//
// The lower u16 is identical in both (it is the dimension), the upper one is
// not. And the upper one is the only number that makes +0x82 add up:
// 0x2639 + 8 blocks = 0x2641.
//
// WHAT ADDS UP, EXACTLY:
//
//   blocks_to_end(k) == blocks(k) + blocks_to_end(k+1), and on the last level
//   == blocks(k) + CLUT (4 blocks for PSMT8, 1 for PSMT4).
//   277 of 277 textures with GS assigned. It is not "the following ones" as I
//   had written - the array goes from the LARGEST to the smallest, so a level's
//   running total covers it and the SMALLER ones.
//
//   Example at 0086E140, where the whole stack can be seen built back to front
//   in GS memory:
//       level 2   32x32   TBP 10219  4 blocks  -> 10223
//       level 1   64x64   TBP 10223 16 blocks  -> 10239
//       level 0  128x128  TBP 10239 64 blocks  -> 10303
//       CLUT              CBP 10303  4 blocks  -> 10307     total 88 = to_end(0)
//
// AND THE PROOF THAT IT REALLY IS A GS ADDRESS: splitting the textures by the
// +0x7C stamp (see the note further down), the pieces of one stamp barely
// overlap - stamp 0x81C5 gives 46 pieces and ZERO overlap, 0x81CB gives 312
// pieces and 6. Mixing stamps it goes up to 192, which is what a cache that
// reuses the same space on different frames should give. And everything falls
// in blocks 9217..13659 of the GS's 16384 - that is after the framebuffers,
// which is exactly where the texture area has to start.
//
//   tbp == 0  means that level is NOT in the GS right now. It is common: of the
//   695 level entries with a valid CBP, 257 have tbp zeroed, and in 111
//   textures two of the three levels are out. It is mip streaming.
//
// The lower u16 is a size class: ((log2(h)-1) << 5) | (log2(w)-1), only it is
// computed on the FOOTPRINT IN BYTES and not on the pixels - which is why
// PSMT4, which spends half a byte per pixel, shows up shifted one step. It
// matches in 7923 of the 9356 levels, and the 1433 mismatches are all PSMT4.
// Redundant with TEX0, so it only serves as a cross-check.
struct MipDesc {
    u16 size_class;                       // byte footprint, as a (w,h) pair
    u16 tbp;                              // GS address in blocks; 0 = not in the GS
    u16 blocks;                           // of this level
    u16 blocks_to_end;                    // this one + the SMALLER ones + CLUT
    u64 tex0 [[format("fmt_tex0")]];
};

// One per level as well, and the index is the same: rmcTexturePS2::Init
// (0x002B7498) addresses it with `this + 0x48 + level*12` (addiu v0,zero,0xC;
// mult fp,v0). The first field is a pointer to the level's data, and Init uses
// `ptr + 0x90`, so the block pointed to has 0x90 bytes of header before the
// payload. In most city textures it is null - the pixels do not live here.
struct MipData {
    u32 buffer [[format("fmt_ptr")]];
    u32 unk_04;
    u32 unk_08;
};

//
// +0x7C TO +0x87 - what is pinned and what is not.
//
// THE TWO POPULATIONS OF +0x7C TO +0x87 ARE "HAS GS" AND "HAS NO GS".
//
// Sweeping the whole RAM of the atlanta dump gives 3662 type-0 rmcTexturePS2,
// and they split into two groups with a sharpness that is not chance:
//
//       +0x82 = CDCD or D1D1 (filler)        3385, and all 3385 have bit31
//                                            of +0x7C set
//       +0x82 with a real value              277, and of these 270 have bit31
//                                            of +0x7C CLEAR
//
// Only 7 exceptions in 3662, and now the split can be explained: the second
// group HAS GS MEMORY ASSIGNED and the first does not. +0x82 is the CLUT
// address (CBP) and +0x7C is the stamp of when the allocation was made.
//
//   +0x7C  stamp. Bit 31 set means "no allocation"; the low 31 bits then hold
//          0 or 1. Without bit 31, the value falls in a narrow window - 31423
//          to 33228 in the 270 textures with GS - and the two largest dominate
//          (33227 in 125 of them, 33228 in 87). It moves like a frame counter
//          used as a use stamp, and splitting the textures by it is what makes
//          the allocations stop colliding (see the MipDesc note). I DID NOT
//          FIND the counter that feeds it: no global in 001A0000..00680000 is
//          33228. It stays "stamp", not "frame".
//
//          The 131 textures where the 31 bits were 0x15DF4 / 0x15DF3, equal to
//          the +0x0C array of rmcTextureProxyPS2, come from another savestate
//          and another context; do not mix the two measurements.
//
//   +0x82  CBP, the CLUT address in the GS in 256-byte blocks. It falls right
//          after the largest loaded level in 217 of the 270.
//
//   +0x85  clamp,  +0x86  mip count,  +0x87  part of the CD when there is no GS.
//
//   +0x80  u16. I COULD NOT PIN DOWN THE RULE. It is always a power of two and
//          follows the size, and in many PSMT8 it equals the base level's
//          blocks, but it breaks: 32x32 PSMT8 gives 16 with only 4 blocks, and
//          the 256x256 root texture gives 4096. Tested and discarded: base
//          level blocks, bytes/16, bytes/256, area/constant. And crossing it
//          with the dimension in the 270 with GS kills the size-function
//          hypothesis for good: 128x128 PSMT8 shows +0x80 = 64 in 41 textures
//          and = 1024 in another 24. It stays an unknown u16 instead of a wrong
//          name.
//
//   +0x84  u8, the only field here that holds in BOTH groups. 1 in ALL 2188
//          PSMT8 textures and 0 in ALL 191 PSMT4, without exception. It is the
//          CLUT class: 256 entries against 16 - exactly the distinction that
//          makes the +0x0E block count add up. The one at 007F8400 is PSMT4
//          and has +0x84 = 0.
//
// AND A WARNING: NONE OF THIS HOLDS FOR rmcTextureReference (type 2). In those
// objects +0x80 sometimes contains 00627040 or 00627280 - the vtables of OTHER
// textures - which can only mean the reference is much smaller than 0xAC and
// that reading this far runs into the neighbouring allocation. Check the type
// byte at +0x04 before applying this struct.

struct TexturePS2 {
    u32 vtable [[format("vt")]];
    u8  kind;                             // +0x04  0/1/2/3, see the note
    u8  pad_05;                           // CD
    u16 refcount;                         // +0x06  1 in everything measured
    MipDesc mip[4];                       // +0x08 .. +0x47
    MipData level[4];                     // +0x48 .. +0x77
    u32 name [[format("fmt_ptr")]];       // +0x78  0 when anonymous
    u32 stamp;                            // +0x7C  bit31 = no GS; rest = use stamp
    u16 unk_80;                           // +0x80  power of two, no rule
    u16 cbp;                              // +0x82  CLUT address in the GS, in blocks
    u8  clut_256;                         // +0x84  1 for PSMT8, 0 for PSMT4
    u8  clamp;                            // +0x85  bit0 U, bit2 V
    u8  mips;                             // +0x86
    padding[1];
    u64 tex1 [[format("fmt_tex1")]];      // +0x88  bits 21..31 = proxy id

    // The pixel buffers, one per level, when they exist. Most city textures
    // have all four null: they are not resident.
    if (EXPAND_PIXELS && level[0].buffer != 0) {
        MipBuffer hdr0 @ ptr(level[0].buffer);
        u8 pixels0[level_bytes(mip[0].tex0)] @ ptr(level[0].buffer) + 0x90;
        if (mips == 1) {
            ClutEntry clut0[clut_entries(mip[0].tex0)] @ ptr(level[0].buffer) + 0x90 + level_bytes(mip[0].tex0);
        }
    }
    if (EXPAND_PIXELS && level[1].buffer != 0) {
        MipBuffer hdr1 @ ptr(level[1].buffer);
        u8 pixels1[level_bytes(mip[1].tex0)] @ ptr(level[1].buffer) + 0x90;
        if (mips == 2) {
            ClutEntry clut1[clut_entries(mip[1].tex0)] @ ptr(level[1].buffer) + 0x90 + level_bytes(mip[1].tex0);
        }
    }
    if (EXPAND_PIXELS && level[2].buffer != 0) {
        MipBuffer hdr2 @ ptr(level[2].buffer);
        u8 pixels2[level_bytes(mip[2].tex0)] @ ptr(level[2].buffer) + 0x90;
        if (mips == 3) {
            ClutEntry clut2[clut_entries(mip[2].tex0)] @ ptr(level[2].buffer) + 0x90 + level_bytes(mip[2].tex0);
        }
    }
    if (EXPAND_PIXELS && level[3].buffer != 0) {
        MipBuffer hdr3 @ ptr(level[3].buffer);
        u8 pixels3[level_bytes(mip[3].tex0)] @ ptr(level[3].buffer) + 0x90;
        if (mips == 4) {
            ClutEntry clut3[clut_entries(mip[3].tex0)] @ ptr(level[3].buffer) + 0x90 + level_bytes(mip[3].tex0);
        }
    }
    // THE STRUCT ENDS AT +0xA0, not at 0xAC as I had concluded before. The
    // mistake: hood descriptors sit 0xB0 apart and I assumed the whole gap
    // belonged to the texture. Measuring, ANOTHER OBJECT starts at +0xA0 in
    // 1850 of the 2379 textures, and the u32 there is a known vtable in 2355 of
    // them - 00626E70 rmcShaderBasic (1177), 00627040 rmcTextureReference (395),
    // 00626F30 rmcShaderInstance (303), 00635C18 mcShaderCityWindow (231),
    // 00627280 rmcTexturePS2 (175). So the hood is name(4) + texture(0xA0) +
    // a 12-byte shader = 0xB0.
    padding[0x10];                        // +0x90 .. +0x9F, zeroed in 2248 of 2379
                                          //        1-based; MMIN at 6..8
};

// CONFIRMED: the block pointed to by hood+0x00 is a char[4] with the short
// name followed by an rmcTexturePS2 PACKED at +0x04. Tested on San Diego's six:
// desc+4 has vtable 00627280 and its TEX0 decodes to 64x64 or 128x128 PSMT8
// with 3 mips.
// name(4) + texture(0xA0) = 0xA4. The 12 bytes up to the next descriptor
// (which sits 0xB0 further on) are a shader, not texture leftovers.
struct HoodTexture {
    char        name[4];              // "be", "dt", "fwy", "hl", "ml", "pk"
    TexturePS2  tex;                  // +0x04
};

struct Hood {
    u32 texture [[format("fmt_ptr")]];
    u32 unique_count;
    u32 unique_array [[format("fmt_ptr")]];
    u32 instance_count;
    u32 instance_array [[format("fmt_ptr")]];

    HoodTexture tex @ ptr(texture);
    CityModel uniques[unique_count] @ ptr(unique_array);
    if (EXPAND_INSTANCES) {
        InstCityModel instances[instance_count] @ ptr(instance_array);
    }
};

// =====================================================================
// SHADER
// =====================================================================
//
// The rmcShaderGroup has the pointer array and the count, and the count is TWO
// equal u16 (capacity and use), not a u32 - read as a u32 it gives 0x05310531,
// some 87 million, and a struct array of that size brings ImHex down. The
// mesh's material index (Mesh+0x0C) indexes THAT array.
//
// MEASURED IN RAM, and here the two cities really diverge:
//
//     atlanta   18 groups x 1329 slots = 23922, with 4105 filled
//     sd        15 groups x 1516 slots = 22740, with 4020 filled
//
// In both cases it is VERY empty - walking the slots you see zero most of the
// way. In atlanta the per-group fill goes from 78 (group 3) to 404 (group 10),
// and group 4 has 236 filled of 1329 with a solid gap of 143 slots between 480
// and 624.
//
// Atlanta's 4105 filled ones split into six classes, with nothing left over:
// rmcShaderBasic 1951, mcShaderCityWindow 1151, rmcShaderInstance 533, its
// second vtable 434, rmcShaderComplex 18 and mcShaderTexAnimation 18.
// Following +0x08 (and +0x0C in the CityWindow case) they reach 5256 distinct
// textures.
//
// Slots per city: 1329 in atlanta, 1516 in sd, 1720 in tokyo, 1154 in detroit.
//
// THE TRAP: rmcModel::FixupShaders (0x2A8A18) does array[index] and calls the
// virtual at +24 WITHOUT A NULL CHECK. In atlanta only 5 indices are filled in
// all 18 groups; 1041 of the 1046 used are empty in some group. Retail survives
// because each mesh only takes part in the passes where its material exists -
// imported geometry with the material swapped underneath has no such
// guarantee.
//
// A shader is 0xB0 bytes with ONE internal pointer at +0x08, which in 1889 of
// 1900 cases points to the sub-object embedded at +0x10. That sub-object is an
// rmcTextureReference - it is the one with GetName, Bind, GetWidth, GetHeight,
// GetMipMapCount, Download and GetDownloadSize.

// THE SHADER SIZE VARIES BY CITY, and I had generalised wrongly from atlanta.
// There the stride between consecutive shaders is 0xB0 and the texture comes
// EMBEDDED at this+0x10 (1889 of 1900 cases). In San Diego the stride is 0x0C:
// the shader is only the header and the texture lives far away -
//   slot 0  shader 007E7EB8  +0x08 -> 00819100  (this + 0x31248)
// So `+0x08` is a pointer to a texture, full stop; if it lands inside its own
// block that is an allocation coincidence, not a rule.

// The shader's `+0x08` points either to an rmcTexturePS2 or to an
// rmcTextureReference, and the two classes do NOT share a layout - reading the
// TEX0 at +0x10 of a reference would show garbage that looks like a register.
// So here the dispatch is on the TYPE BYTE at +0x04, which already separates
// the two, and each branch opens the right struct. Before, this was a raw
// `rest[0x3B]` and it was pure loss: a 59-byte unlabelled smear right on top of
// the most decoded header in this file.
// The wrapper. Exactly 0x10 bytes when the name lives elsewhere, and the name
// right after when it lives alongside (232 of atlanta's 350). See the "type 2"
// note.
// A lone vtable, to lay over an address. ImHex does not accept an attribute
// on a declaration with `@`, so the formatter has to come from inside a
// struct.
struct VtableAlias {
    u32 vtable [[format("vt")]];
};

struct TextureReference {
    u32 vtable [[format("vt")]];      // 00627040 (or 00626FC8)
    u8  kind;                         // 2
    u8  pad_05;                       // CD
    u16 refcount;
    u32 name [[format("fmt_ptr")]];   // +0x08  GetName returns exactly this
    u32 target [[format("fmt_ptr")]]; // +0x0C  where every virtual redispatches

    if (name != 0) {
        std::string::NullString name_text @ ptr(name);
    }
    if (target != 0) {
        VtableAlias target_vtable @ ptr(target);
    }
};

struct TextureAny {
    u32 vtable [[format("vt")]];   // rmcTexturePS2 or rmcTextureReference
    u8  kind;                      // 0..3 in retail

    // A reference is worth opening - it is only 0x10 bytes and what matters is
    // the target. The `$ - 5` goes back to the start of the object itself, so
    // the vtable and the type show up twice in the tree; that is on purpose, to
    // avoid a second struct just for the body.
    // No `else if` on purpose: three mutually exclusive conditions, one per
    // block. Placement with `@` does not move the cursor, so `$ - 5` goes back
    // to the start of the object in all three branches.
    if (kind == 0) {
        TexturePS2 ps2 @ $ - 5;
    }
    if (kind == 2) {
        TextureReference reference @ $ - 5;
    }
    if (kind != 0 && kind != 2) {
        u8 rest[0x3B];
    }
};

// A city texture slot. Same idea as ShaderSlot: the pointer may be null and
// then nothing is placed.
struct TexturePtr {
    u32 address [[format("fmt_ptr")]];

    if (address != 0) {
        TextureAny t @ ptr(address);
    }
};

// THE SPECIAL CASE OF mcShaderCityWindow (vtable 00635C18): it has 0x20 bytes
// and TWO textures, not one. Measured on ATLANTA's 1151, without exception
// (and note: the sd dump has NONE - it is a one-city shader):
//
//   +0x04  07808010 in 1151 of 1151 - a class constant
//   +0x08  first texture, and it is always `this + 0x20`: the texture lives
//          right after the shader
//   +0x0C  second texture, right after the first
//   +0x10  RGBA colour in floats from 0 to 255. (255,255,255,255) in 213 of them
//          and the rest are greys - 149, 155, 126, 136, 143 - with alpha always
//          255.
//
// And on top of that this shader CONFIRMS both sizes by a completely
// independent route: the distance between the first and the second texture is
// 0x10 in the 844 where the first is rmcTextureReference and 0xA0 in the 307
// where it is rmcTexturePS2. Exactly the two sizes I had deduced another way,
// now measured again in 1151 of 1151.
// The block the pass points to at +0x14, and the parameters inside it.
//
// The header is 0x18 bytes and its core is FOUR SLOTS. Each slot is either null
// or points to a 0x20 record inside the block itself, always at a multiple of
// 0x20 from +0x20 - 34 blocks of 34, without exception. That is why the water's
// pass 0 block is 0x80 and passes 1 and 2 are 0xA0: in pass 0 one of the four
// slots is null. Count: 11 blocks with three slots filled and 23 with four.
//
// The record SMELLS OF UV SCROLLING, and the set of 11 waters is too coherent
// to be anything else:
//
//     A = 1.0   B = 0.040   C = 0.5    D = 1     x11
//     A = 1.0   B = 0.023   C = 0.5    D = 1     x22
//     A = 1.0   B = 0.013   C = 0.5    D = 1     x11
//     A = -1.0  B = 0.013   C = 0.5    D = 1     x11
//     A = 0.0   B = 0.0     C = 0.5    D = 5     x48
//     A = 0.0   B = 0.0     C = 0.25   D = 5     x22
//
// A is only +1, -1 or 0; B only shows up with A; D is 1 when the A/B pair is in
// use and 5 when not. So three layers running one way at 0.040, 0.023 and
// 0.013 and one running the other way at 0.013 - which is exactly how water is
// made. I named the fields with letters because that is the reading, but I did
// not find the consuming code; do not rename it "speed" without confirming.
struct PassParam {
    float a;                     // +0x00  1.0 / -1.0 / 0.0
    float b;                     // +0x04  only shows up with a
    u32   mode_08;               // 0x200 when a/b apply, 0 when not
    u32   zero_0c;
    float c;                     // +0x10  0.5 / 0.25
    u16   dd;                    // +0x14  1 with a/b, 5 without
    u16   pad_16;
    u16   g[4];                  // +0x18
};

struct ParamSlot {
    u32 address [[format("fmt_ptr")]];

    if (address != 0) {
        PassParam p @ ptr(address);
    }
};

struct PassBlock {
    u32       zero_00;
    ParamSlot slot[4];           // +0x04 .. +0x13
    u32       zero_14;           // zero in 33 of 34
    u16       unk_18[4];         // +0x18
};

// One rmcShaderComplex pass. 0x20 bytes, chained through +0x1C.
//
//   +0x00  the pass's BYTE PROGRAM. It is not a struct, it is a COMMAND
//          STREAM, and the interpreter is at 0x002B1F90 (called by
//          rmcShaderComplex::Bind, 0x002B2168, after walking the chain):
//
//              command = 1 OPCODE byte + 1 or 4 DATA bytes
//                  opcode & 0x3F   register index
//                  opcode & 0xC0   if ZERO the data is 1 byte, otherwise 4
//                  opcode == 0xFF  end
//
//          The value goes to a table in the scratchpad at 0x70000060
//          (index*4), and is compared with the default table at 0x700000B8 to
//          set or clear a bit at 0x7000010C.
//
//          The 61 reachable programs of the sd savestate decode up to the 0xFF
//          with nothing left over or missing - 61 of 61. Decoded:
//
//            water           pass 0   r11=01 r3=0001000A r9=08 r8=40FFFFFF r0=00
//                            pass 1   r11=01 r3=00010044 r9=07 r8=64FFFFFF r0=00
//            road_reflector           r3=0001000A r9=0F r5=00 r4=01 r8=40FFFFFF r0=00
//                                     (the other form only changes r8 to 80FFFFFF)
//            train_shadow             r9=07 r3=00010044 r5=00 r4=01 r8=40000000
//            black_matte              r1=01 r3=0001000A r8=00 r9=0F r7=01 r4=01
//
//          r8 is a COLOUR in AABBGGRR, and what supports the reading is that it
//          matches the name: low-alpha white on the water, BLACK on
//          train_shadow and black_matte, and the two road_reflector forms
//          differing only in alpha. The rest stays as numbers.
//
//          To decode any program: mc3_shaderprog.py.
//   +0x04  one byte per shader: 0 in most (51 of 62), 2 in black_matte, 5 in
//          ambient_emissive_brakelight, 7 in rider_stealth. It goes with the
//          NAME, not with the instance.
//   +0x05  3 in 62 of 62.
//   +0x08  POINTER TO A TEXTURE SLOT, not to the texture - one more
//          indirection than everything else in the file. In 39 of 39 what it
//          points to is a word that CONTAINS the texture pointer, and in 20 of
//          the 39 that slot lives inside ANOTHER shader (in the water at
//          0081F100 it lands on 00819D2C, which is the +0x1C of an
//          rmcShaderInstance).
//   +0x14  a 0x80-byte block. Only the water has it. Inside it there are three
//          0x20 records whose (float, u16) pair is constant across the 12
//          instances - (0.5, 1), (0.25, 5), (0.25, 5) - and only the two u32
//          at the end change.
struct ShaderPass {
    u32 program [[format("fmt_ptr")]];
    u8  variant;
    u8  three;                           // always 3
    u16 pad_06;
    u32 texture_slot [[format("fmt_ptr")]];
    u32 zero_0c;
    u32 zero_10;
    u32 block [[format("fmt_ptr")]];
    u32 flags_18;
    u32 next_pass [[format("fmt_ptr")]];

    if (texture_slot != 0) {
        TexturePtr texture @ ptr(texture_slot);
    }
    if (block != 0) {
        PassBlock params @ ptr(block);
    }
    if (next_pass != 0) {
        ShaderPass following @ ptr(next_pass);
    }
};

// THE SIZE OF THE SHADER OBJECT, measured in the file:
//
//     size = 0x08 + 4 * max(parameter_count, 1)
//
// where the count is bits 22..24 of the +0x04 bitfield. SLOT 0 ALWAYS EXISTS,
// even with the count zeroed: city_facade has flags == 0 and still carries a
// texture at +0x08, and such objects packed together sit 12 bytes apart. See
// the long note in mc3_city.hexpat.

struct Shader {
    u32 vtable [[format("vt")]];   // rmcShaderBasic / Complex / Instance /
                                   // mcShaderCityWindow / mcShaderTexAnimation
    u32 kind;                      // & 0x7F == 0 in the basic ones
    u32 tex [[format("fmt_ptr")]]; // +0x08 -> the texture

    // rmcShaderComplex IS LEFT OUT: its +0x08 is not a texture in 39 of 39 (it
    // points to a data block). There the texture comes through +0x10, with one
    // more indirection - see its branch further down.
    if (tex != 0 && vtable != 0x00626EC8) {
        TextureAny texture @ ptr(tex);
    }

    if (vtable == 0x00635C18) {         // mcShaderCityWindow
        u32   tex2 [[format("fmt_ptr")]];   // +0x0C  the second one
        float colour[4];                    // +0x10  RGBA from 0 to 255
        if (tex2 != 0) {
            TextureAny texture2 @ ptr(tex2);
        }
    }

    // rmcShaderComplex IS AN 8-BYTE HEADER PLUS A LIST OF PASSES, and the
    // shader file name at the end.
    //
    // I got here the wrong way round: I kept opening +0x08, +0x10, +0x1C and
    // +0x24 as if they were loose fields of the class. They are not. THE FIRST
    // PASS STARTS AT +0x08 and takes 0x20 bytes, and what I called "+0x24" was
    // the pointer to the next pass. Walking the chain:
    //
    //     water.shadert            3 passes, in 11 of 11
    //     everything else          1 pass
    //
    // So the water is drawn in THREE passes. The byte programs of its passes 1
    // and 2 are identical to each other and different from pass 0:
    //
    //     pass 0    0B 01 C3 0A 00 01 00 09 08  C8 FF FF FF 40  00 00 FF
    //     pass 1    0B 01 C3 44 00 01 00 09 07  C8 FF FF FF 64  00 00 FF
    //     pass 2    same as 1
    //
    // And that explains the rest: only the water had that 0x80-byte block and
    // only it had a non-null "+0x24" - they were the PASS's block and the chain
    // pointer, not fields exclusive to one shader.
    //
    // The NAME sits at +0x30, inline, and it is a real file: ASSETS/shaderlib/
    // has 111 .shadert and 39 of the 62 objects of this class in RAM name one of
    // them. It is the direct thread between a city material and the shader
    // source. The 20 that sit in a city group slot use four names:
    // water.shadert, road_reflector.shadert, city_specular.shadert and
    // city_texscroll_doublesided.shadert. Outside the city the vehicle ones
    // show up (ambient_wheel, ambient_shadow, ambient_emissive_brakelight,
    // wheel_inner, rider_stealth, black_matte) and the train ones (train_ads,
    // train_inside, train_people, train_shadow).
    //
    // WARNING: sweeping the RAM for vtable 00626EC8 gives 62 objects but only
    // 39 have a valid name - the others carry race and music paths at the same
    // offset. Check the name against ASSETS/shaderlib before treating it as a
    // shader.
    //
    // The header's +0x04 is 1 in 62 of 62. It is not the pass count: the water
    // has 3 and still marks 1.
    if (vtable == 0x00626EC8) {         // rmcShaderComplex
        ShaderPass pass @ $ - 4;                  // +0x08
        std::string::NullString name @ $ + 0x24;  // +0x30  the .shadert
    }

    // mcShaderTexAnimation: 0x20 bytes and a FRAME STRIP. The 6 in sd:
    //
    //     0081DD90  30 frames at 10 fps      0084CAE0  30 at 10 fps
    //     0082BA40  30 frames at 10 fps      008701C0   5 at  2.000 fps
    //     0088D0C0   4 frames at  2.066 fps  0089D070   4 at  2.314 fps
    //
    // The memory arrangement is economical and worth understanding: the base
    // texture (+0x08) sits exactly 0xA0 before the array (6 of 6), and FRAME 0
    // is not a new texture - it is a 0x10-byte rmcTextureReference pointing back
    // to the base. Frames 1 onwards are their own rmcTexturePS2, packed every
    // 0xA0. It holds in 5 of the 6; the one at 0089D070 breaks the pattern.
    //
    // +0x04 is 20 in 6 of 6, the same way the CityWindow has 07808010: it is a
    // class constant, not data.
    //
    // THE CLASS IS 0x20 BYTES, not 0x18 as I said before. The proof is the
    // retail .pck itself: the base texture starts at shader+0x20 both in the
    // file and in RAM, so +0x18 and +0x1C are its fields.
    //
    // WHAT THOSE TWO FIELDS ARE, I DO NOT KNOW. Sweeping the 33 animations in
    // the retail .pck files (18 in atlanta, 9 in detroit, 6 in sd, none in tokyo
    // nor in modcity) they show up in three forms:
    //
    //     two pointers, almost always 0x10 or 0x30 apart          ~15 cases
    //     a pair (1, N) with N in {4, 6, 8, 9, 0x10, 0x1A, 0x1E}  ~15 cases
    //     CDCDCDCD in both                                           3 cases
    //
    // They stay as unnamed u32, but they are real data and not filler: they are
    // in the retail .pck, and when the pair is a pointer it is relocated
    // normally at load.
    //
    //   CORRECTION: I had written here that the pair showed up REWRITTEN in RAM
    //   and not just relocated. It was wrong, and the mistake was comparing
    //   against the WRONG .pck - I used sd_midnight_clear when the savestate is
    //   dusk_clear. In sd_dusk_clear the values are 06A2BEA0 and 06A2BEB0,
    //   which minus the load delta 0x0601A480 give 00A11A20 and 00A11A30,
    //   exactly what is in RAM. Before diffing file against memory, check the
    //   VARIANT - see the sky note, which gives its name for free.
    if (vtable == 0x00635B10) {         // mcShaderTexAnimation
        float fps;                          // +0x0C
        u16   frames;                       // +0x10
        u16   pad_12;
        u32   list [[format("fmt_ptr")]];   // +0x14
        u32   unk_18;                       // +0x18  see the note above
        u32   unk_1c;                       // +0x1C

        if (list != 0 && frames > 0 && frames <= 512) {
            TexturePtr frame[frames] @ ptr(list);
        }
    }
};

// A group slot. The pointer can be null - most are - and that is why the
// Shader is only placed when it exists.
struct ShaderSlot {
    u32 address [[format("fmt_ptr")]];

    if (address != 0) {
        Shader sh @ ptr(address);
    }
};

struct ShaderGroup {
    u32 vtable [[format("vt")]];
    u32 slots [[format("fmt_ptr")]];
    u16 slot_count;
    u16 slot_count2;
    u32 unk_0c;

    if (EXPAND_SHADERS) {
        ShaderSlot slot[slot_count] @ ptr(slots);
    }
};

// =====================================================================
// PER-CELL INDEX (MapRoot+0x1C) - THE REAL CULLING
// =====================================================================
//
// The tile box is only the INPUT. What the game walks is this table. A
// validated rule, not a deduced one: rebuilding the index from the tile boxes
// themselves, retail comes out identical - atlanta 3968/3968, tokyo 6210/6210,
// detroit 7200/7200. The lists come in DESCENDING index order.
//
// THE GRID IS NOT A FREE PARAMETER: the fixup (0x25BAE8 and 0x25BB40) walks
// cell_count entries of these arrays. Raising MapRoot+0x268 without rebuilding
// the arrays hangs the load. See mc3_cellindex.py.

struct CellRecord {
    s16 unique_count;
    s16 instance_count;
    u32 unique_list   [[format("fmt_ptr")]];    // u16[unique_count]
    u32 instance_list [[format("fmt_ptr")]];    // u16[instance_count]
} [[static]];

// ---------------------------------------------------------------------
// THE SECOND PER-CELL LIST (CellIndex+0x14)
//
// It was annotated as "u32[cell_count], not decoded". It is a vector of
// POINTERS, one per cell: in sd_dusk_clear 2391 of the 4473 cells have one and
// 2082 are zero. The targets add up to 423 KB - it was the largest unmapped
// block of the whole pck.
//
// Each target:
//     { float; u16 na (bit 15 is a flag); u16 nb; u16 list[na + nb] }
// aligned to 4.
//
// CHECKED: in 2390 of the 2391 the size computed this way lands EXACTLY on the
// start of the next record. The one left is the last, which has no next.
//
// The values are an INSTANCE INDEX in 14 bits (largest seen 5007, against
// instance_count 5008) plus a 2-bit field on top, and all four values of that
// field show up. The `na` flag shows up in 1067 of the 2391.
//
// What the two lists separate, and what the float measures, is still open.
// ---------------------------------------------------------------------

struct CellExtra {
    float scale;
    u16   na;                  // bit 15 = flag
    u16   nb;
    u16   list[(na & 0x7FFF) + nb];
};

struct CellExtraPtr {
    u32 address [[format("fmt_ptr")]];
    if (address != 0) {
        CellExtra record @ ptr(address);
    }
};

struct CellIndex {
    u32 unique_count;
    u32 instance_count;
    u32 unique_array   [[format("fmt_ptr")]];   // u32[unique_count]
    u32 instance_array [[format("fmt_ptr")]];   // u32[instance_count]
    u32 cells          [[format("fmt_ptr")]];   // CellRecord[cell_count]
    u32 extra          [[format("fmt_ptr")]];   // CellExtraPtr[cell_count],
                                                // see the note above
};

// =====================================================================
// THE CITY
// =====================================================================
//
// vtable 00625228, derived from parFileIO (Save/Load/AfterLoad/BeforeSave/
// GetDirName/GetTypeName) with a thunk of mcSkyHatClass::FileIO(datParser&):
// the whole root is a class serialised by RAGE's datParser.

//
// +0x04 IS THE MEMORY OWNER, and there is no point touching it in the file.
//
//   mcCity::mcCity(datResource&)  0x00259160
//       sw   vtable, (this)          <- 00625228
//       addiu v1, zero, 1
//       sb   v1, 4(this)             <- always 1, unconditionally
//
//   mcCity::Delete()              0x002575C0
//       if (this->owns_memory) {
//           datPaging::DisableDelete();      // 0x0042E840
//           this->~mcCity(3);                // virtual +0x08
//           datPaging::EnableDelete();       // 0x0042E860
//           aligned_delete(this);            // 0x0042E6C0
//       } else {
//           this->~mcCity(3);                // destroys, does NOT free
//       }
//
// In the file the byte is 0 in the four cities; in RAM it is always 1, because
// the constructor stamps it. The next three bytes stay CD CD CD - allocator
// filler that was never written, not a field.
//
// The constructor taking `datResource&` confirms load-in-place: the object is
// constructed ON TOP of the file image, and that is why it becomes the owner
// of the whole block.

//
// +0x0C IS AN ARRAY, +0x10 IS A SINGLE GROUP. The difference is in the code:
//
//   mcCityModelType::Render      (0x0024C380)   and
//   mcInstCityModelType::Render  (0x00257008)
//       lbu  a1, 0xa(s0)              pass index, taken from the object
//       lw   v0, 0xc(mcCity)          the ARRAY
//       sll  a1, a1, 4                 <- 16 BYTES per descriptor
//       addu a1, v0, a1
//       jal  rmcModel::Draw(rmcShaderGroup const&, ...)   0x002A9918
//
//   mcSkyHatClass::Draw          (0x0025C238)
//       lw   a1, 0x10(mcCity)         the WHOLE group, no indexing
//       jal  rmcModel::Draw(...)
//
// So +0x0C are the CITY GEOMETRY groups, one per pass, and each object picks
// its own with a byte; +0x10 is the SKY group. The sizes agree: the city ones
// have 1329 (atlanta), 1720 (tokyo), 1154 (detroit) and 1516 (San Diego)
// slots, and the sky one has 12, 10, 12 and 10, all rmcShaderBasic.

//
// +0x20 IS THE MASTER SWITCH OF THE CITY DRAWING.
//
// Read in exactly two places, the SAME code duplicated in the unique path
// (0x0024BAF8) and in the instanced one (0x00256AB0):
//
//     lw   s3, 0x1c(mcCity)      ; the per-cell index object
//     lw   s4, 0x1a0(mcCity)     ; which visible buffer
//     lbu  v0, 0x20(mcCity)
//     sltu v0, zero, v0
//     beqz v0, <leave>           ; false -> draws NOTHING
//
// Past the guard, the loop is:
//
//     n    = s3->0x64[s4]                 how many are visible
//     base = s3 + 0x84 + s4 * 0x640       400 pointers per buffer
//     for each obj: if (obj->pass_mask & pass) obj->vtable[+0x18](pass)
//
// So the MapRoot+0x1C object holds TWO things: the static per-cell index
// (+0x10/+0x14, which comes from the file) and the per-frame VISIBLE LIST
// (+0x64 and +0x84, filled at runtime). Checked in San Diego: buffer 0 with
// 30 objects, all mcCityModel, names s_dt_blk_* - the city centre.
//
// The byte is 1 in the file AND in RAM in the four cities, and no code writes
// it through the global. Zeroing it loads the city and draws none of it.

//
// +0x24 IS THE DRAW DISTANCE OF THE FALLBACK PATH.
//
// A single reader, at 0x0024C240 - the loop that walks a type's chain of
// models (s0 = type->0x18, and it follows +0x04):
//
//     if (mcCity->cell_index != 0) goto draw;      // with an index: draw all
//     centre = obj->vtable[+0x10]()                // mcCullable::GetCullCenter
//     d2     = |centre - cam|^2                    // cam = [0x70000640],
//                                                  // in the EE scratchpad
//     radius = obj->vtable[+0x14]()
//     lim    = mcCity->0x24 + radius
//     if (lim*lim < d2) do not draw;
//   draw:
//     obj->vtable[+0x18](pass)                     // Render(mcPassTypes)
//
// So: it ONLY applies when there is no per-cell index. With the index present
// the cut is made by the visible list and this field is not even read.
//
// It is 150.0 in the four cities, in the file and in RAM. At 40 units per
// cell, that is 3.75 cells - short, which is what a fallback path should be.
//
// Checked: in San Diego the position at 0x70000640 is (835.25, 2.07, 232.22,
// 1.0), inside the extents and in relative cell (49, 35).

//
// +0x28 IS A TEXTURE BIND MODE THAT ONLY HOLDS WHILE THE CITY DRAWS.
//
// Written by mcCity::Reset() (0x00257640):
//     +0x28 = 0                      always
//     if (*(u8*)0x0061BB00 == 5)  +0x28 = 1
//
// Read by mcCity::ModelDraw(Matrix34 const*) (0x00257AC8), and only there:
//     v0 = mcCity->0x28
//     s1 = *(u8*)0x006BE588          keeps the old value
//     *(u8*)0x006BE588 = v0          swaps
//     mcCullableMgr::Render()        (0x0024A860)
//     *(u8*)0x006BE588 = s1          restores
//
// And what reads that global is rmcTexturePS2::Bind(int) const - slot +0x2C
// of vtable 00627280, at 0x002B8768:
//     if (*(u8*)0x006BE588 != 0) {
//         if (tex->qword_88 & 0xFFE00000) { arg = 0; ... }
//     }
// so, with the mode on, Bind tests the high bits of the texture's +0x88 qword
// (GS state) and can force an argument to zero.
//
// IN PRACTICE IT IS ALWAYS OFF: the global 0x0061BB00 is 10 in the four
// savestates I have - San Diego and Atlanta, loading and in game - and the
// gate wants 5. So +0x28 is 0 in the file and 0 in RAM in everything
// measured. What mode 5 is, I do not know yet; the only other reader of
// 0x0061BB00 is at 0x00238368, in the middle of the memory card module.

// =====================================================================
// mcConditionVariables  -  mcCity+0x40, 0x150 bytes
// =====================================================================
//
// The 45 names below are NOT guesses: they are literals from the ELF. The
// class registers each field with a datParser, and mc3_parser.py reads the
// literals AddField (0x0055F7C0) receives along with the offset. The mcCity
// constructor confirms the base: `addiu a0, this, 0x40` followed by
// mcConditionVariables::mcConditionVariables(datResource&) (0x0025D370).
//
// Checked value by value on retail atlanta, and all of them land on something
// plausible: fog_start 200 / fog_end 300, refl_dist 200, flare_distance 70,
// m_skylightElevation 90, mAmbientTweakRadius 40, flare_on in a byte 1 with
// the CD filler next to it. Hue/Saturation/Value are 1.0 - which is exactly
// why the two palettes come out equal in the file (see
// ChangePaletteSaturation).
//
// This is the city's appearance panel: fog, indoor fog, ambient light,
// directional light, skylight, reflection, flare and the HSV tint.

struct ConditionVariables {
    padding[0xC];
    float fog_color[3];
    float fog_start;
    float fog_end;
    float fog_threshold;
    float fog_clamp;
    float lightfog_scale;
    float indoor_fog_color[3];
    float indoor_fog_start;
    float indoor_fog_end;
    float indoor_fog_threshold;
    float indoor_fog_clamp;
    float mAmbientTweakConstant;
    float mAmbientTweakRadius;
    float mAmbientVehicleModifier;
    padding[0xC];
    float refl_bk_color[4];
    float refl_dist;
    padding[0x4];
    float light_glow_intensity;
    float light_glow_size;
    u8    flare_on;
    padding[3];
    float flare_intensiry;
    float flare_size;
    float flare_distance;
    padding[0x10];
    float mAmbientLight[4];
    float mDirectionalLightColor[4];
    float mDirectionalRot;
    float mDirectionalElevation;
    padding[0x4];
    float m_skylightRot;
    float m_skylightElevation;
    u8    m_drawSkylightPos;
    padding[3];
    padding[0x8];
    float m_skylightColor[4];
    u8    m_useSkylight;
    padding[3];
    float m_staticLightYOffset;
    float m_staticLightColor[3];
    float m_ambientScale;
    float m_roadShiniess;
    padding[0x4];
    float m_reflectedObjectColorMod[4];
    float m_reflectionWidthMod;
    float m_reflectionHeightMod;
    float m_globalLightMod;
    float mLightAttenuation;
    float mLightFalloff;
    float Brightness;
    float Hue;
    float Saturation;
    float Value;
    padding[0xC];
};

//
// +0x2C AND +0x30 ARE THE TWO 256-COLOUR PALETTES, AND THEY LIVE INSIDE
// mcCity ITSELF.
//
//   +0x2C -> mcCity+0x0290   LIVE palette, the one the renderer uses
//   +0x30 -> mcCity+0x1290   SOURCE palette, the one that came from the file
//
// 256 entries of 16 bytes each (Vector4 with a separate w), 4096 bytes per
// palette - which is why the two pointers sit exactly 0x1000 apart. In the
// FILE the two are identical; in RAM the live one is tinted.
//
// What regenerates it is mcCity::ChangePaletteSaturation() (0x00257770):
//
//   for i in 0..255:                         // slti s2, 0x100
//       hsv = RGBtoHSV(source[i])            // 0x00244C20
//       hsv.h *= condition_vars.Hue          // mcCity+0x178
//       hsv.s *= condition_vars.Saturation   // mcCity+0x17C
//       hsv.v *= condition_vars.Value        // mcCity+0x180
//       clamp each channel to 1.0
//       live[i] = HSVtoRGB(hsv)              // 0x00244D38
//       live[i].w = source[i].w
//
// The three names are literals, taken from mcConditionVariables::FileIO with
// mc3_parser: +0x134 Brightness, +0x138 Hue, +0x13C Saturation, +0x140 Value.
// In the file the three multipliers are 1.0, which is why the two palettes
// come out equal.
//
// CORRECTION: I had labelled mcCity+0x290 as "ambient_rgb". It was not an
// ambient colour - it was entry 0 of the live palette.

struct PaletteEntry {
    float r;
    float g;
    float b;
    float w;
} [[static]];
// =====================================================================
// STAR FIELD  -  mcStarField, hanging off the sky at +0x94
// =====================================================================
//
// The two serialised fields are the ones mcStarField::FileIO (0x0025CAF0)
// registers, and the disassembly gives the offsets with no room for doubt:
//
//     addiu a3, s1, 6084    ; 0x17C4  m_sizeMod    type 6
//     addiu a3, s1, 6096    ; 0x17D0  m_colorMod   type 9
//
// In RAM: m_sizeMod is ONE float (0.5258 in sd, 0.5280 in atlanta) and
// m_colorMod is FOUR - (1, 1, 1, 0.4102) in sd, (1, 1, 1, 0.5874) in atlanta -
// with CD right after the four, which settles the size of both fields.
//
// And the stars themselves start at +0x40: 16-byte records with a UNIT
// DIRECTION of three floats plus a size. There are 110, and all 110 have length
// 1.000 and the same size 0.0025 - in BOTH cities. After the hundred and tenth
// comes CD up to m_sizeMod, so the object has room for ~376 and uses 110.
struct Star {
    float direction[3];   // unit length
    float size;           // 0.0025 in all 110
};

struct StarField {
    u32 vtable [[format("vt")]];
    u32 zero_04;
    u32 zero_08;

    Star    star[110]     @ $ + 0x34;      // +0x40
    float   sizeMod       @ $ + 0x17B8;    // +0x17C4
    float   colorMod[4]   @ $ + 0x17C4;    // +0x17D0
};

struct StarFieldPtr {
    u32 address [[format("fmt_ptr")]];

    if (address != 0) {
        StarField field @ ptr(address);
    }
};


// =====================================================================
// CullableClass  -  mcCity+0x190, +0x194 and +0x198
// =====================================================================
//
// Three records of the same shape, 0x40 bytes each, one per CLASS of
// drawable object. The vtables say which is which:
//
//   +0x190 -> 00625268  mcSkyHatClass
//   +0x194 -> 006250F0  mcCityModelClass
//   +0x198 -> 006251E8  mcInstCityModelClass
//
// The destructor's mangled name confirms the template:
//   CullableClass<mcInstCityModelClass, mcCullableClass, 79,
//                 mcCullableClassOrder, 2>
//
// and the methods that matter are mcCullableClass::RenderAllTypes(mcPassTypes)
// at +0x34, with SetRenderStates(mcPassTypes) at +0x0C and RestoreRenderStates
// at +0x10 - so per-class drawing is prepare, draw them all, restore.
//
// +0x04 and +0x08 are the HEAD and TAIL of a chain of TYPES, linked through the
// node's +0x0C. Measured on atlanta: 651 nodes in +0x194 (one mcCityModelType
// per unique component) and 231 in +0x198 (one mcInstCityModelType per
// instance type). The sky one has both null.
//
// +0x1C IS THE CLASS'S INSTANCE COUNT, and the exact value has an explanation:
// mcInstCityModel derives from mcCityModel, so the base class counts both.
//   atlanta    651 unique + 3608 inst -> +0x194 = 4260, +0x198 = 3609
//   San Diego  628 unique + 5008 inst -> +0x194 = 5637, +0x198 = 5009
// both are sum + 1 in every case.
//
// +0x00 is 78 in the two city classes and 2 in the sky, the same in both
// cities measured - it is a constant of the class, not of the content.

struct CullableClass {
    u32 type_count;                  // +0x00  78 / 78 / 2
    u32 first_type [[format("fmt_ptr")]];// +0x04  head of the chain, walks by +0x0C
    u32 last_type  [[format("fmt_ptr")]];// +0x08  tail
    u8  unk_0c;                      // 1
    padding[3];
    u32 unk_10;
    u16 unk_14;
    u16 unk_16;
    u32 vtable     [[format("vt")]]; // +0x18
    u32 instance_count;              // +0x1C  sum of the instances + 1
    u32 zero_20;
    u32 vtable2    [[format("vt")]]; // +0x24  only the sky has it (its parFileIO)
    u32 name       [[format("fmt_ptr")]]; // +0x28  only the sky has it

    // THE NAME OF THE LOADED VARIANT: "skyhat_sd_dusk_clear",
    // "skyhat_atlanta_dusk_clear". It says which of the 18 weather .pck is in
    // memory, which matters because the game can randomise the weather - and
    // without it you can end up diffing against the wrong file. See the sky
    // note.
    if (name != 0) {
        std::string::NullString name_text @ ptr(name);
    }
    // The star field hangs off +0x94, only on the sky one.
    if (vtable == 0x00625268) {
        StarFieldPtr stars @ $ + 0x68;   // cursor at +0x2C, so +0x94
    }
};

//
// +0x19C IS THE FRAME'S CAMERA MATRIX, and +0x1A0 is the visible buffer.
//
// mcCity::ModelDraw(Matrix34 const*) (0x00257AC8) stores the argument as the
// first thing it does:
//     move s0, a0            ; this
//     move s1, a1            ; the matrix
//     sw   s1, 0x19c(s0)
//
// Zero in the file in the three cities - it only exists at runtime. The mcCity
// constructor even relocates the field (0x002593F0, `if (+0x19C) +0x19C +=
// delta`), but since it is null in the file, that is a dead path.
//
// The reader is mcSkyHatClass::RenderAllTypes(mcPassTypes) (0x0025BFA0),
// twice. The second one is the one that matters:
//
//     jal  mcSkyHatClass::Draw          // 0x0025C238
//     a0 = mcCity->cell_index
//     if (!a0) leave
//     a2 = mcCity->0x19C
//     a1 = mcCity->0x1A0
//     jal  0x0025A5A0
//     a2 = a2 + 0x24                    // the matrix TRANSLATION
//
// that is: (per-cell index, which buffer, camera position) -> 0x0025A5A0.
// That is the routine that fills the visible list at cell_index+0x64 and +0x84.
//
// Checked on the San Diego dump: the 12 floats at 0x01727300 form a Matrix34
// with unit rows and translation (835.25, 2.073, 232.22) - the SAME position
// that is at 0x70000640 in the EE scratchpad.

struct Matrix34 {
    float a[3];
    float b[3];
    float c[3];
    float t[3];        // +0x24  the translation: the camera position
};

//
// +0x1A4, +0x1B0 and +0x1BC ARE THE GLOBAL LIGHTING, and the names come from
// the very text parser that fills them.
//
// mcCity::InitGlobalLighting(char const*) (0x00258E18) builds a file name
// with "%s_%s_%s", opens it, and looks for the tokens:
//
//     global_lighting
//       ambient     { color: ... }
//       directional { color: ...  direction: ... }
//
// and writes, in this order, to this+0x1A4, this+0x1B0 and this+0x1BC. If the
// file does not open, the three become 0.1 / 0.1 / 0.1 and zeros - the
// `lui at, 0x3dcc; ori at, 0xcccd` at 0x00258E80 is the literal 0.1.
//
// Zero in the .pck file in the three cities: this is loaded from outside, from
// the city's condition text, it does not come in the pck.
//
// CHECKED in San Diego, and the set is coherent on its own:
//     ambient      (0.298, 0.302, 0.510)   bluish
//     directional  (0.930, 0.381, 0.242)   warm orange, a sun
//     direction    (0.830, -0.104, -0.549) length EXACTLY 1.0,
//                                          pointing almost horizontally

//
// +0x1D0 IS THE CITY'S ENVIRONMENT MAP: mcCityEnvMap*.
//
// Vtable 00634F78 has mcCityEnvMap::~mcCityEnvMap at +0x08, and the rest of
// the block is parFileIO (Save/Load/FileIO/AfterLoad/GetDirName/GetTypeName) -
// one more class serialised by the datParser.
//
// It is not in the constructor's relocation list and it is ZERO in the file:
// it is created at runtime. Through the global, 26 places read it, and in the
// city module there are 32 accesses, almost all inside mcCullableMgr::Render
// (0x0024A860) - the map is prepared during drawing.
//
// Checked on the San Diego dump (0x013C0EC0):
//     +0x04 -> "sd_dusk_clear"        the name of the current condition
//     +0x20/+0x24 = 256 x 256         the resolution
//     +0x28, +0x2C  rmcRenderTargetPS2 (two)
//     +0x30, +0x34  rmcTexturePS2      (two)
// two render targets and two textures is double buffering: it draws into one
// while sampling the other.

struct CityEnvMap {
    u32   vtable [[format("vt")]];
    u32   condition [[format("fmt_ptr")]];  // -> "sd_dusk_clear"
    u32   unk_08;
    u32   unk_0c;
    float unk_10;                           // 0.25
    float unk_14;                           // 0.5
    u32   unk_18;                           // 2
    u32   unk_1c;
    u32   width;                            // 256
    u32   height;                           // 256
    u32   render_target[2] [[format("fmt_ptr")]];
    u32   texture[2]       [[format("fmt_ptr")]];
    u32   unk_38[2]        [[format("fmt_ptr")]];
};

//
// +0x1D4 IS A TEXTURE AND +0x1D8 ARE TWENTY MORE. This is where the city
// texture comes in.
//
// In the mcCity constructor:
//     lw    a2, 0x1d4(this)          if null, skip
//     lw    a0, [0x0070E56C]         the global rmcTextureFactoryPS2
//     addiu a2, this, 0x1d4          &this->0x1D4, a pointer TO the pointer
//     lw    v0, 0x24(vtable)
//     jalr  v0                       rmcTextureFactoryPS2::ResourcePageIn(
//     addiu a3, zero, 1                  datResource&, rmcTexture**, int)
//
// The method name is a literal from MC.MAP, and the `rmcTexture**` explains
// the `addiu a2, this, 0x1d4`: the factory rewrites the field with the paged
// texture.
//
// A FREE SIZE CONFIRMATION: +0x1D4 points to exactly mcCity + 0x2290, in both
// file cities and in RAM. So the texture lives right at the end of mcCity -
// the same proof, by another route, that the struct ends there.
//
// How many of the 20 slots are in use:
//     atlanta (file)      4      detroit (file)     4
//     San Diego (RAM)    11      - ten rmcTexturePS2 and one rmcTextureReference
// more in RAM than in the file, so part of it is filled at load.

//
// +0x228 IS THE CITY'S rmcTextureProxyPS2 - a thumbnail of EVERY texture.
//
//   0x002BA6B0  rmcTextureProxyPS2::rmcTextureProxyPS2(datResource&)  fixup
//   0x002BA240  rmcTextureProxyPS2::Bind(int, unsigned long)
//   0x002BA1A8  rmcTextureProxyPS2::~rmcTextureProxyPS2   (called with flag 3)
//
// Object layout:
//   +0x00  u16 n, u16 n        how many textures (the same value twice)
//   +0x04  ptr  thumbnails     n * 128 bytes
//   +0x08  ptr  palettes       n * 64 bytes
//   +0x0C  ptr  u32[n]         RUNTIME STATE (CD in the file)
//   +0x10  ptr  u16[n]         RUNTIME STATE (CD in the file)
//
// THE NUMBERS ADD UP EXACTLY IN THE THREE CITIES MEASURED:
//   atlanta  838 textures   107264 = 128.0/tex    53632 = 64.0/tex
//   tokyo    626 textures    80128 = 128.0/tex    40064 = 64.0/tex
//   detroit  930 textures   119040 = 128.0/tex    59520 = 64.0/tex
//   San Diego 848 (in RAM)
// 64 bytes of palette are 16 RGBA entries, which forces 4 bits per pixel, and
// 128 bytes at 4 bits are 256 pixels: 16x16. One thumbnail per texture.
//
// HOW THE RIGHT THUMBNAIL IS REACHED - found by the textures fork, checked
// here. rmcTexturePS2::Bind (0x002B8768) takes the ID from its own TEX1:
//
//     ld    v1, 0x88(tex)            ; TEX1
//     and   v0, v1, 0xFFE00000       ; has an ID?
//     dsll  a1, v1, 11
//     dsra32 a1, a1, 0
//     lbu   a2, 0x85(tex)            ; clamp
//     jal   rmcTextureProxyPS2::Bind
//     andi  a1, a1, 0x7ff            ; 11 bits, 1-BASED
//
// and the proxy's Bind does id = a1 - 1 and indexes +0x0C as u32[id], +0x10 as
// u16[id], +0x04 in blocks of 128 and +0x08 in blocks of 64.
//
// The same 0xFFE00000 mask shows up in the bind-mode path of mcCity+0x28 -
// it is the "does this texture have a proxy" test.

struct TextureProxy {
    u16 count;
    u16 count2;
    u32 indices  [[format("fmt_ptr")]];   // count * 128
    u32 palettes [[format("fmt_ptr")]];   // count * 64
    u32 state_u32 [[format("fmt_ptr")]];  // +0x0C u32[count], runtime (CD in the file)
    u32 state_u16 [[format("fmt_ptr")]];  // +0x10 u16[count], runtime (CD in the file)
};

//
// +0x22C AND +0x230 ARE DEAD SENTINELS IN THIS BUILD.
//
// Both are -999 (FFFFFC19), in the file AND in RAM, in the four cities. And
// nobody consumes them:
//   - the mcCity constructor touches 17 offsets and neither is on the list;
//   - through the global pointer, no access;
//   - in the whole city module, no access (the only swc1 at 0x230 the sweep
//     finds is in mcGlow::mcGlow, on the mcGlow object, not on mcCity);
//   - and the ELF only materialises the constant -999 in ONE place in the
//     whole game, ioMemCard::UpdateIsDataInited, which has nothing to do with
//     the city.
//
// The difference from +0x1C8, which is also a hole: that one is zero and
// nobody ever wrote it; these two were WRITTEN ON PURPOSE by the exporter, with
// an impossible value, and then nobody reads them. It smells of a field only
// the PC build, the Xbox build or the editor uses.

//
// +0x234 IS ANOTHER HOLE, of the same kind as +0x1C8: a byte 0 with the
// CD CD CD filler next to it - the signature of a bool the exporter wrote as
// false - and nobody reads it. Zero in the file and in RAM in the four cities.
//
// I swept the whole ELF: 22 instructions use offset 0x234 and 14 of them have
// a base that touches other "mcCity" offsets, but NONE of those functions loads
// the global pointer - they are CareerHudScreen::CareerHudScreen,
// CareerHudScreen::SetActive, mcRaceHookmen::DrawCheckpointTextLabels and the
// like, each on its own structure. That is the criterion that separates them:
// whether the function loads 0x00615B40 or not.

//
// +0x27C AND +0x280 SIZE THE CITY'S STATIC PHYSICS.
//
// A single reader, mcLayerCity::InitPhys() (0x001A92F8):
//
//     a0 = &mcCity->extents_min
//     a1 = &mcCity->extents_max
//     a2 = mcCity->0x27C + 1000
//     a3 = 0
//     t0 = 96
//     t1 = mcCity->0x280 + 300
//     jal mcPhysics::SetupStaticDataPreFixup(Vector3 const&, Vector3 const&,
//                                            int, int, int, int)   0x002C2910
//
// That SetupStaticDataPreFixup only copies the extents into two globals and
// keeps the four integers at 0x0070FBFC..0x0070FC08. The consumer is
// mcPhysics::InitStaticData() (0x002C2618), which writes each one as a u16
// into a physics object:
//
//     96             -> obj+0x0C
//     +0x280 + 300   -> obj+0x44
//     0              -> obj+0x54
//     +0x27C + 1000  -> obj+0x2A
//
// They are capacities, with slack added. Measured values:
//     atlanta   +0x27C = 3    +0x280 = 4546
//     tokyo     +0x27C = 1    +0x280 = 3797
//     detroit   +0x27C = 19   +0x280 = 3613
//     San Diego +0x27C = 30   +0x280 = 3766
//
// What exactly each one counts would need the physics object decoded, and that
// has not been done yet. But the practical consequence already holds: if you
// replace a city's collision without adjusting these two, the static physics
// is sized by the OLD city's numbers.

struct City {
    u32   vtable [[format("vt")]];              // +0x000
    u8    owns_memory;                          // +0x004 see the note above
    u8    pad_005[3];                           // CD CD CD, filler
    u32   shader_group_count;                   // +0x008  15 in San Diego
    u32   shader_group [[format("fmt_ptr")]];   // +0x00C
    u32   sky_shader_group [[format("fmt_ptr")]];   // +0x010  rmcShaderGroup
                                                // of the sky (mcSkyHatClass)
    u32   hood_count;                           // +0x014  6 in San Diego
    u32   hood_array [[format("fmt_ptr")]];     // +0x018
    u32   cell_index [[format("fmt_ptr")]];     // +0x01C
    u8    uses_cell_list;                       // +0x020 see note
    u8    pad_021[3];
    float draw_dist_fallback;                   // +0x024 see note
    u8    texture_bind_mode;                    // +0x028 see note
    u8    pad_029[3];
    u32   palette_live   [[format("fmt_ptr")]];    // +0x02C -> +0x290
    u32   palette_source [[format("fmt_ptr")]];    // +0x030 -> +0x1290
    u8    unk_034[0x0C];                        // +0x034
    // +0x040  mcConditionVariables. The name comes from the constructor:
    // mcCity::mcCity does `addiu a0, s1, 0x40` and calls 0x0025D370 =
    // mcConditionVariables::mcConditionVariables(datResource&). This block
    // holds the weather and render scalars (200/300 distance, colours,
    // 0.2 / 0.45 / 0.741 ...). The exact size has not been measured yet; here
    // it runs up to where the next known field starts.
    ConditionVariables condition_vars;          // +0x040
    // All three point to objects with the SAME 0x18-byte header BEFORE the
    // vtable - checked on all three, in both cities. It is not a vptr at
    // +0x00 as usual:
    //
    //     +0x00  u32   2 in the sky, 0x4E in the two model ones
    //     +0x04  pointer, and +0x08 repeats the same one
    //     +0x0C  u8 1
    //     +0x18  THE VTABLE
    //     +0x1C  HOW MANY OBJECTS OF THE CLASS EXIST
    //
    //   atlanta   sky 1    mcCityModelClass 4260   mcInstCityModelClass 3609
    //   sd        sky 1    mcCityModelClass 5637   mcInstCityModelClass 5009
    //
    // And the sky one also has a second vtable at +0x24 (the parFileIO,
    // because mcSkyHatClass serialises) and, at +0x28, THE NAME OF THE LOADED
    // VARIANT:
    //
    //     "skyhat_sd_dusk_clear"        "skyhat_atlanta_dusk_clear"
    //
    // That is worth gold for diffing file against memory: it says exactly which
    // of the 18 weather .pck is loaded, which matters because the game can
    // randomise the weather. I lost time comparing against midnight_clear when
    // the savestate was dusk_clear.
    //
    // CHECKED by a second route, independent of the name: taking 1448 bytes
    // where the variants differ from each other and comparing with the RAM,
    // sd_dusk_clear hits 99.0% and the runner-up (sd_dawn_clear, a very similar
    // file) stays at 95.0%. The two methods agree.
    //
    // TRAP on that second route: the _fog variants have a different size and
    // therefore a shifted layout, so comparing by raw offset against them gives
    // ~0.6% and means nothing. Either compare only among the non-_fog ones, or
    // compute each one's load delta.
    u32   sky_class             [[format("fmt_ptr")]];  // +0x190 mcSkyHatClass
    u32   model_class           [[format("fmt_ptr")]];  // +0x194 mcCityModelClass
    u32   inst_model_class      [[format("fmt_ptr")]];  // +0x198 mcInstCityModelClass
    u32   camera_matrix  [[format("fmt_ptr")]];   // +0x19C the frame's Matrix34
    u32   visible_buffer;                       // +0x1A0 which visible buffer
    float ambient_color[3];                     // +0x1A4
    float directional_color[3];                 // +0x1B0
    float directional_direction[3];             // +0x1BC  unit length
    u8    unk_1c8[8];                           // +0x1C8
    u32   env_map [[format("fmt_ptr")]];        // +0x1D0 mcCityEnvMap*
    u32   texture   [[format("fmt_ptr")]];          // +0x1D4 rmcTexturePS2*
    TexturePtr textures[20];                        // +0x1D8 rmcTexture*
    u32   texture_proxy [[format("fmt_ptr")]];      // +0x228 rmcTextureProxyPS2*
    s32   sentinel_22c;                         // -999, see note
    s32   sentinel_230;                         // -999, see note
    u8    dead_234;                             // byte 0, see note
    u8    pad_235[3];                           // CD CD CD

    float extents_min[3];                       // +0x238
    float extents_max[3];                       // +0x244
    float graph_min[3];                         // +0x250  matches EXACTLY the
    float graph_max[3];                         // +0x25C  .graph MinExtents

    // CULLING GRID. Retail invariant, checked in four cities:
    //   cell_origin_x = floor(extents_min.x / 40)
    //   grid_w        = floor(extents_max.x / 40) - cell_origin_x + 1
    //   cell_count    = grid_w * grid_h
    //   atlanta 64x62=3968  tokyo 90x69=6210  detroit 96x75=7200
    //   San Diego 71x63=4473, origin -29/-30
    u32   cell_count;                           // +0x268
    s32   grid_w;                               // +0x26C
    s32   grid_h;                               // +0x270
    s32   cell_origin_x;                        // +0x274  in CELLS
    s32   cell_origin_z;                        // +0x278
    s32   physics_cap_a;                        // +0x27C  +1000 -> obj+0x2A
    s32   physics_cap_b;                        // +0x280  +300  -> obj+0x44
    u8    unk_284[0x0C];
    PaletteEntry palette_live_entries[256];     // +0x0290 .. +0x128F
    PaletteEntry palette_source_entries[256];   // +0x1290 .. +0x228F

    ShaderGroup groups[shader_group_count] @ ptr(shader_group);

    // The SKY group, which used to stay just a pointer. It is small and
    // regular, and worth opening: in sd it has 10 slots, ALL rmcShaderBasic,
    // each with its own rmcTexturePS2 - no reference, no complex. All PSMT8, a
    // single mip, and all resident. 173 KB of texture in total.
    //
    // The 10 come in TWO BLOCKS OF FIVE, and what separates them is the
    // textures' +0x7C stamp:
    //
    //     slots 0..4   stamp 00015DF3   four 256x128 and one 16x16
    //     slots 5..9   stamp 00015DF4   four 128x64  and one 16x16
    //
    // Same shape in both - four large and one tiny - at different resolutions
    // and on consecutive stamps. It looks like two sky layers, but I did not
    // confirm which is which.
    //
    // And the whole group is one compact block: header at 008C27F0, the slot
    // array right at +0x20, and from there on shader and texture alternating.
    if (sky_shader_group != 0) {
        ShaderGroup sky_group @ ptr(sky_shader_group);
    }

    Hood        hoods[hood_count]          @ ptr(hood_array);
    CellIndex   index                      @ ptr(cell_index);

    // The objects pointed to, placed in the tree. Each one only shows when the
    // pointer is not null - in the .pck several are zero and only exist at
    // runtime.
    if (sky_class != 0) {
        CullableClass sky_cls      @ ptr(sky_class);
    }
    if (model_class != 0) {
        CullableClass city_cls     @ ptr(model_class);
    }
    if (inst_model_class != 0) {
        CullableClass inst_cls     @ ptr(inst_model_class);
    }
    if (camera_matrix != 0) {
        Matrix34 camera @ ptr(camera_matrix);
    }
    if (env_map != 0) {
        CityEnvMap envmap @ ptr(env_map);
    }
    if (texture != 0) {
        TexturePS2 root_texture @ ptr(texture);   // +0x1D4, right at the end of mcCity
    }
    if (texture_proxy != 0) {
        TextureProxy proxy @ ptr(texture_proxy);
    }
    // The 20 slots at +0x1D8 open by themselves through TexturePtr, which skips
    // the null ones and dispatches on the type. How many are occupied depends
    // on the city: FOUR in atlanta and ELEVEN in sd (ten rmcTexturePS2 and one
    // reference).
    //
    // In atlanta the four live packed together in memory, each object followed
    // by its own buffers - the whole block can be walked using only
    // MipBuffer.size:
    //
    //   [0] 007F8400  128x128 PSMT4  -> 007F8500 +2100 -> 007FA600 +940 -.
    //   [1] 007FAF40  128x128 PSMT4  -> 007FB000 +2100 -> 007FD100 +940 -.
    //   [2] 007FDA40   64x64  PSMT8  -> 007FDB00 +1500 ------------------.
    //   [3] 007FF000  rmcTextureReference "none" -> 00721070
    //
    // And the block starts BEFORE the array: the root texture at 007E7E10
    // (256x256 PSMT8) has its buffer at 007E7F00 with 0x10500, which ends
    // exactly at 007F8400, slot 0. After slot 3 the chain ENDS IN ATLANTA:
    // 007FF010 there is heap leftovers. Not in sd - there slot 4 sits exactly at
    // 007FF010, right after the reference's 0x10 bytes, and the chain goes on
    // to slot 10. The first four slots are the SAME addresses in both cities.
    //
    // To see the full header of one of these rmcTexturePS2 with the pixels and
    // the CLUT, use LOOSE_TEXTURE at the end.

    if (EXPAND_CELLS) {
        CellRecord   cells[cell_count] @ ptr(std::mem::read_unsigned(ptr(cell_index) + 0x10, 4));
        CellExtraPtr extra_per_cell[cell_count] @ ptr(std::mem::read_unsigned(ptr(cell_index) + 0x14, 4));
    }
};


// =====================================================================
// GLOW GRID (only exists in RAM - allocated at 0x2594B0)
// =====================================================================
//
// Global object at 0x0070FA4C. `+0x00` is an array of cell_count pointers and
// each cell is a singly linked list through the inserted object's `+0x0C`. The
// constructor creates fx_glowstar, fx_glowcone and fxltglow, so this grid is
// for lights, not for the city geometry.
//
// CellGrid::Insert (0x259658): index = CellIndexFromPos(city, obj), and if it
// gives -1 it returns without inserting. An inserted object keeps the cell
// index at `+0x20` (u16) and has bit0 of `+0x22` as a flag.

struct GlowGrid {
    u32 cells [[format("fmt_ptr")]];      // u32[cell_count]
    u32 fx_glowstar [[format("fmt_ptr")]];
    u32 unk_08 [[format("fmt_ptr")]];
    u32 fx_glowcone [[format("fmt_ptr")]];
    u32 fxltglow [[format("fmt_ptr")]];
};


// =====================================================================
// INSPECTION WINDOW  -  for looking at a loose address
// =====================================================================
//
// Change the two values and ImHex marks the range in the tree and in the hex
// view. It works for any address: not everything in memory belongs to a known
// struct, and sometimes what helps is just seeing the bytes with a label next
// to them.
//
// The default points to 0x007E7EA0..0x007E7F00 of the San Diego save, which is
// a good example of a range that is NOT a single structure. Packed there:
//
//   007E7EA0  the last 16 bytes of the root texture (the zeroed tail, +0x90)
//   007E7EB0  the string "none", 8 bytes
//   007E7EB8  SIX 12-byte rmcShaderBasic, packed: EB8, EC4, ED0, EDC,
//             EE8, EF4. Each one is [vtable 00626E70][0][texture pointer],
//             and the first IS slot 0 of shader group 0 - checked.
//             The textures they point to: 00819100, 008191A0, 00819240,
//             00819250, 008192F0, 00819390, mixing rmcTexturePS2 and
//             rmcTextureReference.
//   007E7F00  the root texture's MipBuffer, which is its MipData[0].buffer
//
// The shaders already show up through the normal tree - turn on
// EXPAND_SHADERS and look at group 0, slot 0, which is exactly the one at
// 007E7EB8. The MipBuffer shows up inside the root texture. Only the string at
// +0xA0 hangs off nothing.

u32  INSPECT_ADDRESS = 0x007E7EA0;
u32  INSPECT_SIZE    = 0x60;

struct Window {
    u8 bytes[INSPECT_SIZE];
};

// =====================================================================
// TWO LOOSE TEXTURES  -  the twins that gave the GS address away
// =====================================================================
//
// The texture chain in memory does not all go through mcCity, so the normal
// tree does not reach a good part of it. Put the address here and the struct
// opens whole, with the mips, the pixels and the CLUT.
//
// The two defaults are heap neighbours and they are TWINS - 128x128 PSMT4, 2
// mips, same TEX1, same +0x80, same stamp 0x81CB. They are the ones that prove
// the upper u16 of MipDesc is a GS address, because it is the ONLY field that
// differs along with the CBP, and the sum adds up in both:
//
//     007F8400   level1 tbp = 262E   +8 blocks = 2636 = CBP
//     007FAF40   level1 tbp = 2639   +8 blocks = 2641 = CBP
//
// And they are the opposite of the root texture in almost everything, so they
// test the other branch of everything that was pinned down:
//
//   root  007E7EA0   256x256  PSMT8   1 mip    CLUT of 256   +0x84 = 1
//   these 007F8400   128x128  PSMT4   2 mips   CLUT of  16   +0x84 = 0
//
// The one at 007F8400 opens like this, and passes:
//
//   level 0  buffer 007F8500, pixels 007F8590, 8192 bytes (128*128/2),
//            NO palette at the end - only CD filler, because it is not the last
//   level 1  buffer 007FA600, pixels 007FA690, 2048 bytes,
//            and then the CLUT, at 007FAE90, SIXTEEN 4-byte entries
//
// The palette is all black with varying alpha (06, 21, 34, 16, 53, ...): it is
// a mask, not a colour.
//
// To see the opposite - a texture that only has a header - use 00817930. It is
// the first texture of the mcShaderCityWindow at 00817910, 64x64 PSMT8 with 3
// mips, and everything in it says "I am nowhere": tbp = 0 on the three levels,
// level[].buffer = 0 on all four, stamp = 80000000 (bit 31 = no GS) and
// cbp = CDCD. Both halves of the +0x7C/+0x82 rule agreeing on the same object.
//
// Note that you can walk from one to the other using only MipBuffer.size:
// 007F8500 + 0x2100 = 007FA600, and 007FA600 + 0x940 = 007FAF40.
//
// To turn either one off, set it to 0.

u32 LOOSE_TEXTURE   = 0x007F8400;
u32 LOOSE_TEXTURE_2 = 0x007FAF40;

// =====================================================================
// LOOK AT AN ADDRESS  -  the memory-search tool
// =====================================================================
//
// Put the address in one of the EIGHT slots below and ImHex opens the object
// that is there, ALREADY DECODED, choosing the struct by the vtable. It is for
// the addresses that do not hang off the city tree - which are most of them:
// window shader, neighbourhood texture, loose reference, whatever the memory
// search brings up.
//
// What it recognises today:
//
//   00627280  rmcTexturePS2         opens whole, with mips, pixels and CLUT
//   00627040  rmcTextureReference   with the name and the target's vtable
//   00626FC8  the reference's other vtable
//   00635C18  mcShaderCityWindow    with BOTH textures and the colour
//   00626E70  rmcShaderBasic
//   00626EC8  rmcShaderComplex
//   00626F30  rmcShaderInstance      -  all open through the same Shader
//   00635BC0  rmcShaderInstance (2)
//   00635B10  mcShaderTexAnimation
//
// Those six cover atlanta's 4105 filled slots WITH NOTHING LEFT OVER:
// rmcShaderBasic 1951, mcShaderCityWindow 1151, rmcShaderInstance 533, its
// second vtable 434, rmcShaderComplex 18 and mcShaderTexAnimation 18.
//
// A vtable it does not know is no problem: it falls into the raw branch and
// shows 0x40 bytes with the vtable name next to them, which is already enough
// to know whether it is worth going on. A slot at 0 stays off.
//
// THE DEFAULTS ARE FROM THE sd DUMP (savestate .04). They do not hold in
// atlanta - there 0081DD90 lands in the middle of group 4's slot array and is
// no object at all.
//
//   0081DD90  the 30-frame, 10 fps mcShaderTexAnimation. Opening it you see the
//             whole strip: frame 0 is a reference to the base texture that sits
//             0xA0 before, and the next 29 are rmcTexturePS2 packed every 0xA0,
//             from 0081DEE0 to 0081F060.
//   007FF000  the rmcTextureReference of the city's slot 3, named "none".
//             This one exists in BOTH cities, at the same address.
//
// If you are on the atlanta dump, two addresses that pay off: 00817910, an
// mcShaderCityWindow whose first texture is its own address + 0x20, and
// 00817930, that texture, which is the "header only" case - tbp = 0, no
// pixels, stamp 80000000, cbp CDCD.

u32 LOOK_1 = 0x00917590;   // the rmcModelGeom s_be_blk_01x#geom
                           // (0x009175E8 = the pair right after group_table)
u32 LOOK_2 = 0x007FF000;
u32 LOOK_3 = 0;
u32 LOOK_4 = 0;
u32 LOOK_5 = 0;
u32 LOOK_6 = 0;
u32 LOOK_7 = 0;
u32 LOOK_8 = 0;

struct AnyObject {
    u32 vtable [[format("vt")]];

    if (vtable == 0x00627280) {
        TexturePS2 texture @ $ - 4;
    }
    if (vtable == 0x00627040 || vtable == 0x00626FC8) {
        TextureReference reference @ $ - 4;
    }
    if (vtable == 0x00635C18 || vtable == 0x00626E70 || vtable == 0x00626EC8 ||
        vtable == 0x00626F30 || vtable == 0x00635BC0 || vtable == 0x00635B10) {
        Shader shader @ $ - 4;
    }
    if (vtable != 0x00627280 && vtable != 0x00627040 && vtable != 0x00626FC8 &&
        vtable != 0x00635C18 && vtable != 0x00626E70 && vtable != 0x00626EC8 &&
        vtable != 0x00626F30 && vtable != 0x00635BC0 && vtable != 0x00635B10) {
        u8 raw[0x3C];
    }
};

// =====================================================================

City     city      @ MCCITY;
GlowGrid glow      @ ptr(std::mem::read_unsigned(0x0070FA4C, 4));

Window   window    @ INSPECT_ADDRESS;

if (LOOSE_TEXTURE != 0) {
    TexturePS2 loose_texture @ LOOSE_TEXTURE;
}
if (LOOSE_TEXTURE_2 != 0) {
    TexturePS2 loose_texture_2 @ LOOSE_TEXTURE_2;
}

if (LOOK_1 != 0) {
    AnyObject look_1 @ LOOK_1;
}

if (LOOK_2 != 0) {
    AnyObject look_2 @ LOOK_2;
}

if (LOOK_3 != 0) {
    AnyObject look_3 @ LOOK_3;
}

if (LOOK_4 != 0) {
    AnyObject look_4 @ LOOK_4;
}

if (LOOK_5 != 0) {
    AnyObject look_5 @ LOOK_5;
}

if (LOOK_6 != 0) {
    AnyObject look_6 @ LOOK_6;
}

if (LOOK_7 != 0) {
    AnyObject look_7 @ LOOK_7;
}

if (LOOK_8 != 0) {
    AnyObject look_8 @ LOOK_8;
}
