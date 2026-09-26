// -----------------------------------------------------------------------------
//  mc2_probe - does MC3's own text-mesh reader accept a Midnight Club 2 mesh?
//
//  THE QUESTION
//
//  MC2 ships its city geometry as TEXT (`.xmod`), and MC3 turns out to ship 39
//  text meshes of its own (`ASSETS/model/*.mod`). Both say `version: 1.10`, and
//  the executable names that version explicitly:
//
//      rmcModel::Create(const char *name, int flags)          0x002A8CA0
//        strrchr(name, '.'), strcasecmp(ext, ".mod")
//          equal    -> 0x005555A0  text: reads 14 bytes and sniffs the version
//                        "version: 2.1"   tokenizer vtable 0x00632348
//                        "version: 2.00"        "
//                        "version: 1.10"  tokenizer vtable 0x006323F8  <-- MC2
//                        anything else    loads anyway, version 0
//                      then mshMesh::LoadMod(tokenizer, bool, version) 0x00555758
//          differs  -> 0x005BD038  binary, with "mesh" as the default extension
//
//  So there is a dedicated reader for the MC2 generation of the format, still
//  live in the shipped game. Comparing the files on a PC says the two dialects
//  match. It cannot say the reader accepts one, and that is what this asks.
//
//  WHAT IT DOES
//
//  Six calls to rmcModel::Create, results left at a fixed address a savestate
//  can be read for. Two of the six load MC3's OWN `checker.mod`, and they are
//  first on purpose: if those come back empty too, the call or the path is
//  wrong and the MC2 files were never really tested. A control that costs two
//  lines is worth more than a confident reading of a null.
//
//  The two spellings - with and without `model/` - are there because the reader
//  hands the name to the asset manager with "mod" as a DEFAULT extension, and
//  which spelling that manager wants is not something the disassembly settles.
//  Asking both ways costs one boot instead of two.
//
//  WHAT IT DELIBERATELY DOES NOT DO
//
//  No textures. Every one of these meshes names textures MC3 does not have, and
//  the texture path is known to be forgiving - an unresolved name yields the
//  null texture, not a failure. Supplying them would blur exactly the line this
//  test exists to draw: geometry accepted, or not.
//
//  Nothing is freed. Six leaked models on a test boot is not a cost; freeing a
//  half-constructed object would be a risk.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    // rmcModel *Create(const char *name, int flags). Both retail call sites
    // pass flags = 0, and the flags byte only lands at +6 of the result.
    RMC_MODEL_CREATE = 0x002A8CA0,
};

// How many dispatcher ticks to wait before firing. The dispatcher runs off
// mcGame::Update and friends, so by the time it is called at all the asset
// manager is long since up - this is slack, not a requirement.
//
// A tick is exactly one frame. Four hooks are installed, but only the one for
// the current game state runs, so they do not stack: measured at the frontend,
// ticks and the game's own frame counter were both 266. At 120 Hz this fires
// about two seconds in, which is before anyone can reach for the save key.
#define PROBE_WAIT   240u

#define PROBE_MAGIC  0x4D433342u        /* 'MC3B' */
#define PROBE_ADDR   0x0061CF00u        /* MODREPORT in ../../mc3_inject.py */
#define ATTEMPTS     6

struct mc2_attempt {
    mc3_u32 name;           /* the string that was passed, so `state` can print it */
    mc3_u32 ret;            /* what Create returned; 0 means it produced nothing */
    mc3_u32 body[6];        /* the returned object, all 24 bytes of it */
};

// Keep in sync with the reader in ../../mc3_inject.py.
struct mc2_report {
    mc3_u32 magic;
    mc3_u32 ticks;          /* dispatcher calls seen */
    mc3_u32 running;        /* 1-based attempt in flight, 0 when idle */
    mc3_u32 done;
    mc3_u32 count;          /* attempts finished */
    mc3_u32 pad[3];
    mc2_attempt a[ATTEMPTS];
};

static const char *const g_names[ATTEMPTS] = {
    "model/checker.mod",                    /* MC3's own, the control */
    "checker.mod",
    "model/l_prop_cone_01x_0.mod",          /* MC2: 20 verts, 1 material */
    "l_prop_cone_01x_0.mod",
    "model/sky_0.mod",                      /* MC2: 101 verts, 5 materials */
    "model/l_prop_firehydrant_01x_0.mod",   /* MC2: 147 verts, the largest here */
};

// The table goes behind an accessor for the same reason core.cpp's guard does:
// GCC will otherwise build one `lui` and read all six entries through it, and
// the module format relocates only the first low half. Since that now fails the
// build rather than shipping a wrong address, this is what keeps it buildable.
static __attribute__((noinline)) const char *const *names()
{
    return g_names;
}

static inline int is_object(mc3_u32 p)
{
    return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    mc2_report *const r = (mc2_report *)PROBE_ADDR;

    // The block is zeroed by the image every boot, so this is belt and braces -
    // but a stale report read as a fresh one is the kind of wrong answer that
    // wastes a whole afternoon.
    if (r->magic != PROBE_MAGIC) {
        for (mc3_u32 i = 0; i < sizeof(mc2_report) / 4u; ++i)
            ((mc3_u32 *)r)[i] = 0u;
        r->magic = PROBE_MAGIC;
    }

    r->ticks += 1u;
    if (r->done || r->ticks < PROBE_WAIT)
        return;

    // Set before the loop, cleared after it: if the game hangs inside Create,
    // `running` names the attempt that did it and the savestate still says so.
    const char *const *n = names();
    for (int i = 0; i < ATTEMPTS; ++i) {
        mc2_attempt *const a = &r->a[i];
        a->name = (mc3_u32)n[i];
        r->running = (mc3_u32)(i + 1);

        const mc3_u32 m = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)
                             (n[i], 0);
        a->ret = m;
        if (is_object(m)) {
            for (int w = 0; w < 6; ++w)
                a->body[w] = ((const volatile mc3_u32 *)m)[w];
        }
        r->count = (mc3_u32)(i + 1);
    }

    r->running = 0u;
    r->done = 1u;
}

// No MC3_HOOK: this is a dispatcher module, called once per tick like the
// pedestrian payload. It wants a frame, not a particular function.
