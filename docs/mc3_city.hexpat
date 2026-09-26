#pragma description Midnight Club 3 (PS2) - city / prop / bound .pck
#pragma endian little
#pragma pattern_limit 4000000
#pragma array_limit 200000

import std.core;
import std.mem;
import std.string;

// =====================================================================
// WHAT A .pck IS
// =====================================================================
//
// It is not a data file: it is a MEMORY IMAGE. The game reads the whole
// block to a fixed address and starts using it - "load in place". That is
// why every pointer stored inside it is a PS2 VIRTUAL ADDRESS, not an
// offset, and the conversion is always the same:
//
//     file_offset = VA - base_va + 0x80
//
// `base_va` is in the first 4 bytes (0x06800000 in everything measured)
// and the 0x80 is the header plus the padding before the body.
//
// Practical consequence: NOTHING can move. Inserting bytes in the middle
// invalidates every pointer after it. Every safe edit is "append at the end
// and repoint", which is what mc3_hood_fill does.
//
// CEILINGS MEASURED IN GAME (city):
//   12.08 MB loads.  13.88 MB hangs BEFORE reaching RAM.
//   The instanced shard path (mcInstBucket::Draw 0x1C0178 plus the VU1
//   microcode) has a ceiling of 72 adjuncts - it does not apply to the city.
// =====================================================================

bool EXPAND_COMPONENTS = true;
bool EXPAND_INSTANCES  = false;   // 3608 records: turn on with patience
bool EXPAND_MESHES     = false;   // follows ComponentData -> mesh -> groups
// OFF by default on purpose: the one-byte-per-vertex vectors are 37023
// arrays and 1.03 MB, and on their own they blow the 8 M pattern_limit. Turn
// on when looking at the +0x14 chain, with EXPAND_PIXELS off.
bool EXPAND_VERTEX_BYTES = false;
bool EXPAND_SHADERS    = false;
bool EXPAND_CELLS      = false;   // one CellRecord per cell
bool EXPAND_PIXELS     = true;    // the header and the pixels of each level   // 18 groups x ~1300 slots

u32 BASE_VA = std::mem::read_unsigned(0x00, 4);

fn off(u128 va) {
    if (va == 0) {
        return 0;
    }
    return va - BASE_VA + 0x80;
};

fn fmt_va(u32 va) {
    if (va == 0) {
        return "null";
    }
    return std::format("VA {:08X} -> file {:08X}", va, va - BASE_VA + 0x80);
};

// =====================================================================
// HEADER
// =====================================================================

enum PckType : u32 {
    Flash  = 6,
    Bound  = 9,
    Prop   = 64,     // resources/prop/<city>_<cond>_props.pck
    City   = 67      // resources/city/<city>_<cond>.pck
};

struct PckHeader {
    u32      base_va;          // 0x06800000
    PckType  type;
    u32      version;          // 1
    u32      payload_size;     // == file size minus 0x80
    padding[0x70];             // always zero
};

// =====================================================================
// TILE BOX  (4 bytes, in components and in instances)
// =====================================================================
//
// The box of cells the object occupies, RELATIVE to the origin at
// MapRoot+0x274/+0x278. Cell = 40 world units (global float at
// 0x00615B44 in the retail ELF).
//
//   mcCity::CalcAbsCellXZ    0x257CC8  -> floor(x/40), floor(z/40), no origin
//   mcCity::CalcRelCellXZ    0x257C60  -> the same, minus +0x274 / +0x278
//   mcCity::CellIndexFromPos 0x257BE8  -> validates and returns rel_z * W + rel_x
//
// THE RULE THAT KILLS: 0x257BE8 returns -1 when rel_x < 0, rel_x >= W,
// rel_z < 0 or rel_z >= H, and the caller simply DOES NOT INSERT the object
// (CellGrid::Insert 0x259658 returns without doing anything). An object
// outside the grid vanishes, with no warning and no crash.

struct TileBox {
    u8 x0;
    u8 x1;
    u8 z0;
    u8 z1;
} [[static]];

// =====================================================================
// MESH = rmcModelGeom
// =====================================================================
//
// vtable 00626D98: rmcModelGeom::Draw(int) const, ::DrawCpv(int, int) const,
// ::DrawSkinned(int, Matrix34 const*, int) const and
// ::Load(mshMesh&, bool, char const*) - the last one is the bridge to the
// text .mod reader (mshMesh::LoadMod, 0x5555A0). Inherits from rmcModel.

