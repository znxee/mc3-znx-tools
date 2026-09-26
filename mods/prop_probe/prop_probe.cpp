// -----------------------------------------------------------------------------
//  prop_probe - do LA's props fit through the TEXT path?
//
//  What is already measured statically (FORKS.md, 09/15):
//
//    mcPropManager::Load (0x00391E10) only knows how to load the .pck. If
//    datPaging::LoadBuild returns 0, the code falls into an INFINITE LOOP of
//    nops at 0x00391F88..0x00391F9C. There is no fallback, and that is why
//    removing props.pck hangs the boot at 20 s instead of crashing.
//
//    But mcPropType::LoadTypeData_Guess (0x00395460) is ALIVE in the retail ELF
//    (checked byte for byte against the debug ELF, 6/6 addresses) and reads
//    TEXT through datTokenizer, expecting `prop_template <name> {
//    proptype_index: <int> proptype: <tok> numlods: <int> ... }`. The
//    proptype_index goes to this+0x4D, which is exactly the field InitRaceProp
//    reads to call AllocateProp. It has ZERO callers: it is live, orphaned code.
//
//    And mcPropManagerRaceData::InitRaceProp (0x00393A00) shows the recipe for
//    creating a prop at runtime: FindType(name) -> AllocateProp(type+0x4D, idx)
//    -> vtable[0x34](name, matrix, 0) -> vtable[0x38](0.0f).
//
//  This probe creates NO prop. It answers the three questions that decide
//  whether the real module is worth writing, and nothing more:
//
//    1. does mcPropManager exist and answer at this point of the boot?
//    2. does FindType find a type that came from the .pck (today, Tokyo's)?
//    3. does FindType refuse an LA name, confirming the type is what is missing?
//
//  Question 3 matters because InitRaceProp enters an INFINITE LOOP when
//  FindType returns zero (0x00393AD0) - knowing that BEFORE calling avoids
//  hanging the game blindly.
//
//  The three rules of the .mod format apply: mod_main in .text.start, no data
//  constants of its own, every fixed address behind a noinline accessor.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

enum {
    PROP_GET_INSTANCE = 0x00392948,   // mcPropManager::GetInstance(void)
    PROP_FIND_TYPE    = 0x00392978,   // mcPropManager::FindType(const char *)
    PROP_GET_NUM      = 0x00392650,   // mcPropManager::GetNumProps(int) const
    GUARD            = 0x0061CB00,   // 0x0061CAC0 is core.mod's mc3_core_trace
    DONE             = 0x50525031u,  // PRP1
    WAIT_FRAMES            = 600,
};

static __attribute__((noinline)) void put(char c)
{
    *(volatile unsigned char *)0x1000F180 = (unsigned char)c;
}
static __attribute__((noinline)) volatile mc3_u32 *guard(void)
{
    return (volatile mc3_u32 *)GUARD;
}
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 0xF;
        put((char)(d < 10 ? ('0' + d) : ('A' + d - 10)));
    }
}
static void tag(char a, char b, char c, char d) { put(a); put(b); put(c); put(d); put(' '); }
static int sane(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u; }

// Names built by hand: the .mod format does not accept data constants of its
// own, so each character is an assignment. Two from Tokyo (what the installed
// props.pck has today) and two from LA (what we want to start existing).
static void name_tokyo_door(char *b)
{
    int i = 0;
    b[i++]='t'; b[i++]='_'; b[i++]='p'; b[i++]='r'; b[i++]='o'; b[i++]='p';
    b[i++]='_'; b[i++]='d'; b[i++]='o'; b[i++]='o'; b[i++]='r'; b[i++]='1';
    b[i++]='5'; b[i]=0;
}
static void name_tokyo_light(char *b)
{
    int i = 0;
    b[i++]='t'; b[i++]='_'; b[i++]='p'; b[i++]='r'; b[i++]='o'; b[i++]='p';
    b[i++]='_'; b[i++]='l'; b[i++]='i'; b[i++]='g'; b[i++]='h'; b[i++]='t';
    b[i++]='0'; b[i++]='1'; b[i]=0;
}
static void name_la_dumpster(char *b)
{
    int i = 0;
    b[i++]='l'; b[i++]='_'; b[i++]='p'; b[i++]='r'; b[i++]='o'; b[i++]='p';
    b[i++]='_'; b[i++]='d'; b[i++]='u'; b[i++]='m'; b[i++]='p'; b[i++]='s';
    b[i++]='t'; b[i++]='e'; b[i++]='r'; b[i++]='_'; b[i++]='0'; b[i++]='1';
    b[i++]='x'; b[i]=0;
}
static void name_la_streetlight(char *b)
{
    int i = 0;
    b[i++]='l'; b[i++]='_'; b[i++]='p'; b[i++]='r'; b[i++]='o'; b[i++]='p';
    b[i++]='_'; b[i++]='s'; b[i++]='t'; b[i++]='r'; b[i++]='e'; b[i++]='e';
    b[i++]='t'; b[i++]='l'; b[i++]='i'; b[i++]='g'; b[i++]='h'; b[i++]='t';
    b[i++]='_'; b[i++]='0'; b[i++]='1'; b[i++]='x'; b[i]=0;
}

