// -----------------------------------------------------------------------------
//  standalone_probe - does the engine work as a library, with no game running?
//
//  THE IDEA BEING TESTED
//
//  Main_void ends with `jal mcGame::Execute` at 0x001A1024, and Execute is
//  nothing but `while (2) { ... }` - decompiled, it initialises nothing before
//  its loop. So everything the engine needs is already up by that call:
//  allocators, the file system, the asset manager, the car table, the layer
//  manager, mcGameState. Hooking that `jal` and NOT chaining to it leaves the
//  machine in a state no game mode ever occupies - engine running, nothing
//  loaded, no city, no front end, no movie, no state machine - with the whole
//  heap free.
//
//  That is the state a packing tool would want: it only needs the game's own
//  functions and the PS2's own hardware, not a game.
//
//  WHAT THIS MEASURES, in one boot, in this order (cheapest and safest first,
//  so a hang later still leaves the earlier answers printed over SIO):
//
//    SAFR <free>            the main allocator's free bytes here, by the same
//                           size-minus-used rule the "game packs geometry" work measured against
//                           the game's own "(%d requested, %d available)"
//                           message: 1 456 656 with the front end loaded.
//    SAMT <heap> <bytes>    a private top heap of nearly all of it
//    SARO <root> <used>     rmcModel::Create on model/p000.mesh - the .mesh
//                           branch, the one that reaches rmcGeometry::Packet.
//                           Non-zero root = the resource path works with no
//                           frame pump at all, which is the open question:
//                           every previous run of this chain happened inside a
//                           live mcGame::Execute loop.
//    SAF2 <free>            free again, after popping the private heap
//    SAFT <fontptr>         only when [boot] font = 1. txtGetFontTex hangs when
//                           asked from the front end (see the screen/frontend notes);
//                           whether it hangs HERE, with nothing else touching
//                           the asset manager, decides how an on-screen log
//                           would have to be built. Last on purpose.
//
//  Then it loops forever, so the emulator stays alive to be screenshotted.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"

enum {
    SITE                = 0x001A1024,   // jal mcGame::Execute - never chained

    HEAP_ACTIVE         = 0x0070E500,   // the active memMemoryAllocator*
    ALLOC_BASE          = 0x04,
    ALLOC_SIZE          = 0x08,
    ALLOC_USED          = 0x0C,
    HEAP_CREATE_MEMTOP  = 0x004BD650,   // (bytes, name) -> memMemoryAllocator*
    HEAP_PUSH           = 0x004BD5D8,
    HEAP_POP            = 0x004BD5F0,

    RMC_MODEL_CREATE    = 0x002A8CA0,   // (path, 0) -> rmcModel*
    TXT_GET_FONT_TEX    = 0x002BB880,

    // Colour-per-vertex. rmcGeometry::Packet::Init reads the gate byte first
    // (0x002AC040) and, when it is set, looks the colour up in the palette at
    // sm_Current (0x002AC054 -> rmcCpvPalette::Lookup). mcCity's constructor is
    // what normally installs that palette - `sm_Current = *(city + 0x2C)` - so
    // with no city it is null and the lookup reads address 0.
    CPV_INDEXED         = 0x00615D00,   // rmcCpvIndexed, 1 in the image
    CPV_CURRENT         = 0x0070FA40,   // rmcCpvPalette::sm_Current
    CPV_DIRTY           = 0x0070FA3C,   // rmcCpvPalette::sm_Dirty
    CPV_ENTRIES         = 256,          // Lookup walks 0..254 and reads 255 first
    CPV_BYTES           = CPV_ENTRIES * 4 * 4,   // 256 * Vector4 = 4096

    // The real palette is inline in a city's own .pck, at MapRoot+0x290 (file
    // offset 0x310) - every retail city has +0x2C = 0x06800290 pointing there.
    // It differs per city AND per condition: Atlanta's entry 1 is
    // (0.376,0.373,0.784) at dawn/clear and (0.800,0.404,0.400) at dusk/rainy.
    // Entry 255 is white, which is the one Lookup tests before the walk.
    STREAM_OPEN         = 0x003991F0,   // (path, methods=1) -> Stream*
    STREAM_READ         = 0x003993A8,   // (stream, buf, bytes) -> read
    STREAM_CLOSE        = 0x00399748,
    STREAM_RAW          = 1,

    SLACK               = 64 * 1024,    // leave the main heap room to breathe
};

