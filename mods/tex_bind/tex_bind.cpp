// -----------------------------------------------------------------------------
//  tex_bind - gives the shader slots their texture AFTER the city loads.
//
//  WHY AT RUNTIME. The object the BY-NAME constructor builds is resident; the
//  RESOURCE constructor (rmcTexturePS2::rmcTexturePS2(datResource&) 0x002BA830)
//  only relocates and waits for streaming. Putting the runtime form inside the
//  .pck the game died with 90 cpuTlbMiss at 0x2BA760..0x2BA874; putting the
//  streaming form it loaded but hung. Binding AFTERWARDS, neither form has to go
//  into the file: the city carries EMPTY slots and this module writes the
//  pointer. As a bonus the relocation goes away, which has already cost me three
//  classes of bug.
//
//  HOW IT FINDS THE SLOTS. The city builder writes the signature "MC3TEXSLOTS"
//  followed by the count, right BEFORE group 0's slot array. Sweeping for it is
//  cheaper than discovering the MapRoot at runtime, and depends on no new
//  address.
//
//  THE TEXTURE COMES FROM t000.tex..t509.tex in ASSETS/texture, in the SAME
//  order the builder numbered the slots (output/la_tex_mapa.tsv).
//
//  .mod format rules: mod_main in .text.start; no data constants of its own
//  (name built on the stack with NUMERIC literals); fixed addresses behind a
//  noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    NEW_160        = 0x003B14D0,
    TEX_CTOR_NAME  = 0x002B7088,

    GUARD         = 0x0061CAC8,
    DONE          = 0x54424E31u,
    STEP          = 120,        // sweeps every 2 s, not every frame
    MAX_SLOTS      = 512,
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static __attribute__((noinline)) volatile mc3_u32 *guard(void)
{
    return (volatile mc3_u32 *)GUARD;
}
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? (48 + d) : (55 + d)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(32); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

// Looks for "MC3TEXSLOTS" in RAM. Compares by word so no data constant is
// created: 'MC3T' 'EXSL' 'OTS\0' in little-endian.
static __attribute__((noinline)) mc3_u32 find_marker(void)
{
    const mc3_u32 a = 0x5433434Du;   // "MC3T"
    const mc3_u32 b = 0x4C535845u;   // "EXSL"
    const mc3_u32 c = 0x0053544Fu;   // "OTS\0"
    for (mc3_u32 p = 0x00716000u; p < 0x01FF0000u; p += 16u) {
        if (*(volatile mc3_u32 *)p != a) continue;
        if (*(volatile mc3_u32 *)(p + 4) != b) continue;
        if (*(volatile mc3_u32 *)(p + 8) != c) continue;
        return p;
    }
    return 0;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *g = guard();
    if (*g == DONE) return;
    if (*g > (mc3_u32)(STEP * 64)) *g = 0u;
    *g = *g + 1u;
    if (*g % STEP) return;

    mc3_u32 m = find_marker();
    if (!m) return;                       // the city has not loaded yet

    mc3_u32 n = *(volatile mc3_u32 *)(m + 12);
    if (n == 0u || n > (mc3_u32)MAX_SLOTS) return;
    mc3_u32 slots = m + 16;               // the array comes right after
    tag(66, 73, 78, 68); hex8(m); put(32); hex8(n); put(10);   // BIND

    char tn[8];
    mc3_u32 ok = 0;
    for (mc3_u32 k = 0; k < n; ++k) {
        mc3_u32 sh = *(volatile mc3_u32 *)(slots + 4u * k);
        if (!sane(sh)) continue;
        tn[0]=116;                                     // "t"
        tn[1]=(char)(48 + k / 100);
        tn[2]=(char)(48 + (k / 10) % 10);
        tn[3]=(char)(48 + k % 10);
        tn[4]=0;
        mc3_u32 o = MC3_CALL1(mc3_u32, NEW_160, mc3_u32)(160u);
        if (!o) continue;
        mc3_u32 t = MC3_CALL2(mc3_u32, TEX_CTOR_NAME, mc3_u32, const char *)(o, tn);
        if (sane(t)) {
            *(volatile mc3_u32 *)(sh + 0x08) = t;      // the shader now uses this one
            ++ok;
        }
    }
    tag(66, 79, 75, 32); hex8(ok); put(32); hex8(n); put(10);  // BOK
    *g = DONE;
}
