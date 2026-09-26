// -----------------------------------------------------------------------------
//  city_geom_probe - READ-ONLY look at the real per-frame geometry draw call.
//
//  Chained hook (calls the original every time, changes nothing) at the `jal
//  gfxGeometry::Draw` inside gfxModel::Draw (0x001EBB04) - this is the actual
//  per-mesh-block submission used during NORMAL gameplay (city, props, ...),
//  not the standalone-hook sandbox the other mods in this toolkit use.
//
//  gfxGeometry::Draw itself (0x1ED930) just writes one DMA `ref` tag
//  ([qwc|0x30000000, packet_va]) into the scratchpad ring `lowPsxGfx::SendBuffer`
//  (0x526900) manages - the same low-level queue gfxModel::Draw's texture-ref
//  tags go through. The goal of this probe is only to find out, from inside a
//  real boot, whether VU1 still has rv1_code_main resident at micro-address 0
//  while this call happens (the microcode this whole toolkit's MC2 work reuses),
//  and what the live camera matrix looks like at VU data 4..7 - before trying to
//  splice anything into that ring.
//
//  `[boot] city = ...` (see race_bootargs/race_nofe) to land in a real city.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
typedef unsigned long long mc3_u64;

enum {
    GEOM_DRAW_HOOK = 0x001EBB04,   // jal gfxGeometry::Draw inside gfxModel::Draw
    GEOM_DRAW      = 0x001ED930,   // gfxGeometry::Draw itself
    VU1_MICRO      = 0x11008000u,  // VU1 micro-memory, EE-mapped window
    VU1_DATA       = 0x1100C000u,  // VU1 data-memory, EE-mapped window
    SENDBUF_CUR    = 0x700005ACu,  // lowPsxGfx ring: current write pointer (scratchpad)
    SENDBUF_END    = 0x700005B0u,  // ring: end of the current half
};

struct probe_state {
    mc3_u32 frame_no;
    mc3_u32 frame_calls;
    mc3_u32 total_calls;
    mc3_u32 printed_this_frame;
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

// The first four instructions (lower,upper pairs) of rv1_code_main, from
// output/vu_mc3/vu_26387.bin, to compare against what is resident at VU 0.
static const mc3_u32 RV1_HEAD[8] = {
    0x8000033Cu, 0x080002FFu, 0x8000033Cu, 0x000002FFu,
    0x8000033Cu, 0x080002FFu, 0x8000033Cu, 0x000002FFu,
};

extern "C" mc3_u64 geom_draw_hook(mc3_u32 meshblock)
{
    const mc3_u64 r = MC3_CALL1(mc3_u64, GEOM_DRAW, mc3_u32)(meshblock);   // original, unconditionally
    probe_state *s = st();
    ++s->total_calls;
    ++s->frame_calls;
    // At most 4 calls logged per frame, for the first ~30 frames that call this at all.
    if (s->frame_no < 30 && s->printed_this_frame < 4) {
        ++s->printed_this_frame;
        tag(67, 68, 82, 87);                                              // "CDRW" frame call# meshblock qwc
        hex8(s->frame_no); put(32); hex8(s->frame_calls); put(32); hex8(meshblock); put(32);
        hex8(word(meshblock + 4) & 0xFFFFu); put(10);
        int match = 1;
        for (int i = 0; i < 8; ++i)
            if (word(VU1_MICRO + (mc3_u32)i * 4) != RV1_HEAD[i]) { match = 0; break; }
        tag(86, 77, 48, 32); hex8((mc3_u32)match); put(10);               // "VM0 " <rv1_code_main resident?>
        for (int i = 4; i <= 7; ++i) {
            tag(86, 68, 48 + (char)i, 32);                                // "VD4".."VD7"
            hex8(word(VU1_DATA + (mc3_u32)i * 16 + 0)); put(32);
            hex8(word(VU1_DATA + (mc3_u32)i * 16 + 4)); put(32);
            hex8(word(VU1_DATA + (mc3_u32)i * 16 + 8)); put(32);
            hex8(word(VU1_DATA + (mc3_u32)i * 16 + 12)); put(10);
        }
        tag(86, 68, 51, 53); hex8(word(VU1_DATA + 35 * 16)); put(32);     // "VD35" NLOOP|EOP word, PRIM word
        hex8(word(VU1_DATA + 35 * 16 + 4)); put(10);
        tag(83, 66, 85, 70); hex8(word(SENDBUF_CUR)); put(32); hex8(word(SENDBUF_END)); put(10);   // "SBUF"
    }
    return r;
}
MC3_HOOK(GEOM_DRAW_HOOK, geom_draw_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main(void)
{
    probe_state *s = st();
    if (s->frame_calls)                    // a real frame boundary: something called gfxGeometry::Draw since the last tick
        ++s->frame_no;
    s->frame_calls = 0;
    s->printed_this_frame = 0;
}