struct BlockEntry {
    u32 packet_va [[format("fmt_va")]];
    u16 qwc;                   // packet size in quadwords (qwc * 16 bytes)
    u16 vertex_count;          // <= 48 in retail
};

struct GroupEntry {
    u32 list_va [[format("fmt_va")]];
    u16 count;                 // how many BlockEntry the list has
    u16 pad;                   // CD CD (uninitialised) in retail

    if (EXPAND_MESHES && list_va != 0 && count > 0 && count <= 4096) {
        BlockEntry blocks[count] @ off(list_va);
    }
};

// A city packet is a VIF chain ready for DMA. Measured:
//
//   +00  VIFcode UNPACK S-32  num=2  addr=158 (buffer A) / 453 (buffer B)
//   +04  float   position scale (1/128 typical)
//   +08  u32     vertex count
//   +0C  VIFcode UNPACK V3-16 num=n  addr=256 (A) / 551 (B)  <- positions
//        n * 3 * s16, each component = value * scale
//        UNPACK V2-16 num=n addr=208 (A) / 503 (B)           <- UV (u,v * 4096)
//        [optional] UNPACK V4-8 at ITOP+2                    <- normal / light
//   16-byte tail: STMASK C0C0C0C0 | ITOP 158 or 453 | MSCAL 6
//
// "buffer A / buffer B" is NOT triangle parity: it is which half of the VU1
// double buffer the block uses, and the two halves sit 295 qwords apart. It
// alternates starting from block 0 OF EACH GROUP. Copying the value from the
// source block breaks the alternation when the list changes size, the DMA
// overwrites the half the VU is still running, and the game hangs.

// rmcModelGeom
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
    u32 address [[format("fmt_va")]];
    if (EXPAND_VERTEX_BYTES && address != 0) {
        u8 index[parent.counts_rec.vertices[std::core::array_index()]] @ off(address);
    }
};

struct EntryPerGroup {
    u32 counts  [[format("fmt_va")]];
    u32 vectors [[format("fmt_va")]];

    if (EXPAND_MESHES && counts != 0) {
        CountsPerBlock counts_rec @ off(counts);
        if (vectors != 0 && counts_rec.nblocks > 0 && counts_rec.nblocks <= 4096) {
            ByteVector vector[counts_rec.nblocks] @ off(vectors);
        }
    }
};

struct VertexSlot {
    u32 address [[format("fmt_va")]];
    if (EXPAND_MESHES && address != 0) {
        EntryPerGroup group[parent.group_count] @ off(address);
    }
};

struct Mesh {
    u32 vtable [[format("fmt_va")]];   // address in the ELF, not in the pck
    u32 unk_04;
    u16 group_count;                   // +0x08  zeroing it erases the mesh
    u16 slot_count;                    // +0x0A  slots of the +0x14 chain
    u32 material_table_va [[format("fmt_va")]];  // +0x0C u16[group_count]
    u32 group_table_va    [[format("fmt_va")]];  // +0x10 GroupEntry[group_count]
    u32 per_block_va      [[format("fmt_va")]];  // +0x14 per-vertex payload
    u32 unk_18 [[format("fmt_va")]];
    u32 unk_1c [[format("fmt_va")]];

    if (EXPAND_MESHES && group_count > 0 && group_count <= 64) {
        u16        material_index[group_count] @ off(material_table_va);
        GroupEntry groups[group_count]         @ off(group_table_va);
    }
    if (EXPAND_MESHES && per_block_va != 0 && slot_count > 0 && slot_count <= 4096) {
        VertexSlot slot[slot_count] @ off(per_block_va);
    }
};

// =====================================================================
// ComponentData = mcCityModelType  /  mcInstCityModelType
// =====================================================================
//
// Name taken from the vtable, not guessed: the file's token 0079FF88 becomes
// 00625098 in RAM, and 00625098+0x08 is mcCityModelType::~mcCityModelType.
// Instances use another type, token 007A0090 -> vtable 00625190 =
// mcInstCityModelType. Both share
// mcCityModelType::LoadTypeData(char const*, char const*) at +0x0C.
// See mc3_vtables.py.
//
// This is where the culling sphere and the ten LOD slots live. A slot is NOT
// a list: it is a LEVEL. In retail atlanta, 236 components use only slot 0,
// 128 use only slot 1, 66 use (0,3). An empty slot in the middle is normal.
//
// CAREFUL when emptying a LOD: leaving a slot with a zero group count made
// PCSX2 abort with "FQC = 0 on VIF FIFO READ" - something picks that LOD and
// reads a FIFO nobody filled. A real block is cheap.

