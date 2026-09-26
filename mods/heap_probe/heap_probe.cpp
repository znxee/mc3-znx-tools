// -----------------------------------------------------------------------------
//  heap_probe - what the private heap API and the SIO terminal look like in use
//
//  Two jobs. It is the acceptance test for payload/mc3_heap.h and
//  payload/mc3_sio.h - if this builds with ZERO hi/lo relocations then those
//  headers cost a module nothing - and it answers the question anyone carving a
//  heap asks first: how much is actually there, right now, in this state of the
//  game.
//
//  It reports, in the PCSX2 log, with no PINE and no savestate:
//
//      ALIV <free in the main heap>      as soon as the allocator is up
//      FREE <free>                       once more when it has settled
//      HEAP <base>                       the private heap it carved
//      SIZE <bytes>                      what it got
//      BLOB/ENDB                         a short dump, to prove the channel
//      DONE <free after>
//
//  Read it with:  grep -E "ALIV|FREE|HEAP|SIZE|DONE" emulog.txt
//  or feed the BLOB to mc3_heap_dump.py, which already parses that format.
//
//  Change PROBE_BYTES to ask for more. Asking for more than there is is not a
//  crash: mc3_heap_create returns 0 and SIZE reports 0, because the two tests
//  CreateMemTopHeap would fail at are done before it is called.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_heap.h"
#include "../../payload/mc3_sio.h"

enum {
    PROBE_BYTES = 0x10000,      // 64 KB - enough to prove the path, small
                                // enough to fit beside a resident frontend
    SETTLE_FRAMES = 120,        // AFTER the heap is up: let the game finish
                                // whatever it was allocating, so FREE is a
                                // number worth quoting
    DUMP_BYTES = 64,            // a token blob; the channel is what is proven
};

struct probe_state {
    mc3_u32 stage;
    mc3_u32 ticks;
    mc3_u32 heap;
};

static probe_state g_state;

// One accessor for the one data base - loose symbols make GCC share a `lui`
// across several `%lo` and the packer refuses the build.
static __attribute__((noinline)) probe_state *st(void) { return &g_state; }

extern "C" void mod_main(void) __attribute__((section(".text.start")));
extern "C" void mod_main(void)
{
    probe_state *const s = st();

    switch (s->stage) {
    case 0:
        // Not a frame count: wait for a real allocator. See mc3_heap.h.
        if (!mc3_heap_ready())
            return;
        mc3_sio_mark(65, 76, 73, 86, mc3_heap_free(mc3_heap_active()));  /* ALIV */
        s->stage = 1;
        return;

    case 1:
        if (++s->ticks < SETTLE_FRAMES)
            return;
        mc3_sio_mark(70, 82, 69, 69, mc3_heap_free(mc3_heap_active()));  /* FREE */
        s->stage = 2;
        return;

    case 2: {
        const mc3_u32 h = mc3_heap_create(PROBE_BYTES);
        s->heap = h;
        mc3_sio_mark(72, 69, 65, 80, mc3_heap_base(h));                  /* HEAP */
        mc3_sio_mark(83, 73, 90, 69, h ? PROBE_BYTES : 0u);              /* SIZE */
        if (!h) {
            s->stage = 4;
            return;
        }
        // Anything built between push and pop lands in the private heap, and
        // every pointer in it falls inside - which is the shape of a .pck body.
        mc3_heap_push(h);
        mc3_heap_pop();
        s->stage = 3;
        return;
    }

    case 3:
        // The heap is deliberately NOT released: giving the range back means
        // the next allocation writes over exactly what was meant to be dumped.
        mc3_sio_blob(mc3_heap_base(s->heap), DUMP_BYTES);
        mc3_sio_mark(68, 79, 78, 69, mc3_heap_free(mc3_heap_active()));  /* DONE */
        s->stage = 4;
        return;

    default:
        return;
    }
}

// No hook: a per-frame module, like mc2_probe and mesh_dump.
