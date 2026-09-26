// -----------------------------------------------------------------------------
//  optional_props - lets the game go on without <city>_<weather>_props.pck.
//
//  THE PROBLEM, measured on 09/15 and recorded in FORKS.md:
//
//    mcPropManager::Load (0x00391E10) builds the path from
//    "$/resources/prop/%s_%s_%s_props" and calls datPaging::LoadBuild. If the
//    file does not exist, LoadBuild returns 0 and the code falls into:
//
//        00391f7c  lw    $v0, 4($s2)        ; the prop data pointer
//        00391f80  bne   $v0, $zero, +8     ; only goes on if it loaded
//        00391f84  lui   $v0, 0x61          ; delay slot, used further on
//        00391f88  nop                      ; <-- loop target
//        ...
//        00391f9c  b     0x391f88           ; INFINITE LOOP
//
//    That is NOT a crash: it is a development assertion that hangs the
//    machine. That is why the boot died at 20 s when I renamed the props.
//
//  WHY IT IS SAFE TO GO ON: the rest of the props code was already written to
//  tolerate the null pointer. Checked function by function in the retail ELF:
//
//    mcPropManager::GetNumProps  (0x00392650)  lw $v0,4($a0); bnez; move $v0,$zero
//    mcPropManager::GetPropMC3   (0x00392620)  lw $a0,4($a0); bnez; move $v0,$zero
//    mcPropManager::Kill         (0x00391FF0)  lw $a0,4($s0); beqz -> skips the Delete
//
//    They all return zero instead of dereferencing. The only point that hangs
//    is the loop above.
//
//  THE PATCH: replace the `bne $v0, $zero, +8` (0x14400008) with
//  `beq $zero, $zero, +8` (0x10000008), which is the same branch made
//  unconditional. The delay slot at 0x00391F84 keeps executing in both cases -
//  `b` executes a delay slot too - and it matters, because 0x00391FA8 uses the
//  $v0 it loads.
//
//  Idempotent: it only writes if it finds the original opcode, and counts how
//  many times it applied. If another module already patched it, this one does
//  nothing.
//
//  The three rules of the .mod format apply: mod_main in .text.start, no data
//  constants of its own, every fixed address behind a noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    TARGET      = 0x00391F80,   // the bne inside mcPropManager::Load
    ORIGINAL    = 0x14400008u,  // bne $v0, $zero, +8
    ALWAYS      = 0x10000008u,  // beq $zero, $zero, +8  (same target)
    FLUSH_CACHE = 0x00546C20,   // FlushCache(int)
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static __attribute__((noinline)) volatile mc3_u32 *target(void)
{
    return (volatile mc3_u32 *)TARGET;
}
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    // NO guard and NO wait, on purpose. Measured: without props.pck the game
    // hangs in the loop BEFORE the first frame - the log stops at 592 lines and
    // no per-frame module gets to print. So the patch has to take effect the
    // first time this body runs, whether through the early pass (`= shim` in
    // the .ini) or per frame. The check below makes that cheap and idempotent:
    // two reads and a compare when it is already in place.
    volatile mc3_u32 *p = target();
    mc3_u32 current = *p;
    if (current == ALWAYS) return;          // already patched, nothing to do
    if (current != ORIGINAL) {              // someone changed it: do not overwrite
        tag(80, 82, 79, 63); hex8(current); put(10);     // PRO? <what I found>
        return;
    }

    *p = ALWAYS;
    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
    tag(80, 82, 79, 80); hex8(*p); put(10);                     // PROP <new>
}
