#pragma description Midnight Club 3 (PS2) - vp_*.pck loaded in EE RAM
#pragma endian little
#pragma pattern_limit 2000000
#pragma array_limit 20000
#pragma eval_depth 256

import std.mem;
import std.string;

// Open eeMemory.bin from a savestate with the vehicle loaded. File offset is
// the EE address. Change VP_BODY for each capture; for the documented 350Z it
// is 0x01773680 and corresponds to PCK file offset 0x80.
u32 VP_BODY = 0x01773680;

bool EXPAND_LOD_SLOTS = true;
bool EXPAND_SKELETON = false;
bool EXPAND_MESHES = true;
bool EXPAND_MESH_GROUPS = true;
bool EXPAND_VIF_PACKETS = true;
bool EXPAND_SHADERS = true;
bool EXPAND_SHADER_LOCALS = true;
bool EXPAND_SHADER_PASSES = true;
bool EXPAND_TEXTURE_HEADERS = true;
bool EXPAND_TEXTURE_PIXELS = false;
bool EXPAND_PERFORMANCE = true;
bool EXPAND_VEHICLE_AUDIO = true;
bool EXPAND_AUDIO_CURVES = false;
bool EXPAND_WHEEL_ALIASES = false;

// The latest address is already reachable through the typed vehicle graph.
// Keep this false during normal use so ImHex does not paint the same bytes
// twice.  Turn it on only for a detached shader address that is not reachable,
// preferably together with EXPAND_SHADERS = false.
bool SHOW_TYPED_SHADER_INSPECTOR = false;

// Optional typed inspector. The default address is the latest supplied
// drwShaderDistort, but it is already reachable through:
// vehicle/model_type/special_shaders/slot[0]/shader.
u32 INSPECT_ADDRESS = 0x017B74A0;

fn ptr(u128 a) {
    if (a < 0x00010000 || a > 0x01FFFFFF) { return 0; }
    return a;
};

fn valid_ptr(u128 a) {
    return ptr(a) != 0;
};

fn fmt_ptr(u32 a) {
    if (a == 0) { return "null"; }
    if (!valid_ptr(a)) { return std::format("raw {:08X}", a); }
    return std::format("EE {:08X}", a);
};

fn fmt_skeleton_index_plus_one(u32 value) {
    if (value == 0) { return "not found (stored zero)"; }
    return std::format("stored {} -> skeleton index {}", value, value - 1);
};

fn vt(u32 v) {
    if (v == 0x00626D08) { return "rmcLodPaged"; }
    if (v == 0x00626D98) { return "rmcModelGeom"; }
    if (v == 0x00626E70) { return "rmcShaderBasic"; }
    if (v == 0x00626EC8) { return "rmcShaderComplex"; }
    if (v == 0x00626F20) { return "rmcShaderGroup"; }
    if (v == 0x00626F30) { return "rmcShaderInstance"; }
    if (v == 0x00626FC8) { return "rmcDictionaryReference"; }
    if (v == 0x00627040) { return "rmcTextureReference"; }
    if (v == 0x00627280) { return "rmcTexturePS2"; }
    if (v == 0x006276C0) { return "mcCarCustom"; }
    if (v == 0x00627770) { return "vehicle audio root"; }
    if (v == 0x006277F0) { return "mcCarDamageTune"; }
    if (v == 0x00627820) { return "mcGyroTune"; }
    if (v == 0x006278C8) { return "mcCarSimTune"; }
    if (v == 0x00627978) { return "mcCarTuneZone"; }
    if (v == 0x006279A8) { return "mcCarCamShakeTune"; }
    if (v == 0x00627AC0) { return "mcDriverTune"; }
    if (v == 0x00627F40) { return "mcCarModelTune"; }
    if (v == 0x00627FD0) { return "mcCullableType"; }
    if (v == 0x00627FF8) { return "carWindowDamage"; }
    if (v == 0x006281B0) { return "mcCarModsTune"; }
    if (v == 0x006281E0) { return "mcCarStuckTune"; }
    if (v == 0x00623FD8) { return "mcSpotlightTuneParams"; }
    if (v == 0x0062DF00) { return "phMaterial"; }
    if (v == 0x0062E4C0) { return "drwShaderModel"; }
    if (v == 0x0062E588) { return "drwShaderDistort"; }
    if (v == 0x0062E758) { return "drwShaderHeadlight"; }
    if (v == 0x0062E7B0) { return "drwShaderThirdBrakelight"; }
    if (v == 0x0062E808) { return "drwShaderBrakelight"; }
    if (v == 0x0062E860) { return "drwShaderReverselight"; }
    if (v == 0x0062E8B8) { return "drwShaderTaillight"; }
    if (v == 0x0062E910) { return "drwShaderCarpaintNoVinyl"; }
    if (v == 0x0062E968) { return "drwShaderCarPaint"; }
    if (v == 0x0062F1B0) { return "drwShaderAntiAliasedChrome"; }
    if (v == 0x0062F208) { return "drwShaderAntiAliasedTrim"; }
    if (v == 0x0062F260) { return "drwShaderMaskedChrome"; }
    if (v == 0x0062F310) { return "drwShaderChrome"; }
    if (v == 0x0062F368) { return "drwShaderCarbonFiber"; }
    if (v == 0x0062F3C0) { return "drwShaderDefaultShiny"; }
    if (v == 0x0062F418) { return "drwShaderCarDecalChrome"; }
    if (v == 0x0062F470) { return "drwShaderCarDecalNormal"; }
    if (v == 0x0062F4C8) { return "drwShaderCarDecalMulti"; }
    if (v == 0x0062F520) { return "drwShaderRubber"; }
    if (v == 0x0062F6D8) { return "drwShaderLitTextured"; }
    if (v == 0x0062F730) { return "drwShaderNeonShadow"; }
    if (v == 0x0062F7E0) { return "drwShaderCarWindows"; }
    if (v == 0x0062F838) { return "drwShaderColoredGlass"; }
    if (v == 0x0062F8E8) { return "drwShaderLicensePlate"; }
    if (v == 0x0062F9F8) { return "drwShaderTaillightCasing"; }
    if (v == 0x0062FA50) { return "drwShaderHood"; }
    if (v == 0x0062EDE8) { return "drwShaderRiderChrome"; }
    if (v == 0x00633010) { return "phBoundGeometry"; }
    if (v == 0x00634A50) { return "mcPlayerInputTune"; }
    if (v == 0) { return "null"; }
    return std::format("vtable {:08X}", v);
};

// Every vehicle-specific material seen on the 350Z inherits
// rmcShaderComplex.  Testing virtual slot 3 identifies the base and all its
// derived classes without maintaining a fragile list of vtable addresses.
fn is_complex_shader(u128 v) {
    if (v < 0x00010000 || v > 0x01FFFFFF) { return false; }
    return std::mem::read_unsigned(v + 0x0C, 4) == 0x002B0318;
};

fn fmt_tex0(u128 v) {
    return std::format("{}x{} PSM {:02X} TBP0 {} TBW {} CBP {}",
        1 << ((v >> 26) & 0xF), 1 << ((v >> 30) & 0xF), (v >> 20) & 0x1F,
        v & 0x3FFF, (v >> 14) & 0x3F, (v >> 37) & 0x3FFF);
};

fn fmt_tex1(u128 v) {
    u128 id = (v >> 21) & 0x7FF;
    if (id == 0) { return std::format("TEX1 {:016X} no proxy", v); }
    return std::format("TEX1 {:016X} proxy id {} (index {})", v, id, id - 1);
};

fn fmt_shader_opcode(u128 v) {
    u128 state = v & 0x3F;
    if (v == 0xFF) { return "end"; }
    if (state == 0)  { return "cull"; }
    if (state == 1)  { return "lighting"; }
    if (state == 2)  { return "fog_blend"; }
    if (state == 3)  { return "blend"; }
    if (state == 4)  { return "alpha_function"; }
    if (state == 5)  { return "alpha_reference"; }
    if (state == 6)  { return "depth_function"; }
    if (state == 7)  { return "depth_write"; }
    if (state == 8)  { return "base_color"; }
    if (state == 9)  { return "color_write"; }
    if (state == 10) { return "destination_alpha_test"; }
    if (state == 11) { return "alpha_blend"; }
    if (state == 12) { return "alpha_test_failure"; }
    if (state == 13) { return "smooth_shading"; }
    return std::format("state {}", state);
};

fn fmt_vif_command(u32 v) {
    u128 command = (v >> 24) & 0x7F;
    u128 count = (v >> 16) & 0xFF;
    u128 immediate = v & 0xFFFF;
    if (command == 0x69) { return std::format("UNPACK V3-16  n={} addr={}", count, immediate & 0x3FFF); }
    if (command == 0x65) { return std::format("UNPACK V2-16  n={} addr={}", count, immediate & 0x3FFF); }
    if (command == 0x6A) { return std::format("UNPACK V3-8   n={} addr={}", count, immediate & 0x3FFF); }
    if (command == 0x6E) { return std::format("UNPACK V4-8   n={} addr={}", count, immediate & 0x3FFF); }
    if (command == 0x14) { return std::format("MSCAL {}", immediate); }
    return std::format("VIF cmd={:02X} n={} imm={:04X}", command, count, immediate);
};

fn texture_level_bytes(u128 tex0) {
    u128 width = 1 << ((tex0 >> 26) & 0xF);
    u128 height = 1 << ((tex0 >> 30) & 0xF);
    u128 psm = (tex0 >> 20) & 0x1F;
    if (psm == 0x14) { return width * height / 2; }
    if (psm == 0x13) { return width * height; }
    return 0;
};

fn texture_clut_entries(u128 tex0) {
    u128 psm = (tex0 >> 20) & 0x1F;
    if (psm == 0x14) { return 16; }
    if (psm == 0x13) { return 256; }
    return 0;
};

fn align4(u128 address) {
    return ((address + 3) / 4) * 4;
};

// ============================================================================
// Common math, skeleton IDs and effect records
// ============================================================================

struct Vec3 {
    float x;
    float y;
    float z;
} [[static]];

// Serialized 3x4 transform.  The nitro-emitter table below has room for two
// matrices; nitro_emitter_count says how many of them are meaningful.
struct Matrix34 {
    Vec3 axis_x;
    Vec3 axis_y;
    Vec3 axis_z;
    Vec3 translation;
} [[static]];

// One physical/render wheel.  The three node IDs resolve into the vehicle's
// skeleton.  Motorcycles leave suspension_skeleton_id at FFFFFFFF or use a
// fork/swingarm node; cars normally use Arm_Front/Rear_L/R.
struct WheelRecord {
    u32 ordinal;
    u32 axle_skeleton_id;
    u32 wheel_skeleton_id;
    u32 suspension_skeleton_id;
    Vec3 position;
} [[static]];

struct WheelRecordPointer {
    u32 address [[format("fmt_ptr")]];
    if (EXPAND_WHEEL_ALIASES && valid_ptr(address)) {
        WheelRecord wheel @ ptr(address);
    }
} [[static]];

// RAGE atBitSet: the word array is external, while its two u16 dimensions
// live inline.  In this field every retail vehicle sets exactly the single
// skeleton node named neon/shadow.
struct AtBitSet {
    u32 words [[format("fmt_ptr")]];
    u16 word_count;
    u16 bit_count;

    if (valid_ptr(words) && word_count > 0 && word_count <= 64) {
        u32 word[word_count] @ ptr(words);
    }
} [[static]];

struct ExhaustEmitterMatrixSet {
    Matrix34 emitter[4];
} [[static]];

