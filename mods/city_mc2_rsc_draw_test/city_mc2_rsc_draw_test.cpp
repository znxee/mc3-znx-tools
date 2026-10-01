// Experimental direct-RSC renderer. Submit through the same live SetCamera
// stage used by city_mc2_scene, without invoking empty-city ModelDraw.
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"
#include "../../payload/mc3_heap.h"
#include "../mc2_city_config.h"

enum {
    SET_CAMERA_HOOK = 0x00257AEC,
    SET_CAMERA = 0x002A5040,
    ADD_VCL_CALL = 0x00526A60,
    FLUSH_CACHE = 0x00546C20,
    PROJ_MATRIX_PTR = 0x0070E50C,
    STREAM_OPEN = 0x003991F0,
    STREAM_READ = 0x003993A8,
    STREAM_SIZE = 0x003997A8,
    STREAM_CLOSE = 0x00399748,
    STREAM_CREATE = 0x003992B0,       // Stream::Create(name) -> Stream* (write)
    STREAM_WRITE = 0x00399538,        // Stream::Write(stream, data, bytes)
    RSC0 = 0x30435352,
    PMD0 = 0x30444D50,
    VCLQ = 0x514C4356,
    TEX0 = 0x30584554,
    MIP0 = 0x3050494D,
    PAL0 = 0x304C4150,
    RSCM = 0x4D435352,
    RINS = 0x534E4952,
    STREAM_RAW = 1,
    MAX_PIECES = 1600,          // 1010 in LA, 1493 in Paris; State is in .mod
    MAX_MODELS = 1024,
    MAX_CARDS = 256,
    MAX_INSTANCES = 9000,
    MAX_MODEL_PMDS = 4,
    MAX_TEXTURES = 2048,
    MAX_IMAGES = 1024,
    MAX_LEVELS = 4,
    MAX_RSC_ENTRIES = 8192,
    UPLOAD_PACKET = 112,         // NOP NOP FLUSH DIRECT + 6 GIF qwords
    MAX_SEGMENTS = 96,          // 78 texture calls in the busiest LA PMD
    NO_IMAGE = 0xFFFFu,
    LOADS_PER_FRAME = 4,
    MODEL_MATRIX_OFFSET = 0,    // Piece::packet is the bare 4x4 matrix
    DIRECTORY_MAX = 0x20000,
    PACKET_MAX = 0x10000,
    // MC3's own per-frame texture ring (gfxTexture). rmcTexturePS2 allocates
    // upward from CURRENT; when an upload does not fit below TOP it restarts at
    // BASE and bumps GENERATION, which is what makes every texture whose +0x7C
    // stamp is older re-upload. FlushTexMem (0x52C7D8) does the same once per
    // frame. Render targets live above TOP (0x3580..0x4000); the render target
    // itself is 0x800..0x1600 and Z16 0x1600..0x1D00.
    TEX_VRAM_TOP = 0x0070FBE8,
    TEX_VRAM_CURRENT = 0x0070FBEC,
    TEX_VRAM_BASE = 0x0070FBF0,
    TEX_VRAM_GENERATION = 0x700005C0,
    // Per upload: one 112-byte header per mip level and for the CLUT, plus a
    // 112-byte TEX0/TEX1/MIPTBP switch; the texels are REFs into the file.
    ARENA_BYTES = 0x50000,
    UNCACHED = 0x20000000u,     // EE uncached alias of main RAM
    VIF_FLUSH = 0x11000000u,
    VIF_DIRECT = 0x50000000u,
    // What lowPsxGfx::AddVCLCall (0x526A60) touches besides the REF it writes.
    DMA_PACKET_CUR = 0x700005AC,
    DMA_PACKET_END = 0x700005B0,
    DMA_PACKET_FLUSH = 0x00526900,   // (int qwords) -> where to write them
    VCL_LAST_PACKET = 0x006F4798,    // write-only bookkeeping
    VU_STATE_VALID = 0x0070E5E8,     // cleared: our stream changes VU state
    DMA_REF = 0x30000000u,
    // MC2 CPVS: the original <time>_<weather>_cpvs.rsc read as is.
    CPP0 = 0x30505043,
    PCP0 = 0x30504350,
    RCPM = 0x4D504352,
    CPVS_COVERED = 0x80000000u,
    CPVS_SLICE = 0x40000,       // bytes read from the cpvs file per frame
    CPVS_MAX = 0x400000,
    MAX_CPVS_ENTRIES = 16384,
    HEAP_MARGIN = 0x50000,      // left for the game after our big blocks
    RNT0 = 0x30544E52,
    CMI0 = 0x30494D43,
    MODEL_HASH = 2048,
    INST_HASH = 16384,
    MAX_HOODS = 32,
    MAX_COMP_PCP = 512,
    HOOD_CHUNK = 4096,
    HOOD_CHUNKS_PER_FRAME = 16,
    STREAM_SEEK = 0x003996C0,
    MAX_PROP_TYPES = 128,
    MAX_PROPS = 8192,           // 5695 in LA, 8054 in Paris
    MAX_DYN_PROPS = 48,
    MAX_PROP_PACKETS = 8,
    MAX_AMB_ENTRIES = 2048,
    PRP0 = 0x30505250,
    MOD0 = 0x30444F4D,
    MATRIX_BYTES = 64,
    VIF_UNPACK_MATRIX = 0x6C04000Cu,  // UNPACK V4-32 x4 -> VU 12
    VIF_UNPACK_VIEWPROJ = 0x6C040004u // UNPACK V4-32 x4 -> VU 4 (view-projection)
};
enum { MAX_TOUR_POINTS = 256, MAX_PINS = 128 };
enum { REF_RESOLVED = 0x01000000u };   // queue_pcp: this CPVS REF's address is final
enum { FX_RULE_COUNT = 17, MAX_SPOUTS = 8, MAX_FX_AMBIENT = 128, MAX_SKY_LAYERS = 8 };

struct Target {
    mc3_u32 pmd;
    float cx, cy, cz, radius, ox, oy, oz, lateral, forward;
    mc3_u32 model_id, flags;
};
// A piece keeps its PMD0 block exactly as stored in the RSC - DMA tags and
// payloads - with the low half of every tag zeroed, so the whole block is a
// valid VIF stream (NOP, NOP, the tag's two VIF codes, payload) AND a CPVS
// `ref` into it is simply block + offset. The block is cut after each texture
// CALL into segments sent as REFs; `segs` points at seg_count triples
// {offset, qwc, image selected after this segment or NO_IMAGE}.
struct Piece {
    mc3_u32 ready, block, block_bytes, error, calls, anchor_reported;
    mc3_u32 seg_count, segs;
    mc3_u32 packet;     // static pieces: matrix VCLQ (identity / preview offset)
};
struct Level {
    mc3_u32 addr;
    mc3_u16 qwc, rw, rh, blocks;
    mc3_u8 dbw, dpsm, tbw, pad;
};
struct Slot {                    // one MC2 image (TEX0s sharing MIP0+PAL0)
    mc3_u32 mip, pal;
    mc3_u8 bpp, tw, th, levels;
    mc3_u16 blocks, clut_blocks;
    mc3_u32 tcc;                // 0: CLUT alpha all zero -> colour only
    Level level[MAX_LEVELS];
};
struct Hood {                    // line reader for .lvl / .hood text
    mc3_u32 stream, size, pos, fill, buf_mem;
    mc3_u8 *buf;
    mc3_u32 in_inst, rows, model, ext, have;
    float emin[3], emax[3], m[4][3];
};
struct PropType {
    char name[48];
    mc3_u32 mod, packets, ok;
    float cx, cy, cz, radius;
    mc3_u32 pk_addr[MAX_PROP_PACKETS];
    mc3_u16 pk_qwc[MAX_PROP_PACKETS], pk_slot[MAX_PROP_PACKETS];
    // LOD 2 (MC2's .pdef: lods 0 and 2) and the template's Far flag
    mc3_u32 far, packets2, pk2_addr[MAX_PROP_PACKETS];
    mc3_u16 pk2_qwc[MAX_PROP_PACKETS], pk2_slot[MAX_PROP_PACKETS];
    float mass;          // MC2 tune/phys/<name>.phys `mass:` (50 when absent)
    mc3_u32 fixed;       // heavy (>= 1000 kg): a car does not move it
    mc3_u32 has_phys;    // a .phys exists (smoke/steam/particle props have none)
    // losangeles.prop template flags: FixedObject 1 = never moves (poles,
    // palms, containers), GfxOnly 1 = no collision at all (tunnel lights)
    mc3_u32 fixed_object, gfx_only, drivable;
    // a tree or palm: its cull sphere is the canopy (up to 11 m), the car
    // hits the trunk
    mc3_u32 trunk;
    // MC3 particle rules standing in for the template's MC2 particle parts
    // (prop_part, FX_* indices), and whether one of them is always on
    mc3_u8 fx[4], fx_count, fx_always;
    // the PRP0's compiled lights (MC2 light_data): count and first record
    mc3_u32 light_n, light_rec, light_data;
};
// One layer of MC2's sky (sky_N MOD0 of <time>_<weather>.rsc)
struct SkyLayer {
    mc3_u32 packets, addr[MAX_PROP_PACKETS];
    mc3_u16 qwc[MAX_PROP_PACKETS], slot[MAX_PROP_PACKETS];
};
// A hydrant that keeps spraying after the hit (m_sprayAfterBroken, for
// m_spewTimeLimit seconds) where it stood.
struct FxSpout {
    mc3_u32 rule;
    float left, m[16];
};
// A prop the car has hit: it tips over its base, flies, lands and slides.
struct DynProp {
    mc3_u32 index, state;              // state 1 moving, 0 free slot
    float pos[3], vel[3], axis[3], angle, omega, base_y, rot0[9];
};
struct ModelDraw {
    mc3_u32 flags, count, pieces[MAX_MODEL_PMDS];
};
// A mark the player put in the world (pins_update): category 1 texture,
// 2 collision, 3 other; wall = on the wall ahead instead of the ground.
struct Pin {
    mc3_u32 category, wall;
    float pos[3], forward[3], car[3];
};
struct State {
    Target targets[MAX_PIECES];
    Piece pieces[MAX_PIECES];
    mc3_u32 target_count, manifest_ready, manifest_error, manifest_reported;
    // runtime catalog (build_catalog, step_hoods, build_cpvs_names)
    mc3_u32 rnt, rnt_mem, model_name[MAX_MODELS];
    mc3_u16 model_hash[MODEL_HASH];
    Hood hood;
    mc3_u32 hood_state, hood_index, hood_count, hood_bad;
    char hood_names[MAX_HOODS][32];
    mc3_u16 inst_ext[MAX_INSTANCES], inst_hash[INST_HASH];
    mc3_u32 instance_word[MAX_INSTANCES], target_word[MAX_PIECES];
    mc3_u16 *inst_refl;                     // [MAX_INSTANCES] `_0_refl` PCP0 index + 1, 0 = none
    mc3_u32 draw_pending, no_defer, defer_marked;   // city drawn after the reflection paste
    mc3_u32 *refl_chain;                           // [MAX_CPVS_ENTRIES / 32] bit: a `_0_refl` PCP0
    mc3_u32 palette_refl;                           // CPP0 again, alpha = the road's reflectivity
    mc3_u32 palette_wet;                            // CPP0 again, alpha = wet_scale() (texture wetness)
    mc3_u32 force_tcc;                              // texture switches take TCC=1 (wetness pass)
    mc3_u32 card_model[MAX_MODELS / 32];            // bit: a `*card*` model (MC2's fake reflections)
    mc3_u16 cards[MAX_CARDS];                       // this frame's visible card instances
    mc3_u32 card_count;
    mc3_u16 *later;                                 // [MAX_PIECES + MAX_INSTANCES] this frame's ground draws
    // (these three are heap blocks taken with the city - refl_tables() - so the
    // .mod, which is loaded into the heap in every city, stays small)
    mc3_u32 later_count;
    mc3_u16 comp_pcp[MAX_COMP_PCP];
    mc3_u32 comp_pcp_count, names_ready, map_ready;
    // MC2 props (step_props)
    PropType prop_types[MAX_PROP_TYPES];
    mc3_u32 prop_type_count, prop_types_ok, prop_count, prop_bad;
    mc3_u32 prop_state, prop_error, prop_reported, props_ready;
    mc3_u32 prop_records, prop_matrices, frame_props;
    DynProp dyn[MAX_DYN_PROPS];
    mc3_u32 dyn_next, props_hit, solid_hits, push_brain[4], push_opp[10];
    // [boot] mc2s spectator camera (spectate)
    mc3_u32 spec_inst[16], spec_count, spec_frame, spec_current;
    // mc2s = <N>f: the player's car follows the watched opponent (spectate_follow)
    mc3_u32 follow_brain[4], follow_opp[10], follow_count;
    // [boot] mc2t camera tour (camera_tour): points from host0:/mc2_camtour.txt
    mc3_u32 tour_state, tour_mem, tour_count, tour_frame, tour_shown;
    float tour[MAX_TOUR_POINTS][6];
    // [boot] mc2g drop test (drop_test): points from host0:/mc2_droptest.txt
    mc3_u32 drop_state, drop_mem, drop_points, drop_count, drop_frame, drop_brain[4], drop_opp[10];
    // player pins (pins_update): "MC2PINS1", count, then the pins - found in
    // a savestate by the magic (mc3_pins.py)
    char pin_magic[8];
    mc3_u32 pin_count, pin_keys, pin_mem, pin_mats, pin_saved;
    Pin pins[MAX_PINS];
    mc3_u8 prop_moved[MAX_PROPS];
    mc3_u32 amb_stream, amb_count, amb_bytes, amb_table, amb_dest, slot_of_amb;
    mc3_u32 amb_rnt, amb_rnt_bytes, amb_loaded;
    ModelDraw models[MAX_MODELS];
    mc3_u32 model_count, instance_count, instances_ready, instances_error;
    mc3_u32 instances_reported, instances_error_detail;
    mc3_u32 instances_bank_mem, instances_bank;
    mc3_u32 instances_bank_bytes, instance_matrices_mem, instance_matrices;
    mc3_u32 queued;
    mc3_u32 camera, bootstrap, bootstrap_bytes, matrix_offset, bootstrap_error;
    mc3_u32 bootstrap_reported;
    mc3_u32 texture_error, texture_reported, textures_ready;
    Slot slots[MAX_IMAGES];
    mc3_u32 slot_count, white_slot, slot_of_local, slot_of_shared;
    mc3_u32 tex_type[MAX_RSC_ENTRIES];
    mc3_u32 tex_dest, tex_bytes, tex_next;
    mc3_u32 shared_state, shared_stream, shared, shared_bytes, shared_done;
    mc3_u32 shared_count;
    // Per image, where this frame's upload put it: MC3 generation at upload
    // time (0 = never) and the switch packet that selects it.
    mc3_u32 image_generation[MAX_IMAGES], image_frame[MAX_IMAGES];
    mc3_u32 image_switch[MAX_IMAGES];
    mc3_u32 frame_mem, arena[2], arena_used, call_count, frame_ready;
    mc3_u32 frame_uploads, frame_upload_bytes, frame_wraps, frame_dropped;
    mc3_u32 frame_pcp, frame_pmd, frame_pcp_failed, bound_switch, pcp_limit;
    mc3_u32 frame_skipped, all_white;
    float draw_radius;
    mc3_u32 cpvs_state, cpvs_stream, cpvs_mem, cpvs, cpvs_bytes;
    mc3_u32 cpvs_count, cpvs_error, cpvs_reported, palette;
    mc3_u32 cpvs_table, cpvs_table_mem, cpvs_pos, cpvs_index, cpvs_kept, cpvs_fill;
    mc3_u32 cpvs_palette_index, cpvs_fourcc_last;
    mc3_u32 cpvs_need[MAX_CPVS_ENTRIES / 32];
    mc3_u32 map_error, map_reported, heap_reported, heap_min;
    mc3_u32 rsc_state, rsc_stream, rsc_size, rsc_count, rsc_cursor;
    mc3_u32 rsc_table_mem, rsc_table, rsc_scratch_mem, rsc_scratch;
    mc3_u32 rsc_error, rsc_reported, load_index;
    // MC3 prop particles on the MC2 props (step_prop_fx)
    mc3_u32 fx_state, fx_mem, fx_sys, fx_tex, fx_rules[FX_RULE_COUNT];
    mc3_u32 fx_last_solid, fx_last_frame, fx_frame, fx_ambient_count, fx_blasts;
    FxSpout spouts[MAX_SPOUTS];
    mc3_u16 fx_ambient[MAX_FX_AMBIENT];
    // MC2's sky (step_sky / queue_sky)
    float sky_matrix[16] __attribute__((aligned(16)));
    float card_vp[16] __attribute__((aligned(16)));      // view-projection of the card pass
    mc3_u32 sky_state, sky_stream, sky, sky_bytes, sky_done, sky_count, sky_layers;
    SkyLayer sky_layer[MAX_SKY_LAYERS];
    // MC2 prop lights as MC3 mcLights (step_lights)
    mc3_u32 light_state, light_mgr, light_count, light_objs, light_props, lights_drawn;
    mc3_u32 cyc_sum, cyc_max, cyc_frames;                // set_camera_hook cost (RCYC)
    mc3_u32 cyc_phys, cyc_fx, cyc_lights, cyc_draw;      // its parts (RCY2, in K cycles)
    mc3_u32 prof[5];
    float fr[24] __attribute__((aligned(16)));           // frustum_update: eye, radius, 5 planes
    mc3_u32 fr_ok;                                     // draw_city: setup/sky, components, instances, props, reflect (RCY3/4)
};
// State lives on the heap, taken when a race in an MC2 city starts
// (state_acquire) and given back with everything else (release_all). As a
// static it was ~460 KB of .bss INSIDE the .mod, and core.mod loads a deferred
// module into the heap on the first frame of EVERY city, reading the file and
// placing the code as two blocks at once: booted straight into San Diego or
// Tokyo that asked for 2 x 540 KB on a full heap and stopped the game on
// "Heap (null) overrun". Outside an MC2 city st() is 0.
static State *g_state_ptr;
static mc3_u32 g_state_mem;
static __attribute__((noinline)) State *st() { return g_state_ptr; }
static mc3_u32 cycles_now() { mc3_u32 c; __asm__ volatile("mfc0 %0, $9" : "=r"(c)); return c; }
static __attribute__((noinline)) State **state_slot() { return &g_state_ptr; }
static __attribute__((noinline)) mc3_u32 *state_mem_slot() { return &g_state_mem; }

// Every block this module takes, so that leaving Los Angeles gives all of it back
// (release_all): the renderer holds ~11 MB, and the next city - the front
// end's San Diego included - needs that heap.
enum { MAX_TRACKED = 2560, RETRY_FRAMES = 120,
       RACE_CONFIG_CURRENT = 0x00619B10, GAME_STATE = 0x006199F0,
       RACE_CONFIG_NEXT = 0x00619B14,
       FRONTEND_CALL = 0x001A5660,        // jal EnterStateMC3Frontend in ChangeState
       ENTER_FRONTEND = 0x001A5B08,
       FIRST_CITY_WITHOUT_MENU_CAMERA = 4, FRONTEND_CITY = 0 };
struct Tracked { mc3_u32 n, ptr[MAX_TRACKED], dropped, retry, active; };
static Tracked g_tracked;
static __attribute__((noinline)) Tracked *trk() { return &g_tracked; }
static void *r_alloc(mc3_u32 bytes) {
    Tracked *t = trk();
    if (t->n >= MAX_TRACKED) { ++t->dropped; return 0; }   // refuse rather than leak
    void *p = mc3_alloc(bytes);
    if (p) t->ptr[t->n++] = (mc3_u32)p;
    return p;
}
static void r_free(void *p) {
    if (!p) return;
    Tracked *t = trk();
    for (mc3_u32 i = 0; i < t->n; ++i)
        if (t->ptr[i] == (mc3_u32)p) { t->ptr[i] = t->ptr[--t->n]; break; }
    mc3_free(p);
}

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) {
        mc3_u32 d = (v >> i) & 15u;
        put((char)(d < 10u ? '0' + d : 'A' + d - 10u));
    }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}
static mc3_u32 le32(const mc3_u8 *p) {
    return (mc3_u32)p[0] | ((mc3_u32)p[1] << 8) |
           ((mc3_u32)p[2] << 16) | ((mc3_u32)p[3] << 24);
}
static int read_exact(mc3_u32 h, void *p, mc3_u32 n) {
    return MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)
        (h, (mc3_u32)p, n) == n;
}
static void fail(Piece *p, mc3_u32 code) { p->error = code; }
static mc3_u32 word(mc3_u32 p) { return *(volatile mc3_u32 *)p; }
static float fword(const mc3_u32 *p) {
    union { mc3_u32 u; float f; } v; v.u = *p; return v.f;
}
static mc3_u32 fbits(float f) {
    union { mc3_u32 u; float f; } v; v.f = f; return v.u;
}
static int ptr(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u; }
static mc3_u32 active_city() {
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG_CURRENT;
    return ptr(cfg) ? *(volatile mc3_u32 *)cfg : 0xFFFFFFFFu;
}
static __attribute__((noinline)) const char *empty_suffix() {
    static const char s[] = "";
    return s;
}
// The game's allocator does not return NULL when it runs out: it prints
// "Heap (null) overrun" and stops the machine. Ask before every large block,
// and keep a margin for whatever the game allocates after us.
// The largest free block, not the cursor-top gap alone: entering a city from
// the front end leaves a big freed block below a high cursor.
static int heap_room(mc3_u32 bytes) {
    return mc3_heap_largest(mc3_heap_active()) >= bytes + HEAP_MARGIN;
}

static State *state_acquire() {
    State **slot = state_slot();
    if (*slot) return *slot;
    const mc3_u32 bytes = (mc3_u32)sizeof(State) + 32u;
    if (!heap_room(bytes)) return 0;
    void *mem = mc3_alloc(bytes);
    if (!mem) return 0;
    const mc3_u32 base = ((mc3_u32)mem + 15u) & ~15u;
    volatile mc3_u32 *w = (volatile mc3_u32 *)base;
    for (mc3_u32 i = 0; i < sizeof(State) / 4u; ++i) w[i] = 0u;
    *state_mem_slot() = (mc3_u32)mem;
    *slot = (State *)base;
    return *slot;
}

static int world_mode() {
    const char *value = mc3_bootarg(MC3_ID('r','s','c','w'));
    return value && value[0] == '1';
}
// [boot] mc2p: which draws use MC2's CPVS chains. Bit 0 = world-baked
// components, bit 1 = placed instances; default 3. For measuring.
static mc3_u32 cpvs_mask() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','p'));
    return value && value[0] >= '0' && value[0] <= '3' ? (mc3_u32)(value[0] - '0') : 3u;
}
// [boot] mc2n: at most this many CPVS chains per frame (diagnostic).
static mc3_u32 cpvs_limit() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','n'));
    mc3_u32 n = 0; int any = 0;
    while (v && *v >= '0' && *v <= '9') { n = n * 10u + (mc3_u32)(*v++ - '0'); any = 1; }
    return any ? n : 0xFFFFFFFFu;
}
// [boot] mc2r: sphere-cull distance in world units (default 800).
static float draw_radius() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','r'));
    mc3_u32 n = 0; int any = 0;
    while (v && *v >= '0' && *v <= '9') { n = n * 10u + (mc3_u32)(*v++ - '0'); any = 1; }
    return any && n >= 50u && n <= 20000u ? (float)n : 800.0f;
}
// [boot] mc2z = 0: draw the city without writing depth, as before 2026-09-29 (A/B).
static int zwrite_enabled() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','z'));
    return !value || value[0] != '0';
}
// [boot] mc2q = 0: no MC2 props (diagnostic).
static int props_enabled() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','q'));
    return !value || value[0] != '0';
}
static int instances_enabled() {
    const char *value = mc3_bootarg(MC3_ID('r','i','n','s'));
    return !value || value[0] != '0';
}
static const mc3_u32 *instance_record(const State *s, mc3_u32 i) {
    return (const mc3_u32 *)(s->instances_bank + i * 20u);   // model, centre, radius
}

static mc3_u32 instance_matrix(const State *s, mc3_u32 i) {
    return s->instance_matrices + i * 64u;
}

// ---------------------------------------------------------------------------
// VU bootstrap built at run time from the game's own memory. The microcode is
// MC3's rv1_code_main and the body of rv1_code, exactly as the game keeps
// them for its own DMA uploads (checked byte for byte against the ELF); the
// rest are the constants the former mc2_rsc_vu_bootstrap.vcl carried:
//   - VU1 data 0..63: viewport q[8]/q[9] (native MC3 depth), identity model
//     matrix q[12..15], GIF templates q[35] (strip) and q[36]/q[37] (fan, for
//     the clipper), all with PRIM.ABE; q[42] the colour of non-CPVS models.
//     q[4..7] is the view-projection, rewritten every frame (matrix_offset).
//   - VU1 100..767 prefilled with a point inside the view (the rv1 loop reads
//     one vertex past each batch).
//   - GS: TEX1 (overridden per texture), CLAMP, TEST_1 0x5100F, ALPHA_1 0x44,
//     as MC2 sets them.
// ---------------------------------------------------------------------------
enum {
    VU_MAIN_CHUNKS = 3,
    VU_TAIL_CHUNKS = 4,
    VU_TAIL_BASE = 1000,           // 0x1F40 / 8
    VU_PREFILL_FIRST = 100,
    VU_PREFILL_END = 768,
};

// {EE address, bytes} of each chunk, in upload order (mc3_vu.py --id).
// The game stores each program as 2048-byte pieces 0x808 apart (8 bytes of
// VIF code between them); only the last piece is shorter. Arithmetic, not a
// table: GCC turns an if-chain into a .rodata lookup the .mod packer rejects.
static __attribute__((noinline)) mc3_u32 vu_chunk(mc3_u32 i, mc3_u32 *bytes) {
    if (i < VU_MAIN_CHUNKS) {                          // rv1_code_main
        *bytes = i + 1u == VU_MAIN_CHUNKS ? 1424u : 2048u;
        return 0x00610A68u + i * 0x808u;
    }
    i -= VU_MAIN_CHUNKS;                               // rv1_code body, VU 0x1F40..
    *bytes = i + 1u == VU_TAIL_CHUNKS ? 1496u : 2048u;
    return 0x00612120u + i * 0x808u;
}

struct Emit { mc3_u32 *w, n; };
static void put_w(Emit *e, mc3_u32 v) { if (e->w) e->w[e->n] = v; ++e->n; }
static void align4(Emit *e) { while (e->n & 3u) put_w(e, 0u); }

// Writes the VIF stream into `out` (or only counts words when out is 0).
// Returns the stream length in words; *data_at gets the word index where the
// VU1 data block begins.
static mc3_u32 emit_bootstrap(mc3_u32 *out, mc3_u32 *data_at) {
    Emit e = { out, 0u };
    put_w(&e, 0u); put_w(&e, 0x10000000u);            // NOP, FLUSHE
    put_w(&e, 0x01000101u); put_w(&e, 0x05000000u);   // STCYCL 1,1; STMOD 0
    for (int part = 0; part < 2; ++part) {
        // the microcode as one byte run per program, uploaded 255 instr at a time
        mc3_u32 vu = part ? VU_TAIL_BASE : 0u;
        mc3_u32 first = part ? VU_MAIN_CHUNKS : 0u;
        mc3_u32 last = part ? VU_MAIN_CHUNKS + VU_TAIL_CHUNKS : VU_MAIN_CHUNKS;
        mc3_u32 c = first, coff = 0, left_in_chunk;
        mc3_u32 csize; mc3_u32 caddr = vu_chunk(c, &csize);
        mc3_u32 total = 0;
        for (mc3_u32 k = first; k < last; ++k) { mc3_u32 b; vu_chunk(k, &b); total += b; }
        mc3_u32 done = 0;                              // instructions
        while (done * 8u < total) {
            mc3_u32 nins = total / 8u - done;
            if (nins > 255u) nins = 255u;
            put_w(&e, 0x4A000000u | (nins << 16) | (vu + done));
            for (mc3_u32 i = 0; i < nins * 2u; ++i) {
                left_in_chunk = csize - coff;
                if (!left_in_chunk) { ++c; caddr = vu_chunk(c, &csize); coff = 0; }
                put_w(&e, e.w ? *(volatile mc3_u32 *)(caddr + coff) : 0u);
                coff += 4u;
            }
            align4(&e);
            done += nins;
        }
    }
    // VU1 data block, 64 qwords at 0
    put_w(&e, 0x6C400000u);
    *data_at = e.n;
    for (mc3_u32 q = 0; q < 64u; ++q) {
        mc3_u32 v[4] = { 0u, 0u, 0u, 0u };
        if (q == 8u) { v[0] = 0x43800000u; v[1] = 0xC3600000u; v[2] = 0xC4FFFF00u; }
        else if (q == 9u) { v[0] = 0x45000000u; v[1] = 0x45000000u; v[2] = 0x44FFFF00u; }
        else if (q >= 12u && q <= 15u) v[q - 12u] = 0x3F800000u;
        else if (q == 35u) { v[0] = 0x8000u; v[1] = 0x302E4004u; v[2] = 0x512u; }
        else if (q == 36u || q == 37u) { v[0] = 0x8000u; v[1] = 0x302EC004u; v[2] = 0x512u; }
        else if (q == 42u) { v[0] = v[1] = v[2] = 0x3F4CCCCDu; v[3] = 0x3F800000u; }
        for (int k = 0; k < 4; ++k) put_w(&e, v[k]);
    }
    align4(&e);
    for (mc3_u32 at = VU_PREFILL_FIRST; at < VU_PREFILL_END;) {
        mc3_u32 n = VU_PREFILL_END - at;
        if (n > 255u) n = 255u;
        put_w(&e, 0x6C000000u | (n << 16) | at);
        for (mc3_u32 i = 0; i < n; ++i) {
            put_w(&e, 0x4800AC88u); put_w(&e, 0x48008573u);
            put_w(&e, 0x48009E1Bu); put_w(&e, 0u);
        }
        align4(&e);
        at += n;
    }
    // GS state: FLUSH, DIRECT 5
    put_w(&e, 0x11000000u); put_w(&e, 0x50000005u);
    put_w(&e, 0x8004u); put_w(&e, 0x10000000u); put_w(&e, 0xEu); put_w(&e, 0u);
    put_w(&e, 0x61u); put_w(&e, 0u); put_w(&e, 0x14u); put_w(&e, 0u);     // TEX1_1
    put_w(&e, 0u); put_w(&e, 0u); put_w(&e, 0x08u); put_w(&e, 0u);        // CLAMP_1
    put_w(&e, 0x5100Fu); put_w(&e, 0u); put_w(&e, 0x47u); put_w(&e, 0u);  // TEST_1
    put_w(&e, 0x44u); put_w(&e, 0u); put_w(&e, 0x42u); put_w(&e, 0u);     // ALPHA_1
    return e.n;
}

static int build_bootstrap() {
    State *s = st();
    mc3_u32 data_at;
    const mc3_u32 words = emit_bootstrap(0, &data_at);   // 2 words of tag VIF first
    const mc3_u32 body = (words + 2u) * 4u;
    if (body & 15u) { s->bootstrap_error = 2u; return 0; }
    mc3_u8 *mem = heap_room(body + 32u) ? (mc3_u8 *)r_alloc(body + 32u) : 0;
    if (!mem) { s->bootstrap_error = 3u; return 0; }
    const mc3_u32 base = ((mc3_u32)mem + 15u) & ~15u;
    mc3_u32 *w = (mc3_u32 *)base;
    w[0] = body / 16u; w[1] = VCLQ; w[3] = body;
    w[4] = 0u; w[5] = 0u;                              // the tag's VIF codes
    emit_bootstrap(w + 6, &data_at);
    w[2] = 16u + 8u + data_at * 4u + 4u * 16u;         // q[4], the matrix
    s->bootstrap = base;
    s->bootstrap_bytes = body + 16u;
    s->matrix_offset = w[2];
    mc3_u32 sum = 0;
    for (mc3_u32 i = 0; i < body / 4u; ++i) sum = sum * 31u + w[4 + i];
    mark('R','B','O','T', body, sum);
    return 1;
}

// Per-frame memory: two arenas, because the VIF reads last frame's REFs
// from the MFIFO while this one is being built.
static int alloc_frame_mem() {
    State *s = st();
    const mc3_u32 bytes = 2u * ARENA_BYTES + 16u;
    mc3_u8 *mem = heap_room(bytes) ? (mc3_u8 *)r_alloc(bytes) : 0;
    if (!mem) { s->texture_error = 5u; return 0; }
    const mc3_u32 frame = ((mc3_u32)mem + 15u) & ~15u;
    s->frame_mem = (mc3_u32)mem;
    s->arena[0] = frame;
    s->arena[1] = frame + ARENA_BYTES;
    for (mc3_u32 i = 0; i < MAX_IMAGES; ++i) s->image_generation[i] = 0u;
    s->frame_ready = 1u;
    return 1;
}

static float card_distance();
static float ffrom(mc3_u32 bits);
static inline float fsqrt(float x);
static int update_view_projection() {
    State *s = st();
    const mc3_u32 cam = s->camera, viewport = word(PROJ_MATRIX_PTR);
    if (!ptr(cam) || (cam & 15u) || !ptr(viewport) || (viewport & 3u) ||
        !s->bootstrap || s->matrix_offset + 64u > s->bootstrap_bytes)
        return 0;
    float axis[3][3], pos[3];
    for (int j = 0; j < 3; ++j) {
        for (int i = 0; i < 3; ++i) axis[j][i] = fword((const mc3_u32 *)(cam + j * 12 + i * 4));
        pos[j] = fword((const mc3_u32 *)(cam + 36 + j * 4));
    }
    float view[16] = {
        axis[0][0],axis[1][0],axis[2][0],0,
        axis[0][1],axis[1][1],axis[2][1],0,
        axis[0][2],axis[1][2],axis[2][2],0,
        -(pos[0]*axis[0][0]+pos[1]*axis[0][1]+pos[2]*axis[0][2]),
        -(pos[0]*axis[1][0]+pos[1]*axis[1][1]+pos[2]*axis[1][2]),
        -(pos[0]*axis[2][0]+pos[1]*axis[2][1]+pos[2]*axis[2][2]),1
    };
    float proj[16], vp[16];
    for (int i = 0; i < 16; ++i) proj[i] = fword((const mc3_u32 *)(viewport + 0x10 + i * 4));
    if (!(proj[0] > 0.2f && proj[0] < 10.0f && proj[5] > 0.2f && proj[5] < 10.0f &&
          proj[11] < -0.5f && proj[11] > -1.5f)) return 0;
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
        float sum = 0;
        for (int k = 0; k < 4; ++k) sum += view[r*4+k] * proj[k*4+c];
        vp[r*4+c] = sum;
    }
    mc3_u32 *dst = (mc3_u32 *)(s->bootstrap + s->matrix_offset);
    for (int i = 0; i < 16; ++i) dst[i] = fbits(vp[i]);
    // The card pass: same matrix, its depth row replaced so that the VU's
    // clipper (rv1: CLIP of the clip-space position against w, triangles
    // whose three vertices share a flag are dropped) loses every card vertex
    // farther than card_distance() D. With d = -z_view = w:
    // z' = (1 + e/D) d - e stays inside [-w, w] for e/2 < d < D and leaves it
    // at D - so each card quad beyond D goes, one card at a time, where the
    // CPU only sees whole blocks of 130-270 m (7-10 batches of 40-115 m
    // each). Checked both ways: the inverse row (z' = (1 - e/D) d + e) kept
    // only what lay beyond D. The cards draw with depth test ALWAYS, so the
    // altered z feeds nothing else. Note what the cards are: windows
    // mirrored deep under the road, so a far block paints the near road at
    // grazing angles and a near one shows little - reflections are seen from
    // a distance and fade as you reach the shop, in MC2 too.
    {
        const float D = card_distance(), e = ffrom(0x3F000000u);   // 0.5 m
        float pc[16];
        for (int i = 0; i < 16; ++i) pc[i] = proj[i];
        pc[2] = ffrom(0u); pc[6] = ffrom(0u);
        pc[10] = -(ffrom(0x3F800000u) + e / D);
        pc[14] = -e;
        for (int r = 0; r < 4; ++r) for (int c = 0; c < 4; ++c) {
            float sum = 0;
            for (int k = 0; k < 4; ++k) sum += view[r*4+k] * pc[k*4+c];
            s->card_vp[r*4+c] = sum;
        }
    }
    return 1;
}

