// -----------------------------------------------------------------------------
//  mc2_aib_compat - MC2's <city>.aib read whole by MC3's rail network loader.
//
//  MC3 reads MC2's losangeles.aib (installed as losangeles_city.aib) almost as is:
//  aiRailNetwork::LoadRailNetwork (0x503798) is a TaggedStream loop with
//  versioned tags. One record differs: a traffic control (tag 0x4105) is, in
//  MC3, u16 count + count u16 road ids + two floats (10.0 and 2.5 in every
//  retail file); MC2 stops after the ids. The reader (sub_506F30) always
//  takes the two floats, i.e. 8 bytes past MC2's record, and
//  TaggedStream::ReadTag (0x5B86F8) ends the whole stream when a reader went
//  past its record. So everything after the FIRST traffic control was lost:
//  the other 94 controls and the whole aiRouter section (0x4106: box, cell
//  size, cell -> road lists). Without the router the ambient traffic finds no
//  road near the player to put a car on: measured, 0 of 33 traffic cars on a
//  rail in Los Angeles against 33 of 33 in Tokyo.
//
//  The reader's call (jal at 0x503DD8, case 0x4105) is hooked: count and ids
//  as before, and the two floats only when the record still has room for
//  them (the record's end is TaggedStream+8), else the retail values. Then
//  the reader's own tail: the copies at +28/+32 and
//  aiTrafficControl::ValidateCrossTraffic (0x507A50). Retail files take the
//  same path as before. Marker (first MC2-style record only):
//  RAIB <records without floats> <count of the first>
//
//  Rail flags. Rail records (tag 0x4109, 20 bytes, flags byte at +18) have the
//  same layout in both games but bit 1 does not mean the same: in MC3 (Tokyo)
//  it is set on 398 of 4354 rails, always together with bit 5; in MC2's LA on
//  4500 of 5183, most of them without bit 5. MC3's traffic placement
//  (bPlaceVehicleInRoad, 0x21B9D0) skips a rail with bit 1, so after the
//  MC2 file loaded no feed road had a usable rail and traffic stayed at 0.
//  In MC2 bit 1 is on car lanes AND sidewalks; bit 3 marks a PEDESTRIAN rail
//  (all 1413 of LA's have width field +10 = 40: the paths 2.5 m outside the
//  outer lanes and the loops around planters - MC2's peds walk the same aib).
//  When the file was MC2's (a traffic control without timings was seen), once
//  LoadRailNetwork returns (hook on its call, 0x5032C4): pedestrian rails get
//  bits 1 and 5, matching the closed/no-traffic flags used together by native
//  MC3 rails. Car rails without bit 5 lose bit 1. Marker
//  RAIF <car rails opened | total pedestrian rails closed << 16> <rails>.
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"

enum {
    CONTROL_READ_CALL = 0x00503DD8,   // jal sub_506F30 (case 0x4105)
    STREAM_READ = 0x003993A8,         // Stream::Read(stream, dst, bytes)
    STREAM_TELL = 0x00399738,         // Stream::Tell(stream)
    VEC_NEW_ADDR = 0x003B1630,        // __builtin_vec_new, as the reader allocates the ids
    VALIDATE_CROSS_TRAFFIC = 0x00507A50,
    TEN = 0x41200000u, TWO_AND_HALF = 0x40200000u,
};

static __attribute__((noinline)) void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) { unsigned d = (v >> i) & 15u; put((char)(d < 10 ? '0' + d : 'A' + d - 10)); }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}

struct State { mc3_u32 short_records; };
enum { LOAD_RAIL_NETWORK_CALL = 0x005032C4, LOAD_RAIL_NETWORK = 0x00503798,
       RAIL_NETWORK = 0x0061B170 };
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }

extern "C" mc3_u32 control_read_hook(mc3_u32 control, mc3_u32 tagged) {
    const mc3_u32 stream = *(volatile mc3_u32 *)tagged;
    const mc3_u32 end = *(volatile mc3_u32 *)(tagged + 8u);
    MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, control, 2);
    const int count = *(volatile short *)control;
    const mc3_u32 ids = MC3_CALL1(mc3_u32, VEC_NEW_ADDR, mc3_u32)((mc3_u32)(2 * count));
    *(volatile mc3_u32 *)(control + 4u) = ids;
    MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, ids, 2 * count);
    const mc3_u32 at = MC3_CALL1(mc3_u32, STREAM_TELL, mc3_u32)(stream);
    if (end >= at + 8u) {
        MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, control + 20u, 4);
        MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, control + 24u, 4);
    } else {
        // MC2: no timings in the record - the values every retail file has
        *(volatile mc3_u32 *)(control + 20u) = TEN;
        *(volatile mc3_u32 *)(control + 24u) = TWO_AND_HALF;
        if (!st()->short_records++) mark('R','A','I','B', 1u, (mc3_u32)count);
    }
    *(volatile mc3_u32 *)(control + 28u) = *(volatile mc3_u32 *)(control + 20u);
    *(volatile mc3_u32 *)(control + 32u) = *(volatile mc3_u32 *)(control + 24u);
    MC3_CALL1(void, VALIDATE_CROSS_TRAFFIC, mc3_u32)(control);
    return 1u;
}
MC3_HOOK(CONTROL_READ_CALL, control_read_hook);

