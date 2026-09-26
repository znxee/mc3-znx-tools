// Disposable probe: reports every time mcGameState's own top-level state
// field (the switch ChangeState writes to at +4) changes. Not a feature -
// exists only to confirm race_nofe's queued DoLoadRace actually resulted in
// ChangeState(2), since a headless boot has no video to look at instead.
#include "../../payload/mc3_mod.h"

enum {
    GAMESTATE_PTR = 0x00619958,   // dword_619958, set by mcGameState::CreateInstance
};

struct sw_state {
    mc3_u32 have_seen;
    mc3_u32 last;
    mc3_u32 calls;
};
static sw_state g_state;
static __attribute__((noinline)) sw_state *st(void) { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    sw_state *const s = st();
    ++s->calls;
    if (s->calls == 1u) {
        tag('S', 'W', 'H', 'B');   // SWHB - proof mod_main is being dispatched at all
        put(10);
    }
    const mc3_u32 obj = *(volatile mc3_u32 *)GAMESTATE_PTR;
    if (!sane(obj))
        return;
    const mc3_u32 state = *(volatile mc3_u32 *)(obj + 4);
    if (!s->have_seen) {
        s->have_seen = 1u;
        s->last = state;
        tag('S', 'W', 'I', 'N');   // SWIN <first observed value>
        hex8(state); put(10);
        return;
    }
    if (state != s->last) {
        tag('S', 'W', 'C', 'H');   // SWCH <old> <new>
        hex8(s->last); put(' '); hex8(state); put(10);
        s->last = state;
    }
}