// One pair per rear-bumper customization choice.  These values are exact
// copies of the chosen bumper's skeleton nodes named exst0/exst1 or
// bumr*_ext0/1.  Runtime code indexes this table with mcCarCustom+0x118C.
struct RearBumperExhaustPair {
    Vec3 exhaust_0;
    Vec3 exhaust_1;
} [[static]];

struct CustomExhaustSkeletonSet {
    u32 skeleton_id[4];
} [[static]];

struct RearBumperExhaustSkeletonPair {
    u32 skeleton_id[2];
} [[static]];

// ============================================================================
// Damage, geometry and LOD resources
// ============================================================================

// carWindowDamage::FileIO names these fields explicitly.  The remaining
// bytes in each 0x60-byte record are runtime state not needed to delimit the
// object or to inspect the serialized position/rotation.
struct CarWindowDamageRecord {
    float width;
    float height;
    u8 unknown_08[0x38];
    Vec3 position;                       // +0x40
    Vec3 rotation;                       // +0x4C
    u8 unknown_58[0x08];
} [[static]];

struct CarWindowDamage {
    u32 vtable [[format("vt")]];
    u32 unknown_04 [[format("fmt_ptr")]];
    u32 unknown_08;
    u32 windows [[format("fmt_ptr")]]; // +0x0C, 0x60-byte records
    u8 flags_10;
    u8 pad_11[3];
    u32 window_count;                    // +0x14; 0, 3 or 4 in retail

    if (valid_ptr(windows) && window_count <= 4) {
        CarWindowDamageRecord window[window_count] @ ptr(windows);
    }
} [[static]];

// The mesh tools follow this exact pointer graph and then decode the VIF packet
// rather than scanning for tags. Vehicle packets start with 9800026C/C501026C
// and contain culling data; traffic uses 98000260 and moves the vertex command
// to +0x0C. In both cases each UNPACK payload is padded to four bytes.
struct VifPositionS16 {
    s16 x;
    s16 y;
    s16 z;
} [[static]];

struct VifUvU16 {
    u16 u;                                  // divide by 4096.0
    u16 v;
} [[static]];

struct VifColorV4_8 {
    u8 r;
    u8 g;
    u8 b;
    u8 a;
} [[static]];

struct VifNormalAdcV3_8 {
    s8 x_with_adc;                          // bit 0 suppresses the closing triangle
    s8 y;
    s8 z;
} [[static]];

struct VehicleVifPacketHeader {
    u32 packet_kind;                        // bytes 98 00 02 6C or C5 01 02 6C
    float position_scale;
    float cull_center_y;
    float cull_center_z;
    float cull_radius;
    u32 vertex_count_14;
    float reserved_18;
    float reserved_1C;
    u32 vertex_count_20;
    u32 position_unpack [[format("fmt_vif_command")]];
} [[static]];

struct TrafficVifPacketHeader {
    u32 packet_kind;                        // bytes 98 00 02 60
    float position_scale;
    u32 vertex_count_08;
    u32 position_unpack [[format("fmt_vif_command")]];
} [[static]];

struct VifPacketFooter {
    u32 itop [[format("fmt_vif_command")]];
    u32 mscal [[format("fmt_vif_command")]];
} [[static]];

struct VifCommand {
    u32 value [[format("fmt_vif_command")]];
} [[static]];

struct BlockEntry {
    u32 packet [[format("fmt_ptr")]];
    u16 qwc;
    u16 vertex_count;

    if (EXPAND_VIF_PACKETS && valid_ptr(packet) && qwc > 0 && qwc <= 4096
            && vertex_count > 0 && vertex_count <= 128) {
        if (std::mem::read_unsigned(ptr(packet), 4) == 0x6C020098
                || std::mem::read_unsigned(ptr(packet), 4) == 0x6C0201C5) {
            VehicleVifPacketHeader vehicle_header @ ptr(packet);
            VifPositionS16 position[vertex_count] @ ptr(packet) + 0x28;
            VifCommand uv_unpack @ align4(ptr(packet) + 0x28 + vertex_count * 6);
            VifUvU16 uv[vertex_count]
                @ align4(ptr(packet) + 0x28 + vertex_count * 6) + 4;
            VifCommand color_unpack
                @ align4(ptr(packet) + 0x28 + vertex_count * 6) + 4 + vertex_count * 4;
            VifColorV4_8 color[vertex_count]
                @ align4(ptr(packet) + 0x28 + vertex_count * 6) + 8 + vertex_count * 4;
            VifCommand normal_unpack
                @ align4(ptr(packet) + 0x28 + vertex_count * 6) + 8 + vertex_count * 8;
            VifNormalAdcV3_8 normal_adc[vertex_count]
                @ align4(ptr(packet) + 0x28 + vertex_count * 6) + 12 + vertex_count * 8;
            VifPacketFooter footer
                @ align4(align4(ptr(packet) + 0x28 + vertex_count * 6)
                    + 12 + vertex_count * 11);
        }
        if (std::mem::read_unsigned(ptr(packet), 4) == 0x60020098) {
            TrafficVifPacketHeader traffic_header @ ptr(packet);
            VifPositionS16 position[vertex_count] @ ptr(packet) + 0x10;
            VifCommand uv_unpack @ align4(ptr(packet) + 0x10 + vertex_count * 6);
            VifUvU16 uv[vertex_count]
                @ align4(ptr(packet) + 0x10 + vertex_count * 6) + 4;
            VifCommand color_unpack
                @ align4(ptr(packet) + 0x10 + vertex_count * 6) + 4 + vertex_count * 4;
            VifColorV4_8 color[vertex_count]
                @ align4(ptr(packet) + 0x10 + vertex_count * 6) + 8 + vertex_count * 4;
            VifCommand normal_unpack
                @ align4(ptr(packet) + 0x10 + vertex_count * 6) + 8 + vertex_count * 8;
            VifNormalAdcV3_8 normal_adc[vertex_count]
                @ align4(ptr(packet) + 0x10 + vertex_count * 6) + 12 + vertex_count * 8;
            VifPacketFooter footer
                @ align4(align4(ptr(packet) + 0x10 + vertex_count * 6)
                    + 12 + vertex_count * 11);
        }
    }
};

struct GroupEntry {
    u32 blocks [[format("fmt_ptr")]];
    u16 block_count;
    u16 pad;

    if (EXPAND_MESH_GROUPS && valid_ptr(blocks) && block_count > 0 && block_count <= 4096) {
        BlockEntry block[block_count] @ ptr(blocks);
    }
};

struct Mesh {
    u32 vtable [[format("vt")]];
    u32 render_flags;
    u16 group_count;
    u16 slot_count;
    u32 material_indices [[format("fmt_ptr")]];
    u32 groups [[format("fmt_ptr")]];
    u32 per_block [[format("fmt_ptr")]];
    u32 unknown_ptr_18 [[format("fmt_ptr")]];
    u32 unknown_ptr_1C [[format("fmt_ptr")]];

    if (EXPAND_MESH_GROUPS && valid_ptr(material_indices) && valid_ptr(groups)
            && group_count > 0 && group_count <= 64) {
        u16 material_index[group_count] @ ptr(material_indices);
        GroupEntry group[group_count] @ ptr(groups);
    }
};

// Sparse-pointer wrapper copied from the city shader tree: an array of u32
// cannot conditionally instantiate only its non-null targets, but an array of
// wrappers can.  This makes embedded and externally paged stock parts appear
// through exactly the same path.
struct MeshSlot {
    u32 address [[format("fmt_ptr")]];

    if (EXPAND_MESHES && valid_ptr(address)) {
        Mesh geometry @ ptr(address);
    }
};

struct CStr {
    std::string::NullString text;
};

struct NameSlot {
    u32 address [[format("fmt_ptr")]];

    if (valid_ptr(address)) {
        CStr resource_name @ ptr(address);
    }
};

struct SkeletonNode {
    Vec3 anchor1;
    u32 name [[format("fmt_ptr")]];
    u32 unk_10;
    u32 next [[format("fmt_ptr")]];
    u32 child [[format("fmt_ptr")]];
    u32 parent_node [[format("fmt_ptr")]];
    Vec3 anchor2;
    u8 unk_2C[0x0C];
    u16 id;
    u8 unk_3A[0x0A];

    if (valid_ptr(name)) {
        CStr node_name @ ptr(name);
    }
} [[static]];

struct SkeletonHeader {
    u16 count;
    u16 unknown;
    u32 tag;                       // 0x000102EE; not a retail vtable
    u32 nodes [[format("fmt_ptr")]];
    if (EXPAND_SKELETON && valid_ptr(nodes) && count > 0 && count < 4096) {
        SkeletonNode node[count] @ ptr(nodes);
    }
};

struct LodPaged {
    u16 unknown;
    u16 count;
    u32 vtable [[format("vt")]];
    u32 meshes [[format("fmt_ptr")]];
    u32 names [[format("fmt_ptr")]];
    u32 ids [[format("fmt_ptr")]];
    SkeletonHeader skeleton;

    if (EXPAND_LOD_SLOTS && valid_ptr(meshes) && valid_ptr(names) && valid_ptr(ids)
            && count > 0 && count < 4096) {
        MeshSlot mesh[count] @ ptr(meshes);
        NameSlot name[count] @ ptr(names);
        u32 skeleton_id[count] @ ptr(ids);
    }
};

// ============================================================================
// Textures
// ============================================================================

// These layouts are shared engine classes copied from mc3_city_ram.hexpat and
// revalidated against the live 350Z.  In particular, a TextureReference is
// only 0x10 bytes while a TexturePS2 occupies 0xA0.
struct VtableAlias {
    u32 vtable [[format("vt")]];
};

struct TextureClutEntry {
    u8 r;
    u8 g;
    u8 b;
    u8 a;                                  // PS2 alpha range is 0..0x80
} [[static]];

// MipData.pixel_buffer points at this 0x90-byte allocation header. The image
// indices begin at +0x90; the last resident mip is followed by its CLUT. This
// is the same chain used by mc3_tex.py to extract/replace vehicle atlases.
struct TextureMipBuffer {
    u32 zero_00;
    u32 shared_node [[format("fmt_ptr")]];
    u32 owner_mip_data [[format("fmt_ptr")]];
    u32 allocation_size;                   // 0x100 + pixels + final CLUT
    u32 unknown_10;
    u16 unknown_14;
    u16 sentinel_16;                       // FFFF in measured resident buffers
    u8 reserved_18[0x78];
} [[static]];

struct MipDesc {
    u16 size_class;
    u16 tbp;                         // GS address in 256-byte blocks
    u16 blocks;
    u16 blocks_through_last_mip;
    u64 tex0 [[format("fmt_tex0")]];
} [[static]];

struct MipData {
    u32 pixel_buffer [[format("fmt_ptr")]];
    u32 unknown_04;
    u32 unknown_08;
} [[static]];

// Bodies begin at texture+0x05. TextureAny owns the common vtable/type bytes,
// so the header is represented once instead of being overlaid by @ $ - 5.
struct TexturePS2Body {
    u8 reserved_05;
    u16 reference_count;
    MipDesc mip[4];                   // owner +0x08..+0x47
    MipData level[4];                 // owner +0x48..+0x77
    u32 name [[format("fmt_ptr")]];  // owner +0x78
    u32 allocation_stamp;
    u16 unknown_80;
    u16 cbp;                          // GS CLUT address in blocks
    u8 clut_256;                      // PSMT8=1, PSMT4=0
    u8 clamp;                         // bit0 U, bit2 V
    u8 mip_count;
    u8 reserved_87;
    u64 tex1 [[format("fmt_tex1")]];
    u8 runtime_tail_90[0x10];         // owner total size 0xA0

