// -----------------------------------------------------------------------------
//  city_index_watch - WHEN does the frontend's city index change, not just what
//  it ends up as.
//
//  city_probe.mod already reads *(*(0x00619B10)) - the current-city index the
//  frontend background reads - but only at a handful of checkpoints starting
//  ten seconds in. That answers "what". This answers "when", by polling every
//  frame from the first one core.mod runs a deferred module (frame 1) and
//  printing ONLY on a change, with the frame number attached.
//
//  WHY THIS MATTERS for finding the memory-card write
//
//  The session object (0x00619B10, one of three mcRaceConfig instances built
//  at Main_void+0x1A0F74) is constructed with whatever default its ctor
//  (sub_4BDAC8) leaves at +0. If the index is ALREADY the saved value by frame
//  1, the memory-card restore happens synchronously during boot, before any
//  frame renders - which rules out a lazy/async load and points back at
//  Main's own init sequence rather than at frontend code. If it changes
//  LATER, the frame number brackets a much smaller code range to search than
//  the whole executable.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    SESSAO = 0x00619B10,
};

struct watch_state {
    mc3_u32 frame;
    mc3_u32 last_value;
    mc3_u32 have_last;
    mc3_u32 first_ptr_frame;   /* frame the session pointer first became valid */
};
static watch_state g_state;
static __attribute__((noinline)) watch_state *st(void) { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }

static int sane(mc3_u32 p)
{
    return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    watch_state *const s = st();
    s->frame += 1u;

    const mc3_u32 ptr = *(volatile mc3_u32 *)SESSAO;
    if (!sane(ptr)) {
        // Not constructed yet. Cap the noise: only say so on the very first
        // frame, so a long wait is silence, not a flood.
        if (s->frame == 1u) { tag('C','I','W','0'); hex8(ptr); put(10); }
        return;
    }
    if (!s->first_ptr_frame) {
        s->first_ptr_frame = s->frame;
        tag('C','I','W','P'); hex8(s->frame); put(' '); hex8(ptr); put(10);
    }

    const mc3_u32 value = *(volatile mc3_u32 *)ptr;
    if (!s->have_last || value != s->last_value) {
        tag('C','I','W','C');            // CIWC <frame> <old> -> <new>
        hex8(s->frame); put(' ');
        hex8(s->last_value); put(' ');
        hex8(value); put(10);
        s->last_value = value;
        s->have_last = 1u;
    }
}
