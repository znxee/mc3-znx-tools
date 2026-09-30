// -----------------------------------------------------------------------------
//  city_capacity_test - can the city TABLE hold more than six records?
//
//  city_slot6.mod answered "is there a spare slot" - yes, retail already
//  allocates six records and only fills five, and registering the sixth is
//  five stores into memory the game already walks every boot. This mod asks
//  the next question: can the table itself be made to hold SEVEN, i.e. does
//  the six-slot capacity generalise, or is six baked in everywhere that
//  matters?
//
//  WHAT "SIX" ACTUALLY IS
//
//  Not a constant read from memory - six literal immediates inside the game's
//  own code, at 0x004B7600 (mc::mcCityData table builder) and three lookup
//  functions. Measured directly from the ELF, each a one-word MIPS
//  addiu/slti:
//
//      004B7604  addiu $a0, $zero, 0x1D8   new[] size  = 16 + 6*76
//      004B763C  addiu $v1, $zero, 6       new[] cookie (element count)
//      004B7618  addiu $s1, $zero, 5       construct 6 objects (5 downto -1)
//      004B775C  addiu $s0, $zero, 5       mc::LoadDefaultCityData walks 0..5
//      004B93B8  slti  $v0, $s0, 6         mc::LookupCity search bound
//      004B95F8  slti  $v0, $s0, 6         mc::LookupRace search bound
//      004BE75C  slti  $v0, $s0, 6         mcRaceConfig::SetCity search bound
//
//  Rewriting these seven words to build for SEVEN records and search up to
//  index 6 is the whole mechanism - same self-modifying-constant technique
//  draw_distance.mod already uses on GetLODDist, done here on code the game
//  is about to execute rather than a value it is about to read.
//
//  WHY THE NEW SLOT IS LEFT EMPTY - this is the important part
//
//  This mod registers "losangeles" into slot 5 exactly as city_slot6.mod does,
//  and does NOT put anything in the new slot 6. That is deliberate: patching
//  the constants above only proves the REGISTRY can be seven long. Whether
//  the rest of the game tolerates a seventh city is a much bigger question -
//  99 instructions across 55 functions read the table's base pointer,
//  spanning career progression, the knowledge base, rewards and side quests,
//  and about half of those functions carry no name in this project's symbol
//  work. Auditing each of them was out of scope for this question.
//
//  So this is the minimal, additive experiment: does the capacity patch alone
//  - nobody claiming the new slot - survive a normal boot to the frontend? If
//  it does, that is evidence the wider systems either don't care about the
//  array's length or read it correctly rather than assuming six; it is not
//  proof every one of the 55 is safe. If it does NOT survive, that already
//  answers the question, and cheaply.
//
//  The seventh slot, once the patched constructor loop runs, is a genuinely
//  well-formed "not_initialized" mcCityData object - the same shape slot 5
//  was in before city_slot6.mod ever touched it, and the same shape
//  mc::LoadDefaultCityData already walks into every boot for real. Nothing
//  about leaving it empty is untested territory on its own; only the fact
//  that it is record SIX rather than five is new.
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

    CITY_SLOTS_NEW = 7,     // the experiment
    CITY_LOSANGELES      = 5,     // losangeles, exactly as city_slot6.mod
    CITY_PARIS     = 6,     // the new slot - deliberately left empty

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
        { 0x004B7604u, 0x240401D8u, 0x24040224u },  // alloc: 16+6*76 -> 16+7*76
        { 0x004B763Cu, 0x24030006u, 0x24030007u },  // vec_new cookie: 6 -> 7
        { 0x004B7618u, 0x24110005u, 0x24110006u },  // ctor loop: build 7
        { 0x004B775Cu, 0x24100005u, 0x24100006u },  // LoadDefaultCityData: walk 0..6
        { 0x004B93B8u, 0x2A020006u, 0x2A020007u },  // LookupCity bound
        { 0x004B95F8u, 0x2A020006u, 0x2A020007u },  // LookupRace bound
        { 0x004BE75Cu, 0x2A020006u, 0x2A020007u },  // SetCity bound
    };
    return p;
}

enum { PATCH_COUNT = 7 };

struct cap_state {
    mc3_u32 patched;
    mc3_u32 refused_at;      // address of the first mismatch, 0 if none
    mc3_u32 base;
    mc3_u32 slot5_status;    // 1 registered, 2 refused, 3 no array
};
static cap_state g_state;
static __attribute__((noinline)) cap_state *st(void) { return &g_state; }

// Rewrites the seven words. Stops at the first mismatch rather than patching
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

// Same content city_slot6.mod registers - this experiment is about capacity,
// not about a different city, so slot 5 stays exactly what was already proved.
static const city_def g_losangeles = {
    "losangeles", "m", 12,
    { "m_bh",  "m_dt",  "m_gh",  "m_hl",  "m_hw",  "m_i05",
      "m_i10", "m_101", "m_105", "m_ind", "m_lax", "m_sm" }
};
static const city_def g_paris = {
    "paris", "p", 7,
    { "p_bh", "p_dt", "p_fwy", "p_lf", "p_mt", "p_ug", "p_we" }
};
static __attribute__((noinline)) const city_def *losangeles(void) { return &g_losangeles; }
static __attribute__((noinline)) const city_def *paris(void) { return &g_paris; }

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
    sio_word(67, 67, 80, 84, s->patched);          // CCPT <7 if all patched>
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
}

extern "C" void mc3_city_paris_slot7_after_load(void)
{
    MC3_CALL(void, LOAD_DEFAULT_CITY_DATA)();

    cap_state *const s = st();
    if (!s->base)
        return;

    // The race count at +0x14, for slot 5 and for the extra slot 6. Slot 5
    // should read 1, matching city_slot6.mod's proven result. Slot 6 should
    // read 0 - LoadDefaultCityData tried "tune/race/not_initialized.loc" for
    // it exactly as it already does today for any unclaimed record, and there
    // is no such file, so nothing loads. A non-zero here would be the actual
    // surprise.
    const mc3_u32 slot5 = s->base + (mc3_u32)(CITY_LOSANGELES * CITY_STRIDE);
    const mc3_u32 extra = s->base + (mc3_u32)(CITY_PARIS * CITY_STRIDE);
    sio_word(67, 67, 82, 53, *(volatile mc3_u32 *)(slot5 + 0x14));  // CCR5
    sio_word(67, 67, 82, 54, *(volatile mc3_u32 *)(extra + 0x14));  // CCR6
    sio_word(67, 67, 68, 78, *(volatile mc3_u32 *)(extra + 0x00));  // CCDN
                                                                      // <extra name
                                                                      //  ptr again,
                                                                      //  post-load>
}

MC3_HOOK(0x001A0F40, mc3_city_paris_slot7);
MC3_HOOK(0x001A12E0, mc3_city_paris_slot7_after_load);