    if (valid_ptr(name)) {
        CStr texture_name @ ptr(name);
    }
    if (EXPAND_TEXTURE_PIXELS && valid_ptr(level[0].pixel_buffer)
            && texture_level_bytes(mip[0].tex0) > 0) {
        TextureMipBuffer buffer0 @ ptr(level[0].pixel_buffer);
        u8 pixels0[texture_level_bytes(mip[0].tex0)] @ ptr(level[0].pixel_buffer) + 0x90;
        if (mip_count == 1 && texture_clut_entries(mip[0].tex0) > 0) {
            TextureClutEntry clut0[texture_clut_entries(mip[0].tex0)]
                @ ptr(level[0].pixel_buffer) + 0x90 + texture_level_bytes(mip[0].tex0);
        }
    }
    if (EXPAND_TEXTURE_PIXELS && valid_ptr(level[1].pixel_buffer)
            && texture_level_bytes(mip[1].tex0) > 0) {
        TextureMipBuffer buffer1 @ ptr(level[1].pixel_buffer);
        u8 pixels1[texture_level_bytes(mip[1].tex0)] @ ptr(level[1].pixel_buffer) + 0x90;
        if (mip_count == 2 && texture_clut_entries(mip[1].tex0) > 0) {
            TextureClutEntry clut1[texture_clut_entries(mip[1].tex0)]
                @ ptr(level[1].pixel_buffer) + 0x90 + texture_level_bytes(mip[1].tex0);
        }
    }
    if (EXPAND_TEXTURE_PIXELS && valid_ptr(level[2].pixel_buffer)
            && texture_level_bytes(mip[2].tex0) > 0) {
        TextureMipBuffer buffer2 @ ptr(level[2].pixel_buffer);
        u8 pixels2[texture_level_bytes(mip[2].tex0)] @ ptr(level[2].pixel_buffer) + 0x90;
        if (mip_count == 3 && texture_clut_entries(mip[2].tex0) > 0) {
            TextureClutEntry clut2[texture_clut_entries(mip[2].tex0)]
                @ ptr(level[2].pixel_buffer) + 0x90 + texture_level_bytes(mip[2].tex0);
        }
    }
    if (EXPAND_TEXTURE_PIXELS && valid_ptr(level[3].pixel_buffer)
            && texture_level_bytes(mip[3].tex0) > 0) {
        TextureMipBuffer buffer3 @ ptr(level[3].pixel_buffer);
        u8 pixels3[texture_level_bytes(mip[3].tex0)] @ ptr(level[3].pixel_buffer) + 0x90;
        if (mip_count == 4 && texture_clut_entries(mip[3].tex0) > 0) {
            TextureClutEntry clut3[texture_clut_entries(mip[3].tex0)]
                @ ptr(level[3].pixel_buffer) + 0x90 + texture_level_bytes(mip[3].tex0);
        }
    }
} [[static]];

struct TextureReferenceBody {
    u8 reserved_05;
    u16 reference_count;
    u32 name [[format("fmt_ptr")]];  // owner +0x08
    u32 target [[format("fmt_ptr")]];// owner +0x0C, virtual calls forward here

    if (valid_ptr(name)) {
        CStr reference_name @ ptr(name);
    }
    if (valid_ptr(target)) {
        VtableAlias target_class @ ptr(target);
    }
} [[static]];

struct TextureAny {
    u32 vtable [[format("vt")]];
    u8 type;                          // 0 PS2, 2 reference, 3 dictionary ref

    if (EXPAND_TEXTURE_HEADERS && type == 0) {
        TexturePS2Body ps2;
    }
    if (EXPAND_TEXTURE_HEADERS && type == 2) {
        TextureReferenceBody reference;
    }
};

// ============================================================================
// Shader programs, passes and derived vehicle shaders
// ============================================================================

// A pointer-sized texture slot. rmcShaderComplex pass+0x08 points here, not
// directly to the texture: the extra indirection is significant.
struct TexturePointer {
    u32 address [[format("fmt_ptr")]];
    if (valid_ptr(address)) {
        TextureAny texture @ ptr(address);
    }
};

// One command in the compact render-state stream. Opcodes with no high bits
// carry one byte; opcodes with 0x40/0x80/0xC0 carry a little-endian u32.
// Recursion stops on 0xFF and makes the actual program visible in ImHex.
struct ShaderStateCommand {
    u8 opcode [[format("fmt_shader_opcode")]];
    if (opcode != 0xFF) {
        if ((opcode & 0xC0) == 0) {
            u8 state_value_u8;
        }
        if ((opcode & 0xC0) != 0) {
            u32 state_value_u32;
        }
        ShaderStateCommand next_command;
    }
};

// Optional texture-coordinate transform owned by one ShaderPass. The five
// non-null pointers are waveform objects parsed from texframe/scales/slides/
// scalet/slidet/rotate directives. The matrix is allocated at runtime.
struct ShaderTextureTransform {
    u32 runtime_matrix [[format("fmt_ptr")]];
    u32 scale_s_waveform [[format("fmt_ptr")]];
    u32 slide_s_waveform [[format("fmt_ptr")]];
    u32 scale_t_waveform [[format("fmt_ptr")]];
    u32 slide_t_waveform [[format("fmt_ptr")]];
    u32 rotation_waveform [[format("fmt_ptr")]];
} [[static]];

// Shared 0x20-byte rmcShaderComplex pass. The first pass lives inline at
// shader+0x08; +0x1C links additional passes. The field names below come from
// rmcShaderComplex::LoadInternal rather than from value-shape guesses.
struct ShaderPass {
    u32 state_program [[format("fmt_ptr")]];
    u8 microcode_index;
    u8 quality_limit;                    // high=0, med=1, low=2, vlow=3
    u16 reserved_06;
    u32 texture_slot [[format("fmt_ptr")]];
    u32 texture_frame_waveform [[format("fmt_ptr")]];
    u32 user0_waveform [[format("fmt_ptr")]];
    u32 texture_transform [[format("fmt_ptr")]];
    u8 texture_count;
    u8 texgen_and_source;
    u8 texture_combine;
    u8 reserved_1B;
    u32 next_pass [[format("fmt_ptr")]];

    if (valid_ptr(state_program)) {
        ShaderStateCommand commands @ ptr(state_program);
    }
    if (valid_ptr(texture_slot) && texture_count > 0 && texture_count <= 16) {
        TexturePointer texture[texture_count] @ ptr(texture_slot);
    }
    if (valid_ptr(texture_transform)) {
        ShaderTextureTransform transform @ ptr(texture_transform);
    }
    if (EXPAND_SHADER_PASSES && valid_ptr(next_pass)) {
        ShaderPass following_pass @ ptr(next_pass);
    }
} [[static]];

struct DrwShaderCarDecalNormalColor {
    float color_r;                               // +0x30
    float color_g;                               // +0x34
    float color_b;                               // +0x38
    float color_a;                               // +0x3C; Draw halves alpha for AABBGGRR
} [[static]];

// The chrome companion has the same 0x40-byte outer size but different colour
// semantics.  RGB becomes the low 24 bits of 0x70000080 with fixed alpha 0x80.
// reflection_to_white is instead used as lerp(global reflection colour, white)
// before SetFresnelParamsChrome.  Its first pass always links a second pass;
// Draw dereferences second_pass+0x14, so that transform block is mandatory.
struct DrwShaderCarDecalChromeColor {
    float base_color_r;                          // +0x30
    float base_color_g;                          // +0x34
    float base_color_b;                          // +0x38
    float reflection_to_white;                   // +0x3C; not packed alpha
} [[static]];

// Exact 0x34-byte carpaint_novinyl.shadert object.  Its four bytes are local
// parameter indices, not RGBA values or padding.  Load resolves the names and
// Draw gathers the four floats before setting the car-paint Fresnel vector.
struct DrwShaderCarpaintNoVinylLocals {
    u8 carpaint_env_scale_index;                 // +0x30
    u8 carpaint_fresnel_min_index;               // +0x31
    u8 carpaint_fresnel_max_index;               // +0x32
    u8 carpaint_fresnel_scale_index;             // +0x33
} [[static]];

// Exact 0x38-byte distort.shadert object. Load resolves these four local
// names even though the retail Draw/Bind implementation no longer reads the
// indices directly. The common pass owns three texture-pointer entries:
// colour, light map and environment map.
struct DrwShaderDistortLocals {
    u8 distort_env_scale_index;                  // +0x30
    u8 distort_fresnel_min_index;                // +0x31
    u8 distort_fresnel_max_index;                // +0x32
    u8 distort_fresnel_scale_index;              // +0x33
    u8 reserved_34[4];                           // total object 0x38, CD in PCK/RAM
} [[static]];

struct ShaderPointer {
    u32 address [[format("fmt_ptr")]];
} [[static]];

struct Shader {
    u32 vtable [[format("vt")]];
    u32 shader_flags;                            // type, draw bucket and class bits

    // rmcShaderBasic has a direct texture pointer at +0x08.
    if (vtable == 0x00626E70) {
        u32 direct_texture [[format("fmt_ptr")]];
        if (valid_ptr(direct_texture)) {
            TextureAny basic_texture @ ptr(direct_texture);
        }
    }

    // Complex and derived shaders are represented inline. This makes their
    // visible size 0x30 before a derived-class extension, with no @ overlap.
    if (is_complex_shader(vtable)) {
        ShaderPass first_pass;                   // +0x08..+0x27
        u32 shader_template_name [[format("fmt_ptr")]]; // +0x28, GetName()
        u32 render_state_mask;                   // +0x2C, save/restore mask

        if (valid_ptr(shader_template_name)) {
            CStr template_name @ ptr(shader_template_name);
        }

        if (vtable == 0x0062F470) {
            DrwShaderCarDecalNormalColor normal_color; // total object 0x40
        }
        if (vtable == 0x0062F418) {
            DrwShaderCarDecalChromeColor chrome_color; // total object 0x40
        }
        if (vtable == 0x0062E910) {
            DrwShaderCarpaintNoVinylLocals carpaint_locals; // total object 0x34
        }
        if (vtable == 0x0062E588) {
            DrwShaderDistortLocals distort_locals; // total object 0x38
        }

        // tlcasing.shadert owns three shader children through an external
        // pointer vector: no-vinyl carpaint, carbon fibre and chrome.  The
        // 350Z object at 0x0178C110 is reached through child[0], rather than
        // by assuming that nearby allocations form an implicit hierarchy.
        if (vtable == 0x0062F9F8) {
            u32 casing_variants [[format("fmt_ptr")]]; // +0x30 -> 3 pointers
            u32 active_casing_variant;                  // +0x34
            u32 casing_variant_count;                   // +0x38, retail = 3
            u8 taillight_casing_shader_index;           // +0x3C
            u8 reserved_3D[3];                          // total object 0x40

            if (valid_ptr(casing_variants) && casing_variant_count == 3) {
                ShaderPointer no_vinyl_shader @ ptr(casing_variants);
                ShaderPointer carbon_fiber_shader @ ptr(casing_variants) + 4;
                ShaderPointer chrome_shader @ ptr(casing_variants) + 8;
                if (valid_ptr(no_vinyl_shader.address)) {
                    Shader no_vinyl_variant @ ptr(no_vinyl_shader.address);
                }
                if (valid_ptr(carbon_fiber_shader.address)) {
                    Shader carbon_fiber_variant @ ptr(carbon_fiber_shader.address);
                }
                if (valid_ptr(chrome_shader.address)) {
                    Shader chrome_variant @ ptr(chrome_shader.address);
                }
            }
        }

        // drwShaderCarDecalMulti owns two concrete shader children.  The
        // variants pointer targets the two inline pointer words at +0x48.
        if (vtable == 0x0062F4C8) {
            u32 variants [[format("fmt_ptr")]];          // +0x30 -> this+0x48
            u32 active_variant;                          // +0x34, 0 normal/1 chrome
            u32 variant_count;                           // +0x38, always 2 here
            u8 local_decal_shader_index;                 // +0x3C
            u8 local_decal_tint_r_index;
            u8 local_decal_tint_g_index;
            u8 local_decal_tint_b_index;
            u8 local_decal_tint_a_index;                 // +0x40
            u8 reserved_41[7];                           // +0x41..+0x47
            u32 normal_shader [[format("fmt_ptr")]];    // +0x48
            u32 chrome_shader [[format("fmt_ptr")]];    // +0x4C

            if (valid_ptr(normal_shader)) {
                Shader normal_variant @ ptr(normal_shader);
            }
            if (valid_ptr(chrome_shader)) {
                Shader chrome_variant @ ptr(chrome_shader);
            }
        }
    }
};

