// -----------------------------------------------------------------------------
//  city_camera_probe - READ-ONLY. Two chained hooks bracketing the real,
//  once-per-frame city render (mcCity::ModelDraw, 0x257AC8):
//
//    1. `jal rmcState::SetCamera` (0x257AEC) - the live camera Matrix34 pointer
//       arrives here as the call's own argument; nothing else in this function
//       hands it to us, so this is the only place to capture it.
//    2. `jal mcCullableMgr::Render` (0x257BAC) - runs right after ALL city
//       geometry (every cullable class, every pass) has been drawn for this
//       frame. Both hooks call the original first, unconditionally - this
//       build changes nothing, it only reads state at a real point in a real
//       frame, to plan the actual injection from measured numbers instead of
//       guesses.
//
//  Freecam already established the Matrix34 convention this engine passes
//  around: {right, up, back, pos}, camera-to-world, row-vectors - see
//  freecam_math.h. dword_70E50C is a Matrix44 handed to
//  gfxViewport::PrepFastSphereVisCheck right after SetCamera; almost certainly
//  the frame's projection or view-projection.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    SET_CAMERA_HOOK  = 0x00257AEC,   // jal rmcState::SetCamera(Matrix34 const&)
    RENDER_HOOK      = 0x00257BAC,   // jal mcCullableMgr::Render(void), right after it returns
    SET_CAMERA       = 0x002A5040,   // rmcState::SetCamera(Matrix34 const&)
    RENDER           = 0x0024A860,   // mcCullableMgr::Render itself
    PROJ_MATRIX_PTR  = 0x0070E50Cu,  // dword_70E50C: Matrix44* handed to PrepFastSphereVisCheck
    VU1_MICRO        = 0x11008000u,
    MAIN_MICROCODE   = 0x0061C2B8u,  // off_61C2B8 / MainMicrocode: which VU program DoMicrocode uses
};

struct probe_state {
    mc3_u32 camera_ptr;
    mc3_u32 frame_no;
    mc3_u32 printed;
};
static probe_state g_state;
static __attribute__((noinline)) probe_state *st(void) { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? (48 + d) : (55 + d)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(32); }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }

extern "C" void set_camera_hook(mc3_u32 matrix_ptr)
{
    MC3_CALL1(void, SET_CAMERA, mc3_u32)(matrix_ptr);
    st()->camera_ptr = matrix_ptr;
}
MC3_HOOK(SET_CAMERA_HOOK, set_camera_hook);

extern "C" void render_hook(void)
{
    MC3_CALL(void, RENDER)();
    probe_state *s = st();
    ++s->frame_no;
    if (s->printed >= 12)
        return;
    ++s->printed;
    const mc3_u32 cam = s->camera_ptr;
    tag(67, 65, 77, 32); hex8(s->frame_no); put(32); hex8(cam); put(10);   // "CAM " frame ptr
    if (cam >= 0x00100000u && cam < 0x02000000u) {
        for (int row = 0; row < 4; ++row) {
            tag(67, 77, 48 + (char)row, 32);                              // "CM0".."CM3"
            hex8(word(cam + (mc3_u32)row * 12 + 0)); put(32);
            hex8(word(cam + (mc3_u32)row * 12 + 4)); put(32);
            hex8(word(cam + (mc3_u32)row * 12 + 8)); put(10);
        }
    }
    const mc3_u32 proj = word(PROJ_MATRIX_PTR);
    tag(80, 82, 74, 32); hex8(proj); put(10);                             // "PRJ "
    if (proj >= 0x00100000u && proj < 0x02000000u) {
        for (int row = 0; row < 4; ++row) {
            tag(80, 77, 48 + (char)row, 32);                              // "PM0".."PM3"
            hex8(word(proj + (mc3_u32)row * 16 + 0)); put(32);
            hex8(word(proj + (mc3_u32)row * 16 + 4)); put(32);
            hex8(word(proj + (mc3_u32)row * 16 + 8)); put(32);
            hex8(word(proj + (mc3_u32)row * 16 + 12)); put(10);
        }
    }
    tag(77, 77, 67, 32); hex8(word(MAIN_MICROCODE)); put(10);             // "MMC " current MainMicrocode ptr
    tag(86, 85, 48, 32);                                                  // "VU0 " first 4 micro words
    hex8(word(VU1_MICRO)); put(32); hex8(word(VU1_MICRO + 4)); put(32);
    hex8(word(VU1_MICRO + 8)); put(32); hex8(word(VU1_MICRO + 12)); put(10);
}
MC3_HOOK(RENDER_HOOK, render_hook);