static void update_model_matrix(Piece *piece, const Target *target, int native) {
    State *s = st();
    if (target->flags == 1u) return;  // instance matrix is queued separately
    if (!piece->packet || !ptr(s->camera)) return;
    const float px = fword((const mc3_u32 *)(s->camera + 36u));
    const float py = fword((const mc3_u32 *)(s->camera + 40u));
    const float pz = fword((const mc3_u32 *)(s->camera + 44u));
    const float bx = fword((const mc3_u32 *)(s->camera + 24u));
    const float by = fword((const mc3_u32 *)(s->camera + 28u));
    const float bz = fword((const mc3_u32 *)(s->camera + 32u));
    const float rx = fword((const mc3_u32 *)(s->camera + 0u));
    const float ry = fword((const mc3_u32 *)(s->camera + 4u));
    const float rz = fword((const mc3_u32 *)(s->camera + 8u));
    // Source models are world-baked. A shared preview origin keeps adjacent
    // models in their original relative positions instead of stacking them.
    const float tx = native ? 0.0f :
        px - bx * target->forward + rx * target->lateral - target->ox;
    const float ty = native ? 0.0f :
        py - by * target->forward + ry * target->lateral - target->oy;
    const float tz = native ? 0.0f :
        pz - bz * target->forward + rz * target->lateral - target->oz;
    mc3_u32 *dst = (mc3_u32 *)(piece->packet + MODEL_MATRIX_OFFSET);
    for (int i = 0; i < 16; ++i) dst[i] = 0;
    dst[0] = fbits(1.0f); dst[5] = fbits(1.0f); dst[10] = fbits(1.0f);
    dst[12] = fbits(tx); dst[13] = fbits(ty); dst[14] = fbits(tz);
    if (!piece->anchor_reported) {
        piece->anchor_reported = 1;
        mark('R','A','N','C', target->pmd, native ? 1u :
             fbits(px - bx * target->forward + rx * target->lateral));
    }
}
static int outside_plane(float a, float b, float c, float d,
                         float x, float y, float z, float radius) {
    const float distance = a * x + b * y + c * z + d;
    if (distance >= 0.0f) return 0;
    const float normal2 = a * a + b * b + c * c;
    return distance * distance > radius * radius * normal2;
}

// The frustum of this frame, built once (frustum_update, right after
// update_view_projection): camera position, draw radius and the five side
// planes, normalised. Until 2026-09-30 sphere_visible rebuilt the planes from
// the bootstrap's matrix at every call - for 8500 instances and 5700 props a
// frame that was ~2 M of the ~2.7 M EE cycles the city draw took on
// Hollywood's avenues, enough to push frames past 30 fps (player's flicker).
// The VU uses row vectors: clip.x = x*m[0] + y*m[4] + z*m[8] + m[12], so the
// planes come from matrix columns, not contiguous rows (using rows rejected
// visible models as the camera rotated). Z is left to the GS.
static void frustum_update() {
    State *s = st();
    s->fr_ok = 0u;
    const mc3_u32 camera = s->camera;
    if (!ptr(camera) || !s->bootstrap || s->matrix_offset + 64u > s->bootstrap_bytes) return;
    float *f = s->fr;
    f[0] = fword((const mc3_u32 *)(camera + 36u));
    f[1] = fword((const mc3_u32 *)(camera + 40u));
    f[2] = fword((const mc3_u32 *)(camera + 44u));
    f[3] = draw_radius();
    const mc3_u32 *vp = (const mc3_u32 *)(s->bootstrap + s->matrix_offset);
    const float wx = fword(vp + 3), wy = fword(vp + 7), wz = fword(vp + 11), w0 = fword(vp + 15);
    const float xx = fword(vp + 0), xy = fword(vp + 4), xz = fword(vp + 8), x0 = fword(vp + 12);
    const float yx = fword(vp + 1), yy = fword(vp + 5), yz = fword(vp + 9), y0 = fword(vp + 13);
    const float pl[5][4] = {
        { wx, wy, wz, w0 },
        { wx + xx, wy + xy, wz + xz, w0 + x0 }, { wx - xx, wy - xy, wz - xz, w0 - x0 },
        { wx + yx, wy + yy, wz + yz, w0 + y0 }, { wx - yx, wy - yy, wz - yz, w0 - y0 },
    };
    for (int k = 0; k < 5; ++k) {
        float *q = f + 4 + k * 4;
        const float n2 = pl[k][0] * pl[k][0] + pl[k][1] * pl[k][1] + pl[k][2] * pl[k][2];
        if (n2 > ffrom(0x2EDBE6FFu)) {                          // 1e-10
            const float inv = ffrom(0x3F800000u) / fsqrt(n2);
            q[0] = pl[k][0] * inv; q[1] = pl[k][1] * inv; q[2] = pl[k][2] * inv; q[3] = pl[k][3] * inv;
        } else {                                                // degenerate: never culls
            q[0] = q[1] = q[2] = ffrom(0u); q[3] = ffrom(0x3F800000u);
        }
    }
    s->fr_ok = 1u;
}
static inline int fr_visible(const float *f, float x, float y, float z, float radius) {
    const float dx = x - f[0], dy = y - f[1], dz = z - f[2];
    const float limit = f[3] + radius;
    if (dx * dx + dy * dy + dz * dz > limit * limit) return 0;
    for (int k = 0; k < 5; ++k) {
        const float *q = f + 4 + k * 4;
        if (q[0] * x + q[1] * y + q[2] * z + q[3] < -radius) return 0;
    }
    return 1;
}
static int sphere_visible(float x, float y, float z, float radius) {
    const State *s = st();
    return s->fr_ok && fr_visible(s->fr, x, y, z, radius);
}

static int piece_visible(const Piece *piece, const Target *target) {
    if (!piece->ready) return 0;
    const mc3_u32 *m = (const mc3_u32 *)(piece->packet + MODEL_MATRIX_OFFSET);
    return sphere_visible(target->cx + fword(m + 12),
                          target->cy + fword(m + 13),
                          target->cz + fword(m + 14), target->radius);
}

static int instance_visible(const mc3_u32 *record) {
    const State *s = st();
    return s->fr_ok && fr_visible(s->fr, fword(record + 1), fword(record + 2),
                          fword(record + 3), fword(record + 4));
}

static int consume_to(mc3_u32 limit, mc3_u32 budget);
static int plan_rsc_textures();

static void close_rsc_stream(State *s) {
    if (s->rsc_stream)
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->rsc_stream);
    r_free((void *)s->rsc_table_mem);
    r_free((void *)s->rsc_scratch_mem);
    s->rsc_stream = 0;
    s->rsc_table_mem = s->rsc_table = 0;
    s->rsc_scratch_mem = s->rsc_scratch = 0;
}

static int open_rsc_stream() {
    State *s = st();
    const char *const path = mc2_city_rsc(active_city());
    if (!path) { s->rsc_error = 1; s->rsc_state = 3; return 0; }
    const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
        (path, STREAM_RAW);
    if (!h) { s->rsc_error = 1; s->rsc_state = 3; return 0; }
    const int signed_size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
    mc3_u8 header[8] __attribute__((aligned(16)));
    if (signed_size < 8 || !read_exact(h, header, sizeof(header)) ||
        le32(header) != RSC0) {
        s->rsc_error = 2;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        s->rsc_state = 3;
        return 0;
    }
    const mc3_u32 count = le32(header + 4u);
    const mc3_u32 table_bytes = count * 8u;
    if (!count || count > MAX_RSC_ENTRIES || table_bytes > DIRECTORY_MAX ||
        table_bytes > (mc3_u32)signed_size - 8u) {
        s->rsc_error = 3;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        s->rsc_state = 3;
        return 0;
    }
    mc3_u8 *table_mem = (mc3_u8 *)r_alloc(table_bytes + 16u);
    mc3_u8 *scratch_mem = (mc3_u8 *)r_alloc(2048u + 16u);
    if (!table_mem || !scratch_mem) {
        s->rsc_error = 4;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        r_free(table_mem);
        r_free(scratch_mem);
        s->rsc_state = 3;
        return 0;
    }
    const mc3_u32 table = ((mc3_u32)table_mem + 15u) & ~15u;
    const mc3_u32 scratch = ((mc3_u32)scratch_mem + 15u) & ~15u;
    if (!read_exact(h, (void *)table, table_bytes)) {
        s->rsc_error = 5;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        r_free(table_mem);
        r_free(scratch_mem);
        s->rsc_state = 3;
        return 0;
    }
    s->rsc_stream = h;
    s->rsc_size = (mc3_u32)signed_size;
    s->rsc_count = count;
    s->rsc_cursor = 8u + table_bytes;
    s->rsc_table_mem = (mc3_u32)table_mem;
    s->rsc_table = table;
    s->rsc_scratch_mem = (mc3_u32)scratch_mem;
    s->rsc_scratch = scratch;
    s->rsc_state = 1;
    mark('R','S','C','S', count, s->rsc_size);
    if (!plan_rsc_textures()) {
        if (!s->texture_error) s->texture_error = 2u;
    }
    return 1;
}

static int build_packet(Piece *piece, const Target *target) {
    State *s = st();
    mc3_u8 *packet_mem = 0;
    mc3_u32 consumed = 0, ncall = 0, terminal = 0;
    mc3_u32 off = 0, end = 0;
    const mc3_u32 h = s->rsc_stream;
    if (!h || !s->rsc_table || target->pmd >= s->rsc_count) {
        fail(piece, 5); goto done;
    }
    {
        mc3_u8 *table = (mc3_u8 *)s->rsc_table;
        const mc3_u32 type = le32(table + target->pmd * 8u + 4u);
        off = le32(table + target->pmd * 8u);
        end = target->pmd + 1u < s->rsc_count
            ? le32(table + (target->pmd + 1u) * 8u) : s->rsc_size;
        if (type != PMD0 || off < s->rsc_cursor || end <= off ||
            end > s->rsc_size || end - off > 0x10000u) {
            fail(piece, 6); goto done;
        }
        // Bounded forward reading per frame; texture entries on the way are
        // kept (consume_to), everything else between the PMDs is dropped.
        const int r = s->tex_dest ? consume_to(off, 0x10000u) : -1;
        if (r < 0) { fail(piece, 7); goto done; }
        if (r == 0) return 2;
    }
    packet_mem = (mc3_u8 *)r_alloc(PACKET_MAX + 16u);
    if (!packet_mem) { fail(piece, 8); goto done; }
    {
        const mc3_u32 base = ((mc3_u32)packet_mem + 15u) & ~15u;
        const mc3_u32 size = end - off;
        if (!read_exact(h, (void *)base, size)) { fail(piece, 11); goto done; }
        consumed = size;
        mc3_u32 seg_start[MAX_SEGMENTS], seg_qwc[MAX_SEGMENTS];
        mc3_u32 seg_img[MAX_SEGMENTS];        // CALL handle selected after it
        mc3_u32 nseg = 0, start = 0, bound = 0xFFFFFFFFu, p = 0;
        while (p < size) {
            if (size - p < 16u) { fail(piece, 9); goto done; }
            mc3_u32 *tag = (mc3_u32 *)(base + p);
            const mc3_u32 lo = tag[0], id = (lo >> 28) & 7u;
            const mc3_u32 next = p + 16u + (lo & 0xFFFFu) * 16u;
            if (next > size) { fail(piece, 10); goto done; }
            const mc3_u32 handle = tag[1];
            // DMA half -> VIF NOP, NOP. The first NOP keeps the tag's QWC in
            // its IMMEDIATE field, which a NOP ignores: resolve_ref needs it to
            // tell which of up to three PMDs a CPVS ref really points into.
            tag[0] = lo & 0xFFFFu; tag[1] = 0;
            if (id == 5u) {
                // MC2's CALL into a texture chain: its payload goes first, then
                // the texture. Cut here unless it is already the bound one.
                ++ncall;
                if (handle != bound) {
                    if (nseg >= MAX_SEGMENTS) { fail(piece, 18); goto done; }
                    seg_start[nseg] = start; seg_qwc[nseg] = (next - start) / 16u;
                    seg_img[nseg++] = handle;
                    start = next; bound = handle;
                }
            } else if (id == 6u) {
                terminal = 1; p = next; break;
            } else { fail(piece, 12); goto done; }
            p = next;
        }
        if (!terminal || p != size) { fail(piece, 13); goto done; }
        if (p > start) {
            if (nseg >= MAX_SEGMENTS) { fail(piece, 18); goto done; }
            seg_start[nseg] = start; seg_qwc[nseg] = (p - start) / 16u;
            seg_img[nseg++] = 0xFFFFFFFFu;
        }
        const mc3_u32 matrix_bytes = target->flags == 0u ? MATRIX_BYTES : 0u;
        // Many-city mode depends on compaction: never retain a 64 KiB buffer
        // per model if the game's allocator cannot provide the compact copy.
        mc3_u8 *compact_mem = (mc3_u8 *)r_alloc(size + nseg * 12u + matrix_bytes + 32u);
        if (!compact_mem) { fail(piece, 17); goto done; }
        const mc3_u32 compact = ((mc3_u32)compact_mem + 15u) & ~15u;
        for (mc3_u32 j = 0; j < size / 4u; ++j)
            ((mc3_u32 *)compact)[j] = ((mc3_u32 *)base)[j];
        mc3_u32 *table = (mc3_u32 *)(compact + size);
        for (mc3_u32 j = 0; j < nseg; ++j) {
            table[j * 3u] = seg_start[j];
            table[j * 3u + 1u] = seg_qwc[j];
            table[j * 3u + 2u] = seg_img[j];
        }
        piece->packet = 0;
        if (matrix_bytes) {
            // World-baked components get identity (or the preview offset,
            // rewritten per frame); instance templates get their placement's
            // matrix packet from RINS instead.
            const mc3_u32 m = (compact + size + nseg * 12u + 15u) & ~15u;
            mc3_u32 *w = (mc3_u32 *)m;
            for (int i = 0; i < 16; ++i) w[i] = 0u;
            w[0] = w[5] = w[10] = w[15] = fbits(1.0f);
            piece->packet = m;
        }
        r_free(packet_mem);
        packet_mem = compact_mem;
        piece->block = compact;
        piece->block_bytes = size;
        piece->seg_count = nseg;
        piece->segs = compact + size;
        piece->calls = ncall;
        piece->ready = 1;
        s->rsc_cursor = off + consumed;
    }
done:
    if (!piece->ready) {
        r_free(packet_mem);
        s->rsc_error = piece->error;
        s->rsc_state = 3;
        close_rsc_stream(s);
    }
    if (piece->ready) {
        if (s->load_index < 8u || !(s->load_index % 32u))
            mark('R','D','R','W', target->pmd, piece->block_bytes);
    } else mark('R','D','E','R', piece->error, target->pmd);
    return piece->ready ? 1 : 0;
}

// lowPsxGfx::AddVCLCall with the tag's VIF codes filled in instead of NOPs:
// the VIF1 channel runs with TTE, so they reach the VIF exactly as they did in
// MC2's own chains. Same bookkeeping as the original, same packet.
static void emit_ref(mc3_u32 addr, mc3_u32 qwc, mc3_u32 vif0, mc3_u32 vif1) {
    *(volatile mc3_u32 *)VU_STATE_VALID = 0u;
    volatile mc3_u32 *cur = (volatile mc3_u32 *)DMA_PACKET_CUR;
    const mc3_u32 at0 = *cur;
    *(volatile mc3_u32 *)VCL_LAST_PACKET = addr - 16u;
    mc3_u32 at;
    if (*(volatile mc3_u32 *)DMA_PACKET_END < at0 + 16u)
        at = MC3_CALL1(mc3_u32, DMA_PACKET_FLUSH, int)(1);
    else { *cur = at0 + 16u; at = at0; }
    volatile mc3_u32 *t = (volatile mc3_u32 *)at;
    t[0] = (qwc & 0xFFFFu) | DMA_REF; t[1] = addr; t[2] = vif0; t[3] = vif1;
}

static int queue_ref(mc3_u32 addr, mc3_u32 qwc, mc3_u32 vif0, mc3_u32 vif1) {
    emit_ref(addr, qwc, vif0, vif1);
    ++st()->call_count;
    return 1;
}
// Only for packets written through cached memory (bank, bootstrap): arena
// packets are written uncached and passed with their qwc directly.
static int queue_vcl(mc3_u32 packet) {
    return queue_ref(packet + 16u, word(packet) & 0xFFFFu, 0u, 0u);
}

// Take `blocks` from MC3's texture ring exactly as rmcTexturePS2 does. A wrap
// bumps the generation, so MC3's own textures below re-upload when bound, and
// our images uploaded before the wrap stop matching (image_generation).
static mc3_u32 ring_alloc(mc3_u32 blocks) {
    State *s = st();
    volatile mc3_u32 *cur = (volatile mc3_u32 *)TEX_VRAM_CURRENT;
    const mc3_u32 top = *(volatile mc3_u32 *)TEX_VRAM_TOP;
    const mc3_u32 base = *(volatile mc3_u32 *)TEX_VRAM_BASE;
    mc3_u32 at = *cur;
    if (top > 0x4000u || base >= top || top - base < blocks) return 0;
    if (at < base || at > top || top - at < blocks) {
        volatile mc3_u32 *gen = (volatile mc3_u32 *)TEX_VRAM_GENERATION;
        *gen = *gen + 1u;
        at = base;
        ++s->frame_wraps;
    }
    *cur = at + blocks;
    return at;
}

// Make `image` resident for the draws that follow and return its TEX0 switch
// packet, uploading it first unless this frame already did so since the last
// ring wrap. Returns 0 when the arena or the call list is full.
static int image_resident(mc3_u32 image) {
    const State *s = st();
    return s->image_generation[image] == word(TEX_VRAM_GENERATION) + 1u &&
        s->image_frame[image] == s->queued;
}

// Arena bytes one upload of `image` takes.
static mc3_u32 upload_bytes(mc3_u32 image) {
    return (st()->slots[image].levels + 2u) * UPLOAD_PACKET;
}

// A draw is only started when the arena can take every upload it may need -
// a stream cut in the middle of a PMD/PCP chain left the VU with half a batch
// and hung the GIF as soon as driving fast pushed the frame past the arena.
static int arena_can_take(mc3_u32 bytes) {
    const State *s = st();
    return s->arena_used + bytes <= ARENA_BYTES;
}

// One host->local transfer: this 112-byte header in the arena (A+D with
// BITBLTBUF..TRXDIR, then the IMAGE tag), and the texels REF'd straight from
// the loaded file with DIRECT qwc in the REF's own VIF code, so the data is
// quadword aligned and the GIF packet simply continues.
static void queue_upload(mc3_u32 p, mc3_u32 data, mc3_u32 qwc, mc3_u32 dbp,
                         mc3_u32 dbw, mc3_u32 dpsm, mc3_u32 rw, mc3_u32 rh) {
    mc3_u32 *w = (mc3_u32 *)(p | UNCACHED);
    w[0] = 0u; w[1] = 0u; w[2] = VIF_FLUSH; w[3] = VIF_DIRECT | 6u;
    w[4] = 4u; w[5] = 0x10000000u; w[6] = 0xEu; w[7] = 0u;       // A+D x4
    w[8] = 0u; w[9] = dbp | (dbw << 16) | (dpsm << 24); w[10] = 0x50u; w[11] = 0u;
    w[12] = 0u; w[13] = 0u; w[14] = 0x51u; w[15] = 0u;           // TRXPOS
    w[16] = rw; w[17] = rh; w[18] = 0x52u; w[19] = 0u;           // TRXREG
    w[20] = 0u; w[21] = 0u; w[22] = 0x53u; w[23] = 0u;           // TRXDIR
    w[24] = qwc | 0x8000u; w[25] = 0x18000000u; w[26] = 0xEu; w[27] = 0u; // IMAGE
    queue_ref(p, 7u, 0u, 0u);
    queue_ref(data, qwc, 0u, VIF_DIRECT | qwc);
}

// ZBUF for the city. The GS state this renderer sets (bootstrap: TEX1, CLAMP,
// TEST, ALPHA) never included ZBUF, so the city drew with whatever Z write
// mask MC3 had left - and at SetCamera time that is OFF: the MC2 city wrote
// no depth. Cars behind a building were still hidden (drawn before it, then
// painted over) but everything MC3 draws after the city with a depth test and
// no Z write - light reflections on the road (mcGlow::drawLightReflections via
// sub_251AF8, drawn by mcCullableMgr::Render with ZTST GEQUAL), glows - showed
// through the walls. The value is MC3's own: gfxState::SetZWriteEnable writes
// register 0x4E + context (byte 0x715C9D) = *(u64 *)0x6F4888 | ZMSK << 32; the
// write flag it believes in is the byte at 0x70E5A8, restored after the city.
enum { GS_ZBUF_BASE = 0x006F4888, GS_ZWRITE_FLAG = 0x0070E5A8, GS_CONTEXT = 0x00715C9D };
static void queue_zbuf(mc3_u32 arena, int write) {
    State *s = st();
    if (!arena_can_take(48u)) return;
    const mc3_u32 p = arena + s->arena_used;
    mc3_u32 *w = (mc3_u32 *)(p | UNCACHED);
    w[0] = 0u; w[1] = 0u; w[2] = VIF_FLUSH; w[3] = VIF_DIRECT | 2u;
    w[4] = 0x8001u; w[5] = 0x10000000u; w[6] = 0xEu; w[7] = 0u;     // A+D x1, EOP
    w[8] = word(GS_ZBUF_BASE);
    w[9] = word(GS_ZBUF_BASE + 4u) | (write ? 0u : 1u);           // ZMSK
    w[10] = 0x4Eu + *(volatile mc3_u8 *)GS_CONTEXT; w[11] = 0u;
    queue_ref(p, 3u, 0u, 0u);
    s->arena_used += 48u;
}

static mc3_u32 use_image(mc3_u32 image, mc3_u32 arena) {
    State *s = st();
    if (image_resident(image)) return s->image_switch[image];
    const Slot *t = &s->slots[image];
    if (!arena_can_take(upload_bytes(image))) { ++s->frame_dropped; return 0; }
    const mc3_u32 at = ring_alloc(t->blocks);
    if (!at) { ++s->frame_dropped; return 0; }
    mc3_u32 p = arena + s->arena_used;
    mc3_u32 tbp[MAX_LEVELS], next = at;
    for (mc3_u32 k = 0; k < t->levels; ++k) {
        const Level *l = &t->level[k];
        tbp[k] = next;
        queue_upload(p, l->addr, l->qwc, next, l->dbw, l->dpsm, l->rw, l->rh);
        s->frame_upload_bytes += l->qwc * 16u;
        next += l->blocks;
        p += UPLOAD_PACKET;
    }
    const mc3_u32 cbp = next;
    if (t->bpp == 8u) queue_upload(p, t->pal + 16u, 64u, cbp, 1u, 0u, 16u, 16u);
    else queue_upload(p, t->pal + 16u, 4u, cbp, 1u, 0u, 8u, 2u);
    p += UPLOAD_PACKET;
    // Switch: FLUSH, DIRECT 6 - GIF tag (5 x A+D) TEX0, TEX1, MIPTBP1/2, TEXFLUSH
    const Level *l0 = &t->level[0];
    const mc3_u32 psm = t->bpp == 8u ? 0x13u : 0x14u;
    mc3_u32 *w = (mc3_u32 *)(p | UNCACHED);
    w[0] = 0u; w[1] = 0u; w[2] = VIF_FLUSH; w[3] = VIF_DIRECT | 6u;
    w[4] = 0x8005u; w[5] = 0x10000000u; w[6] = 0xEu; w[7] = 0u;
    w[8] = tbp[0] | ((mc3_u32)l0->tbw << 14) | (psm << 20) | ((mc3_u32)t->tw << 26) |
           ((mc3_u32)(t->th & 3u) << 30);
    w[9] = ((mc3_u32)t->th >> 2) | (t->tcc << 2) | (cbp << 5) | (1u << 29);   // TCC, CBP, CLD
    w[10] = 0x06u; w[11] = 0u;
    w[12] = 0x120u | ((mc3_u32)(t->levels - 1u) << 2);   // MMAG lin, MMIN 4, MXL
    w[13] = 0xFC8u;                                      // K = -3.5, as MC2
    w[14] = 0x14u; w[15] = 0u;
    mc3_u32 m1lo = 0u, m1hi = 0u;
    if (t->levels > 1u) m1lo |= tbp[1] | ((mc3_u32)t->level[1].tbw << 14);
    if (t->levels > 2u) {
        m1lo |= (tbp[2] & 0xFFFu) << 20;
        m1hi |= (tbp[2] >> 12) | ((mc3_u32)t->level[2].tbw << 2);
    }
    if (t->levels > 3u) m1hi |= (tbp[3] << 8) | ((mc3_u32)t->level[3].tbw << 22);
    w[16] = m1lo; w[17] = m1hi; w[18] = 0x34u; w[19] = 0u;
    w[20] = 0u; w[21] = 0u; w[22] = 0x36u; w[23] = 0u;
    w[24] = 0u; w[25] = 0u; w[26] = 0x3Fu; w[27] = 0u;
    s->arena_used += upload_bytes(image);
    ++s->frame_uploads;
    // +1 so that a fresh image_generation of 0 never matches generation -1
    s->image_generation[image] = word(TEX_VRAM_GENERATION) + 1u;
    s->image_frame[image] = s->queued;
    s->image_switch[image] = p;
    return p;
}

static mc3_u32 texture_slot(mc3_u32 handle);

// Select a texture. MC2 chains repeat the same CALL after nearly every
// batch; each switch carries a FLUSH that makes the VIF wait for the VU, so
// sending a switch that is already in effect costs a pipeline stall for
// nothing (measured: ~2000 of them per frame, 30 -> 17 FPS).
static volatile mc3_u32 *ad_begin(mc3_u32 arena, mc3_u32 n);
static void ad_put(volatile mc3_u32 *w, mc3_u32 k, mc3_u32 lo, mc3_u32 hi, mc3_u32 reg);
static void bind_switch(mc3_u32 sw) {
    State *s = st();
    if (sw == s->bound_switch) return;
    s->bound_switch = sw;
    queue_ref(sw, 7u, 0u, 0u);
    if (s->force_tcc) {
        // the same TEX0 with TCC=1 (and no CLUT reload): the texture's own
        // alpha - the wetness of MC2's ground textures - reaches the frame
        const volatile mc3_u32 *w = (const volatile mc3_u32 *)(sw | UNCACHED);
        volatile mc3_u32 *a = ad_begin(s->arena[s->queued & 1u], 1u);
        if (a) ad_put(a, 0, w[8], (w[9] | 4u) & ~(7u << 29), 0x06u);
    }
}

// Queue a piece's PMD0 as MC2 would run it: each segment, then the texture
// its CALL selected. The whole piece is skipped when its uploads do not fit;
// once started it is always sent whole - if a ring wrap still makes an upload
// fail, the texture in effect stays (a wrong texture, never a cut stream).
static int queue_piece(const Piece *piece, mc3_u32 arena) {
    State *s = st();
    const mc3_u32 *seg = (const mc3_u32 *)piece->segs;
    mc3_u32 need = 0, last = NO_IMAGE;
    for (mc3_u32 k = 0; k < piece->seg_count; ++k) {
        const mc3_u32 image = texture_slot(seg[k * 3u + 2u]);
        if (image != NO_IMAGE && image != last && !image_resident(image))
            need += upload_bytes(image);
        last = image;
    }
    if (!arena_can_take(need)) { ++s->frame_skipped; return 0; }
    for (mc3_u32 k = 0; k < piece->seg_count; ++k) {
        queue_ref(piece->block + seg[k * 3u], seg[k * 3u + 1u], 0u, 0u);
        const mc3_u32 image = texture_slot(seg[k * 3u + 2u]);
        if (image == NO_IMAGE) continue;
        const mc3_u32 sw = use_image(image, arena);
        if (sw) bind_switch(sw);
    }
    ++s->frame_pmd;
    return 1;
}

// ---------------------------------------------------------------------------
// MC2 CPVS
// ---------------------------------------------------------------------------
static __attribute__((noinline)) const char *cpvs_folder() {
    return mc2_city_root(active_city());
}
static __attribute__((noinline)) const char *cpvs_suffix() {
    static const char s[] = "_cpvs.rsc";
    return s;
}
static __attribute__((noinline)) const char *default_time() {
    static const char s[] = "midnight";
    return s;
}
static __attribute__((noinline)) const char *default_weather() {
    static const char s[] = "clear";
    return s;
}
// The race's time of day and weather: mcRaceConfig (current, 0x619B10) +4 and
// +8, named by the game's own tables mc::TODNames 0x619B48 (dawn, midnight,
// dusk) and mc::WeatherNames 0x619B58 (clear, cloudy, rainy) - MC2 names its
// files the same way. Until 2026-09-30 this read [boot] time/weather, so an
// Arcade race picked at dawn in the menu drew midnight's CPVS, textures,
// props and sky. The boot keys stay the fallback when there is no config.
enum { TOD_NAMES = 0x00619B48, WEATHER_NAMES = 0x00619B58 };
static const char *cond_name(mc3_u32 field, mc3_u32 table, mc3_u32 boot, const char *fallback) {
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG_CURRENT;
    if (cfg >= 0x00100000u && cfg < 0x02000000u) {
        const mc3_u32 i = *(volatile mc3_u32 *)(cfg + field);
        if (i < 3u) return (const char *)*(volatile mc3_u32 *)(table + 4u * i);
    }
    const char *v = mc3_bootarg(boot);
    return v && v[0] ? v : fallback;
}
static const char *cond_time() { return cond_name(4u, TOD_NAMES, MC3_ID('t','i','m','e'), default_time()); }
static const char *cond_weather() { return cond_name(8u, WEATHER_NAMES, MC3_ID('w','e','a','t'), default_weather()); }
static int append(char *out, int at, const char *text) {
    while (*text && at < 94) out[at++] = *text++;
    out[at] = 0;
    return at;
}

// ---------------------------------------------------------------------------
// MC2 textures read from the original files, uploaded with their mip levels.
//
// TEX0 (192 bytes): +0x88 u16 width, height; +0x90 PAL0 handle; +0x94 MIP0
// handle (bit 14 set = same container, clear = the shared textures_*.rsc).
// MIP0: +0x08 u32 offset of each level; +0x28 low nibble 5 = 8 bpp, 6 = 4 bpp,
// next byte = levels - 1; +0x48 u16 size of each level in 256-byte blocks;
// +0x58 u8 buffer width (TBW) of each level. Each level is the memory image of
// a PSMCT32 rectangle - (w/2 x h/2) words for 8 bpp, the block table below for
// 4 bpp - so it is uploaded as is, like MC2 did; the 16x16 4 bpp level 0 is
// the exception, 128 packed bytes uploaded as PSMT4. PAL0: 16-byte header,
// then 256 or 16 CLUT words ready to upload.
// LOD as MC2 sets it (TEX1 read from an MC2 savestate): LCM 0, MMAG linear,
// MMIN 4 (bilinear, nearest mip), L 0, K -3.5; MXL per texture.
// ---------------------------------------------------------------------------
static __attribute__((noinline)) const char *textures_prefix() {
    static const char s[] = "textures_";
    return s;
}
static __attribute__((noinline)) const char *rsc_suffix() {
    static const char s[] = ".rsc";
    return s;
}

// Blocks per level -> (across, down) blocks of 8x8 words, 4 bpp.
static int rect4(mc3_u32 blocks, mc3_u32 *across, mc3_u32 *down) {
    switch (blocks) {
    case 1: *across = 1; *down = 1; return 1;
    case 2: *across = 2; *down = 1; return 1;
    case 4: *across = 2; *down = 2; return 1;
    case 8: *across = 4; *down = 2; return 1;
    case 16: *across = 4; *down = 4; return 1;
    case 32: *across = 8; *down = 4; return 1;
    }
    return 0;
}
static mc3_u32 log2u(mc3_u32 v) { mc3_u32 n = 0; while (v > 1u) { v >>= 1; ++n; } return n; }

// Index of a TEX0/MIP0/PAL0 handle's entry: container 0 = losangeles.rsc,
// 1 = textures_*.rsc. Returns the entry's data address or 0.
static mc3_u32 amb_entry(mc3_u32 index, mc3_u32 fourcc);
static mc3_u32 tex_entry(mc3_u32 container, mc3_u32 handle, mc3_u32 fourcc) {
    State *s = st();
    const mc3_u32 index = (handle & 0x3FFFu) - 1u;
    if (!(handle & 0x3FFFu)) return 0u;
    if (container == 2u) return amb_entry(index, fourcc);
    if (container == 3u) {                              // the sky, read whole (step_sky)
        if (!s->sky || index >= s->sky_count) return 0u;
        if (word(s->sky + 12u + index * 8u) != fourcc) return 0u;
        return s->sky + word(s->sky + 8u + index * 8u);
    }
    if (container == 0u) {
        if (index >= s->rsc_count || !s->tex_dest) return 0u;
        if (s->tex_type[index] != fourcc) return 0u;
        return ((const mc3_u32 *)s->tex_dest)[index];
    }
    if (!s->shared || index >= s->shared_count) return 0u;
    if (word(s->shared + 12u + index * 8u) != fourcc) return 0u;
    return s->shared + word(s->shared + 8u + index * 8u);
}

