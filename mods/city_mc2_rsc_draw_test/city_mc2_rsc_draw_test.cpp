// Experimental direct-RSC renderer. Submit through the same live SetCamera
// stage used by city_mc2_scene, without invoking empty-city ModelDraw.
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"
#include "../../payload/mc3_heap.h"

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
    RSC0 = 0x30435352,
    PMD0 = 0x30444D50,
    VCLQ = 0x514C4356,
    TEX0 = 0x30584554,
    MIP0 = 0x3050494D,
    PAL0 = 0x304C4150,
    RSCM = 0x4D435352,
    RINS = 0x534E4952,
    STREAM_RAW = 1,
    MAX_PIECES = 1100,          // 1010 in LA; State is part of the .mod
    MAX_MODELS = 1024,
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
    MAX_PROPS = 6144,
    MAX_PROP_PACKETS = 8,
    MAX_AMB_ENTRIES = 2048,
    PRP0 = 0x30505250,
    MOD0 = 0x30444F4D,
    MATRIX_BYTES = 64,
    VIF_UNPACK_MATRIX = 0x6C04000Cu   // UNPACK V4-32 x4 -> VU 12
};

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
};
struct ModelDraw {
    mc3_u32 flags, count, pieces[MAX_MODEL_PMDS];
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
    mc3_u16 comp_pcp[MAX_COMP_PCP];
    mc3_u32 comp_pcp_count, names_ready, map_ready;
    // MC2 props (step_props)
    PropType prop_types[MAX_PROP_TYPES];
    mc3_u32 prop_type_count, prop_types_ok, prop_count, prop_bad;
    mc3_u32 prop_state, prop_error, prop_reported, props_ready;
    mc3_u32 prop_records, prop_matrices, frame_props;
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
};
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }

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
// The game's allocator does not return NULL when it runs out: it prints
// "Heap (null) overrun" and stops the machine. Ask before every large block,
// and keep a margin for whatever the game allocates after us.
static int heap_room(mc3_u32 bytes) {
    return mc3_heap_free(mc3_heap_active()) >= bytes + HEAP_MARGIN;
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
    mc3_u8 *mem = heap_room(body + 32u) ? (mc3_u8 *)mc3_alloc(body + 32u) : 0;
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
    mc3_u8 *mem = heap_room(bytes) ? (mc3_u8 *)mc3_alloc(bytes) : 0;
    if (!mem) { s->texture_error = 5u; return 0; }
    const mc3_u32 frame = ((mc3_u32)mem + 15u) & ~15u;
    s->frame_mem = (mc3_u32)mem;
    s->arena[0] = frame;
    s->arena[1] = frame + ARENA_BYTES;
    for (mc3_u32 i = 0; i < MAX_IMAGES; ++i) s->image_generation[i] = 0u;
    s->frame_ready = 1u;
    return 1;
}

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
    return 1;
}

