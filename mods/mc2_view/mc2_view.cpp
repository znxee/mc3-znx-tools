// -----------------------------------------------------------------------------
//  mc2_view - a free camera over a patch of Midnight Club 2's Los Angeles, drawn by
//  MC3's own VU1 microcode (rv1_code_main) in the empty standalone state.
//
//  Runs like pck_forge / mc2_draw_probe: hooks `jal mcGame::Execute` (0x001A1024) and
//  never chains, so the engine is up and nothing else draws or reads the pad.
//
//  The scene is host0:/mc2view.bin, made by
//      mc2_scene_blob.py <losangeles.rsc> --view --bin $MC3_HOSTFS/mc2view.bin
//  = a 128-byte header (camera pose, focal length, where the per-frame part of the
//  chain starts) followed by one VIF1 DMA chain. The first frame sends all of it
//  (microcode, data, palette, textures, pieces); later frames send only the pieces,
//  after a small packet that replaces the view-projection matrix at VU address 4.
//
//  EVERY FRAME
//      1. read the USB keyboard snapshot freecam reads (0x6F8EC8), move the camera
//      2. clear the buffer that is NOT on screen (colour + Z) through GIF path 3
//      3. UNPACK the new matrix, then send the chain of pieces down VIF1
//      4. FLUSHA + MARK, wait for the mark: everything has left the VU and the GIF
//      5. point DISPFB2 at the finished buffer
//  Two 512x448 CT16 buffers (pages 0 and 112) share one 16-bit Z buffer (page 56).
//
//  KEYS (same as freecam)   W/S forward/back   A/D left/right   R/F up/down
//      arrows look   Shift fast   Ctrl slow   Z/X field of view   M/N speed
//      H back to the start pose
//  Boot key `cama = 1` makes the camera fly a circle by itself (for screenshots).
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"
#include "../freecam/freecam_math.h"
#include "../freecam/freecam_keys.h"

typedef unsigned long long u64;
typedef unsigned long long mc3_u64;

enum {
    SITE          = 0x001A1024,
    FLUSH_CACHE   = 0x00546C20,
    DISP_ENV      = 0x00715A40,
    GS_PUT_DISP   = 0x00529BD0,
    USB_KB_POLL   = 0x00234698,     // sub_234698: keeps sceUsbKb's state; nothing calls it in this state
    STREAM_OPEN   = 0x003991F0,
    STREAM_READ   = 0x003993A8,
    STREAM_CLOSE  = 0x00399748,
    STREAM_RAW    = 1,
    CHAIN_MAX     = 12 * 1024 * 1024,

    D1_CHCR       = 0x10009000,
    D2_CHCR       = 0x1000A000,
    VIF1_MARK     = 0x10003C30,
    VIF1_STAT     = 0x10003C00,
    VIF1_FIFO     = 0x10005000,
    DISPFB2       = 0x12000090,
    GS_CSR        = 0x12001000,     // bit 1 = FINISH
    GS_FINISH     = 0x61,

    GS_PRIM = 0x00, GS_RGBAQ = 0x01, GS_XYZ2 = 0x05, GS_XYOFFSET_1 = 0x18, GS_PRMODECONT = 0x1A,
    GS_SCISSOR_1 = 0x40, GS_COLCLAMP = 0x46, GS_TEST_1 = 0x47, GS_FRAME_1 = 0x4C, GS_ZBUF_1 = 0x4E,

    FB_WIDTH      = 512,
    FB_HEIGHT     = 448,
    FB1_PAGE      = 112,               // page 0 = buffer 0, page 56 = Z, page 112 = buffer 1
    Z_PAGE        = 56,
    MAGIC         = 0x3253434D,        // "MCS2"
    SCRATCH_WORDS = 256,
};

