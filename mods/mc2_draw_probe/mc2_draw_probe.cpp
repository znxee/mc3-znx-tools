// -----------------------------------------------------------------------------
//  mc2_draw_probe - can MC3 put a Midnight Club 2 display list on the screen?
//
//  Runs in the same empty state as pck_forge: hooks `jal mcGame::Execute`
//  (0x001A1024) and never chains, so the engine is up and nothing draws.
//
//  STAGE 1 - the GS alone. No VU, no microcode, just a GIF packet by DMA:
//  set a draw context onto the buffer the game is DISPLAYING, clear it to grey,
//  draw one Gouraud triangle. If this shows on screen, "a pixel can be put
//  there from this state" is settled, and everything after it is about the VU.
//
//  WHERE TO DRAW - read from the game, not guessed. gfxPipeline::VBLANK
//  (0x005280B8) rewrites DISPFB1/2 every vsync, but ONLY when dword_61C380 == 1,
//  and from a shadow value, qword_715A50:
//
//      DISPFB:  FBP [8:0]   FBW [14:9]   PSM [19:15]   DBX [42:32]   DBY [53:43]
//
//  So the frame buffer on screen is whatever that shadow names. FRAME_1 is built
//  from the same three fields (both count FBP in 2048-word pages and FBW in
//  64-pixel units), and the handler keeps displaying it for as long as it runs.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"
#define POST_EXPERIMENTS 0

typedef unsigned long long u64;

enum {
    SITE          = 0x001A1024,
    FLUSH_CACHE   = 0x00546C20,

    DISP_ENV      = 0x00715A40,   // the game's sceGsDispEnv: PMODE SMODE2 DISPFB DISPLAY BGCOLOR
    STREAM_OPEN   = 0x003991F0,
    STREAM_READ   = 0x003993A8,
    STREAM_CLOSE  = 0x00399748,
    STREAM_RAW    = 1,
    CHAIN_MAX     = 12 * 1024 * 1024,
    GS_PUT_DISP   = 0x00529BD0,   // gfxPipeline::gfxGsPutDispEnv(sceGsDispEnv*)

    VBLANK_FLAG   = 0x0061C380,   // dword_61C380: 1 = the handler rewrites DISPFB
    DISPFB_SHADOW = 0x00715A50,   // qword_715A50
    FIELD_OFFSET  = 0x0061C388,   // dword_61C388: DBY step per field

    D1_CHCR       = 0x10009000,   // VIF1 channel
    D2_CHCR       = 0x1000A000,   // GIF channel
    D2_MADR       = 0x1000A010,
    D2_QWC        = 0x1000A020,

    // GS registers, as GIF A+D addresses
    GS_PRIM       = 0x00,
    GS_RGBAQ      = 0x01,
    GS_XYZ2       = 0x05,
    GS_XYOFFSET_1 = 0x18,
    GS_PRMODECONT = 0x1A,
    GS_SCISSOR_1  = 0x40,
    GS_COLCLAMP   = 0x46,
    GS_TEST_1     = 0x47,
    GS_FRAME_1    = 0x4C,
    GS_ZBUF_1     = 0x4E,

    PACKET_WORDS  = 1024,
};

struct probe_state {
    mc3_u32 raw[PACKET_WORDS + 8];
};
static probe_state g_state;
static __attribute__((noinline)) probe_state *st(void) { return &g_state; }

// Reads host0:/mc2chain.bin (mc2_vu_blob.py --bin) into the game heap, 16-byte aligned.
// Character codes, never a literal: a string literal is a second data base.
static mc3_u32 load_chain(mc3_u32 *qwords)
{
    char *path = (char *)st()->raw;                       // scratch, free at this point
    static const unsigned char name[] = { 104, 111, 115, 116, 48, 58, 47, 109, 99, 50, 99, 104, 97, 105, 110, 46, 98, 105, 110, 0 };
    for (int i = 0; i < 20; ++i) path[i] = (char)name[i];
    *qwords = 0;
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
    *qwords = got / 16u;
    return got ? base : 0;
}

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

// One A+D quadword: the 64-bit register value, then the register address.
static mc3_u32 *ad(mc3_u32 *w, u64 value, mc3_u32 reg)
{
    w[0] = (mc3_u32)value;
    w[1] = (mc3_u32)(value >> 32);
    w[2] = reg;
    w[3] = 0;
    return w + 4;
}