struct sap_state {
    char name[12];
    char path[20];
    char pal_path[20];
};
static sap_state g_state;
static __attribute__((noinline)) sap_state *st(void) { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? (48 + d) : (55 + d)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(32); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

static mc3_u32 free_bytes(mc3_u32 a)
{
    if (!sane(a))
        return 0;
    return *(volatile mc3_u32 *)(a + ALLOC_SIZE) - *(volatile mc3_u32 *)(a + ALLOC_USED);
}

// Character codes, not literals: a string literal is a second data base and
// mc3_mkmod refuses the build (see docs/WRITING_A_MOD.md).
static const char *name(void)
{
    char *d = st()->name;
    d[0] = 115; d[1] = 97; d[2] = 112; d[3] = 114; d[4] = 111;   // "sapro"
    d[5] = 98;  d[6] = 101; d[7] = 0;                            // "be"
    return d;
}
static const char *path(void)
{
    char *d = st()->path;
    d[0] = 109; d[1] = 111; d[2] = 100; d[3] = 101; d[4] = 108; d[5] = 47;  // "model/"
    d[6] = 112; d[7] = 48;  d[8] = 48;  d[9] = 48;                          // "p000"
    d[10] = 46; d[11] = 109; d[12] = 101; d[13] = 115; d[14] = 104; d[15] = 0; // ".mesh"
    return d;
}

// "host0:/cpv.bin" - the raw 4096 bytes lifted out of a city's .pck.
static const char *pal_path(void)
{
    char *d = st()->pal_path;
    d[0] = 104; d[1] = 111; d[2] = 115; d[3] = 116; d[4] = 48; d[5] = 58;   // "host0:"
    d[6] = 47;                                                              // "/"
    d[7] = 99; d[8] = 112; d[9] = 118;                                      // "cpv"
    d[10] = 46; d[11] = 98; d[12] = 105; d[13] = 110; d[14] = 0;            // ".bin"
    return d;
}

// Loads the palette file into the active heap and installs it. Returns the
// address, or 0. Reports the first entry so the log proves WHICH palette this
// was - a gray or empty one would be invisible otherwise.
static mc3_u32 palette_from_file(void)
{
    const mc3_u32 stream = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
                              (pal_path(), STREAM_RAW);
    if (!stream)
        return 0;
    const mc3_u32 buf = (mc3_u32)mc3_alloc(CPV_BYTES);
    mc3_u32 got = 0;
    if (sane(buf))
        got = MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)
                 (stream, buf, CPV_BYTES);
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    tag(83, 65, 80, 70); hex8(got); put(32); hex8(buf); put(32);      // SAPF <read> <buf>
    if (got == CPV_BYTES) {
        hex8(*(volatile mc3_u32 *)(buf + 0));  put(32);               // entry 0, r/g/b as
        hex8(*(volatile mc3_u32 *)(buf + 4));  put(32);               // raw float bits
        hex8(*(volatile mc3_u32 *)(buf + 8));
        put(10);
        return buf;
    }
    put(10);
    return 0;
}

extern "C" void standalone_probe_hook(mc3_u32 game)
{
    // No chain to mcGame::Execute: from here on this module IS the program.
    const mc3_u32 main_alloc = *(volatile mc3_u32 *)HEAP_ACTIVE;
    tag(83, 65, 70, 82); hex8(free_bytes(main_alloc)); put(32); hex8(main_alloc); put(10);

    const mc3_u32 want = free_bytes(main_alloc) > SLACK ? free_bytes(main_alloc) - SLACK : 0;
    const mc3_u32 heap = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP, mc3_u32, const char *)
                            (want, name());
    tag(83, 65, 77, 84); hex8(heap); put(32); hex8(want); put(10);

    if (sane(heap)) {
        MC3_CALL1(void, HEAP_PUSH, mc3_u32)(heap);

        // [boot] cpv = off    turn the indexed path off; no palette is consulted
        //        cpv = dummy  keep it on, but point sm_Current at a real 4 KB
        //                     palette of our own (all entries 0.5) so the walk
        //                     reads memory that exists. Content is not the
        //                     point here - the city's own palette is - only
        //                     whether the null read is what the faults were.
        const char *const cpv = mc3_bootarg(MC3_ID('c', 'p', 'v', 0));
        if (cpv && cpv[0] == 111) {                      // 'o' = off
            *(volatile unsigned char *)CPV_INDEXED = 0;
            tag(83, 65, 67, 79); hex8(0); put(10);       // SACO 0: gate off
        } else if (cpv && cpv[0] == 102) {               // 'f' = file
            const mc3_u32 pal = palette_from_file();
            if (sane(pal)) {
                *(volatile mc3_u32 *)CPV_CURRENT = pal;
                *(volatile unsigned char *)CPV_DIRTY = 1;
            }
            tag(83, 65, 67, 70); hex8(pal); put(10);     // SACF <palette installed>
        } else if (cpv && cpv[0] == 100) {               // 'd' = dummy
            const mc3_u32 pal = (mc3_u32)mc3_alloc(CPV_BYTES);
            if (sane(pal)) {
                for (mc3_u32 i = 0; i < CPV_BYTES; i += 4)
                    *(volatile mc3_u32 *)(pal + i) = 0x3F000000u;   // 0.5f
                *(volatile mc3_u32 *)CPV_CURRENT = pal;
                *(volatile unsigned char *)CPV_DIRTY = 1;
            }
            tag(83, 65, 67, 80); hex8(pal); put(10);     // SACP <palette>
        }
        const mc3_u32 root = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)
                                (path(), 0);
        const mc3_u32 used = *(volatile mc3_u32 *)(heap + ALLOC_USED);
        MC3_CALL(void, HEAP_POP)();
        tag(83, 65, 82, 79); hex8(root); put(32); hex8(used); put(10);

        // A checksum of everything the model occupies, so two boots with two
        // different palettes can be compared without dumping 30 KB twice. If
        // the palette really reaches the packets, this number moves.
        const mc3_u32 base = *(volatile mc3_u32 *)(heap + ALLOC_BASE);
        mc3_u32 sum = 0;
        for (mc3_u32 o = 0; o + 4 <= used; o += 4)
            sum = (sum << 1 | sum >> 31) ^ *(volatile mc3_u32 *)(base + o);
        tag(83, 65, 67, 75); hex8(sum); put(10);        // SACK <checksum>

    }

    tag(83, 65, 70, 50); hex8(free_bytes(*(volatile mc3_u32 *)HEAP_ACTIVE)); put(10);

    // Last, and only when asked: this is the call that hangs from the front end.
    const char *const font = mc3_bootarg(MC3_ID('f', 'o', 'n', 't'));
    if (font && font[0] != 48) {
        const mc3_u32 f = MC3_CALL(mc3_u32, TXT_GET_FONT_TEX)();
        tag(83, 65, 70, 84); hex8(f); put(10);
    }

    for (;;) { }
}

MC3_HOOK(SITE, standalone_probe_hook);