struct viewer_state {
    mc3_u32 scratch[SCRATCH_WORDS + 8];
    FcMatrix cam, start;
    float focal, start_focal, near_plane, far_plane, speed, turn;
};
static viewer_state g_state;
static __attribute__((noinline)) viewer_state *st(void) { return &g_state; }

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
static mc3_u32 ticks(void) { mc3_u32 t; __asm__ volatile("mfc0 %0, $9" : "=r"(t)); return t; }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }
static float fword(const mc3_u32 *p) { union { mc3_u32 u; float f; } v; v.u = *p; return v.f; }
static mc3_u32 fbits(float f) { union { mc3_u32 u; float f; } v; v.f = f; return v.u; }

// Reads host0:/mc2view.bin into the game heap, 16-byte aligned. Character codes, not a
// literal: a string literal is a second data base and mc3_mkmod refuses the build.
static mc3_u32 load_scene(mc3_u32 *bytes)
{
    char *path = (char *)st()->scratch;
    static const unsigned char name[] = { 104, 111, 115, 116, 48, 58, 47, 109, 99, 50, 118, 105, 101, 119, 46, 98, 105, 110, 0 };
    for (int i = 0; i < 19; ++i) path[i] = (char)name[i];
    *bytes = 0;
    const mc3_u32 stream = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
    if (!stream)
        return 0;
    const mc3_u32 raw = (mc3_u32)mc3_alloc(CHAIN_MAX + 16);
    mc3_u32 got = 0, base = 0;
    if (raw >= 0x00100000u && raw < 0x02000000u) {
        base = (raw + 15u) & ~15u;
        got = MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)(stream, base, CHAIN_MAX);
    }
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    *bytes = got;
    return got ? base : 0;
}

static mc3_u32 *ad(mc3_u32 *w, u64 value, mc3_u32 reg)
{
    w[0] = (mc3_u32)value; w[1] = (mc3_u32)(value >> 32); w[2] = reg; w[3] = 0;
    return w + 4;
}

static int wait_idle(mc3_u32 chan)
{
    for (mc3_u32 spin = 0; spin < 40000000u; ++spin)
        if (!(*(volatile mc3_u32 *)chan & 0x100))
            return 1;
    return 0;
}

static int dma_normal(mc3_u32 chan, mc3_u32 p, mc3_u32 qwords)
{
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    sync_all();
    *(volatile mc3_u32 *)(chan + 0x10) = p;
    *(volatile mc3_u32 *)(chan + 0x20) = qwords;
    sync_all();
    *(volatile mc3_u32 *)chan = 0x101;
    sync_all();
    return wait_idle(chan);
}

static int dma_chain(mc3_u32 tadr)
{
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    sync_all();
    *(volatile mc3_u32 *)(D1_CHCR + 0x30) = tadr;
    *(volatile mc3_u32 *)(D1_CHCR + 0x20) = 0;
    sync_all();
    *(volatile mc3_u32 *)D1_CHCR = 0x145;                 // from memory, chain, TTE, START
    sync_all();
    return wait_idle(D1_CHCR);
}

// The USB keyboard, as freecam reads it. Returns 0 when the guards say there is none.
static mc3_u32 keyboard(void)
{
    MC3_CALL(void, USB_KB_POLL)();                // freecam relies on the game's own loop calling this
    if (!*(volatile mc3_u8 *)0x0062314Eu || !*(volatile mc3_u8 *)0x00615A38u ||
        !*(volatile mc3_u8 *)0x00615A39u)
        return 0;
    return fc_usb_held(word(0x006F8ECCu), (const volatile mc3_u16 *)0x006F8ED4u, word(0x006F8ED0u));
}

static float axis(mc3_u32 keys, mc3_u32 pos, mc3_u32 neg)
{
    return (keys & pos ? fc_float(0x3f800000u) : fc_float(0u)) - (keys & neg ? fc_float(0x3f800000u) : fc_float(0u));
}

