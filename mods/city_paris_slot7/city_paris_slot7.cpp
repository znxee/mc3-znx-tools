// -----------------------------------------------------------------------------
//  city_paris_slot7 - register the MC2 cities: Paris as city index 6 and
//  Tokyo (MC2) as 7.
//
//  Retail allocates six city records but registers only five.  Paris and
//  Tokyo need a seventh and an eighth, so eight guarded MIPS immediates grow
//  the allocation, constructor/default-load loops and all four measured lookup
//  bounds from 6 to 8.  The final bound at 0x004B79D8 belongs to
//  mc::LoadCityData; omitting it leaves a visually registered city with zero
//  races.
//
//  Slot 5 remains losangeles/Los Angeles.  Slot 6 is registered as paris/p and
//  slot 7 as tokyo_mc2/k (MC3 already has a "tokyo", slot 3), each with its
//  seven MC3 shell hoods.  Runtime markers prove the registration and the
//  post-load race counts (CCR5/CCR6/CCR7).
//
//  CANNOT COEXIST WITH city_slot6.mod: both want the same two hook sites
//  (0x001A0F40, 0x001A12E0), and a second install would silently replace the
//  first - see docs/WRITING_A_MOD.md. Disable city_slot6.mod in the .ini while
//  this one is on.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_sio.h"

enum {
    CITY_TABLE_INIT        = 0x004B7600,
    CITY_REGISTER          = 0x004B7AD0,
    CITY_ARRAY             = 0x00619D4C,
    LOAD_DEFAULT_CITY_DATA = 0x004B7750,
    CITY_STRIDE            = 76,

    CITY_SLOTS_NEW = 8,
    CITY_LOSANGELES      = 5,     // losangeles, exactly as city_slot6.mod
    CITY_PARIS     = 6,
    CITY_TOKYO_MC2 = 7,

    NOT_INITIALIZED = 0x0066A492,
    FLUSH_CACHE     = 0x00546C20,
};

// One word each, {address, expected current word, new word}. Guarded: a word
// that does not match means this is not the image these addresses were
// measured against, and nothing is written.
struct capacity_patch { mc3_u32 addr, old_word, new_word; };

static __attribute__((noinline)) const capacity_patch *patches()
{
    static const capacity_patch p[] = {
        { 0x004B7604u, 0x240401D8u, 0x24040270u },  // alloc: 16+6*76 -> 16+8*76
        { 0x004B763Cu, 0x24030006u, 0x24030008u },  // vec_new cookie: 6 -> 8
        { 0x004B7618u, 0x24110005u, 0x24110007u },  // ctor loop: build 8
        { 0x004B775Cu, 0x24100005u, 0x24100007u },  // LoadDefaultCityData: walk 0..7
        { 0x004B93B8u, 0x2A020006u, 0x2A020008u },  // LookupCity bound
        { 0x004B95F8u, 0x2A020006u, 0x2A020008u },  // LookupRace bound
        { 0x004BE75Cu, 0x2A020006u, 0x2A020008u },  // SetCity bound
        { 0x004B79D8u, 0x2A620006u, 0x2A620008u },  // LoadCityData bound
    };
    return p;
}

enum { PATCH_COUNT = 8 };

struct cap_state {
    mc3_u32 patched;
    mc3_u32 refused_at;      // address of the first mismatch, 0 if none
    mc3_u32 base;
    mc3_u32 slot5_status;    // 1 registered, 2 refused, 3 no array
};
static cap_state g_state;
static __attribute__((noinline)) cap_state *st(void) { return &g_state; }

// Rewrites the guarded words. Stops at the first mismatch rather than patching
// some and not others - a half-patched allocator is worse than an unpatched
// one.
static void apply_capacity_patch(void)
{
    cap_state *const s = st();
    const capacity_patch *const p = patches();

    for (int i = 0; i < PATCH_COUNT; ++i) {
        volatile mc3_u32 *const w = (volatile mc3_u32 *)p[i].addr;
        if (*w != p[i].old_word) {
            s->refused_at = p[i].addr;
            return;
        }
    }
    for (int i = 0; i < PATCH_COUNT; ++i) {
        volatile mc3_u32 *const w = (volatile mc3_u32 *)p[i].addr;
        *w = p[i].new_word;
    }
    s->patched = (mc3_u32)PATCH_COUNT;

    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
}

struct city_def {
    const char *name;
    const char *code;
    int hood_count;
    const char *hoods[12];
};