struct ShaderSlot {
    u32 address [[format("fmt_ptr")]];
    if (valid_ptr(address)) {
        Shader shader @ ptr(address);
    }
};

struct ShaderLocal {
    u32 following [[format("fmt_ptr")]];
    u32 name [[format("fmt_ptr")]];
    float default_value;

    if (valid_ptr(name)) {
        CStr local_name @ ptr(name);
    }
    if (EXPAND_SHADER_LOCALS && valid_ptr(following)) {
        ShaderLocal next_local @ ptr(following);
    }
};

struct ShaderGroup {
    u32 vtable [[format("vt")]];
    u32 slots [[format("fmt_ptr")]];
    u16 slot_count;
    u16 slot_capacity;
    u32 local_head [[format("fmt_ptr")]];

    if (EXPAND_SHADERS && valid_ptr(slots) && slot_count > 0 && slot_count < 4096) {
        ShaderSlot slot[slot_count] @ ptr(slots);
    }
    if (EXPAND_SHADER_LOCALS && valid_ptr(local_head)) {
        ShaderLocal first_local @ ptr(local_head);
    }
};

// ============================================================================
// Generic hash tables and drwShaderModel
// ============================================================================

// Generic RAGE string HashTable.  Each bucket points to a linked chain of
// 0x0C-byte HashEntry objects.  `data` may be either a small integer or a
// relocatable pointer; the caller-specific fixup callback decides which.
struct HashEntry {
    u32 name [[format("fmt_ptr")]];
    u32 data;
    u32 following [[format("fmt_ptr")]];

    if (valid_ptr(name)) {
        CStr key @ ptr(name);
    }
    if (valid_ptr(following)) {
        HashEntry next_entry @ ptr(following);
    }
} [[static]];

struct HashBucket {
    u32 address [[format("fmt_ptr")]];
    if (valid_ptr(address)) {
        HashEntry first_entry @ ptr(address);
    }
} [[static]];

struct HashTable {
    s16 fixed_name_capacity;                 // meaningful only in fixed mode
    u8 uses_fixed_entries;
    u8 permanent_allocation;
    u32 fixed_entries [[format("fmt_ptr")]];
    u32 bucket_count;
    u32 entry_count;
    u32 buckets [[format("fmt_ptr")]];
    u32 kill_list_next [[format("fmt_ptr")]];

    if (valid_ptr(buckets) && bucket_count > 0 && bucket_count <= 4096) {
        HashBucket bucket[bucket_count] @ ptr(buckets);
    }
} [[static]];

// Caller-specific view of the same generic HashTable allocation. Bone tags use
// the data word as a 1-based skeleton-array index; this is not a hierarchy and
// `following` only resolves collisions inside one hash bucket.
struct BoneTagEntry {
    u32 name [[format("fmt_ptr")]];
    u32 skeleton_index_plus_one [[format("fmt_skeleton_index_plus_one")]];
    u32 following [[format("fmt_ptr")]];

    if (valid_ptr(name)) {
        CStr tag_name @ ptr(name);
    }
    if (valid_ptr(following)) {
        BoneTagEntry next_collision @ ptr(following);
    }
} [[static]];

struct BoneTagBucket {
    u32 address [[format("fmt_ptr")]];
    if (valid_ptr(address)) {
        BoneTagEntry first_entry @ ptr(address);
    }
};

struct BoneTagHashTable {
    s16 fixed_name_capacity;
    u8 uses_fixed_entries;
    u8 permanent_allocation;
    u32 fixed_entries [[format("fmt_ptr")]];
    u32 bucket_count;
    u32 entry_count;
    u32 buckets [[format("fmt_ptr")]];
    u32 kill_list_next [[format("fmt_ptr")]];

    if (valid_ptr(buckets) && bucket_count > 0 && bucket_count <= 4096) {
        BoneTagBucket bucket[bucket_count] @ ptr(buckets);
    }
} [[static]];

// drwBonyData is 0x10 bytes.  The primary table maps a tag name to
// skeleton_index+1 (zero remains the HashTable miss sentinel).  The secondary
// table is populated by LoadAnimations.  The final 8 bytes are runtime list
// state and are reset by the load-in-place constructor.
struct DrwBonyData {
    u32 bone_tags [[format("fmt_ptr")]];
    u32 animations [[format("fmt_ptr")]];
    u32 runtime_animation_list [[format("fmt_ptr")]];
    u16 runtime_animation_count;
    u16 runtime_animation_capacity;

    if (valid_ptr(bone_tags)) {
        BoneTagHashTable bone_tag_table @ ptr(bone_tags);
    }
    if (valid_ptr(animations)) {
        HashTable animation_table @ ptr(animations);
    }
} [[static]];

// Shared 0x20-byte collision polygon used by both vehicle bounds and city
// bounds. A triangle is marked by vertex[3] == 0 and neighbour[3] == FFFF;
// otherwise all four vertex indices belong to a quad. The normal is unit
// length and area is the real geometric area.
struct PhPolygon {
    Vec3 normal;                              // +0x00
    float area;                              // +0x0C
    u16 vertex[4];                           // +0x10
    u16 neighbour[4];                        // +0x18
} [[static]];

// phBoundGeometry is 0x80 bytes before its separately allocated arrays.
// The phBound base owns +0x04..+0x4B; phBoundPolyhedron adds the counts,
// runtime edge state and geometry pointers; phBoundGeometry adds materials.
// Its datResource constructors relocate +0x68/+0x6C/+0x70 explicitly.
struct PhBoundGeometry {
    u32 vtable [[format("vt")]];             // +0x00 phBoundGeometry
    u8 bound_type;                            // +0x04, geometry = 3
    u8 material_count;                        // +0x05
    u8 has_offset;                            // +0x06
    u8 has_cg_offset;                         // +0x07
    Vec3 aabb_min;                            // +0x08
    Vec3 aabb_max;                            // +0x14
    Vec3 offset;                              // +0x20, phBound::SetOffset
    Vec3 center_of_gravity_offset;            // +0x2C
    float sphere_radius;                      // +0x38
    float radius_from_origin;                 // +0x3C
    u32 unknown_40;
    u32 unknown_44;
    u16 runtime_bound_flags;                  // +0x48, 0xFDE8 after placement
    u16 reserved_4A;
    u32 vertex_count;                         // +0x4C
    u32 polygon_count;                        // +0x50
    u16 edge_count;                           // +0x54
    u16 reserved_56;
    s32 runtime_polyhedron_58;                // +0x58, reset to -1
    u32 runtime_polyhedron_5C;                // +0x5C, reset to 0
    s32 runtime_polyhedron_60;                // +0x60, reset to -1
    u32 runtime_polyhedron_64;                // +0x64, reset to 0
    u32 vertices [[format("fmt_ptr")]];      // +0x68, Vec3[vertex_count]
    u32 polygons [[format("fmt_ptr")]];      // +0x6C, PhPolygon[polygon_count]
    u32 materials [[format("fmt_ptr")]];     // +0x70, global material indices
    s32 default_material_index;               // +0x74, -1 = use table
    u32 reserved_78;
    u32 reserved_7C;

    if (valid_ptr(vertices) && vertex_count > 0 && vertex_count < 65536) {
        Vec3 vertex[vertex_count] @ ptr(vertices);
    }
    if (valid_ptr(polygons) && polygon_count > 0 && polygon_count < 65536) {
        PhPolygon polygon[polygon_count] @ ptr(polygons);
    }
    if (valid_ptr(materials) && material_count > 0 && material_count < 256) {
        u32 material_index[material_count] @ ptr(materials);
    }
} [[static]];

struct DrwShaderModel {
    u32 vtable [[format("vt")]];           // +0x00 drwShaderModel
    u8 lod_count;                           // +0x04
    u8 pad_05;
    u16 load_flags;                         // +0x06 gets bit 0x8000
    u32 shader_group [[format("fmt_ptr")]]; // +0x08 rmcShaderGroup
    u32 zero_0C;
    u32 high [[format("fmt_ptr")]];
    u32 medium [[format("fmt_ptr")]];
    u32 low [[format("fmt_ptr")]];
    u32 very_low [[format("fmt_ptr")]];
    float lod_distance[4];                  // +0x20
    s32 bucket_mask[4];                     // +0x30
    Vec3 cull_center;                        // +0x40
    float cull_radius;                       // +0x4C
    Vec3 box_min;                            // +0x50
    Vec3 box_max;                            // +0x5C
    u32 skeleton [[format("fmt_ptr")]];     // +0x68
    u32 edge_model [[format("fmt_ptr")]];
    u32 pad_70;
    u32 flags_74;
    u32 bound [[format("fmt_ptr")]];        // +0x78 phBoundGeometry
    u32 pad_7C;
    u32 bony_data [[format("fmt_ptr")]];    // +0x80 drwBonyData

    if (valid_ptr(shader_group)) {
        ShaderGroup shaders @ ptr(shader_group);
    }
    if (valid_ptr(high)) {
        LodPaged lod_high @ ptr(high);
    }
    if (valid_ptr(medium)) {
        LodPaged lod_medium @ ptr(medium);
    }
    if (valid_ptr(low)) {
        LodPaged lod_low @ ptr(low);
    }
    if (valid_ptr(very_low)) {
        LodPaged lod_very_low @ ptr(very_low);
    }
    if (valid_ptr(skeleton)) {
        SkeletonHeader skel @ ptr(skeleton);
    }
    if (valid_ptr(bound)) {
        PhBoundGeometry collision_bound @ ptr(bound);
    }
    if (valid_ptr(bony_data)) {
        DrwBonyData bone_data @ ptr(bony_data);
    }
};

// rmVehOccludeDataSet is 12 bytes.  Each 0x10-byte group inherits the
// 12-byte rmConvexSpaceData prefix (unknown word, plane count, plane pointer);
// the last word is still intentionally unnamed.
struct RmOccludeGroupData {
    u32 unknown_00;
    u32 plane_count;
    u32 planes [[format("fmt_ptr")]];
    u32 unknown_0C;
} [[static]];

struct RmVehOccludeDataSet {
    s32 type_or_version;
    u32 group_count;
    u32 groups [[format("fmt_ptr")]];

    if (valid_ptr(groups) && group_count > 0 && group_count <= 64) {
        RmOccludeGroupData group[group_count] @ ptr(groups);
    }
} [[static]];

// 0x64-byte mcSpotlightTuneParams. The 15 named floats are the exact fields
// serialized by mcSpotlightTuneParams::FileIO; the trailing integer is consumed
// by mcSpotlight::draw but is distinct from the global cone-segment count.
struct McSpotlightTuneParams {
    u32 vtable [[format("vt")]];
    u32 config_name [[format("fmt_ptr")]];
    u32 zero_08;
    u32 reserved_0C;
    float color_r;
    float color_g;
    float color_b;
    float brightness;
    float x_scale;
    float y_scale;
    float z_scale;
    float x_offset;
    float y_offset;
    float z_offset;
    float high_beam_y_scale;
    u8 runtime_or_legacy_3C;
    u8 pad_3D[3];
    float sharpness;
    float falloff;
    float aim_tweak;
    u32 runtime_or_legacy_4C;
    u32 runtime_or_legacy_50;
    float high_beam_tweak;
    u32 runtime_or_legacy_58;
    u32 runtime_or_legacy_5C;
    s32 render_intensity;