// View-projection, row-vector convention (clip = [x y z 1] * M), from the camera basis:
//   view rows = (right, up, back) as columns, translation = -dot(axis, position)
//   projection: x scaled by f/aspect, y by f, w = -z_view, z = (far+near)/(near-far)
static void build_matrix(const viewer_state *s, float out[16])
{
    const float f = s->focal, a = fc_float(0x3f924925u);         // 512 / 448
    const float n = s->near_plane, fa = s->far_plane;
    const float c2 = (fa + n) / (n - fa);
    const float c3 = fc_float(0x40000000u) * fa * n / (n - fa);
    const float fx = f / a, fy = f;
    const FcVec ax[3] = { s->cam.right, s->cam.up, s->cam.back };
    float row[4][4];
    for (int i = 0; i < 3; ++i) {
        const float x = (i == 0 ? ax[0].x : i == 1 ? ax[0].y : ax[0].z);
        const float y = (i == 0 ? ax[1].x : i == 1 ? ax[1].y : ax[1].z);
        const float z = (i == 0 ? ax[2].x : i == 1 ? ax[2].y : ax[2].z);
        row[i][0] = x * fx; row[i][1] = y * fy; row[i][2] = z * c2; row[i][3] = -z;
    }
    const float t0 = -fc_dot(s->cam.right, s->cam.pos);
    const float t1 = -fc_dot(s->cam.up, s->cam.pos);
    const float t2 = -fc_dot(s->cam.back, s->cam.pos);
    row[3][0] = t0 * fx; row[3][1] = t1 * fy; row[3][2] = t2 * c2 + c3; row[3][3] = -t2;
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j)
            out[i * 4 + j] = row[i][j];
}

// Homogeneous outcodes of the 8 corners of a box against -w <= x,y,z <= w; the box is
// out when every corner is outside the SAME plane. Valid behind the camera too.
static bool box_visible(const float M[16], const mc3_u32 *e, float min_ndc, float *extent)
{
    const float lo[3] = { fword(e + 2), fword(e + 3), fword(e + 4) };
    const float hi[3] = { fword(e + 5), fword(e + 6), fword(e + 7) };
    mc3_u32 all = 0x3Fu;
    bool front = true;                       // every corner in front of the camera
    float x0 = fc_float(0x49742400u), x1 = -x0, y0 = x0, y1 = -x0;
    for (int c = 0; c < 8; ++c) {
        const float x = (c & 1) ? hi[0] : lo[0], y = (c & 2) ? hi[1] : lo[1], z = (c & 4) ? hi[2] : lo[2];
        const float cx = x * M[0] + y * M[4] + z * M[8] + M[12];
        const float cy = x * M[1] + y * M[5] + z * M[9] + M[13];
        const float cz = x * M[2] + y * M[6] + z * M[10] + M[14];
        const float cw = x * M[3] + y * M[7] + z * M[11] + M[15];
        mc3_u32 f = 0;
        if (cx > cw) f |= 1u;
        if (cx < -cw) f |= 2u;
        if (cy > cw) f |= 4u;
        if (cy < -cw) f |= 8u;
        if (cz > cw) f |= 16u;
        if (cz < -cw) f |= 32u;
        all &= f;
        if (cw > fc_float(0x38d1b717u)) {
            const float nx = cx / cw, ny = cy / cw;
            if (nx < x0) x0 = nx;
            if (nx > x1) x1 = nx;
            if (ny < y0) y0 = ny;
            if (ny > y1) y1 = ny;
        } else {
            front = false;
        }
    }
    // how big the box looks (NDC, the viewport is 2 wide); huge when any corner is behind the camera
    *extent = front ? ((x1 - x0) > (y1 - y0) ? (x1 - x0) : (y1 - y0)) : fc_float(0x49742400u);
    if (all) return false;
    // a box that is entirely in front of the camera and smaller than `min_ndc` on both axes
    // covers a couple of pixels at most: not worth a VU kick
    if (front && (x1 - x0) < min_ndc && (y1 - y0) < min_ndc) return false;
    return true;
}

