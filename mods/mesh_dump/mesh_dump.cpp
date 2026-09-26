// -----------------------------------------------------------------------------
//  mesh_dump - unload the frontend, build meshes into a private heap, and say
//              exactly where that heap is so a savestate can be cut out of it.
//
//  WHY THIS EXISTS
//
//  The game builds the SAME structure a .pck holds when it reads a mesh in
//  text: rmcModel::Create parses into a temporary mshMesh, hands it to
//  rmcModelGeom::Load, and throws the mshMesh away. What survives is an
//  rmcModelGeom with the same fields at the same offsets the file has - the
//  .pck is that object graph frozen. So the game is a packetizer, and all a
//  module has to do is get the result out of RAM.
//
//  The obstacle was space: with the frontend resident there was not enough
//  main heap left for the meshes. Hence this module, in that order.
//
//  WHAT IT DOES, AND WHY IN THIS ORDER
//
//    1. waits WAIT_FRAMES so the frontend has finished loading - there is no
//       point unloading something still coming up.
//    2. records how much the main heap has free BEFORE.
//    3. unloads layer 8, the frontend, through the game's own
//       mcLayerManager::UnloadDownToSpecifiedLayer(floor, keep). Called with
//       floor = 8 the loop body runs for layer 8 and nothing else: it starts at
//       8 and stops when the index drops below `floor`.
//    4. records free AFTER, so the report itself says what the unload returned.
//       If it says the same number twice, the frontend was not the thing
//       holding the memory and this module has told you so.
//    5. carves a PRIVATE heap off the top with mcHeap::CreateMemTopHeap and
//       makes it current with mcHeap::PushHeap - two words, it just swaps the
//       allocator global.
//    6. calls rmcModel::Create for every name in the table.
//    7. pops the heap and reports its base and length.
//
//  WHY A PRIVATE HEAP AND NOT JUST THE MAIN ONE
//
//  The memtop heap is contiguous and nobody else allocates from it, so every
//  pointer in the object graph lands inside a range this module can name in one
//  line. That is the shape of a .pck body: base, length, and pointers that all
//  fall within it. Dumping the main heap instead would mean carrying the whole
//  game's allocations along with the meshes.
//
//  datMemoryStartUseTemporary does NOT switch allocators - it pushes two
//  bookkeeping globals and nothing more - so the parse buffer comes from the
//  pushed heap too, and is freed there before Create returns. What is left in
//  the range is the model graph plus the hole the parse buffer left behind.
//
//  IT PARKS AT THE END, ON PURPOSE
//
//  Unloading the frontend while the game is still in the frontend state leaves
//  the next frame drawing a dead layer. Rather than let that turn into a crash
//  that may scribble on the report, the module spins once the work is done, so
//  the machine sits still with everything written and a savestate can be taken
//  cleanly. Set PARK_AT_END to 0 to let it return instead.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

#define MC3_LOG_OWNER 'DUMP'
#include "../../payload/mc3_log.h"

enum {
    // The layer system. GetLayer(e) is *(manager + 4 + 4*e); the frontend is 8.
    LAYER_MANAGER_GET   = 0x001A97B0,   // mcLayerManager::GetInstance()
    LAYER_UNLOAD_DOWNTO = 0x001A98E8,   // (this, floor, keep)
    LAYER_FRONTEND      = 8,

    // The heap. The active allocator is a single global the push/pop swap.
    HEAP_CREATE_MEMTOP  = 0x004BD650,   // (size, name) -> memMemoryAllocator*
    HEAP_RELEASE_MEMTOP = 0x004BD770,
    HEAP_PUSH           = 0x004BD5D8,   // (allocator)
    HEAP_POP            = 0x004BD5F0,
    HEAP_ACTIVE         = 0x0070E500,   // dword_70E500, the current allocator

    // memMemoryAllocator layout, as GetMemUsed walks it.
    ALLOC_BASE          = 0x04,
    ALLOC_TOP           = 0x08,
    ALLOC_SIZE          = 0x0C,

    RMC_MODEL_CREATE    = 0x002A8CA0,   // (name, flags) -> rmcModelGeom*

    // How long to let the frontend settle, and how much to ask for.
    WAIT_FRAMES         = 240,
    HEAP_BYTES          = 0x400000,     // 4 MB; CreateMemTopHeap says if it is
                                        // too much, and the report carries the
                                        // free figure so you can size it.
    MAX_MODELS          = 16,
    PARK_AT_END         = 1,
};

#define DUMP_MAGIC  0x4D433348u         /* 'MC3H' */
#define DUMP_ADDR   0x0061CF00u         /* MODREPORT in ../../mc3_inject.py */

