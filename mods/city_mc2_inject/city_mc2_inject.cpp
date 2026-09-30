// -----------------------------------------------------------------------------
//  city_mc2_inject - draw one MC2 piece EVERY FRAME, chained into REAL gameplay,
//  following the REAL live camera. First attempt at "the consumer": not a
//  standalone sandbox, not a synthetic camera - the actual mcCity::ModelDraw of
//  a normal boot, with the game running exactly as it always does.
//
//  Two hooks, both chained (call the original first, unconditionally):
//    1. jal rmcState::SetCamera (0x257AEC) - captures the live Matrix34
//       {right, up, back, pos}, camera-to-world, row-vectors (confirmed live
//       against freecam's own convention with city_camera_probe: 12 frames
//       dumped while driving in Atlanta had all three axes near-unit and
//       near-orthogonal, position (~308,~1.6,~180) drifting a few units a
//       frame - exactly a chase-cam matrix, not garbage).
//    2. jal mcCullableMgr::Render (0x257BAC) - right after ALL real city
//       geometry for the frame is drawn. Here we draw one more thing.
//
//  host0:/mc2piece.bin is a COMPLETE self-contained VIF1 chain from
//  mc2_vu_blob.py (microcode upload, data block incl. an IDENTITY instance
//  matrix at VU 12, the palette/textures, then the PMD0's own tags) - the same
//  file already proven on screen from the standalone sandbox
//  (mc2_draw_probe_tex.png, PMD0 #21, the Santa Monica freeway ramp).
//
//  THE SPLICE. The file's geometry is a `unique_component`: its vertices are
//  ALREADY MC2's own world coordinates (centred near -669.7, 5.3, -686.2 -
//  mc2_vu_blob.py prints this as `centre` when it builds the file), and the
//  data block's instance matrix at VU 12 is the identity because nothing is
//  meant to move it. To make it appear near the LIVE camera instead, this mod
//  overrides that translation every frame with (target - piece_centre), where
//  target = camera.pos + camera.back * -30 - a fixed distance straight ahead.
//
//  The file's first tag is one big `cnt` sized to cover everything up to and
//  including that identity matrix; nothing after it touches VU 12 again. This
//  mod turns that first tag into a `next` that detours through a tiny SPLICE
//  packet built once (FLUSH, UNPACK V4-32 x4 -> VU 12, then `next` back to the
//  file's own second tag - its real geometry) and rewritten every frame with
//  nothing but fresh position floats, so the bulk of the file is never touched.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../freecam/freecam_math.h"

enum {
    SET_CAMERA_HOOK = 0x00257AEC,
    RENDER_HOOK     = 0x00257BAC,
    SET_CAMERA      = 0x002A5040,
    RENDER          = 0x0024A860,
    FLUSH_CACHE     = 0x00546C20,
    STREAM_OPEN     = 0x003991F0,
    STREAM_READ     = 0x003993A8,
    STREAM_CLOSE    = 0x00399748,
    STREAM_RAW      = 1,
    PIECE_MAX       = 256 * 1024,
    D1_CHCR         = 0x10009000,
    VIF1_MARK       = 0x10003C30,
};

// PMD0 #21's own centre, from mc2_vu_blob.py's `centre [-669.7, 5.3, -686.2]`.
static const float PIECE_CX = -669.7f, PIECE_CY = 5.3f, PIECE_CZ = -686.2f;
static const float DIST_AHEAD = 30.0f;

struct inject_state {
    mc3_u32 camera_ptr;
    mc3_u32 piece_base;               // host0:/mc2piece.bin, loaded once
    mc3_u32 piece_second_tag;         // file offset of its own tag[1] (untouched)
    mc3_u32 splice[24];               // tag A header, 4 qw matrix, tag B header (the two-tag detour)
    mc3_u32 frame_no;
    mc3_u32 ok_count;
    mc3_u32 skip_busy;
    mc3_u32 printed;
    mc3_u32 markbuf[4];
};
static inject_state g_state;
static __attribute__((noinline)) inject_state *st(void) { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? (48 + d) : (55 + d)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(32); }
static void sync_all(void) { __asm__ volatile("sync.l; sync.p" ::: "memory"); }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }
static float fword(const mc3_u32 *p) { union { mc3_u32 u; float f; } v; v.u = *p; return v.f; }
static mc3_u32 fbits(float f) { union { mc3_u32 u; float f; } v; v.f = f; return v.u; }

static mc3_u32 load_piece(void)
{
    char path[24];
    static const unsigned char name[] = { 104, 111, 115, 116, 48, 58, 47, 109, 99, 50, 112, 105, 101, 99, 101, 46, 98, 105, 110, 0 };
    for (int i = 0; i < 20; ++i) path[i] = (char)name[i];
    const mc3_u32 stream = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
    if (!stream)
        return 0;
    const mc3_u32 raw = (mc3_u32)mc3_alloc(PIECE_MAX + 16);
    mc3_u32 got = 0, base = 0;
    if (raw >= 0x00100000u && raw < 0x02000000u) {
        base = (raw + 15u) & ~15u;
        got = MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)(stream, base, PIECE_MAX);
    }
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    return got ? base : 0;
}

extern "C" void set_camera_hook(mc3_u32 matrix_ptr)
{
    MC3_CALL1(void, SET_CAMERA, mc3_u32)(matrix_ptr);
    st()->camera_ptr = matrix_ptr;
}
MC3_HOOK(SET_CAMERA_HOOK, set_camera_hook);