extern "C" mc3_u32 load_rail_network_hook(mc3_u32 net, mc3_u32 name) {
    st()->short_records = 0u;
    const mc3_u32 r = MC3_CALL2(mc3_u32, LOAD_RAIL_NETWORK, mc3_u32, mc3_u32)(net, name);
    if (st()->short_records) {
        const mc3_u32 rails = *(volatile mc3_u32 *)(net + 60u);
        const int n = *(volatile short *)(net + 56u);
        mc3_u32 opened = 0, pedestrians = 0;
        for (int i = 0; rails && i < n; ++i) {
            volatile mc3_u8 *f = (volatile mc3_u8 *)(rails + 20u * (mc3_u32)i + 18u);
            if (*f & 8u) {                               // pedestrian rail: no cars
                *f = (mc3_u8)(*f | 34u);                 // MC3 closed rail: bits 1 + 5
                ++pedestrians;
            } else if ((*f & 2u) && !(*f & 32u)) {
                *f = (mc3_u8)(*f & ~2u); ++opened;
            }
        }
        mark('R','A','I','F', opened | (pedestrians << 16), (mc3_u32)n);
    }
    return r;
}
MC3_HOOK(LOAD_RAIL_NETWORK_CALL, load_rail_network_hook);

// aiRouter next-hop table. The router (tag 0x4106, read by sub_504E48) ends
// with an n x n u16 table, by road: from road a to road b go to road
// table[a*n + b] (0xFFFF = none), walked by the route search 0x5059B0 - the
// battle modes' AI only; point-to-point races never call it. MC3 stores it
// as 0x4202 (u32 n) + 0x4204 (one block); MC2 as 0x4206 (u16 n) + n records
// 0x4207 (u16 n + one row), which MC3's loader skips: n (router+68) and the
// table (router+72) stayed 0 and the search read 2*road from address 0 - a
// TLB miss at 0x505A30, ignored by PCSX2's recompiler, fatal on a PS2.
// The table costs 2*n*n bytes (LA 211 KB, Paris 220 KB, Tokyo 331 KB) taken
// from the heap the props' physics load from next, so it is built only for
// a race type whose AI routes (BATTLE_TYPES); otherwise n stays 0 and the
// table is a 2 KB block from the same heap (the router frees it as its own)
// of NO_ROUTE_ROADS entries 0xFFFF: with n = 0 the index is road b, "no way".
// Both are made like the loader's own table (0x505018): the entries are an
// array of a class with a destructor, so the block is new[] with a 16-byte
// cookie - vec_new(16 + 2*count), count at +0, table = block + 16. The
// router's destructor (sub_5050C0, on every city unload: back to the menu,
// Next Race) reads the count at table-16 and frees table-16; a table without
// the cookie handed the allocator a pointer inside a block and it stopped in
// memMemoryAllocator::Free's `while (1)` (0x3B0558).
// The router loader's call (0x503DFC) is hooked to know the router, and its
// one TaggedStream::ReadTag call (0x505084) to read 0x4206/0x4207 here; the
// switch then takes them as unknown tags, as before. Marker
// RRTR <race type | 1 << 16 if built> <n | rows << 16>.
enum { ROUTER_LOAD_CALL = 0x00503DFC, ROUTER_LOAD = 0x00504E48,
       ROUTER_TAG_CALL = 0x00505084, READ_TAG = 0x005B86F8,
       RACE_CONFIG = 0x00619B10, RACE_TYPE = 0x18, NO_ROUTE_ROADS = 1024 };
// capture_the_flag 10, survival 12, tag 13, keepaway 14, bomb_tag 15,
// destroy 17, last_man_out 19, paint 20 (table 0x619C20)
static const mc3_u32 BATTLE_TYPES = (1u << 10) | (1u << 12) | (1u << 13) | (1u << 14) |
                                    (1u << 15) | (1u << 17) | (1u << 19) | (1u << 20);
struct Router { mc3_u32 router, table, n, rows; };
static Router g_router;
static __attribute__((noinline)) Router *rt() { return &g_router; }

