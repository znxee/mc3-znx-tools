// -----------------------------------------------------------------------------
//  console_probe - proves the output channel through the EE CONSOLE.
//
//  WHY IT EXISTS. This project's mods only talk through RAM: they write to a
//  fixed address and someone takes a savestate to read it, which costs a manual
//  boot per round. PCSX2 logs the EE console output to the log file when
//  EnableEEConsole is on, and mc3_pcsx2.py already runs the emulator from the
//  command line - if a mod writes to that channel, the loop closes without a
//  savestate.
//
//  THE CHANNEL: writing a byte to the SIO transmit register, at 0x1000F180, is
//  the classic homebrew idiom for printing on the EE, and it needs neither libc
//  nor ps2sdk - a requirement here, because these modules link against nothing.
//
//  MEASURED BEFORE WRITING: the mc3boot report does NOT show up in the log,
//  because it uses scr_printf and draws to the framebuffer. Hence the probe,
//  instead of assuming any print will do.
//
//  TWO RESTRICTIONS OF THE .mod FORMAT this file respects, both discovered by
//  the packer refusing the build:
//
//    * each data base needs its `lui`/`lo` pair together, so every access to a
//      fixed address goes behind a `noinline` accessor - without that GCC shares
//      one `lui` across several accesses and the relocation does not close;
//    * NO data constants of its own: string literals and tables become a
//      section with its own base and fall into the same problem. That is why
//      the text goes out character by character and the hex digit is computed.
//
//  Without MC3_HOOK, the loader calls this once per frame; the guard in RAM lets
//  only the first one through.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}

static __attribute__((noinline)) volatile mc3_u32 *guard(void)
{
    return (volatile mc3_u32 *)0x0061CAB0;   // free window next to the trace
}

static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}

// THE FUNCTION MUST BE CALLED mod_main AND LIVE IN .text.start.
//
// The first version of this used the mod.ld of a hook-only module and a name
// of its own, and the build warned without my reading it: "entry +0". A module
// without MC3_HOOK is called by the per-frame callback, and the loader finds
// that callback through the header's ENTRY field, which the packer takes from
// the ELF's e_entry. With entry 0 there is nothing to call - the module loaded
// and never ran, and the savestate showed its guard zeroed.
extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *g = guard();
    if (*g == 0x50524F42u)
        return;
    *g = 0x50524F42u;

    put('M'); put('C'); put('3'); put('P'); put('R'); put('O'); put('B');
    put('E'); put(' ');
    hex8(0xC0FFEE01u);
    put('\n');

    // CHANNEL 2: make the GAME OPEN A FILE with a distinctive name.
    //
    // Measured: the IOP console logs every `open` - that is how the loader's
    // eight `open name host0:/<mod>` lines showed up. If the game's Open goes
    // through the same path, the name shows up in the log and I get TWO results
    // at once: proof that the callback ran, and proof of whether the game's
    // Open is observable - which is the open question about `defer` not working
    // on a real PS2.
    //
    // The string goes ON THE STACK because the .mod format does not accept data
    // constants of its own: a literal would become a section with its own base
    // and the relocation would not close.
    char name[20];
    name[0]='h'; name[1]='o'; name[2]='s'; name[3]='t'; name[4]='0';
    name[5]=':'; name[6]='/'; name[7]='M'; name[8]='C'; name[9]='3';
    name[10]='P'; name[11]='R'; name[12]='O'; name[13]='B'; name[14]='E';
    name[15]='.'; name[16]='t'; name[17]='x'; name[18]='t'; name[19]=0;
    MC3_CALL2(mc3_u32, 0x003991F0, const char *, int)(name, 1);
}