static void update_camera(viewer_state *s, mc3_u32 keys)
{
    if (keys & RECENTER) { s->cam = s->start; s->focal = s->start_focal; return; }
    const float one = fc_float(0x3f800000u);
    const float mult = (keys & FAST ? fc_float(0x40a00000u) : one) * (keys & SLOW ? fc_float(0x3e99999au) : one);
    const float step = s->speed * mult;
    const float fwd = axis(keys, FORWARD, BACK), side = axis(keys, RIGHT, LEFT), lift = axis(keys, UP, DOWN);
    s->cam.pos = fc_add(s->cam.pos, fc_mul(s->cam.back, -fwd * step));
    s->cam.pos = fc_add(s->cam.pos, fc_mul(s->cam.right, side * step));
    s->cam.pos = fc_add(s->cam.pos, fc_mul(s->cam.up, lift * step));
    const float yaw = axis(keys, LOOK_L, LOOK_R) * s->turn;
    const float pitch = axis(keys, LOOK_U, LOOK_D) * s->turn;
    if (yaw != fc_float(0u) || pitch != fc_float(0u))
        fc_turn(s->cam, yaw, pitch, fc_float(0u));
    const float z = axis(keys, WIDE, NARROW);                  // Z widens: smaller focal length
    if (z > fc_float(0u)) s->focal = s->focal * fc_float(0x3f788b2fu);
    if (z < fc_float(0u)) s->focal = s->focal * fc_float(0x3f83d70au);
    const float sp = axis(keys, SPEED_UP, SPEED_DOWN);
    if (sp > fc_float(0u)) s->speed = s->speed * fc_float(0x3f866666u);
    if (sp < fc_float(0u)) s->speed = s->speed * fc_float(0x3f73cf3du);
}

// GIF packet: one buffer's frame register, colour + Z clear, and the depth test for the scene.
static mc3_u32 clear_packet(mc3_u32 fbp, mc3_u32 *base)
{
    mc3_u32 *w = base + 4;
    const u64 w2 = FB_WIDTH / 2u, h2 = FB_HEIGHT / 2u;
    w = ad(w, (u64)fbp | ((u64)(FB_WIDTH / 64) << 16) | ((u64)2 << 24), GS_FRAME_1);
    w = ad(w, (u64)Z_PAGE | ((u64)0x32 << 24), GS_ZBUF_1);
    w = ad(w, ((2048u - w2) << 4) | (((2048u - h2) << 4) << 32), GS_XYOFFSET_1);
    w = ad(w, (u64)(FB_WIDTH - 1) << 16 | (u64)(FB_HEIGHT - 1) << 48, GS_SCISSOR_1);
    w = ad(w, 0x30000ull, GS_TEST_1);                            // Z test ALWAYS: the clear must win
    w = ad(w, 1ull, GS_PRMODECONT);
    w = ad(w, 1ull, GS_COLCLAMP);
    w = ad(w, 6ull, GS_PRIM);
    w = ad(w, ((u64)0x3F800000u << 32) | 0x80404040ull, GS_RGBAQ);
    w = ad(w, ((2048u - w2) << 4) | (((2048u - h2) << 4) << 16), GS_XYZ2);
    w = ad(w, ((2048u + w2) << 4) | (((2048u + h2) << 4) << 16), GS_XYZ2);
    w = ad(w, 0x50000ull, GS_TEST_1);                            // GEQUAL for the pieces
    const mc3_u32 entries = (mc3_u32)(w - (base + 4)) / 4u;
    base[0] = entries | 0x8000u; base[1] = 0x10000000u; base[2] = 0xEu; base[3] = 0;
    return entries + 1u;
}

