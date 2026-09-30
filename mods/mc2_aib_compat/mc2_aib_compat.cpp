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

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
