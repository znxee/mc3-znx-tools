// -----------------------------------------------------------------------------
//  mesh_pck_dump - lets the GAME build the mesh and dumps the heap OVER THE
//  CONSOLE.
//
//  ITS OWN NAME ON PURPOSE. There is a mesh_dump from the C++ FORK in the
//  folder next door, and it already overwrote me once: same directory, same
//  file installed as $MC3_HOSTFS/mesh_dump.mod. Both solve the same problem by
//  different routes and both are valid, so they now live side by side.
//
//  WHAT THIS ONE DOES DIFFERENTLY: the dump goes out over SIO (0x1000F180) in
//  hexadecimal, between a BLOB line and an ENDB line, and mc3_heap_dump.py
//  rebuilds it from the PCSX2 log. It needs no savestate and no user action,
//  which closes the iteration loop - 148 KB came out in 0.024 s of emulated
//  time.
//
//  WHAT I TOOK FROM THE C++ FORK: unload the frontend first, with
//  mcLayerManager::UnloadDownToSpecifiedLayer(floor 8), and measure the free
//  memory BEFORE and AFTER. Without that I measured 1456656 bytes free and a
//  27-material model took 169904 - one model per boot.
//
//  USE .mesh, NOT .mod. Measured: the .mod branch of rmcModel::Create builds
//  the skeleton and stops (packets with the pointer set and size ZERO; the
//  root's transitive closure reaches 390 bytes of a 148 KB heap). Through .mesh
//  the same model reaches 46 KB in blocks that start with VIF, because only
//  that branch gets to rmcGeometry::Packet::Init.
//
//  THREE RULES OF THE .mod FORMAT, all learned the hard way from the packer:
//    * mod_main has to live in .text.start, with the mod.ld that puts that
//      section first; with the mod.ld of a hook-only module it loads and never
//      runs;
//    * no data constants of its own - no string, no table: they become a
//      section with its own base and the relocation does not close ("LO16
//      without HI16"). Names are built on the STACK, with a numeric suffix;
//    * every fixed address behind a noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    LAYER_MANAGER_GET   = 0x001A97B0,   // mcLayerManager::GetInstance()
    LAYER_UNLOAD_DOWNTO = 0x001A98E8,   // (this, floor, keep)
    LAYER_FRONTEND      = 8,

    HEAP_CREATE_MEMTOP  = 0x004BD650,   // (bytes, name) -> memMemoryAllocator*
    HEAP_PUSH           = 0x004BD5D8,
    HEAP_POP            = 0x004BD5F0,
    HEAP_ACTIVE         = 0x0070E500,

    ALLOC_BASE          = 0x04,
    ALLOC_SIZE          = 0x08,
    ALLOC_USED          = 0x0C,

    RMC_MODEL_CREATE    = 0x002A8CA0,

    GUARD               = 0x0061CABC,
    DONE                = 0x50434B34u,
    WAIT_FRAMES         = 600,
    MODELS              = 47,    // Fork City PARIS, batch E (map in
                                // output/paris_stage/staging_paris_E.json)
    SLACK               = 64 * 1024,
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static __attribute__((noinline)) volatile mc3_u32 *guard(void)
{
    return (volatile mc3_u32 *)GUARD;
}
static __attribute__((noinline)) mc3_u32 allocator(void)
{
    return *(volatile mc3_u32 *)HEAP_ACTIVE;
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

// free = size - used. Measured: the game prints "(%d requested, %d available)"
// when CreateMemTopHeap does not fit, and its number matched EXACTLY this
// subtraction on the main heap - 1456656.
static mc3_u32 free_bytes(mc3_u32 a)
{
    if (!sane(a)) return 0;
    return *(volatile mc3_u32 *)(a + ALLOC_SIZE) - *(volatile mc3_u32 *)(a + ALLOC_USED);
}

static void dump_block(mc3_u32 base, mc3_u32 n)
{
    for (mc3_u32 off = 0; off < n; off += 32u) {
        hex8(off); put(58);
        for (mc3_u32 k = 0; k < 32u && off + k < n; ++k) {
            unsigned b = *(volatile unsigned char *)(base + off + k);
            unsigned hi = (b >> 4) & 0xF, lo = b & 0xF;
            put((char)(hi < 10 ? (48 + hi) : (55 + hi)));
            put((char)(lo < 10 ? (48 + lo) : (55 + lo)));
        }
        put(10);
    }
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *g = guard();
    if (*g == DONE) return;
    if (*g < WAIT_FRAMES) { *g = *g + 1u; return; }
    *g = DONE;

    mc3_u32 before = free_bytes(allocator());
    tag(70, 82, 69, 69); hex8(before); put(10);             // FREE

    // Unload the frontend. Floor 8 makes the loop run only for layer 8; `keep`
    // is any layer != 8, and 0 (City) is the natural choice because City is not
    // even loaded in the frontend.
    mc3_u32 mgr = MC3_CALL(mc3_u32, LAYER_MANAGER_GET)();
    tag(76, 77, 71, 82); hex8(mgr); put(10);                // LMGR
    if (mgr)
        MC3_CALL3(void, LAYER_UNLOAD_DOWNTO, mc3_u32, mc3_u32, mc3_u32)
            (mgr, LAYER_FRONTEND, 0u);

    mc3_u32 after = free_bytes(allocator());
    tag(70, 82, 69, 50); hex8(after); put(32); hex8(after - before); put(10);  // FRE2

    // Ask for almost everything left. If it does not fit, the game prints by
    // itself how many bytes there were - the number comes for free instead of
    // by bisection.
    mc3_u32 requested = (after > SLACK) ? (after - SLACK) : 0;
    char name[12];
    name[0]=112; name[1]=99; name[2]=107; name[3]=100;      // "pckdump"
    name[4]=117; name[5]=109; name[6]=112; name[7]=0;
    mc3_u32 h = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP, mc3_u32, const char *)(requested, name);
    tag(77, 84, 79, 80); hex8(h); put(32); hex8(requested); put(10);   // MTOP
    if (!sane(h)) { for (;;) {} }

    // One heap for all the models: each root is printed, and
    // mc3_heap_to_pck.py extracts the transitive closure of each one from the
    // SAME dump.
    MC3_CALL1(void, HEAP_PUSH, mc3_u32)(h);
    // TWO-DIGIT NAME. With a single digit the loop stopped at 9 and the tenth
    // model became "model/p:.mesh" - ':' is the character after '9'.
    char path[24];
    for (int t = 0; t < MODELS; ++t) {
        int i = 0;
        path[i++]=109; path[i++]=111; path[i++]=100; path[i++]=101;
        path[i++]=108; path[i++]=47;                        // "model/"
        path[i++]=112;                                      // "p"
        path[i++]=(char)(48 + t / 100);
        path[i++]=(char)(48 + (t / 10) % 10);
        path[i++]=(char)(48 + t % 10);
        path[i++]=46;  path[i++]=109; path[i++]=101;
        path[i++]=115; path[i++]=104; path[i]=0;            // ".mesh"
        mc3_u32 r = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)(path, 0);
        tag(82, 79, 79, 84);                                // ROOT
        put((char)(48 + t / 100)); put((char)(48 + (t / 10) % 10));
        put((char)(48 + t % 10)); put(32);
        hex8(r); put(32); hex8(*(volatile mc3_u32 *)(h + ALLOC_USED)); put(10);
    }
    MC3_CALL(void, HEAP_POP)();

    mc3_u32 base = *(volatile mc3_u32 *)(h + ALLOC_BASE);
    mc3_u32 used = *(volatile mc3_u32 *)(h + ALLOC_USED);
    tag(66, 76, 79, 66); hex8(base); put(32); hex8(used); put(10);   // BLOB
    dump_block(base, used);
    tag(69, 78, 68, 66); hex8(used); put(10);                        // ENDB

    // Stops on purpose: with the frontend unloaded the next frame draws a dead
    // layer, and hanging with everything already written is better than a crash
    // that could scribble over the end of the dump.
    for (;;) {}
}