// Keep slot 5 identical to the already-proved city_slot6 registration.
static const city_def g_losangeles = {
    "losangeles", "m", 12,
    { "m_bh",  "m_dt",  "m_gh",  "m_hl",  "m_hw",  "m_i05",
      "m_i10", "m_101", "m_105", "m_ind", "m_lax", "m_sm" }
};
static const city_def g_paris = {
    "paris", "p", 7,
    { "p_bh", "p_dt", "p_fwy", "p_lf", "p_mt", "p_ug", "p_we" }
};
static const city_def g_tokyo_mc2 = {
    "tokyo_mc2", "k", 7,
    { "k_bh", "k_dt", "k_fwy", "k_lf", "k_mt", "k_ug", "k_we" }
};
static __attribute__((noinline)) const city_def *losangeles(void) { return &g_losangeles; }
static __attribute__((noinline)) const city_def *paris(void) { return &g_paris; }
static __attribute__((noinline)) const city_def *tokyo_mc2(void) { return &g_tokyo_mc2; }

static void sio_word(int a, int b, int c, int d, mc3_u32 v)
{
    mc3_sio_mark(a, b, c, d, v);
}

extern "C" void mc3_city_paris_slot7(void)
{
    cap_state *const s = st();

    // Patch the constants BEFORE the code that reads them runs. The registry
    // builder self-modifies its own future execution.
    apply_capacity_patch();
    sio_word(67, 67, 80, 84, s->patched);          // CCPT <8 if all patched>
    if (s->refused_at) {
        sio_word(67, 67, 78, 71, s->refused_at);   // CCNG <address that refused>
        return;                                     // do not run an unpatched
                                                      // allocator against a
                                                      // half-applied plan
    }

    MC3_CALL(void, CITY_TABLE_INIT)();

    const mc3_u32 base = *(volatile mc3_u32 *)CITY_ARRAY;
    s->base = base;
    sio_word(67, 67, 66, 65, base);                // CCBA <table base>
    if (base < 0x00100000u || base >= 0x02000000u || (base & 3u)) {
        s->slot5_status = 3;
        return;
    }

    // Slot 6, read BEFORE registration touches anything: proof the patched
    // constructor loop actually built a seventh, well-formed, untouched
    // record rather than leaving garbage past the old boundary.
    const mc3_u32 extra = base + (mc3_u32)(CITY_PARIS * CITY_STRIDE);
    sio_word(67, 67, 69, 78, *(volatile mc3_u32 *)(extra + 0));  // CCEN <name ptr>
    sio_word(67, 67, 69, 72, *(volatile mc3_u32 *)(extra + 12)); // CCEH <hood count>

    // Slot 5: register losangeles, with the same guard city_slot6.mod uses.
    const mc3_u32 slot5 = base + (mc3_u32)(CITY_LOSANGELES * CITY_STRIDE);
    const mc3_u32 was_name = *(volatile mc3_u32 *)(slot5 + 0);
    if (was_name != (mc3_u32)NOT_INITIALIZED) {
        s->slot5_status = 2;
        sio_word(67, 67, 53, 82, was_name);        // CC5R <refused: what was there>
        return;
    }

    const city_def *const c = losangeles();
    MC3_CALL5(void, CITY_REGISTER, mc3_u32, const char *, const char *,
              int, const char *const *)
        (slot5, c->name, c->code,
         c->hood_count, c->hoods);
    s->slot5_status = 1;
    sio_word(67, 67, 53, 79, 1);                   // CC5O <ok>

    // Slot 6 is the new Paris entry.  Its name/code are distinct, so all
    // subsequent PCK/PPF and tune lookups resolve through paris_*/paris.
    const mc3_u32 was_paris = *(volatile mc3_u32 *)(extra + 0);
    if (was_paris != (mc3_u32)NOT_INITIALIZED) {
        sio_word(67, 67, 80, 82, was_paris);       // CP7R <refused>
        return;
    }
    const city_def *const p = paris();
    MC3_CALL5(void, CITY_REGISTER, mc3_u32, const char *, const char *,
              int, const char *const *)
        (extra, p->name, p->code,
         p->hood_count, p->hoods);
    sio_word(67, 67, 80, 79, 1);                   // CP7O <ok>

    // Slot 7: Tokyo (MC2), files named tokyo_mc2_* / tokyo_mc2.
    const mc3_u32 extra7 = base + (mc3_u32)(CITY_TOKYO_MC2 * CITY_STRIDE);
    const mc3_u32 was_tokyo = *(volatile mc3_u32 *)(extra7 + 0);
    if (was_tokyo != (mc3_u32)NOT_INITIALIZED) {
        sio_word(67, 67, 75, 82, was_tokyo);       // CCKR <refused>
        return;
    }
    const city_def *const k = tokyo_mc2();
    MC3_CALL5(void, CITY_REGISTER, mc3_u32, const char *, const char *,
              int, const char *const *)
        (extra7, k->name, k->code,
         k->hood_count, k->hoods);
    sio_word(67, 67, 75, 79, 1);                   // CCKO <ok>
}

