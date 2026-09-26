// -----------------------------------------------------------------------------
// proper_widescreen - runtime-selectable 4:3 / 16:9 / 21:9 patch engine.
//
// The patch words are generated from the supplied SuperType1/Remco PNACH files;
// none of their injected MIPS is translated by hand.  4:3 is not an empty
// profile: profiles.generated.h contains every touched address with the retail
// ELF word (or zero for the 0x00180000 code cave), so changing back really
// removes the widescreen hooks.
//
// This module has no game hook. Load it as `defer`, which makes core.mod call its
// entry every frame from stable heap memory.  That is intentional: the two full
// tables are larger than the 10 KB boot cave, and patch=1 PNACH semantics mean
// the selected table should be checked again each frame anyway.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../proper_widescreen_shared.h"
#include "profiles.generated.h"


static int poke_word(mc3_u32 address, mc3_u32 value)
{
    volatile mc3_u32 *const word = (volatile mc3_u32 *)address;
    if (*word == value)
        return 0;
    *word = value;
    return 1;
}


// EE kernel syscall 100: write back D-cache first, then invalidate I-cache.
// The tables replace executable instructions as well as ordinary constants.
static void ee_flush_cache(int mode)
{
    asm volatile(
        "move  $4, %0  \n"
        "li    $3, 100 \n"
        "syscall       \n"
        :
        : "r"(mode)
        : "$2", "$3", "$4", "$5", "$6", "$7", "memory");
}


static mc3_u32 apply_table(const ws_write *writes, mc3_u32 count)
{
    mc3_u32 changed = 0;
    for (mc3_u32 i = 0; i < count; ++i)
        changed += (mc3_u32)poke_word(writes[i].address, writes[i].value);
    return changed;
}


static mc3_u32 apply_profile(mc3_u32 profile)
{
    switch (profile) {
    case WS_PROFILE_16X9:
        return apply_table(ws_16x9, WS_16X9_COUNT);
    case WS_PROFILE_21X9:
        return apply_table(ws_21x9, WS_21X9_COUNT);
    default:
        return 0; // 4:3 is represented by ws_stock during a profile change.
    }
}


static void flush_if_changed(mc3_u32 changed)
{
    if (!changed)
        return;
    ee_flush_cache(0);
    ee_flush_cache(2);
}


extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile ws_mailbox *const box = ws_get_mailbox();

    // The menu module normally creates this during game init.  Keeping a
    // fallback makes the engine useful by itself and gives it a sane default.
    if (box->magic != WS_MAILBOX_MAGIC) {
        box->requested = (mc3_u32)WS_PROFILE_16X9;
        box->current = (mc3_u32)WS_PROFILE_PENDING;
        box->switches = 0;
        box->magic = WS_MAILBOX_MAGIC;
    }

    mc3_u32 requested = box->requested;
    if (!ws_profile_valid(requested)) {
        requested = (mc3_u32)WS_PROFILE_16X9;
        box->requested = requested;
    }

    if (box->current != requested) {
        // First remove every hook/data word belonging to the previous profile.
        // This also handles words present in only one of the two PNACH files.
        mc3_u32 changed = apply_table(ws_stock, WS_STOCK_COUNT);
        changed += apply_profile(requested);
        flush_if_changed(changed);

        box->current = requested;
        box->switches += 1u;
        return;
    }

    // Reproduce patch=1 semantics.  Read/compare avoids stores and cache flushes
    // in the normal case, but repairs a word if the game overwrites one later.
    if (requested != (mc3_u32)WS_PROFILE_4X3)
        flush_if_changed(apply_profile(requested));
}