// Build one slot from a TEX0 (in `container`). Returns the slot or NO_IMAGE.
static mc3_u32 make_slot(mc3_u32 container, mc3_u32 tex) {
    State *s = st();
    const mc3_u32 w = word(tex + 0x88u) & 0xFFFFu, h = word(tex + 0x88u) >> 16;
    const mc3_u32 hpal = word(tex + 0x90u), hmip = word(tex + 0x94u);
    // "this container": bit 15 in ambients, bit 14 in the city container,
    // both (0xC0xx) in the sky's; neither = the shared bank
    const mc3_u32 local = container == 2u ? 0x8000u : container == 3u ? 0xC000u : 0x4000u;
    const mc3_u32 mip = tex_entry((hmip & local) ? container : 1u, hmip, MIP0);
    const mc3_u32 pal = tex_entry((hpal & local) ? container : 1u, hpal, PAL0);
    if (!mip || !pal || w < 8u || h < 8u || w > 256u || h > 256u ||
        (w & (w - 1u)) || (h & (h - 1u)) || h > w) return NO_IMAGE;
    for (mc3_u32 i = 0; i < s->slot_count; ++i)
        if (s->slots[i].mip == mip && s->slots[i].pal == pal) return i;
    if (s->slot_count >= MAX_IMAGES) return NO_IMAGE;
    const mc3_u32 fmt = *(volatile mc3_u8 *)(mip + 0x28u) & 15u;
    if (fmt != 5u && fmt != 6u) return NO_IMAGE;
    Slot *t = &s->slots[s->slot_count];
    t->mip = mip; t->pal = pal;
    t->bpp = fmt == 5u ? 8u : 4u;
    t->tw = log2u(w); t->th = log2u(h);
    mc3_u32 levels = (*(volatile mc3_u8 *)(mip + 0x29u) & 7u) + 1u;
    {   // [boot] mc2m = 0: level 0 only (diagnostic A/B of the mipmaps)
        const char *v = mc3_bootarg(MC3_ID('m','c','2','m'));
        if (v && v[0] == '0') levels = 1u;
    }
    if (levels > MAX_LEVELS) levels = MAX_LEVELS;
    t->levels = 0; t->blocks = 0;
    for (mc3_u32 k = 0; k < levels; ++k) {
        const mc3_u32 wk = w >> k, hk = h >> k;
        const mc3_u32 off = word(mip + 0x08u + k * 4u);
        const mc3_u32 have = (word(mip + 0x48u + (k >> 1) * 4u) >> ((k & 1u) * 16u)) & 0xFFFFu;
        mc3_u32 tbw = *(volatile mc3_u8 *)(mip + 0x58u + k);
        mc3_u32 rw, rh, dbw = 1u, dpsm = 0u, blocks;
        if (t->bpp == 8u) {
            rw = wk / 2u; rh = hk / 2u;
            if (!rw || !rh) break;
            dbw = (rw + 63u) / 64u;
            blocks = (wk * hk + 255u) / 256u;
        } else if (k == 0u && wk == 16u && hk == 16u) {
            rw = 16u; rh = 16u; dpsm = 0x14u;          // 128 packed bytes, PSMT4
            blocks = 1u;
        } else {
            mc3_u32 across, down;
            blocks = wk * hk / 512u;
            if (!rect4(blocks, &across, &down)) break;
            rw = across * 8u; rh = down * 8u;
        }
        const mc3_u32 bytes = dpsm ? rw * rh / 2u : rw * rh * 4u;
        if (!off || (off & 15u) || (bytes & 15u) || bytes > have * 256u + 128u) break;
        if (!tbw) tbw = wk >= 64u ? wk / 64u : 1u;
        Level *l = &t->level[k];
        l->addr = mip + off; l->qwc = bytes / 16u;
        l->rw = rw; l->rh = rh; l->dbw = dbw; l->dpsm = dpsm; l->tbw = tbw;
        l->blocks = blocks;
        t->blocks += blocks;
        t->levels = k + 1u;
    }
    if (!t->levels) return NO_IMAGE;
    t->clut_blocks = t->bpp == 8u ? 4u : 1u;
    t->blocks += t->clut_blocks;
    // The shared bank's ground textures (asphalt, sidewalks) have alpha 0 in
    // every CLUT entry: the channel carries nothing, and with TCC=1 plus
    // MC2's blending they drew invisible. Such textures take their alpha from
    // the vertex (TCC=0). Eight more ground textures of that bank (crosswalk,
    // ghetto/freeway asphalt, two sidewalks) carry a faint alpha, at most 0x1F:
    // not opacity either - with TCC=1 the crosswalks drew almost transparent.
    // The one texture of LA's bank with more alpha, l_roads_sidewalkghetto0
    // (CLUT alpha up to 0x80), has alpha 0 in 99.4% of its texels - a mask,
    // not a cut-out: with TCC=1 the ghetto sidewalks (6laneroad_g_*,
    // 4laneroad_g_*, the ghetto blocks) vanished and the background showed
    // (found from the player's pins, 2026-09-27). So the shared bank - ground
    // textures only - is always TCC=0; in the city container any non-zero
    // alpha keeps TCC=1 (glass, cut-outs).
    mc3_u32 top_alpha = 0u;
    for (mc3_u32 i = 0; i < (t->bpp == 8u ? 256u : 16u); ++i) {
        const mc3_u32 a = word(pal + 16u + i * 4u) >> 24;
        if (a > top_alpha) top_alpha = a;
    }
    t->tcc = container == 1u ? 0u : top_alpha != 0u;
    return s->slot_count++;
}

// The slot of a texture handle from a PMD/PCP CALL, NO_IMAGE if unknown.
static mc3_u32 texture_slot(mc3_u32 handle) {
    State *s = st();
    if (!s->textures_ready) return NO_IMAGE;
    if (!(handle & 0x3FFFu) || s->all_white) return s->white_slot;
    const mc3_u32 index = (handle & 0x3FFFu) - 1u;
    if (handle & 0x4000u)
        return index < s->rsc_count ? ((const mc3_u16 *)s->slot_of_local)[index] : NO_IMAGE;
    return index < s->shared_count ? ((const mc3_u16 *)s->slot_of_shared)[index] : NO_IMAGE;
}

// Handle 0 = no texture: 16x16 8 bpp of index 0 whose CLUT[0] is 0x80 white,
// so MODULATE gives back the vertex colour.
static int make_white() {
    State *s = st();
    if (s->slot_count >= MAX_IMAGES || !heap_room(256u + 1024u + 32u)) return 0;
    mc3_u8 *mem = (mc3_u8 *)r_alloc(256u + 1024u + 32u);
    if (!mem) return 0;
    const mc3_u32 base = ((mc3_u32)mem + 15u) & ~15u;
    for (mc3_u32 i = 0; i < (256u + 1024u) / 4u; ++i) ((mc3_u32 *)base)[i] = 0u;
    ((mc3_u32 *)base)[256u / 4u] = 0x80808080u;
    Slot *t = &s->slots[s->slot_count];
    t->mip = base; t->pal = base + 256u - 16u;   // PAL0 data sits at +0x10
    t->bpp = 8u; t->tw = 4u; t->th = 4u; t->levels = 1u;
    Level *l = &t->level[0];
    l->addr = base; l->qwc = 16u; l->rw = 8u; l->rh = 8u; l->dbw = 1u;
    l->dpsm = 0u; l->tbw = 1u; l->blocks = 1u;
    t->clut_blocks = 4u; t->blocks = 5u; t->tcc = 1u;
    s->white_slot = s->slot_count++;
    return 1;
}

static int build_slots() {
    State *s = st();
    const mc3_u32 local_bytes = s->rsc_count * 2u + 16u;
    const mc3_u32 shared_bytes = s->shared_count * 2u + 16u;
    mc3_u8 *a = heap_room(local_bytes + shared_bytes) ? (mc3_u8 *)r_alloc(local_bytes) : 0;
    mc3_u8 *b = a ? (mc3_u8 *)r_alloc(shared_bytes) : 0;
    if (!b) { r_free(a); return 0; }
    s->slot_of_local = ((mc3_u32)a + 15u) & ~15u;
    s->slot_of_shared = ((mc3_u32)b + 15u) & ~15u;
    s->slot_count = 0;
    if (!make_white()) return 0;
    mc3_u16 *local = (mc3_u16 *)s->slot_of_local;
    mc3_u16 *shared = (mc3_u16 *)s->slot_of_shared;
    for (mc3_u32 i = 0; i < s->rsc_count; ++i) {
        local[i] = NO_IMAGE;
        if (s->tex_type[i] == TEX0 && ((const mc3_u32 *)s->tex_dest)[i])
            local[i] = (mc3_u16)make_slot(0u, ((const mc3_u32 *)s->tex_dest)[i]);
    }
    for (mc3_u32 i = 0; i < s->shared_count; ++i) {
        shared[i] = NO_IMAGE;
        if (word(s->shared + 12u + i * 8u) == TEX0)
            shared[i] = (mc3_u16)make_slot(1u, s->shared + word(s->shared + 8u + i * 8u));
    }
    return 1;
}

// Read forward in losangeles.rsc to `limit`, keeping the bytes of every
// TEX0/MIP0/PAL0 entry and dropping the rest; at most `budget` bytes per
// call. Returns 1 when `limit` is reached, 0 to be called again, -1 on error.
static int consume_to(mc3_u32 limit, mc3_u32 budget) {
    State *s = st();
    mc3_u8 *scratch = (mc3_u8 *)s->rsc_scratch;
    const mc3_u8 *table = (const mc3_u8 *)s->rsc_table;
    const mc3_u32 *dest = (const mc3_u32 *)s->tex_dest;
    while (s->rsc_cursor < limit) {
        while (s->tex_next < s->rsc_count &&
               (!dest[s->tex_next] || le32(table + s->tex_next * 8u) < s->rsc_cursor))
            ++s->tex_next;
        const mc3_u32 e = s->tex_next;
        const mc3_u32 at = e < s->rsc_count ? le32(table + e * 8u) : s->rsc_size;
        if (at > s->rsc_cursor) {                      // plain gap
            mc3_u32 n = (at < limit ? at : limit) - s->rsc_cursor;
            if (n > 2048u) n = 2048u;
            if (!budget) return 0;
            if (n > budget) n = budget;
            if (!read_exact(s->rsc_stream, scratch, n)) return -1;
            s->rsc_cursor += n; budget -= n;
            continue;
        }
        const mc3_u32 end = e + 1u < s->rsc_count ? le32(table + (e + 1u) * 8u) : s->rsc_size;
        if (end > limit && limit != s->rsc_size) return -1;   // a PMD inside a texture?
        if (!budget) return 0;
        if (!read_exact(s->rsc_stream, (void *)dest[e], end - at)) return -1;
        s->rsc_cursor = end;
        budget = budget > end - at ? budget - (end - at) : 0u;
        ++s->tex_next;
    }
    return 1;
}

// Sizes the copy of every texture entry of losangeles.rsc, from its
// directory, and gives each a place in one block.
static int plan_rsc_textures() {
    State *s = st();
    const mc3_u8 *table = (const mc3_u8 *)s->rsc_table;
    mc3_u32 total = 0;
    for (mc3_u32 i = 0; i < s->rsc_count; ++i) {
        const mc3_u32 type = le32(table + i * 8u + 4u);
        s->tex_type[i] = type;
        if (type != TEX0 && type != MIP0 && type != PAL0) continue;
        const mc3_u32 off = le32(table + i * 8u);
        const mc3_u32 end = i + 1u < s->rsc_count ? le32(table + (i + 1u) * 8u) : s->rsc_size;
        if (end < off || end > s->rsc_size || (off & 15u)) return 0;
        total += (end - off + 15u) & ~15u;
    }
    const mc3_u32 map_bytes = s->rsc_count * 4u + 16u;
    if (!heap_room(total + map_bytes + 32u)) { s->texture_error = 3u; return 0; }
    mc3_u8 *map = (mc3_u8 *)r_alloc(map_bytes);
    mc3_u8 *data = map ? (mc3_u8 *)r_alloc(total + 16u) : 0;
    if (!data) { r_free(map); s->texture_error = 3u; return 0; }
    s->tex_dest = ((mc3_u32)map + 15u) & ~15u;
    mc3_u32 *dest = (mc3_u32 *)s->tex_dest;
    mc3_u32 at = ((mc3_u32)data + 15u) & ~15u;
    for (mc3_u32 i = 0; i < s->rsc_count; ++i) {
        dest[i] = 0u;
        const mc3_u32 type = s->tex_type[i];
        if (type != TEX0 && type != MIP0 && type != PAL0) continue;
        const mc3_u32 off = le32(table + i * 8u);
        const mc3_u32 end = i + 1u < s->rsc_count ? le32(table + (i + 1u) * 8u) : s->rsc_size;
        dest[i] = at;
        at += (end - off + 15u) & ~15u;
    }
    s->tex_bytes = total;
    s->tex_next = 0;
    return 1;
}

// textures_<time>_<weather>.rsc, the shared bank handles without bit 14 name.
// Small (1 MB): read whole, a slice per frame.
static void step_shared_textures() {
    State *s = st();
    if (s->shared_state == 0u) {
        char path[96];
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, textures_prefix());
        n = append(path, n, cond_time());
        path[n++] = '_'; path[n] = 0;
        n = append(path, n, cond_weather());
        append(path, n, rsc_suffix());
        const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
            (path, STREAM_RAW);
        if (!h) { s->texture_error = 6u; s->shared_state = 3u; return; }
        const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
        mc3_u8 *mem = size > 16 && size <= 0x200000 && heap_room((mc3_u32)size + 16u)
            ? (mc3_u8 *)r_alloc((mc3_u32)size + 16u) : 0;
        if (!mem) {
            s->texture_error = 7u;
            MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
            s->shared_state = 3u;
            return;
        }
        s->shared_stream = h;
        s->shared = ((mc3_u32)mem + 15u) & ~15u;
        s->shared_bytes = (mc3_u32)size;
        s->shared_done = 0u;
        s->shared_state = 1u;
        return;
    }
    if (s->shared_state != 1u) return;
    mc3_u32 part = s->shared_bytes - s->shared_done;
    if (part > CPVS_SLICE) part = CPVS_SLICE;
    const int ok = read_exact(s->shared_stream, (void *)(s->shared + s->shared_done), part);
    if (ok) s->shared_done += part;
    if (ok && s->shared_done < s->shared_bytes) return;
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->shared_stream);
    s->shared_stream = 0u;
    const mc3_u32 count = ok ? word(s->shared + 4u) : 0u;
    int valid = ok && word(s->shared) == RSC0 && count && count < 4096u &&
        8u + count * 8u <= s->shared_bytes;
    for (mc3_u32 i = 0; valid && i < count; ++i) {
        const mc3_u32 off = word(s->shared + 8u + i * 8u);
        valid = off >= 8u + count * 8u && off < s->shared_bytes && !(off & 15u);
    }
    if (!valid) { s->texture_error = 8u; s->shared_state = 3u; return; }
    s->shared_count = count;
    s->shared_state = 2u;
}

// ---------------------------------------------------------------------------
// MC2's sky. Why there was none: the city .pck we build has no sky - its
// mcSkyHatClass (mcCity+0x190) loads skyhat_<city>_<tod>_<weather>.parfileio
// (colours, lightning) but its eight layer models (+0x34..+0x50, rmcModel*,
// drawn by mcSkyHatClass::Draw 0x25C238 with the sky shader group mcCity+0x10)
// are all null and that group has no slot, so MC3 only clears the screen to
// m_clearColor. And MC2's sky was never installed: it is not in the city or
// ambients containers but in resource/<city>/<time>_<weather>.rsc (222 KB),
// MOD0 sky_0 (the dome: radius 100 m, y -4..42, five textures) and, rainy,
// sky_1..sky_6 (cloud layers), with their TEX0/MIP0/PAL0, handles 0xC0xx =
// this container. MC2 draws it around the camera before everything else.
// Here: the container read whole (container 3 for tex_entry/make_slot), each
// MOD0 a layer, queued first in draw_city at the camera's position with depth
// test ALWAYS and no Z write, so the city covers it. [boot] mc2b = 0: no sky.
// ---------------------------------------------------------------------------
static int sky_enabled() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','b'));
    return !value || value[0] != '0';
}
static void load_sky_layer(mc3_u32 m) {
    State *s = st();
    if (s->sky_layers >= MAX_SKY_LAYERS) return;
    const mc3_u32 n = word(m) & 0xFFFFu;
    if (!n || n > MAX_PROP_PACKETS || (word(m) >> 16) != 1u) return;
    SkyLayer *l = &s->sky_layer[s->sky_layers];
    const mc3_u32 mat = m + word(m + 4u), pkt = m + word(m + 8u);
    for (mc3_u32 k = 0; k < n; ++k) {
        const mc3_u32 pd = m + word(pkt + k * 4u);
        const mc3_u32 off = word(pd), q = word(pd + 4u) & 0xFFFFu;
        if ((off & 15u) || !q) return;
        l->addr[k] = m + off;
        l->qwc[k] = (mc3_u16)q;
        const mc3_u32 handle = word(m + word(mat + k * 4u));
        const mc3_u32 tex = tex_entry(3u, handle, TEX0);
        const mc3_u32 slot = tex ? make_slot(3u, tex) : NO_IMAGE;
        l->slot[k] = (mc3_u16)(slot == NO_IMAGE ? s->white_slot : slot);
    }
    l->packets = n;
    ++s->sky_layers;
}
static void step_sky() {
    State *s = st();
    if (s->sky_state == 0u) {
        char path[96];
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, cond_time());
        path[n++] = '_'; path[n] = 0;
        n = append(path, n, cond_weather());
        append(path, n, rsc_suffix());
        const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
        if (!h) { s->sky_state = 9u; mark('R','S','K','E', 1u, 0u); return; }
        const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
        mc3_u8 *mem = size > 16 && size <= 0x80000 && heap_room((mc3_u32)size + 16u)
            ? (mc3_u8 *)r_alloc((mc3_u32)size + 16u) : 0;
        if (!mem) {
            MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
            s->sky_state = 9u; mark('R','S','K','E', 2u, (mc3_u32)size); return;
        }
        s->sky_stream = h;
        s->sky = ((mc3_u32)mem + 15u) & ~15u;
        s->sky_bytes = (mc3_u32)size;
        s->sky_done = 0u;
        s->sky_state = 1u;
        return;
    }
    if (s->sky_state != 1u) return;
    mc3_u32 part = s->sky_bytes - s->sky_done;
    if (part > CPVS_SLICE) part = CPVS_SLICE;
    const int ok = read_exact(s->sky_stream, (void *)(s->sky + s->sky_done), part);
    if (ok) s->sky_done += part;
    if (ok && s->sky_done < s->sky_bytes) return;
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->sky_stream);
    s->sky_stream = 0u;
    const mc3_u32 count = ok ? word(s->sky + 4u) : 0u;
    int valid = ok && word(s->sky) == RSC0 && count && count < 256u && 8u + count * 8u <= s->sky_bytes;
    for (mc3_u32 i = 0; valid && i < count; ++i) {
        const mc3_u32 off = word(s->sky + 8u + i * 8u);
        valid = off >= 8u + count * 8u && off < s->sky_bytes && !(off & 15u);
    }
    if (!valid) { s->sky_state = 9u; s->sky = 0u; mark('R','S','K','E', 3u, count); return; }
    s->sky_count = count;
    for (mc3_u32 i = 0; i < count; ++i)
        if (word(s->sky + 12u + i * 8u) == MOD0) load_sky_layer(s->sky + word(s->sky + 8u + i * 8u));
    s->sky_state = 2u;
    mark('R','S','K','Y', s->sky_layers, s->sky_bytes);
}

// ---------------------------------------------------------------------------
// Runtime catalog: everything the renderer needs about the city, derived at
// load time from the ORIGINAL MC2 files - no generated manifest, placement or
// CPVS map ships with the mod any more.
//
//   losangeles.rnt       names of the CMI0 models            (RNT0)
//   losangeles.rsc CMI0  cull sphere + near-LOD PMD0 handles  (read by Seek)
//   city/losangeles.lvl  the list of hoods
//   city/<hood>.hood     instance_component: type, extension, matrix, bounds
//   <tod>_cpvs.rnt       PCP0 names: <owner>_x_<type><ext>_0_main (instances)
//                        and <component>_0_main (world-baked components)
//
// RNT0: +4 count, +0x18 count x {u32 string offset (absolute), fourcc,
// handle}. CMI0 (64 bytes): centre xyz, radius, 4 slots of 2 handles (0,1
// near LOD, 2,3 far), flags. The hood files and the .rnt disagree on case,
// so every name compares lowercased.
// ---------------------------------------------------------------------------

static __attribute__((noinline)) const char *city_folder() {
    return mc2_city_dir(active_city());
}
static __attribute__((noinline)) const char *rnt_name() {
    return mc2_city_rnt(active_city());
}
static __attribute__((noinline)) const char *lvl_name() {
    return mc2_city_lvl(active_city());
}
static __attribute__((noinline)) const char *hood_suffix() {
    static const char s[] = ".hood";
    return s;
}
static __attribute__((noinline)) const char *cpvs_rnt_suffix() {
    static const char s[] = "_cpvs.rnt";
    return s;
}

static mc3_u32 lower(mc3_u32 c) { return c >= 'A' && c <= 'Z' ? c + 32u : c; }
// FNV-1a over the lowercased name, `n` characters or up to NUL/space.
// MC2's fake reflections: `l_inst_*cardblk*` / `*card*` models are vertical
// glow cards (fx_reflection_01) standing BELOW the ground under shop windows -
// the windows mirrored. 68 models / 530 instances in LA.
static int name_has_card(const char *p) {
    for (mc3_u32 i = 0; i < 120u && p[i]; ++i) {
        const char a = p[i] | 0x20, b = p[i + 1] | 0x20, c = p[i + 2] | 0x20, d = p[i + 3] | 0x20;
        if (a == 'c' && b == 'a' && c == 'r' && d == 'd') return 1;
    }
    return 0;
}
static mc3_u32 name_hash(const char *p, mc3_u32 n) {
    mc3_u32 h = 2166136261u;
    for (mc3_u32 i = 0; i < n && p[i] && p[i] != ' ' && p[i] != '\t' &&
         p[i] != '\r' && p[i] != '\n'; ++i)
        h = (h ^ lower((mc3_u8)p[i])) * 16777619u;
    return h;
}
static int name_equal(const char *a, const char *b, mc3_u32 n) {
    for (mc3_u32 i = 0; i < n; ++i) {
        const mc3_u32 ca = lower((mc3_u8)a[i]), cb = lower((mc3_u8)b[i]);
        const int ea = !ca || ca == ' ' || ca == '\t' || ca == '\r' || ca == '\n';
        const int eb = !cb || cb == ' ' || cb == '\t' || cb == '\r' || cb == '\n';
        if (ea || eb) return ea && eb;
        if (ca != cb) return 0;
    }
    return 1;
}
static mc3_u32 find_model(const char *p, mc3_u32 n) {
    State *s = st();
    mc3_u32 at = name_hash(p, n) & (MODEL_HASH - 1u);
    for (mc3_u32 k = 0; k < MODEL_HASH; ++k, at = (at + 1u) & (MODEL_HASH - 1u)) {
        const mc3_u32 m = s->model_hash[at];
        if (m == 0xFFFFu) return NO_IMAGE;
        if (name_equal((const char *)s->model_name[m], p, n)) return m;
    }
    return NO_IMAGE;
}

static __attribute__((noinline)) int stream_seek(mc3_u32 h, mc3_u32 pos) {
    return MC3_CALL2(int, STREAM_SEEK, mc3_u32, int)(h, (int)pos);
}

// Whole small file into the heap (16-aligned, NUL after the end).
static mc3_u32 read_whole(const char *path, mc3_u32 max, mc3_u32 *bytes, mc3_u32 *mem) {
    const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
    if (!h) return 0u;
    const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
    mc3_u8 *m = size > 0 && (mc3_u32)size <= max && heap_room((mc3_u32)size + 32u)
        ? (mc3_u8 *)r_alloc((mc3_u32)size + 32u) : 0;
    mc3_u32 base = 0u;
    if (m) {
        base = ((mc3_u32)m + 15u) & ~15u;
        if (read_exact(h, (void *)base, (mc3_u32)size)) ((mc3_u8 *)base)[size] = 0;
        else { r_free(m); m = 0; base = 0u; }
    }
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
    *bytes = m ? (mc3_u32)size : 0u;
    *mem = (mc3_u32)m;
    return base;
}

// CMI0 models and the PMD0 list, before the geometry pass. Sets the targets
// (sorted by PMD index) and each model's pieces; rewinds the RSC stream.
static int build_catalog() {
    State *s = st();
    mc3_u32 bytes, mem;
    const mc3_u32 rnt = read_whole(rnt_name(), 0x40000u, &bytes, &mem);
    if (!rnt || word(rnt) != RNT0 || 0x18u + word(rnt + 4u) * 12u > bytes) {
        s->manifest_error = 1u; return 0;
    }
    s->rnt = rnt; s->rnt_mem = mem;
    const mc3_u32 nrec = word(rnt + 4u);
    const mc3_u8 *table = (const mc3_u8 *)s->rsc_table;
    for (mc3_u32 i = 0; i < MODEL_HASH; ++i) s->model_hash[i] = 0xFFFFu;
    mc3_u32 nt = 0;
    s->model_count = 0;
    for (mc3_u32 e = 0; e < s->rsc_count; ++e) {
        if (le32(table + e * 8u + 4u) != CMI0) continue;
        if (s->model_count >= MAX_MODELS) { s->manifest_error = 3u; return 0; }
        mc3_u8 cmi[64] __attribute__((aligned(16)));
        if (stream_seek(s->rsc_stream, le32(table + e * 8u)) < 0 ||
            !read_exact(s->rsc_stream, cmi, 64u)) { s->manifest_error = 4u; return 0; }
        const mc3_u32 m = s->model_count;
        // name: the RNT record whose handle is this entry of this container
        mc3_u32 name = 0u;
        for (mc3_u32 r = 0; r < nrec && !name; ++r) {
            const mc3_u32 rec = rnt + 0x18u + r * 12u;
            if (word(rec + 4u) == CMI0 && (word(rec + 8u) & 0x3FFFu) == e + 1u &&
                word(rec) < bytes) name = rnt + word(rec);
        }
        if (!name) { s->manifest_error = 5u; return 0; }
        s->model_name[m] = name;
        if (name_has_card((const char *)name)) s->card_model[m >> 5] |= 1u << (m & 31u);
        else s->card_model[m >> 5] &= ~(1u << (m & 31u));
        mc3_u32 at = name_hash((const char *)name, 256u) & (MODEL_HASH - 1u);
        while (s->model_hash[at] != 0xFFFFu) at = (at + 1u) & (MODEL_HASH - 1u);
        s->model_hash[at] = (mc3_u16)m;
        const mc3_u32 *w = (const mc3_u32 *)cmi;
        ModelDraw *md = &s->models[m];
        md->flags = 0u; md->count = 0u;
        // near LOD = slots 0 and 1; a model without one falls back to 2 and 3
        mc3_u32 first = 4u;
        if (!w[4] && !w[5] && !w[6] && !w[7]) first = 8u;
        for (mc3_u32 k = first; k < first + 4u; ++k) {
            const mc3_u32 hnd = w[k];
            if (!hnd) continue;
            const mc3_u32 pmd = (hnd & 0x3FFFu) - 1u;
            if (!(hnd & 0x4000u) || pmd >= s->rsc_count ||
                le32(table + pmd * 8u + 4u) != PMD0 || nt >= MAX_PIECES) {
                s->manifest_error = 6u; return 0;
            }
            Target *t = &s->targets[nt++];
            t->pmd = pmd; t->model_id = m; t->flags = 0u;
            t->cx = fword(w); t->cy = fword(w + 1); t->cz = fword(w + 2);
            t->radius = fword(w + 3);
            t->ox = t->oy = t->oz = t->lateral = t->forward = 0.0f;
        }
        ++s->model_count;
    }
    // Sort by PMD index (the pass reads the RSC forward) and bind models.
    for (mc3_u32 i = 1; i < nt; ++i) {
        Target t = s->targets[i];
        mc3_u32 j = i;
        while (j && s->targets[j - 1u].pmd > t.pmd) { s->targets[j] = s->targets[j - 1u]; --j; }
        s->targets[j] = t;
    }
    for (mc3_u32 i = 0; i < nt; ++i) {
        if (i && s->targets[i - 1u].pmd == s->targets[i].pmd) { s->manifest_error = 7u; return 0; }
        ModelDraw *md = &s->models[s->targets[i].model_id];
        if (md->count >= MAX_MODEL_PMDS) { s->manifest_error = 8u; return 0; }
        md->pieces[md->count++] = i;
    }
    if (!nt || stream_seek(s->rsc_stream, s->rsc_cursor) < 0) { s->manifest_error = 9u; return 0; }
    s->target_count = nt;
    s->manifest_ready = 1u;
    mark('R','C','A','T', s->model_count, nt);
    return 1;
}

// ---- .lvl and .hood ------------------------------------------------------
// Float constants through a call: a literal would be a .rodata load, and
// the .mod packer refuses two of them sharing one `lui`.
static __attribute__((noinline)) float ffrom(mc3_u32 bits) {
    union { mc3_u32 u; float f; } v; v.u = bits; return v.f;
}
static float parse_float(const char **pp) {
    const char *p = *pp;
    while (*p == ' ' || *p == '\t') ++p;
    int neg = 0;
    if (*p == '-') { neg = 1; ++p; } else if (*p == '+') ++p;
    float v = ffrom(0u);
    while (*p >= '0' && *p <= '9') v = v * ffrom(0x41200000u) + (float)(*p++ - '0');
    if (*p == '.') {
        ++p;
        float f = ffrom(0x3DCCCCCDu);
        while (*p >= '0' && *p <= '9') { v += f * (float)(*p++ - '0'); f *= ffrom(0x3DCCCCCDu); }
    }
    if (*p == 'e' || *p == 'E') {
        ++p;
        int eneg = 0, e = 0;
        if (*p == '-') { eneg = 1; ++p; } else if (*p == '+') ++p;
        while (*p >= '0' && *p <= '9') e = e * 10 + (*p++ - '0');
        while (e-- > 0) v = eneg ? v * ffrom(0x3DCCCCCDu) : v * ffrom(0x41200000u);
    }
    *pp = p;
    return neg ? -v : v;
}
static int starts(const char *p, const char *w) {
    while (*w) if (*p++ != *w++) return 0;
    return 1;
}
static __attribute__((noinline)) const char *kw_instance() { static const char s[] = "instance_component"; return s; }
static __attribute__((noinline)) const char *kw_unique() { static const char s[] = "unique_component"; return s; }
static __attribute__((noinline)) const char *kw_type() { static const char s[] = "type:"; return s; }
static __attribute__((noinline)) const char *kw_ext() { static const char s[] = "extension:"; return s; }
static __attribute__((noinline)) const char *kw_emin() { static const char s[] = "emin"; return s; }
static __attribute__((noinline)) const char *kw_emax() { static const char s[] = "emax"; return s; }
static __attribute__((noinline)) const char *kw_name() { static const char s[] = "name:"; return s; }

static void hood_line(const char *p) {
    State *s = st();
    Hood *hd = &s->hood;
    while (*p == ' ' || *p == '\t') ++p;
    if (starts(p, kw_instance())) {
        hd->in_inst = 1; hd->rows = 0; hd->model = NO_IMAGE; hd->ext = 0;
        hd->have = 0;
        return;
    }
    if (starts(p, kw_unique())) { hd->in_inst = 0; return; }
    if (!hd->in_inst) return;
    if (*p == '}') {
        hd->in_inst = 0;
        if (hd->model == NO_IMAGE || hd->rows != 4 || hd->have != 3) { ++s->hood_bad; return; }
        if (s->instance_count >= MAX_INSTANCES) { ++s->hood_bad; return; }
        const mc3_u32 i = s->instance_count++;
        mc3_u32 *rec = (mc3_u32 *)(s->instances_bank + i * 20u);
        float c[3], d2 = ffrom(0u);
        for (int k = 0; k < 3; ++k) {
            c[k] = (hd->emin[k] + hd->emax[k]) * ffrom(0x3F000000u);
            const float e = hd->emax[k] - hd->emin[k];
            d2 += e * e;
        }
        // sqrt by Newton: radius = |emax - emin| / 2 + 0.03 (as the generator)
        float r = d2 > ffrom(0x3F800000u) ? d2 : ffrom(0x3F800000u);
        for (int it = 0; it < 24; ++it) r = ffrom(0x3F000000u) * (r + d2 / r);
        rec[0] = hd->model;
        rec[1] = fbits(c[0]); rec[2] = fbits(c[1]); rec[3] = fbits(c[2]);
        rec[4] = fbits(r * ffrom(0x3F000000u) + ffrom(0x3CF5C28Fu));
        mc3_u32 *mat = (mc3_u32 *)(s->instance_matrices + i * 64u);
        for (int row = 0; row < 4; ++row) {
            for (int k = 0; k < 3; ++k) mat[row * 4 + k] = fbits(hd->m[row][k]);
            mat[row * 4 + 3] = row == 3 ? 0x3F800000u : 0u;
        }
        s->inst_ext[i] = (mc3_u16)hd->ext;
        s->models[hd->model].flags = 1u;
        return;
    }
    if (starts(p, kw_type())) {
        p += 5;
        while (*p == ' ' || *p == '\t') ++p;
        hd->model = find_model(p, 128u);
        return;
    }
    if (starts(p, kw_ext())) {
        p += 10;
        hd->ext = (mc3_u32)parse_float(&p);
        return;
    }
    if (starts(p, kw_emin()) || starts(p, kw_emax())) {
        const int mx = p[2] == 'a';
        p += 4;
        float *dst = mx ? hd->emax : hd->emin;
        for (int k = 0; k < 3; ++k) dst[k] = parse_float(&p);
        hd->have |= mx ? 2 : 1;
        return;
    }
    if ((*p >= '0' && *p <= '9') || *p == '-' || *p == '.') {
        if (hd->rows < 4) {
            for (int k = 0; k < 3; ++k) hd->m[hd->rows][k] = parse_float(&p);
            ++hd->rows;
        }
    }
}

// Feed a stream through `line` a chunk at a time. Returns 1 at end of file,
// 0 to continue next frame, -1 on error.
static void lvl_line(const char *p);
static void prop_line(const char *p);
// `lvl`: 1 = the .lvl, 0 = a .hood (no function pointers in a .mod: taking a
// function's address is not relocated and the call jumped to 0).
static int hood_pump(int lvl) {
    State *s = st();
    Hood *hd = &s->hood;
    for (mc3_u32 chunk = 0; chunk < HOOD_CHUNKS_PER_FRAME; ++chunk) {
        mc3_u32 part = hd->size - hd->pos;
        if (part > HOOD_CHUNK) part = HOOD_CHUNK;
        if (part && !read_exact(hd->stream, hd->buf + hd->fill, part)) return -1;
        hd->pos += part;
        hd->fill += part;
        const int last = hd->pos >= hd->size;
        mc3_u32 start = 0;
        for (mc3_u32 i = 0; i < hd->fill; ++i) {
            if (hd->buf[i] == '\n' || (last && i + 1u == hd->fill)) {
                hd->buf[hd->buf[i] == '\n' ? i : i + 1u] = 0;
                if (lvl == 1) lvl_line((const char *)hd->buf + start);
                else if (lvl == 2) prop_line((const char *)hd->buf + start);
                else hood_line((const char *)hd->buf + start);
                start = i + 1u;
            }
        }
        mc3_u32 keep = hd->fill > start ? hd->fill - start : 0u;
        if (keep > 256u) keep = 0;             // no line is this long; drop it
        for (mc3_u32 i = 0; i < keep; ++i) hd->buf[i] = hd->buf[start + i];
        hd->fill = keep;
        if (last) return 1;
    }
    return 0;
}

static void lvl_line(const char *p) {
    State *s = st();
    while (*p == ' ' || *p == '\t') ++p;
    if (!starts(p, kw_name()) || s->hood_count >= MAX_HOODS) return;
    p += 5;
    while (*p == ' ' || *p == '\t') ++p;
    char *dst = s->hood_names[s->hood_count];
    mc3_u32 n = 0;
    while (p[n] && p[n] != ' ' && p[n] != '\t' && p[n] != '\r' && n < 31u) { dst[n] = p[n]; ++n; }
    dst[n] = 0;
    if (n) ++s->hood_count;
}

static int hood_open(const char *name, const char *suffix) {
    State *s = st();
    char path[128];
    int n = append(path, 0, city_folder());
    n = append(path, n, name);
    append(path, n, suffix);
    const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
    if (!h) return 0;
    Hood *hd = &s->hood;
    hd->stream = h;
    hd->size = (mc3_u32)MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
    hd->pos = 0; hd->fill = 0; hd->in_inst = 0;
    return 1;
}