/* THE REPORT WINDOW IS SHARED AND THIS MODULE REFUSES TO FIGHT OVER IT.
 * city_slot6 ('MC3Y') and mc2_probe ('MC3B') claim the same first word, and
 * menu_row lives 200 bytes in. If one of those got there first, writing here
 * would corrupt a live report and the reader would decode the wrong struct.
 * So the claim is conditional, and everything that matters also goes to the
 * log ring, which by design cannot collide - each module's head lives in its
 * own relocated .data and the reader finds them by scanning.
 * Run this with city_slot6.mod and mc2_probe.mod turned off in mc3boot.ini to
 * get the report as well as the log. */
#define FOREIGN_MAGIC(x) ((x) != 0u && (x) != DUMP_MAGIC)

enum {
    ST_WAITING = 0, ST_UNLOADED = 1, ST_NO_HEAP = 2, ST_BUILDING = 3, ST_DONE = 4
};

struct dump_model {
    mc3_u32 name;           /* the string, so the report is self-describing */
    mc3_u32 obj;            /* the rmcModelGeom, or 0 */
    mc3_u32 groups;         /* +0x08, u16 - one per material */
    mc3_u32 matidx;         /* +0x0C - the u16 table */
    mc3_u32 geom;           /* +0x10 - the rmcGeometry array */
};

// Keep in sync with the reader in ../../mc3_inject.py.
struct dump_report {
    mc3_u32 magic;
    mc3_u32 ticks;
    mc3_u32 state;
    mc3_u32 free_before;    /* main heap, top - size */
    mc3_u32 free_after;     /* the same, once the frontend is gone */
    mc3_u32 heap;           /* the private allocator */
    mc3_u32 heap_base;      /* what to dump */
    mc3_u32 heap_len;
    mc3_u32 count;
    mc3_u32 running;        /* the attempt in flight, so a hang names itself */
    dump_model m[MAX_MODELS];
};

// One struct behind one noinline accessor: several loose string symbols make
// GCC share a single `lui` across them and mc3_mkmod refuses the build with
// "LO16 sem HI16". Same reasoning as city_slot6's city().
// EVERY string this module uses lives here, behind ONE noinline accessor.
// Loose literals scattered through the code make GCC emit a single `lui` shared
// by several `%lo` accesses, and mc3_mkmod refuses that build with "LO16 sem
// HI16" - it cannot relocate a base it cannot see. One struct, one base, one
// relocation.
struct dump_list {
    const char *heapname;
    const char *msg[8];
    const char *name[MAX_MODELS];
};

// Edit this and rebuild. Names go through the asset manager exactly as
// rmcModel::Create takes them, so they need the directory prefix - "checker"
// alone resolves to nothing while "model/checker.mod" loads.
static const dump_list g_list = {
    "meshdump",
    {
        "armed (0 = window ours)",      /* 0 */
        "free before",                  /* 1 */
        "about to unload frontend",     /* 2 */
        "free after / gained",          /* 3 */
        "about to CreateMemTopHeap",    /* 4 */
        "CreateMemTopHeap failed",      /* 5 */
        "about to Create",              /* 6 */
        "done: base/len",               /* 7 */
    },
    {
        "model/checker.mod",
        0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0,
    }
};

// Used instead of the shared window when another module already owns it: the
// module keeps working and reports through the log ring alone. Behind an
// accessor for the same reason as everything else here - a second data base in
// the open makes GCC share a `lui` and the packer rejects the build.
static dump_report g_fallback;

static __attribute__((noinline)) dump_report *fallback()
{
    return &g_fallback;
}

static __attribute__((noinline)) const dump_list *list()
{
    return &g_list;
}