// new[] of `count` u16 entries with the cookie the router's destructor expects.
static mc3_u32 new_table(mc3_u32 count) {
    const mc3_u32 block = MC3_CALL1(mc3_u32, VEC_NEW_ADDR, mc3_u32)(16u + 2u * count);
    if (!block) return 0u;
    *(volatile mc3_u32 *)block = count;
    return block + 16u;
}

static mc3_u32 race_type() {
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG;
    return (cfg >= 0x00100000u && cfg < 0x02000000u) ? *(volatile mc3_u32 *)(cfg + RACE_TYPE) : 0xFFu;
}

extern "C" int router_load_hook(mc3_u32 router, mc3_u32 tagged) {
    Router *r = rt();
    r->router = router; r->table = 0u; r->n = 0u; r->rows = 0u;
    const int ok = MC3_CALL2(int, ROUTER_LOAD, mc3_u32, mc3_u32)(router, tagged);
    if (!*(volatile mc3_u32 *)(router + 72u)) {          // no table in the file, or not built
        const mc3_u32 none = new_table(NO_ROUTE_ROADS);
        if (none) {
            for (int i = 0; i < NO_ROUTE_ROADS; ++i) ((volatile unsigned short *)none)[i] = 0xFFFFu;
            *(volatile mc3_u32 *)(router + 68u) = 0u;
            *(volatile mc3_u32 *)(router + 72u) = none;
        }
    }
    if (r->n) {
        const mc3_u32 type = race_type();
        mark('R','R','T','R', (type & 0xFFFFu) | (r->table ? 0x10000u : 0u), r->n | (r->rows << 16));
    }
    r->router = 0u;
    return ok;
}
MC3_HOOK(ROUTER_LOAD_CALL, router_load_hook);

extern "C" int router_tag_hook(mc3_u32 tagged, mc3_u32 tag_out, mc3_u32 len_out) {
    const int got = MC3_CALL3(int, READ_TAG, mc3_u32, mc3_u32, mc3_u32)(tagged, tag_out, len_out);
    Router *r = rt();
    if (!got || !r->router) return got;
    const mc3_u32 stream = *(volatile mc3_u32 *)tagged;
    const unsigned tag = *(volatile unsigned short *)tag_out;
    unsigned short n16 = 0;
    if (tag == 0x4206u) {
        MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, (mc3_u32)&n16, 2);
        r->n = n16;
        const mc3_u32 type = race_type();
        if (n16 && type < 32u && (BATTLE_TYPES >> type) & 1u) {
            r->table = new_table((mc3_u32)n16 * n16);
            if (r->table) {
                *(volatile mc3_u32 *)(r->router + 68u) = n16;
                *(volatile mc3_u32 *)(r->router + 72u) = r->table;
            }
        }
    } else if (tag == 0x4207u && r->table && r->rows < r->n) {
        MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)(stream, (mc3_u32)&n16, 2);
        if (n16 == r->n) {
            MC3_CALL3(int, STREAM_READ, mc3_u32, mc3_u32, int)
                (stream, r->table + 2u * r->n * r->rows, (int)(2u * r->n));
            ++r->rows;
        }
    }
    return got;
}
MC3_HOOK(ROUTER_TAG_CALL, router_tag_hook);

// The battle modes' route planner (sub_41B440, one 44-byte step per path
// entry) gives a step on a road of type 0 or 4 the roads before and after it,
// and asks sub_41B120 (road a, road b, out, max) for the nodes they share. A
// path that ENDS on such a road - with MC2's files, the CTF/Detonator AI on
// every map - has no road after it: b is null and sub_41B120 reads its node
// count at address 4. PCSX2's recompiler reads 0 there (count 0: no shared
// node, the AI drives on); a PS2 takes a TLB miss and stops. The three calls
// (0x41C148, 0x41C164, 0x41C23C) answer 0 for a null road, as the emulator did.
// Marker RNUL <calls with a null road> (first one only).
enum { SHARED_NODES = 0x0041B120 };
static mc3_u32 g_null_roads;
static __attribute__((noinline)) mc3_u32 *null_roads() { return &g_null_roads; }
extern "C" int shared_nodes_hook(mc3_u32 a, mc3_u32 b, mc3_u32 out, int max) {
    if (!a || !b) {
        if (!(*null_roads())++) mark('R','N','U','L', a, b);
        return 0;
    }
    return MC3_CALL4(int, SHARED_NODES, mc3_u32, mc3_u32, mc3_u32, int)(a, b, out, max);
}
MC3_HOOK(0x0041C148, shared_nodes_hook);
MC3_HOOK(0x0041C164, shared_nodes_hook);
MC3_HOOK(0x0041C23C, shared_nodes_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