// Placements from losangeles.lvl + every hood it names, a slice per frame.
static void step_hoods() {
    State *s = st();
    Hood *hd = &s->hood;
    if (s->hood_state == 0u) {
        const mc3_u32 need = MAX_INSTANCES * (20u + 64u) + 64u;
        mc3_u8 *a = heap_room(need) ? (mc3_u8 *)r_alloc(MAX_INSTANCES * 20u + 16u) : 0;
        mc3_u8 *b = a ? (mc3_u8 *)r_alloc(MAX_INSTANCES * 64u + 16u) : 0;
        mc3_u8 *c = b ? (mc3_u8 *)r_alloc(HOOD_CHUNK + 512u) : 0;
        if (!c) { s->instances_error = 3u; s->hood_state = 9u; return; }
        s->instances_bank_mem = (mc3_u32)a;
        s->instances_bank = ((mc3_u32)a + 15u) & ~15u;
        s->instance_matrices_mem = (mc3_u32)b;
        s->instance_matrices = ((mc3_u32)b + 15u) & ~15u;
        hd->buf_mem = (mc3_u32)c;
        hd->buf = (mc3_u8 *)(((mc3_u32)c + 15u) & ~15u);
        s->instance_count = 0; s->hood_count = 0; s->hood_bad = 0;
        if (!hood_open(lvl_name(), empty_suffix())) { s->instances_error = 1u; s->hood_state = 9u; return; }
        s->hood_state = 1u;
    }
    if (s->hood_state == 1u) {                          // the .lvl
        const int r = hood_pump(1);
        if (r == 0) return;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(hd->stream);
        hd->stream = 0;                          // closing twice jumps to 0
        if (r < 0 || !s->hood_count) { s->instances_error = 2u; s->hood_state = 9u; return; }
        s->hood_index = 0;
        s->hood_state = 2u;
    }
    while (s->hood_state == 2u) {                       // each .hood
        if (!hd->stream) {
            if (s->hood_index >= s->hood_count) { s->hood_state = 3u; break; }
            if (!hood_open(s->hood_names[s->hood_index], hood_suffix())) {
                s->instances_error = 4u; s->hood_state = 9u; return;
            }
        }
        const int r = hood_pump(0);
        if (r == 0) return;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(hd->stream);
        hd->stream = 0;
        if (r < 0) { s->instances_error = 5u; s->hood_state = 9u; return; }
        ++s->hood_index;
        return;                                          // one file per frame at most
    }
    if (s->hood_state == 3u) {
        r_free((void *)hd->buf_mem);
        hd->buf_mem = 0;
        for (mc3_u32 i = 0; i < s->target_count; ++i)
            s->targets[i].flags = s->models[s->targets[i].model_id].flags;
        // (model, extension) -> instance, for the CPVS names
        for (mc3_u32 i = 0; i < INST_HASH; ++i) s->inst_hash[i] = 0xFFFFu;
        for (mc3_u32 i = 0; i < s->instance_count; ++i) {
            const mc3_u32 m = word(s->instances_bank + i * 20u);
            mc3_u32 at = (m * 7919u + s->inst_ext[i] * 104729u) & (INST_HASH - 1u);
            while (s->inst_hash[at] != 0xFFFFu) at = (at + 1u) & (INST_HASH - 1u);
            s->inst_hash[at] = (mc3_u16)i;
        }
        s->instances_ready = 1u;
        s->hood_state = 4u;
        mark('R','H','O','O', s->instance_count, s->hood_bad);
    }
}

static mc3_u32 find_instance(mc3_u32 model, mc3_u32 ext) {
    State *s = st();
    mc3_u32 at = (model * 7919u + ext * 104729u) & (INST_HASH - 1u);
    for (mc3_u32 k = 0; k < INST_HASH; ++k, at = (at + 1u) & (INST_HASH - 1u)) {
        const mc3_u32 i = s->inst_hash[at];
        if (i == 0xFFFFu) return NO_IMAGE;
        if (word(s->instances_bank + i * 20u) == model && s->inst_ext[i] == ext) return i;
    }
    return NO_IMAGE;
}

// <tod>_cpvs.rnt: which LOD 0 `_main` PCP0 draws each placement, and the list
// of component PCP0s (their records are found later, from their refs).
// `_0_refl` chains too: despite the name they are not a second pass over the
// same geometry - they colour the OTHER near pieces of a model, the road deck
// and sidewalks (LA: 309 pieces, Paris: 343, never referenced by a `_main`).
// Without them that ground was drawn with the flat colour, a visible seam
// against the lit piece beside it. Read after every `_main` (second pass).
static int refl_tables() {
    State *s = st();
    if (s->inst_refl) return 1;
    const mc3_u32 bytes = MAX_INSTANCES * 2u + (MAX_PIECES + MAX_INSTANCES) * 2u +
                          MAX_CPVS_ENTRIES / 8u + 32u;
    mc3_u8 *mem = heap_room(bytes) ? (mc3_u8 *)r_alloc(bytes) : 0;
    if (!mem) return 0;
    const mc3_u32 base = ((mc3_u32)mem + 15u) & ~15u;
    s->refl_chain = (mc3_u32 *)base;
    s->inst_refl = (mc3_u16 *)(base + MAX_CPVS_ENTRIES / 8u);
    s->later = s->inst_refl + MAX_INSTANCES;
    return 1;
}
static int build_cpvs_names() {
    State *s = st();
    char path[96];
    int n = append(path, 0, cpvs_folder());
    n = append(path, n, cond_time());
    path[n++] = '_'; path[n] = 0;
    n = append(path, n, cond_weather());
    append(path, n, cpvs_rnt_suffix());
    mc3_u32 bytes, mem;
    const mc3_u32 rnt = read_whole(path, 0x200000u, &bytes, &mem);
    if (!rnt || word(rnt) != RNT0 || 0x18u + word(rnt + 4u) * 12u > bytes) {
        r_free((void *)mem);
        s->map_error = 1u; return 0;
    }
    const int refl_ok = refl_tables();       // without them: no `_0_refl` chains at all
    for (mc3_u32 i = 0; i < MAX_CPVS_ENTRIES / 32u; ++i) { s->cpvs_need[i] = 0u; if (refl_ok) s->refl_chain[i] = 0u; }
    for (mc3_u32 i = 0; i < MAX_INSTANCES; ++i) { s->instance_word[i] = 0u; if (refl_ok) s->inst_refl[i] = 0u; }
    for (mc3_u32 i = 0; i < MAX_PIECES; ++i) s->target_word[i] = 0u;
    s->comp_pcp_count = 0;
    mc3_u32 inst_hits = 0, refl_hits = 0;
    const mc3_u32 nrec = word(rnt + 4u);
    for (mc3_u32 pass = 0; pass < (refl_ok ? 2u : 1u); ++pass)
    for (mc3_u32 r = 0; r < nrec; ++r) {
        const mc3_u32 rec = rnt + 0x18u + r * 12u;
        if (word(rec + 4u) != PCP0 || word(rec) >= bytes) continue;
        const mc3_u32 index = (word(rec + 8u) & 0x3FFFu) - 1u;
        if (index >= MAX_CPVS_ENTRIES) continue;
        const char *nm = (const char *)(rnt + word(rec));
        mc3_u32 len = 0;
        while (nm[len]) ++len;
        // "..._0_main" (pass 0) or "..._0_refl" (pass 1)
        if (len < 8u || nm[len - 7u] != '_' || nm[len - 6u] != '0' || nm[len - 5u] != '_')
            continue;
        const mc3_u8 c0 = lower((mc3_u8)nm[len - 4u]), c1 = lower((mc3_u8)nm[len - 3u]),
                     c2 = lower((mc3_u8)nm[len - 2u]), c3 = lower((mc3_u8)nm[len - 1u]);
        const int is_main = c0 == 'm' && c1 == 'a' && c2 == 'i' && c3 == 'n';
        const int is_refl = c0 == 'r' && c1 == 'e' && c2 == 'f' && c3 == 'l';
        if (pass == 0u ? !is_main : !is_refl) continue;
        const mc3_u32 core = len - 7u;              // name without "_0_main"
        const mc3_u8 inst_prefix = (mc3_u8)mc2_city_instance_prefix(active_city());
        mc3_u32 inst_at = 0xFFFFFFFFu;
        for (mc3_u32 i = 0; i + 8u <= core; ++i)
            if (nm[i] == '_' && lower((mc3_u8)nm[i + 1u]) == inst_prefix && nm[i + 2u] == '_' &&
                lower((mc3_u8)nm[i + 3u]) == 'i' && lower((mc3_u8)nm[i + 4u]) == 'n' &&
                lower((mc3_u8)nm[i + 5u]) == 's' && lower((mc3_u8)nm[i + 6u]) == 't' &&
                nm[i + 7u] == '_') { inst_at = i + 1u; break; }
        if (inst_at != 0xFFFFFFFFu) {
            // <city>_inst_<type><extension>: extension is the trailing digits.
            mc3_u32 d = core;
            while (d > inst_at && nm[d - 1u] >= '0' && nm[d - 1u] <= '9') --d;
            if (d == core || d == inst_at) continue;
            mc3_u32 ext = 0;
            for (mc3_u32 i = d; i < core; ++i) ext = ext * 10u + (mc3_u32)(nm[i] - '0');
            const mc3_u32 model = find_model(nm + inst_at, d - inst_at);
            if (model == NO_IMAGE) continue;
            const mc3_u32 inst = find_instance(model, ext);
            if (inst == NO_IMAGE) continue;
            if (pass == 0u) { s->instance_word[inst] = index + 1u; ++inst_hits; }
            else { s->inst_refl[inst] = (mc3_u16)(index + 1u); ++refl_hits; }
            s->cpvs_need[index >> 5] |= 1u << (index & 31u);
            if (pass == 1u) s->refl_chain[index >> 5] |= 1u << (index & 31u);
        } else if (s->comp_pcp_count < MAX_COMP_PCP) {
            s->comp_pcp[s->comp_pcp_count++] = (mc3_u16)index;
            s->cpvs_need[index >> 5] |= 1u << (index & 31u);
            if (pass == 1u) { ++refl_hits; s->refl_chain[index >> 5] |= 1u << (index & 31u); }
        }
    }
    r_free((void *)mem);
    s->names_ready = 1u;
    mark('R','C','P','N', s->comp_pcp_count, (refl_hits << 16) | (inst_hits & 0xFFFFu));
    return 1;
}

// After the cpvs file is in: a component PCP0 is drawn by the lowest record
// it takes geometry from; every record it covers skips its own PMD0.
static int resolve_ref_target(mc3_u32 addr, mc3_u32 qwc);
// An instance's `_0_main` PCP0 does not always draw its whole model: in 100
// of LA's instanced types it takes only the first near PMD0, and the others -
// the ground of the model: freeway and road deck (8lanefwy_*, 4laneroad_*),
// warehouse yards, office parking - have no PCP0 at all; MC2 draws them
// straight from the PMD0. Drawing the PCP0 alone made that ground vanish
// (the background showed through) as soon as the CPVS was in. Bits 16..19 of
// instance_word = the model's pieces neither its `_main` nor its `_refl` PCP0
// covers, drawn flat after them; bits 20..23 = the pieces the `_refl` chain
// covers (drawn flat if that chain cannot be queued). An absent or unloaded
// chain is dropped here (low 16 bits / inst_refl cleared).
// Marker RCOI <instances with a PCP0> <pieces left out << 16 | with a _refl>.
static __attribute__((noinline)) mc3_u32 pcp_pieces(mc3_u32 index1, const ModelDraw *model) {
    const State *s = st();
    const mc3_u32 index = index1 - 1u;
    if (!index1 || index >= s->cpvs_count) return 0x80000000u;
    const mc3_u32 v = ((const mc3_u32 *)s->cpvs_table)[index];
    if (!(v & 0x80000000u)) return 0x80000000u;
    mc3_u32 used = 0;                            // bit j: model piece j referenced
    const mc3_u32 off = (v & 0x3FFFFu) << 4;
    const mc3_u32 end = off + (((v >> 18) & 0x1FFFu) << 4);
    for (mc3_u32 p = off; p + 16u <= end;) {
        const mc3_u32 at = s->cpvs + p;
        const mc3_u32 lo = word(at), id = (lo >> 28) & 7u, qwc = lo & 0xFFFFu;
        if (id == 3u) {
            const int t = (word(at) & REF_RESOLVED) ? -1 : resolve_ref_target(word(at + 4u), qwc);
            for (mc3_u32 j = 0; t >= 0 && j < model->count && j < 4u; ++j)
                if (model->pieces[j] == (mc3_u32)t) used |= 1u << j;
            p += 16u;
            continue;
        }
        p += 16u + qwc * 16u;
        if (id == 6u || id == 7u) break;
    }
    return used;
}
static void cover_instances() {
    State *s = st();
    mc3_u32 with_pcp = 0, partial = 0, with_refl = 0;
    for (mc3_u32 i = 0; i < s->instance_count; ++i) {
        const mc3_u32 w = s->instance_word[i];
        const mc3_u32 refl1 = s->inst_refl ? s->inst_refl[i] : 0u;
        if (!(w & 0xFFFFu) && !refl1) continue;
        const ModelDraw *model = &s->models[instance_record(s, i)[0]];
        mc3_u32 main_used = pcp_pieces(w & 0xFFFFu, model);
        mc3_u32 refl_used = pcp_pieces(refl1, model);
        mc3_u32 main_index = w & 0xFFFFu;
        if (main_used & 0x80000000u) { main_used = 0; main_index = 0; }
        if (refl_used & 0x80000000u) { refl_used = 0; if (refl1) s->inst_refl[i] = 0; }
        refl_used &= ~main_used;                 // never twice
        const int has_refl = s->inst_refl && s->inst_refl[i];
        if (!main_index && !has_refl) { s->instance_word[i] = 0u; continue; }
        mc3_u32 left = 0;
        for (mc3_u32 j = 0; j < model->count && j < 4u; ++j)
            if (!((main_used | refl_used) & (1u << j))) left |= 1u << (16u + j);
        s->instance_word[i] = main_index | left | (refl_used << 20);
        ++with_pcp;
        if (left) ++partial;
        if (has_refl) ++with_refl;
    }
    mark('R','C','O','I', with_pcp, (partial << 16) | (with_refl & 0xFFFFu));
}
static void cover_components() {
    State *s = st();
    mc3_u32 done = 0;
    for (mc3_u32 c = 0; c < s->comp_pcp_count; ++c) {
        const mc3_u32 index = s->comp_pcp[c];
        const mc3_u32 v = index < s->cpvs_count ? ((const mc3_u32 *)s->cpvs_table)[index] : 0u;
        if (!(v & 0x80000000u)) continue;
        const mc3_u32 off = (v & 0x3FFFFu) << 4;
        const mc3_u32 end = off + (((v >> 18) & 0x1FFFu) << 4);
        mc3_u32 covered[MAX_MODEL_PMDS * 4], nc = 0, owner = 0xFFFFFFFFu, p = off;
        int ok = 1;
        while (ok && p + 16u <= end) {
            const mc3_u32 at = s->cpvs + p;
            const mc3_u32 lo = word(at), id = (lo >> 28) & 7u, qwc = lo & 0xFFFFu;
            if (id == 3u) {
                const int t = (word(at) & REF_RESOLVED) ? -1 : resolve_ref_target(word(at + 4u), qwc);
                if (t < 0 || s->targets[t].flags) { ok = 0; break; }
                mc3_u32 seen = 0;
                for (mc3_u32 k = 0; k < nc; ++k) if (covered[k] == (mc3_u32)t) seen = 1;
                if (!seen && nc < MAX_MODEL_PMDS * 4) covered[nc++] = (mc3_u32)t;
                if ((mc3_u32)t < owner) owner = (mc3_u32)t;
                p += 16u;
                continue;
            }
            p += 16u + qwc * 16u;
            if (id == 6u || id == 7u) break;
        }
        if (!ok || owner == 0xFFFFFFFFu || (s->target_word[owner] & 0xFFFFu)) continue;
        for (mc3_u32 k = 0; k < nc; ++k) s->target_word[covered[k]] |= CPVS_COVERED;
        s->target_word[owner] |= index + 1u;
        ++done;
    }
    s->map_ready = 1u;
    mark('R','C','O','V', done, s->comp_pcp_count);
    cover_instances();
}

// ---------------------------------------------------------------------------
// MC2 props (lamp posts, trees, signs, benches...) straight from the MC2
// files, drawn by this renderer like the rest of the city:
//
//   city/losangeles.prop     prop_template <name> {...} and 5695 placements
//                            prop N { matrix { 4 rows } prop_template: <name> }
//   ambients_<time>.rsc/.rnt PRP0 <name>: +0 MOD0 handle LOD 0, +4 LOD 2,
//                            +0x08..0x10 sphere centre, +0x14 radius
//   MOD0                     +0 low 16 = materials = packets, +4 material
//                            offsets -> {texture handle, colour offset},
//                            +8 packet offsets -> {offset, qwc}; each packet
//                            is a ready VIF stream (positions, ST, vertex
//                            colours, MSCAL 0)
//
// In the ambients container "this container" is handle bit 15 (bit 14 in the
// city container); a handle with neither bit names the shared texture bank.
// Composite parts, animation and physics are not imported: the body (LOD 0)
// is drawn where MC2 places it.
// ---------------------------------------------------------------------------

static __attribute__((noinline)) const char *amb_prefix() {
    static const char s[] = "ambients_";
    return s;
}
static __attribute__((noinline)) const char *rnt_suffix() {
    static const char s[] = ".rnt";
    return s;
}
static __attribute__((noinline)) const char *prop_file() {
    return mc2_city_prop(active_city());
}
static __attribute__((noinline)) const char *kw_prop() { static const char s[] = "prop "; return s; }
static __attribute__((noinline)) const char *kw_template() { static const char s[] = "prop_template"; return s; }
static __attribute__((noinline)) const char *kw_matrix() { static const char s[] = "matrix"; return s; }
static __attribute__((noinline)) const char *kw_far() { static const char s[] = "Far:"; return s; }
static __attribute__((noinline)) const char *kw_fixed() { static const char s[] = "FixedObject:"; return s; }
static __attribute__((noinline)) const char *kw_gfx() { static const char s[] = "GfxOnly:"; return s; }
static __attribute__((noinline)) const char *kw_drivable() { static const char s[] = "Drivable:"; return s; }

// An ambients entry, read with Seek the first time it is asked for.
static mc3_u32 amb_entry(mc3_u32 index, mc3_u32 fourcc) {
    State *s = st();
    if (index >= s->amb_count || word(s->amb_table + index * 8u + 4u) != fourcc) return 0u;
    mc3_u32 *dest = (mc3_u32 *)s->amb_dest;
    if (dest[index]) return dest[index];
    const mc3_u32 off = word(s->amb_table + index * 8u);
    const mc3_u32 end = index + 1u < s->amb_count ? word(s->amb_table + (index + 1u) * 8u)
                                                  : s->amb_bytes;
    if (end <= off || end - off > 0x40000u || !heap_room(end - off + 32u)) return 0u;
    mc3_u8 *mem = (mc3_u8 *)r_alloc(end - off + 32u);
    if (!mem) return 0u;
    const mc3_u32 at = ((mc3_u32)mem + 15u) & ~15u;
    if (stream_seek(s->amb_stream, off) < 0 || !read_exact(s->amb_stream, (void *)at, end - off)) {
        r_free(mem);
        return 0u;
    }
    s->amb_loaded += end - off;
    dest[index] = at;
    return at;
}

static mc3_u32 prop_texture_slot(mc3_u32 handle) {
    State *s = st();
    if (!handle) return s->white_slot;
    if (!(handle & 0x8000u)) return texture_slot(handle);     // shared bank
    const mc3_u32 index = (handle & 0x3FFFu) - 1u;
    if (index >= s->amb_count) return NO_IMAGE;
    mc3_u16 *slot = (mc3_u16 *)s->slot_of_amb;
    if (slot[index] != 0xFFFEu) return slot[index];
    const mc3_u32 tex = amb_entry(index, TEX0);
    slot[index] = (mc3_u16)(tex ? make_slot(2u, tex) : NO_IMAGE);
    return slot[index];
}

// The template's PRP0 (by name in the ambients .rnt) and its LOD 0 MOD0.
// A MOD0 -> its packets and their texture slots. Returns the packet count.
static mc3_u32 load_mod(mc3_u32 handle, mc3_u32 *addr, mc3_u16 *qwc, mc3_u16 *slots) {
    State *s = st();
    if (!(handle & 0x8000u)) return 0u;
    const mc3_u32 m = amb_entry((handle & 0x3FFFu) - 1u, MOD0);
    if (!m) return 0u;
    const mc3_u32 n = word(m) & 0xFFFFu;
    if (!n || n > MAX_PROP_PACKETS || (word(m) >> 16) != 1u) return 0u;
    const mc3_u32 mat = m + word(m + 4u), pkt = m + word(m + 8u);
    for (mc3_u32 k = 0; k < n; ++k) {
        const mc3_u32 pd = m + word(pkt + k * 4u);
        const mc3_u32 off = word(pd), q = word(pd + 4u) & 0xFFFFu;
        if ((off & 15u) || !q) return 0u;
        addr[k] = m + off;
        qwc[k] = (mc3_u16)q;
        const mc3_u32 slot = prop_texture_slot(word(m + word(mat + k * 4u)));
        slots[k] = (mc3_u16)(slot == NO_IMAGE ? s->white_slot : slot);
    }
    return n;
}

// The template's PRP0 (by name in the ambients .rnt) and its MOD0s.

// MC2's physics tuning of a prop: host0:/mc2/tune/phys/<name>.phys (copied by
// the installer from assets/tune/phys). Only `mass:` is used - everything in
// LA's set is type BREAK; 1000 kg and up (parked car, containers) stays put.
static __attribute__((noinline)) const char *phys_dir() { static const char s[] = "host0:/mc2/tune/phys/"; return s; }
static __attribute__((noinline)) const char *phys_ext() { static const char s[] = ".phys"; return s; }
static __attribute__((noinline)) const char *kw_mass() { static const char s[] = "mass:"; return s; }
static mc3_u32 read_whole(const char *path, mc3_u32 max, mc3_u32 *bytes, mc3_u32 *mem);
static void load_prop_mass(PropType *t) {
    char path[96];
    mc3_u32 n = 0;
    for (const char *d = phys_dir(); *d && n < 60u; ++d) path[n++] = *d;
    for (mc3_u32 i = 0; t->name[i] && n < 88u; ++i) path[n++] = t->name[i];
    for (const char *e = phys_ext(); *e && n < 95u; ++e) path[n++] = *e;
    path[n] = 0;
    t->mass = ffrom(0x42480000u);                       // 50 kg
    mc3_u32 bytes = 0, mem = 0;
    const mc3_u32 base = read_whole(path, 4096u, &bytes, &mem);
    t->has_phys = base != 0u;
    if (base) {
        const char *q = (const char *)base;
        for (mc3_u32 i = 0; i + 5u < bytes; ++i)
            if (starts(q + i, kw_mass())) {
                const char *v = q + i + 5;
                t->mass = parse_float(&v);
                break;
            }
        r_free((void *)mem);
    }
    t->fixed = t->mass >= ffrom(0x447A0000u);          // 1000 kg
}
static __attribute__((noinline)) const char *kw_palm() { static const char s[] = "palm"; return s; }
static __attribute__((noinline)) const char *kw_tree() { static const char s[] = "tree"; return s; }
static int name_has(const char *name, const char *w) {
    for (mc3_u32 i = 0; i < 48u && name[i]; ++i) {
        mc3_u32 k = 0;
        while (w[k] && i + k < 48u && lower((mc3_u8)name[i + k]) == (mc3_u32)w[k]) ++k;
        if (!w[k]) return 1;
    }
    return 0;
}
static void load_prop_type(PropType *t) {
    State *s = st();
    t->ok = 0;
    const mc3_u32 rnt = s->amb_rnt, nrec = word(rnt + 4u);
    mc3_u32 prp = 0xFFFFFFFFu;
    for (mc3_u32 r = 0; r < nrec; ++r) {
        const mc3_u32 rec = rnt + 0x18u + r * 12u;
        if (word(rec + 4u) == PRP0 && word(rec) < s->amb_rnt_bytes &&
            name_equal((const char *)(rnt + word(rec)), t->name, 48u)) {
            prp = (word(rec + 8u) & 0x3FFFu) - 1u;
            break;
        }
    }
    const mc3_u32 p = prp != 0xFFFFFFFFu ? amb_entry(prp, PRP0) : 0u;
    if (!p) return;
    t->packets = load_mod(word(p), t->pk_addr, t->pk_qwc, t->pk_slot);
    if (!t->packets) return;
    t->packets2 = load_mod(word(p + 4u), t->pk2_addr, t->pk2_qwc, t->pk2_slot);
    t->cx = fword((const mc3_u32 *)(p + 8u));
    t->cy = fword((const mc3_u32 *)(p + 12u));
    t->cz = fword((const mc3_u32 *)(p + 16u));
    t->radius = fword((const mc3_u32 *)(p + 20u));
    // PRP0 +0x18: how many lights; the records (MC2's light_data, 0xC0 each)
    // follow from +0x20 - the entry is 32 + n * 192 bytes
    t->light_n = word(p + 0x18u);
    if (t->light_n > 8u) t->light_n = 0u;
    t->light_rec = p + 0x20u;
    t->light_data = 0u;
    load_prop_mass(t);
    t->trunk = name_has(t->name, kw_palm()) || name_has(t->name, kw_tree());
    t->ok = 1;
    ++s->prop_types_ok;
}

static mc3_u32 find_prop_type(const char *p) {
    State *s = st();
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i)
        if (name_equal(s->prop_types[i].name, p, 48u)) return i;
    return NO_IMAGE;
}

// ---------------------------------------------------------------------------
// MC2 particle parts -> MC3 prop particle rules. An MC2 template names its
// effects as parts, `name: <template>_particle_<effect>` (newspaper01,
// firehydrant_l, wood_splinters, steam_p...). MC3 has the same kind of
// effect on its own props, compiled into the props packs; mc3_prop_ptx.py
// writes detroit's as tune/effects/<rule>.ptx (their tiles index
// d_shared_particle, the atlas MC3 loads for cities 5 and 6). FX_* below is
// the rule list; fx_map() lines are `effect keyword,template keyword,rules`
// (rules as letters, 'A' = rule 0), first matching line wins.
// ---------------------------------------------------------------------------
enum { FX_HYDRANT = 13 };
static __attribute__((noinline)) const char *fx_rule_names() {
    static const char s[] =
        "d_prop_newstand_01x_particle_paper\0"                 // A newspapers
        "d_prop_trashcan_01x_particle_trash_d\0"               // B trash
        "d_prop_trashbag_01x_particle_trashbag\0"              // C
        "d_prop_trashbags_01x_particle_trashbag02\0"           // D
        "d_prop_dumpster_01x_particle_dumpster_d\0"            // E
        "d_prop_bench_01x_particle_benchsplinters\0"           // F
        "d_prop_pallets_01x_particle_splinters\0"              // G
        "d_prop_alpha_tree_04x_particle_splinters\0"           // H
        "d_prop_alpha_tree_04x_particle_dust\0"                // I
        "d_prop_alpha_tree_01x_particle_tree_dead\0"           // J leaves
        "d_prop_planter_02x_particle_bush_d\0"                 // K
        "d_prop_mailbox_01x_particle_mail_s\0"                 // L letters
        "d_prop_parkmeter_01x_particle_coins_d\0"              // M
        "d_prop_hydrant_01x_particle_hydrant_firehydrant02\0"  // N spray, 10 s
        "d_prop_steam_01x_particle_steam_t\0"                  // O always on
        "d_prop_fountain_01x_particle_fountainspray\0"         // P always on
        "d_prop_telpole_01x_particle_wood\0";                  // Q
    return s;
}
static __attribute__((noinline)) const char *fx_map() {
    static const char s[] =
        "newspaper,,A\n"
        "trashbag02,,D\n"
        "trashbags,,D\n"
        "trashbag,,C\n"
        "trash,dumpster,E\n"
        "trash,,B\n"
        "wood_splinters,bench,F\n"
        "wood_splinters,,G\n"
        "splinter_tree,,HI\n"
        "leaf_bush,,K\n"
        "leaf_tree,,J\n"
        "mail,,L\n"
        "letters,,L\n"
        "coin,,M\n"
        "firehydrant,,N\n"
        "steam,,O\n"
        "smokestack,,O\n"
        "cherubpee,,P\n"
        "fountain,,P\n"
        "sparks,telephone_pole,QI\n";
    return s;
}
static __attribute__((noinline)) const char *kw_particle() { static const char s[] = "_particle_"; return s; }
static const char *fx_rule_name(mc3_u32 k) {
    const char *p = fx_rule_names();
    while (k--) { while (*p) ++p; ++p; }
    return p;
}
static mc3_u32 word_len(const char *s, mc3_u32 max) {
    mc3_u32 n = 0;
    while (n < max && s[n] && s[n] != '\r' && s[n] != '\n' && s[n] != ' ' && s[n] != '\t') ++n;
    return n;
}
// s[0..n) contains w[0..wn) (case-insensitive; an empty w always matches)
static int contains(const char *s, mc3_u32 n, const char *w, mc3_u32 wn) {
    for (mc3_u32 i = 0; i + wn <= n; ++i) {
        mc3_u32 k = 0;
        while (k < wn && lower((mc3_u8)s[i + k]) == (mc3_u32)w[k]) ++k;
        if (k == wn) return 1;
    }
    return 0;
}
static void prop_part(PropType *t, const char *part) {
    const mc3_u32 n = word_len(part, 96u);
    const char *pk = kw_particle();
    mc3_u32 at = n;
    for (mc3_u32 i = 0; i + 10u <= n && at == n; ++i) {
        mc3_u32 k = 0;
        while (k < 10u && part[i + k] == pk[k]) ++k;
        if (k == 10u) at = i + 10u;
    }
    if (at >= n) return;
    const mc3_u32 tn = word_len(t->name, 48u);
    for (const char *m = fx_map(); *m; ) {
        const char *kw = m;
        while (*m != ',') ++m;
        const mc3_u32 kn = (mc3_u32)(m - kw);
        const char *tk = ++m;
        while (*m != ',') ++m;
        const mc3_u32 tkn = (mc3_u32)(m - tk);
        const char *rules = ++m;
        while (*m != '\n') ++m;
        const mc3_u32 rn = (mc3_u32)(m - rules);
        ++m;
        if (!contains(part + at, n - at, kw, kn) || !contains(t->name, tn, tk, tkn)) continue;
        for (mc3_u32 r = 0; r < rn; ++r) {
            const mc3_u8 idx = (mc3_u8)(rules[r] - 'A');
            int dup = 0;
            for (mc3_u32 j = 0; j < t->fx_count; ++j) dup |= t->fx[j] == idx;
            if (!dup && t->fx_count < 4u) t->fx[t->fx_count++] = idx;
        }
        return;
    }
}

// losangeles.prop, one line at a time (the .hood reader, mode 2).
static void prop_line(const char *p) {
    State *s = st();
    Hood *hd = &s->hood;
    while (*p == ' ' || *p == '\t') ++p;
    if (starts(p, kw_template())) {
        const char *q = p + 13;
        const int placement = *q == ':';
        if (placement) ++q;
        while (*q == ' ' || *q == '\t') ++q;
        if (!placement) {                                     // a template
            if (s->prop_type_count >= MAX_PROP_TYPES) return;
            PropType *t = &s->prop_types[s->prop_type_count++];
            mc3_u32 n = 0;
            while (q[n] && q[n] != ' ' && q[n] != '\t' && q[n] != '{' && q[n] != '\r' && n < 47u) {
                t->name[n] = q[n]; ++n;
            }
            t->name[n] = 0;
            t->far = 0;
            t->fixed_object = t->gfx_only = t->drivable = 0;
            t->fx_count = t->fx_always = 0;
            load_prop_type(t);
            return;
        }
        // placement: the matrix came first. A template without a model is
        // kept when it has particles (steam vents, fountains): nothing is
        // drawn for it, the particles are.
        const mc3_u32 type = find_prop_type(q);
        if (hd->rows != 4 || type == NO_IMAGE ||
            (!s->prop_types[type].ok && !s->prop_types[type].fx_count) ||
            s->prop_count >= MAX_PROPS) { ++s->prop_bad; hd->rows = 0; return; }
        const PropType *t = &s->prop_types[type];
        const mc3_u32 i = s->prop_count++;
        mc3_u32 *mat = (mc3_u32 *)(s->prop_matrices + i * 64u);
        for (int row = 0; row < 4; ++row) {
            for (int k = 0; k < 3; ++k) mat[row * 4 + k] = fbits(hd->m[row][k]);
            mat[row * 4 + 3] = row == 3 ? 0x3F800000u : 0u;
        }
        // world centre of the cull sphere: local centre * rotation + position
        float *rec = (float *)(s->prop_records + i * 20u);
        for (int k = 0; k < 3; ++k)
            rec[k] = t->cx * hd->m[0][k] + t->cy * hd->m[1][k] + t->cz * hd->m[2][k] + hd->m[3][k];
        rec[3] = t->radius;
        ((mc3_u32 *)rec)[4] = type;
        hd->rows = 0;
        return;
    }
    if (s->prop_type_count && !hd->in_inst) {
        PropType *last = &s->prop_types[s->prop_type_count - 1u];
        if (starts(p, kw_fixed())) { last->fixed_object = p[12] == ' ' ? p[13] == '1' : p[12] == '1'; return; }
        if (starts(p, kw_gfx())) { last->gfx_only = p[8] == ' ' ? p[9] == '1' : p[8] == '1'; return; }
        if (starts(p, kw_drivable())) { last->drivable = p[9] == ' ' ? p[10] == '1' : p[9] == '1'; return; }
        if (starts(p, kw_name())) {
            const char *q = p + 5;
            while (*q == ' ' || *q == '\t') ++q;
            prop_part(last, q);
            return;
        }
    }
    if (starts(p, kw_far()) && s->prop_type_count && !hd->in_inst) {
        const char *q = p + 4;
        s->prop_types[s->prop_type_count - 1u].far = parse_float(&q) > ffrom(0x3F000000u);
        return;
    }
    if (starts(p, kw_prop())) { hd->rows = 0; hd->in_inst = 1; return; }
    if (starts(p, kw_matrix())) { hd->rows = 0; return; }
    if (hd->in_inst && ((*p >= '0' && *p <= '9') || *p == '-' || *p == '.') && hd->rows < 4) {
        for (int k = 0; k < 3; ++k) hd->m[hd->rows][k] = parse_float(&p);
        ++hd->rows;
    }
}

static void step_props() {
    State *s = st();
    Hood *hd = &s->hood;
    if (s->prop_state == 0u) {
        char path[96];
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, amb_prefix());
        n = append(path, n, cond_time());
        const int stem = n;
        append(path, n, rnt_suffix());
        mc3_u32 bytes, mem;
        s->amb_rnt = read_whole(path, 0x40000u, &bytes, &mem);
        s->amb_rnt_bytes = bytes;
        if (!s->amb_rnt || word(s->amb_rnt) != RNT0) { s->prop_error = 1u; s->prop_state = 9u; return; }
        append(path, stem, rsc_suffix());
        const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
        if (!h) { s->prop_error = 2u; s->prop_state = 9u; return; }
        mc3_u8 head[16] __attribute__((aligned(16)));
        const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
        const mc3_u32 count = read_exact(h, head, 8u) && le32(head) == RSC0 ? le32(head + 4u) : 0u;
        const mc3_u32 need = count * 8u + count * 4u + count * 2u + MAX_PROPS * 84u + 256u;
        mc3_u8 *tab = count && count <= MAX_AMB_ENTRIES && heap_room(need)
            ? (mc3_u8 *)r_alloc(count * 14u + 64u) : 0;
        mc3_u8 *recs = tab ? (mc3_u8 *)r_alloc(MAX_PROPS * 20u + 16u) : 0;
        mc3_u8 *mats = recs ? (mc3_u8 *)r_alloc(MAX_PROPS * 64u + 16u) : 0;
        mc3_u8 *buf = mats ? (mc3_u8 *)r_alloc(HOOD_CHUNK + 512u) : 0;
        if (!buf) { MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h); s->prop_error = 3u; s->prop_state = 9u; return; }
        const mc3_u32 tb = ((mc3_u32)tab + 15u) & ~15u;
        if (!read_exact(h, (void *)tb, count * 8u)) {
            MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h); s->prop_error = 4u; s->prop_state = 9u; return;
        }
        s->amb_stream = h;
        s->amb_count = count;
        s->amb_bytes = (mc3_u32)size;
        s->amb_table = tb;
        s->amb_dest = (tb + count * 8u + 15u) & ~15u;
        s->slot_of_amb = (s->amb_dest + count * 4u + 15u) & ~15u;
        for (mc3_u32 i = 0; i < count; ++i) {
            ((mc3_u32 *)s->amb_dest)[i] = 0u;
            ((mc3_u16 *)s->slot_of_amb)[i] = 0xFFFEu;           // not looked up yet
        }
        s->prop_records = ((mc3_u32)recs + 15u) & ~15u;
        s->prop_matrices = ((mc3_u32)mats + 15u) & ~15u;
        hd->buf_mem = (mc3_u32)buf;
        hd->buf = (mc3_u8 *)(((mc3_u32)buf + 15u) & ~15u);
        hd->stream = 0;
        if (!hood_open(prop_file(), empty_suffix())) {
            s->prop_error = 5u; s->prop_state = 9u; return;
        }
        s->prop_state = 1u;
        return;
    }
    if (s->prop_state == 1u) {
        const int r = hood_pump(2);
        if (r == 0) return;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(hd->stream);
        hd->stream = 0;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->amb_stream);
        s->amb_stream = 0;
        r_free((void *)hd->buf_mem);
        hd->buf_mem = 0;
        if (r < 0) { s->prop_error = 6u; s->prop_state = 9u; return; }
        s->props_ready = 1u;
        s->prop_state = 2u;
        mark('R','P','R','P', (s->prop_types_ok << 16) | s->prop_type_count,
             (s->prop_bad << 16) | s->prop_count);
        mark('R','P','R','B', s->amb_loaded, mc3_heap_free(mc3_heap_active()));
    }
}

