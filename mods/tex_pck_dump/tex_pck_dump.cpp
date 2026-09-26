// -----------------------------------------------------------------------------
//  tex_pck_dump - lets the GAME build the textures and dumps the heap.
//
//  SAME RECIPE AS mesh_pck_dump, and for the same reason: instead of reversing
//  the `datChunkRef` encoding (12 bytes from +0x48 of rmcTexturePS2, which shows
//  up in TWO different forms inside the same atlanta file), I ask the game to
//  build the object and take the finished result.
//
//  rmcTextureFactoryPS2::Create(factory, name) 0x002B9BD8: looks it up in the
//  hash with AttachToExistingTexture; if not found it does `new(160)`, builds by
//  name and calls vtable[+0x30] to load. The `new` comes from the ACTIVE
//  ALLOCATOR, so a PushHeap captures the object - and, if the loader also
//  allocates the pixels there, they come in the dump and the .pck comes out
//  self-contained, without a .ppf.
//
//  THE FACTORY IS FOUND BY SWEEPING THE RAM. mcCityTextureFactory::Create is in
//  slot 1 of vtable 0x00635C78, and the factory itself passes `this` as the PS2
//  factory (measured at 0x00596338: `move a0,s2`). So any object whose first
//  word is that vtable will do. Sweeping is cheaper than finding the global.
//
//  THREE RULES OF THE .mod FORMAT: mod_main in .text.start; no data constants of
//  its own (name built on the STACK, with a numeric suffix and characters as
//  NUMERIC literals so no data base is created); every fixed address behind a
//  noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    LAYER_MANAGER_GET   = 0x001A97B0,
    LAYER_UNLOAD_DOWNTO = 0x001A98E8,
    LAYER_FRONTEND      = 8,

    HEAP_CREATE_MEMTOP  = 0x004BD650,
    HEAP_PUSH           = 0x004BD5D8,
    HEAP_POP            = 0x004BD5F0,
    HEAP_ACTIVE         = 0x0070E500,
    ALLOC_BASE          = 0x04,
    ALLOC_SIZE          = 0x08,
    ALLOC_USED          = 0x0C,

    NEW_160             = 0x003B14D0,   // operator new, from the ACTIVE allocator
    TEX_CTOR_NAME       = 0x002B7088,   // rmcTexturePS2::rmcTexturePS2(char const*)
                                        // reads the image from disc via gfxLoadImageAll

    GUARD              = 0x0061CAC0,
    DONE               = 0x54455838u,  // 'TEX1'
    WAIT_FRAMES              = 2400,   // 40 s: the factory only exists after the city loads
    FIRST            = 340,            // t<FIRST> .. t<FIRST+COUNT-1>
    COUNT             = 170,
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

// Sweeps the RAM looking for an object whose first word is the city texture
// factory's vtable. Step of 16 because the allocator aligns that way.
// THE FACTORY IS NOT NEEDED, and that came from the C++ fork (log of 09/03):
// Create(name) only checks the cache and, if not found, `new(160)` plus
// rmcTexturePS2::rmcTexturePS2(char const*) 0x002B7088 - and IT IS THE
// CONSTRUCTOR that loads the image from disc, via off_61C2B0 = gfxLoadImageAll
// 0x00526538. I do not want the cache, I want the object: I call the
// constructor directly. That gets round there being no mcCityTextureFactory in
// RAM while the frontend is up - I swept 0x006A0000..0x02000000 and got ZERO.

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    // THE GUARD DOES NOT START AT ZERO. mesh_pck_dump's window (0x0061CABC) came
    // zeroed and this one (0x0061CAC0) does not: `*g < WAIT_FRAMES` was false on
    // the first call and the module acted at once, at 15.7 s, before the city
    // existed - and the symptom was "factory not found", which looked like a
    // sweep problem. Any absurd value becomes zero.
    volatile mc3_u32 *g = guard();
    if (*g == DONE) return;
    if (*g > (mc3_u32)(WAIT_FRAMES * 4)) *g = 0u;
    if (*g < WAIT_FRAMES) { *g = *g + 1u; return; }
    *g = DONE;

    mc3_u32 before = free_bytes(allocator());
    tag(70, 82, 69, 69); hex8(before); put(10);          // FREE
    mc3_u32 mgr = MC3_CALL(mc3_u32, LAYER_MANAGER_GET)();
    if (mgr)
        MC3_CALL3(void, LAYER_UNLOAD_DOWNTO, mc3_u32, mc3_u32, mc3_u32)
            (mgr, LAYER_FRONTEND, 0u);
    mc3_u32 after = free_bytes(allocator());
    tag(70, 82, 69, 50); hex8(after); put(10);         // FRE2

    mc3_u32 requested = (after > SLACK) ? (after - SLACK) : 0;
    char name[12];
    name[0]=116; name[1]=101; name[2]=120; name[3]=100; // "texdump"
    name[4]=117; name[5]=109; name[6]=112; name[7]=0;
    mc3_u32 h = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP, mc3_u32, const char *)(requested, name);
    tag(77, 84, 79, 80); hex8(h); put(32); hex8(requested); put(10);
    if (!sane(h)) { for (;;) {} }

    MC3_CALL1(void, HEAP_PUSH, mc3_u32)(h);
    char tn[8];
    for (int k = 0; k < COUNT; ++k) {
        int t = FIRST + k;
        tn[0]=116;                                       // "t"
        tn[1]=(char)(48 + t / 100);
        tn[2]=(char)(48 + (t / 10) % 10);
        tn[3]=(char)(48 + t % 10);
        tn[4]=0;
        mc3_u32 o = MC3_CALL1(mc3_u32, NEW_160, mc3_u32)(160u);
        mc3_u32 r = o ? MC3_CALL2(mc3_u32, TEX_CTOR_NAME, mc3_u32, const char *)(o, tn) : 0u;
        tag(84, 69, 88, 32);                             // "TEX "
        put((char)(48 + t / 100)); put((char)(48 + (t / 10) % 10));
        put((char)(48 + t % 10)); put(32);
        hex8(r); put(32); hex8(*(volatile mc3_u32 *)(h + ALLOC_USED)); put(10);
    }
    MC3_CALL(void, HEAP_POP)();

    mc3_u32 base  = *(volatile mc3_u32 *)(h + ALLOC_BASE);
    mc3_u32 used = *(volatile mc3_u32 *)(h + ALLOC_USED);
    tag(66, 76, 79, 66); hex8(base); put(32); hex8(used); put(10);
    dump_block(base, used);
    tag(69, 78, 68, 66); hex8(used); put(10);
    for (;;) {}
}