static mc3_u32 heap_free(mc3_u32 alloc)
{
    if (!alloc)
        return 0u;
    const mc3_u32 top  = *(volatile mc3_u32 *)(alloc + ALLOC_TOP);
    const mc3_u32 used = *(volatile mc3_u32 *)(alloc + ALLOC_SIZE);
    return top > used ? top - used : 0u;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    dump_report *r = (dump_report *)DUMP_ADDR;
    const dump_list *const L0 = list();

    if (FOREIGN_MAGIC(r->magic)) {
        /* Someone else owns the window. Fall back to private storage and say so
         * once - the fallback's own magic doubles as the "already said" flag,
         * because a loose `static int` here would be one more data base for the
         * packer to trip over. */
        const mc3_u32 foreign = r->magic;
        r = fallback();
        if (r->magic != DUMP_MAGIC) {
            r->magic = DUMP_MAGIC;
            MC3_LOG1(L0->msg[0], foreign);
        }
    } else if (r->magic != DUMP_MAGIC) {
        for (mc3_u32 *p = (mc3_u32 *)r;
             p < (mc3_u32 *)(DUMP_ADDR + sizeof(dump_report)); ++p)
            *p = 0u;
        r->magic = DUMP_MAGIC;
        MC3_LOG1(L0->msg[0], 0u);
    }

    if (r->state >= ST_DONE) {
        if (PARK_AT_END)
            for (;;) { }
        return;
    }

    r->ticks += 1u;
    if (r->ticks < WAIT_FRAMES)
        return;

    // A frame count is not a signal that the game is up, and the calls below
    // do not defend themselves: mcHeap::CreateMemTopHeap opens with
    // `*(dword_70E500 + 8)` and has NO null check, so reaching it while the
    // active allocator is still null reads address 8, derives a base from the
    // garbage and memsets from it. Measured in pck_tool as 50 TLB misses at
    // 0x0042E760 storing from 0x02000000 up - the top of EE RAM - and PCSX2
    // then died. Wait for a real allocator instead of a number of frames.
    const mc3_u32 active = *(volatile mc3_u32 *)HEAP_ACTIVE;
    if (active < 0x00100000u || active >= 0x02000000u)
        return;
    r->free_before = heap_free(active);
    MC3_LOG1(L0->msg[1], r->free_before);

    // Step 3. floor = 8 makes the loop touch layer 8 and stop; `keep` is any
    // layer that is not 8, and 0 (City) is the natural choice because City is
    // not loaded in the frontend anyway.
    const mc3_u32 mgr = MC3_CALL(mc3_u32, LAYER_MANAGER_GET)();
    MC3_LOG1(L0->msg[2], mgr);
    if (mgr)
        MC3_CALL3(void, LAYER_UNLOAD_DOWNTO, mc3_u32, mc3_u32, mc3_u32)
            (mgr, LAYER_FRONTEND, 0u);
    r->state = ST_UNLOADED;

    r->free_after = heap_free(*(volatile mc3_u32 *)HEAP_ACTIVE);
    MC3_LOG2(L0->msg[3], r->free_after, r->free_after - r->free_before);

    // Step 5. A private, contiguous heap. If this returns 0 the game has
    // already printed how many bytes it wanted against how many it had, and
    // free_after in the report says the same thing.
    MC3_LOG1(L0->msg[4], HEAP_BYTES);
    const mc3_u32 h = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP, mc3_u32, const char *)
                         (HEAP_BYTES, L0->heapname);
    r->heap = h;
    if (!h) {
        r->state = ST_NO_HEAP;
        MC3_LOG(L0->msg[5]);
        return;
    }
    r->heap_base = *(volatile mc3_u32 *)(h + ALLOC_BASE);
    r->heap_len  = *(volatile mc3_u32 *)(h + ALLOC_SIZE);
    r->state = ST_BUILDING;

    MC3_CALL1(void, HEAP_PUSH, mc3_u32)(h);

    // Step 6. `running` is set BEFORE the call and cleared after the loop: a
    // hang inside Create leaves the attempt that caused it named in the report.
    const dump_list *const L = L0;
    for (int i = 0; i < MAX_MODELS; ++i) {
        const char *const n = L->name[i];
        if (!n)
            break;
        dump_model *const d = &r->m[i];
        d->name = (mc3_u32)n;
        r->running = (mc3_u32)(i + 1);
        MC3_LOG1(L0->msg[6], (mc3_u32)n);

        const mc3_u32 o = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)(n, 0);
        d->obj = o;
        if (o) {
            d->groups = *(volatile mc3_u16 *)(o + 0x08);
            d->matidx = *(volatile mc3_u32 *)(o + 0x0C);
            d->geom   = *(volatile mc3_u32 *)(o + 0x10);
        }
        r->count = (mc3_u32)(i + 1);
        MC3_LOG2(L0->msg[6], o, d->groups);
    }
    r->running = 0u;

    MC3_CALL(void, HEAP_POP)();

    // The heap is left ALIVE on purpose - releasing it would hand the range
    // back and the next allocation would walk over the very thing being
    // dumped. Whoever reads the savestate takes heap_base..heap_base+heap_len.
    r->heap_len = *(volatile mc3_u32 *)(h + ALLOC_SIZE);
    r->state = ST_DONE;
    MC3_LOG2(L0->msg[7], r->heap_base, r->heap_len);
}

// No MC3_HOOK: this is a dispatcher module, called once per frame like
// mc2_probe. It wants a frame at a moment of its choosing, not a particular
// function.