// mcCityModelType
struct ComponentData {
    u32   unk_00;                        // 1 in retail
    u32   unk_04 [[format("fmt_va")]];
    u32   unk_08 [[format("fmt_va")]];
    u32   chain_next [[format("fmt_va")]];      // +0x0C list of ComponentData
    u32   unk_10 [[format("fmt_va")]];
    u32   unk_14;
    u32   owner_array_va [[format("fmt_va")]];  // +0x18 array of the owning hood
    u8    unk_1c[0x0C];                  // CD CD ... (uninitialised)
    u32   type_va [[format("fmt_va")]];  // +0x28 type object, in the ELF
    float centre_x;                      // +0x2C SPHERE CENTRE, IN WORLD SPACE
    float centre_y;
    float centre_z;
    float radius;                        // +0x38 sphere radius
    u32   lod[10] [[format("fmt_va")]];  // +0x3C .. +0x60  Mesh per level
    u32   unk_64 [[format("fmt_va")]];
    u32   unk_68 [[format("fmt_va")]];
    u32   unk_6c [[format("fmt_va")]];
    char  name[20];                      // +0x70 e.g. "a_bh_blk_02x#geom"
    u32   unk_84;
    u32   unk_88;
    u32   unk_8c;

    // 128 of atlanta's 651 components have slot 0 empty and only slot 1:
    // following lod[0] without checking would put a Mesh at offset zero.
    if (EXPAND_MESHES && lod[0] != 0) {
        Mesh lod0 @ off(lod[0]);
    }
};

// =====================================================================
// UNIQUE COMPONENT = mcCityModel  (28 bytes)
// =====================================================================
//
// vtable 006250C0. Methods that give the fields their meaning:
//   +0x10 mcCullable::GetCullCenter()      <- that is why there is a centre
//   +0x18 mcCityModel::Render(mcPassTypes) <- the "passes" are the 18 groups
//   +0x1C mcCullable::CalcLOD()            <- what picks among the 10 slots
//   +0x24 mcCityModel::AddAreaLight()
//   +0x28 mcCityModel::InitExtents(Vector3 const&, Vector3 const&)

// mcCityModel
// The u32 at +0x08 is NOT an opaque flags word. It is four bytes, and two of
// them drive the drawing:
//
//   +0x0A  shader group index. mcCityModelType::Render (0x0024C380) does
//          lbu a1, 0xa(obj); sll a1, a1, 4; addu a1, mcCity->0x0C, a1.
//          Measured: 0..17 in the three 18-group cities, never above.
//   +0x0B  pass mask (mcPassTypes). The draw loop does
//          lbu v0, 0xb(obj); and v0, v0, <requested pass>; if zero, skip.
//          Values seen: 8, 64, 2, 10, 72, 12, 74, 4, 66, 14 - bits 1,2,3,6.

struct UniqueComponent {
    u32     next [[format("fmt_va")]];
    u32     prev [[format("fmt_va")]];
    u8      unk_08;
    u8      unk_09;
    u8      shader_group;                 // +0x0A index into mcCity+0x0C
    u8      pass_mask;                    // +0x0B mcPassTypes
    u32     data_va [[format("fmt_va")]];   // +0x0C -> ComponentData
    u32     type_va [[format("fmt_va")]];   // +0x10 type object, in the ELF
    TileBox tile;                           // +0x14
    float   radius;                         // +0x18 copy of the radius

    if (EXPAND_COMPONENTS) {
        ComponentData data @ off(data_va);
    }
};

// =====================================================================
// INSTANCE = mcInstCityModel  (96 bytes)
// =====================================================================
//
// vtable 006251B8, and it inherits from mcCullable like mcCityModel:
//   +0x0C mcInstCityModel::GetCenter()
//   +0x10 mcInstCityModel::GetCullCenter()
//   +0x18 mcInstCityModel::Render(mcPassTypes)
//   +0x1C mcCullable::CalcLOD()
//   +0x28 mcCityModel::InitExtents(...)
//
// An instance does NOT carry geometry: +0x0C points to the TYPE's
// ComponentData. Instances are CHAINED BY TYPE - measured on atlanta, 3377 of
// 3608 have +0x00 pointing to another instance OF THE SAME TYPE and none point
// to a different type; there are 231 chains, the head has +0x04 == 0.
// Repointing +0x0C while treating the slots as one pool mixes the types and
// the game loads forever: THE CHAIN IS THE UNIT.