// Queue one placed prop: its matrix, then per packet the texture and the
// MOD0's own VIF stream. Skipped whole when its uploads do not fit.

// ---------------------------------------------------------------------------
// Car against MC2 props. The props are ours (the MC3 prop manager never heard
// of them), so the knock is ours too: the player's position and velocity come
// from mcPlayer::GetPlayer(0) 0x514530, mcRacer::GetPosition 0x51B810 and
// mcRacer::ObstacleVelocity 0x51B850 (the chain digital_speedometer reads).
// A prop within reach of a car moving faster than 1.5 m/s takes the velocity
// of an elastic hit (2 m_car / (m_car + m_prop), damped), tips over its base
// towards the hit, falls, lands at its own base height and slides to rest.
// The car loses a share of its speed by the prop's mass. Props >= 1000 kg do
// not move. Marker RPHT <prop> <props hit so far>.
// ---------------------------------------------------------------------------
enum { PLAYER_GET = 0x00514530, RACER_POSITION = 0x0051B810, RACER_VELOCITY = 0x0051B850 };
static inline float fsqrt(float x) { float r; __asm__("sqrt.s %0, %1" : "=f"(r) : "f"(x)); return r; }
// sin/cos for 0..pi/2 (Taylor, error < 1e-4 there)
static void sincos_q(float a, float *sn, float *cs) {
    const float a2 = a * a;
    *sn = a * (ffrom(0x3F800000u) - a2 * (ffrom(0x3E2AAAABu) - a2 * (ffrom(0x3C088889u) - a2 * ffrom(0x39500D01u))));
    *cs = ffrom(0x3F800000u) - a2 * (ffrom(0x3F000000u) - a2 * (ffrom(0x3D2AAAABu) - a2 * (ffrom(0x3AB60B61u) - a2 * ffrom(0x37D00D01u))));
}
// v rotated by `angle` about the unit axis k (Rodrigues)
static void rotate_vec(const float *v, const float *k, float sn, float cs, float *out) {
    const float kx = k[0], ky = k[1], kz = k[2];
    const float cx = ky * v[2] - kz * v[1], cy = kz * v[0] - kx * v[2], cz = kx * v[1] - ky * v[0];
    const float d = (kx * v[0] + ky * v[1] + kz * v[2]) * (ffrom(0x3F800000u) - cs);
    out[0] = v[0] * cs + cx * sn + kx * d;
    out[1] = v[1] * cs + cy * sn + ky * d;
    out[2] = v[2] * cs + cz * sn + kz * d;
}
static int player_motion(float **pos, float **vel) {
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    if (!ptr(player) || !ptr(word(player + 20u)) || !ptr(word(word(player + 20u) + 4u))) return 0;
    const mc3_u32 p = MC3_CALL1(mc3_u32, RACER_POSITION, mc3_u32)(player);
    const mc3_u32 v = MC3_CALL1(mc3_u32, RACER_VELOCITY, mc3_u32)(player);
    if (!ptr(p) || !ptr(v)) return 0;
    // GetPosition answers the car instance's Matrix34 (instance +0x10, the
    // matrix AlgumCorrupto's FlyHack reads); the translation is its 4th row
    *pos = (float *)(p + 36u); *vel = (float *)v;
    return 1;
}
// The velocity player_motion hands out is phInertialCS+64 (ObstacleVelocity =
// *(*(sim+104)+12) + 64); the body keeps its momentum and derives that field
// from it every step, so writing it changes nothing - the car went straight
// through palms and trees (player's pins, 2026-09-27). phInertialCS::
// SetVelocity (0x57C108) sets both.
enum { INERTIAL_SET_VELOCITY_PROPS = 0x0057C108 };
static void apply_car_velocity(float *car_vel) {
    float v[4];
    v[0] = car_vel[0]; v[1] = car_vel[1]; v[2] = car_vel[2]; v[3] = ffrom(0u);
    MC3_CALL2(void, INERTIAL_SET_VELOCITY_PROPS, mc3_u32, mc3_u32)((mc3_u32)car_vel - 64u, (mc3_u32)v);
}
// Out of a trunk: with the throttle down the engine gives back ~2 m/s every
// frame, so a velocity change alone let the car creep 10 cm per frame through
// the trunk. When it is inside, the car is moved back to the edge the way the
// AI respawn moves cars - aiBrain::TeleportCar (0x3FC600) with a fake brain
// (brain+12 -> opponent, opponent+36 -> car): Reposition, phLevel update and
// the velocity, all at once.
static void push_car_out(float nx, float nz, const float *vel) {
    State *s = st();
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    if (!ptr(player) || !ptr(word(player + 20u))) return;
    const mc3_u32 car = word(player + 20u);
    if (!ptr(word(car + 0x18u))) return;
    const float *cm = (const float *)(word(car + 0x18u) + 0x10u);
    float m[12], v[4];
    for (int k = 0; k < 12; ++k) m[k] = cm[k];
    m[9] = nx; m[11] = nz;
    v[0] = vel[0]; v[1] = vel[1]; v[2] = vel[2]; v[3] = ffrom(0u);
    s->push_opp[9] = car;
    s->push_brain[3] = (mc3_u32)&s->push_opp[0];
    MC3_CALL3(void, 0x003FC600, mc3_u32, mc3_u32, mc3_u32)
        ((mc3_u32)&s->push_brain[0], (mc3_u32)m, (mc3_u32)v);
}
// ---------------------------------------------------------------------------
// MC3's prop particles on the MC2 props: the rules the retail props use
// (fx_rule_names), thrown by the game's own prop particle system,
// mcPropManager::s_pSwPtx - a swPropPtxSystem of 256 particles that mcSwPtx
// updates and draws with every other effect, in every city (mcLayerCity::Load
// creates it). What mcPropFixed::EmitParticles 0x390B38 does per rule:
// count = AdjustPropEmissionCount(m_emitRate), position = m_position through
// the prop's matrix, BlastTransformed(system, position, count, matrix, rule).
//
// A rule is an mcPropParticleBirthRule (0x190 bytes): built here in our own
// memory (ctor 0x38FFC8, name at +4, parFileIO::Load 0x55F3D8 reads
// tune/effects/<name>.ptx) rather than through LoadWithHash, whose global
// hash would keep pointers into this city's heap after we leave. Every
// particle points at its rule, so release_all resets the system (all
// particles dead) before the rules go. Texture: SetTexture(rule, the
// system's atlas, 8, 8), like mcPropType::LoadTypeData.
//
// Hits: a movable prop (hit_prop) or a solid one (pole, tree trunk; once per
// prop until another is hit or 1.5 s pass) throws its rules that are not
// always on, when the impact (speed x 1500 kg) reaches m_minForceToSpawn.
// m_sprayAfterBroken (hydrant) keeps spraying m_spewTimeLimit seconds where
// it stood. m_alwaysOn (steam vents, fountains) emit every frame within
// 70 m of the camera, at most FX_AMBIENT_PER_FRAME of them.
// [boot] mc2f = 0 turns all of it off (mc2x/y/z together place the camera).
// Markers RPFX (rules), RPFB (blasts).
// ---------------------------------------------------------------------------
enum {
    PROP_PTX_SYSTEM = 0x00617F6C,        // mcPropManager::s_pSwPtx
    PROP_PTX_TEXTURE = 0x00617F70,       // mcPropManager::s_pPropParticleTex
    BIRTH_RULE_CTOR = 0x0038FFC8,
    PARFILEIO_LOAD = 0x0055F3D8,
    BIRTH_SET_TEXTURE = 0x001EFB08,
    PTX_BLAST_TRANSFORMED = 0x001F42D8,
    PTX_RESET = 0x001F4538,
    FX_ASSET_MANAGER = 0x006D557C,
    BIRTH_RULE_BYTES = 0x190,
    RULE_LIFE = 80, RULE_SPRAY = 0x161, RULE_MIN_FORCE = 0x168, RULE_ALWAYS = 0x170,
    RULE_POSITION = 0x178, RULE_SPEW = 0x184, RULE_EMIT = 0x188,
    RULE_COLORS = 176, RULE_AMBIENT = 0x163, RULE_EMISSIVE = 0x16C,
    MC_CITY = 0x00615B40, MC_CITY_AMBIENT = 0xE0,
    PTX_HYDRANT_FLAG = 52,               // copied into each particle (+49)
    FX_AMBIENT_PER_FRAME = 6
};
static int fx_enabled() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','f'));
    return !value || value[0] != '0';
}
static __attribute__((noinline)) const char *assets_root() { static const char s[] = "$/"; return s; }
static mc3_u8 byte_at(mc3_u32 p) { return *(volatile mc3_u8 *)p; }
// mcPropType::AdjustPropEmissionCount without its divide trap: counts of 3
// and up are divided by the race's particle divisor (race config +80).
static mc3_u32 fx_count(mc3_u32 rule) {
    const int n = (int)fword((const mc3_u32 *)(rule + RULE_EMIT));
    if (n < 3) return n > 0 ? (mc3_u32)n : 1u;
    const mc3_u32 cfg = word(RACE_CONFIG_CURRENT);
    int div = ptr(cfg) ? (int)word(cfg + 80u) : 1;
    if (div <= 0) div = 1;
    return n / div > 0 ? (mc3_u32)(n / div) : 1u;
}
static void fx_emit(mc3_u32 rule, const float *m, mc3_u32 count, int hydrant) {
    const mc3_u32 sys = word(PROP_PTX_SYSTEM);
    if (!ptr(sys) || !rule || !count) return;
    float mat[12] __attribute__((aligned(16)));
    float pos[4] __attribute__((aligned(16)));
    for (int r = 0; r < 4; ++r)
        for (int k = 0; k < 3; ++k) mat[r * 3 + k] = m[r * 4 + k];
    const float *lp = (const float *)(rule + RULE_POSITION);
    for (int k = 0; k < 3; ++k) pos[k] = lp[0] * m[k] + lp[1] * m[4 + k] + lp[2] * m[8 + k] + m[12 + k];
    pos[3] = ffrom(0u);
    // Always written: the system is built with this byte 0xCD (debug fill)
    // and only EmitParticles ever clears it. Set, the particles stop on any
    // car within 3 m (Update 0x1F3850) - newspapers thrown by the player's
    // own hit stuck under the car, invisible.
    *(volatile mc3_u8 *)(sys + PTX_HYDRANT_FLAG) = hydrant ? 1u : 0u;
    // m_bRecieveAmbientLighting: the birth/death colours times the city's
    // ambient (mcCity +0xE0), lifted towards white by m_emissive, only for
    // this blast - as EmitParticles does (its nearest-light term is left out:
    // the MC2 city's lights are not mcLights). Without it newspapers glow
    // white at midnight.
    float *col = (float *)(rule + RULE_COLORS);
    float keep[8];
    const mc3_u32 city = word(MC_CITY);
    const int lit = byte_at(rule + RULE_AMBIENT) && ptr(city);
    if (lit) {
        const float e = fword((const mc3_u32 *)(rule + RULE_EMISSIVE));
        for (int k = 0; k < 8; ++k) keep[k] = col[k];
        for (int k = 0; k < 3; ++k) {
            float a = fword((const mc3_u32 *)(city + MC_CITY_AMBIENT + 4u * k));
            if (a > ffrom(0x3F800000u)) a = ffrom(0x3F800000u);
            if (a < ffrom(0u)) a = ffrom(0u);
            a += (ffrom(0x3F800000u) - a) * e;
            col[k] *= a; col[4 + k] *= a;
        }
    }
    MC3_CALL5(void, PTX_BLAST_TRANSFORMED, mc3_u32, mc3_u32, int, mc3_u32, mc3_u32)
        (sys, (mc3_u32)pos, (int)count, (mc3_u32)mat, rule);
    if (lit) for (int k = 0; k < 8; ++k) col[k] = keep[k];
    *(volatile mc3_u8 *)(sys + PTX_HYDRANT_FLAG) = 0u;
    ++st()->fx_blasts;
}
// The rules the city's props use, once the props are read.
static void fx_load_rules() {
    State *s = st();
    const mc3_u32 sys = word(PROP_PTX_SYSTEM), tex = word(PROP_PTX_TEXTURE);
    if (!ptr(sys) || !ptr(tex)) return;                 // not yet: try next frame
    s->fx_state = 9u;                                   // one attempt, whatever happens
    mc3_u32 used = 0, n = 0;
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i)
        for (mc3_u32 j = 0; j < s->prop_types[i].fx_count; ++j) used |= 1u << s->prop_types[i].fx[j];
    for (mc3_u32 k = 0; k < FX_RULE_COUNT; ++k) n += (used >> k) & 1u;
    if (!n || !heap_room(n * BIRTH_RULE_BYTES + 64u)) { mark('R','P','F','X', used, 0xFFFFFFFFu); return; }
    mc3_u8 *mem = (mc3_u8 *)r_alloc(n * BIRTH_RULE_BYTES + 32u);
    if (!mem) return;
    s->fx_mem = (mc3_u32)mem;
    mc3_u32 at = ((mc3_u32)mem + 15u) & ~15u, loaded = 0;
    const mc3_u32 assets = word(FX_ASSET_MANAGER);
    const mc3_u32 vt = ptr(assets) ? word(assets) : 0u;
    if (vt) MC3_CALL2(void, word(vt + 0x10u), mc3_u32, const char *)(assets, assets_root());
    for (mc3_u32 k = 0; k < FX_RULE_COUNT; ++k) {
        if (!((used >> k) & 1u)) continue;
        for (mc3_u32 b = 0; b < BIRTH_RULE_BYTES; b += 4u) *(volatile mc3_u32 *)(at + b) = 0u;
        MC3_CALL1(void, BIRTH_RULE_CTOR, mc3_u32)(at);
        *(volatile mc3_u32 *)(at + 4u) = (mc3_u32)fx_rule_name(k);
        MC3_CALL1(int, PARFILEIO_LOAD, mc3_u32)(at);
        // a missing file leaves the defaults (life 0): no such rule
        if (fword((const mc3_u32 *)(at + RULE_LIFE)) > ffrom(0u)) {
            MC3_CALL4(void, BIRTH_SET_TEXTURE, mc3_u32, mc3_u32, int, int)(at, tex, 8, 8);
            s->fx_rules[k] = at;
            loaded |= 1u << k;
        }
        at += BIRTH_RULE_BYTES;
    }
    if (vt) MC3_CALL1(void, word(vt + 0x14u), mc3_u32)(assets);
    s->fx_sys = sys;
    s->fx_tex = tex;
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i) {
        PropType *t = &s->prop_types[i];
        for (mc3_u32 j = 0; j < t->fx_count; ++j) {
            const mc3_u32 r = s->fx_rules[t->fx[j]];
            if (r && byte_at(r + RULE_ALWAYS)) t->fx_always = 1u;
        }
    }
    for (mc3_u32 i = 0; i < s->prop_count && s->fx_ambient_count < MAX_FX_AMBIENT; ++i)
        if (s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]].fx_always)
            s->fx_ambient[s->fx_ambient_count++] = (mc3_u16)i;
    mark('R','P','F','X', used, loaded);
    mark('R','P','F','2', s->fx_ambient_count, tex);
}
static void fx_on_hit(mc3_u32 i, const float *m, float speed) {
    State *s = st();
    if (s->fx_state != 9u || !fx_enabled()) return;
    const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
    const float force = speed * ffrom(0x44BB8000u);            // x 1500 kg
    for (mc3_u32 j = 0; j < t->fx_count; ++j) {
        const mc3_u32 rule = s->fx_rules[t->fx[j]];
        if (!rule || byte_at(rule + RULE_ALWAYS)) continue;
        if (force < fword((const mc3_u32 *)(rule + RULE_MIN_FORCE))) continue;
        if (byte_at(rule + RULE_SPRAY)) {
            for (mc3_u32 k = 0; k < MAX_SPOUTS; ++k) {
                FxSpout *sp = &s->spouts[k];
                if (sp->left > ffrom(0u)) continue;
                sp->rule = rule;
                sp->left = fword((const mc3_u32 *)(rule + RULE_SPEW));
                for (int e = 0; e < 16; ++e) sp->m[e] = m[e];
                break;
            }
            continue;
        }
        fx_emit(rule, m, fx_count(rule), t->fx[j] == FX_HYDRANT);
    }
    mark('R','P','F','B', i | ((mc3_u32)t->fx_count << 24), s->fx_blasts);
}
static void step_prop_fx() {
    State *s = st();
    if (s->fx_state == 0u) { fx_load_rules(); return; }
    if (s->fx_state != 9u || !s->fx_mem || !fx_enabled()) return;
    ++s->fx_frame;
    // the city was reloaded under us (restart): same rules, the new atlas
    const mc3_u32 sys = word(PROP_PTX_SYSTEM), tex = word(PROP_PTX_TEXTURE);
    if (!ptr(sys) || !ptr(tex)) return;
    if (sys != s->fx_sys || tex != s->fx_tex) {
        for (mc3_u32 k = 0; k < FX_RULE_COUNT; ++k)
            if (s->fx_rules[k]) MC3_CALL4(void, BIRTH_SET_TEXTURE, mc3_u32, mc3_u32, int, int)(s->fx_rules[k], tex, 8, 8);
        for (mc3_u32 k = 0; k < MAX_SPOUTS; ++k) s->spouts[k].left = ffrom(0u);
        s->fx_sys = sys; s->fx_tex = tex;
    }
    if (!s->camera) return;
    const float cam_x = fword((const mc3_u32 *)(s->camera + 36u));
    const float cam_y = fword((const mc3_u32 *)(s->camera + 40u));
    const float cam_z = fword((const mc3_u32 *)(s->camera + 44u));
    const float near2 = ffrom(0x45992000u);                   // 70^2
    const float dt = ffrom(0x3D088889u);
    for (mc3_u32 k = 0; k < MAX_SPOUTS; ++k) {
        FxSpout *sp = &s->spouts[k];
        if (sp->left <= ffrom(0u)) continue;
        sp->left -= dt;
        const float dx = sp->m[12] - cam_x, dy = sp->m[13] - cam_y, dz = sp->m[14] - cam_z;
        if (dx * dx + dy * dy + dz * dz > near2 * ffrom(0x40000000u)) continue;
        fx_emit(sp->rule, sp->m, fx_count(sp->rule), 1);
    }
    mc3_u32 done = 0;
    for (mc3_u32 a = 0; a < s->fx_ambient_count && done < FX_AMBIENT_PER_FRAME; ++a) {
        const mc3_u32 i = s->fx_ambient[a];
        const float *m = (const float *)(s->prop_matrices + i * 64u);
        const float dx = m[12] - cam_x, dy = m[13] - cam_y, dz = m[14] - cam_z;
        if (dx * dx + dy * dy + dz * dz > near2) continue;
        const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
        for (mc3_u32 j = 0; j < t->fx_count; ++j) {
            const mc3_u32 rule = s->fx_rules[t->fx[j]];
            if (rule && byte_at(rule + RULE_ALWAYS)) fx_emit(rule, m, fx_count(rule), 0);
        }
        ++done;
    }
}
// ---------------------------------------------------------------------------
// MC2's prop lights as MC3 lights. An MC2 prop's lights are compiled into its
// PRP0 (ambients_<time>.rsc): +0x18 count, then 0xC0-byte records from +0x20
// holding exactly MC2's tune/lightdata/<prop>_<n>.light_data - checked field
// by field on l_prop_streetlight_01x:
//   +0x0C type  +0x10 position  +0x20 color  +0x30 intensity  +0x34 decay_rate
//   +0x38 direction  +0x44 spot_angle  +0x48 spot_dropoff  +0x4C draw_glow
//   +0x50 glow_offset  +0x60 glow_color  +0x70 draw_cone  +0x74 cone_size
//   +0x78 cone_intensity  +0x7C cone_offset  +0x80 cone_color  +0x90 draw_flare
//   +0x94 flare_offset  +0xA0 flare_color  +0xB0 reflection_color
// MC3's mcLightData (176 bytes, parFileIO, FileIO 0x595B18) has the same
// fields by the same names - it is the same class a version later - so each
// record becomes one: ctor 0x5958E8, then the fields at MC3's offsets, local
// position +0x18 and direction +0x24 as mcLightData::Init would put them.
// Each placed prop then gets MC3's mcLight (36 bytes, 48 here): the init
// 0x259988(light, Matrix34, data) that mcPropLightData::CreateLights uses
// (world position/direction from the prop's matrix, flags 5 = on), and
// mcLightManager::AddLightToGrid, so the grid MC3 lights cars and particles
// with (GetClosestLights) has them. Every frame, the lights within 160 m of
// the camera go through what mcPropType::Render does with a prop's lights -
// 0x259FF0 (glow, cone, flare into mcGlow's buffers, pass 512) and 0x25A1F8
// (the reflection on the road, pass 4; $f12 = the prop's ground height) -
// both only buffer, mcGlow draws them in its passes. A knocked-down lamp
// goes dark (flag 4 off). [boot] mc2l = 0: no lights. Markers RLGT
// <templates' lights << 16 | placed> <manager>, RLGD <drawn this frame>.
// ---------------------------------------------------------------------------
enum {
    LIGHT_MANAGER = 0x0070FA4C,
    LIGHTDATA_CTOR = 0x005958E8,
    LIGHT_INIT = 0x00259988,
    LIGHT_ADD = 0x00259658,
    LIGHT_REMOVE = 0x00259610,
    LIGHT_GLOW = 0x00259FF0,
    LIGHT_REFLECT = 0x0025A1F8,
    LIGHTDATA_BYTES = 176, LIGHT_BYTES = 48, MAX_LIGHTS_PLACED = 6144, LIGHTS_PER_FRAME = 48
};
static int lights_enabled() {
    const char *value = mc3_bootarg(MC3_ID('m','c','2','l'));
    return !value || value[0] != '0';
}
static void put_u32(mc3_u32 at, mc3_u32 v) { *(volatile mc3_u32 *)at = v; }
// 0x25A1F8(light) takes the ground height in $f12 (the game's EABI: floats
// in $f12 on). This toolchain would put a float second argument in $f13, so
// the call is made by hand.
static void light_reflect(mc3_u32 light, float ground) {
    register mc3_u32 a0 __asm__("$4") = light;
    register float f12 __asm__("$f12") = ground;
    __asm__ volatile(
        "lui $25, 0x0025\n\t"
        "ori $25, $25, 0xA1F8\n\t"
        "jalr $25\n\t"
        "nop"
        : "+r"(a0), "+f"(f12)
        :
        : "$2", "$3", "$5", "$6", "$7", "$8", "$9", "$10", "$11", "$12", "$13", "$14",
          "$15", "$24", "$25", "$31", "$f0", "$f1", "$f2", "$f3", "$f4", "$f5", "$f6",
          "$f7", "$f8", "$f9", "$f10", "$f11", "$f13", "$f14", "$f15", "$f16", "$f17",
          "$f18", "$f19", "memory");
}
static void copy_words(mc3_u32 to, mc3_u32 from, mc3_u32 n) {
    for (mc3_u32 k = 0; k < n; ++k) put_u32(to + 4u * k, word(from + 4u * k));
}
static void light_data_from_mc2(mc3_u32 d, mc3_u32 r, const char *name) {
    for (mc3_u32 b = 0; b < LIGHTDATA_BYTES; b += 4u) put_u32(d + b, 0u);
    MC3_CALL1(void, LIGHTDATA_CTOR, mc3_u32)(d);
    put_u32(d + 0x04u, (mc3_u32)name);
    put_u32(d + 0x0Cu, word(r + 0x0Cu));                     // type
    put_u32(d + 0x10u, word(r + 0x30u));                     // intensity
    copy_words(d + 0x18u, r + 0x10u, 3u);                    // position (local)
    copy_words(d + 0x24u, r + 0x38u, 3u);                    // direction (local)
    copy_words(d + 0x30u, r + 0x20u, 3u);                    // color
    put_u32(d + 0x3Cu, 0x3F800000u);
    copy_words(d + 0x40u, r + 0x60u, 4u);                    // glow_color
    copy_words(d + 0x50u, r + 0x80u, 4u);                    // cone_color
    copy_words(d + 0x60u, r + 0xA0u, 4u);                    // flare_color
    copy_words(d + 0x70u, r + 0xB0u, 4u);                    // reflection_color
    *(volatile mc3_u8 *)(d + 0x80u) = 0u;                    // not flashing, not directional
    *(volatile mc3_u8 *)(d + 0x81u) = word(r + 0x4Cu) != 0u; // draw_glow
    *(volatile mc3_u8 *)(d + 0x82u) = word(r + 0x70u) != 0u; // draw_cone
    *(volatile mc3_u8 *)(d + 0x83u) = word(r + 0x90u) != 0u; // draw_flare
    put_u32(d + 0x84u, word(r + 0x34u));                     // decay_rate
    put_u32(d + 0x88u, word(r + 0x44u));                     // spot_angle
    put_u32(d + 0x8Cu, word(r + 0x48u));                     // spot_dropoff
    put_u32(d + 0x90u, word(r + 0x50u));                     // glow_offset
    put_u32(d + 0x94u, word(r + 0x74u));                     // cone_size
    put_u32(d + 0x98u, word(r + 0x78u));                     // cone_intensity
    put_u32(d + 0x9Cu, word(r + 0x7Cu));                     // cone_offset
    put_u32(d + 0xA0u, word(r + 0x94u));                     // flare_offset
}
static void lights_init() {
    State *s = st();
    const mc3_u32 mgr = word(LIGHT_MANAGER);
    if (!ptr(mgr) || !ptr(word(mgr))) return;                // not yet: next frame
    s->light_state = 9u;
    mc3_u32 datas = 0, placed = 0;
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i)
        if (s->prop_types[i].ok) datas += s->prop_types[i].light_n;
    for (mc3_u32 i = 0; i < s->prop_count; ++i) {
        const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
        if (t->ok) placed += t->light_n;
    }
    if (placed > MAX_LIGHTS_PLACED) placed = MAX_LIGHTS_PLACED;
    const mc3_u32 bytes = datas * LIGHTDATA_BYTES + placed * (LIGHT_BYTES + 2u) + 64u;
    if (!datas || !placed || !heap_room(bytes)) { mark('R','L','G','X', datas, placed); return; }
    mc3_u8 *mem = (mc3_u8 *)r_alloc(bytes);
    if (!mem) return;
    mc3_u32 at = ((mc3_u32)mem + 15u) & ~15u;
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i) {
        PropType *t = &s->prop_types[i];
        if (!t->ok || !t->light_n) continue;
        t->light_data = at;
        for (mc3_u32 k = 0; k < t->light_n; ++k)
            light_data_from_mc2(at + k * LIGHTDATA_BYTES, t->light_rec + k * 0xC0u, t->name);
        at += t->light_n * LIGHTDATA_BYTES;
    }
    s->light_objs = at;
    s->light_props = at + placed * LIGHT_BYTES;
    mc3_u32 n = 0;
    float mat[12] __attribute__((aligned(16)));
    for (mc3_u32 i = 0; i < s->prop_count && n < placed; ++i) {
        const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
        if (!t->ok || !t->light_n) continue;
        const float *m = (const float *)(s->prop_matrices + i * 64u);
        for (int r = 0; r < 4; ++r)
            for (int k = 0; k < 3; ++k) mat[r * 3 + k] = m[r * 4 + k];
        for (mc3_u32 k = 0; k < t->light_n && n < placed; ++k, ++n) {
            const mc3_u32 l = s->light_objs + n * LIGHT_BYTES;
            for (mc3_u32 b = 0; b < LIGHT_BYTES; b += 4u) put_u32(l + b, 0u);
            MC3_CALL3(void, LIGHT_INIT, mc3_u32, mc3_u32, mc3_u32)(l, (mc3_u32)mat, t->light_data + k * LIGHTDATA_BYTES);
            MC3_CALL2(void, LIGHT_ADD, mc3_u32, mc3_u32)(mgr, l);
            ((mc3_u16 *)s->light_props)[n] = (mc3_u16)i;
        }
    }
    s->light_count = n;
    s->light_mgr = mgr;
    mark('R','L','G','T', (datas << 16) | n, mgr);
}
static void step_lights() {
    State *s = st();
    if (s->light_state == 0u) { lights_init(); return; }
    if (s->light_state != 9u || !s->light_count || !lights_enabled() || !ptr(s->camera)) return;
    const mc3_u32 mgr = word(LIGHT_MANAGER);
    if (!ptr(mgr) || !ptr(word(mgr))) return;
    if (mgr != s->light_mgr) {             // the city was reloaded (restart): a new, empty grid
        for (mc3_u32 n = 0; n < s->light_count; ++n) {
            const mc3_u32 l = s->light_objs + n * LIGHT_BYTES;
            put_u32(l + 12u, 0u);
            *(volatile mc3_u16 *)(l + 32u) = 0xFFFFu;
            MC3_CALL2(void, LIGHT_ADD, mc3_u32, mc3_u32)(mgr, l);
        }
        s->light_mgr = mgr;
    }
    const float cam_x = fword((const mc3_u32 *)(s->camera + 36u));
    const float cam_y = fword((const mc3_u32 *)(s->camera + 40u));
    const float cam_z = fword((const mc3_u32 *)(s->camera + 44u));
    // Only what mcPropType::Render would reach: lights of props in view (the
    // sphere spans lamp to ground, where the reflection lies), within 120 m,
    // at most LIGHTS_PER_FRAME. Every lamp within 160 m, in view or not, cost
    // the frame its 30 fps on Hollywood's avenues: 120 renderer frames took
    // up to 4.79 s instead of 4.00 there (4.25 with mc2l = 0), and a frame
    // held for a third vblank flips the interlaced field - the fast flicker
    // that neither screenshots nor PCSX2's video capture show (player).
    const float near2 = ffrom(0x46610000u);                   // 120^2
    mc3_u32 drawn = 0;
    for (mc3_u32 n = 0; n < s->light_count && drawn < LIGHTS_PER_FRAME; ++n) {
        const mc3_u32 l = s->light_objs + n * LIGHT_BYTES;
        const mc3_u32 prop = ((const mc3_u16 *)s->light_props)[n];
        if (s->prop_moved[prop]) {                            // knocked down: dark
            *(volatile mc3_u8 *)(l + 34u) &= (mc3_u8)~4u;
            continue;
        }
        const float *p = (const float *)l;
        const float dx = p[0] - cam_x, dy = p[1] - cam_y, dz = p[2] - cam_z;
        if (dx * dx + dy * dy + dz * dz > near2) continue;
        const float ground = ((const float *)(s->prop_matrices + prop * 64u))[13];
        const float half = (p[1] - ground) * ffrom(0x3F000000u);
        if (!sphere_visible(p[0], ground + half, p[2], (half < ffrom(0u) ? -half : half) + ffrom(0x40800000u)))
            continue;
        MC3_CALL1(void, LIGHT_GLOW, mc3_u32)(l);
        light_reflect(l, ground);
        ++drawn;
    }
    s->lights_drawn = drawn;
}
// Leaving the city: our lights out of MC3's grid while it is still the same one.
static void lights_release() {
    State *s = st();
    if (!s || !s->light_count || !ptr(s->light_mgr) || word(LIGHT_MANAGER) != s->light_mgr) return;
    for (mc3_u32 n = 0; n < s->light_count; ++n) {
        const mc3_u32 l = s->light_objs + n * LIGHT_BYTES;
        if (*(volatile mc3_u16 *)(l + 32u) != 0xFFFFu)
            MC3_CALL2(void, LIGHT_REMOVE, mc3_u32, mc3_u32)(s->light_mgr, l);
    }
    s->light_count = 0u;
}
// Leaving the city: no particle may point at our rules any more.
static void fx_release() {
    State *s = st();
    if (!s || !s->fx_mem) return;
    const mc3_u32 sys = word(PROP_PTX_SYSTEM);
    if (ptr(sys) && sys == s->fx_sys) MC3_CALL1(void, PTX_RESET, mc3_u32)(sys);
    s->fx_mem = 0u;
}
static void hit_prop(mc3_u32 i, float *car_vel, float speed) {
    State *s = st();
    const mc3_u32 *rec = (const mc3_u32 *)(s->prop_records + i * 20u);
    const PropType *t = &s->prop_types[rec[4]];
    DynProp *d = &s->dyn[s->dyn_next];
    s->dyn_next = (s->dyn_next + 1u) % MAX_DYN_PROPS;   // oldest slot: that prop stays where it lies
    float *m = (float *)(s->prop_matrices + i * 64u);
    fx_on_hit(i, m, speed);
    const float inv = ffrom(0x3F800000u) / speed;
    const float dx = car_vel[0] * inv, dz = car_vel[2] * inv;
    const float k = ffrom(0x40000000u) * ffrom(0x44BB8000u) / (ffrom(0x44BB8000u) + t->mass) * ffrom(0x3F333333u);
    d->index = i; d->state = 1u;
    for (int a = 0; a < 3; ++a) {
        d->pos[a] = m[12 + a];
        d->rot0[a] = m[a]; d->rot0[3 + a] = m[4 + a]; d->rot0[6 + a] = m[8 + a];
    }
    d->base_y = m[13];
    d->vel[0] = car_vel[0] * k; d->vel[2] = car_vel[2] * k;
    d->vel[1] = speed * ffrom(0x3DF5C28Fu);
    d->axis[0] = dz; d->axis[1] = ffrom(0u); d->axis[2] = -dx;
    d->angle = ffrom(0u);
    float w = speed * ffrom(0x3EB33333u);
    if (w < ffrom(0x40200000u)) w = ffrom(0x40200000u);
    if (w > ffrom(0x41100000u)) w = ffrom(0x41100000u);
    d->omega = w;
    // the car gives up part of its speed to the prop
    const float keep = ffrom(0x3F800000u) - ffrom(0x3F19999Au) * t->mass / (ffrom(0x44BB8000u) + t->mass);
    car_vel[0] *= keep; car_vel[2] *= keep;
    apply_car_velocity(car_vel);
    s->prop_moved[i] = 1u;
    ++s->props_hit;
    mark('R','P','H','T', i, s->props_hit);
    mark('R','P','H','2', rec[4] | ((mc3_u32)t->mass << 16), fbits(speed));
    mark('R','P','H','3', fbits(d->pos[0]), fbits(d->pos[2]));
}
// [boot] mc2h = 1 (diagnostic): at the start line, line up the three movable
// props nearest to the car 12, 20 and 28 m ahead of it, to drive into.
// mc2h = 2 (particles): the nearest prop throwing the hydrant spray, the
// nearest steam vent and the nearest one throwing trash, in that order.
static int has_fx(const PropType *t, mc3_u32 rule) {
    for (mc3_u32 j = 0; j < t->fx_count; ++j) if (t->fx[j] == rule) return 1;
    return 0;
}
static void line_up_test_props(const float *cp) {
    State *s = st();
    const char *v = mc3_bootarg(MC3_ID('m','c','2','h'));
    if (!v || (v[0] != '1' && v[0] != '2') || s->props_hit || s->dyn_next) return;
    if (v[0] == '2' && s->fx_state != 9u) return;           // wait for the rules
    // forward = -row 2 of the car's Matrix34 (rows of three floats; cp is row 3)
    const float fx = -cp[-3], fz = -cp[-1];
    mc3_u32 used[3] = { 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu };
    for (int k = 0; k < 3; ++k) {
        float best = ffrom(0x7F000000u);
        for (mc3_u32 i = 0; i < s->prop_count; ++i) {
            if (i == used[0] || i == used[1]) continue;
            const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
            if (v[0] == '2') {
                const mc3_u32 want = k == 0 ? FX_HYDRANT : k == 1 ? 14u : 1u;
                if (!has_fx(t, want)) continue;
            } else
            if (t->gfx_only || !t->has_phys || t->fixed || t->fixed_object || t->drivable) continue;
            const float *m = (const float *)(s->prop_matrices + i * 64u);
            const float dx = m[12] - cp[0], dz = m[14] - cp[2], d2 = dx * dx + dz * dz;
            if (d2 < best) { best = d2; used[k] = i; }
        }
        if (used[k] == 0xFFFFFFFFu) return;
        float *m = (float *)(s->prop_matrices + used[k] * 64u);
        const float dist = ffrom(0x41400000u) + ffrom(0x41000000u) * (float)k;   // 12, 20, 28
        m[12] = cp[0] + fx * dist; m[13] = cp[1]; m[14] = cp[2] + fz * dist;
        if (v[0] == '2' && k == 1) { m[12] -= fz * ffrom(0x40800000u); m[14] += fx * ffrom(0x40800000u); }  // 4 m aside
        float *rec = (float *)(s->prop_records + used[k] * 20u);
        rec[0] = m[12]; rec[1] = m[13]; rec[2] = m[14];
        mark('R','P','L','U', used[k], ((const mc3_u32 *)(s->prop_records + used[k] * 20u))[4]);
    }
    s->dyn_next = MAX_DYN_PROPS - 1u;          // once (dyn_next != 0 from here on)
}
static void step_prop_physics() {
    State *s = st();
    float *cp, *cv;
    if (player_motion(&cp, &cv)) {
        line_up_test_props(cp);
        const float speed = fsqrt(cv[0] * cv[0] + cv[2] * cv[2]);
        if (speed > ffrom(0x3FC00000u)) {
            const float range = ffrom(0x40C00000u);
            for (mc3_u32 i = 0; i < s->prop_count; ++i) {
                if (s->prop_moved[i]) continue;
                const float *m = (const float *)(s->prop_matrices + i * 64u);
                const float dx = m[12] - cp[0], dz = m[14] - cp[2], dy = m[13] - cp[1];
                if (dx > range || dx < -range || dz > range || dz < -range) continue;
                if (dy > ffrom(0x40800000u) || dy < -ffrom(0x40800000u)) continue;
                const PropType *t = &s->prop_types[((const mc3_u32 *)(s->prop_records + i * 20u))[4]];
                if (t->gfx_only || t->drivable) continue;
                const int solid = t->fixed_object || t->fixed;
                // a movable prop needs its .phys (mass); a FixedObject one
                // does not - palms and most trees have none, and the car
                // drove through them (player's pins, 2026-09-27)
                if (!solid && !t->has_phys) continue;
                // a circle stands for the prop; for a big fixed one (container,
                // ramp, crane) it would be wrong, so those are left alone -
                // except trees and palms, whose big sphere is the canopy:
                // they are a trunk of 0.45 m
                if (solid && t->radius > ffrom(0x41000000u) && !t->trunk) continue;
                float r = t->radius * ffrom(0x3E99999Au);
                if (r < ffrom(0x3E99999Au)) r = ffrom(0x3E99999Au);
                if (r > ffrom(0x3F99999Au)) r = ffrom(0x3F99999Au);
                if (t->trunk) r = ffrom(0x3EE66666u);
                r += ffrom(0x3F8CCCCDu);
                const float d2 = dx * dx + dz * dz;
                if (d2 > r * r) continue;
                const float into = dx * cv[0] + dz * cv[2];
                if (into <= ffrom(0u)) continue;   // moving away from it
                if (solid) {
                    // take the velocity into the prop away, with a little bounce
                    // head-on (within ~45 degrees): the car stops with a
                    // small bounce, as against MC3's own poles; a glancing
                    // blow only loses the part of the velocity into it
                    const float dl = fsqrt(d2);
                    if (into > ffrom(0x3F333333u) * dl * speed) {
                        const float back = speed * ffrom(0x3E4CCCCDu) / dl;
                        cv[0] = -dx * back; cv[2] = -dz * back;
                    } else {
                        const float k = into / d2 * ffrom(0x3F99999Au);
                        cv[0] -= dx * k; cv[2] -= dz * k;
                    }
                    apply_car_velocity(cv);
                    const float dl2 = fsqrt(d2);
                    if (dl2 < r * ffrom(0x3F733333u) && dl2 > ffrom(0x3C23D70Au)) {
                        // inside the trunk: back to its edge (0.95 r deep or more)
                        const float out = (r - dl2) / dl2;
                        push_car_out(cp[0] - dx * out, cp[2] - dz * out, cv);
                    }
                    if (i != s->fx_last_solid || s->fx_frame - s->fx_last_frame > 45u) {
                        fx_on_hit(i, m, speed);
                        s->fx_last_solid = i;
                        s->fx_last_frame = s->fx_frame;
                    }
                    if (s->solid_hits++ < 40u) {
                        mark('R','P','S','O', i | (((const mc3_u32 *)(s->prop_records + i * 20u))[4] << 16), fbits(speed));
                        mark('R','P','S','P', fbits(cp[0]), fbits(cp[2]));
                    }
                    continue;
                }
                hit_prop(i, cv, speed);
            }
        }
    }
    const float dt = ffrom(0x3D088889u);
    for (mc3_u32 j = 0; j < MAX_DYN_PROPS; ++j) {
        DynProp *d = &s->dyn[j];
        if (d->state != 1u) continue;
        d->vel[1] -= ffrom(0x411CCCCDu) * dt;
        for (int a = 0; a < 3; ++a) d->pos[a] += d->vel[a] * dt;
        int ground = 0;
        if (d->pos[1] <= d->base_y) {
            ground = 1;
            d->pos[1] = d->base_y;
            d->vel[1] = d->vel[1] < ffrom(0u) ? -d->vel[1] * ffrom(0x3E4CCCCDu) : d->vel[1];
            if (d->vel[1] < ffrom(0x3F800000u)) d->vel[1] = ffrom(0u);
            const float f = ffrom(0x3F800000u) - ffrom(0x40400000u) * dt;
            d->vel[0] *= f; d->vel[2] *= f;
        }
        d->angle += d->omega * dt;
        if (d->angle >= ffrom(0x3FC90FDAu)) { d->angle = ffrom(0x3FC90FDAu); d->omega = ffrom(0u); }
        float sn, cs;
        sincos_q(d->angle, &sn, &cs);
        float *m = (float *)(s->prop_matrices + d->index * 64u);
        for (int row = 0; row < 3; ++row) rotate_vec(&d->rot0[row * 3], d->axis, sn, cs, &m[row * 4]);
        for (int a = 0; a < 3; ++a) m[12 + a] = d->pos[a];
        float *rec = (float *)(s->prop_records + d->index * 20u);
        rec[0] = d->pos[0]; rec[1] = d->pos[1]; rec[2] = d->pos[2];
        const float v2 = d->vel[0] * d->vel[0] + d->vel[2] * d->vel[2];
        if (ground && d->omega == ffrom(0u) && v2 < ffrom(0x3D4CCCCDu) * ffrom(0x3D4CCCCDu)) {
            d->state = 0u;
            mark('R','P','R','S', d->index, fbits(d->angle));
            mark('R','P','R','2', fbits(d->pos[0]), fbits(d->pos[2]));
        }
    }
}
static void queue_prop(mc3_u32 i, mc3_u32 arena, int lod2) {
    State *s = st();
    const mc3_u32 *rec = (const mc3_u32 *)(s->prop_records + i * 20u);
    const PropType *t = &s->prop_types[rec[4]];
    const int use2 = lod2 && t->packets2;
    const mc3_u32 n = use2 ? t->packets2 : t->packets;
    const mc3_u32 *addr = use2 ? t->pk2_addr : t->pk_addr;
    const mc3_u16 *qwc = use2 ? t->pk2_qwc : t->pk_qwc;
    const mc3_u16 *slot = use2 ? t->pk2_slot : t->pk_slot;
    mc3_u32 need = 0, last = NO_IMAGE;
    for (mc3_u32 k = 0; k < n; ++k) {
        const mc3_u32 image = slot[k];
        if (image != last && !image_resident(image)) need += upload_bytes(image);
        last = image;
    }
    if (!arena_can_take(need)) { ++s->frame_skipped; return; }
    queue_ref(s->prop_matrices + i * 64u, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
    for (mc3_u32 k = 0; k < n; ++k) {
        const mc3_u32 sw = use_image(slot[k], arena);
        if (sw) bind_switch(sw);
        queue_ref(addr[k], qwc[k], 0u, 0u);
    }
    ++s->frame_props;
}

static int pcp_needed(mc3_u32 i) {
    return (st()->cpvs_need[i >> 5] >> (i & 31u)) & 1u;
}

// Read the original cpvs container sequentially, a slice per frame, keeping
// only what is drawn: the LOD 0 `_main` PCP0s named by the map (1.9 of its
// 3 MB in LA) and the CPP0 palette. cpvs_table[i] packs, for kept entry i,
// offset/16 (bits 0..17) and length/16 (bits 18..30) - file offsets while the
// directory is read, offsets into the compact copy once it is loaded.
// The frame alpha the reflective ground (`_0_refl` pieces) writes, 0..128:
// [boot] mc2a, default 10 (the user's pick against retail; 78 = m_roadShiniess x 128 was too strong).
static mc3_u32 road_alpha() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','a'));
    mc3_u32 n = 0; int any = 0;
    while (v && *v >= '0' && *v <= '9') { n = n * 10u + (mc3_u32)(*v++ - '0'); any = 1; }
    return any && n <= 128u ? n : 10u;
}
// [boot] mc2u: how much of MC2's texture WETNESS reaches the reflection mask.
// MC2's ground textures carry, in their alpha, how wet each texel is, per
// weather: clear asphalt 0, cloudy up to 29 (mean 7), rainy up to 104-128
// (mean 25-40, the puddles) - measured in LA's nine banks. 0..128, default 128
// (as MC2); 0 = off (only the flat mc2a).
static mc3_u32 wet_scale() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','u'));
    mc3_u32 n = 0; int any = 0;
    while (v && *v >= '0' && *v <= '9') { n = n * 10u + (mc3_u32)(*v++ - '0'); any = 1; }
    return any && n <= 128u ? n : 128u;
}
// [boot] mc2k = 0: the card reflections are not drawn (see reflect_mask_pass)
static int cards_enabled() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','k'));
    return !v || v[0] != '0';
}
// How far the cards are drawn: [boot] mc2k = N (metres, 10 and up), default
// 200. Drawn out to the city's radius, the shop-window glows of a whole
// avenue showed at once, far down the street (player, 2026-09-30).
static float card_distance() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','k'));
    mc3_u32 n = 0;
    while (v && *v >= '0' && *v <= '9') n = n * 10u + (mc3_u32)(*v++ - '0');
    return n >= 10u && n <= 2000u ? (float)n : 200.0f;
}
static void step_cpvs() {
    State *s = st();
    mc3_u8 scratch[2048] __attribute__((aligned(16)));
    if (s->cpvs_state == 0u) {
        char path[96];
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, cond_time());
        path[n++] = '_'; path[n] = 0;
        n = append(path, n, cond_weather());
        append(path, n, cpvs_suffix());
        const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
            (path, STREAM_RAW);
        if (!h) { s->cpvs_error = 1u; s->cpvs_state = 3u; return; }
        const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
        mc3_u8 header[16] __attribute__((aligned(16)));
        const mc3_u32 count = size > 16 && size <= (int)CPVS_MAX &&
            read_exact(h, header, 8u) && le32(header) == RSC0 ? le32(header + 4u) : 0u;
        mc3_u8 *table = count && count <= MAX_CPVS_ENTRIES &&
            8u + count * 8u < (mc3_u32)size && heap_room(count * 4u + 16u)
            ? (mc3_u8 *)r_alloc(count * 4u + 16u) : 0;
        if (!table) {
            s->cpvs_error = count ? 3u : 2u;
            s->cpvs_bytes = (mc3_u32)size;
            MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
            s->cpvs_state = 3u;
            return;
        }
        s->cpvs_stream = h;
        s->cpvs_bytes = (mc3_u32)size;
        s->cpvs_count = count;
        s->cpvs_table = ((mc3_u32)table + 15u) & ~15u;
        s->cpvs_table_mem = (mc3_u32)table;
        s->cpvs_pos = 8u;
        s->cpvs_index = 0u;
        s->cpvs_kept = 0u;
        s->cpvs_palette_index = 0xFFFFFFFFu;
        s->cpvs_state = 1u;
        return;
    }
    mc3_u32 *table = (mc3_u32 *)s->cpvs_table;
    if (s->cpvs_state == 1u) {
        // Directory: entry i ends where entry i+1 starts, so each entry is
        // settled when the next offset is read.
        const mc3_u32 count = s->cpvs_count;
        mc3_u32 budget = 256u;
        while (budget-- && s->cpvs_index < count) {
            if (!read_exact(s->cpvs_stream, scratch, 8u)) { s->cpvs_error = 4u; break; }
            s->cpvs_pos += 8u;
            const mc3_u32 i = s->cpvs_index++;
            table[i] = le32(scratch);                      // offset for now
            s->cpvs_fourcc_last = le32(scratch + 4u);
            if (s->cpvs_fourcc_last == CPP0) s->cpvs_palette_index = i;
            if (s->cpvs_fourcc_last != PCP0 && s->cpvs_fourcc_last != CPP0)
                table[i] |= 0x80000000u;                   // unknown: never kept
        }
        if (!s->cpvs_error && s->cpvs_index < count) return;
        // settle lengths, pick what is kept, size the compact copy
        mc3_u32 kept = 0u;
        for (mc3_u32 i = 0; !s->cpvs_error && i < count; ++i) {
            const mc3_u32 off = table[i] & 0x7FFFFFFFu;
            const mc3_u32 end = i + 1u < count ? table[i + 1u] & 0x7FFFFFFFu : s->cpvs_bytes;
            const int want = !(table[i] & 0x80000000u) &&
                (i == s->cpvs_palette_index || pcp_needed(i));
            if (off < 8u + count * 8u || end < off || end > s->cpvs_bytes ||
                (off & 15u) || ((end - off) & 15u) || end - off >= (8192u << 4)) {
                s->cpvs_error = 5u;
                break;
            }
            table[i] = want ? 0x80000000u | (off >> 4) | (((end - off) >> 4) << 18) : 0u;
            if (want) kept += end - off;
        }
        if (!s->cpvs_error && (s->cpvs_palette_index >= count ||
                               !(table[s->cpvs_palette_index] & 0x80000000u)))
            s->cpvs_error = 6u;
        mc3_u8 *mem = !s->cpvs_error && heap_room(kept + 16u)
            ? (mc3_u8 *)r_alloc(kept + 16u) : 0;
        if (!mem) {
            if (!s->cpvs_error) s->cpvs_error = 7u;
            s->cpvs_bytes = kept;
            MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->cpvs_stream);
            s->cpvs_stream = 0u;
            s->cpvs_state = 3u;
            return;
        }
        s->cpvs_mem = (mc3_u32)mem;
        s->cpvs = ((mc3_u32)mem + 15u) & ~15u;
        s->cpvs_kept = kept;
        s->cpvs_fill = 0u;
        s->cpvs_index = 0u;
        s->cpvs_state = 4u;
        return;
    }
    if (s->cpvs_state != 4u) return;
    // Payload: skip to each kept entry and read it straight into the copy.
    mc3_u32 budget = CPVS_SLICE;
    while (budget && s->cpvs_index < s->cpvs_count) {
        const mc3_u32 i = s->cpvs_index;
        const mc3_u32 v = table[i];
        if (!(v & 0x80000000u)) { ++s->cpvs_index; continue; }
        const mc3_u32 off = (v & 0x3FFFFu) << 4;
        const mc3_u32 len = ((v >> 18) & 0x1FFFu) << 4;
        if (s->cpvs_pos < off) {
            mc3_u32 gap = off - s->cpvs_pos;
            if (gap > 2048u) gap = 2048u;
            if (!read_exact(s->cpvs_stream, scratch, gap)) { s->cpvs_error = 8u; break; }
            s->cpvs_pos += gap;
            budget = budget > gap ? budget - gap : 0u;
            continue;
        }
        if (s->cpvs_pos != off || s->cpvs_fill + len > s->cpvs_kept ||
            !read_exact(s->cpvs_stream, (void *)(s->cpvs + s->cpvs_fill), len)) {
            s->cpvs_error = 9u;
            break;
        }
        table[i] = 0x80000000u | (s->cpvs_fill >> 4) | ((len >> 4) << 18);
        s->cpvs_fill += len;
        s->cpvs_pos += len;
        budget = budget > len ? budget - len : 0u;
        ++s->cpvs_index;
    }
    if (!s->cpvs_error && s->cpvs_index < s->cpvs_count) return;
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->cpvs_stream);
    s->cpvs_stream = 0u;
    if (s->cpvs_error || s->cpvs_fill != s->cpvs_kept) {
        if (!s->cpvs_error) s->cpvs_error = 10u;
        s->cpvs_state = 3u;
        return;
    }
    const mc3_u32 palette_at = s->cpvs +
        ((table[s->cpvs_palette_index] & 0x3FFFFu) << 4);
    // Three copies of the palette, differing only in ALPHA: MC2's own (0x80)
    // for drawing, the road's flat reflectivity (road_alpha()) and the scale of
    // the texture wetness (wet_scale()), the last two for the alpha-only pass
    // that tells MC3 where reflections may show (reflect_mask_pass).
    mc3_u8 *pm = heap_room(3200u) ? (mc3_u8 *)r_alloc(3u * (16u + 1040u) + 16u) : 0;
    if (!pm) { s->cpvs_error = 11u; s->cpvs_state = 3u; return; }
    const mc3_u32 pk = ((mc3_u32)pm + 15u) & ~15u;
    for (mc3_u32 k = 0; k < 3u; ++k) {
        mc3_u32 *w = (mc3_u32 *)(pk + k * 1056u);
        const mc3_u32 alpha = k == 2u ? wet_scale() : k ? road_alpha() : 0x80u;   // 0: MC2's own
        w[0] = 65u; w[1] = VCLQ; w[2] = 0u; w[3] = 1040u;
        w[4] = VIF_FLUSH;
        w[5] = 0x6E000000u | 768u;       // UNPACK V4-8 x256 -> VU 768, the palette
        for (mc3_u32 i = 0; i < 256u; ++i)
            w[6u + i] = (word(palette_at + i * 4u) & 0x00FFFFFFu) | (alpha << 24);
        w[262] = 0u; w[263] = 0u;
    }
    s->palette = pk;
    s->palette_refl = pk + 1056u;
    s->palette_wet = pk + 2112u;
    s->cpvs_state = 2u;
    mark('R','C','P','S', s->cpvs_count, s->cpvs_kept);
    mark('R','H','E','P', mc3_heap_free(mc3_heap_active()), 2u);
}

