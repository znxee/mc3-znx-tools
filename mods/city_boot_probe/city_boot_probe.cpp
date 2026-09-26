// -----------------------------------------------------------------------------
//  city_boot_probe - what is the session's city index the INSTANT after
//  mcConfig::CopyNextToCurrent runs, before anything later in Main can touch
//  it again.
//
//  city_index_watch.mod already showed the index is 5 by frame 1 - but frame 1
//  is AFTER the whole of Main's init has run, so it cannot tell "CopyNextToCurrent
//  left it at 5" apart from "CopyNextToCurrent left it at 0 and something later
//  in Main overwrote it". Disassembly says the latter should be true: the
//  object it copies FROM (dword_619B14) was just set to "sd" (index 0) by the
//  function called immediately before, unconditionally.
//
//  This hooks the jal RIGHT AFTER CopyNextToCurrent - Main_void+0x1A0F8C, the
//  call into mcGame::InitPreCity - and reads the session object before letting
//  the original run. There is nothing else between the two in the disassembly,
//  so this reading is as close to CopyNextToCurrent's own return as a hook can
//  get without patching CopyNextToCurrent itself.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    SITE = 0x001A0F8C,
    ORIG = 0x001A12D0,   // mcGame::InitPreCity(void), decoded from the jal
    SESSAO = 0x00619B10,

    // Right after mcGame::InitCareer(void) returns - the jal into
    // mcMemCard::CreateInstance(void) at Main+0x1A12F0. Brackets whether the
    // write happens INSIDE InitCareer (mcPgCareer construction, its
    // parFileIO::Load("career")) or somewhere after it.
    SITE2 = 0x001A12F0,
    ORIG2 = 0x001AE6E8,  // mcMemCard::CreateInstance(void), decoded from the jal

    // Right after mcMemCard::CreateInstance(void) returns.
    SITE3 = 0x001A12F8,
    ORIG3 = 0x00205EA0,  // mcAudioManager::CreateInstance(bool), decoded from the jal
};

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

extern "C" void city_boot_probe_hook(mc3_u32 a0)
{
    const mc3_u32 ptr = *(volatile mc3_u32 *)SESSAO;
    tag('C', 'B', 'P', 'P'); hex8(ptr); put(10);   // CBPP <session ptr>
    if (sane(ptr)) {
        tag('C', 'B', 'P', 'V');                    // CBPV <city index right here>
        hex8(*(volatile mc3_u32 *)ptr);
        put(10);
    }
    MC3_CALL1(void, ORIG, mc3_u32)(a0);
}

extern "C" void city_boot_probe_hook2(void)
{
    const mc3_u32 ptr = *(volatile mc3_u32 *)SESSAO;
    tag('C', 'B', 'Q', 'V');                    // CBQV <city index after InitCareer>
    if (sane(ptr))
        hex8(*(volatile mc3_u32 *)ptr);
    else
        hex8(0xFFFFFFFFu);
    put(10);
    MC3_CALL(void, ORIG2)();
}

extern "C" void city_boot_probe_hook3(mc3_u32 a0)
{
    const mc3_u32 ptr = *(volatile mc3_u32 *)SESSAO;
    tag('C', 'B', 'R', 'V');                    // CBRV <city index after mcMemCard::CreateInstance>
    if (sane(ptr))
        hex8(*(volatile mc3_u32 *)ptr);
    else
        hex8(0xFFFFFFFFu);
    put(10);
    MC3_CALL1(void, ORIG3, mc3_u32)(a0);
}

MC3_HOOK(SITE, city_boot_probe_hook);
MC3_HOOK(SITE2, city_boot_probe_hook2);
MC3_HOOK(SITE3, city_boot_probe_hook3);
