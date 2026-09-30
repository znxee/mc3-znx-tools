// -----------------------------------------------------------------------------
//  city_place - draws a Midnight Club 2 city into Midnight Club 3.
//
//  WHY THIS HAS TO BE CODE
//
//  Of the three legs of porting a city from raw files, two need no module at
//  all. Geometry: MC3's text `.mod` reader is live and accepts MC2's dialect
//  unchanged. Collision: phBound::LoadFromFile falls back to a `.bnd` ASCII
//  reader that handles `type: otgrid`, which is what MC2 ships.
//
//  Placement is the exception. The `.lvl`/`.hood` grammar is present in the
//  string pool and NOTHING REFERENCES IT - the readers were compiled out. So
//  the one structural hole is putting each mesh at its matrix, and that is what
//  this does.
//
//  THE RECIPE, read out of the game rather than invented
//
//  From 0x0029CAC8, instruction by instruction:
//
//      gfxState::SetWorld(&matrix)                  0x0052E0D0
//      gfxModel::Draw(model, *(model + 4), 0)       0x001EB998
//
//  SetWorld reads three floats per row and expands each to four, so its
//  argument is a Matrix34: twelve floats, row-major, translation last. The
//  exporter's columns are already in that order, so mc3_place.py copies them
//  through and the pointer goes straight to the game.
//
//  Models come from gfxGetModel(name, 0, 274) 0x001ED340 - the flags are 274
//  because that is what hudArrow::Init passes at 0x0029C21C. It prefixes
//  "model" and appends the extension itself, so the name is bare.
//
//  WHAT THIS VERSION DELIBERATELY IS NOT
//
//  It is capped, and it does not cull. The City fork is right that 8809 draws a
//  frame will not run without culling by AABB - but culling needs the camera,
//  and a first version that needs the camera AND the placement AND the model
//  cache all correct at once tells you nothing when it shows a black screen.
//
//  So this draws MAX_DRAWS entries with no cull. If MC2 buildings appear in the
//  right places, placement is solved and culling is a performance problem. If
//  they appear in the wrong places, the matrix is wrong and no amount of
//  culling would have helped. One question per boot.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#define MC3_LOG_OWNER 0x504C4143u      /* 'PLAC' */
#include "../../payload/mc3_log.h"

enum {
    // the game's own file API, the same one core.mod reads modules with
    STREAM_OPEN   = 0x003991F0,   // (path, methods) - 1 selects raw
    STREAM_READ   = 0x003993A8,
    STREAM_CLOSE  = 0x00399748,
    STREAM_SIZE   = 0x003997A8,
    STREAM_RAW    = 1,

    // rmcModel::Create, NOT gfxGetModel. gfxGetModel (0x001ED340) does not
    // return with an MC2 .mod - measured in the frontend, where mc2_probe had
    // already loaded four of the same files through this function without
    // trouble. Three explanations were tried and buried before that isolated
    // it to the function itself.
    RMC_CREATE    = 0x002A8CA0,   // (name, 0) -> rmcModel, vtable 0x00626D98
    SET_WORLD     = 0x0052E0D0,   // (const Matrix34 *)

    // What an rmcModel is, from the object mc2_probe photographed:
    //     +0  vtable   +8  mesh count   +16  array of 8-byte entries
    // The vtable slot at +12 draws one mesh by index: it computes
    // &array[i] and tail-calls 0x002AC970, which reaches 0x00526900 in the
    // renderer. Called through the vtable rather than direct, because that is
    // how the game reaches it and the indirection costs nothing.
    RMC_COUNT     = 8,
    RMC_VT_DRAW   = 12,

    MC_GAME_DRAW  = 0x001A2998,

    // The last call of the function that loads a city - by here the city
    // image and its collision are in. This is where the meshes belong: a
    // loading screen is the one place in the game where a stall is expected,
    // and the frontend is NOT, because it already has a city of its own
    // loaded as a backdrop and we were competing with it.
    // 0x001A9020 is where the game loads the city image itself - the one call
    // that pulls in $/resources/city/<name>_<time>_<weather>. Ours goes here,
    // at the same point in the sequence, rather than at the end of loading:
    // by the end the city has taken its memory and rmcModel::Create starts
    // returning null, which is exactly what the first attempt measured.
    CITY_IMAGE_ORIG = 0x0042E810,

