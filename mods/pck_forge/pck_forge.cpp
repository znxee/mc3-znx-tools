// -----------------------------------------------------------------------------
//  pck_forge - the game as a geometry compiler, with no game running.
//
//  WHAT IT IS
//
//  Main_void's last call is `jal mcGame::Execute` at 0x001A1024, and Execute is
//  nothing but `while (2) { ... }` - it initialises nothing before its loop. So
//  by that instruction the engine is fully up and NOTHING is loaded: no city,
//  no front end, no movie, no state machine. Hooking that `jal` and never
//  chaining to it turns the executable into a batch program that happens to
//  contain Rockstar's mesh compiler.
//
//  Measured in that state, against the front end for comparison:
//
//      main heap free      25 254 912 bytes   (1 456 656 with the front end)
//      rmcModel::Create    works, with no frame pump of any kind
//
//  WHAT IT DOES, per boot
//
//    1. loads the colour-per-vertex palette from host0:/cpv.bin and installs it
//       in rmcCpvPalette::sm_Current - see THE PALETTE below;
//    2. takes almost the whole main heap as one private top heap;
//    3. builds model/p000.mesh .. p<N-1>.mesh inside it, N from [boot] mdls;
//    4. dumps that heap in hex over SIO, in the format mc3_heap_dump.py and
//       mc3_heap_to_pck.py already read (BLOB/offset lines/ENDB, plus a ROOT
//       line per model), so nothing downstream had to change;
//    5. stops. mc3_pck_forge.py drives all of it from the host side.
//
//  USE .mesh, NOT .mod: the .mod branch of rmcModel::Create builds the skeleton
//  and stops (packets with a pointer and size zero). Only the .mesh branch
//  reaches rmcGeometry::Packet::Init, which is what emits VIF.
//
//  THE PALETTE, and why getting it wrong is invisible
//
//  rmcGeometry::Packet::Init turns each vertex colour into an INDEX by looking
//  it up in the palette at rmcCpvPalette::sm_Current (0x0070FA40) - the gate
//  byte rmcCpvIndexed (0x00615D00) is 1 in the image, so this path is on.
//  mcCity's constructor is what normally installs that palette, from the city
//  object's +0x2C, so with no city it is NULL and the lookup reads address 0.
//
//  The palette is inline in a city's own .pck at MapRoot+0x290, 256 entries of
//  Vector4 float, and it differs per city AND per condition: Atlanta's entry 1
//  is (0.376,0.373,0.784) at dawn/clear and (0.800,0.404,0.400) at dusk/rainy.
//  Measured: the same mesh built against two different palettes gives the same
//  root and the same size but a different checksum. Build with the wrong one
//  and nothing reports anything - the colours are just wrong. So the host side
//  chooses it explicitly, and this module refuses to run the CPV path against a
//  palette it did not install: with no cpv.bin it turns the gate OFF instead,
//  which produces packets with no CPV indices rather than packets with wrong
//  ones.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"

enum {
    SITE                = 0x001A1024,   // jal mcGame::Execute - never chained

    HEAP_ACTIVE         = 0x0070E500,
    ALLOC_BASE          = 0x04,
    ALLOC_SIZE          = 0x08,
    ALLOC_USED          = 0x0C,
    HEAP_CREATE_MEMTOP  = 0x004BD650,
    HEAP_PUSH           = 0x004BD5D8,
    HEAP_POP            = 0x004BD5F0,

    RMC_MODEL_CREATE    = 0x002A8CA0,

    CPV_INDEXED         = 0x00615D00,
    CPV_CURRENT         = 0x0070FA40,
    CPV_DIRTY           = 0x0070FA3C,
    CPV_BYTES           = 256 * 16,

    STREAM_OPEN         = 0x003991F0,
    STREAM_READ         = 0x003993A8,
    STREAM_CLOSE        = 0x00399748,
    STREAM_RAW          = 1,

    SLACK               = 64 * 1024,
    MAX_MODELS          = 999,          // the path has three digits
};

struct forge_state {
    char heap_name[12];
    char pal_path[16];
    char mesh_path[20];
};
static forge_state g_state;
static __attribute__((noinline)) forge_state *st(void) { return &g_state; }

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

static mc3_u32 parse_uint(const char *s)
{
    mc3_u32 v = 0;
    for (; s && *s >= 48 && *s <= 57; ++s)
        v = v * 10u + (mc3_u32)(*s - 48);
    return v;
}

// Character codes, never literals: a string literal is a second data base and
// mc3_mkmod refuses the build (docs/WRITING_A_MOD.md).
static const char *heap_name(void)
{
    char *d = st()->heap_name;
    d[0] = 102; d[1] = 111; d[2] = 114; d[3] = 103; d[4] = 101; d[5] = 0;   // "forge"
    return d;
}
static const char *pal_path(void)
{
    char *d = st()->pal_path;
    d[0] = 104; d[1] = 111; d[2] = 115; d[3] = 116; d[4] = 48; d[5] = 58;   // "host0:"
    d[6] = 47; d[7] = 99; d[8] = 112; d[9] = 118;                           // "/cpv"
    d[10] = 46; d[11] = 98; d[12] = 105; d[13] = 110; d[14] = 0;            // ".bin"
    return d;
}
// "model/pNNN.mesh" - three digits, because with one the tenth model became
// "model/p:.mesh" (':' is what follows '9').
static const char *mesh_path(mc3_u32 i)
{
    char *d = st()->mesh_path;
    d[0] = 109; d[1] = 111; d[2] = 100; d[3] = 101; d[4] = 108; d[5] = 47;  // "model/"
    d[6] = 112;                                                             // "p"
    d[7] = (char)(48 + (i / 100) % 10);
    d[8] = (char)(48 + (i / 10) % 10);
    d[9] = (char)(48 + i % 10);
    d[10] = 46; d[11] = 109; d[12] = 101; d[13] = 115; d[14] = 104; d[15] = 0;  // ".mesh"
    return d;
}