// mcInstCityModel
struct Instance {
    u32     next [[format("fmt_va")]];      // next of the SAME type
    u32     prev [[format("fmt_va")]];      // 0 at the head of the chain
    u8      unk_08;
    u8      unk_09;
    u8      shader_group;                 // +0x0A index into mcCity+0x0C
    u8      pass_mask;                    // +0x0B mcPassTypes
    u32     data_va [[format("fmt_va")]];   // +0x0C ComponentData of the TYPE
    u32     type_va [[format("fmt_va")]];
    TileBox tile;                           // +0x14
    float   radius;                         // +0x18
    u32     chain_index;                    // +0x1C position inside the chain
    float   matrix[12];                     // +0x20 Matrix34 row by row
                                            //       (m00..m22, tx, ty, tz)
    float   sphere_centre[3];               // +0x50 centre in world space
    u32     pad_5c;

    if (EXPAND_INSTANCES) {
        ComponentData data @ off(data_va);
    }
};

// =====================================================================
// HOOD  (neighbourhood)
// =====================================================================
//
// MEASURED ON RETAIL: each hood is a COMPACT SPATIAL CLUSTER and the seven
// barely overlap. Atlanta, box of the component centres:
//
//   bh   x  -838..  -275   z -1210.. -322     dt   x -897.. 141  z -315..1014
//   fwy  x  -385.. 1117    z -1260..  911     lf   x   34.. 723  z  -75.. 853
//   mt   x  -169..  857    z -1119..   41     ug   x -713..-267  z  441.. 851
//   we   x -1225..  -606   z  -312..  561
//
// So a hood is not just a bag of slots, it is a region. Scattering imported
// geometry over arbitrary hoods breaks that invariant.
//
// THE 0xB0 BLOCK POINTED TO BY +0x00 IS NOT A "HOOD DESCRIPTOR": IT IS A
// TEXTURE. Token 007A2320 becomes vtable 00627280, which is rmcTexturePS2 - and
// one of the hoods uses 007A20E0 -> 00627040 = rmcTextureReference. The short
// name at the start ("bh", "dt", "fwy") is the texture name, and the rest is
// real GS state. So each hood carries its own atlas.

// =====================================================================
// rmcTexturePS2  -  the texture header
// =====================================================================
//
// The small accessors in the vtable say where each field lives:
//   GetName        005BDD60   return this->0x78;
//   GetWidth       005BDD68   ld this->0x10; >>26 & 0xF; 1 << that
//   GetHeight      005BDD88   ld this->0x10; >>30 & 0xF; 1 << that
//   GetMipMapCount 005BDDA8   byte this->0x86
//   GetClamp       002B8F40   byte this->0x85, bit 0 = U, bit 2 = V
//   GetFilter      002B8FE8   ld this->0x88, bits 6..8 (MMIN)
//
// +0x10 is the GS TEX0 and +0x88 is TEX1. IN THE FILE the vtable field holds a
// TOKEN, not the vtable - only in RAM is it 00627280.

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