extern "C" void mc3_city_paris_slot7_after_load(void)
{
    MC3_CALL(void, LOAD_DEFAULT_CITY_DATA)();

    cap_state *const s = st();
    if (!s->base)
        return;

    // Race counts at +0x14 after LoadDefaultCityData.  A valid Paris install
    // reports 0x18 (one standard Cruise and 23 converted MC2 races).
    const mc3_u32 slot5 = s->base + (mc3_u32)(CITY_LOSANGELES * CITY_STRIDE);
    const mc3_u32 extra = s->base + (mc3_u32)(CITY_PARIS * CITY_STRIDE);
    sio_word(67, 67, 82, 53, *(volatile mc3_u32 *)(slot5 + 0x14));  // CCR5
    sio_word(67, 67, 82, 54, *(volatile mc3_u32 *)(extra + 0x14));  // CCR6
    sio_word(67, 67, 82, 55, *(volatile mc3_u32 *)(extra + CITY_STRIDE + 0x14));  // CCR7
    sio_word(67, 67, 68, 78, *(volatile mc3_u32 *)(extra + 0x00));  // CCDN
                                                                      // <extra name
                                                                      //  ptr again,
                                                                      //  post-load>
}

// The city's siren/helicopter sound bank. sub_209B98 reads it from a table at
// 0x615740, 20 bytes per city - indexed by the current race config's city
// (*(0x619B10)) with no bound, and the table has entries for cities 0..3 only
// (all four "Sirens_Helicopt"). Los Angeles (5) read 0x3F3AE148 - a float -
// as the bank's name and Paris (6) a null pointer, both handed to the audio
// manager and the name hash 0x578648. PCSX2's recompiler logs that as a TLB
// miss and reads zero; the console (and PCSX2's interpreter) takes the
// exception, and the game stopped on the loading screen of either city.
// Its only two callers - mcAudioManager::FrontendStart (0x1BDE24) and
// mcLayerAmbients::Unload (0x1BDF30) - get this instead: Tokyo (MC2) uses
// Tokyo's bank, the other cities past the table San Diego's. (The MCLA PSP
// fork's install hooks the same two calls; it does not load this module.)
enum { SIREN_TABLE = 0x00615740, SIREN_STRIDE = 20, SIREN_CITIES = 4, RACE_CONFIG = 0x00619B10,
       SIREN_TOKYO = 3 };
extern "C" mc3_u32 mc3_city_siren_bank(void)
{
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG;
    mc3_u32 city = cfg ? *(volatile mc3_u32 *)cfg : 0u;
    if (city == (mc3_u32)CITY_TOKYO_MC2)
        city = (mc3_u32)SIREN_TOKYO;
    else if (city >= (mc3_u32)SIREN_CITIES)
        city = 0u;
    return *(volatile mc3_u32 *)(SIREN_TABLE + city * SIREN_STRIDE);
}

MC3_HOOK(0x001A0F40, mc3_city_paris_slot7);
MC3_HOOK(0x001A12E0, mc3_city_paris_slot7_after_load);
MC3_HOOK(0x001BDE24, mc3_city_siren_bank);
MC3_HOOK(0x001BDF30, mc3_city_siren_bank);

// The siren SOUND constructor 0x209AD0(sound, city) indexes the same table on
// its own, `city >= 6 ? entry 0 : entry 5 * city` - a guard for the six city
// records, not for the four entries. Los Angeles (5), the one added city
// below 6, took entry 5: the float 0.5 where the name goes, and strcpy
// 0x432E58 read 0x3F000000 (Next Race; callers mcCarAudio::Init 0x2CFE18 and
// 0x216820, 2026-10-09). Same mapping as the bank above.
extern "C" mc3_u32 mc3_city_siren_sound(mc3_u32 sound, mc3_u32 city)
{
    if (city == (mc3_u32)CITY_TOKYO_MC2)
        city = (mc3_u32)SIREN_TOKYO;
    else if (city >= (mc3_u32)SIREN_CITIES)
        city = 0u;
    return MC3_CALL2(mc3_u32, 0x00209AD0, mc3_u32, mc3_u32)(sound, city);
}
MC3_HOOK(0x00216820, mc3_city_siren_sound);
MC3_HOOK(0x002CFE18, mc3_city_siren_sound);