static mc3_u32 free_bytes(mc3_u32 alloc)
{
    if (!sane(alloc))
        return 0;
    return *(volatile mc3_u32 *)(alloc + ALLOC_SIZE) - *(volatile mc3_u32 *)(alloc + ALLOC_USED);
}

// Reads cpv.bin into the MAIN heap - before the private one is carved out, so
// the palette is not part of what gets dumped - and installs it.
static mc3_u32 install_palette(void)
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
    if (got != CPV_BYTES)
        return 0;
    *(volatile mc3_u32 *)CPV_CURRENT = buf;
    *(volatile unsigned char *)CPV_DIRTY = 1;
    return buf;
}

static void dump(mc3_u32 base, mc3_u32 n)
{
    for (mc3_u32 off = 0; off < n; off += 32u) {
        hex8(off); put(58);
        for (mc3_u32 k = 0; k < 32u && off + k < n; ++k) {
            const unsigned b = *(volatile unsigned char *)(base + off + k);
            const unsigned hi = (b >> 4) & 0xF, lo = b & 0xF;
            put((char)(hi < 10 ? (48 + hi) : (55 + hi)));
            put((char)(lo < 10 ? (48 + lo) : (55 + lo)));
        }
        put(10);
    }
}

extern "C" void pck_forge_hook(mc3_u32 game)
{
    // From here on this module is the whole program: mcGame::Execute is not
    // called, so no state is ever entered and nothing else runs.
    const mc3_u32 main_alloc = *(volatile mc3_u32 *)HEAP_ACTIVE;
    tag(80, 70, 82, 69); hex8(free_bytes(main_alloc)); put(10);     // PFRE <free>

    const mc3_u32 pal = install_palette();
    if (!pal)
        *(volatile unsigned char *)CPV_INDEXED = 0;   // no palette: no CPV, not a wrong CPV
    tag(80, 70, 80, 76); hex8(pal); put(10);                        // PFPL <palette|0>

    // THE SPACER, and why it is not decoration.
    //
    // The rebuilder finds each model by walking pointers, so a word of vertex
    // data that happens to equal a block address welds a neighbour's geometry
    // onto the closure. That trap is in mc3_heap_to_pck's own notes, but it is
    // far worse here than in the front-end state, and the reason is the
    // ADDRESS: with nothing loaded the private heap lands around 0x007Fxxxx,
    // which packed 16-bit vertex data spells constantly, while the front-end
    // state put it at 0x0176xxxx, which data almost never does. Measured with
    // the heap low: 237 of 298 LA components came out with a neighbour welded
    // on, one of them 1.8 MB out of a 3.3 MB heap.
    //
    // Burning the low part of the free store first pushes the working heap
    // into that same rare range. Rewinding the allocator between models was
    // tried instead and is WRONG: the bump pointer is not the whole story
    // (16-byte block headers in a list), and that run built one model and
    // stalled.
    const mc3_u32 spacer_mb = parse_uint(mc3_bootarg(MC3_ID('s', 'p', 'a', 'c')));
    if (spacer_mb)
        mc3_alloc(spacer_mb * 1024u * 1024u);

    mc3_u32 count = parse_uint(mc3_bootarg(MC3_ID('m', 'd', 'l', 's')));
    if (count == 0) count = 1;
    if (count > MAX_MODELS) count = MAX_MODELS;

    const mc3_u32 want = free_bytes(main_alloc) > SLACK
                       ? free_bytes(main_alloc) - SLACK : 0;
    const mc3_u32 heap = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP, mc3_u32, const char *)
                            (want, heap_name());
    tag(80, 70, 72, 80); hex8(heap); put(32); hex8(want); put(32); hex8(count); put(10);
    if (!sane(heap)) {                                           // PFHP <heap> <bytes> <n>
        tag(80, 70, 69, 82); hex8(0); put(10);                      // PFER
        for (;;) { }
    }
    tag(80, 70, 66, 83); hex8(*(volatile mc3_u32 *)(heap + ALLOC_BASE)); put(10);  // PFBS <base>

    // One heap for every model: each root is printed and mc3_heap_to_pck.py
    // --lote extracts each one's transitive closure from the SAME dump.
    MC3_CALL1(void, HEAP_PUSH, mc3_u32)(heap);
    for (mc3_u32 i = 0; i < count; ++i) {
        const mc3_u32 root = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)
                                (mesh_path(i), 0);
        tag(82, 79, 79, 84);                                        // ROOT <nnn> <root> <used>
        put((char)(48 + (i / 100) % 10));
        put((char)(48 + (i / 10) % 10));
        put((char)(48 + i % 10)); put(32);
        hex8(root); put(32); hex8(*(volatile mc3_u32 *)(heap + ALLOC_USED)); put(10);
    }
    MC3_CALL(void, HEAP_POP)();

    const mc3_u32 base = *(volatile mc3_u32 *)(heap + ALLOC_BASE);
    const mc3_u32 used = *(volatile mc3_u32 *)(heap + ALLOC_USED);
    tag(66, 76, 79, 66); hex8(base); put(32); hex8(used); put(10);  // BLOB <base> <bytes>
    dump(base, used);
    tag(69, 78, 68, 66); hex8(used); put(10);                       // ENDB <bytes>

    tag(80, 70, 68, 78); put(10);                                   // PFDN: done
    for (;;) { }
}

MC3_HOOK(SITE, pck_forge_hook);