static int find_target(mc3_u32 pmd) {
    const State *s = st();
    int lo = 0, hi = (int)s->target_count - 1;
    while (lo <= hi) {
        const int mid = (lo + hi) >> 1;
        const mc3_u32 v = s->targets[mid].pmd;
        if (v == pmd) return mid;
        if (v < pmd) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
}

// A PCP0 `ref` names a PMD0 payload as (RSC entry index + 1, modulo 4096)
// << 20 | byte offset; the entry is whichever loaded PMD it can be.
static mc3_u32 resolve_ref(mc3_u32 addr, mc3_u32 qwc) {
    const State *s = st();
    const mc3_u32 src = ((addr >> 20) & 0xFFFu) - 1u, want = addr & 0xFFFF0u;
    for (mc3_u32 k = 0; k < 3u; ++k) {
        const int t = find_target(src + k * 4096u);
        if (t < 0) continue;
        const Piece *piece = &s->pieces[t];
        // The index is only known modulo 4096 (LA has 4436 entries): accept
        // the PMD whose own tag right before `want` carries this same QWC.
        // First-fit on bounds alone sent 60 of LA's 10190 refs to the wrong
        // PMD and the VU into garbage.
        if (piece->ready && want >= 16u && want + qwc * 16u <= piece->block_bytes &&
            word(piece->block + want - 16u) == qwc &&
            word(piece->block + want - 12u) == 0u)
            return piece->block + want;
    }
    return 0u;
}

static int resolve_ref_target(mc3_u32 addr, mc3_u32 qwc) {
    const State *s = st();
    const mc3_u32 src = ((addr >> 20) & 0xFFFu) - 1u, want = addr & 0xFFFF0u;
    for (mc3_u32 k = 0; k < 3u; ++k) {
        const int t = find_target(src + k * 4096u);
        if (t < 0) continue;
        const Piece *piece = &s->pieces[t];
        if (piece->ready && want >= 16u && want + qwc * 16u <= piece->block_bytes &&
            word(piece->block + want - 16u) == qwc &&
            word(piece->block + want - 12u) == 0u)
            return t;
    }
    return -1;
}

// Queue PCP0 `index` of the cpvs container: inline tags straight from the
// container, refs into the PMD blocks, and every CALL's texture. A dry pass
// first: when a ref or texture cannot be resolved nothing is queued and the
// caller draws the PMDs flat instead.
static int queue_pcp(mc3_u32 index, mc3_u32 arena) {
    State *s = st();
    if (index >= s->cpvs_count || s->frame_pcp >= s->pcp_limit) return 0;
    const mc3_u32 v = ((const mc3_u32 *)s->cpvs_table)[index];
    if (!(v & 0x80000000u)) return 0;
    const mc3_u32 off = (v & 0x3FFFFu) << 4;
    const mc3_u32 end = off + (((v >> 18) & 0x1FFFu) << 4);
    mc3_u32 need = 0, last = NO_IMAGE;
    for (int pass = 0; pass < 2; ++pass) {
        if (pass && !arena_can_take(need)) { ++s->frame_skipped; return 1; }
        mc3_u32 p = off;
        int closed = 0;
        while (!closed && p + 16u <= end) {
            const mc3_u32 at = s->cpvs + p;
            const mc3_u32 lo = word(at), addr = word(at + 4u);
            const mc3_u32 id = (lo >> 28) & 7u, qwc = lo & 0xFFFFu;
            if (id == 3u) {                                  // ref
                // resolved once, then kept in the tag: the address replaces
                // MC2's handle and bit 24 of the tag's low word (always 0 in
                // MC2's tags; every reader takes only qwc and id) says so.
                // ~10 000 refs looked up again every frame cost the frame.
                // (Bit 31 of the handle could not be the flag: it holds a
                // 12-bit index + 1, so indices from 2048 on set it.)
                mc3_u32 src;
                if (lo & REF_RESOLVED) src = addr;
                else {
                    src = resolve_ref(addr, qwc);
                    if (!src) return 0;
                    *(volatile mc3_u32 *)(at + 4u) = src;
                    *(volatile mc3_u32 *)at = lo | REF_RESOLVED;
                }
                if (pass) queue_ref(src, qwc, word(at + 8u), word(at + 12u));
                p += 16u;
                continue;
            }
            if (id != 1u && id != 5u && id != 6u && id != 7u) return 0;
            if (p + 16u + qwc * 16u > end) return 0;
            if (pass && (qwc || word(at + 8u) || word(at + 12u)))
                queue_ref(at + 16u, qwc, word(at + 8u), word(at + 12u));
            p += 16u + qwc * 16u;
            if (id == 5u) {                                  // call: texture
                const mc3_u32 image = texture_slot(addr);
                if (image == NO_IMAGE) {
                    // unknown texture: keep the one in effect
                } else if (!pass) {
                    if (image != last && !image_resident(image)) need += upload_bytes(image);
                    last = image;
                } else {
                    const mc3_u32 sw = use_image(image, arena);
                    if (sw) bind_switch(sw);    // else keep the one in effect
                }
            }
            closed = id == 6u || id == 7u;              // ret / end
        }
        if (!pass && !closed) return 0;
    }
    ++s->frame_pcp;
    return 1;
}

// [boot] mc2x / mc2y / mc2z: put the game camera at this world point
// (signed integers; diagnostic, to photograph any place of the city).
static int boot_int(mc3_u32 id, int *out) {
    const char *v = mc3_bootarg(id);
    if (!v) return 0;
    int sign = 1, n = 0, any = 0;
    if (*v == '-') { sign = -1; ++v; }
    while (*v >= '0' && *v <= '9') { n = n * 10 + (*v++ - '0'); any = 1; }
    *out = sign * n;
    return any;
}
// Close every stream, free every block, and go back to the state the module
// had when it was loaded (all of State zero). Runs when the loaded race is not
// in Los Angeles any more.
static void release_all() {
    State *s = st();
    lights_release();
    fx_release();
    if (s) {
        const mc3_u32 streams[5] = { s->rsc_stream, s->shared_stream, s->amb_stream,
                                     s->cpvs_stream, s->hood.stream };
        for (int i = 0; i < 5; ++i)
            if (streams[i]) MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(streams[i]);
    }
    Tracked *t = trk();
    const mc3_u32 blocks = t->n;
    while (t->n) mc3_free((void *)t->ptr[--t->n]);
    if (*state_mem_slot()) {
        mc3_free((void *)*state_mem_slot());
        *state_mem_slot() = 0u;
        *state_slot() = 0;
    }
    t->dropped = 0; t->retry = 0;
    mark('R','R','E','L', blocks, mc3_heap_largest(mc3_heap_active()));
}

// In a race (or its replay) in Los Angeles. City = mcRaceConfig current +0 (the
// menu edits another config, 0x619B18). Game state = gGameState (0x6199F0)
// +4, as mcGameState::ChangeState 0x1A55D0 stores it: 2 game, 3 replay,
// 5 garage, 7 front end. The front end reloads the last city as its
// background - Los Angeles after an arcade race - and with our ~11 MB resident
// its menu never came up, so the front end gets the heap back.
static int in_mc2_city() {
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG_CURRENT;
    const mc3_u32 gs = *(volatile mc3_u32 *)GAME_STATE;
    if (!ptr(cfg) || !ptr(gs)) return 0;
    const mc3_u32 state = *(volatile mc3_u32 *)(gs + 4u);
    return mc2_city_supported(*(volatile mc3_u32 *)cfg) && (state == 2u || state == 3u);
}

// Nothing left for load_step to do: the props are in (they come last) or a
// stage failed for good (memory errors 3/5 are retried, so they do not count).
static int load_finished() {
    State *s = st();
    if (s->prop_state >= 2u) return 1;
    if (s->manifest_error || s->rsc_error || s->bootstrap_error || s->instances_error) return 1;
    if (s->texture_error && s->texture_error != 3u && s->texture_error != 5u) return 1;
    return 0;
}

// One slice of the city load: catalog, geometry, textures, hoods, CPVS and
// props, each a bounded amount of work. Called once per frame by the camera
// hook, and in a loop by preload_city before the race starts. Returns 1 when
// there is nothing left to load (done or failed for good).
static int load_step() {
    State *s = st();
    if (!s->heap_reported) {
        s->heap_reported = 1u;
        mark('R','H','E','P', mc3_heap_free(mc3_heap_active()), 0u);
    }
    if (!s->bootstrap && !s->bootstrap_error) build_bootstrap();
    if (!s->frame_ready && !s->texture_error) alloc_frame_mem();
    if (s->rsc_state == 2u && !s->textures_ready && !s->texture_error) {
        // Textures after the geometry pass: losangeles.rsc's are kept on the
        // way; the shared bank of this time/weather is read whole.
        step_shared_textures();
        if (s->shared_state == 2u) {
            if (build_slots()) {
                s->textures_ready = 1u;
                mark('R','T','E','X', s->slot_count, s->tex_bytes + s->shared_bytes);
                mark('R','H','E','P', mc3_heap_free(mc3_heap_active()), 3u);
            } else if (!s->texture_error) s->texture_error = 9u;
        }
    }
    if (s->textures_ready && !s->instances_ready && !s->instances_error) step_hoods();
    if (s->instances_ready && !s->names_ready && !s->map_error) build_cpvs_names();
    if (s->names_ready && (s->cpvs_state < 2u || s->cpvs_state == 4u)) step_cpvs();
    if (s->cpvs_state == 2u && !s->map_ready) cover_components();
    // props last: the city is complete without them
    if ((s->map_ready || s->map_error || s->cpvs_error) && s->prop_state < 2u) step_props();
    if (s->textures_ready && s->sky_state < 2u && sky_enabled()) step_sky();
    if (s->prop_error && !s->prop_reported) {
        s->prop_reported = 1u;
        mark('R','P','R','E', s->prop_error, s->prop_type_count);
    }
    if (s->map_error && !s->map_reported) {
        s->map_reported = 1u;
        mark('R','C','M','E', s->map_error, 0);
    }
    if (s->cpvs_error && !s->cpvs_reported) {
        s->cpvs_reported = 1u;
        mark('R','C','P','E', s->cpvs_error, s->cpvs_bytes);
    }
    if (s->frame_ready && s->rsc_state == 0u) open_rsc_stream();
    if (s->rsc_state == 1u && !s->manifest_ready && !s->manifest_error) build_catalog();
    if (s->rsc_state == 1u && s->manifest_ready) {
        mc3_u32 built = 0;
        while (s->load_index < s->target_count && built < LOADS_PER_FRAME) {
            const int result = build_packet(&s->pieces[s->load_index],
                                            &s->targets[s->load_index]);
            if (result == 2) break;  // bounded RSC skip; continue next frame
            if (!result) break;
            ++s->load_index;
            ++built;
        }
        if (s->rsc_state == 1u && s->load_index == s->target_count) {
            // the rest of the file may still hold texture entries
            const int r = s->tex_dest ? consume_to(s->rsc_size, 0x20000u) : 1;
            if (r == 0) return 0;
            if (r < 0) s->texture_error = 4u;
            close_rsc_stream(s);
            s->rsc_state = 2u;
            mark('R','S','C','D', s->load_index, s->rsc_cursor);
            mark('R','H','E','P', mc3_heap_free(mc3_heap_active()), 1u);
        }
    }
    if (s->manifest_error && !s->manifest_reported) {
        s->manifest_reported = 1u;
        mark('R','D','M','E', s->manifest_error, 0);
    }
    if (s->bootstrap_error && !s->bootstrap_reported) {
        s->bootstrap_reported = 1u;
        mark('R','D','B','E', s->bootstrap_error, 0);
    }
    if (s->texture_error && !s->texture_reported) {
        s->texture_reported = 1u;
        mark('R','D','T','E', s->texture_error, 0);
    }
    if (s->instances_error && !s->instances_reported) {
        s->instances_reported = 1u;
        mark('R','I','N','E', s->instances_error, s->hood_index);
    }
    if (s->rsc_error && !s->rsc_reported) {
        s->rsc_reported = 1u;
        mark('R','S','C','E', s->rsc_error, s->load_index);
    }
    return load_finished();
}


// ---------------------------------------------------------------------------
// [boot] mc2s = N (diagnostic): the camera chases the AI opponents instead of
// the player, N seconds each (default 15), so whole races can be watched and
// photographed around the map. Same idea as the Orbit mode of AlgumCorrupto's
// freecam (CinematicClub): every car is an instance with vtable 0x00627630,
// Matrix34 at +0x10 (translation +0x34); the player's own is *(player+20)+0x18
// and is skipped. The heap is scanned once when the list is empty or stale.
// Camera = look-at from 7 m behind and 2.5 m above the car (rows right, up,
// back, then the eye at +36, as mcCamera keeps it). Marker RSPC <cars> <shown>.
// ---------------------------------------------------------------------------
enum { CAR_VTABLE = 0x00627630 };
static int spectate_seconds() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','s'));
    if (!v || v[0] < '1' || v[0] > '9') return 0;
    int n = 0;
    while (*v >= '0' && *v <= '9') n = n * 10 + (*v++ - '0');
    return n > 1 ? n : 15;
}
// mc2s = <N>f: the player's car hangs 30 m above the watched opponent, every
// frame, so the opponents are never far from the player (test for whatever in
// the game depends on the distance to the player: physics/LOD of far cars, the
// stacking at shortcut nodes). The car is moved the way the AI respawn moves
// its own: aiBrain::TeleportCar (0x3FC600) takes the brain and reads only
// brain+12 -> opponent, opponent+36 -> car (mcCarSim::Reposition, phLevel
// UpdateObject, SetVelocity), so a two-word fake brain pointing at the
// player's car (*(player+20)) is enough. Marker RFOL <moves> <car>.
// mc2s = <N>t: watch the AMBIENT TRAFFIC cars instead of the opponents -
// aiAmbientTraffic (*0x6157C4)+8 -> aiAmbientTrafficData: +300 count, +304
// array of aiAmbientVehicle, 560 bytes each, current rail pointer at +264
// (zero = not placed), position at +100. Marker RSPT <placed> <shown index>.
static int spectate_traffic() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','s'));
    if (!v) return 0;
    while (*v >= '0' && *v <= '9') ++v;
    return *v == 't';
}
static int spectate_traffic_follow() {       // mc2s = <N>tf
    const char *v = mc3_bootarg(MC3_ID('m','c','2','s'));
    if (!v) return 0;
    while (*v >= '0' && *v <= '9') ++v;
    return v[0] == 't' && v[1] == 'f';
}
static void follow(const float *opp);
static int traffic_car(mc3_u32 k, float *pos, mc3_u32 *rail_out, mc3_u32 *ped_out) {
    *rail_out = 0xFFFFFFFFu;
    *ped_out = 0x0000FFFFu;
    const mc3_u32 traffic = word(0x006157C4u);
    if (!ptr(traffic) || !ptr(word(traffic + 8u))) return 0;
    const mc3_u32 data = word(traffic + 8u);
    const int n = *(volatile short *)(data + 300u);
    const mc3_u32 arr = word(data + 304u);
    if (!ptr(arr) || n <= 0 || n > 64) return 0;
    const mc3_u32 net = word(0x0061B170u);
    const mc3_u32 base = ptr(net) ? word(net + 60u) : 0u;
    const mc3_u32 count = ptr(net) ? *(volatile mc3_u16 *)(net + 56u) : 0u;
    mc3_u32 placed = 0, want = 0xFFFFFFFFu, ped = 0, first_ped = 0xFFFFu;
    for (int i = 0; i < n; ++i) {
        const mc3_u32 current = word(arr + 560u * (mc3_u32)i + 264u);
        if (ptr(current)) {
            if (placed == k % 64u) want = (mc3_u32)i;
            ++placed;
            if (ptr(base) && current >= base && current < base + count * 20u && ((current - base) % 20u) == 0u) {
                const mc3_u32 rail_id = (current - base) / 20u;
                if (*(volatile mc3_u8 *)(current + 18u) & 8u) {
                    if (!ped) first_ped = rail_id;
                    ++ped;
                }
            }
        }
    }
    *ped_out = (ped << 16) | first_ped;
    if (!placed) return 0;
    if (want == 0xFFFFFFFFu) {                  // k past the placed ones: wrap
        mc3_u32 c = 0;
        for (int i = 0; i < n; ++i)
            if (ptr(word(arr + 560u * (mc3_u32)i + 264u)) && c++ == k % placed) want = (mc3_u32)i;
    }
    const float *p = (const float *)(arr + 560u * want + 100u);
    const mc3_u32 rail = word(arr + 560u * want + 264u);
    if (ptr(base) && rail >= base && rail < base + count * 20u && ((rail - base) % 20u) == 0u)
        *rail_out = (rail - base) / 20u;
    pos[0] = p[0]; pos[1] = p[1]; pos[2] = p[2];
    return (int)placed;
}
static int spectate_follow() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','s'));
    if (!v) return 0;
    while (*v >= '0' && *v <= '9') ++v;
    return *v == 'f';
}
// mc2s = <N>c: CHASE - the player's car is put 14 m behind the watched opponent,
// on the ground, every frame, and the game's own camera is left alone. Unlike
// the modes above, what MC3 culls is then decided from the camera that draws:
// mcGame::PreDraw builds the occluder viewer and the cull frustum from the
// local player's camera, not from the camera this renderer is handed.
static int spectate_chase() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','s'));
    if (!v) return 0;
    while (*v >= '0' && *v <= '9') ++v;
    return *v == 'c';
}
enum { AI_TELEPORT_CAR = 0x003FC600, INERTIAL_SET_VELOCITY = 0x0057C108 };
static void follow_at(const float *opp, float up, float back);
static void follow(const float *opp) { follow_at(opp, ffrom(0x41F00000u), ffrom(0u)); }   // 30 m up
static void follow_at(const float *opp, float up, float back) {
    State *s = st();
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    if (!ptr(player) || !ptr(word(player + 20u))) return;
    const mc3_u32 car = word(player + 20u);
    s->follow_opp[9] = car;                                  // opponent+36 -> car
    s->follow_brain[3] = (mc3_u32)&s->follow_opp[0];         // brain+12 -> opponent
    float m[12], vel[4];
    for (int i = 0; i < 12; ++i) m[i] = opp[i];
    // rows right, up, back: `back` metres along the back row, `up` metres up
    m[9] = opp[9] + opp[6] * back;
    m[10] = opp[10] + opp[7] * back + up;
    m[11] = opp[11] + opp[8] * back;
    vel[0] = vel[1] = vel[2] = vel[3] = ffrom(0u);
    MC3_CALL3(void, AI_TELEPORT_CAR, mc3_u32, mc3_u32, mc3_u32)
        ((mc3_u32)&s->follow_brain[0], (mc3_u32)m, (mc3_u32)vel);
    if ((s->follow_count++ % 150u) == 0u) {
        // opponent x,z and y*16, then the player's instance after the move
        const float *mine = (const float *)(word(car + 0x18u) + 0x10u);
        mark('R','F','O','L', (mc3_u32)(int)opp[9], (mc3_u32)(int)opp[11]);
        mark('R','F','O','Y', (mc3_u32)(int)(opp[10] * ffrom(0x41800000u)),
             (mc3_u32)(int)(mine[10] * ffrom(0x41800000u)));
        mark('R','F','P','L', (mc3_u32)(int)mine[9], (mc3_u32)(int)mine[11]);
    }
}
static void spectate_scan() {
    State *s = st();
    s->spec_count = 0;
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    const mc3_u32 mine = ptr(player) && ptr(word(player + 20u)) ? word(word(player + 20u) + 0x18u) : 0u;
    const mc3_u32 alloc = mc3_heap_active();
    if (!mc3_ptr_ok(alloc)) return;
    const mc3_u32 base = word(alloc + 4u), top = word(alloc + 8u);
    if (!ptr(base) || top > 0x02000000u) return;
    for (mc3_u32 a = base & ~15u; a < top && s->spec_count < 16u; a += 16u)
        if (word(a) == CAR_VTABLE && a != mine) s->spec_inst[s->spec_count++] = a;
    mark('R','S','P','C', s->spec_count, s->spec_current);
}
// Diagnostic, every 2 s: the watched car's aiOpponent (aiOpponentManager::
// sm_Instance 0x618B68: count +0, array +8, 1136 bytes each; opponent+36 ->
// car, car+24 -> instance), its aiStuck (opponent+116: state +8, count +20,
// really-stuck flag +92) and aiBrain (opponent+120: state object +8 -> type
// +8), with the car's position and up.y. Markers RAIS / RAIP / RAIU.
enum { AI_MANAGER = 0x00618B68, AI_OPPONENT_SIZE = 1136 };
static void spectate_ai_state(mc3_u32 inst) {
    const mc3_u32 mgr = word(AI_MANAGER);
    if (!ptr(mgr)) return;
    const mc3_u32 n = word(mgr), arr = word(mgr + 8u);
    if (!ptr(arr) || n > 16u) return;
    for (mc3_u32 i = 0; i < n; ++i) {
        const mc3_u32 opp = arr + i * AI_OPPONENT_SIZE;
        const mc3_u32 car = word(opp + 36u);
        if (!ptr(car) || word(car + 24u) != inst) continue;
        const mc3_u32 stuck = word(opp + 116u), brain = word(opp + 120u);
        const mc3_u32 st_state = ptr(stuck) ? word(stuck + 8u) : 0xEEu;
        const mc3_u32 st_count = ptr(stuck) ? word(stuck + 20u) : 0xEEu;
        const mc3_u32 st_really = ptr(stuck) ? (mc3_u32)*(const mc3_u8 *)(stuck + 92u) : 0xEEu;
        const mc3_u32 bstate = ptr(brain) ? word(brain + 8u) : 0xEEu;
        const float *m = (const float *)(inst + 0x10u);
        mark('R','A','I','S', (i << 24) | (st_state << 16) | (st_count << 8) | st_really, bstate);
        mark('R','A','I','P', (mc3_u32)(int)m[9], (mc3_u32)(int)m[11]);
        mark('R','A','I','U', (mc3_u32)(int)(m[4] * ffrom(0x42C80000u)),
             (mc3_u32)(int)(m[10] * ffrom(0x41800000u)));
        return;
    }
    mark('R','A','I','S', 0xFFFFFFFFu, n);
}
// Record every opponent at the same 2 s interval. This catches stalls while
// the spectator camera is watching a different car. RAAS packs opponent index,
// aiStuck state/count/flag; RAAP is X/Z; RAAU is upright Y and height*16.
static void spectate_all_ai_states() {
    const mc3_u32 mgr = word(AI_MANAGER);
    if (!ptr(mgr)) return;
    const mc3_u32 n = word(mgr), arr = word(mgr + 8u);
    if (!ptr(arr) || n > 16u) return;
    for (mc3_u32 i = 0; i < n; ++i) {
        const mc3_u32 opp = arr + i * AI_OPPONENT_SIZE;
        const mc3_u32 car = word(opp + 36u);
        const mc3_u32 inst = ptr(car) ? word(car + 24u) : 0u;
        if (!ptr(inst) || word(inst) != CAR_VTABLE) continue;
        const mc3_u32 stuck = word(opp + 116u);
        const mc3_u32 st_state = ptr(stuck) ? word(stuck + 8u) : 0xEEu;
        const mc3_u32 st_count = ptr(stuck) ? word(stuck + 20u) : 0xEEu;
        const mc3_u32 st_really = ptr(stuck) ? (mc3_u32)*(const mc3_u8 *)(stuck + 92u) : 0xEEu;
        const float *m = (const float *)(inst + 0x10u);
        mark('R','A','A','S', (i << 24) | (st_state << 16) | (st_count << 8) | st_really, 0u);
        mark('R','A','A','P', (mc3_u32)(int)m[9], (mc3_u32)(int)m[11]);
        mark('R','A','A','U', (mc3_u32)(int)(m[4] * ffrom(0x42C80000u)),
             (mc3_u32)(int)(m[10] * ffrom(0x41800000u)));
        // What the AI is trying to do: aiOpponent+152 = aiTarget position (the
        // point the steering aims at), +232 = its speed (m/s); aiBrain+168 =
        // the brain's target position, +228 = the speed it asks for. Integers
        // in metres (speeds x 10). RAAT <opp | target x, z> <brain target x, z>,
        // RAAV <speed*10 | wanted*10> <target distance*10>.
        const mc3_u32 brain = word(opp + 120u);
        if (ptr(brain)) {
            const float *t = (const float *)(opp + 152u), *bt = (const float *)(brain + 168u);
            const float dx = t[0] - m[9], dz = t[2] - m[11];
            mark('R','A','A','T', (i << 24) | (((mc3_u32)(int)t[0] & 0xFFFu) << 12) | ((mc3_u32)(int)t[2] & 0xFFFu),
                 (((mc3_u32)(int)bt[0] & 0xFFFFu) << 16) | ((mc3_u32)(int)bt[2] & 0xFFFFu));
            mark('R','A','A','V', (((mc3_u32)(int)(fword((const mc3_u32 *)(opp + 232u)) * ffrom(0x41200000u)) & 0xFFFFu) << 16) |
                 ((mc3_u32)(int)(fword((const mc3_u32 *)(brain + 228u)) * ffrom(0x41200000u)) & 0xFFFFu),
                 (mc3_u32)(int)(fsqrt(dx * dx + dz * dz) * ffrom(0x41200000u)));
            // The path point the brain is heading for: aiBrain+160 -> aiPathGraph,
            // +12 -> aiPathPoint (u16 road/intersection id at +0), and that
            // road's centre (aiRoad +8, +12 = x, z). RAAG <car | road id>
            // <centre x | centre z> (16 bit each; id 0xFFFF = none).
            const mc3_u32 graph = word(brain + 160u);
            const mc3_u32 point = ptr(graph) ? word(graph + 12u) : 0u;
            const mc3_u32 road = ptr(point) ? *(const mc3_u16 *)point : 0xFFFFu;
            const mc3_u32 net = word(0x0061B170u);
            const mc3_u32 roads = ptr(net) ? word(net + 44u) : 0u;
            const mc3_u32 nroads = ptr(net) ? (mc3_u32)*(const mc3_u16 *)(net + 40u) : 0u;
            mc3_u32 pos = 0u;
            if (ptr(roads) && road < nroads) {
                const float *c = (const float *)(roads + road * 52u + 8u);
                pos = (((mc3_u32)(int)c[0] & 0xFFFFu) << 16) | ((mc3_u32)(int)c[1] & 0xFFFFu);
            }
            mark('R','A','A','G', (i << 24) | road, pos);
        }
    }
}
static void look_at(mc3_u32 camera, const float *eye, const float *target);
static void spectate(mc3_u32 camera, int seconds) {
    State *s = st();
    if (!ptr(camera)) return;
    if (spectate_traffic()) {
        const mc3_u32 k = s->spec_frame++ / ((mc3_u32)seconds * 30u);
        float p[3];
        mc3_u32 rail, ped;
        const int placed = traffic_car(k, p, &rail, &ped);
        if ((s->spec_frame % ((mc3_u32)seconds * 30u)) == 1u) {
            mark('R','S','P','T', (mc3_u32)placed, k);
            mark('R','S','P','A', (mc3_u32)placed, ped);
            const mc3_u32 net = word(0x0061B170u);
            const mc3_u32 count = ptr(net) ? *(volatile mc3_u16 *)(net + 56u) : 0u;
            const mc3_u32 rails = ptr(net) ? word(net + 60u) : 0u;
            const mc3_u32 record = ptr(rails) && rail < count ? rails + rail * 20u : 0u;
            const mc3_u32 width = ptr(record) ? *(volatile mc3_u16 *)(record + 10u) : 0u;
            const mc3_u32 flags = ptr(record) ? *(volatile mc3_u8 *)(record + 18u) : 0u;
            mark('R','S','P','R', rail, (flags << 16) | width);
        }
        if (!placed) return;
        if (spectate_traffic_follow()) {
            float m[12];
            for (int q = 0; q < 12; ++q) m[q] = ffrom(0u);
            m[0] = m[4] = m[8] = ffrom(0x3F800000u);
            m[9] = p[0]; m[10] = p[1]; m[11] = p[2];
            follow(m);                                   // the player 30 m above it
        }
        float eye[3], at[3];
        eye[0] = p[0] - ffrom(0x40C00000u); eye[1] = p[1] + ffrom(0x40800000u); eye[2] = p[2] - ffrom(0x40C00000u);
        at[0] = p[0]; at[1] = p[1] + ffrom(0x3F800000u); at[2] = p[2];
        look_at(camera, eye, at);
        return;
    }
    const mc3_u32 every = (mc3_u32)seconds * 30u;
    if (!s->spec_count || (s->spec_frame % every) == 0u ||
        word(s->spec_inst[s->spec_current % (s->spec_count ? s->spec_count : 1u)]) != CAR_VTABLE) {
        if ((s->spec_frame % every) == 0u && s->spec_frame) ++s->spec_current;
        spectate_scan();
    }
    ++s->spec_frame;
    if (!s->spec_count) return;
    const float *car = (const float *)(s->spec_inst[s->spec_current % s->spec_count] + 0x10u);
    if (spectate_follow()) follow(car);
    if (spectate_chase()) {
        follow_at(car, ffrom(0x3E99999Au), ffrom(0x41600000u));   // 0.3 m up, 14 m back
        if ((s->spec_frame % 60u) == 1u) {
            spectate_ai_state(s->spec_inst[s->spec_current % s->spec_count]);
            spectate_all_ai_states();
        }
        return;                                            // the game's camera draws
    }
    if ((s->spec_frame % 60u) == 1u) {
        spectate_ai_state(s->spec_inst[s->spec_current % s->spec_count]);
        spectate_all_ai_states();
    }
    // car rows: right, up, back (the car drives along -back), position
    const float fx = -car[6], fz = -car[8];
    const float fl = fsqrt(fx * fx + fz * fz);
    if (fl < ffrom(0x3C23D70Au)) return;
    const float ex = car[9] - fx / fl * ffrom(0x40E00000u);          // 7 m behind
    const float ey = car[10] + ffrom(0x40200000u);                    // 2.5 m up
    const float ez = car[11] - fz / fl * ffrom(0x40E00000u);
    const float tx = car[9], ty = car[10] + ffrom(0x3F4CCCCDu), tz = car[11];
    float bx = ex - tx, by = ey - ty, bz = ez - tz;                   // back = eye - target
    float bl = fsqrt(bx * bx + by * by + bz * bz);
    bx /= bl; by /= bl; bz /= bl;
    // right = up_world x back, up = back x right
    float rx = bz, ry = ffrom(0u), rz = -bx;
    const float rl = fsqrt(rx * rx + rz * rz);
    rx /= rl; rz /= rl;
    const float ux = by * rz - bz * ry, uy = bz * rx - bx * rz, uz = bx * ry - by * rx;
    float *m = (float *)camera;
    m[0] = rx; m[1] = ry; m[2] = rz;
    m[3] = ux; m[4] = uy; m[5] = uz;
    m[6] = bx; m[7] = by; m[8] = bz;
    m[9] = ex; m[10] = ey; m[11] = ez;
}