fn fmt_tex1(u128 v) {
    u128 id = (v >> 21) & 0x7FF;
    if (id == 0) {
        return std::format("TEX1 {:016X}  no proxy", v);
    }
    return std::format("TEX1 {:016X}  proxy id {} (index {})", v, id, id - 1);
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
// THE SIZE: of the 350 references in San Diego's RAM, 232 have the name INLINE
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
// levels of the San Diego savestate:
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
// sit back to back, so `ptr + (+0x0C)` lands on the next one. On San Diego's
// root texture, 007E7F00 + 0x10500 = 007F8400, which is the next texture. But
// this is allocator opportunism and not a rule: of the 168 pairs of
// consecutive levels, only 92 are contiguous.
//
// +0x04 points to a shared node in the 0x0196xxxx range, repeated across
// levels, and is null on the root texture. +0x14 varies (14, 28, 32, 1...)
// without following size or level. From +0x18 to +0x8F it is never written:
// it stays CD.
struct MipBuffer {
    u32 zero_00;                                 // +0x00  0 in 577 of 577
    u32 shared_node [[format("fmt_va")]];        // +0x04
    u32 owner [[format("fmt_va")]];              // +0x08  -> the MipData that points here
    u32 size;                                    // +0x0C  0x100 + pixels + clut
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
    u32 buffer [[format("fmt_va")]];
    u32 unk_04;
    u32 unk_08;
};

//
// +0x7C TO +0x87 - what is pinned and what is not.
//
// THE TWO POPULATIONS OF +0x7C TO +0x87 ARE "HAS GS" AND "HAS NO GS".
//
// Sweeping the whole RAM of the San Diego savestate gives 3662 type-0
// rmcTexturePS2, and they split into two groups with a sharpness that is not
// chance:
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
    u32 vtable [[format("fmt_va")]];      // token in the file
    u8  kind;                             // +0x04  0/1/2/3, see the note
    u8  pad_05;                           // CD
    u16 refcount;                         // +0x06  1 in everything measured
    MipDesc mip[4];                       // +0x08 .. +0x47
    MipData level[4];                     // +0x48 .. +0x77
    u32 name [[format("fmt_va")]];        // +0x78  0 when anonymous
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
        MipBuffer hdr0 @ off(level[0].buffer);
        u8 pixels0[level_bytes(mip[0].tex0)] @ off(level[0].buffer) + 0x90;
        if (mips == 1) {
            ClutEntry clut0[clut_entries(mip[0].tex0)] @ off(level[0].buffer) + 0x90 + level_bytes(mip[0].tex0);
        }
    }
    if (EXPAND_PIXELS && level[1].buffer != 0) {
        MipBuffer hdr1 @ off(level[1].buffer);
        u8 pixels1[level_bytes(mip[1].tex0)] @ off(level[1].buffer) + 0x90;
        if (mips == 2) {
            ClutEntry clut1[clut_entries(mip[1].tex0)] @ off(level[1].buffer) + 0x90 + level_bytes(mip[1].tex0);
        }
    }
    if (EXPAND_PIXELS && level[2].buffer != 0) {
        MipBuffer hdr2 @ off(level[2].buffer);
        u8 pixels2[level_bytes(mip[2].tex0)] @ off(level[2].buffer) + 0x90;
        if (mips == 3) {
            ClutEntry clut2[clut_entries(mip[2].tex0)] @ off(level[2].buffer) + 0x90 + level_bytes(mip[2].tex0);
        }
    }
    if (EXPAND_PIXELS && level[3].buffer != 0) {
        MipBuffer hdr3 @ off(level[3].buffer);
        u8 pixels3[level_bytes(mip[3].tex0)] @ off(level[3].buffer) + 0x90;
        if (mips == 4) {
            ClutEntry clut3[clut_entries(mip[3].tex0)] @ off(level[3].buffer) + 0x90 + level_bytes(mip[3].tex0);
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
};

// rmcTexturePS2 (or rmcTextureReference)
// name(4) + texture(0xA0) = 0xA4. The 12 bytes up to the next descriptor
// (which sits 0xB0 further on) are a shader, not texture leftovers.
struct HoodDesc {
    char       name[4];               // "bh", "dt", "fwy", ...
    TexturePS2 tex;                   // +0x04
};

struct HoodEntry {
    u32 desc_va [[format("fmt_va")]];
    u32 unique_count;
    u32 unique_array_va [[format("fmt_va")]];
    u32 instance_count;
    u32 instance_array_va [[format("fmt_va")]];

    HoodDesc desc @ off(desc_va);
    UniqueComponent uniques[unique_count] @ off(unique_array_va);
    if (EXPAND_INSTANCES) {
        Instance instances[instance_count] @ off(instance_array_va);
    }
};

// =====================================================================
// SHADER GROUP
// =====================================================================
//
// MapRoot+0x08 says how many groups (18 in atlanta, tokyo and detroit; 15 in
// sandiego) and +0x0C points to the descriptor. Group 0 has the pointer
// array and the slot count - 1329 in atlanta, 1720 in tokyo, 1154 in detroit,
// 1516 in sandiego.
//
// A mesh's material index (mesh+0x0C) indexes THAT array. And here is the
// trap: rmcModel::FixupShaders (0x2A8A18) does array[index] and calls the
// virtual at +24 WITHOUT A NULL CHECK. In atlanta only 5 indices are filled in
// all 18 groups (1, 150, 631, 684, 685); 1041 of the 1046 used are empty in
// some group. Retail survives because each mesh only takes part in the passes
// where its material exists.
//
// A shader is 0xB0 bytes with ONE internal pointer, almost always to the
// sub-object embedded at +0x10 (1889 of 1900 in atlanta; +0x0C in 11 and
// +0xAC in 3). The stride between consecutive shaders is 0xB0.

// ---------------------------------------------------------------------
// THE SHADER OBJECT, AND WHY SLOT 0 ALWAYS EXISTS
//
// The +0x04 bitfield had already been read:
//     bits  7..11  drawbucket
//     bits 15..21  shader index in the 32-slot registry
//     bits 22..24  NUMBER of parameters
//     bits 25..31  mask: bit i set = parameter i is a TEXTURE
//
// One rule was missing: the parameters start at +0x08 and SLOT 0 EXISTS EVEN
// WITH THE COUNT ZEROED. city_facade (token 007A1EF8 in sd, 1780 objects) has
// flags == 0 - index 0, no parameter - and still carries a texture at +0x08.
// Such objects packed together sit 12 bytes apart, not 8.
//
//     size = 0x08 + 4 * max(count, 1)
//
// Measured on sd_dusk_clear:
//     007A1EF8  city_facade   flags 00000000  n=0 -> 0x0C   1780 objects
//     007A1FC8  hdr/doublesided            n=1 -> 0x0C       600
//     007B22B8  city_road                  n=4 -> 0x18       302
//     007B2318  city_window                n=6 -> 0x20      1322
// and in city_window the parameters are, in order: two textures and four
// floats that give 163/146/107/255 - the RGBA of the lit window.
//
// Reading a raw 0x08 + 4*n left the city_facade texture and its pixels out of
// the tree: that was 338 KB, the second largest unmapped block of the pck.
// ---------------------------------------------------------------------

fn shader_slots(u32 flags) {
    u32 n = (flags >> 22) & 7;
    if (n == 0) {
        return 1;
    }
    return n;
};

fn fmt_shader_flags(u32 f) {
    return std::format("shader {} | {} param | texture mask {:02X} | drawbucket {}",
                       (f >> 15) & 0x7F, (f >> 22) & 7, (f >> 25) & 0x7F, (f >> 7) & 0x1F);
};

struct ShaderObj {
    u32 token [[format("fmt_va")]];
    u32 flags [[format("fmt_shader_flags")]];
    u32 param[shader_slots(flags)] [[format("fmt_va")]];
};

struct ShaderSlot {
    u32 address [[format("fmt_va")]];
    if (EXPAND_SHADERS && address != 0) {
        ShaderObj object @ off(address);
    }
};

struct ShaderGroupDesc {
    u32 vtable [[format("fmt_va")]];
    u32 array_va [[format("fmt_va")]];
    u16 slot_count;
    u16 slot_count2;
    u32 unk_0c;

    if (EXPAND_SHADERS && array_va != 0) {
        ShaderSlot slots[slot_count] @ off(array_va);
    }
};

// =====================================================================
// PER-CELL INDEX  (MapRoot+0x1C)  -  THE REAL CULLING
// =====================================================================
//
// The tile box is only the INPUT. What the game walks is this table, and it
// comes READY in the file. Swapping geometry without rebuilding the index
// leaves the city loading, running and invisible: in one ported build the
// index matched in 333 of 3968 cells, because it still listed the donor's
// objects.
//
// RULE VALIDATED AGAINST RETAIL, not deduced: rebuilding the index from the
// tile boxes themselves, atlanta comes out 3968/3968, tokyo 6210/6210 and
// detroit 7200/7200. The lists come in DESCENDING index order.
//
// AND THE GRID IS NOT A FREE PARAMETER: the fixup (0x25BAE8 and 0x25BB40)
// walks `cell_count` entries of these two arrays. Raising MapRoot+0x268
// without rebuilding the arrays makes the loop run past the end and hangs the
// load.
//
// mc3_cellindex.py rebuilds and checks it.

struct CellRecord {
    s16 unique_count;
    s16 instance_count;
    u32 unique_list_va   [[format("fmt_va")]];  // u16[unique_count]
    u32 instance_list_va [[format("fmt_va")]];  // u16[instance_count]
} [[static]];

// ---------------------------------------------------------------------
// THE SECOND PER-CELL LIST (CellIndex+0x14)
//
// This field was annotated as "u32[cell_count], not decoded". It is a vector
// of POINTERS, one per cell, and in sd_dusk_clear 2391 of the 4473 cells have
// one; the other 2082 are zero. The targets take 423 KB - the largest block of
// the whole file still unmapped until now.
//
// Each target:
//     { float; u16 na (bit 15 is a flag); u16 nb; u16 list[na + nb] }
// aligned to 4.
//
// CHECKED: in 2390 of the 2391 records the size computed this way lands
// EXACTLY on the start of the next record; none falls outside. The remaining
// 1 is the last one, which has no next.
//
// The list values are an INSTANCE INDEX in 14 bits (largest seen 5007, against
// instance_count 5008) plus a 2-bit field on top, and all four values of that
// field show up. The `na` flag shows up in 1067 of the 2391.
//
// What the two lists separate, and what the float measures, I do not know yet.
// ---------------------------------------------------------------------

struct CellExtra {
    float  scale;
    u16    na;                 // bit 15 = flag
    u16    nb;
    u16    list[(na & 0x7FFF) + nb];
};

struct CellExtraPtr {
    u32 address [[format("fmt_va")]];
    if (address != 0) {
        CellExtra record @ off(address);
    }
};

struct CellIndex {
    u32 unique_count;                            // 651 in atlanta
    u32 instance_count;                          // 3608
    u32 unique_array_va   [[format("fmt_va")]];  // u32[unique_count]
    u32 instance_array_va [[format("fmt_va")]];  // u32[instance_count]
    u32 cells_va          [[format("fmt_va")]];  // CellRecord[cell_count]
    u32 extra_va          [[format("fmt_va")]];  // CellExtraPtr[cell_count]
                                                 // see the note above
                                                 // (14-bit indices with a 2-bit
                                                 // flag; smells of PVS)
};

// =====================================================================
// MapRoot / mcCity  -  the object at offset 0x80
// =====================================================================
//
// The global pointer to the live object is at 0x00615B40 in the retail ELF,
// and the vtable is 00625228 - derived from parFileIO (Save/Load/AfterLoad/
// BeforeSave/GetDirName/GetTypeName), with a thunk of
// mcSkyHatClass::FileIO(datParser&). So the whole MapRoot is a class
// serialised by RAGE's datParser.

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
    u32 first_type [[format("fmt_va")]];// +0x04  head of the chain, walks by +0x0C
    u32 last_type  [[format("fmt_va")]];// +0x08  tail
    u8  unk_0c;                      // 1
    padding[3];
    u32 unk_10;
    u16 unk_14;
    u16 unk_16;
    u32 vtable     [[format("fmt_va")]]; // +0x18
    u32 instance_count;              // +0x1C  sum of the instances + 1
    padding[0x20];                   // up to 0x40
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
// (In the FILE this field is always zero.)
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
    u32 indices  [[format("fmt_va")]];   // count * 128
    u32 palettes [[format("fmt_va")]];   // count * 64
    u32 table    [[format("fmt_va")]];   // count * 4
    u32 extra    [[format("fmt_va")]];
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

struct MapRoot {
    u32   vtable [[format("fmt_va")]];          // +0x000
    u8    owns_memory;                          // +0x004 see the note above
    u8    pad_005[3];                           // CD CD CD, filler
    u32   shader_group_count;                   // +0x008  18 in atlanta
    u32   shader_group_va [[format("fmt_va")]]; // +0x00C
    u32   sky_shader_group [[format("fmt_va")]];   // +0x010  rmcShaderGroup
                                                // of the sky (mcSkyHatClass)
    u32   hood_count;                           // +0x014
    u32   hood_array_va [[format("fmt_va")]];   // +0x018
    u32   cell_index_va [[format("fmt_va")]];   // +0x01C PER-CELL INDEX -
                                                // the real culling, see
                                                // CellIndex below
    u8    uses_cell_list;                       // +0x020 see note
    u8    pad_021[3];
    float draw_dist_fallback;                   // +0x024 see note
    u8    texture_bind_mode;                    // +0x028 see note
    u8    pad_029[3];
    u32   palette_live   [[format("fmt_va")]];    // +0x02C -> +0x290
    u32   palette_source [[format("fmt_va")]];    // +0x030 -> +0x1290
    u8    unk_034[0x0C];                        // +0x034
    // +0x040  mcConditionVariables. The name comes from the constructor:
    // mcCity::mcCity does `addiu a0, s1, 0x40` and calls 0x0025D370 =
    // mcConditionVariables::mcConditionVariables(datResource&). This block
    // holds the weather and render scalars (200/300 distance, colours,
    // 0.2 / 0.45 / 0.741 ...). The exact size has not been measured yet; here
    // it runs up to where the next known field starts.
    ConditionVariables condition_vars;          // +0x040
    u32   sky_class             [[format("fmt_va")]];  // +0x190 CullableClass
    u32   city_model_class      [[format("fmt_va")]];  // +0x194 CullableClass
    u32   inst_city_model_class [[format("fmt_va")]];  // +0x198 CullableClass
    u32   camera_matrix  [[format("fmt_va")]];   // +0x19C the frame's Matrix34
    u32   visible_buffer;                       // +0x1A0 which visible buffer
    float ambient_color[3];                     // +0x1A4
    float directional_color[3];                 // +0x1B0
    float directional_direction[3];             // +0x1BC  unit length
    u8    unk_1c8[8];                           // +0x1C8
    u32   env_map;                              // +0x1D0 mcCityEnvMap*, always 0 in the file
    u32   texture   [[format("fmt_va")]];          // +0x1D4 rmcTexturePS2*         // +0x1D4
    u32   textures[20] [[format("fmt_va")]];        // +0x1D8 rmcTexture*     // +0x1D8 .. +0x224
    u32   texture_proxy [[format("fmt_va")]];      // +0x228 rmcTextureProxyPS2*
    s32   sentinel_22c;                         // -999, see note                              // -999
    s32   sentinel_230;                         // -999, see note                              // -999
    u8    dead_234;                             // byte 0, see note
    u8    pad_235[3];                           // CD CD CD

    float extents_min[3];                       // +0x238
    float extents_max[3];                       // +0x244
    float inner_min[3];                         // +0x250  smaller box, not
    float inner_max[3];                         // +0x25C  identified yet

    // ----- CULLING GRID -----
    // RETAIL INVARIANT, checked in the three cities measured:
    //   cell_origin_x = floor(extents_min.x / 40)
    //   cell_origin_z = floor(extents_min.z / 40)
    //   grid_w        = floor(extents_max.x / 40) - cell_origin_x + 1
    //   grid_h        = floor(extents_max.z / 40) - cell_origin_z + 1
    //   cell_count    = grid_w * grid_h
    // and ZERO objects with a tile box outside (grid_w, grid_h).
    //
    //   atlanta  64 x 62 = 3968   origin -33, -34
    //   tokyo    90 x 69 = 6210   origin -48, -34
    //   detroit  96 x 75 = 7200   origin -58, -35
    //
    // Rewriting the extents without rewriting W/H leaves the two in
    // disagreement and every object that falls outside becomes invisible.
    u32   cell_count;                           // +0x268  == grid_w * grid_h
    s32   grid_w;                               // +0x26C  rel_x limit
    s32   grid_h;                               // +0x270  rel_z limit
    s32   cell_origin_x;                        // +0x274  in CELLS
    s32   cell_origin_z;                        // +0x278  in CELLS
    s32   physics_cap_a;                        // +0x27C  +1000 -> obj+0x2A
    s32   physics_cap_b;                        // +0x280  +300  -> obj+0x44
    u8    unk_284[0x0C];
    PaletteEntry palette_live_entries[256];     // +0x0290 .. +0x128F
    PaletteEntry palette_source_entries[256];   // +0x1290 .. +0x228F

    ShaderGroupDesc shader_groups[shader_group_count] @ off(shader_group_va);
    HoodEntry       hoods[hood_count]                 @ off(hood_array_va);
    CellIndex       cell_index                        @ off(cell_index_va);

    // In the FILE several of these are null - camera_matrix and env_map only
    // exist at runtime. Each one only shows when the pointer is not zero.
    if (sky_class != 0) {
        CullableClass sky_cls       @ off(sky_class);
    }
    if (city_model_class != 0) {
        CullableClass city_cls      @ off(city_model_class);
    }
    if (inst_city_model_class != 0) {
        CullableClass inst_cls      @ off(inst_city_model_class);
    }
    if (texture != 0) {
        TexturePS2 root_texture @ off(texture);   // +0x1D4, right at the end
    }
    if (texture_proxy != 0) {
        TextureProxy proxy @ off(texture_proxy);
    }
    if (camera_matrix != 0) {
        Matrix34 camera @ off(camera_matrix);
    }
    if (EXPAND_CELLS) {
        CellRecord   cells[cell_count] @ off(std::mem::read_unsigned(off(cell_index_va) + 0x10, 4));
        CellExtraPtr extra_per_cell[cell_count] @ off(std::mem::read_unsigned(off(cell_index_va) + 0x14, 4));
    }
};

// =====================================================================

PckHeader header @ 0x00;
MapRoot   root   @ 0x80;