// Sends `qwords` quadwords starting at `p` (16-byte aligned) to the GIF and
// waits for the channel to finish. Physical address == EE address below 32 MB.
static int send_dma(mc3_u32 chan, mc3_u32 p, mc3_u32 qwords)
{
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    sync_all();
    *(volatile mc3_u32 *)(chan + 0x10) = p;
    *(volatile mc3_u32 *)(chan + 0x20) = qwords;
    sync_all();
    *(volatile mc3_u32 *)chan = 0x101;             // from memory, normal mode, START
    sync_all();
    for (mc3_u32 spin = 0; spin < 4000000u; ++spin)
        if (!(*(volatile mc3_u32 *)chan & 0x100))
            return 1;
    return 0;                                      // never finished
}
static int send_gif(mc3_u32 p, mc3_u32 qwords) { return send_dma(D2_CHCR, p, qwords); }

extern "C" void mc2_draw_probe_hook(mc3_u32 game)
{
    const mc3_u32 flag = *(volatile mc3_u32 *)VBLANK_FLAG;
    const u64 dfb = *(volatile u64 *)DISPFB_SHADOW;
    const mc3_u32 field = *(volatile mc3_u32 *)FIELD_OFFSET;
    tag(68, 70, 66, 49); hex8(flag); put(32);      // "DFB1" <flag> <dispfb hi> <lo> <field step>
    hex8((mc3_u32)(dfb >> 32)); put(32); hex8((mc3_u32)dfb); put(32); hex8(field); put(10);

    // The game's own display environment, and whether it is filled in at all.
    for (mc3_u32 i = 0; i < 5; ++i) {
        const u64 q = *(volatile u64 *)(DISP_ENV + i * 8u);
        tag(68, 69, 78, 48 + (char)i); hex8((mc3_u32)(q >> 32)); put(32); hex8((mc3_u32)q); put(10);   // "DEN0".."DEN4"
    }
    // Ask the game to program the CRTC from it. Nothing has, in this state.
    MC3_CALL1(void, GS_PUT_DISP, mc3_u32)(DISP_ENV);

    const mc3_u32 fbp = (mc3_u32)(dfb & 0x1FF);
    const mc3_u32 fbw = (mc3_u32)((dfb >> 9) & 0x3F);
    const mc3_u32 psm = (mc3_u32)((dfb >> 15) & 0x1F);
    const mc3_u32 width = fbw * 64u;
    const mc3_u32 height = 448u;                   // NTSC frame; the scissor clips the rest
    tag(68, 70, 66, 50); hex8(fbp); put(32); hex8(fbw); put(32); hex8(psm); put(10);  // "DFB2"

    // 16-byte aligned scratch inside the module's own state
    mc3_u32 *base = (mc3_u32 *)(((mc3_u32)st()->raw + 15u) & ~15u);
    mc3_u32 *w = base + 4;                         // leave room for the GIFtag

    const u64 ofx = (u64)((2048u - width / 2u) << 4);
    const u64 ofy = (u64)((2048u - height / 2u) << 4);

    w = ad(w, (u64)fbp | ((u64)fbw << 16) | ((u64)psm << 24), GS_FRAME_1);
    // 16-bit Z buffer right after the 512x448 CT16 frame (56 pages); the scene tests GEQUAL
    w = ad(w, 56ull | ((u64)0x32 << 24), GS_ZBUF_1);              // ZBP 56, PSMZ16, Z writes on
    w = ad(w, ofx | (ofy << 32), GS_XYOFFSET_1);
    w = ad(w, (u64)(width - 1u) << 16 | (u64)(height - 1u) << 48, GS_SCISSOR_1);
    w = ad(w, 0x30000ull, GS_TEST_1);                            // ZTE=1, ZTST=ALWAYS
    w = ad(w, 1ull, GS_PRMODECONT);
    w = ad(w, 1ull, GS_COLCLAMP);

    // clear: a grey sprite over the whole buffer
    const u64 x0 = (u64)((2048u - width / 2u) << 4), y0 = (u64)((2048u - height / 2u) << 4);
    const u64 x1 = (u64)((2048u + width / 2u) << 4), y1 = (u64)((2048u + height / 2u) << 4);
    w = ad(w, 6ull, GS_PRIM);                                    // SPRITE
    w = ad(w, ((u64)0x3F800000u << 32) | 0x80404040ull, GS_RGBAQ);   // grey, Q=1.0f
    w = ad(w, x0 | (y0 << 16), GS_XYZ2);
    w = ad(w, x1 | (y1 << 16), GS_XYZ2);

    const mc3_u32 entries = (mc3_u32)(w - (base + 4)) / 4u;
    base[0] = entries | 0x8000u;                   // NLOOP | EOP
    base[1] = 0x10000000u;                         // NREG = 1
    base[2] = 0xEu;                                // REGS = A+D
    base[3] = 0;

    const int ok = send_gif((mc3_u32)base, entries + 1u);
    tag(71, 73, 70, 68); hex8((mc3_u32)ok); put(32); hex8(entries); put(10);   // "GIFD" <finished> <entries>

    // D_CTRL.MFD (bits 3:2) = 2 in the game's state: channel 1 is drained through the
    // MFIFO ring, so a plain transfer just waits for the ring. Take it out for ours.
    const mc3_u32 dctl = *(volatile mc3_u32 *)0x1000E000u;
    *(volatile mc3_u32 *)0x1000E000u = dctl & ~0xCu;
    sync_all();
    tag(68, 49, 66, 70); hex8(*(volatile mc3_u32 *)D1_CHCR); put(32);   // "D1BF": CHCR before / after clearing STR
    *(volatile mc3_u32 *)D1_CHCR = 0;
    sync_all();
    hex8(*(volatile mc3_u32 *)D1_CHCR); put(10);
    // ---- STAGE 2b: MC3's own rv1_code_main + one MC2 PMD0 chain -------------------
    // chain.h (generated by mc2_vu_blob.py) is a complete VIF1 DMA chain: microcode
    // upload, the data-memory block, then the PMD0's own tags. The grey clear from
    // stage 1 is under it; whatever appears on top came out of the VU.
    mc3_u32 chain_qw = 0;
    const mc3_u32 CHAIN = load_chain(&chain_qw);
    tag(67, 72, 65, 73); hex8(CHAIN); put(32); hex8(chain_qw); put(10);   // "CHAI" <address> <qwords>
    int okv = 0;
    if (CHAIN) {
        MC3_CALL1(void, FLUSH_CACHE, int)(0);
        sync_all();
        *(volatile mc3_u32 *)(D1_CHCR + 0x30) = CHAIN;    // TADR
        *(volatile mc3_u32 *)(D1_CHCR + 0x20) = 0;                 // QWC
        sync_all();
        *(volatile mc3_u32 *)D1_CHCR = 0x145;                      // from memory, chain, TTE, START
        sync_all();
        for (mc3_u32 spin = 0; spin < 40000000u; ++spin)
            if (!(*(volatile mc3_u32 *)D1_CHCR & 0x100)) { okv = 1; break; }
        for (mc3_u32 spin = 0; spin < 4000000u; ++spin)            // VU1 still running?
            if (!(*(volatile mc3_u32 *)0x10003D00u & 0x100)) break;
    }
    *(volatile mc3_u32 *)0x1000E000u = dctl;
    const mc3_u32 vw = chain_qw * 4u;
    tag(86, 85, 50, 65); hex8((mc3_u32)okv); put(32); hex8(vw); put(10); // "VU2A" <finished> <words>
    tag(86, 73, 70, 49);                                                // "VIF1" STAT ERR CODE
    hex8(*(volatile mc3_u32 *)0x10003C00u); put(32); hex8(*(volatile mc3_u32 *)0x10003C20u); put(32);
    hex8(*(volatile mc3_u32 *)0x10003C30u); put(10);
    tag(68, 77, 65, 49);                                                // "DMA1" CHCR MADR QWC TADR
    hex8(*(volatile mc3_u32 *)D1_CHCR); put(32); hex8(*(volatile mc3_u32 *)(D1_CHCR + 0x10)); put(32);
    hex8(*(volatile mc3_u32 *)(D1_CHCR + 0x20)); put(32); hex8(*(volatile mc3_u32 *)(D1_CHCR + 0x30)); put(10);
    tag(68, 67, 84, 76);                                                // "DCTL" D_CTRL D_STAT
    hex8(*(volatile mc3_u32 *)0x1000E000u); put(32); hex8(*(volatile mc3_u32 *)0x1000E010u); put(10);

#if POST_EXPERIMENTS
    // Control experiment: a tiny program that only XGKICKs the batch the chain left at
    // VU address 211. If the mesh shows up now, the GS accepts it and the rv1 path is
    // what does not reach XGKICK.
    {
        mc3_u32 *c = (mc3_u32 *)(((mc3_u32)st()->raw + 15u) & ~15u);
        mc3_u32 *w = c;
        *w++ = 0; *w++ = 0;                                    // NOP NOP
        *w++ = 0x4A000000u | (5u << 16) | 2016u;               // MPG 5 -> instruction 2016 (0x3F00)
        *w++ = 0x100100D3u; *w++ = 0x000002FFu;                // IADDIU vi01, vi00, 211 | nop
        *w++ = 0x80000EFCu; *w++ = 0x000002FFu;                // XGKICK vi01
        *w++ = 0x8000033Cu; *w++ = 0x000002FFu;
        *w++ = 0x8000033Cu; *w++ = 0x400002FFu;                // nop[E]
        *w++ = 0x8000033Cu; *w++ = 0x000002FFu;
        *w++ = 0x14000000u | 2016u;                            // MSCAL 2016
        while ((w - c) & 3) *w++ = 0;
        send_dma(D1_CHCR, (mc3_u32)c, (mc3_u32)(w - c) / 4u);
    }
    // The integer registers survive between microprograms. Store VI01..VI15 into data
    // memory 900.. so the state the rv1 tail was left in can be read.
    {
        mc3_u32 *c = (mc3_u32 *)(((mc3_u32)st()->raw + 15u) & ~15u);
        mc3_u32 *w = c;
        *w++ = 0; *w++ = 0;
        *w++ = 0x4A000000u | (18u << 16) | 2016u;              // MPG 18 -> instruction 2016
        for (mc3_u32 k = 1; k <= 15; ++k) { *w++ = 0x0B000000u | (k << 16) | (900u + k); *w++ = 0x000002FFu; }
        *w++ = 0x8000033Cu; *w++ = 0x400002FFu;                // nop[E]
        *w++ = 0x8000033Cu; *w++ = 0x000002FFu;
        *w++ = 0x8000033Cu; *w++ = 0x000002FFu;
        *w++ = 0x14000000u | 2016u;                            // MSCAL 2016
        while ((w - c) & 3) *w++ = 0;
        send_dma(D1_CHCR, (mc3_u32)c, (mc3_u32)(w - c) / 4u);
        for (mc3_u32 spin = 0; spin < 2000000u; ++spin) { }
        for (mc3_u32 k = 1; k <= 15; ++k) {
            put(86); put(73); hex8(k); put(32); hex8(*(volatile mc3_u32 *)(0x1100C000u + (900u + k) * 16u)); put(10);   // "VI" <n> <value>
        }
    }
#endif
    // what the VU1 data memory holds afterwards (EE window 0x1100C000), one quadword each
    static const unsigned short want[] = { 378, 379, 380, 381, 382, 383, 384, 385, 386, 99, 100, 101, 110, 111, 165, 166, 209, 210, 4, 5, 6, 7, 8, 9, 34, 35, 40, 41, 42, 48, 110, 111, 210, 211, 212, 213, 214, 215, 384, 385, 386, 558, 559, 560 };
    for (unsigned i = 0; i < sizeof(want) / sizeof(want[0]); ++i) {
        const volatile mc3_u32 *m = (const volatile mc3_u32 *)(0x1100C000u + want[i] * 16u);
        put(81); put(87); hex8(want[i]); put(32);                       // "QW" <index>
        hex8(m[0]); put(32); hex8(m[1]); put(32); hex8(m[2]); put(32); hex8(m[3]); put(10);
    }
    {   // and the first quadwords of micro memory
        const volatile mc3_u32 *m = (const volatile mc3_u32 *)0x11008000u;
        put(85); put(77); put(32); hex8(m[0]); put(32); hex8(m[1]); put(32); hex8(m[2]); put(32); hex8(m[3]); put(10);   // "UM"
        const volatile mc3_u32 *t = (const volatile mc3_u32 *)(0x11008000u + 0x1F40u);
        put(85); put(84); put(32); hex8(t[0]); put(32); hex8(t[1]); put(32); hex8(t[2]); put(32); hex8(t[3]); put(10);    // "UT"
    }
    tag(86, 80, 85, 83); hex8(*(volatile mc3_u32 *)0x10003D00u); put(10);      // "VPUS"
    for (;;) { }
}

MC3_HOOK(SITE, mc2_draw_probe_hook);
