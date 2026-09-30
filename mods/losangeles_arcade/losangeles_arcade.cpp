// -----------------------------------------------------------------------------
//  losangeles_arcade - the added cities (5 losangeles, 6 paris) in the Arcade menu.
//
//  Two things kept them out, both measured in the ELF:
//
//  1. mcMenuArcade::ChangeCity (0x0033DA38) steps the city index with a
//     hard-coded modulo 4:
//         0033DAB4  li    v1, 4          ; div (cur + dir) by 4
//         0033DAD8  addiu s1, a2, 4      ; negative remainder + 4
//     and skips a city whose race count (record +0x14) is 0 or that is not
//     unlocked. Both words become 7 (the size of the city table with
//     city_paris_slot7); nocity (4) has no races and keeps being skipped.
//  2. mcPgUnlockingRulesEval::IsCityUnlocked (0x004D42B0) asks the save's
//     rule table (IsRuleSatisfied(this, 5, city, 1), jal at 0x004D42CC), which
//     only knows the retail cities. For city >= 5 the hook answers "unlocked";
//     every other question goes to the original.
//
//  The code words are rewritten from the hook (it runs during frontend setup,
//  long before the Arcade menu), guarded: only when they still hold the retail
//  instruction, then the caches are flushed.
//
//  (Leaving the city back to the front end is handled by the renderer,
//  city_mc2_rsc_draw_test: it has to give its heap back at that very call.)
//
//  Markers: RARC <patched words> <city asked>  (first time only)
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"

enum {
    RULE_CALL = 0x004D42CC,          // jal IsRuleSatisfied in IsCityUnlocked
    IS_RULE_SATISFIED = 0x004D7508,
    RULE_CITY_UNLOCKED = 5,
    FIRST_ADDED_CITY = 5,
    FLUSH_CACHE = 0x00546C20,
    MOD_WORD_A = 0x0033DAB4, MOD_OLD_A = 0x24030004u, MOD_NEW_A = 0x24030007u,
    MOD_WORD_B = 0x0033DAD8, MOD_OLD_B = 0x24D10004u, MOD_NEW_B = 0x24D10007u,
};

static __attribute__((noinline)) void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) { unsigned d = (v >> i) & 15u; put((char)(d < 10 ? '0' + d : 'A' + d - 10)); }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}

struct State { mc3_u32 done; };
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }

static mc3_u32 patch_word(mc3_u32 addr, mc3_u32 old_word, mc3_u32 new_word) {
    volatile mc3_u32 *w = (volatile mc3_u32 *)addr;
    if (*w == old_word) { *w = new_word; return 1u; }
    return *w == new_word ? 0u : 0x100u;          // 0x100: unexpected word, left alone
}

extern "C" mc3_u32 rule_hook(mc3_u32 self, mc3_u32 rule, mc3_u32 city, mc3_u32 flag) {
    State *s = st();
    if (!s->done) {
        s->done = 1u;
        const mc3_u32 n = patch_word(MOD_WORD_A, MOD_OLD_A, MOD_NEW_A) +
                          patch_word(MOD_WORD_B, MOD_OLD_B, MOD_NEW_B);
        if (n & 3u) {
            MC3_CALL1(void, FLUSH_CACHE, int)(0);
            MC3_CALL1(void, FLUSH_CACHE, int)(2);
        }
        mark('R','A','R','C', n, city);
    }
    if (rule == RULE_CITY_UNLOCKED && city >= FIRST_ADDED_CITY && city < 16u) return 1u;
    return MC3_CALL4(mc3_u32, IS_RULE_SATISFIED, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(self, rule, city, flag);
}
MC3_HOOK(RULE_CALL, rule_hook);


extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