    if (valid_ptr(config_name)) {
        CStr tune_name @ ptr(config_name);
    }
} [[static]];

// ============================================================================
// rmcCarModelType: shader bindings, customization and vehicle render metadata
// ============================================================================

struct RmcCarModelTypePrefix {
    u32 version_or_count;
    u32 type_registry [[format("fmt_ptr")]]; // +0x04, filled at runtime
    u32 type_name [[format("fmt_ptr")]];     // +0x08, SetName
    u32 unknown_0C [[format("fmt_ptr")]];
    u32 active_type_prev [[format("fmt_ptr")]];
    u32 active_type_next [[format("fmt_ptr")]];
    u32 active_instances [[format("fmt_ptr")]]; // +0x18, linked list head
    u8 pad_1C[0x0C];
    u32 base_vtable [[format("vt")]];       // +0x28 mcCullableType
    // 76 u16 shader-local bindings, exactly +0x2C..+0xC3.  A value is an
    // index, not a light intensity: consumers multiply it by four and add a
    // rmcCarModelShaderLocalData buffer.  0xFFFF means that the vehicle does
    // not expose the local.  Body locals are serialized; wheel/caliper locals
    // begin as FFFF and are resolved after their streamed models arrive.
    u16 local_tlights_on_index;                 // +0x2C; 350Z 38
    u16 local_blights_on_index;                 // +0x2E; 350Z 39
    u16 local_thirdbrake_on_index;               // +0x30; 350Z 29
    u16 local_headlights_on_index;               // +0x32; 350Z 30
    u16 local_taillight_lights_on_index;         // +0x34; 350Z 36
    u16 local_coloredglass_headlightval_index;   // +0x36; 350Z 18
    u16 local_coloredglass_brakelightval_index;  // +0x38; 350Z 19
    u16 local_reverselight_lights_on_index;      // +0x3A; 350Z 37
    u16 local_coplights1_on_index;               // +0x3C
    u16 local_coplights2_on_index;               // +0x3E
    u16 local_coplightbar_on_index;              // +0x40; code-only optional
    u16 local_coplightbaridx_index;              // +0x42; code-only optional

    u16 local_CarPaintEnvScale_index;            // +0x44
    u16 local_CarPaintFresnelMin_index;           // +0x46
    u16 local_CarPaintFresnelMax_index;           // +0x48
    u16 local_CarPaintFresnelScale_index;         // +0x4A
    u16 local_WinFresnelEnvScale_index;           // +0x4C
    u16 local_WinFresnelMin_index;                // +0x4E
    u16 local_WinFresnelMax_index;                // +0x50
    u16 local_WinFresnelScale_index;              // +0x52
    u16 local_tintR_index;                        // +0x54
    u16 local_tintG_index;                        // +0x56
    u16 local_tintB_index;                        // +0x58
    u16 local_tintA_index;                        // +0x5A
    u16 local_WindowsBroken_index;                // +0x5C

    // Resolved by drwShaderBrakeCaliper::Load from the streamed caliper
    // shader group.  On this capture they changed FFFF -> 0,1,2,3.
    u16 local_caliperR_index;                     // +0x5E
    u16 local_caliperG_index;                     // +0x60
    u16 local_caliperB_index;                     // +0x62
    u16 local_caliperA_index;                     // +0x64
    u16 reserved_local_66;                        // +0x66; always FFFF, unread

    u16 local_shadowR_index;                      // +0x68
    u16 local_shadowG_index;                      // +0x6A
    u16 local_shadowB_index;                      // +0x6C
    u16 local_shadowA_index;                      // +0x6E
    u16 local_isAdditive_index;                   // +0x70
    u16 local_HoodShaderIdx_index;                // +0x72
    u16 local_TaillightCasingShaderIdx_index;     // +0x74

    // Three speed/temperature locals belong to separately streamed wheel
    // and brake-caliper local buffers.
    u16 local_brakeTemp_index;                    // +0x76; runtime lookup
    u16 local_caliper_rimSpeed_index;             // +0x78; runtime lookup
    u16 local_wheel_rimSpeed_index;               // +0x7A; runtime lookup
    u16 local_trim_color_r_index;                 // +0x7C
    u16 local_trim_color_g_index;                 // +0x7E
    u16 local_trim_color_b_index;                 // +0x80
    u16 local_trim_color_a_index;                 // +0x82
    u16 local_chrome_color_r_index;               // +0x84
    u16 local_chrome_color_g_index;               // +0x86
    u16 local_chrome_color_b_index;               // +0x88
    u16 local_chrome_color_a_index;               // +0x8A

    u16 local_side_exhaust_chrome_color_r_index;  // +0x8C
    u16 local_side_exhaust_chrome_color_g_index;  // +0x8E
    u16 local_side_exhaust_chrome_color_b_index;  // +0x90
    u16 local_side_exhaust_chrome_color_a_index;  // +0x92
    // Same custom exhaust colour, but in the separately streamed masked-
    // chrome shader group.  Its local names are simply chrome_color_*.
    u16 local_exhaust_chrome_color_r_index;       // +0x94; runtime lookup
    u16 local_exhaust_chrome_color_g_index;       // +0x96; runtime lookup
    u16 local_exhaust_chrome_color_b_index;       // +0x98; runtime lookup
    u16 local_exhaust_chrome_color_a_index;       // +0x9A; runtime lookup
    u16 local_rim_chrome_color_r_index;           // +0x9C; runtime lookup
    u16 local_rim_chrome_color_g_index;           // +0x9E; runtime lookup
    u16 local_rim_chrome_color_b_index;           // +0xA0; runtime lookup
    u16 local_rim_chrome_color_a_index;           // +0xA2; runtime lookup
    u16 local_rim_insert_color_r_index;           // +0xA4; runtime lookup
    u16 local_rim_insert_color_g_index;           // +0xA6; runtime lookup
    u16 local_rim_insert_color_b_index;           // +0xA8; runtime lookup
    u16 local_rim_insert_color_a_index;           // +0xAA; runtime lookup

    u16 local_DecalShaderIdx_index;               // +0xAC
    u16 local_DecalShaderTintR_index;             // +0xAE
    u16 local_DecalShaderTintG_index;             // +0xB0
    u16 local_DecalShaderTintB_index;             // +0xB2
    u16 local_DecalShaderTintA_index;             // +0xB4
    u16 reserved_local_B6;                        // +0xB6; always FFFF, unread
    u16 reserved_local_B8;                        // +0xB8; always FFFF, unread
    u16 reserved_local_BA;                        // +0xBA; always FFFF, unread
    u16 reserved_local_BC;                        // +0xBC; always FFFF, unread
    u16 local_CoplightRFlasher_index;             // +0xBE
    u16 local_CoplightLFlasher_index;             // +0xC0
    u16 local_sprocketSpeed_index;                // +0xC2

    // The requested range ends above.  These five runtime-selected streamed
    // resources begin at EE 0x01773794 in the supplied 350Z capture.
    u32 runtime_shader_resource_C4 [[format("fmt_ptr")]];
    u32 runtime_shader_resource_C8 [[format("fmt_ptr")]];
    u32 wheel_shader_resource [[format("fmt_ptr")]];          // +0xCC
    u32 brake_caliper_shader_resource [[format("fmt_ptr")]];  // +0xD0
    u32 masked_chrome_shader_resource [[format("fmt_ptr")]];  // +0xD4
    u32 shader_model [[format("fmt_ptr")]];                   // +0xD8
    u32 skeleton [[format("fmt_ptr")]];                       // +0xDC

    u32 unknown_E0;                                           // zero in 117/117
    u16 unknown_E4;                                           // 0 or 1
    u16 pad_E6;                                               // CDCD in 117/117

    // +0xE8..+0x2DF: fixed capacity of 21 choices.  Unused records are zero.
    // rmcCarModel::UpdateExhaustDamagePos selects:
    //   this + 0xE8 + rear_bumper * 24 + exhaust_index * 12
    RearBumperExhaustPair rear_bumper_exhaust[21];

    // Serialized and runtime-zero in all 117 canonical vp_*.pck packages.
    u32 reserved_2E0[18];

    // Duplicates SkeletonHeader::count. ComputeMatrixRemapTable and Init use
    // it as the bone/matrix count; it matched the header in 117/117 packages.
    u32 skeleton_node_count;                                  // +0x328
    u32 nitro_emitter_count;                                  // +0x32C; 0..2
    u32 reserved_330;                                         // zero, 117/117
    u32 reserved_334;                                         // zero, 117/117
    u32 reserved_338;                                         // zero, 117/117

    // +0x33C..+0x38F and +0x3F0..+0x158F remain literal 0xCD padding after
    // loading.  They are kept explicit so later fields retain their proven
    // offsets and ImHex does not mistake padding for relocated pointers.
    u8 reserved_33C[0x54];
    Matrix34 nitro_emitter_matrix[2];                          // +0x390
    u8 reserved_3F0[0x11A0];

    // Fixed-capacity serialized node-ID table.  Only the first
    // nitro_emitter_count entries are populated; the rest are FFFFFFFF.
    // mcCarParticles::UpdateExhaustParticles resolves entries 0 and 1 to
    // live skeleton matrices in its stock/fallback path.
    u32 nitro_emitter_skeleton_id[24];                         // +0x1590
    u8 reserved_15F0[0x120];

    // Custom-exhaust choice tables. mcCarCustom+0x11E8 selects one of the 24
    // rows when the byte at +0x11EC is set. Each row permits four emitter
    // nodes; the matching skeleton IDs are mapped below at type+0x2970.
    u32 custom_exhaust_emitter_count[24];                      // +0x1710

    // +0x1770..+0x296F: every one of these 96 matrices is identity in all
    // 117 canonical packages, including all 403 count-declared entries. No
    // retail instruction directly addresses +0x1770. Keep the legacy table
    // visible, but do not mistake it for populated per-node transforms.
    ExhaustEmitterMatrixSet custom_exhaust_legacy_matrix[24]; // +0x1770

    // +0x2970..+0x2AEF: matching IDs for the count table at +0x1710.
    // UpdateExhaustParticles reads one selected row when custom exhaust is on.
    CustomExhaustSkeletonSet custom_exhaust_skeleton[24];     // +0x2970

    // Names in all 117 skeletons identify these two as slipstream anchors,
    // but Remix has no direct immediate reader for either serialized field.
    // The cone ID is read by rmcCarModel::Render.
    u32 slipstream_left_skeleton_id;                          // +0x2AF0
    u32 slipstream_right_skeleton_id;                         // +0x2AF4
    u32 headlight_cone_skeleton_id;                           // +0x2AF8

    // Customization lists contain skeleton-node indices; FFFFFFFF is empty.
    // AddNamesToPartDatabase proves the category names and the count fields
    // below. All lists have capacity 24 except body, whose real capacity is 8.
    u32 front_bumper_skeleton[24];                            // +0x2AFC
    u32 rear_bumper_skeleton[24];                             // +0x2B5C
    u32 side_skirt_skeleton[24];                              // +0x2BBC
    u32 spoiler_skeleton[24];                                 // +0x2C1C
    u32 hood_skeleton[24];                                    // +0x2C7C
    u32 blower_skeleton[24];                                  // +0x2CDC