extern "C" void mc2_view_hook(mc3_u32 game)
{
    viewer_state *s = st();
    // ---- display: the game never programmed the CRTC in this state
    MC3_CALL1(void, GS_PUT_DISP, mc3_u32)(DISP_ENV);
    // ---- DMA1 has a stuck chain of the game's (CHCR 0x145, MFD = 2): clear both
    *(volatile mc3_u32 *)0x1000E000u = word(0x1000E000u) & ~0xCu;
    sync_all();
    *(volatile mc3_u32 *)D1_CHCR = 0;
    sync_all();

    mc3_u32 bytes = 0;
    const mc3_u32 file = load_scene(&bytes);
    tag(77, 86, 76, 68); hex8(file); put(32); hex8(bytes); put(10);           // "MVLD" <address> <bytes>
    if (!file || word(file) != MAGIC) {
        tag(77, 86, 69, 82); hex8(word(file)); put(10);                        // "MVER"
        for (;;) { }
    }
    const mc3_u32 *hdr = (const mc3_u32 *)file;
    const mc3_u32 chain = file + 128u;
    const mc3_u32 static_last = chain + hdr[3] * 4u;          // last tag of the state + texture uploads
    const mc3_u32 table = file + hdr[21];                     // 8 words per piece: start, last, lo.xyz, hi.xyz
    const mc3_u32 count = hdr[22];
    const mc3_u32 end_tag = chain + hdr[23] * 4u;
    s->cam.pos = { fword(hdr + 4), fword(hdr + 5), fword(hdr + 6) };
    s->cam.right = { fword(hdr + 7), fword(hdr + 8), fword(hdr + 9) };
    s->cam.up = { fword(hdr + 10), fword(hdr + 11), fword(hdr + 12) };
    s->cam.back = { fword(hdr + 13), fword(hdr + 14), fword(hdr + 15) };
    s->focal = s->start_focal = fword(hdr + 16);
    s->near_plane = fword(hdr + 17); s->far_plane = fword(hdr + 18);
    s->speed = fword(hdr + 19); s->turn = fword(hdr + 20);
    s->start = s->cam;

    const char *pc = mc3_bootarg(MC3_ID('p', 'a', 'c', 'e'));
    mc3_u32 pace_ms = 100;
    if (pc && *pc >= 48 && *pc <= 57) { pace_ms = 0; for (; *pc >= 48 && *pc <= 57; ++pc) pace_ms = pace_ms * 10u + (mc3_u32)(*pc - 48); }
    const mc3_u32 pace_ticks = pace_ms * 294912u;                           // EE clock: 294.912 MHz
    const mc3_u32 automatic = mc3_bootarg(MC3_ID('c', 'a', 'm', 'a')) != 0;
    const bool cull = mc3_bootarg(MC3_ID('n', 'o', 'c', 'u')) == 0;
    // [boot] minpx = N: pieces smaller than N pixels across are skipped (default 4; 0 = only the frustum)
    const char *mp = mc3_bootarg(MC3_ID('m', 'i', 'n', 'p'));
    mc3_u32 px = 4;
    if (mp && *mp >= 48 && *mp <= 57) { px = 0; for (; *mp >= 48 && *mp <= 57; ++mp) px = px * 10u + (mc3_u32)(*mp - 48); }
    // [boot] lodpx = N: a piece that looks smaller than N pixels across draws its far LOD (off by default)
    const char *lp = mc3_bootarg(MC3_ID('l', 'o', 'd', 'p'));
    mc3_u32 lpx = 40;
    if (lp && *lp >= 48 && *lp <= 57) { lpx = 0; for (; *lp >= 48 && *lp <= 57; ++lp) lpx = lpx * 10u + (mc3_u32)(*lp - 48); }
    // off by default: measured no PCSX2 draw-count change (a TEX0/TEXFLUSH-only packet does not
    // reach GSState::Draw()) and a real visual regression (draw order changes which overlapping
    // decal wins a Z tie, e.g. window glows) - see FORKS.md. [boot] group = 1 turns it on.
    const bool grp = mc3_bootarg(MC3_ID('g', 'r', 'o', 'u')) != 0;
    const bool lod = lp != 0;                     // off unless [boot] lodpx is given: it did not raise the frame rate
    const float lod_ndc = (float)lpx * fc_float(0x3b000000u) * fc_float(0x40000000u);
    const float min_ndc = (float)px * fc_float(0x3b000000u) * fc_float(0x40000000u);   // 2 * px / 512 in NDC (viewport is 2 wide)            // [boot] nocull = 1 turns it off
    // debugging: [boot] skip = N leaves out piece N only (to find state that leaks between pieces)
    const char *sk = mc3_bootarg(MC3_ID('s', 'k', 'i', 'p'));
    mc3_u32 skip_piece = 0xFFFFFFFFu;
    if (sk && *sk >= 48 && *sk <= 57) { skip_piece = 0; for (; *sk >= 48 && *sk <= 57; ++sk) skip_piece = skip_piece * 10u + (mc3_u32)(*sk - 48); }
    tag(75, 66, 71, 68);                                                       // "KBGD" the keyboard guards
    hex8(*(volatile mc3_u8 *)0x0062314Eu); put(32); hex8(*(volatile mc3_u8 *)0x00615A38u); put(32);
    hex8(*(volatile mc3_u8 *)0x00615A39u); put(32); hex8(word(0x006F8ED0u)); put(10);

    mc3_u32 *base = (mc3_u32 *)(((mc3_u32)s->scratch + 15u) & ~15u);
    mc3_u32 *mat = base + 64;                                                  // clear packet first, matrix packet after
    mc3_u32 *mark = base + 128;
    mc3_u32 shown = 0;                                                         // buffer on screen: the game's, page 0
    mc3_u32 last = ticks();

    for (mc3_u32 frame = 0;; ++frame) {
        mc3_u32 keys = keyboard();
        if (automatic && frame < 36) keys |= FORWARD | FAST;   // a short flight toward the city, for screenshots
        update_camera(s, keys);

        const mc3_u32 t_a = ticks();
        const mc3_u32 back = shown ^ 1u;                                       // draw where nothing is shown
        const mc3_u32 fbp = back ? FB1_PAGE : 0u;
        const mc3_u32 cq = clear_packet(fbp, base);
        int ok = dma_normal(D2_CHCR, (mc3_u32)base, cq);

        float M[16];
        build_matrix(s, M);
        mat[0] = 0x11000000u; mat[1] = 0x6C000000u | (4u << 16) | 4u;           // FLUSH, UNPACK V4-32 x4 -> VU 4
        for (int i = 0; i < 16; ++i) mat[2 + i] = fbits(M[i]);
        mat[18] = 0; mat[19] = 0;
        ok &= dma_normal(D1_CHCR, (mc3_u32)mat, 5u);
        const mc3_u32 t_b = ticks();

        // Culling: string the visible pieces together. The last tag of a piece is a `cnt`;
        // making it a `next` whose address is the following visible piece skips the rest.
        mc3_u32 head = end_tag, prev_last = 0, seen = 0, far_used = 0, skipped = 0;
        mc3_u32 prev_tex = 0xFFFFFFFFu;                                        // texture the previous drawn piece ends on
        for (mc3_u32 i = 0; i < count; ++i) {
            const mc3_u32 *e = (const mc3_u32 *)(table + i * 64u);
            float extent = fc_float(0x49742400u);
            if (i == skip_piece) continue;
            if (cull && !box_visible(M, e, min_ndc, &extent)) continue;
            // words 8/9: the far LOD (0 when the piece has none); used when the piece looks small
            mc3_u32 first = e[0], last = e[1];
            bool far_lod = false;
            if (lod && e[8] && extent < lod_ndc) { first = e[8]; last = e[9]; ++far_used; far_lod = true; }
            // words 12..15: this piece's first texture switch, the texture it selects first and last,
            // and whether the switch may be skipped. When the piece before it (in the chain) already
            // ends on that texture the switch is turned into a jump over itself; otherwise it is
            // put back. A skipped switch is one draw less for the GS.
            if (e[15] && !far_lod) {
                volatile mc3_u32 *sw = (volatile mc3_u32 *)(chain + e[12] * 4u);
                if (grp && prev_tex == e[13]) {
                    sw[0] = 2u << 28;                                            // next, no data
                    sw[1] = (mc3_u32)sw + 64u;                                   // over the 3 quadwords of payload
                    sw[2] = 0; sw[3] = 0;
                    ++skipped;
                } else {
                    sw[0] = 3u | (1u << 28);                                     // cnt, 3 quadwords
                    sw[1] = 0;
                    sw[2] = 0x11000000u; sw[3] = 0x50000003u;                    // FLUSH, DIRECT 3
                }
            }
            prev_tex = (e[15] && !far_lod) ? e[14] : 0xFFFFFFFFu;
            const mc3_u32 start = chain + first * 4u;
            if (prev_last) {
                volatile mc3_u32 *t = (volatile mc3_u32 *)prev_last;
                t[0] = (t[0] & ~0x70000000u) | 0x20000000u;                      // cnt -> next
                t[1] = start;
            } else {
                head = start;
            }
            prev_last = chain + last * 4u;
            ++seen;
        }
        if (prev_last) {
            volatile mc3_u32 *t = (volatile mc3_u32 *)prev_last;
            t[0] = (t[0] & ~0x70000000u) | 0x20000000u;
            t[1] = end_tag;
        }
        if (frame == 0) {                                                        // the first frame also sends the static part
            volatile mc3_u32 *t = (volatile mc3_u32 *)static_last;
            t[0] = (t[0] & ~0x70000000u) | 0x20000000u;
            t[1] = head;
            ok &= dma_chain(chain);
        } else {
            ok &= dma_chain(head);
        }

        const mc3_u32 t_c = ticks();
        mark[0] = 0x13000000u;                                                 // FLUSHA: VU1 and the GIF are done
        mark[1] = 0x07000000u | ((frame + 1u) & 0xFFFFu);                      // MARK
        mark[2] = 0; mark[3] = 0;
        ok &= dma_normal(D1_CHCR, (mc3_u32)mark, 1u);
        for (mc3_u32 spin = 0; spin < 40000000u; ++spin)
            if ((word(VIF1_MARK) & 0xFFFFu) == ((frame + 1u) & 0xFFFFu)) break;

        const mc3_u32 t_d = ticks();
        // The GS runs on its own thread in the emulator and can be frames behind the EE: make it
        // say it has finished this frame (FINISH event) before the buffer goes on screen.
        // Pace the loop. The emulator's GS runs on its own thread and nothing the guest can read
        // says when it has drawn a frame (FINISH only says the GIF handed the packet over, and a
        // GS->host download does not wait either), so an EE that runs ahead queues frames and the
        // picture on screen is caught half drawn. A fixed period longer than the GS needs keeps
        // it clean: [boot] pace = ms (default 100; measured clean at 60 ms with minpx 4 on the 1075-piece
        // scene, one half-drawn frame in six at 30 ms; 0 = as fast as possible).
        while (pace_ticks && (ticks() - t_a) < pace_ticks) { }
        const mc3_u32 t_e = ticks();
        *(volatile u64 *)DISPFB2 = (u64)fbp | ((u64)(FB_WIDTH / 64) << 9) | ((u64)2 << 15);
        shown = back;

        if (frame < 6 || (frame % 16) == 0) {
            const mc3_u32 now = ticks();
            tag(77, 86, 70, 82); hex8(frame); put(32); hex8(ok); put(32); hex8(now - last); put(32);   // "MVFR" <frame> <ok> <ticks>
            hex8(keys); put(32); hex8(seen); put(32); hex8(far_used); put(32); hex8(skipped); put(32); hex8(fbits(s->cam.pos.x)); put(32); hex8(fbits(s->cam.pos.z)); put(10);
            last = now;
            tag(77, 86, 80, 72); hex8(t_b - t_a); put(32); hex8(t_c - t_b); put(32); hex8(t_d - t_c); put(32); hex8(t_e - t_d); put(32); hex8(word(0x10003020u)); put(32); hex8(word(VIF1_STAT)); put(10);   // + GIF_STAT, VIF1_STAT   // "MVPH" clear+matrix / cull+chain / mark / finish
        }
    }
}

MC3_HOOK(SITE, mc2_view_hook);