// ---------------------------------------------------------------------------
// [boot] mc2t = N (diagnostic): camera tour. host0:/mc2_camtour.txt holds one
// point per line, "eye_x eye_y eye_z target_x target_y target_z" (a line
// starting with '#' is skipped); the camera shows each for N seconds, in
// order, and starts over. One boot photographs any list of places - the
// ground-coverage clusters of mc2_ground_coverage.py, a grid over the city.
// A target straight below the eye looks down with +x right, -z up. Marker
// RTOU <index> <eye x|z as int16> at every switch (match to screenshots).
// ---------------------------------------------------------------------------
static __attribute__((noinline)) const char *tour_path() {
    static const char s[] = "host0:/mc2_camtour.txt"; return s;
}
static int tour_seconds() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','t'));
    if (!v || v[0] < '1' || v[0] > '9') return 0;
    int n = 0;
    while (*v >= '0' && *v <= '9') n = n * 10 + (*v++ - '0');
    return n;
}
static void tour_load() {
    State *s = st();
    s->tour_state = 2u;
    mc3_u32 bytes, mem;
    const mc3_u32 text = read_whole(tour_path(), 0x40000u, &bytes, &mem);
    if (!text) { mark('R','T','O','E', 1u, 0u); return; }
    const char *p = (const char *)text, *end = p + bytes;
    while (p < end && s->tour_count < MAX_TOUR_POINTS) {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) ++p;
        if (p >= end) break;
        if (*p != '#') {
            float *t = s->tour[s->tour_count];
            for (int k = 0; k < 6; ++k) t[k] = parse_float(&p);
            ++s->tour_count;
        }
        while (p < end && *p != '\n') ++p;
    }
    r_free((void *)mem);
    mark('R','T','O','L', s->tour_count, bytes);
}
static void look_at(mc3_u32 camera, const float *eye, const float *target) {
    float bx = eye[0] - target[0], by = eye[1] - target[1], bz = eye[2] - target[2];
    const float bl = fsqrt(bx * bx + by * by + bz * bz);
    if (bl < ffrom(0x3C23D70Au)) return;
    bx /= bl; by /= bl; bz /= bl;
    float rx = bz, ry = ffrom(0u), rz = -bx;
    float rl = fsqrt(rx * rx + rz * rz);
    if (rl < ffrom(0x3A83126Fu)) { rx = ffrom(0x3F800000u); rz = ffrom(0u); rl = rx; }
    rx /= rl; rz /= rl;
    const float ux = by * rz - bz * ry, uy = bz * rx - bx * rz, uz = bx * ry - by * rx;
    float *m = (float *)camera;
    m[0] = rx; m[1] = ry; m[2] = rz;
    m[3] = ux; m[4] = uy; m[5] = uz;
    m[6] = bx; m[7] = by; m[8] = bz;
    m[9] = eye[0]; m[10] = eye[1]; m[11] = eye[2];
}
static void camera_tour(mc3_u32 camera, int seconds) {
    State *s = st();
    if (!ptr(camera)) return;
    if (!s->tour_state) tour_load();
    if (!s->tour_count) return;
    const mc3_u32 i = (s->tour_frame++ / ((mc3_u32)seconds * 30u)) % s->tour_count;
    const float *t = s->tour[i];
    if (i != s->tour_shown || s->tour_frame == 1u) {
        s->tour_shown = i;
        mark('R','T','O','U', i, (((mc3_u32)(int)t[0] & 0xFFFFu) << 16) |
                                 ((mc3_u32)(int)t[2] & 0xFFFFu));
    }
    look_at(camera, t, t + 3);
}


// ---------------------------------------------------------------------------
// [boot] mc2g = F (diagnostic): collision drop test. host0:/mc2_droptest.txt
// holds "x ground_y z [vx vz]" per line ('#' skipped; with a velocity the car
// is thrown sideways - a wall test; mc2_ground_coverage.py writes
// it). Once the city is loaded, the player's car is put 1.5 m above each point
// (upright, still) with aiBrain::TeleportCar as in follow(), left to fall for
// F frames, and where it ended up is logged: a car that went through the road
// ends far below ground_y (or wherever the game respawns it). The camera
// watches each drop from 20 m away. Markers RDRP <index> <final y * 16>,
// RDRX <final x> <final z>, RDRE <points> when done (then the tour stops).
// mc2g = Fd ("drive"): the car is put at each point FACING its velocity and
// thrown once at it, the game's camera is left alone - a way to start a drive
// anywhere (frame pacing on a given street, 2026-09-30).
// ---------------------------------------------------------------------------
static __attribute__((noinline)) const char *drop_path() {
    static const char s[] = "host0:/mc2_droptest.txt"; return s;
}
static int drop_frames() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','g'));
    if (!v || v[0] < '1' || v[0] > '9') return 0;
    int n = 0;
    while (*v >= '0' && *v <= '9') n = n * 10 + (*v++ - '0');
    return n < 10 ? 10 : n;
}
static void drop_load() {
    State *s = st();
    s->drop_state = 1u;
    mc3_u32 bytes, mem;
    const mc3_u32 text = read_whole(drop_path(), 0x100000u, &bytes, &mem);
    if (!text) { mark('R','D','R','E', 0u, 1u); s->drop_state = 3u; return; }
    mc3_u32 lines = 0;
    for (mc3_u32 i = 0; i < bytes; ++i) if (((const char *)text)[i] == '\n') ++lines;
    float *pts = (float *)r_alloc((lines + 1u) * 20u + 16u);
    if (!pts) { r_free((void *)mem); mark('R','D','R','E', 0u, 2u); s->drop_state = 3u; return; }
    s->drop_mem = (mc3_u32)pts;
    s->drop_points = ((mc3_u32)pts + 15u) & ~15u;
    float *out = (float *)s->drop_points;
    const char *p = (const char *)text, *end = p + bytes;
    s->drop_count = 0;
    while (p < end && s->drop_count <= lines) {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) ++p;
        if (p >= end) break;
        if (*p != '#') {
            // x y z [vx vz]: parse_float stops at the end of the line, so
            // missing values read 0
            float *t = out + s->drop_count * 5u;
            for (int k = 0; k < 5; ++k) t[k] = parse_float(&p);
            ++s->drop_count;
        }
        while (p < end && *p != '\n') ++p;
    }
    r_free((void *)mem);
    mark('R','D','R','L', s->drop_count, bytes);
    s->drop_state = 2u;
}
static int drop_drive() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','g'));
    while (v && *v >= '0' && *v <= '9') ++v;
    return v && *v == 'd';
}
static void drop_test(mc3_u32 camera, int frames) {
    State *s = st();
    const int drive = drop_drive();
    if (!s->drop_state) {
        if (!load_finished()) return;           // the whole city first
        drop_load();
    }
    if (s->drop_state != 2u) return;
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    if (!ptr(player) || !ptr(word(player + 20u))) return;
    const mc3_u32 car = word(player + 20u);
    if (!ptr(word(car + 0x18u))) return;
    const mc3_u32 i = s->drop_frame / (mc3_u32)frames, f = s->drop_frame % (mc3_u32)frames;
    if (i >= s->drop_count) {
        mark('R','D','R','E', s->drop_count, 0u);
        s->drop_state = 3u;
        return;
    }
    ++s->drop_frame;
    const float *t = (const float *)s->drop_points + i * 5u;
    const float *mine = (const float *)(word(car + 0x18u) + 0x10u);
    if (f == 0u) {
        float m[12], vel[4];
        for (int k = 0; k < 12; ++k) m[k] = ffrom(0u);
        m[0] = m[4] = m[8] = ffrom(0x3F800000u);
        m[9] = t[0]; m[10] = t[1] + ffrom(0x3FC00000u); m[11] = t[2];   // 1.5 m up
        const float speed = fsqrt(t[3] * t[3] + t[4] * t[4]);
        if (drive && speed > ffrom(0x3F800000u)) {
            // forward is -row 2: row 2 = -dir, row 0 = row 1 x row 2
            const float dx = t[3] / speed, dz = t[4] / speed;
            m[0] = -dz; m[1] = ffrom(0u); m[2] = dx;
            m[6] = -dx; m[7] = ffrom(0u); m[8] = -dz;
        }
        vel[0] = t[3]; vel[1] = ffrom(0u); vel[2] = t[4]; vel[3] = ffrom(0u);
        s->drop_opp[9] = car;
        s->drop_brain[3] = (mc3_u32)&s->drop_opp[0];
        MC3_CALL3(void, AI_TELEPORT_CAR, mc3_u32, mc3_u32, mc3_u32)
            ((mc3_u32)&s->drop_brain[0], (mc3_u32)m, (mc3_u32)vel);
    } else if (!drive && (t[3] != ffrom(0u) || t[4] != ffrom(0u)) && f + 6u < (mc3_u32)frames) {
        // wall test: keep pushing at the same speed (the car's own drag
        // stops a single throw within 2-5 m); phInertialCS::SetVelocity on
        // *(*(car+4)+104)+12, the body aiBrain::TeleportCar gives a velocity
        const mc3_u32 sim = word(car + 4u);
        const mc3_u32 inertial = ptr(sim) && ptr(word(sim + 104u)) ? word(word(sim + 104u) + 12u) : 0u;
        if (ptr(inertial)) {
            float vel[4];
            vel[0] = t[3]; vel[1] = ffrom(0u); vel[2] = t[4]; vel[3] = ffrom(0u);
            MC3_CALL2(void, INERTIAL_SET_VELOCITY, mc3_u32, mc3_u32)(inertial, (mc3_u32)vel);
        }
    } else if (f == (mc3_u32)frames - 1u) {
        mark('R','D','R','P', i, (mc3_u32)(int)(mine[10] * ffrom(0x41800000u)));
        mark('R','D','R','X', (mc3_u32)(int)mine[9], (mc3_u32)(int)mine[11]);
    }
    if (ptr(camera) && !drive) {
        float eye[3], at[3];
        at[0] = t[0]; at[1] = t[1]; at[2] = t[2];
        eye[0] = t[0]; eye[1] = t[1] + ffrom(0x41A00000u); eye[2] = t[2] - ffrom(0x41A00000u);
        look_at(camera, eye, at);
    }
}