    // *(*(0x00619B10)) is the session city index - the one the asset path
    // builders read. Nothing here may run before it says our city is up: the
    // first version loaded meshes from inside a draw call in the FRONTEND,
    // where no city exists, and hung the boot.
    SESSION       = 0x00619B10,
    LOSANGELES_INDEX = 5,

    PLACE_MAGIC   = 0x4D43334C,   // 'MC3L', written by mc3_place.py
};

// How much of the city to attempt. Both are the safety rail, not a design
// limit: raise them once a boot has shown something in the right place.
#define MAX_MODELS   64
#define MAX_DRAWS   512

// The gap between city_slot6 (bytes 0..176 of the window) and menu_row
// (200..240). Counted, not estimated: the first attempt put this at +232 on the
// belief that menu_row was 32 bytes, and it had grown to 40 - so this trace
// landed on top of its last two fields and each mod corrupted the other's
// report. See the sub-map in ../../mc3_inject.py before picking an offset.
#define PLACE_TRACE  0x0061CFB0u      /* MODREPORT + 176, 24 bytes */
#define TRACE_MAGIC  0x4D433347u      /* 'MC3G' */

// Written BEFORE each risky step and cleared on success, so a hang leaves the
// stage behind instead of a zero. A savestate then names the call that did not
// return - which is the difference between "it broke" and "Stream::Open broke".
enum { E_NONE = 0, E_OPEN, E_SIZE, E_ALLOC, E_READ, E_MAGIC, E_SLOTS };

struct place_trace {
    mc3_u32 magic;
    mc3_u32 blob;           /* where the table landed, 0 until it is read */
    mc3_u32 entries;        /* what the file says it holds */
    mc3_u32 models;         /* how many gfxGetModel calls returned something */
    mc3_u32 drawn;          /* draws issued on the last frame */
    mc3_u32 last_err;
};
static place_trace *const g_trace = (place_trace *)PLACE_TRACE;

/* Mirrors the header mc3_place.py writes. */
struct place_header {
    mc3_u32 magic;
    mc3_u32 version;
    mc3_u32 n_names;
    mc3_u32 n_entries;
    mc3_u32 names_off;
    mc3_u32 names_size;
    mc3_u32 entries_off;
    mc3_u32 reserved;
    float   emin[3];
    float   emax[3];
};

struct place_entry {
    mc3_u32 name_off;       /* into the name table, not an index */
    float   m[12];          /* Matrix34, ready for SetWorld */
    float   centre[3];
    float   radius;
};

/* name offset -> model pointer, filled once. Lives on the game heap rather
 * than in the module: a static array here would be read and written through a
 * shared `lui`, which the module format cannot relocate and mc3_mkmod now
 * refuses outright. */
struct model_slot {
    mc3_u32 name_off;
    mc3_u32 model;
};

static __attribute__((noinline)) const char *place_path()
{
    return "host0:/losangeles.place";
}

static inline int valid(mc3_u32 p)
{
    return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u;
}

// -----------------------------------------------------------------------------
//  One-shot: read the table, then ask the game for the first MAX_MODELS meshes.
//  Returns the blob, or 0 with last_err set.
// -----------------------------------------------------------------------------
static mc3_u32 load_once()
{
    g_trace->last_err = E_OPEN;
    const mc3_u32 stream = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
                              (place_path(), STREAM_RAW);
    if (!stream) { g_trace->last_err = E_OPEN; return 0u; }

    g_trace->last_err = E_SIZE;
    const int size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(stream);
    if (size <= (int)sizeof(place_header)) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        g_trace->last_err = E_SIZE;
        return 0u;
    }

    // The table stays for the whole session - it is what every frame reads.
    g_trace->last_err = E_ALLOC;
    void *blob = mc3_alloc((mc3_u32)size);
    if (!blob) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        g_trace->last_err = E_ALLOC;
        return 0u;
    }

    g_trace->last_err = E_READ;
    const int got = MC3_CALL3(int, STREAM_READ, mc3_u32, void *, int)
                       (stream, blob, size);
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    if (got != size) { mc3_free(blob); g_trace->last_err = E_READ; return 0u; }

    const place_header *h = (const place_header *)blob;
    if (h->magic != (mc3_u32)PLACE_MAGIC) {
        mc3_free(blob);
        g_trace->last_err = E_MAGIC;
        return 0u;
    }
    g_trace->entries = h->n_entries;
    MC3_LOGD("place table read, entries", h->n_entries);

    // The model table goes on the heap too, and its address is the only thing
    // this function leaves behind besides the blob. It starts EMPTY - the
    // meshes are asked for one a frame, from resolve_next below.
    g_trace->last_err = E_SLOTS;
    model_slot *slots = (model_slot *)mc3_alloc(
        (mc3_u32)(MAX_MODELS * (int)sizeof(model_slot)));
    if (!slots) { mc3_free(blob); g_trace->last_err = E_ALLOC; return 0u; }

    const char *const names = (const char *)blob + h->names_off;
    const place_entry *e = (const place_entry *)((const char *)blob + h->entries_off);

    for (mc3_u32 i = 0u; i < (mc3_u32)MAX_MODELS; ++i) {
        slots[i].name_off = 0xFFFFFFFFu;     // empty, not "name at offset 0"
        slots[i].model = 0u;
    }

    ((place_header *)blob)->reserved = (mc3_u32)slots;
    g_trace->last_err = E_NONE;              // everything came back
    return (mc3_u32)blob;
}