    // UpdateBlower indexes this with the same blower selection and animates
    // the secondary valve/scoop node while blower_skeleton selects the root.
    u32 blower_animation_skeleton[24];                        // +0x2D3C

    // Fixed mechanical/effect nodes. Popup-spoiler code consumes the root and
    // four flap IDs as one five-entry set.
    u32 trunk_skeleton_id;                                    // +0x2D9C
    u32 neon_shadow_skeleton_id;                              // +0x2DA0
    u32 front_fork_skeleton_id;                               // +0x2DA4
    u32 rear_axle_skeleton_id;                                // +0x2DA8
    u32 steering_skeleton_id;                                 // +0x2DAC
    u32 reserved_2DB0[4];                                    // FFFFFFFF, 117/117
    u32 popup_spoiler_root_skeleton_id;                       // +0x2DC0
    u32 popup_spoiler_flap_skeleton_id[4];                    // +0x2DC4

    u32 headlight_skeleton[24];                               // +0x2DD4
    u32 taillight_skeleton[24];                               // +0x2E34

    // Two exhaust nodes per rear-bumper option. +0x3194 is the total number
    // of non-FFFF u32 entries, not the number of populated rows.
    RearBumperExhaustSkeletonPair rear_bumper_exhaust_skeleton[24]; // +0x2E94
    u32 car_file_exhaust_skeleton[24];                        // +0x2F54
    u32 body_skeleton[8];                                     // +0x2FB4
    u32 brush_guard_skeleton[24];                             // +0x2FD4
    u32 front_grille_skeleton[24];                            // +0x3034
    u32 one_shot_kit_skeleton[24];                            // +0x3094
    u32 wheelie_bar_skeleton[24];                             // +0x30F4

    // DrawEngineFire chooses one of these two IDs.
    u32 engine_fire_skeleton_id[2];                           // +0x3154

    // Embedded polymorphic object. Its pointers/vtable are the only words in
    // this range changed by load-in-place relocation in the supplied capture.
    CarWindowDamage window_damage;                            // +0x315C

    u32 front_bumper_count;                                  // +0x3174
    u32 rear_bumper_count;                                   // +0x3178
    u32 side_skirt_count;                                    // +0x317C
    u32 spoiler_count;                                       // +0x3180
    u32 hood_count;                                          // +0x3184
    u32 blower_count;                                        // +0x3188
    u32 headlight_count;                                     // +0x318C
    u32 taillight_count;                                     // +0x3190
    u32 rear_bumper_exhaust_skeleton_count;                  // +0x3194
    u32 car_file_exhaust_count;                              // +0x3198
    u32 reserved_319C;                                       // zero, 117/117
    u32 body_count;                                          // +0x31A0
    u32 brush_guard_count;                                   // +0x31A4
    u32 front_grille_count;                                  // +0x31A8
    u32 one_shot_kit_count;                                  // +0x31AC
    u32 wheelie_bar_count;                                   // +0x31B0
    u32 reserved_31B4[3];                                    // FFFFFFFF, 117/117

    // One closed/local base transform for each selectable blower.  The
    // secondary blower node table at +0x2D3C uses the same choice index.
    Matrix34 blower_animation_base_matrix[24];                // +0x31C0

    // Pop-up headlights share one target X rotation.  CDCDCDCD means that
    // the feature is absent; 11 of 117 retail packages populate it.
    float popup_headlight_target_angle;                       // +0x3640, radians
    u8 reserved_3644[0x5C];                                  // no direct reader

    // Five possible pop-up spoiler pieces (root plus four flaps).  Only
    // entries with a non-FFFFFFFF skeleton ID at +0x2DC0 are meaningful.
    Vec3 popup_spoiler_rotation_xyz[5];                       // +0x36A0
    Vec3 popup_spoiler_open_position[5];                      // +0x36DC
    u8 reserved_3718[0x08];
    Matrix34 popup_spoiler_closed_matrix[5];                  // +0x3720

    u32 wheel_records [[format("fmt_ptr")]];                 // +0x3810
    u32 wheel_count;                                          // +0x3814; 4 car, 2 bike
    u32 car_description [[format("fmt_ptr")]];               // +0x3818, runtime mc::CarDescription*
    AtBitSet neon_shadow_skeleton_mask;                       // +0x381C

    // Cars split negative-X and positive-X wheels (0/2 versus 1/3).
    // Motorcycles put both inline wheels in side A and leave side B null.
    WheelRecordPointer wheel_side_a[8];                       // +0x3824
    WheelRecordPointer wheel_side_b[8];                       // +0x3844

    u32 spotlight_tune [[format("fmt_ptr")]];                // +0x3864, mcSpotlightTuneParams*
    u32 special_shader_group [[format("fmt_ptr")]];          // +0x3868, three slots
    u32 distort_shader [[format("fmt_ptr")]];                // +0x386C, distort.shadert
    u32 rider_stealth_shader [[format("fmt_ptr")]];          // +0x3870; two cars use decal chrome
    u32 rider_chrome_shader [[format("fmt_ptr")]];           // +0x3874

    // rmcCarModel::Render copies these into drwShaderModel+0x20, scales them
    // for model size/view angle, then selects H/M/L/VL by camera distance.
    float base_lod_distance[4];                               // +0x3878: 30,50,100,110

    u32 vehicle_occlude_dataset [[format("fmt_ptr")]];       // +0x3888
    u32 reserved_388C;                                       // CDCDCDCD

    if (valid_ptr(wheel_records) && wheel_count > 0 && wheel_count <= 8) {
        WheelRecord wheel_record[wheel_count] @ ptr(wheel_records);
    }
    if (valid_ptr(spotlight_tune)) {
        McSpotlightTuneParams spotlight @ ptr(spotlight_tune);
    }
    if (valid_ptr(special_shader_group)) {
        ShaderGroup special_shaders @ ptr(special_shader_group);
    }
    // These three pointers alias the three slots of special_shader_group.
    // Expand them only as a fallback, otherwise ImHex paints each shader twice.
    if (!valid_ptr(special_shader_group)) {
        if (valid_ptr(distort_shader)) {
            Shader distort @ ptr(distort_shader);
        }
        if (valid_ptr(rider_stealth_shader)) {
            Shader rider_stealth @ ptr(rider_stealth_shader);
        }
        if (valid_ptr(rider_chrome_shader)) {
            Shader rider_chrome @ ptr(rider_chrome_shader);
        }
    }
    if (valid_ptr(vehicle_occlude_dataset)) {
        RmVehOccludeDataSet occlusion @ ptr(vehicle_occlude_dataset);
    }

    if (valid_ptr(shader_model)) {
        DrwShaderModel model @ ptr(shader_model);
    }
};

// ============================================================================
// Vehicle tune/performance objects already decoded by mc3_perf.py
// ============================================================================

struct NamedTuneHeader {
    u32 vtable [[format("vt")]];
    u32 config_name [[format("fmt_ptr")]];
    u32 unknown_08;
    if (valid_ptr(config_name)) {
        CStr name_text @ ptr(config_name);
    }
} [[static]];

struct VehInputTune {
    NamedTuneHeader header;
    float sss_threshold;
    float sss_value;
    float legacy_constant_6_705;              // +0x14, not parsed or read
    float wheel_range;                        // +0x18, UpdateInput
    float front_wheel_amplitude_divisor;      // +0x1C, UpdateEffects
    float front_wheel_amplitude_exponent;     // +0x20, UpdateEffects
    u32 constant_magnitude;                   // +0x24, UpdateEffects
    u32 spring_magnitude;                     // +0x28, UpdateEffects
    u32 damper_magnitude;                     // +0x2C, UpdateEffects
    float steer_rate;                         // +0x30, UpdateInput
    float handbrake_rate;                     // +0x34, UpdateInput
    float drift_rate;                         // +0x38, UpdateInput
} [[static]];

struct VehDamageTune {
    NamedTuneHeader header;
    float max_damage;
    float medium_damage;
    float impact_threshold;
    float wipeout_threshold;
    float weight_transfer_threshold;
    float explode_threshold;
    float jolt_threshold;
    float fatal_blow;
    float wipeout_delay;
    float damage_out_delay;
    u8 reserved_34[8];
    float explode_impulse;
    float spark_start_r;
    float spark_start_g;
    float spark_start_b;
    float spark_start_a;
    float spark_end_r;
    float spark_end_g;
    float spark_end_b;
    float spark_end_a;
    float spark_velocity_variance_a;
    float spark_velocity_variance_b;
    float spark_velocity_variance_c;
    float roof_spark_velocity_variance_a;
    float roof_spark_velocity_variance_b;
    float roof_spark_velocity_variance_c;
    float spark_lifetime;
    float spark_emit_modifier;
    float spark_size;
    u32 bottom_out_cap;
} [[static]];

struct VehModelTune {
    NamedTuneHeader header;
    float suspension_limit_front;
    float suspension_limit_rear;
    float tire_diameter_limit_front;
    float tire_diameter_limit_rear;
    float tire_extent_front;
    float tire_extent_rear;
    float suspension_clearance_front;
    float suspension_clearance_rear;
    float tire_steer_limit;
    float ground_clearance;
} [[static]];

struct WheelTune {
    NamedTuneHeader header;
    float tire_displacement_limit_lateral;
    float tire_displacement_limit_longitudinal;
    float tire_damping_lateral;
    float tire_damping_longitudinal;
    float surface_drag_lateral;
    float surface_drag_longitudinal;
    float steering_limit;
    float camber_limit;
    float wobble_limit;
    float axle_limit;
    float brake_coefficient;
    float handbrake_coefficient;
    float steering_offset;
    float travel_limit;
    float travel_extent;
    float suspension_offset;
    float suspension_limit;
    float suspension_extent;
    float suspension_clamp;
    float suspension_factor;
    float suspension_damping_coefficient;
    float suspension_damping_factor;
    float optimum_slip_percent;
    float static_friction;
    float sliding_friction;
} [[static]];

struct EngineTune {
    float angular_inertia;
    float maximum_horsepower;
    float idle_rpm;
    float optimum_rpm;
    float maximum_rpm;
    float engage_rpm;
    float gcl;
    float gear_change_throttle;
    float boost_duration;
    float boost_horsepower;
} [[static]];

struct TransmissionTune {
    u32 manual_gear_count;                  // includes reverse and neutral
    float reverse;
    float low;
    float high;
    float upshift_bias;
    float downshift_bias_min;
    float downshift_bias_max;
    u32 maximum_downshifts;
    float gear_bias;
    float gear_change_time;
} [[static]];

struct DrivetrainTune {
    float angular_inertia;
    float brake_dynamic_coefficient;
    float brake_static_coefficient;
} [[static]];

struct AxleTune {
    float torque_coefficient;
    float damping_coefficient;
} [[static]];

struct AeroTune {
    u32 unknown_00;
    float angular_damping_x;
    float angular_damping_y;
    float angular_damping_z;
    float angular_velocity_damping_x;
    float angular_velocity_damping_y;
    float angular_velocity_damping_z;
    float angular_velocity_squared_damping_x;
    float angular_velocity_squared_damping_y;
    float angular_velocity_squared_damping_z;
    float unknown_28;
    float down;
    u8 reserved_30[8];
    float slipstream_drag;
    float drag;
} [[static]];

struct FluidTune {
    float damping;
    float unknown_04;
    float current;
    u32 reserved_0C;
    float buoyancy;
    float minimum_buoyancy;
    float sink_rate;
} [[static]];

struct NitroTune {
    u16 nitro_count;
    u16 reserved_02;
    float boost_exponent;
    float boost_amount;
    float boost_time;
    float fov;
    float fov_out_time;
    float fov_in_time;
    float flame_time;
    float flame_size;
} [[static]];

