// Experimental MC2 draw injection through MC3's existing lowPsxGfx queue.
// Unlike city_mc2_inject, this never starts, clears, or retargets DMA1/VIF1.

#include "../../payload/mc3_mod.h"
#include "../freecam/freecam_math.h"

enum {
    SET_CAMERA_HOOK = 0x00257AEC,
    SET_CAMERA      = 0x002A5040,
    ADD_VCL_CALL    = 0x00526A60,
    FLUSH_CACHE     = 0x00546C20,
    STREAM_OPEN     = 0x003991F0,
    STREAM_READ     = 0x003993A8,
    STREAM_SIZE     = 0x003997A8,
    STREAM_CLOSE    = 0x00399748,
    PROJ_MATRIX_PTR = 0x0070E50C,
    STREAM_RAW      = 1,
    VCLQ_MAGIC      = 0x514C4356,
    PACKET_MAX      = 256 * 1024,
};

static const float PIECE_CX = -669.7f, PIECE_CY = 5.3f, PIECE_CZ = -686.2f;
static const float DIST_AHEAD = 30.0f;

struct queue_state {
    mc3_u32 camera_ptr;
    mc3_u32 packet;
    mc3_u32 packet_size;
    mc3_u32 translation_off;
    mc3_u32 frame_no;
    mc3_u32 queued;
    mc3_u32 last_error;
    mc3_u32 logged;
    mc3_u32 matrix_logged;
    mc3_u32 anchor_valid;
    float anchor_x, anchor_y, anchor_z;
};
static queue_state g_state;
static __attribute__((noinline)) queue_state *st() { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? 48 + d : 55 + d));
    }
}
static void tag(char a, char b, char c, char d)
{ put(a); put(b); put(c); put(d); put(' '); }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }
static float fword(const mc3_u32 *p)
{ union { mc3_u32 u; float f; } v; v.u = *p; return v.f; }
static mc3_u32 fbits(float f)
{ union { mc3_u32 u; float f; } v; v.f = f; return v.u; }
static void sync_all() { __asm__ volatile("sync.l; sync.p" ::: "memory"); }

static int update_view_projection(mc3_u32 camera_ptr, mc3_u32 viewport,
                                  mc3_u32 *matrix_log)
{
    if (camera_ptr < 0x00100000u || camera_ptr >= 0x02000000u || (camera_ptr & 15u) ||
        viewport < 0x00100000u || viewport >= 0x02000000u || (viewport & 3u))
        return 0;

    float right[3], up[3], back[3], pos[3];
    for (int i = 0; i < 3; ++i) {
        right[i] = fword((const mc3_u32 *)(camera_ptr + i * 4));
        up[i]    = fword((const mc3_u32 *)(camera_ptr + 12 + i * 4));
        back[i]  = fword((const mc3_u32 *)(camera_ptr + 24 + i * 4));
        pos[i]   = fword((const mc3_u32 *)(camera_ptr + 36 + i * 4));
    }

    float view[16] = {
        right[0], up[0], back[0], 0.0f,
        right[1], up[1], back[1], 0.0f,
        right[2], up[2], back[2], 0.0f,
        -(pos[0]*right[0] + pos[1]*right[1] + pos[2]*right[2]),
        -(pos[0]*up[0] + pos[1]*up[1] + pos[2]*up[2]),
        -(pos[0]*back[0] + pos[1]*back[1] + pos[2]*back[2]), 1.0f
    };
    float proj[16];
    for (int i = 0; i < 16; ++i)
        proj[i] = fword((const mc3_u32 *)(viewport + 0x10 + i * 4));
    if (!(proj[0] > 0.2f && proj[0] < 10.0f && proj[5] > 0.2f && proj[5] < 10.0f &&
          proj[11] < -0.5f && proj[11] > -1.5f))
        return 0;

    float view_proj[16];
    for (int row = 0; row < 4; ++row) {
        for (int col = 0; col < 4; ++col) {
            float sum = 0.0f;
            for (int k = 0; k < 4; ++k)
                sum += view[row * 4 + k] * proj[k * 4 + col];
            view_proj[row * 4 + col] = sum;
        }
    }

    queue_state *s = st();
    if (s->translation_off < 11u * 16u)
        return 0;
    mc3_u32 *matrix = (mc3_u32 *)(s->packet + 16 + s->translation_off - 11u * 16u);
    for (int i = 0; i < 16; ++i)
        matrix[i] = fbits(view_proj[i]);
    if (!s->matrix_logged) {
        s->matrix_logged = 1;
        *matrix_log = 1;
    }
    return 1;
}