static void update_model_matrix(Piece *piece, const Target *target) {
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
    const int native = world_mode();
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

static int sphere_visible(float x, float y, float z, float radius) {
    State *s = st();
    const mc3_u32 camera = s->camera;
    if (!ptr(camera)) return 0;
    const float dx = x - fword((const mc3_u32 *)(camera + 36u));
    const float dy = y - fword((const mc3_u32 *)(camera + 40u));
    const float dz = z - fword((const mc3_u32 *)(camera + 44u));
    const float limit = st()->draw_radius + radius;
    if (dx * dx + dy * dy + dz * dz > limit * limit) return 0;

    // The VU uses row vectors: clip.x = x*m[0] + y*m[4] + z*m[8] + m[12].
    // Build clip planes from matrix columns, not contiguous rows. Using rows
    // here rejects visible models as the camera rotates and makes broad holes.
    // Leave Z clipping to the GS, whose native depth range is calibrated.
    if (!s->bootstrap || s->matrix_offset + 64u > s->bootstrap_bytes) return 0;
    const mc3_u32 *vp = (const mc3_u32 *)(s->bootstrap + s->matrix_offset);
    const float wx = fword(vp + 3), wy = fword(vp + 7);
    const float wz = fword(vp + 11), w0 = fword(vp + 15);
    const float xx = fword(vp + 0), xy = fword(vp + 4);
    const float xz = fword(vp + 8), x0 = fword(vp + 12);
    const float yx = fword(vp + 1), yy = fword(vp + 5);
    const float yz = fword(vp + 9), y0 = fword(vp + 13);
    if (outside_plane(wx, wy, wz, w0, x, y, z, radius) ||
        outside_plane(wx + xx, wy + xy, wz + xz, w0 + x0,
                      x, y, z, radius) ||
        outside_plane(wx - xx, wy - xy, wz - xz, w0 - x0,
                      x, y, z, radius) ||
        outside_plane(wx + yx, wy + yy, wz + yz, w0 + y0,
                      x, y, z, radius) ||
        outside_plane(wx - yx, wy - yy, wz - yz, w0 - y0,
                      x, y, z, radius)) return 0;
    return 1;
}

static int piece_visible(const Piece *piece, const Target *target) {
    if (!piece->ready) return 0;
    const mc3_u32 *m = (const mc3_u32 *)(piece->packet + MODEL_MATRIX_OFFSET);
    return sphere_visible(target->cx + fword(m + 12),
                          target->cy + fword(m + 13),
                          target->cz + fword(m + 14), target->radius);
}

static int instance_visible(const mc3_u32 *record) {
    return sphere_visible(fword(record + 1), fword(record + 2),
                          fword(record + 3), fword(record + 4));
}

static int consume_to(mc3_u32 limit, mc3_u32 budget);
static int plan_rsc_textures();

static void close_rsc_stream(State *s) {
    if (s->rsc_stream)
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(s->rsc_stream);
    mc3_free((void *)s->rsc_table_mem);
    mc3_free((void *)s->rsc_scratch_mem);
    s->rsc_stream = 0;
    s->rsc_table_mem = s->rsc_table = 0;
    s->rsc_scratch_mem = s->rsc_scratch = 0;
}

static int open_rsc_stream() {
    static const char path[] = "host0:/mc2/losangeles/losangeles.rsc";
    State *s = st();
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
    mc3_u8 *table_mem = (mc3_u8 *)mc3_alloc(table_bytes + 16u);
    mc3_u8 *scratch_mem = (mc3_u8 *)mc3_alloc(2048u + 16u);
    if (!table_mem || !scratch_mem) {
        s->rsc_error = 4;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        mc3_free(table_mem);
        mc3_free(scratch_mem);
        s->rsc_state = 3;
        return 0;
    }
    const mc3_u32 table = ((mc3_u32)table_mem + 15u) & ~15u;
    const mc3_u32 scratch = ((mc3_u32)scratch_mem + 15u) & ~15u;
    if (!read_exact(h, (void *)table, table_bytes)) {
        s->rsc_error = 5;
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h);
        mc3_free(table_mem);
        mc3_free(scratch_mem);
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
    packet_mem = (mc3_u8 *)mc3_alloc(PACKET_MAX + 16u);
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
        mc3_u8 *compact_mem = (mc3_u8 *)mc3_alloc(size + nseg * 12u + matrix_bytes + 32u);
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
        mc3_free(packet_mem);
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
        mc3_free(packet_mem);
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
static void bind_switch(mc3_u32 sw) {
    State *s = st();
    if (sw == s->bound_switch) return;
    s->bound_switch = sw;
    queue_ref(sw, 7u, 0u, 0u);
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
    static const char s[] = "host0:/mc2/losangeles/";
    return s;
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
    // "this container": bit 15 in ambients, bit 14 in the city container;
    // neither = the shared bank
    const mc3_u32 local = container == 2u ? 0x8000u : 0x4000u;
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
    // So in the shared bank alpha counts only from 0x40 up; in the city
    // container any non-zero alpha keeps TCC=1 (glass, cut-outs).
    mc3_u32 top_alpha = 0u;
    for (mc3_u32 i = 0; i < (t->bpp == 8u ? 256u : 16u); ++i) {
        const mc3_u32 a = word(pal + 16u + i * 4u) >> 24;
        if (a > top_alpha) top_alpha = a;
    }
    t->tcc = container == 1u ? top_alpha >= 0x40u : top_alpha != 0u;
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
    mc3_u8 *mem = (mc3_u8 *)mc3_alloc(256u + 1024u + 32u);
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
    mc3_u8 *a = heap_room(local_bytes + shared_bytes) ? (mc3_u8 *)mc3_alloc(local_bytes) : 0;
    mc3_u8 *b = a ? (mc3_u8 *)mc3_alloc(shared_bytes) : 0;
    if (!b) { mc3_free(a); return 0; }
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
    mc3_u8 *map = (mc3_u8 *)mc3_alloc(map_bytes);
    mc3_u8 *data = map ? (mc3_u8 *)mc3_alloc(total + 16u) : 0;
    if (!data) { mc3_free(map); s->texture_error = 3u; return 0; }
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
        const char *t = mc3_bootarg(MC3_ID('t','i','m','e'));
        const char *w = mc3_bootarg(MC3_ID('w','e','a','t'));
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, textures_prefix());
        n = append(path, n, t && t[0] ? t : default_time());
        path[n++] = '_'; path[n] = 0;
        n = append(path, n, w && w[0] ? w : default_weather());
        append(path, n, rsc_suffix());
        const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
            (path, STREAM_RAW);
        if (!h) { s->texture_error = 6u; s->shared_state = 3u; return; }
        const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(h);
        mc3_u8 *mem = size > 16 && size <= 0x200000 && heap_room((mc3_u32)size + 16u)
            ? (mc3_u8 *)mc3_alloc((mc3_u32)size + 16u) : 0;
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
    static const char s[] = "host0:/mc2/losangeles/city/";
    return s;
}
static __attribute__((noinline)) const char *rnt_name() {
    static const char s[] = "host0:/mc2/losangeles/losangeles.rnt";
    return s;
}
static __attribute__((noinline)) const char *lvl_name() {
    static const char s[] = "losangeles.lvl";
    return s;
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
        ? (mc3_u8 *)mc3_alloc((mc3_u32)size + 32u) : 0;
    mc3_u32 base = 0u;
    if (m) {
        base = ((mc3_u32)m + 15u) & ~15u;
        if (read_exact(h, (void *)base, (mc3_u32)size)) ((mc3_u8 *)base)[size] = 0;
        else { mc3_free(m); m = 0; base = 0u; }
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
        mc3_u8 *a = heap_room(need) ? (mc3_u8 *)mc3_alloc(MAX_INSTANCES * 20u + 16u) : 0;
        mc3_u8 *b = a ? (mc3_u8 *)mc3_alloc(MAX_INSTANCES * 64u + 16u) : 0;
        mc3_u8 *c = b ? (mc3_u8 *)mc3_alloc(HOOD_CHUNK + 512u) : 0;
        if (!c) { s->instances_error = 3u; s->hood_state = 9u; return; }
        s->instances_bank_mem = (mc3_u32)a;
        s->instances_bank = ((mc3_u32)a + 15u) & ~15u;
        s->instance_matrices_mem = (mc3_u32)b;
        s->instance_matrices = ((mc3_u32)b + 15u) & ~15u;
        hd->buf_mem = (mc3_u32)c;
        hd->buf = (mc3_u8 *)(((mc3_u32)c + 15u) & ~15u);
        s->instance_count = 0; s->hood_count = 0; s->hood_bad = 0;
        const char *empty = lvl_name() + 14;           // ""
        if (!hood_open(lvl_name(), empty)) { s->instances_error = 1u; s->hood_state = 9u; return; }
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
        mc3_free((void *)hd->buf_mem);
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
static int build_cpvs_names() {
    State *s = st();
    char path[96];
    const char *t = mc3_bootarg(MC3_ID('t','i','m','e'));
    const char *w = mc3_bootarg(MC3_ID('w','e','a','t'));
    int n = append(path, 0, cpvs_folder());
    n = append(path, n, t && t[0] ? t : default_time());
    path[n++] = '_'; path[n] = 0;
    n = append(path, n, w && w[0] ? w : default_weather());
    append(path, n, cpvs_rnt_suffix());
    mc3_u32 bytes, mem;
    const mc3_u32 rnt = read_whole(path, 0x200000u, &bytes, &mem);
    if (!rnt || word(rnt) != RNT0 || 0x18u + word(rnt + 4u) * 12u > bytes) {
        mc3_free((void *)mem);
        s->map_error = 1u; return 0;
    }
    for (mc3_u32 i = 0; i < MAX_CPVS_ENTRIES / 32u; ++i) s->cpvs_need[i] = 0u;
    for (mc3_u32 i = 0; i < MAX_INSTANCES; ++i) s->instance_word[i] = 0u;
    for (mc3_u32 i = 0; i < MAX_PIECES; ++i) s->target_word[i] = 0u;
    s->comp_pcp_count = 0;
    mc3_u32 inst_hits = 0;
    const mc3_u32 nrec = word(rnt + 4u);
    for (mc3_u32 r = 0; r < nrec; ++r) {
        const mc3_u32 rec = rnt + 0x18u + r * 12u;
        if (word(rec + 4u) != PCP0 || word(rec) >= bytes) continue;
        const mc3_u32 index = (word(rec + 8u) & 0x3FFFu) - 1u;
        if (index >= MAX_CPVS_ENTRIES) continue;
        const char *nm = (const char *)(rnt + word(rec));
        mc3_u32 len = 0;
        while (nm[len]) ++len;
        // "..._0_main"
        if (len < 8u || nm[len - 7u] != '_' || nm[len - 6u] != '0' || nm[len - 5u] != '_' ||
            lower((mc3_u8)nm[len - 4u]) != 'm' || lower((mc3_u8)nm[len - 3u]) != 'a' ||
            lower((mc3_u8)nm[len - 2u]) != 'i' || lower((mc3_u8)nm[len - 1u]) != 'n')
            continue;
        const mc3_u32 core = len - 7u;              // name without "_0_main"
        mc3_u32 inst_at = 0xFFFFFFFFu;
        for (mc3_u32 i = 0; i + 8u <= core; ++i)
            if (nm[i] == '_' && lower((mc3_u8)nm[i + 1u]) == 'l' && nm[i + 2u] == '_' &&
                lower((mc3_u8)nm[i + 3u]) == 'i' && lower((mc3_u8)nm[i + 4u]) == 'n' &&
                lower((mc3_u8)nm[i + 5u]) == 's' && lower((mc3_u8)nm[i + 6u]) == 't' &&
                nm[i + 7u] == '_') { inst_at = i + 1u; break; }
        if (inst_at != 0xFFFFFFFFu) {
            // l_inst_<type><extension>: the extension is the trailing digits
            mc3_u32 d = core;
            while (d > inst_at && nm[d - 1u] >= '0' && nm[d - 1u] <= '9') --d;
            if (d == core || d == inst_at) continue;
            mc3_u32 ext = 0;
            for (mc3_u32 i = d; i < core; ++i) ext = ext * 10u + (mc3_u32)(nm[i] - '0');
            const mc3_u32 model = find_model(nm + inst_at, d - inst_at);
            if (model == NO_IMAGE) continue;
            const mc3_u32 inst = find_instance(model, ext);
            if (inst == NO_IMAGE) continue;
            s->instance_word[inst] = index + 1u;
            s->cpvs_need[index >> 5] |= 1u << (index & 31u);
            ++inst_hits;
        } else if (s->comp_pcp_count < MAX_COMP_PCP) {
            s->comp_pcp[s->comp_pcp_count++] = (mc3_u16)index;
            s->cpvs_need[index >> 5] |= 1u << (index & 31u);
        }
    }
    mc3_free((void *)mem);
    s->names_ready = 1u;
    mark('R','C','P','N', s->comp_pcp_count, inst_hits);
    return 1;
}

// After the cpvs file is in: a component PCP0 is drawn by the lowest record
// it takes geometry from; every record it covers skips its own PMD0.
static int resolve_ref_target(mc3_u32 addr, mc3_u32 qwc);
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
                const int t = resolve_ref_target(word(at + 4u), qwc);
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
    static const char s[] = "losangeles.prop";
    return s;
}
static __attribute__((noinline)) const char *kw_prop() { static const char s[] = "prop "; return s; }
static __attribute__((noinline)) const char *kw_template() { static const char s[] = "prop_template"; return s; }
static __attribute__((noinline)) const char *kw_matrix() { static const char s[] = "matrix"; return s; }
static __attribute__((noinline)) const char *kw_far() { static const char s[] = "Far:"; return s; }

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
    mc3_u8 *mem = (mc3_u8 *)mc3_alloc(end - off + 32u);
    if (!mem) return 0u;
    const mc3_u32 at = ((mc3_u32)mem + 15u) & ~15u;
    if (stream_seek(s->amb_stream, off) < 0 || !read_exact(s->amb_stream, (void *)at, end - off)) {
        mc3_free(mem);
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
    t->ok = 1;
    ++s->prop_types_ok;
}

static mc3_u32 find_prop_type(const char *p) {
    State *s = st();
    for (mc3_u32 i = 0; i < s->prop_type_count; ++i)
        if (name_equal(s->prop_types[i].name, p, 48u)) return i;
    return NO_IMAGE;
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
            load_prop_type(t);
            return;
        }
        // placement: the matrix came first
        const mc3_u32 type = find_prop_type(q);
        if (hd->rows != 4 || type == NO_IMAGE || !s->prop_types[type].ok ||
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
        const char *t = mc3_bootarg(MC3_ID('t','i','m','e'));
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, amb_prefix());
        n = append(path, n, t && t[0] ? t : default_time());
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
            ? (mc3_u8 *)mc3_alloc(count * 14u + 64u) : 0;
        mc3_u8 *recs = tab ? (mc3_u8 *)mc3_alloc(MAX_PROPS * 20u + 16u) : 0;
        mc3_u8 *mats = recs ? (mc3_u8 *)mc3_alloc(MAX_PROPS * 64u + 16u) : 0;
        mc3_u8 *buf = mats ? (mc3_u8 *)mc3_alloc(HOOD_CHUNK + 512u) : 0;
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
        if (!hood_open(prop_file(), prop_file() + 15)) {        // "" suffix
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
        mc3_free((void *)hd->buf_mem);
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
static void step_cpvs() {
    State *s = st();
    mc3_u8 scratch[2048] __attribute__((aligned(16)));
    if (s->cpvs_state == 0u) {
        char path[96];
        const char *t = mc3_bootarg(MC3_ID('t','i','m','e'));
        const char *w = mc3_bootarg(MC3_ID('w','e','a','t'));
        int n = append(path, 0, cpvs_folder());
        n = append(path, n, t && t[0] ? t : default_time());
        path[n++] = '_'; path[n] = 0;
        n = append(path, n, w && w[0] ? w : default_weather());
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
            ? (mc3_u8 *)mc3_alloc(count * 4u + 16u) : 0;
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
            ? (mc3_u8 *)mc3_alloc(kept + 16u) : 0;
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
    mc3_u8 *pm = heap_room(1072u) ? (mc3_u8 *)mc3_alloc(16u + 1040u + 16u) : 0;
    if (!pm) { s->cpvs_error = 11u; s->cpvs_state = 3u; return; }
    const mc3_u32 pk = ((mc3_u32)pm + 15u) & ~15u;
    mc3_u32 *w = (mc3_u32 *)pk;
    w[0] = 65u; w[1] = VCLQ; w[2] = 0u; w[3] = 1040u;
    w[4] = VIF_FLUSH;
    w[5] = 0x6E000000u | 768u;       // UNPACK V4-8 x256 -> VU 768, the palette
    for (mc3_u32 i = 0; i < 256u; ++i) w[6u + i] = word(palette_at + i * 4u);
    w[262] = 0u; w[263] = 0u;
    s->palette = pk;
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
                const mc3_u32 src = resolve_ref(addr, qwc);
                if (!src) return 0;
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
extern "C" void set_camera_hook(mc3_u32 camera) {
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
    MC3_CALL1(void, SET_CAMERA, mc3_u32)(camera);
    State *s = st();
    s->camera = camera;
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
            if (r == 0) return;
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
    if (!s->manifest_ready || !s->bootstrap || !s->textures_ready ||
        !s->frame_ready || !update_view_projection()) return;
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
    for (mc3_u32 i = 0; i < s->target_count; ++i)
        if (s->pieces[i].ready && s->targets[i].flags == 0u)
            update_model_matrix(&s->pieces[i], &s->targets[i]);
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
    queue_vcl(s->bootstrap);
    if (cpv) queue_vcl(s->palette);
    for (mc3_u32 i = 0; i < s->target_count; ++i) {
        Piece *piece = &s->pieces[i];
        if (!piece->ready || s->targets[i].flags != 0u) continue;
        if (!piece_visible(piece, &s->targets[i])) continue;
        const mc3_u32 m = (use_cpvs & 1u) ? map_targets[i] : 0u;
        if ((m & CPVS_COVERED) && !(m & 0xFFFFu)) continue;  // another record draws it
        ++visible_count;
        queue_ref(piece->packet, 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
        if (m & 0xFFFFu) {
            if (queue_pcp((m & 0xFFFFu) - 1u, arena)) continue;
            ++s->frame_pcp_failed;
        }
        queue_piece(piece, arena);
    }
    for (mc3_u32 i = 0; draw_instances && i < s->instance_count; ++i) {
        const mc3_u32 *record = instance_record(s, i);
        const ModelDraw *model = &s->models[record[0]];
        if (!instance_visible(record)) continue;
        int ready = 1;
        for (mc3_u32 j = 0; j < model->count; ++j)
            if (!s->pieces[model->pieces[j]].ready) ready = 0;
        if (!ready) continue;
        ++visible_instances;
        visible_count += model->count;
        queue_ref(instance_matrix(s, i), 4u, VIF_FLUSH, VIF_UNPACK_MATRIX);
        const mc3_u32 m = (use_cpvs & 2u) ? map_instances[i] : 0u;
        if (m) {
            if (queue_pcp(m - 1u, arena)) continue;
            ++s->frame_pcp_failed;
        }
        for (mc3_u32 j = 0; j < model->count; ++j)
            queue_piece(&s->pieces[model->pieces[j]], arena);
    }
    // MC2 props, after the city so their cut-outs blend over it
    const float cam_x = fword((const mc3_u32 *)(s->camera + 36u));
    const float cam_y = fword((const mc3_u32 *)(s->camera + 40u));
    const float cam_z = fword((const mc3_u32 *)(s->camera + 44u));
    const float prop_near2 = ffrom(0x47742400u);    // 250^2
    const float prop_lod2 = ffrom(0x461C4000u);     // 100^2
    for (mc3_u32 i = 0; s->props_ready && props_enabled() && i < s->prop_count; ++i) {
        const float *rec = (const float *)(s->prop_records + i * 20u);
        const PropType *t = &s->prop_types[((const mc3_u32 *)rec)[4]];
        const float dx = rec[0] - cam_x, dy = rec[1] - cam_y, dz = rec[2] - cam_z;
        const float d2 = dx * dx + dy * dy + dz * dz;
        // MC2 draws only Far props at a distance; the rest end at PROP_NEAR
        if (!t->far && d2 > prop_near2) continue;
        if (!sphere_visible(rec[0], rec[1], rec[2], rec[3])) continue;
        queue_prop(i, arena, d2 > prop_lod2);
    }
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
        if (ptr(s->camera))
            mark('R','C','A','M', word(s->camera + 36u), word(s->camera + 44u));
    }
}
MC3_HOOK(SET_CAMERA_HOOK, set_camera_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
