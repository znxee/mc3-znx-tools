// -----------------------------------------------------------------------------
//  losangeles_page_pool - give Los Angeles a small texture page pool.
//
//  mcLayerCity (0x1A9450) creates the city's texture page manager with
//  0x42EA90(0x11000, 0x57800, 100, 1, 0): 100 pages of 0x11000 bytes, 6.8 MB
//  of the game heap (measured in a freeroam savestate: 100 blocks of 0x11080,
//  every byte still 0xCD). Los Angeles's city is drawn by city_mc2_rsc_draw_test,
//  its own .ppf is never asked for anything (overlay: "PPF123 resident 0/49"),
//  and that module is short of exactly this memory (CPVS, mipmaps).
//
//  Only when the city being loaded is Los Angeles (mcRaceConfig at 0x619B10,
//  +0 = city index 5) the count becomes LOSANGELES_PAGES; every other city gets
//  its 100 untouched. Load it as `shim`: with [boot] nofe the city is loaded
//  before the first frame, too early for a `defer` hook.
//
//  Marker: RPPL <city << 16 | pages asked> <pages given>
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../mc2_city_config.h"

enum {
    PAGE_POOL_CALL = 0x001A94A4,        // jal 0x42EA90 inside mcLayerCity
    PAGE_POOL_CREATE = 0x0042EA90,
    RACE_CONFIG_CURRENT = 0x00619B10,   // mcRaceConfig*; +0 = city index
    MEM_AVAIL = 0x003AFBA8,             // MemAvail(false): active heap top - cursor
    LOSANGELES_PAGES = 8,
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static __attribute__((noinline)) mc3_u32 current_config(void)
{
    return *(volatile mc3_u32 *)RACE_CONFIG_CURRENT;
}

extern "C" mc3_u32 page_pool_hook(mc3_u32 page, mc3_u32 extra, mc3_u32 count,
                                  mc3_u32 flag, mc3_u32 last)
{
    const mc3_u32 cfg = current_config();
    const mc3_u32 city = cfg >= 0x00100000u && cfg < 0x02000000u
        ? *(volatile mc3_u32 *)cfg : 0xFFFFFFFFu;
    const mc3_u32 asked = count;
    if (mc2_city_supported(city) && count > LOSANGELES_PAGES) count = LOSANGELES_PAGES;
    put('R'); put('P'); put('P'); put('L'); put(' ');
    hex8((city << 16) | (asked & 0xFFFFu)); put(' '); hex8(count); put('\n');
    // The pool (sub_42C5C0) takes min(count, 2 * (MemAvail - extra) / page)
    // pages, `extra` being the reserve it must leave free (0x200000 from mcLayerCity). MemAvail
    // is only the gap between the active heap's cursor and its top - the holes
    // below the cursor do not count - and leaving a RACE back to the front end
    // (EnterStateMC3Frontend 0x1A5B08 recreates the pool every time) left it
    // at 0x1C5320 in Paris (1.8 MB, with 17 MB free in holes): the count went negative,
    // vec_new got a negative size and its debug fill ran past 0x2000000 (EE at
    // 99%, pc 0x42E760). With less than the reserve plus two pages above the
    // cursor the pool gets NO pages (reserve = MemAvail, count 0): the cursor
    // is a high-water mark that never comes down, and the front end entered
    // next creates its NetHeap (300 KB) and LoadHeap (1.1 MB) from the top
    // above it - pages taken here (an earlier version halved the reserve) left
    // the next exit 1.1 MB, "not enough main heap memory for memtop heap" and
    // a null heap (TLB misses at 0x3AFE40, Paris then Tokyo MC2). The front
    // end over an empty MC2/PSP city asks the pool for nothing.
    // Marker RPPR <MemAvail> <reserve used>.
    const mc3_u32 avail = MC3_CALL1(mc3_u32, MEM_AVAIL, int)(0);
    if ((int)avail < (int)(extra + 2u * page)) {
        extra = (int)avail > 0 ? avail : 0u;
        put('R'); put('P'); put('P'); put('R'); put(' ');
        hex8(avail); put(' '); hex8(extra); put('\n');
    }
    return MC3_CALL5(mc3_u32, PAGE_POOL_CREATE, mc3_u32, mc3_u32, mc3_u32,
                     mc3_u32, mc3_u32)(page, extra, count, flag, last);
}
MC3_HOOK(PAGE_POOL_CALL, page_pool_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    /* Nothing per frame: the work is in the hook. */
}