static void report(mc3_u32 mgr, const char *n, char a, char b)
{
    // FIND <marker> <type pointer>. Zero = FindType refused.
    mc3_u32 t = 0;
    if (sane(mgr))
        t = MC3_CALL2(mc3_u32, PROP_FIND_TYPE, mc3_u32, const char *)(mgr, n);
    tag(70, 78, a, b); hex8(t); put(10);
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    // PERIODIC SAMPLE, for the same reason as city_probe: a single read does not
    // tell "the manager does not exist" from "the manager is not up yet".
    // The guard does NOT start zeroed, and its initial value cannot be assumed:
    // with a limit test ('stop after N samples') the module switched itself off
    // on the FIRST call when the garbage was already above the limit, and
    // printed nothing. Only the remainder by WAIT_FRAMES matters, and it holds
    // for any start.
    volatile mc3_u32 *g = guard();
    mc3_u32 n = *g + 1u;
    *g = n;
    if (n % WAIT_FRAMES) return;

    mc3_u32 mgr = MC3_CALL(mc3_u32, PROP_GET_INSTANCE)();
    tag(80, 77, 71, 82); hex8(mgr); put(32); hex8(n); put(10);   // PMGR <inst> <frames>
    if (!sane(mgr))
        return;

    // how many live props there are in each of the manager's three lists
    for (int k = 0; k < 3; ++k) {
        mc3_u32 q = MC3_CALL2(mc3_u32, PROP_GET_NUM, mc3_u32, int)(mgr, k);
        tag(78, 85, 77, (char)('0' + k)); hex8(q); put(10);      // NUMk <count>
    }

    // ENUMERATE THE TYPES. mcCullableClass::FindType (0x0024A5D8) shows the
    // walk: the list head is at cullable+4, each node's name at node+8 and the
    // next at node+0xC. It is the SAME walk the final module will use, so it is
    // worth confirming here first.
    mc3_u32 data = *(volatile mc3_u32 *)(mgr + 4);
    if (!sane(data)) { tag(68, 65, 84, 65); hex8(data); put(10); return; }
    mc3_u32 cull = *(volatile mc3_u32 *)(data + 8);
    tag(67, 85, 76, 76); hex8(cull); put(10);                    // CULL <pointer>
    if (!sane(cull)) return;

    mc3_u32 node = *(volatile mc3_u32 *)(cull + 4);
    int k = 0;
    while (sane(node) && k < 400) {
        mc3_u32 nm = *(volatile mc3_u32 *)(node + 8);
        tag(84, 89, 80, (char)('0' + (k % 10))); hex8(node); put(32);
        if (sane(nm & ~3u)) {
            for (int i = 0; i < 28; ++i) {
                unsigned char c = *(volatile unsigned char *)(nm + i);
                if (!c) break;
                put((c >= 32 && c < 127) ? (char)c : '.');
            }
        } else {
            put('?');
        }
        put(10);
        node = *(volatile mc3_u32 *)(node + 0xC);
        ++k;
    }
    tag(84, 67, 78, 84); hex8((mc3_u32)k); put(10);              // TCNT <read>
}
