// -----------------------------------------------------------------------------
//  city_probe - which city the frontend loads in the background, and what is
//  in the registry.
//
//  I need this to test a city .pck WITHOUT touching an existing city: if the
//  session's city index is a field I can point at the free slot (the one
//  city_slot6 registers), the frontend loads MY city by itself and the test no
//  longer depends on navigating the menu.
//
//  It prints: the registry array (0x00619D4C, six records), the session
//  pointer (0x00619B10) and its first word, which the C++ fork recorded as
//  being the city index.
//
//  The three rules of the .mod format apply here as in any module: mod_main in
//  .text.start, no data constants of its own, every fixed address behind a
//  noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    CITY_ARRAY = 0x00619D4C,   // &record[0]
    SESSION    = 0x00619B10,   // session pointer; +0 = city index
    // The layer says whether the city LOADED or is still on the loading screen:
    // GetLayer(e) is *(manager + 4 + 4*e), and layer 0 is City (8 is the
    // frontend). Rendering at 60 Hz does not tell the two apart; this does.
    LAYER_MANAGER_GET = 0x001A97B0,
    LAYER_CITY        = 0,
    GUARD     = 0x0061CAB8,
    DONE      = 0x43505233u,  // CPR3
    WAIT_FRAMES     = 600,
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static __attribute__((noinline)) volatile mc3_u32 *guard(void)
{
    return (volatile mc3_u32 *)GUARD;
}
static __attribute__((noinline)) mc3_u32 arr(void)
{
    return *(volatile mc3_u32 *)CITY_ARRAY;
}
static __attribute__((noinline)) mc3_u32 ses(void)
{
    return *(volatile mc3_u32 *)SESSION;
}

static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

// prints a short string a pointer points to, to read the city's NAME
static void print_name(mc3_u32 p)
{
    if (!sane(p & ~3u)) { put('?'); return; }
    for (int i = 0; i < 24; ++i) {
        unsigned char c = *(volatile unsigned char *)(p + i);
        if (!c) break;
        put((c >= 32 && c < 127) ? (char)c : '.');
    }
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    // PERIODIC SAMPLE. A single read does not tell "empty layer because it
    // hung" from "empty layer because it is still loading". Eight samples over
    // two minutes do: if it fills up, it loaded; if it stays the same from start
    // to end, it is stuck.
    volatile mc3_u32 *g = guard();
    mc3_u32 n = *g + 1u;
    *g = n;
    if (n < WAIT_FRAMES || (n - WAIT_FRAMES) % 600u != 0u) return;
    mc3_u32 sample = (n - WAIT_FRAMES) / 600u;
    if (sample > 3u) return;
    tag(78, 35, 48 + (char)sample, 32); hex8(n); put(10);   // N#k <frames>

    mc3_u32 a = arr();
    if (sample == 0) { tag('C','A','R','R'); hex8(a); put(10); }
    if (sane(a) && sample == 0) {
        // six records; the size of each comes from the dump itself
        for (int i = 0; i < 6; ++i) {
            mc3_u32 r = a + (mc3_u32)(i * 0x54);   /* measured: "sd" at +0 and "atlanta" at +0x54 */
            tag('C','I','T',(char)('0' + i));
            hex8(r); put(':'); put(' ');
            for (int k = 0; k < 4; ++k) { hex8(*(volatile mc3_u32 *)(r + 4u*k)); put(' '); }
            put('"'); print_name(*(volatile mc3_u32 *)r); put('"');
            put(10);
        }
    }
    mc3_u32 mgr = MC3_CALL(mc3_u32, LAYER_MANAGER_GET)();
    tag('L','M','G','R'); hex8(mgr); put(10);
    // ALL the layers, not just City. The frontend shows the last saved city in
    // the background; if it is not in layer 0, it is in another one, and a
    // sample of layer 0 alone answers nothing. 0 City, 1 Ambients,
    // 2 Conditions, 3 Race, 4 Garage, 5 Movie, 6 RaceEditor, 7 null,
    // 8 MC3Frontend.
    if (sane(mgr)) {
        for (int e = 0; e <= 8; ++e) {
            mc3_u32 layer = *(volatile mc3_u32 *)(mgr + 4u + 4u * (mc3_u32)e);
            tag(76, 65, 89, 48 + (char)e); hex8(layer); put(58); put(32);   // LAYk
            if (sane(layer))
                for (int k = 0; k < 6; ++k) { hex8(*(volatile mc3_u32 *)(layer + 4u*k)); put(32); }
            put(10);
        }
    }
    mc3_u32 s = ses();
    tag('S','E','S','S'); hex8(s); put(10);
    if (sane(s)) {
        tag('S','E','S',':');
        for (int k = 0; k < 8; ++k) { hex8(*(volatile mc3_u32 *)(s + 4u*k)); put(' '); }
        put(10);
    }
}