struct SuperStopTurboTune {
    float boost_exponent;
    float boost_amount;
    float use_time;
    float regeneration_time;
    float falloff_time;
    float charged_falloff_time;
    float stay_full_time;
    float lose_charge_speed;
    float lose_charge_speed_2;
    float unknown_24;
    float fov_out_time;
    float fov_in_time;
    float turbo_fov;
    float turbo_distance;
    float blur_off_time;
    float blur_start_amount;
    float envelope_d1;
    float envelope_d2;
    float envelope_d3;
    float flame_time;
    float flame_size;
    float smoke_size;
    u8 reserved_58[8];
    float smoke_start_r;
    float smoke_start_g;
    float smoke_start_b;
    float smoke_start_a;
    float smoke_end_r;
    float smoke_end_g;
    float smoke_end_b;
    float smoke_end_a;
} [[static]];

struct HydraulicsTune {
    float release_time;
    float front_rise;
    float rear_rise;
    float front_drop;
    float rear_drop;
    float rise_rate;
    float drop_rate;
    float suspension_extent;
    float suspension_damping;
    float mass_offset;
    float unknown_28;
} [[static]];

struct AiInfoTune {
    u32 unknown_00;
    float turn_constant;
    float handbrake_early_time;
    float handbrake_steer_multiplier;
    float maximum_brake_deceleration;
    float steering_scalar;
    float steering_gamma;
    float car_friction_handling;
    float friction_multiplier;
    float sliding_friction_factor;
    float unknown_28;
} [[static]];

struct VehSimTune {
    NamedTuneHeader header;
    float model_offset_x;
    float model_offset_y;
    float model_offset_z;
    float center_of_mass_x;
    float center_of_mass_y;
    float center_of_mass_z;
    float mass;
    float size_x;
    float size_y;
    float size_z;
    float inertia_box_x;
    float inertia_box_y;
    float inertia_box_z;
    float bound_friction;
    float bound_elasticity;
    float bound_gravity;
    float air_gravity;
    u32 drivetrain_type;                    // 0 RWD, 1 FWD, 2/3 AWD variants

    u32 wheel_front [[format("fmt_ptr")]]; // +0x54
    u32 wheel_rear [[format("fmt_ptr")]];
    u32 engine [[format("fmt_ptr")]];
    u32 transmission [[format("fmt_ptr")]];
    u32 drivetrain [[format("fmt_ptr")]];
    u32 freetrain [[format("fmt_ptr")]];
    u32 axle_front [[format("fmt_ptr")]];
    u32 axle_rear [[format("fmt_ptr")]];
    u32 aero [[format("fmt_ptr")]];
    u32 fluid [[format("fmt_ptr")]];
    u32 reserved_subsystem_0A [[format("fmt_ptr")]];
    u32 nitro [[format("fmt_ptr")]];
    u32 super_stop_turbo [[format("fmt_ptr")]];
    u32 unknown_module [[format("fmt_ptr")]];
    u32 hydraulics [[format("fmt_ptr")]];
    u32 ai_info [[format("fmt_ptr")]];

    if (EXPAND_PERFORMANCE && valid_ptr(wheel_front)) {
        WheelTune front_wheel @ ptr(wheel_front);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(wheel_rear)) {
        WheelTune rear_wheel @ ptr(wheel_rear);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(engine)) {
        EngineTune engine_data @ ptr(engine);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(transmission)) {
        TransmissionTune transmission_data @ ptr(transmission);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(drivetrain)) {
        DrivetrainTune drivetrain_data @ ptr(drivetrain);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(freetrain)) {
        DrivetrainTune freetrain_data @ ptr(freetrain);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(axle_front)) {
        AxleTune front_axle @ ptr(axle_front);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(axle_rear)) {
        AxleTune rear_axle @ ptr(axle_rear);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(aero)) {
        AeroTune aero_data @ ptr(aero);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(fluid)) {
        FluidTune fluid_data @ ptr(fluid);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(nitro)) {
        NitroTune nitro_data @ ptr(nitro);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(super_stop_turbo)) {
        SuperStopTurboTune turbo_data @ ptr(super_stop_turbo);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(unknown_module)) {
        VtableAlias unknown_module_header @ ptr(unknown_module);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(hydraulics)) {
        HydraulicsTune hydraulics_data @ ptr(hydraulics);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(ai_info)) {
        AiInfoTune ai_data @ ptr(ai_info);
    }
} [[static]];

struct VehGyroTune {
    NamedTuneHeader header;
    float turn;
    float drift;
    float spin_180;
    float reverse;
    float yaw;
    float pitch;
    float roll_torque;
    float lean;
    float wheelie;
    float turn_factor;
    u32 reserved_34;
    float turn_limit;
    float turn_damping;
    float turn_bias;
    float lean_factor;
    float lean_angle;
    float lean_angle_maximum;
    float lean_rate;
    float lean_limit;
    float lean_damping;
    float lean_impulse_up;
    float lean_impulse_down;
    float lean_speed_minimum;
    float lean_steer_limit;
    float lean_center_of_gravity;
    float noise_angle;
    float wheelie_angle;
    float wheelie_rate;
    float wheelie_limit;
    float wheelie_damping;
    float roll_limit;
    float roll_damping;
    float drift_thrust;
    float drift_decay;
    float wheelie_boost_acceleration;
    float wheelie_boost_duration;
    float wobble_amplitude;
    float wobble_frequency;
    float burnout_rate;
    float burnout_limit;
    float burnout_turn;
    float burnout_turn_reverse;
    float burnout_lean;
    float burnout_grip;
    float reverse_acceleration;
    float reverse_limit;
} [[static]];

struct VehZoneTune {
    NamedTuneHeader header;
    float center_of_mass_x;
    float center_of_mass_y;
    float center_of_mass_z;
    float sss_value;
    float static_friction;
    float sliding_friction;
    float optimum_slip_percent;
    float steering_limit;
    float turn_bias;
    float turn_factor;
    float turn_torque;
    float turn_limit;
    float turn_damping;
    float bike_lean_factor;
    float bike_lean_torque;
    float bike_lean_limit;
    float bike_lean_damping;
    float bike_lean_rate;
} [[static]];

struct EngineUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 engine_hp;
    u16 engine_rpm;
    u16 gear_ratios;
    u16 drag;
    u16 wheelie;
    u16 burnout;
    u16 nitro;
    u16 wobble;
    u16 backfire;
} [[static]];

struct TransmissionUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 shift_time;
    u16 boost;
    u16 gear_count;
    u16 gear_ratios;
} [[static]];

struct BrakeUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 brake_coefficient;
    u16 handbrake_coefficient;
} [[static]];

struct SuspensionUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 spring;
    u16 shock;
    u16 drift;
    u16 turn;
    u16 sss;
    u16 simulation_center_of_gravity;
    u16 spin;
    u16 axle;
    u16 inertia;
    u16 angular_damping;
} [[static]];

struct TireUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 tread;
    u16 sidewall;
    u16 compound;
} [[static]];

struct HydraulicsUpgradeRecord {
    u16 category;
    u16 level_count;
    u16 progression[4];
    u16 pump;
} [[static]];

// The six pointer slots are not in numeric category order: the category ID is
// the first u16 of the target record (the 350Z stores Suspension before Brake).
// mc3_perf.py dispatches on that ID and caps each list at its known shop item
// count, which also prevents one list from running into the next one.
struct UpgradeCategoryPointer {
    u32 address [[format("fmt_ptr")]];

    if (EXPAND_PERFORMANCE && valid_ptr(address)) {
        if (std::mem::read_unsigned(ptr(address), 2) == 0) {
            EngineUpgradeRecord exhaust_headers_intake_turbo_upgrade_chip_nitro[7] @ ptr(address);
        }
        if (std::mem::read_unsigned(ptr(address), 2) == 1) {
            TransmissionUpgradeRecord clutch_gears[2] @ ptr(address);
        }
        if (std::mem::read_unsigned(ptr(address), 2) == 2) {
            BrakeUpgradeRecord brakes[1] @ ptr(address);
        }
        if (std::mem::read_unsigned(ptr(address), 2) == 3) {
            SuspensionUpgradeRecord shocks_springs_swaybar[2] @ ptr(address);
        }
        if (std::mem::read_unsigned(ptr(address), 2) == 4) {
            TireUpgradeRecord tires[1] @ ptr(address);
        }
        if (std::mem::read_unsigned(ptr(address), 2) == 5) {
            HydraulicsUpgradeRecord pneumatic_hydraulic[2] @ ptr(address);
        }
    }
} [[static]];

struct VehModsTune {
    NamedTuneHeader header;
    UpgradeCategoryPointer category_slot[6];   // +0x0C..+0x23
} [[static]];

// ============================================================================
// Vehicle audio objects already decoded by bnk_tool/pck.py
// ============================================================================

struct AudioRangeRecord {
    char sample_name[0x20];
    float minimum_rpm;
    float maximum_rpm;
    float middle_rpm;
} [[static]];

struct AudioCurveRow {
    float x0;
    float y0;
    float control_x;
    float control_y;
    float x1;
    float y1;
    float quadratic_a;
    float quadratic_b;
    float quadratic_c;
} [[static]];

struct AudioCurve {
    u32 class_token_or_vtable;
    float minimum_x;
    float minimum_y;
    float maximum_x;
    float maximum_y;
    u32 active_series_count;
    u32 rows [[format("fmt_ptr")]];
    u32 unknown_1C;

    if (EXPAND_AUDIO_CURVES && valid_ptr(rows) && active_series_count <= 8) {
        AudioCurveRow allocated_row[8] @ ptr(rows);
    }
} [[static]];

struct AudioBlockFull {
    u32 class_token_or_vtable;
    char bank_name[0x20];
    float minimum_recorded_rpm;
    float maximum_recorded_rpm;
    float near_distance;
    float far_distance;
    u32 active_range_count;
    u32 ranges [[format("fmt_ptr")]];
    float warble_minimum_rpm;
    float warble_maximum_rpm;
    float warble_minimum_rpm_delta;
    float warble_minimum_volume_delta;
    float warble_maximum_rpm_delta;
    float warble_maximum_volume_delta;
    float reverse_warble_maximum_rpm_delta;
    float reverse_warble_period_rate;
    float boost_mix_percent;
    float boost_mix_rate;
    u32 high_pitch_curve_engage_gear;
    char engine_rev_sound[0x20];
    u32 volume_curve [[format("fmt_ptr")]];
    u32 pitch_curve [[format("fmt_ptr")]];
    u32 high_pitch_curve [[format("fmt_ptr")]];
    u32 upshift_curve [[format("fmt_ptr")]];
    u32 downshift_curve [[format("fmt_ptr")]];
    u32 unknown_9C;

    if (EXPAND_VEHICLE_AUDIO && valid_ptr(ranges) && active_range_count <= 8) {
        AudioRangeRecord allocated_range[8] @ ptr(ranges);
    }
    if (EXPAND_AUDIO_CURVES && valid_ptr(volume_curve)) {
        AudioCurve volume @ ptr(volume_curve);
    }
    if (EXPAND_AUDIO_CURVES && valid_ptr(pitch_curve)) {
        AudioCurve pitch @ ptr(pitch_curve);
    }
    if (EXPAND_AUDIO_CURVES && valid_ptr(high_pitch_curve)) {
        AudioCurve high_pitch @ ptr(high_pitch_curve);
    }
    if (EXPAND_AUDIO_CURVES && valid_ptr(upshift_curve)) {
        AudioCurve upshift @ ptr(upshift_curve);
    }
    if (EXPAND_AUDIO_CURVES && valid_ptr(downshift_curve)) {
        AudioCurve downshift @ ptr(downshift_curve);
    }
} [[static]];