static mc3_u32 load_packet()
{
    static const char path[] = "host0:/mc2piece.vcl";
    const mc3_u32 stream = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
                              (path, STREAM_RAW);
    if (!stream) return 0;
    const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(stream);
    if (size < 32 || size > PACKET_MAX || ((size - 16) & 15)) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        st()->last_error = 1;
        return 0;
    }
    void *raw = mc3_alloc((mc3_u32)size + 16);
    mc3_u32 base = (mc3_u32)raw;
    if (base < 0x00100000u || base >= 0x02000000u) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        st()->last_error = 2;
        return 0;
    }
    base = (base + 15u) & ~15u;
    const mc3_u32 got = MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)
                            (stream, base, (mc3_u32)size);
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    if (got != (mc3_u32)size || (word(base + 4) != VCLQ_MAGIC) ||
        (word(base + 12) != (mc3_u32)size - 16) ||
        ((word(base) & 0xFFFFu) != (mc3_u32)((size - 16) / 16)) ||
        (word(base + 8) + 16 > (mc3_u32)size)) {
        st()->last_error = 3;
        return 0;
    }
    st()->packet_size = (mc3_u32)size;
    st()->translation_off = word(base + 8);
    st()->packet = base;
    return base;
}

extern "C" void set_camera_hook(mc3_u32 matrix_ptr)
{
    MC3_CALL1(void, SET_CAMERA, mc3_u32)(matrix_ptr);
    queue_state *s = st();
    s->camera_ptr = matrix_ptr;
    ++s->frame_no;
    if (!s->packet && !s->last_error)
        load_packet();
    if (!s->packet) {
        if (s->logged < 4) {
            ++s->logged; tag('Q','E','R','R'); hex8(s->last_error); put('\n');
        }
        return;
    }
    if (matrix_ptr < 0x00100000u || matrix_ptr >= 0x02000000u)
        return;

    const FcVec pos = { fword((const mc3_u32 *)(matrix_ptr + 36)),
                        fword((const mc3_u32 *)(matrix_ptr + 40)),
                        fword((const mc3_u32 *)(matrix_ptr + 44)) };
    const FcVec back = { fword((const mc3_u32 *)(matrix_ptr + 24)),
                         fword((const mc3_u32 *)(matrix_ptr + 28)),
                         fword((const mc3_u32 *)(matrix_ptr + 32)) };
    if (!s->anchor_valid) {
        const FcVec target = fc_add(pos, fc_mul(back, -DIST_AHEAD));
        s->anchor_x = target.x;
        s->anchor_y = target.y;
        s->anchor_z = target.z;
        s->anchor_valid = 1;
        tag('Q','A','N','C'); hex8(fbits(target.x)); put(' ');
        hex8(fbits(target.y)); put(' '); hex8(fbits(target.z)); put('\n');
    }
    mc3_u32 *m = (mc3_u32 *)(s->packet + 16 + s->translation_off);
    m[0] = fbits(s->anchor_x - PIECE_CX);
    m[1] = fbits(s->anchor_y - PIECE_CY);
    m[2] = fbits(s->anchor_z - PIECE_CZ);
    // This is an affine homogeneous transform row: [tx, ty, tz, 1].
    m[3] = fbits(1.0f);

    mc3_u32 matrix_log = 0;
    if (!update_view_projection(matrix_ptr, word(PROJ_MATRIX_PTR), &matrix_log)) {
        if (s->logged < 4) {
            ++s->logged; tag('Q','M','E','R'); hex8(s->frame_no); put('\n');
        }
        return;
    }
    if (matrix_log) {
        const mc3_u32 vp = word(PROJ_MATRIX_PTR);
        tag('Q','M','A','T'); hex8(vp); put(' ');
        hex8(word(vp + 0x10)); put(' '); hex8(word(vp + 0x24)); put('\n');
    }

    // Add a REF through the existing renderer ring; no DMA channel is touched.
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    sync_all();
    MC3_CALL1(void, ADD_VCL_CALL, mc3_u32)(s->packet);
    ++s->queued;
    if (s->logged < 20) {
        ++s->logged; tag('Q','A','D','D'); hex8(s->frame_no); put(' ');
        hex8(s->queued); put(' '); hex8(word(s->packet) & 0xFFFFu); put('\n');
    }
}
MC3_HOOK(SET_CAMERA_HOOK, set_camera_hook);
