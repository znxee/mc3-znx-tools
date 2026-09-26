// -----------------------------------------------------------------------------
//  city_slot6 - fills the city slot the retail game allocates and never uses.
//
//  WHAT IS THERE TO FILL
//
//  The city registry is built once, at 0x004B7600:
//
//      new(472)              472 = 16 (the new[] cookie) + 6 * 76, exactly
//      6 constructors        s1 counts 5 down to -1
//      5 registrations       sd/"s"(6 hoods)  atlanta/"a"(7)  detroit/"d"(6)
//                            tokyo/"t"(1)     nocity/"nocity"(1)
//
//  Six records, five filled. Those five `jal 0x4B7AD0` are the only calls to it
//  in the whole executable, so record 5 keeps whatever the constructor
//  (0x4B7A40) left: name = "not_initialized", hood count = -1.
//
//  Registering is five stores and nothing else - no allocation, no list to
//  splice, no neighbour touched:
//
//      0x4B7AD0(record, name, code, hood_count, hood_names)
//          +0  name        +4  code       +8  name again
//          +12 hood count  +16 char *const * of hood names
//
//  WHY IT IS SAFE TO DO THIS AT INIT
//
//  The per-city loop at 0x4B7750 runs `s0 = 5; bgez` - indices 0 THROUGH 5. So
//  the game already walks the empty slot on every boot and calls 0x4B77B0 with
//  the name "not_initialized". That function asks the asset manager for
//  "tune/race" + name and, when nothing answers, takes `beq s2, zero` straight
//  to its own exit. A city with no content is a case the shipped game handles
//  every time it starts, because it has one.
//
//  So this hooks the ONE call to 0x4B7600 (at 0x001A0F40), lets it build the
//  table, and fills record 5 before anything reads it.
//
//  THE GUARD
//
//  Before writing, the name pointer in record 5 must be exactly the
//  constructor's "not_initialized" (0x0066A492). If it is anything else, the
//  stride or the base is wrong and the write would land on a real city - so it
//  refuses and says so. Reading a wrong address is a bad report; writing one is
//  a corrupted game.
//
//  WHAT THIS DOES NOT DO
//
//  It does not make a city appear in a menu. It makes the engine able to
//  resolve one by name. The frontend entry, the career, the map and the loading
//  screen are a different layer - see FORKS.md for how that was split up.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    CITY_TABLE_INIT = 0x004B7600,   // builds the array; called once, from 0x1A0F40
    CITY_REGISTER   = 0x004B7AD0,   // (record, name, code, nhoods, hood_names)
    CITY_ARRAY      = 0x00619D4C,   // holds &record[0] - past the new[] cookie
    // mc::LoadDefaultCityData: walks records 0..5 and loads tune/race/<name>
    // for each. Also called exactly once, from 0x1A12E0.
    LOAD_DEFAULT_CITY_DATA = 0x004B7750,
    CITY_STRIDE     = 76,
    CITY_SLOTS      = 6,
    CITY_FREE       = 5,            // the one retail never registers

    // What 0x4B7A40 leaves in a record nobody claimed. The whole safety of this
    // mod is that this is checked before anything is written.
    NOT_INITIALIZED = 0x0066A492,
};

#define CITY_MAGIC  0x4D433359u         /* 'MC3Y' */
#define CITY_ADDR   0x0061CF00u         /* MODREPORT in ../../mc3_inject.py */

enum { ST_NONE = 0, ST_REGISTERED = 1, ST_REFUSED = 2, ST_NO_ARRAY = 3 };

struct city_slot {
    mc3_u32 name;
    mc3_u32 code;
    mc3_u32 nhoods;
    mc3_u32 hoods;
    mc3_u32 races;          /* +0x14 - the count the frontend gates on */
    mc3_u32 race_array;     /* +0x20 - 48 bytes per race */
};

// Keep in sync with the reader in ../../mc3_inject.py.
struct city_report {
    mc3_u32 magic;
    mc3_u32 ran;            /* the registration hook */
    mc3_u32 base;           /* *(CITY_ARRAY) */
    mc3_u32 slot;           /* &record[CITY_FREE] */
    mc3_u32 status;
    mc3_u32 was_name;       /* what stood in the slot before, for the guard */
    mc3_u32 was_nhoods;
    mc3_u32 snapped;        /* the race-data hook */
    city_slot rec[CITY_SLOTS];   /* the whole table, all six */
};

// One struct, one base address, one relocated pair - rather than three loose
// symbols, which is how GCC ends up with a single `lui` feeding several `%lo`
// and mc3_mkmod refuses the build. Same reasoning as core.cpp's done_slot().
struct city_def {
    const char *name;
    const char *code;
    const char *hoods[12];
};