struct AudioCommonProfile {
    u32 class_token_or_vtable;
    float parameter[22];
} [[static]];

struct VehicleAudioRoot {
    u32 vtable [[format("vt")]];
    u32 config_name [[format("fmt_ptr")]];
    u32 unknown_08;
    u32 unknown_0C;
    u32 unknown_10;
    u32 unknown_14;
    u32 impact_level0 [[format("fmt_ptr")]];
    u32 big_air_level0 [[format("fmt_ptr")]];
    u32 wind_level0 [[format("fmt_ptr")]];
    u32 horn_level0 [[format("fmt_ptr")]];
    u32 powerup_level0 [[format("fmt_ptr")]];
    u32 fire_level0 [[format("fmt_ptr")]];
    u32 wheel_level0 [[format("fmt_ptr")]];
    u32 wheel_level1 [[format("fmt_ptr")]];
    u32 wheel_level2 [[format("fmt_ptr")]];
    u32 suspension_level0 [[format("fmt_ptr")]];
    u32 suspension_level1 [[format("fmt_ptr")]];
    u32 suspension_level2 [[format("fmt_ptr")]];
    u32 gearshift_level0 [[format("fmt_ptr")]];
    u32 gearshift_level1 [[format("fmt_ptr")]];
    u32 gearshift_level2 [[format("fmt_ptr")]];
    u32 backfire_level0 [[format("fmt_ptr")]];
    u32 backfire_level1 [[format("fmt_ptr")]];
    u32 backfire_level2 [[format("fmt_ptr")]];
    u32 turbo_blower_level0 [[format("fmt_ptr")]];
    u32 turbo_blower_level1 [[format("fmt_ptr")]];
    u32 turbo_blower_level2 [[format("fmt_ptr")]];
    u32 engine_level0 [[format("fmt_ptr")]];
    u32 engine_level1 [[format("fmt_ptr")]];
    u32 engine_level2 [[format("fmt_ptr")]];
    u32 engine_level3 [[format("fmt_ptr")]];
    u32 engine_level4 [[format("fmt_ptr")]];
    u32 exhaust_level0 [[format("fmt_ptr")]];
    u32 exhaust_level1 [[format("fmt_ptr")]];
    u32 exhaust_level2 [[format("fmt_ptr")]];
    u32 exhaust_level3 [[format("fmt_ptr")]];
    u32 exhaust_level4 [[format("fmt_ptr")]];
    u32 common_level0 [[format("fmt_ptr")]];
    u32 common_level1 [[format("fmt_ptr")]];
    u32 common_level2 [[format("fmt_ptr")]];
    u32 common_level3 [[format("fmt_ptr")]];
    u32 common_level4 [[format("fmt_ptr")]];
    u8 unknown_A8[0x10];

    if (valid_ptr(config_name)) {
        CStr audio_name @ ptr(config_name);
    }
    if (EXPAND_VEHICLE_AUDIO) {
        if (valid_ptr(impact_level0)) {
            VtableAlias impact_header @ ptr(impact_level0);
        }
        if (valid_ptr(big_air_level0)) {
            VtableAlias big_air_header @ ptr(big_air_level0);
        }
        if (valid_ptr(wind_level0)) {
            VtableAlias wind_header @ ptr(wind_level0);
        }
        if (valid_ptr(horn_level0)) {
            VtableAlias horn_header @ ptr(horn_level0);
        }
        if (valid_ptr(powerup_level0)) {
            VtableAlias powerup_header @ ptr(powerup_level0);
        }
        if (valid_ptr(fire_level0)) {
            VtableAlias fire_header @ ptr(fire_level0);
        }
        if (valid_ptr(wheel_level0)) {
            VtableAlias wheel0_header @ ptr(wheel_level0);
        }
        if (valid_ptr(wheel_level1)) {
            VtableAlias wheel1_header @ ptr(wheel_level1);
        }
        if (valid_ptr(wheel_level2)) {
            VtableAlias wheel2_header @ ptr(wheel_level2);
        }
        if (valid_ptr(suspension_level0)) {
            VtableAlias suspension0_header @ ptr(suspension_level0);
        }
        if (valid_ptr(suspension_level1)) {
            VtableAlias suspension1_header @ ptr(suspension_level1);
        }
        if (valid_ptr(suspension_level2)) {
            VtableAlias suspension2_header @ ptr(suspension_level2);
        }
        if (valid_ptr(gearshift_level0)) {
            VtableAlias gearshift0_header @ ptr(gearshift_level0);
        }
        if (valid_ptr(gearshift_level1)) {
            VtableAlias gearshift1_header @ ptr(gearshift_level1);
        }
        if (valid_ptr(gearshift_level2)) {
            VtableAlias gearshift2_header @ ptr(gearshift_level2);
        }
        if (valid_ptr(backfire_level0)) {
            VtableAlias backfire0_header @ ptr(backfire_level0);
        }
        if (valid_ptr(backfire_level1)) {
            VtableAlias backfire1_header @ ptr(backfire_level1);
        }
        if (valid_ptr(backfire_level2)) {
            VtableAlias backfire2_header @ ptr(backfire_level2);
        }
        if (valid_ptr(turbo_blower_level0)) {
            AudioBlockFull turbo0 @ ptr(turbo_blower_level0);
        }
        if (valid_ptr(turbo_blower_level1)) {
            AudioBlockFull turbo1 @ ptr(turbo_blower_level1);
        }
        if (valid_ptr(turbo_blower_level2)) {
            AudioBlockFull turbo2 @ ptr(turbo_blower_level2);
        }
        if (valid_ptr(engine_level0)) {
            AudioBlockFull engine0 @ ptr(engine_level0);
        }
        if (valid_ptr(engine_level1)) {
            AudioBlockFull engine1 @ ptr(engine_level1);
        }
        if (valid_ptr(engine_level2)) {
            AudioBlockFull engine2 @ ptr(engine_level2);
        }
        if (valid_ptr(engine_level3)) {
            AudioBlockFull engine3 @ ptr(engine_level3);
        }
        if (valid_ptr(engine_level4)) {
            AudioBlockFull engine4 @ ptr(engine_level4);
        }
        if (valid_ptr(exhaust_level0)) {
            AudioBlockFull exhaust0 @ ptr(exhaust_level0);
        }
        if (valid_ptr(exhaust_level1)) {
            AudioBlockFull exhaust1 @ ptr(exhaust_level1);
        }
        if (valid_ptr(exhaust_level2)) {
            AudioBlockFull exhaust2 @ ptr(exhaust_level2);
        }
        if (valid_ptr(exhaust_level3)) {
            AudioBlockFull exhaust3 @ ptr(exhaust_level3);
        }
        if (valid_ptr(exhaust_level4)) {
            AudioBlockFull exhaust4 @ ptr(exhaust_level4);
        }
        if (valid_ptr(common_level0)) {
            AudioCommonProfile common0 @ ptr(common_level0);
        }
        if (valid_ptr(common_level1)) {
            AudioCommonProfile common1 @ ptr(common_level1);
        }
        if (valid_ptr(common_level2)) {
            AudioCommonProfile common2 @ ptr(common_level2);
        }
        if (valid_ptr(common_level3)) {
            AudioCommonProfile common3 @ ptr(common_level3);
        }
        if (valid_ptr(common_level4)) {
            AudioCommonProfile common4 @ ptr(common_level4);
        }
    }
} [[static]];

// ============================================================================
// Runtime selections and package root
// ============================================================================

struct McCarCustomSelections {
    u32 vtable [[format("vt")]];
    u8 unknown_0004[0x1180];
    s32 front_bumper;                         // +0x1184
    u32 unknown_1188;
    s32 rear_bumper;                          // +0x118C
    u32 unknown_1190;
    s32 side_skirts;                          // +0x1194
    u32 unknown_1198;
    s32 brush_top_provisional;                // +0x119C
    s32 grille_louvers;                       // +0x11A0
    u32 unknown_11A4;
    s32 wing_spoiler;                         // +0x11A8
    u8 unknown_11AC[0x3C];
    s32 custom_exhaust;                        // +0x11E8
    u8 custom_exhaust_enabled;                 // +0x11EC
    u8 pad_11ED[3];
    s32 hidden_24_slot_set;                   // +0x11F0
    u32 unknown_11F4;
    s32 hood;                                 // +0x11F8
    u32 unknown_11FC;
    s32 scoop_blower;                         // +0x1200, -1 = none
    u8 unknown_1204[0xB8];
    s32 taillights;                           // +0x12BC
};

struct VehicleBodyPrefix {
    u32 flags;
    u32 item_count;
    u32 car_model_type [[format("fmt_ptr")]];
    u32 package_bound [[format("fmt_ptr")]]; // role vs root+0x78 still open
    u32 material [[format("fmt_ptr")]];
    u32 stuck_tune [[format("fmt_ptr")]];
    u32 player_input_tune [[format("fmt_ptr")]];
    u32 driver_tune [[format("fmt_ptr")]];
    u32 damage_tune [[format("fmt_ptr")]];
    u32 model_tune [[format("fmt_ptr")]];
    u32 audio_root [[format("fmt_ptr")]];
    u32 car_custom [[format("fmt_ptr")]];   // runtime-only; file has CD padding
    u32 cam_shake_tune [[format("fmt_ptr")]];
    u32 sim_stock [[format("fmt_ptr")]];
    u32 sim_upgraded [[format("fmt_ptr")]];
    u32 gyro_stock [[format("fmt_ptr")]];
    u32 gyro_upgraded [[format("fmt_ptr")]];
    u32 tune_zone [[format("fmt_ptr")]];
    u32 mods_tune [[format("fmt_ptr")]];
    u32 zero_4C;

    if (valid_ptr(car_model_type)) {
        RmcCarModelTypePrefix model_type @ ptr(car_model_type);
    }
    if (valid_ptr(package_bound)) {
        PhBoundGeometry package_collision @ ptr(package_bound);
    }
    if (valid_ptr(material)) {
        VtableAlias physics_material_header @ ptr(material);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(stuck_tune)) {
        NamedTuneHeader stuck @ ptr(stuck_tune);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(player_input_tune)) {
        VehInputTune input @ ptr(player_input_tune);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(driver_tune)) {
        NamedTuneHeader driver @ ptr(driver_tune);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(damage_tune)) {
        VehDamageTune damage @ ptr(damage_tune);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(model_tune)) {
        VehModelTune model_parameters @ ptr(model_tune);
    }
    if (EXPAND_VEHICLE_AUDIO && valid_ptr(audio_root)) {
        VehicleAudioRoot audio @ ptr(audio_root);
    }
    if (valid_ptr(car_custom)) {
        McCarCustomSelections custom @ ptr(car_custom);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(cam_shake_tune)) {
        NamedTuneHeader camera_shake @ ptr(cam_shake_tune);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(sim_stock)) {
        VehSimTune stock_simulation @ ptr(sim_stock);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(sim_upgraded)) {
        VehSimTune upgraded_simulation @ ptr(sim_upgraded);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(gyro_stock)) {
        VehGyroTune stock_gyro @ ptr(gyro_stock);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(gyro_upgraded)) {
        VehGyroTune upgraded_gyro @ ptr(gyro_upgraded);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(tune_zone)) {
        VehZoneTune zone @ ptr(tune_zone);
    }
    if (EXPAND_PERFORMANCE && valid_ptr(mods_tune)) {
        VehModsTune upgrades @ ptr(mods_tune);
    }
};

VehicleBodyPrefix vehicle @ VP_BODY;

if (SHOW_TYPED_SHADER_INSPECTOR) {
    Shader inspected_shader @ INSPECT_ADDRESS;
}