// -----------------------------------------------------------------------------
//  One mesh per frame.
//
//  Returns 0 while there is still work to do, so the caller can skip drawing an
//  incomplete set - and 1 once every name in range has been asked for.
//
//  Asking for all sixty-four inside one draw call is what hung the first build.
//  Amortised, `models resolved` climbing by one a frame is a progress bar, and
//  the number it stops at names the mesh that killed it.
// -----------------------------------------------------------------------------
static int resolve_next(mc3_u32 blob)
{
    const place_header *h = (const place_header *)blob;
    model_slot *slots = (model_slot *)h->reserved;
    const char *const names = (const char *)blob + h->names_off;
    const place_entry *e = (const place_entry *)((const char *)blob + h->entries_off);

    // ALL of them, not the first MAX_DRAWS. The entries are sorted by name, so
    // 512 of them covered only nine distinct meshes and the loop declared
    // itself finished at nine - a cap meant for drawing quietly became a cap on
    // loading.
    const mc3_u32 limit = h->n_entries;
    const mc3_u32 n = g_trace->models;
    if (n >= (mc3_u32)MAX_MODELS)
        return 1;

    for (mc3_u32 i = 0u; i < limit; ++i) {
        mc3_u32 k = 0u;
        for (; k < n; ++k)
            if (slots[k].name_off == e[i].name_off)
                break;
        if (k < n)
            continue;                        // already asked for this one

        // Recorded BEFORE the call: if the game does not come back from this
        // mesh, the slot still names which one it was - and so does the log.
        slots[n].name_off = e[i].name_off;
        MC3_LOG2("loading mesh (slot, name offset)", n, e[i].name_off);
        slots[n].model = MC3_CALL2(mc3_u32, RMC_CREATE, const char *, int)
                            (names + e[i].name_off, 0);
        g_trace->models = n + 1u;
        MC3_LOG2("  -> rmcModel::Create gave", n, slots[n].model);
        return 0;                            // one a frame, and only one
    }
    return 1;                                // nothing left to ask for
}