// The twelve neighbourhoods of Midnight Club 2's Los Angeles, which is what
// this slot is being pointed at.
//
// TWO SPELLINGS, and they are not interchangeable. The .pck stores the bare
// name in a FOUR-BYTE field - three characters and a terminator - which is why
// retail reads `bh`, `dt`, `fwy` rather than whole words. The array here is the
// engine side and takes `<code>_<name>`, exactly as atlanta's is `a_bh`,
// `a_dt`. MC2 spells its own hoods `l_beverlyhills`, `l_downtown`: the code
// prefix carries over unchanged, the rest had to be abbreviated.
//
// Build the matching skeleton with:
//   python mc3_city_build.py --donor atlanta_midnight_clear.pck //       --hoods bh,dt,gh,hl,hw,i05,i10,101,105,ind,lax,sm --out-path ...
static const city_def g_city = {
    "modcity", "m",
    { "m_bh",  "m_dt",  "m_gh",  "m_hl",  "m_hw",  "m_i05",
      "m_i10", "m_101", "m_105", "m_ind", "m_lax", "m_sm" }
};

static __attribute__((noinline)) const city_def *city()
{
    return &g_city;
}

static void snapshot(city_report *r, mc3_u32 base)
{
    for (int i = 0; i < CITY_SLOTS; ++i) {
        const mc3_u32 p = base + (mc3_u32)(i * CITY_STRIDE);
        r->rec[i].name       = *(volatile mc3_u32 *)(p + 0x00);
        r->rec[i].code       = *(volatile mc3_u32 *)(p + 0x04);
        r->rec[i].nhoods     = *(volatile mc3_u32 *)(p + 0x0C);
        r->rec[i].hoods      = *(volatile mc3_u32 *)(p + 0x10);
        r->rec[i].races      = *(volatile mc3_u32 *)(p + 0x14);
        r->rec[i].race_array = *(volatile mc3_u32 *)(p + 0x20);
    }
}

extern "C" void mc3_city_slot6()
{
    // The original first: there is no table to fill until it has run.
    MC3_CALL(void, CITY_TABLE_INIT)();

    city_report *const r = (city_report *)CITY_ADDR;
    for (mc3_u32 i = 0; i < sizeof(city_report) / 4u; ++i)
        ((mc3_u32 *)r)[i] = 0u;
    r->magic = CITY_MAGIC;
    r->ran = 1u;

    const mc3_u32 base = *(volatile mc3_u32 *)CITY_ARRAY;
    r->base = base;
    if (base < 0x00100000u || base >= 0x02000000u || (base & 3u)) {
        r->status = ST_NO_ARRAY;
        return;
    }

    const mc3_u32 slot = base + (mc3_u32)(CITY_FREE * CITY_STRIDE);
    r->slot = slot;
    r->was_name   = *(volatile mc3_u32 *)(slot + 0);
    r->was_nhoods = *(volatile mc3_u32 *)(slot + 12);

    if (r->was_name != (mc3_u32)NOT_INITIALIZED) {
        // Not the empty slot. Report the table as found and change nothing.
        r->status = ST_REFUSED;
        snapshot(r, base);
        return;
    }

    const city_def *const c = city();
    MC3_CALL5(void, CITY_REGISTER, mc3_u32, const char *, const char *,
              int, const char *const *)
        (slot, c->name, c->code,
         (int)(sizeof(g_city.hoods) / sizeof(g_city.hoods[0])), c->hoods);

    r->status = ST_REGISTERED;
    snapshot(r, base);
}

// -----------------------------------------------------------------------------
//  The second hook, and the reason there are two.
//
//  Registering happens at 0x1A0F40. The race data - field +0x14, the count the
//  frontend gates on - is loaded later, by mc::LoadDefaultCityData (0x4B7750)
//  from 0x1A12E0. A snapshot taken at registration time would show 0 races for
//  every city including the shipped ones, which says nothing.
//
//  So this runs the loader and photographs the table on the way out. That loop
//  walks records 0 through 5, so by here it has already tried to open
//  "tune/race/modcity.loc" - and whether that worked is the whole question.
// -----------------------------------------------------------------------------
extern "C" void mc3_city_after_load()
{
    MC3_CALL(void, LOAD_DEFAULT_CITY_DATA)();

    city_report *const r = (city_report *)CITY_ADDR;
    if (r->magic != CITY_MAGIC)         // registration did not run; nothing to say
        return;
    r->snapped += 1u;
    if (r->base)
        snapshot(r, r->base);
}

// The single call to the registry builder, in the game's init path. Hooking it
// is what puts this before every reader instead of after them.
MC3_HOOK(0x001A0F40, mc3_city_slot6);
// The single call to the race-data loader, right after it.
MC3_HOOK(0x001A12E0, mc3_city_after_load);
