// -----------------------------------------------------------------------------
//  pagein_probe - WHO asks for a page-in with a broken datChunkRef?
//
//  THE DEFECT, measured side by side on 09/19 with race_nofe + mc3_pcsx2_shots:
//  the city WITH LA's 500 props gives 43 exceptions in datPageFile::PageIn
//  (pc 0x42CAE4/0x42CBEC/0x42CC1C/0x42CC40, addr 0x5C/0x60/0x70/0x74) and the
//  texture corrupts on screen; the SAME city without props gives ZERO.
//
//  At 0x0042CAE0 the function does `lw $a3, ($a2)` - the first word of the
//  datChunkRefBase - and from there on uses $a3 as a pointer (+0x08, +0x1C,
//  +0x20). The failing addresses say $a3 is 0x54, and in one case 0x58: it is
//  neither NULL nor high garbage, it is a small number where an address should
//  be.
//
//  Looking for that value by sweeping the savestate RAM does not converge -
//  0x54 is far too common, and both attempts landed on vertex data and on a
//  low-RAM table. So instead of looking for the object, this module waits for
//  the CALLER: datChunkRefBase::PageIn (0x0042CC80) is a method, so at the call
//  site $a0 ALREADY IS the chunkref. Each site gets its own handler with a
//  four-letter marker, and it only prints when the first word is broken -
//  non-zero and too small to be a pointer. That way the marker names the
//  subsystem without needing a stack or a debugger.
//
//  The sites are the ones the debug ELF's caller table lists, one per family
//  (there are more in each; one is enough to identify the family):
//
//      PGDR  0x002A625C  rmcDrawable::Touch_Guess          +0x6C
//      PGLG  0x002A7368  rmcLodStreamed::GetModel_Guess    +0x50
//      PGLT  0x002A750C  rmcLodStreamed::Touch_Guess       +0x84
//      PGTB  0x002B8838  rmcTexturePS2::Bind_Guess         +0xD8
//      PGTT  0x002B8CB4  rmcTexturePS2::Touch_Guess        +0x78
//
//  CAREFUL when touching this: the handler REPLACES the `jal`, so it is its
//  duty to call the original and return what it returned. The real signature is
//  PageIn(int, float) const, that is $a0 = this, $a1 = int and the float goes
//  in $f12 - which is why the handlers declare only the two integers: the
//  floating point register passes through untouched, which is what we want.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    PAGE_IN_FILE = 0x0042CA90,  // datPageFile::PageIn_Guess
    SITE         = 0x0042CCB0,  // the ONLY jal to it, in datChunkRefBase::PageIn
    TABLE        = 0x00714CC0,  // table of datPageFile*, indexed by fileId
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }

/* A page table entry is either zero (not requested), odd (pending: the code
 * tests `andi 1`), or a real pointer. Anything else - even, non-zero and too low
 * to be an address - is what PageIn dereferences and blows up on. */
static int bad_entry(mc3_u32 e)
{
    return e != 0u && (e & 1u) == 0u && e < 0x00100000u;
}

static int pagein(mc3_u32 pf, mc3_u32 n, mc3_u32 ref)
{
    mc3_u32 w    = *(volatile mc3_u32 *)(ref + 8);
    mc3_u32 fid  = w >> 23;
    mc3_u32 page  = w & 0x7FFFFFu;
    mc3_u32 tab  = pf ? *(volatile mc3_u32 *)(pf + 0xC) : 0u;
    mc3_u32 entry  = (tab >= 0x00100000u && tab < 0x02000000u)
                 ? *(volatile mc3_u32 *)(tab + page * 4u) : 0u;
    if (bad_entry(entry)) {
        tag('P', 'G', 'B', 'D');            // PGBD = page bad
        hex8(ref); put(' ');                // where the chunkref lives
        hex8(fid); put(' ');                // requested fileId
        hex8(page); put(' ');                // requested page
        hex8(pf);  put(' ');                // the table's datPageFile
        hex8(tab); put(' ');                // its page table
        hex8(entry); put(10);                 // the broken entry
    }
    return MC3_CALL3(int, PAGE_IN_FILE, mc3_u32, mc3_u32, mc3_u32)(pf, n, ref);
}
MC3_HOOK(SITE, pagein);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    /* Nothing per frame: all the work is in the hooks. */
}