// -----------------------------------------------------------------------------
//  Once per frame, after the game has drawn its own world.
// -----------------------------------------------------------------------------
extern "C" void mc3_city_place_draw(mc3_u32 self)
{
    MC3_CALL1(void, MC_GAME_DRAW, mc3_u32)(self);

    if (g_trace->magic != (mc3_u32)TRACE_MAGIC) {
        for (mc3_u32 i = 0u; i < sizeof(place_trace) / 4u; ++i)
            ((mc3_u32 *)g_trace)[i] = 0u;
        g_trace->magic = (mc3_u32)TRACE_MAGIC;
    }
    // THE TABLE IS READ HERE, from whatever state the game is in - and it has
    // to be here rather than in the update hook, because mcGame::Update is the
    // IN-CITY update and does not run in the frontend. Moving the read there
    // put a 610 KB allocation in competition with the city's own loading, and
    // it came back as E_ALLOC with the screen black.
    //
    // This hook runs everywhere, so the read lands in the frontend where memory
    // is free - which is exactly where it succeeded before.
    if (!g_trace->blob) {
        if (g_trace->last_err && g_trace->last_err != E_ALLOC)
            return;                          // failed for a real reason
        g_trace->blob = load_once();
        return;                              // never read and draw on one frame
    }

    // LOAD OUTSIDE THE CITY, DRAW INSIDE IT - and this time the split is
    // measured rather than guessed.
    //
    // rmcModel::Create returns a real object for these meshes in the frontend
    // (fifty of them, with pointers) and NULL for every one of them during the
    // city load - all 64, even hooked at the earliest point, before the city
    // has taken any memory. So it is not memory pressure and not the moment:
    // the asset manager simply does not resolve loose files while it is
    // loading a city.
    //
    // So the whole set is asked for here, in one pass, the first time this
    // runs outside our city. One stall in a menu, once, instead of a trickle
    // that competed with the frontend's own backdrop city for 3585 frames.
    const mc3_u32 sess = *(volatile mc3_u32 *)SESSION;
    const int in_city = valid(sess)
        && *(volatile mc3_u32 *)sess == (mc3_u32)LOSANGELES_INDEX;

    if (!in_city) {
        if (!g_trace->models)
            while (!resolve_next(g_trace->blob))
                ;
        return;
    }

    const place_header *h = (const place_header *)g_trace->blob;
    const model_slot *slots = (const model_slot *)h->reserved;
    const place_entry *e = (const place_entry *)
        ((const char *)h + h->entries_off);
    if (!valid((mc3_u32)slots))
        return;

    mc3_u32 desenhados = 0u;
    const mc3_u32 limit = h->n_entries < (mc3_u32)MAX_DRAWS
                             ? h->n_entries : (mc3_u32)MAX_DRAWS;
    for (mc3_u32 i = 0u; i < limit; ++i) {
        mc3_u32 model = 0u;
        for (mc3_u32 k = 0u; k < g_trace->models; ++k) {
            if (slots[k].name_off == e[i].name_off) {
                model = slots[k].model;
                break;
            }
        }
        if (!valid(model))
            continue;

        // One draw per mesh in the model, through the object's own vtable.
        const mc3_u32 n = *(volatile mc3_u32 *)(model + RMC_COUNT);
        if (!n || n > 64u)                   // empty or nonsense: skip it
            continue;
        const mc3_u32 vt = *(volatile mc3_u32 *)model;
        if (!valid(vt))
            continue;
        const mc3_u32 desenha = *(volatile mc3_u32 *)(vt + RMC_VT_DRAW);
        if (!valid(desenha))
            continue;

        MC3_CALL1(void, SET_WORLD, const float *)(e[i].m);
        for (mc3_u32 k = 0u; k < n; ++k)
            ((void (*)(mc3_u32, mc3_u32))desenha)(model, k);
        ++desenhados;
    }
    if (g_trace->drawn != desenhados)        // only when it changes
        MC3_LOGD("draws issued", desenhados);
    g_trace->drawn = desenhados;
}

// All three call sites of mcGame::Draw: which one runs depends on the game
// state, and hooking one of three would work only some of the time - the kind
// of bug that reads as "it sometimes does not draw".
MC3_HOOK(0x001A24D0, mc3_city_place_draw);
MC3_HOOK(0x001A2618, mc3_city_place_draw);
MC3_HOOK(0x001A2740, mc3_city_place_draw);

// -----------------------------------------------------------------------------
//  The loading half, and why it hangs off the city load.
//
//  This lived in the frontend for a while, one mesh a frame, because an earlier
//  loader (gfxGetModel) would not return from inside the city. That turned out
//  to be the function and not the place - rmcModel::Create resolved fifty of
//  the same meshes without complaint - and the workaround outlived its reason.
//
//  It was also the wrong place on its own terms: the FRONTEND ALREADY HAS A
//  CITY LOADED as its backdrop, the last one the player saved in. Trickling
//  mesh loads into it meant competing with that for memory and time, which is
//  exactly what it looked like: nine meshes in 3585 frames, and the menu
//  stuttering.
//
//  0x001A9430 is the last call of the city loading function, after the city
//  image and its collision are in. Everything is resolved here IN ONE GO,
//  because a loading screen is the one moment in the game where a stall costs
//  nothing.
// -----------------------------------------------------------------------------
extern "C" mc3_u32 mc3_city_loaded(const char *name, int kind,
                                   void *a, void *b)
{
    // Nothing of ours happens here any more - loading during the city load was
    // measured returning NULL 64 times out of 64. The hook stays only because
    // its return value is the city image base and the chain has to be intact
    // if anyone puts work back here.
    return MC3_CALL4(mc3_u32, CITY_IMAGE_ORIG,
                     const char *, int, void *, void *)(name, kind, a, b);
}

MC3_HOOK(0x001A9020, mc3_city_loaded);