static void ensure_loaded(inject_state *s)
{
    if (s->piece_base)
        return;
    const mc3_u32 base = load_piece();
    tag(77, 73, 76, 68); hex8(base); put(10);                              // "MILD" <address>
    if (!base)
        return;
    const mc3_u32 qwc0 = word(base) & 0xFFFFu;
    const mc3_u32 second = base + 16u + qwc0 * 16u;                        // where tag[1] already is

    // Tag A: qwc=4, id=cnt(1) - transfers the matrix payload, then falls through
    // sequentially to tag B, which is physically right after it in `splice`.
    mc3_u32 *sp = s->splice;
    sp[0] = 4u | (1u << 28); sp[1] = 0; sp[2] = 0x11000000u; sp[3] = 0x6C000000u | (4u << 16) | 12u;   // FLUSH, UNPACK V4-32 x4 -> VU12
    // sp[4..19]: the 4 qw matrix payload, written fresh every frame below.
    // Tag B: qwc=0, id=next(2) - no payload of its own, jumps to the file's tag[1].
    sp[20] = 0u | (2u << 28); sp[21] = second; sp[22] = 0; sp[23] = 0;

    s->piece_second_tag = second;
    // Turn the file's own first tag (a `cnt`) into a `next` that detours through the splice.
    *(volatile mc3_u32 *)(base + 0) = qwc0 | (2u << 28);                   // same qwc, id cnt(1) -> next(2)
    *(volatile mc3_u32 *)(base + 4) = (mc3_u32)&s->splice[0];              // ADDR: jump here after the payload
    s->piece_base = base;
    tag(77, 73, 83, 66); hex8(second); put(10);                            // "MISB" <tag1 address>
}

extern "C" void render_hook(void)
{
    MC3_CALL(void, RENDER)();
    inject_state *s = st();
    ++s->frame_no;
    ensure_loaded(s);
    if (!s->piece_base)
        return;
    const mc3_u32 cam = s->camera_ptr;
    if (cam < 0x00100000u || cam >= 0x02000000u)
        return;
    // DMA1 was found permanently busy (STR=1, MFIFO mode) while driving a full Atlanta - this
    // build only WAITS for it to go idle (bounded), never forces it off. Testing with the empty
    // losangeles kit to see whether that busy state is proportional to city geometry traffic.
    int drained = 0;
    for (mc3_u32 spin = 0; spin < 4000000u; ++spin)
        if (!(word(D1_CHCR) & 0x100u)) { drained = 1; break; }
    if (!drained) {
        ++s->skip_busy;
        if (s->printed < 20) { ++s->printed; tag(77, 73, 66, 89); hex8(s->frame_no); put(32); hex8(word(D1_CHCR)); put(10); }  // "MIBY" still busy after waiting
        return;
    }
    const FcVec pos  = { fword((const mc3_u32 *)(cam + 36)), fword((const mc3_u32 *)(cam + 40)), fword((const mc3_u32 *)(cam + 44)) };
    const FcVec back = { fword((const mc3_u32 *)(cam + 24)), fword((const mc3_u32 *)(cam + 28)), fword((const mc3_u32 *)(cam + 32)) };
    const FcVec target = fc_add(pos, fc_mul(back, -DIST_AHEAD));
    const float tx = target.x - PIECE_CX, ty = target.y - PIECE_CY, tz = target.z - PIECE_CZ;

    mc3_u32 *m = &s->splice[4];
    m[0] = fbits(1.0f); m[1] = 0; m[2] = 0; m[3] = 0;
    m[4] = 0; m[5] = fbits(1.0f); m[6] = 0; m[7] = 0;
    m[8] = 0; m[9] = 0; m[10] = fbits(1.0f); m[11] = 0;
    m[12] = fbits(tx); m[13] = fbits(ty); m[14] = fbits(tz); m[15] = 0;

    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    sync_all();
    *(volatile mc3_u32 *)(D1_CHCR + 0x30) = s->piece_base;                 // TADR
    *(volatile mc3_u32 *)(D1_CHCR + 0x20) = 0;                             // QWC
    sync_all();
    *(volatile mc3_u32 *)D1_CHCR = 0x145;                                  // from memory, chain, TTE, START
    sync_all();
    int ok = 0;
    for (mc3_u32 spin = 0; spin < 4000000u; ++spin)
        if (!(word(D1_CHCR) & 0x100u)) { ok = 1; break; }
    if (ok) {
        mc3_u32 *markbuf = s->markbuf;
        markbuf[0] = 0x13000000u; markbuf[1] = 0x07000000u | (s->frame_no & 0xFFFFu); markbuf[2] = 0; markbuf[3] = 0;
        MC3_CALL1(void, FLUSH_CACHE, int)(0);
        sync_all();
        *(volatile mc3_u32 *)(D1_CHCR + 0x10) = (mc3_u32)markbuf;
        *(volatile mc3_u32 *)(D1_CHCR + 0x20) = 1;
        sync_all();
        *(volatile mc3_u32 *)D1_CHCR = 0x101;
        sync_all();
        for (mc3_u32 spin = 0; spin < 4000000u; ++spin)
            if ((word(VIF1_MARK) & 0xFFFFu) == (s->frame_no & 0xFFFFu)) break;
        ++s->ok_count;
    }
    if (s->printed < 20) {
        ++s->printed;
        tag(77, 73, 70, 82); hex8(s->frame_no); put(32); hex8((mc3_u32)ok); put(32);    // "MIFR"
        hex8(s->ok_count); put(32); hex8(s->skip_busy); put(32);
        hex8(fbits(tx)); put(32); hex8(fbits(ty)); put(32); hex8(fbits(tz)); put(10);
    }
}
MC3_HOOK(RENDER_HOOK, render_hook);
