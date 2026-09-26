// -----------------------------------------------------------------------------
// no_road_reflectors_test - controlled modloader diagnostic.
//
// This is deliberately not named or documented as a fix. It makes
// mcRoadReflectorSystem::RenderAllTypes return immediately so an A/B test can
// establish whether the reflector path participates in the first-frame hang.
//
// Safety rule: write nothing unless both original words match the retail ELF.
// A different executable or an earlier patch therefore turns this module into
// a no-op instead of writing into unknown code.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    RENDER_ALL_TYPES = 0x001DBD58u,
    ORIGINAL_WORD_0 = 0x27BDFFE0u,
    ORIGINAL_WORD_1 = 0x3C020062u,
    JR_RA            = 0x03E00008u,
    NOP              = 0x00000000u,
    FLUSH_CACHE      = 0x00546C20u,
};

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *const code =
        (volatile mc3_u32 *)(mc3_u32)RENDER_ALL_TYPES;

    if (code[0] != (mc3_u32)ORIGINAL_WORD_0 ||
        code[1] != (mc3_u32)ORIGINAL_WORD_1)
        return;

    code[0] = (mc3_u32)JR_RA;
    code[1] = (mc3_u32)NOP;

    // The core also flushes after loading deferred modules. Keep this here so
    // the module remains correct if another fork changes its load mode.
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
}