// ---------------------------------------------------------------------------
// Player pins: marking problems while driving. USB keyboard (the snapshot
// freecam reads, 0x6F8EC8; the game polls it), keys on the number row so
// they do not clash with freecam:
//     1  missing texture   (drawn as MC2's orange cone)
//     2  missing collision (a stop sign)
//     3  anything else     (a freeway barrel)
//     Shift + 1/2/3: on the wall the car faces - 2.6 m ahead, 0.8 m up;
//     without Shift: on the ground 4 m ahead
//     Backspace: remove the last pin
// Every change rewrites the city's pins file (host0:/mc2_pins.txt for LA,
// host0:/mc2_pins_paris.txt for Paris; one pin per line: index,
// category, ground|wall, pin xyz, car forward xz, car xyz) and the table in
// State starts with "MC2PINS1", so a savestate holds them too. The pins are
// drawn with the MC2 prop of their category. Marker RPIN <index|cat<<16|wall<<24> <count>.
// ---------------------------------------------------------------------------
static const char *pins_path() { return mc2_city_pins(active_city()); }
static __attribute__((noinline)) const char *pins_head() {
    static const char s[] = "# mc2 pins: index category(1 texture 2 collision 3 other) ground|wall pin_x pin_y pin_z forward_x forward_z car_x car_y car_z\n";
    return s;
}
static __attribute__((noinline)) const char *word_wall() { static const char s[] = " wall"; return s; }
static __attribute__((noinline)) const char *word_ground() { static const char s[] = " ground"; return s; }
static const char *pin_prop(mc3_u32 category) {
    return mc2_city_pin_prop(active_city(), category);
}
enum { KEY_1 = 0x1E, KEY_2 = 0x1F, KEY_3 = 0x20, KEY_BACKSPACE = 0x2A };
// bit k-1 for keys 1..3, bit 3 backspace, bit 4 shift
static mc3_u32 pin_keys_held() {
    if (!*(volatile mc3_u8 *)0x0062314Eu || !*(volatile mc3_u8 *)0x00615A38u ||
        !*(volatile mc3_u8 *)0x00615A39u)
        return 0u;
    const mc3_u32 count = word(0x006F8ED0u);
    if (count > 64u) return 0u;
    const volatile mc3_u16 *keys = (const volatile mc3_u16 *)0x006F8ED4u;
    mc3_u32 held = (word(0x006F8ECCu) & 0x22u) ? 16u : 0u;
    for (mc3_u32 i = 0; i < count; ++i) {
        const mc3_u32 k = keys[i];
        if (k == KEY_1) held |= 1u;
        else if (k == KEY_2) held |= 2u;
        else if (k == KEY_3) held |= 4u;
        else if (k == KEY_BACKSPACE) held |= 8u;
    }
    return held;
}
static int put_text(char *out, int at, const char *t) { while (*t) out[at++] = *t++; return at; }
static int put_int(char *out, int at, int v) {
    if (v < 0) { out[at++] = '-'; v = -v; }
    char tmp[12]; int n = 0;
    do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (n) out[at++] = tmp[--n];
    return at;
}
static int put_fixed(char *out, int at, float f) {      // one decimal
    int t = (int)(f * ffrom(0x41200000u) + (f < ffrom(0u) ? ffrom(0xBF000000u) : ffrom(0x3F000000u)));
    if (t < 0) { out[at++] = '-'; t = -t; }
    at = put_int(out, at, t / 10);
    out[at++] = '.';
    out[at++] = (char)('0' + t % 10);
    return at;
}
static void pins_save() {
    State *s = st();
    const mc3_u32 h = MC3_CALL1(mc3_u32, STREAM_CREATE, const char *)(pins_path());
    if (!h) { mark('R','P','I','E', 1u, s->pin_count); return; }
    char line[160];
    {
        const char *head = pins_head();
        int n = 0;
        while (head[n]) ++n;
        MC3_CALL3(int, STREAM_WRITE, mc3_u32, const void *, int)(h, head, n);
    }
    for (mc3_u32 i = 0; i < s->pin_count; ++i) {
        const Pin *pn = &s->pins[i];
        int n = put_int(line, 0, (int)i);
        line[n++] = ' ';
        n = put_int(line, n, (int)pn->category);
        n = put_text(line, n, pn->wall ? word_wall() : word_ground());
        const float v[8] = { pn->pos[0], pn->pos[1], pn->pos[2], pn->forward[0], pn->forward[2],
                             pn->car[0], pn->car[1], pn->car[2] };
        for (int k = 0; k < 8; ++k) { line[n++] = ' '; n = put_fixed(line, n, v[k]); }
        line[n++] = '\n';
        MC3_CALL3(int, STREAM_WRITE, mc3_u32, const void *, int)(h, line, n);
    }
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
    s->pin_saved = s->pin_count;
}
// The pins of earlier sessions: host0:/mc2_pins.txt as pins_save writes it.
static void pins_load() {
    State *s = st();
    mc3_u32 bytes, mem;
    const mc3_u32 text = read_whole(pins_path(), 0x40000u, &bytes, &mem);
    s->pin_count = 0;
    if (!text) return;
    const char *p = (const char *)text, *end = p + bytes;
    while (p < end && s->pin_count < MAX_PINS) {
        while (p < end && (*p == ' ' || *p == '\r' || *p == '\n')) ++p;
        if (p >= end) break;
        if (*p != '#') {
            Pin *pn = &s->pins[s->pin_count];
            parse_float(&p);                                      // index
            pn->category = (mc3_u32)(int)parse_float(&p);
            while (*p == ' ') ++p;
            pn->wall = *p == 'w';
            while (p < end && *p != ' ' && *p != '\n') ++p;
            float v[8];
            for (int k = 0; k < 8; ++k) v[k] = parse_float(&p);
            pn->pos[0] = v[0]; pn->pos[1] = v[1]; pn->pos[2] = v[2];
            pn->forward[0] = v[3]; pn->forward[1] = ffrom(0u); pn->forward[2] = v[4];
            pn->car[0] = v[5]; pn->car[1] = v[6]; pn->car[2] = v[7];
            if (pn->category >= 1u && pn->category <= 3u) ++s->pin_count;
        }
        while (p < end && *p != '\n') ++p;
    }
    r_free((void *)mem);
    mark('R','P','I','L', s->pin_count, bytes);
}
// the pin's matrix (MC2 row convention: right, up, forward, position)
static void pin_matrix(mc3_u32 i) {
    State *s = st();
    const Pin *pn = &s->pins[i];
    float *m = (float *)(s->pin_mats + i * 64u);
    const float fx = pn->forward[0], fz = pn->forward[2];
    m[0] = fz;         m[1] = ffrom(0u); m[2] = -fx;  m[3] = ffrom(0u);
    m[4] = ffrom(0u);  m[5] = ffrom(0x3F800000u); m[6] = ffrom(0u); m[7] = ffrom(0u);
    m[8] = fx;         m[9] = ffrom(0u); m[10] = fz;  m[11] = ffrom(0u);
    m[12] = pn->pos[0]; m[13] = pn->pos[1]; m[14] = pn->pos[2]; m[15] = ffrom(0x3F800000u);
}
static void pins_update() {
    State *s = st();
    if (!s->pin_mats) {
        mc3_u8 *mem = (mc3_u8 *)r_alloc(MAX_PINS * 64u + 16u);
        if (!mem) return;
        s->pin_mem = (mc3_u32)mem;
        s->pin_mats = ((mc3_u32)mem + 15u) & ~15u;
        s->pin_magic[0] = 'M'; s->pin_magic[1] = 'C'; s->pin_magic[2] = '2'; s->pin_magic[3] = 'P';
        s->pin_magic[4] = 'I'; s->pin_magic[5] = 'N'; s->pin_magic[6] = 'S'; s->pin_magic[7] = '1';
        pins_load();
        for (mc3_u32 i = 0; i < s->pin_count; ++i) pin_matrix(i);
    }
    const mc3_u32 held = pin_keys_held();
    const mc3_u32 pressed = held & ~s->pin_keys;
    s->pin_keys = held;
    if (pressed & 8u) {
        if (s->pin_count) --s->pin_count;
        pins_save();
        mark('R','P','I','N', 0xFFFFFFFFu, s->pin_count);
        return;
    }
    const mc3_u32 category = (pressed & 1u) ? 1u : (pressed & 2u) ? 2u : (pressed & 4u) ? 3u : 0u;
    if (!category || s->pin_count >= MAX_PINS) return;
    const mc3_u32 player = MC3_CALL1(mc3_u32, PLAYER_GET, int)(0);
    if (!ptr(player) || !ptr(word(player + 20u)) || !ptr(word(word(player + 20u) + 0x18u))) return;
    const float *car = (const float *)(word(word(player + 20u) + 0x18u) + 0x10u);
    float fx = -car[6], fz = -car[8];                // the car drives along -back
    const float fl = fsqrt(fx * fx + fz * fz);
    if (fl < ffrom(0x3C23D70Au)) return;
    fx /= fl; fz /= fl;
    Pin *pn = &s->pins[s->pin_count];
    pn->category = category;
    pn->wall = (held & 16u) ? 1u : 0u;
    const float ahead = pn->wall ? ffrom(0x40266666u) : ffrom(0x40800000u);   // 2.6 / 4 m
    pn->pos[0] = car[9] + fx * ahead;
    pn->pos[1] = car[10] + (pn->wall ? ffrom(0x3F4CCCCDu) : ffrom(0u));      // 0.8 m up
    pn->pos[2] = car[11] + fz * ahead;
    pn->forward[0] = fx; pn->forward[1] = ffrom(0u); pn->forward[2] = fz;
    pn->car[0] = car[9]; pn->car[1] = car[10]; pn->car[2] = car[11];
    pin_matrix(s->pin_count);
    ++s->pin_count;
    pins_save();
    mark('R','P','I','N', (s->pin_count - 1u) | (category << 16) | (pn->wall << 24), s->pin_count);
    {
        const mc3_u32 type = find_prop_type(pin_prop(category));
        const PropType *t = type == NO_IMAGE ? 0 : &s->prop_types[type];
        mark('R','P','I','T', type, t ? (t->ok << 24) | (t->packets << 8) | (mc3_u32)(int)t->radius : 0u);
    }
}
static void queue_pins(mc3_u32 arena) {
    State *s = st();
    if (!s->pin_mats || !s->props_ready) return;
    for (mc3_u32 i = 0; i < s->pin_count; ++i) {
        const Pin *pn = &s->pins[i];
        const mc3_u32 type = find_prop_type(pin_prop(pn->category));
        if (type == NO_IMAGE || !s->prop_types[type].ok) continue;
        const PropType *t = &s->prop_types[type];
        if (!sphere_visible(pn->pos[0], pn->pos[1], pn->pos[2], t->radius + ffrom(0x3F800000u))) continue;
        mc3_u32 need = 0, last = NO_IMAGE;
        for (mc3_u32 k = 0; k < t->packets; ++k) {
            if (t->pk_slot[k] != last && !image_resident(t->pk_slot[k])) need += upload_bytes(t->pk_slot[k]);
            last = t->pk_slot[k];
        }
        if (!arena_can_take(need)) { ++s->frame_skipped; continue; }
        queue_ref(s->pin_mats + i * 64u, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
        for (mc3_u32 k = 0; k < t->packets; ++k) {
            const mc3_u32 sw = use_image(t->pk_slot[k], arena);
            if (sw) bind_switch(sw);
            queue_ref(t->pk_addr[k], t->pk_qwc[k], 0u, 0u);
        }
    }
}

// aiOpponent::Update (0x4034A0) starts with a fell-out-of-the-world test: a car
// lower than the aiRouter's min y - 10 skips the whole AI and its brain puts it
// back on the path, every frame. The router (aiRailNetwork::sm_pinstance
// 0x61B170, +88) is never loaded in game - its box stays 0 - so the floor is
// y = -10, and LA has road below that: the PCH under the Santa Monica bluffs
// is at y = -16. All opponents that took the California Incline stayed glued
// to the shortcut node at (-826, -16, -1170) for the rest of the race. The
// box's min y is set to MC2's own (losangeles.aib, aiRouter tag 0x4205: min
// y -16.31), so the floor is -26.3 like in MC2. Marker RFLR <old> <new>.
enum { RAIL_NETWORK = 0x0061B170 };
static void fix_ai_floor() {
    const mc3_u32 target = mc2_city_router_min_y(active_city());
    const mc3_u32 rail = word(RAIL_NETWORK);
    const mc3_u32 router = ptr(rail) ? word(rail + 88u) : 0u;
    if (!target || !ptr(router) || word(router + 4u) == target) return;
    mark('R','F','L','R', word(router + 4u), target);
    *(volatile mc3_u32 *)(router + 4u) = target;
}

// Diagnostic: what MC3's occluder culling decided (mcGame::PreDraw 0x1A14B0 ->
// rmcCarModel::CullUpdate 0x2F6278 stores IsAABoxOccluded == 0 at model+64 for
// view 0) and from where: the viewer is the local player's camera matrix
// (player+108 -> +8 -> +0x30 -> +0x40 or +0x10 by the byte at +0x7C), NOT the
// camera this renderer is handed - so in the spectator modes the occlusion is
// seen from the player's own car. Every 30 frames:
//   ROCC <racer << 24 | visible flag> <car x << 16 | z (whole metres)>
//   ROCV <viewer x> <viewer z> (floats), ROCY <viewer y> <racer count>
enum { PLAYER_MANAGER = 0x0061B1E0 };
static int spectate_seconds();
static void occ_probe() {
    const mc3_u32 mgr = word(PLAYER_MANAGER);
    if (!ptr(mgr)) return;
    const mc3_u32 n = word(mgr + 40u);
    for (mc3_u32 i = 0; i < n && i < 8u; ++i) {
        const mc3_u32 p = word(mgr + 8u + 4u * i);
        if (!ptr(p)) continue;
        const mc3_u32 car = word(p + 20u);
        const mc3_u32 model = ptr(car) ? word(car + 8u) : 0u;
        const mc3_u32 mat = ptr(car) ? word(car + 24u) : 0u;
        if (!ptr(model) || !ptr(mat)) continue;
        const float *m = (const float *)mat;
        mark('R','O','C','C', (i << 24) | (word(model + 64u) & 0xFFFFFFu),
             (((mc3_u32)(int)m[13] & 0xFFFFu) << 16) | ((mc3_u32)(int)m[15] & 0xFFFFu));
    }
    const mc3_u32 p0 = word(mgr + 8u);
    const mc3_u32 cam = ptr(p0) ? word(p0 + 108u) : 0u;
    const mc3_u32 a = ptr(cam) ? word(cam + 8u) : 0u;
    const mc3_u32 v1 = ptr(a) ? word(a + 0x30u) : 0u;
    if (!ptr(v1)) return;
    const mc3_u32 view = *(volatile mc3_u8 *)(v1 + 0x7Cu) ? v1 + 0x40u : v1 + 0x10u;
    mark('R','O','C','V', word(view + 36u), word(view + 44u));
    mark('R','O','C','Y', word(view + 40u), n);
}

// The city is drawn here, into MC3's packet stream at this point of the frame.
// Where MC3's light reflections may show. MC3 draws them (headlights, traffic,
// city lights, sun/moon) into the city env map's target and pastes that with
// ALPHA 0x58 = Cs * Ad + Cd (mcCityEnvMap::CopyReflectedObjectsToFrame): the
// FRAME ALPHA is the reflectivity of each pixel. MC2 draws with alpha 0x80
// everywhere, so every wall, prop and leaf was a perfect mirror. After the city
// and the props: the whole frame's alpha is cleared to 0 (a sprite, alpha only,
// no depth test or write), then the `_0_refl` ground is drawn again, alpha only,
// with the palette whose alpha is road_alpha(), depth-tested (GEQUAL, no Z
// write) so only the ground that is really visible reflects. Colours, blending
// and depth are untouched - an earlier try that changed the blend and the
// vertex alpha broke every cut-out texture (leaves, pylons, wires).
// FRAME as rmcState::DoFlush builds it: FBP gfxPipeline::GetFBP(0) = *0x715C7C,
// FBW *0x70E510 / 64, PSM by *0x70FAD8/*0x70FADC, FBMSK in the high word (MC3's
// current one is *0x6BE618). [boot] mc2m = 0 turns the pass off.
enum { GS_FBP = 0x00715C7C, GS_WIDTH = 0x0070E510, GS_FMT = 0x0070FAD8, GS_FMT2 = 0x0070FADC,
       GS_FBMSK = 0x006BE618 };
static int reflect_mask_enabled() {
    const char *v = mc3_bootarg(MC3_ID('m','c','2','m'));
    return !v || v[0] != '0';
}
static void frame_reg(mc3_u32 *lo, mc3_u32 *hi, mc3_u32 fbmsk) {
    const mc3_u32 fmt = word(GS_FMT);
    const mc3_u32 psm = fmt == 16u ? (word(GS_FMT2) >= 17u ? 10u : 2u) : 0u;
    *lo = word(GS_FBP) | ((word(GS_WIDTH) >> 6) << 16) | (psm << 24);
    *hi = fbmsk;
}
// A+D list: FLUSH, DIRECT, one GIF tag, then n register writes (ad_put)
static volatile mc3_u32 *ad_begin(mc3_u32 arena, mc3_u32 n) {
    State *s = st();
    const mc3_u32 bytes = 32u + n * 16u;
    if (!arena_can_take(bytes)) return 0;
    const mc3_u32 p = arena + s->arena_used;
    volatile mc3_u32 *w = (volatile mc3_u32 *)(p | UNCACHED);
    w[0] = 0u; w[1] = 0u; w[2] = VIF_FLUSH; w[3] = VIF_DIRECT | (n + 1u);
    w[4] = 0x8000u | n; w[5] = 0x10000000u; w[6] = 0xEu; w[7] = 0u;
    queue_ref(p, n + 2u, 0u, 0u);
    s->arena_used += bytes;
    return w + 8;
}
static void ad_put(volatile mc3_u32 *w, mc3_u32 k, mc3_u32 lo, mc3_u32 hi, mc3_u32 reg) {
    w[4u * k] = lo; w[4u * k + 1u] = hi; w[4u * k + 2u] = reg; w[4u * k + 3u] = 0u;
}
// The `_0_refl` ground this frame drew (later[]), once more with whatever
// palette and state are in effect.
static void redraw_ground(mc3_u32 arena) {
    State *s = st();
    for (mc3_u32 k = 0; k < s->later_count; ++k) {
        const mc3_u32 v = s->later[k];
        if (v & 0x8000u) {
            const mc3_u32 i = v & 0x7FFFu;
            queue_ref(instance_matrix(s, i), 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
            queue_pcp(s->inst_refl[i] - 1u, arena);
        } else {
            queue_ref(s->pieces[v].packet, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
            queue_pcp((s->target_word[v] & 0xFFFFu) - 1u, arena);
        }
    }
}
// One instance with its `_0_main` chain, or its pieces flat.
static void draw_instance_plain(mc3_u32 i, mc3_u32 arena) {
    State *s = st();
    const ModelDraw *model = &s->models[instance_record(s, i)[0]];
    for (mc3_u32 j = 0; j < model->count; ++j)
        if (!s->pieces[model->pieces[j]].ready) return;
    queue_ref(instance_matrix(s, i), 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
    const mc3_u32 m = s->instance_word[i];
    if ((m & 0xFFFFu) && queue_pcp((m & 0xFFFFu) - 1u, arena)) return;
    for (mc3_u32 j = 0; j < model->count; ++j)
        queue_piece(&s->pieces[model->pieces[j]], arena);
}
static void reflect_mask_pass(mc3_u32 arena) {
    State *s = st();
    if (!reflect_mask_enabled()) return;
    const mc3_u32 ctx = *(volatile mc3_u8 *)GS_CONTEXT;
    mc3_u32 flo, fhi;
    frame_reg(&flo, &fhi, 0x00FFFFFFu);                      // write alpha only
    volatile mc3_u32 *w = ad_begin(arena, 8u);
    if (!w) return;
    ad_put(w, 0, flo, fhi, 0x4Cu + ctx);                     // FRAME
    ad_put(w, 1, word(GS_ZBUF_BASE), word(GS_ZBUF_BASE + 4u) | 1u, 0x4Eu + ctx);  // no Z write
    ad_put(w, 2, 0x30000u, 0u, 0x47u + ctx);                 // TEST: Z always, no alpha test
    ad_put(w, 3, 6u | (ctx << 9), 0u, 0x00u);                // PRIM sprite
    ad_put(w, 4, 0u, 0x3F800000u, 0x01u);                    // RGBAQ, alpha 0
    ad_put(w, 5, 0u, 0u, 0x05u);                             // XYZ2 top left
    ad_put(w, 6, 0xFFFFFFFFu, 0u, 0x05u);                    // XYZ2 bottom right
    ad_put(w, 7, 0x5100Fu, 0u, 0x47u + ctx);                 // TEST as the city (GEQUAL)
    if (s->later_count) {
        queue_vcl(s->palette_refl);
        redraw_ground(arena);
        // MC2's wetness: the same ground again with the texture's alpha (TCC=1)
        // times wet_scale(), kept only where it beats the flat road_alpha
        // (alpha test GREATER, failing pixels untouched). Rainy puddles then
        // mirror, dry asphalt keeps the flat value.
        const mc3_u32 wet = wet_scale();
        if (wet && s->palette_wet) {
            w = ad_begin(arena, 1u);
            if (w) {
                ad_put(w, 0, 0x5000Du | (road_alpha() << 4), 0u, 0x47u + ctx);
                queue_vcl(s->palette_wet);
                s->force_tcc = 1u; s->bound_switch = 0u;
                redraw_ground(arena);
                s->force_tcc = 0u; s->bound_switch = 0u;   // next switch re-sends TCC as stored
                w = ad_begin(arena, 1u);
                if (w) ad_put(w, 0, 0x5100Fu, 0u, 0x47u + ctx);
            }
        }
        queue_vcl(s->palette);
    }
    // MC2's card reflections - glow cards standing under the ground below shop
    // windows - drawn the way MC2 pastes its reflections (seen in the DMA list
    // of an MC2 frame): colour added times the FRAME alpha (ALPHA 0x58 =
    // Cs * Ad + Cd), depth test ALWAYS, no Z and no alpha write. So they show
    // only through the reflective ground actually visible, never on walls.
    if (s->card_count) {
        frame_reg(&flo, &fhi, 0xFF000000u);                  // colour only
        w = ad_begin(arena, 3u);
        if (w) {
            ad_put(w, 0, flo, fhi, 0x4Cu + ctx);
            ad_put(w, 1, 0x3000Fu, 0u, 0x47u + ctx);         // alpha != 0, Z always
            ad_put(w, 2, 0x58u, 0u, 0x42u + ctx);            // Cs * Ad + Cd
            s->bound_switch = 0u;
            queue_ref((mc3_u32)s->card_vp, 4u, VIF_FLUSH, VIF_UNPACK_VIEWPROJ);
            for (mc3_u32 k = 0; k < s->card_count; ++k) draw_instance_plain(s->cards[k], arena);
            queue_ref(s->bootstrap + s->matrix_offset, 4u, VIF_FLUSH, VIF_UNPACK_VIEWPROJ);
            w = ad_begin(arena, 2u);
            if (w) {
                ad_put(w, 0, 0x44u, 0u, 0x42u + ctx);        // the city's ALPHA again
                ad_put(w, 1, 0x5100Fu, 0u, 0x47u + ctx);
            }
        }
    }
    frame_reg(&flo, &fhi, word(GS_FBMSK));                   // MC3's own mask again
    w = ad_begin(arena, 1u);
    if (w) ad_put(w, 0, flo, fhi, 0x4Cu + ctx);
}

static int is_refl_chain(mc3_u32 index) {
    const State *s = st();
    return s->refl_chain && index < MAX_CPVS_ENTRIES && (s->refl_chain[index >> 5] >> (index & 31u)) & 1u;
}
// MC2's sky layers at the camera, before anything else: depth test ALWAYS and
// no Z write, so the city and MC3's cars draw over them wherever they are.
static void queue_sky(mc3_u32 arena) {
    State *s = st();
    const mc3_u32 ctx = *(volatile mc3_u8 *)GS_CONTEXT;
    mc3_u32 need = 0, last = NO_IMAGE;
    for (mc3_u32 j = 0; j < s->sky_layers; ++j)
        for (mc3_u32 k = 0; k < s->sky_layer[j].packets; ++k) {
            const mc3_u32 image = s->sky_layer[j].slot[k];
            if (image != last && !image_resident(image)) need += upload_bytes(image);
            last = image;
        }
    if (!arena_can_take(need)) { ++s->frame_skipped; return; }
    queue_zbuf(arena, 0);
    volatile mc3_u32 *w = ad_begin(arena, 1u);
    if (w) ad_put(w, 0, 0x3000Fu, 0u, 0x47u + ctx);             // alpha != 0, Z always
    queue_ref((mc3_u32)s->sky_matrix, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
    for (mc3_u32 j = 0; j < s->sky_layers; ++j) {
        const SkyLayer *l = &s->sky_layer[j];
        for (mc3_u32 k = 0; k < l->packets; ++k) {
            const mc3_u32 sw = use_image(l->slot[k], arena);
            if (sw) bind_switch(sw);
            queue_ref(l->addr[k], l->qwc[k], 0u, 0u);
        }
    }
    w = ad_begin(arena, 1u);
    if (w) ad_put(w, 0, 0x5100Fu, 0u, 0x47u + ctx);             // TEST as the city
}
static void draw_city() {
    State *s = st();
    const int sky = s->sky_state == 2u && s->sky_layers && sky_enabled() && zwrite_enabled() &&
                    ptr(s->camera);
    if (sky) {
        for (int k = 0; k < 16; ++k) s->sky_matrix[k] = ffrom(0u);
        s->sky_matrix[0] = s->sky_matrix[5] = s->sky_matrix[10] = s->sky_matrix[15] = ffrom(0x3F800000u);
        s->sky_matrix[12] = fword((const mc3_u32 *)(s->camera + 36u));
        s->sky_matrix[13] = fword((const mc3_u32 *)(s->camera + 40u));
        s->sky_matrix[14] = fword((const mc3_u32 *)(s->camera + 44u));
    }
    const int draw_instances = s->instances_ready && instances_enabled();
    const int cpv = s->cpvs_state == 2u && s->map_ready && s->palette;
    const mc3_u32 *map_targets = s->target_word;
    const mc3_u32 *map_instances = s->instance_word;
    const mc3_u32 use_cpvs = cpv ? cpvs_mask() : 0u;
    mc3_u32 visible_count = 0, visible_instances = 0;
    // Everything the VIF will read this frame that went through the data
    // cache - matrices just rewritten, banks loaded by STREAM_READ - is pushed
    // out first. Arena packets are then written through the uncached alias,
    // so REFs can be emitted while the frame is being decided.
    const int native = world_mode();                 // boot arg: once, not per piece
    for (mc3_u32 i = 0; i < s->target_count; ++i)
        if (s->pieces[i].ready && s->targets[i].flags == 0u)
            update_model_matrix(&s->pieces[i], &s->targets[i], native);
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    __asm__ volatile("sync.l; sync.p" ::: "memory");
    const mc3_u32 arena = s->arena[s->queued & 1u];
    s->arena_used = 0; s->call_count = 0;
    {
        const mc3_u32 f = mc3_heap_free(mc3_heap_active());
        if (!s->heap_min || f < s->heap_min) s->heap_min = f;
    }
    s->frame_uploads = s->frame_upload_bytes = 0;
    s->frame_wraps = s->frame_dropped = 0;
    s->frame_pcp = s->frame_pmd = s->frame_pcp_failed = s->frame_skipped = 0;
    s->frame_props = 0;
    s->bound_switch = 0;
    s->pcp_limit = cpvs_limit();
    {   // [boot] mc2w = 1: every texture white (diagnostic)
        const char *v = mc3_bootarg(MC3_ID('m','c','2','w'));
        s->all_white = v && v[0] == '1';
    }
    s->draw_radius = draw_radius();
    mc3_u32 pc = cycles_now();
    queue_vcl(s->bootstrap);
    // the sky BEFORE the CPVS palette: its MOD0 packets unpack over the VU
    // memory the palette lives in, and drawn after it (first build of
    // 2026-09-30) the city took its vertex colours from sky vertices - patches
    // of orange/purple that changed with the camera (player's savestate)
    if (sky) queue_sky(arena);
    if (cpv) queue_vcl(s->palette);
    if (zwrite_enabled()) queue_zbuf(arena, 1);
    // `_0_refl` chains (the reflective ground) are drawn as everything else and
    // also recorded, for reflect_mask_pass. later[]: target, or instance | 0x8000.
    s->later_count = 0;
    s->card_count = 0;
    s->prof[0] += cycles_now() - pc; pc = cycles_now();
    const int split = cpv && s->palette_refl && s->later;
    const int card_pass = split && reflect_mask_enabled() && cards_enabled();
    const float card_far = card_distance();
    const float eye_x = fword((const mc3_u32 *)(s->camera + 36u));
    const float eye_y = fword((const mc3_u32 *)(s->camera + 40u));
    const float eye_z = fword((const mc3_u32 *)(s->camera + 44u));
    for (mc3_u32 i = 0; i < s->target_count; ++i) {
        Piece *piece = &s->pieces[i];
        if (!piece->ready || s->targets[i].flags != 0u) continue;
        if (!piece_visible(piece, &s->targets[i])) continue;
        const mc3_u32 m = (use_cpvs & 1u) ? map_targets[i] : 0u;
        if ((m & CPVS_COVERED) && !(m & 0xFFFFu)) continue;  // another record draws it
        ++visible_count;
        if (split && (m & 0xFFFFu) && is_refl_chain((m & 0xFFFFu) - 1u))
            s->later[s->later_count++] = (mc3_u16)i;
        queue_ref(piece->packet, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
        if (m & 0xFFFFu) {
            if (queue_pcp((m & 0xFFFFu) - 1u, arena)) continue;
            ++s->frame_pcp_failed;
        }
        queue_piece(piece, arena);
    }
    s->prof[1] += cycles_now() - pc; pc = cycles_now();
    for (mc3_u32 i = 0; draw_instances && i < s->instance_count; ++i) {
        const mc3_u32 *record = instance_record(s, i);
        const ModelDraw *model = &s->models[record[0]];
        if (!instance_visible(record)) continue;
        if (card_pass && (s->card_model[record[0] >> 5] >> (record[0] & 31u)) & 1u) {
            // a card instance is a whole block of shop windows (130-270 m):
            // kept when its sphere comes within the distance, the VU then
            // drops the single cards beyond it (card_vp)
            const float dx = fword(record + 1) - eye_x, dy = fword(record + 2) - eye_y,
                        dz = fword(record + 3) - eye_z, reach = card_far + fword(record + 4);
            if (dx * dx + dy * dy + dz * dz <= reach * reach && s->card_count < MAX_CARDS)
                s->cards[s->card_count++] = (mc3_u16)i;
            continue;       // drawn by reflect_mask_pass, weighted by the mask
        }
        int ready = 1;
        for (mc3_u32 j = 0; j < model->count; ++j)
            if (!s->pieces[model->pieces[j]].ready) ready = 0;
        if (!ready) continue;
        ++visible_instances;
        visible_count += model->count;
        queue_ref(instance_matrix(s, i), 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
        const mc3_u32 m = (use_cpvs & 2u) ? map_instances[i] : 0u;
        const mc3_u32 refl = (use_cpvs & 2u) && s->inst_refl ? s->inst_refl[i] : 0u;
        if ((m & 0xFFFFu) || refl) {
            if (!(m & 0xFFFFu) || queue_pcp((m & 0xFFFFu) - 1u, arena)) {
                mc3_u32 flat = (m >> 16) & 15u;  // pieces no chain covers (cover_instances)
                if (refl && split) s->later[s->later_count++] = (mc3_u16)(0x8000u | i);
                if (refl && !queue_pcp(refl - 1u, arena)) {
                    ++s->frame_pcp_failed;
                    flat |= (m >> 20) & 15u;
                }
                for (mc3_u32 j = 0; j < model->count && j < 4u; ++j)
                    if (flat & (1u << j))
                        queue_piece(&s->pieces[model->pieces[j]], arena);
                continue;
            }
            ++s->frame_pcp_failed;
        }
        for (mc3_u32 j = 0; j < model->count; ++j)
            queue_piece(&s->pieces[model->pieces[j]], arena);
    }
    s->prof[2] += cycles_now() - pc; pc = cycles_now();
    // MC2 props, after the city so their cut-outs blend over it
    const float cam_x = fword((const mc3_u32 *)(s->camera + 36u));
    const float cam_y = fword((const mc3_u32 *)(s->camera + 40u));
    const float cam_z = fword((const mc3_u32 *)(s->camera + 44u));
    const float prop_near2 = ffrom(0x47742400u);    // 250^2
    const float prop_lod2 = ffrom(0x461C4000u);     // 100^2
    // props_enabled() reads the boot args: once, not per prop (5700 lookups a
    // frame were ~1 M EE cycles)
    const mc3_u32 prop_n = s->props_ready && props_enabled() ? s->prop_count : 0u;
    for (mc3_u32 i = 0; i < prop_n; ++i) {
        const float *rec = (const float *)(s->prop_records + i * 20u);
        const PropType *t = &s->prop_types[((const mc3_u32 *)rec)[4]];
        if (!t->packets) continue;                  // particles only (steam vent)
        const float dx = rec[0] - cam_x, dy = rec[1] - cam_y, dz = rec[2] - cam_z;
        const float d2 = dx * dx + dy * dy + dz * dz;
        // MC2 draws only Far props at a distance; the rest end at PROP_NEAR
        if (!t->far && d2 > prop_near2) continue;
        if (!sphere_visible(rec[0], rec[1], rec[2], rec[3])) continue;
        queue_prop(i, arena, d2 > prop_lod2);
    }
    queue_pins(arena);
    s->prof[3] += cycles_now() - pc; pc = cycles_now();
    if (split) reflect_mask_pass(arena);
    s->prof[4] += cycles_now() - pc;
    if (zwrite_enabled()) queue_zbuf(arena, *(volatile mc3_u8 *)GS_ZWRITE_FLAG != 0u);
    ++s->queued;
    if (s->queued == 1u || !(s->queued % 120u)) {
        mark('R','D','A','D', s->queued,
             ((visible_instances & 0xFFFFu) << 16) | (visible_count & 0xFFFFu));
        mark('R','D','U','T', s->frame_uploads, s->frame_upload_bytes);
        mark('R','D','V','R', (s->frame_wraps << 16) | (s->frame_dropped & 0xFFFFu),
             (word(TEX_VRAM_CURRENT) << 16) | (s->call_count & 0xFFFFu));
        mark('R','D','C','P', (s->frame_pcp << 16) | (s->frame_pmd & 0xFFFFu),
             s->frame_pcp_failed);
        mark('R','H','M','N', s->heap_min, mc3_heap_free(mc3_heap_active()));
        mark('R','D','S','K', s->frame_skipped, s->arena_used);
        mark('R','D','P','R', s->frame_props, s->prop_count);
        float *cp, *cv;
        if (player_motion(&cp, &cv)) mark('R','C','A','R', fbits(cp[0]), fbits(cp[2]));
        if (ptr(s->camera))
            mark('R','C','A','M', word(s->camera + 36u), word(s->camera + 44u));
    }
}

// WHERE in the frame the MC2 city is drawn. mcCity::ModelDraw sets the camera
// (0x257AEC, set_camera_hook) and only then runs mcCullableMgr::Render
// (0x257BAC), whose passes go 1, 2, 4, 8 ... 2048. Pass 4 renders the
// "reflected objects" and the light reflections on the wet road
// (mcGlow::drawAllBufferedReflections) into the city env map's target and
// pastes that into the frame (mcCityEnvMap::CopyReflectedObjectsToFrame, called
// at 0x24AB80); pass 8 ends with mcCityEnvMap::DrawSky (the whole screen),
// RenderLightGlows and DebugDraw (0x24ABC0); the city's own opaque passes come
// after and cover all of it, so a reflection shows only where MC3's road lets
// it through. Drawn at SetCamera, the MC2 city was UNDER the paste: every
// reflection - headlights, traffic, sun/moon - showed through its walls, and on
// its road at full strength. So the draw waits for the end of that block
// (envmap_done_hook; drawing right after the paste lost the city under the
// sky) when the city has an env map (mcCity+464) and pass 4 is on; otherwise,
// or with [boot] mc2e = 0, it stays at SetCamera. If a frame never reached that point, drawing falls back to
// SetCamera for good (marker RDEF 0 <frame>); RDEF 1 = deferring.
enum { ENVMAP_DEBUG_DRAW_CALL = 0x0024ABC0, ENVMAP_DEBUG_DRAW = 0x00563990, CITY_PTR = 0x00615B40,
       CULL_PASS_MASK = 0x00615B0C };
static int defer_city_draw() {
    State *s = st();
    const char *v = mc3_bootarg(MC3_ID('m','c','2','e'));
    if (!v || v[0] != '1' || s->no_defer) return 0;     // off by default: see above
    const mc3_u32 city = word(CITY_PTR);
    return ptr(city) && ptr(word(city + 464u)) && (*(volatile mc3_u16 *)CULL_PASS_MASK & 4u);
}
// Drawn here, in the middle of mcCullableMgr::Render, the city's own VU1
// program and constants (bootstrap) overwrite what MC3 already uploaded for the
// rest of the frame - light matrices, state - and its cars came out black on an
// orange road. MC3 recovers from its own passes with this exact sequence
// (mcCullableMgr::Render after each pass): mark every cached state dirty, flush
// it, re-send the light matrices.
enum { RMC_DIRTY = 0x002A4F40, RMC_DO_FLUSH = 0x002AD228, RMC_UPDATE_LIGHTS = 0x002ADC78,
       RMC_FLAGS = 0x7000010C, RMC_PENDING = 0x7000012C, RMC_CURRENT = 0x70000120,
       RMC_DEFAULT = 0x70000110, RMC_LIGHTS_ON = 0x70000130, RMC_LIGHTS = 0x70000064,
       RMC_SCRATCH = 0x70000000 };
static void restore_mc3_state() {
    ((void (*)(void))RMC_DIRTY)();
    volatile mc3_u32 *pending = (volatile mc3_u32 *)RMC_PENDING;
    if (word(RMC_FLAGS) || *pending >= 2u) {
        MC3_CALL1(void, RMC_DO_FLUSH, mc3_u32)(RMC_SCRATCH);
    } else if (*pending == 1u) {
        *pending = 0u;
        const mc3_u32 obj = word(RMC_DEFAULT);
        *(volatile mc3_u32 *)RMC_CURRENT = obj;
        MC3_CALL2(void, word(word(obj) + 44u), mc3_u32, mc3_u32)(obj, 0u);
    }
    if (*(volatile mc3_u8 *)RMC_LIGHTS_ON && word(RMC_LIGHTS))
        MC3_CALL1(void, RMC_UPDATE_LIGHTS, mc3_u32)(RMC_SCRATCH);
}
extern "C" void envmap_done_hook(mc3_u32 env) {
    MC3_CALL1(void, ENVMAP_DEBUG_DRAW, mc3_u32)(env);
    State *s = st();
    if (!s || !s->draw_pending) return;
    s->draw_pending = 0u;
    if (!s->defer_marked) { s->defer_marked = 1u; mark('R','D','E','F', 1u, s->queued); }
    draw_city();
    restore_mc3_state();
}
MC3_HOOK(ENVMAP_DEBUG_DRAW_CALL, envmap_done_hook);

// EE cycles this hook takes (COP0 Count, one per cycle at 294.912 MHz; a
// 30 fps frame is 9.83 M): average and worst of each 120 frames in RCYC.
static void set_camera_hook_body(mc3_u32 camera);
extern "C" void set_camera_hook(mc3_u32 camera) {
    const mc3_u32 c0 = cycles_now();
    set_camera_hook_body(camera);
    State *s = st();
    if (!s) return;
    const mc3_u32 d = cycles_now() - c0;
    s->cyc_sum += d;
    if (d > s->cyc_max) s->cyc_max = d;
    if (++s->cyc_frames >= 120u) {
        mark('R','C','Y','C', s->cyc_sum / s->cyc_frames, s->cyc_max);
        mark('R','C','Y','2', ((s->cyc_phys / s->cyc_frames) >> 10) << 16 | ((s->cyc_fx / s->cyc_frames) >> 10),
             ((s->cyc_lights / s->cyc_frames) >> 10) << 16 | ((s->cyc_draw / s->cyc_frames) >> 10));
        s->cyc_sum = s->cyc_max = s->cyc_frames = 0u;
        s->cyc_phys = s->cyc_fx = s->cyc_lights = s->cyc_draw = 0u;
        const mc3_u32 nf = 120u;
        mark('R','C','Y','3', ((s->prof[0] / nf) >> 10) << 16 | ((s->prof[1] / nf) >> 10),
             ((s->prof[2] / nf) >> 10) << 16 | ((s->prof[3] / nf) >> 10));
        mark('R','C','Y','4', (s->prof[4] / nf) >> 10, 0u);
        for (int k = 0; k < 5; ++k) s->prof[k] = 0u;
    }
}
static void set_camera_hook_body(mc3_u32 camera) {
    const int city = in_mc2_city() && state_acquire();
    if (city && spectate_seconds() && !(st()->queued % 30u)) occ_probe();   // diagnostic, spectator runs only
    {
        int x, y, z;
        if (ptr(camera) && boot_int(MC3_ID('m','c','2','x'), &x) &&
            boot_int(MC3_ID('m','c','2','y'), &y) && boot_int(MC3_ID('m','c','2','z'), &z)) {
            float *pos = (float *)(camera + 36u);
            pos[0] = (float)x; pos[1] = (float)y; pos[2] = (float)z;
            int down;
            if (boot_int(MC3_ID('m','c','2','d'), &down) && down) {
                // look straight down: right +X, up -Z, back +Y
                float *m = (float *)camera;
                m[0] = 1.0f; m[1] = 0.0f; m[2] = 0.0f;
                m[3] = 0.0f; m[4] = 0.0f; m[5] = -1.0f;
                m[6] = 0.0f; m[7] = 1.0f; m[8] = 0.0f;
            }
        }
    }
    {
        const int seconds = spectate_seconds();
        if (seconds && city) spectate(camera, seconds);
        const int tour = tour_seconds();
        if (tour && city) camera_tour(camera, tour);
        const int drop = drop_frames();
        if (drop && city) drop_test(camera, drop);
        if (city && st()->props_ready) pins_update();
    }
    MC3_CALL1(void, SET_CAMERA, mc3_u32)(camera);
    Tracked *t = trk();
    if (!city) {
        // front end, garage or another city: nothing of ours may stay resident
        if (t->active || st()) { release_all(); t->active = 0u; }
        return;
    }
    t->active = 1u;
    fix_ai_floor();
    State *s = st();
    s->camera = camera;
    if (s->draw_pending) {                 // last frame never reached the paste
        s->draw_pending = 0u;
        s->no_defer = 1u;
        mark('R','D','E','F', 0u, s->queued);
    }
    // out of memory is not final: try again every RETRY_FRAMES
    if ((s->texture_error == 5u || s->texture_error == 3u) && ++t->retry >= RETRY_FRAMES) {
        t->retry = 0u;
        s->texture_error = 0u;
    }
    if (!load_step()) { /* still loading */ }
    if (s->props_ready && props_enabled()) {
        mc3_u32 c = cycles_now();
        step_prop_physics();
        s->cyc_phys += cycles_now() - c; c = cycles_now();
        step_prop_fx();
        s->cyc_fx += cycles_now() - c; c = cycles_now();
        step_lights();
        s->cyc_lights += cycles_now() - c;
    }
    if (!s->manifest_ready || !s->bootstrap || !s->textures_ready ||
        !s->frame_ready || !update_view_projection()) return;
    frustum_update();
    {   // [boot] mc2o = 0 (diagnostic): the MC2 city is not drawn at all
        const char *v = mc3_bootarg(MC3_ID('m','c','2','o'));
        if (v && v[0] == '0') return;
    }
    if (defer_city_draw()) { s->draw_pending = 1u; return; }
    const mc3_u32 c = cycles_now();
    draw_city();
    s->cyc_draw += cycles_now() - c;
}

MC3_HOOK(SET_CAMERA_HOOK, set_camera_hook);

// Leaving a race for the front end (mcGameState::ChangeState(7) -> jal
// EnterStateMC3Frontend at 0x1A5660). Two things have to happen right here,
// before the front end loads its background city:
//  - our heap goes back (release_all): no SetCamera runs between the race and
//    that load, and San Diego alone asks for 8.9 MB;
//  - the background city: the game reloads the LAST race's city and aims the
//    menu camera at its entry in tune/ui/<lang>/menucamera.ui, which only has
//    sd/atlanta/detroit/tokyo - for an added city the menus render off screen.
//    Current and next race configs go back to San Diego, as after a race
//    there; the arcade's own selection (0x619B18) keeps the player's city.
// Marker: RFEN <city found> <blocks released>
extern "C" mc3_u32 frontend_hook(mc3_u32 game_state) {
    Tracked *t = trk();
    const mc3_u32 blocks = t->n;
    if (t->active || st()) { release_all(); t->active = 0u; }
    const mc3_u32 cfgs[2] = { *(volatile mc3_u32 *)RACE_CONFIG_CURRENT,
                              *(volatile mc3_u32 *)RACE_CONFIG_NEXT };
    mc3_u32 found = 0xFFFFFFFFu;
    for (int i = 0; i < 2; ++i) {
        if (!ptr(cfgs[i])) continue;
        volatile mc3_u32 *city = (volatile mc3_u32 *)cfgs[i];
        if (i == 0) found = *city;
        if (*city >= (mc3_u32)FIRST_CITY_WITHOUT_MENU_CAMERA && *city < 16u)
            *city = (mc3_u32)FRONTEND_CITY;
    }
    mark('R','F','E','N', found, blocks);
    return MC3_CALL1(mc3_u32, ENTER_FRONTEND, mc3_u32)(game_state);
}
MC3_HOOK(FRONTEND_CALL, frontend_hook);

// The whole city load, before the race starts. mcGameState::EnterStateGame
// (0x1A5700) ends with jal StartRace (0x1A58F0 -> 0x1A3E70); by then the city,
// its layer and the cars are loaded, and the loading screen - drawn by
// mcLoadingThread, not by this thread - is still up. Loading here instead of a
// slice per frame means the race no longer starts with the map still coming
// in (it took ~20 s). Needs `= shim` in the .ini, so the hook is in place for
// the first race too. Marker RPLD <slices> <finished>.
enum { START_RACE_CALL = 0x001A58F0, START_RACE = 0x001A3E70, PRELOAD_MAX_SLICES = 200000 };
static void preload_city() {
    if (!in_mc2_city() || !state_acquire()) return;
    Tracked *t = trk();
    t->active = 1u;
    mc3_u32 n = 0;
    int done = 0;
    while (!done && n < PRELOAD_MAX_SLICES) { done = load_step(); ++n; }
    mark('R','P','L','D', n, (mc3_u32)done);
}
extern "C" mc3_u32 start_race_hook(mc3_u32 game) {
    preload_city();
    return MC3_CALL1(mc3_u32, START_RACE, mc3_u32)(game);
}
MC3_HOOK(START_RACE_CALL, start_race_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
