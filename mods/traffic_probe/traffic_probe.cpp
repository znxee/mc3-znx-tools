// traffic_probe (diagnostic, not shipped): counts the steps of ambient traffic
// placement - aiAmbientTrafficData::ErrorPropagateBasedVehicleSlotting picks
// a vehicle type for a feed road (sub_21B030), tries the road
// (bPlaceVehicleInRoad, sub_21B9D0), which tries a rail
// (bSlotVehicleAlongRail) and activates a car (ActivateVehicle).
// Marker every 128 type picks: RTR1 <type picks> <no type>,
// RTR2 <road tries> <road ok>, RTR3 <rail tries> <rail ok>, RTR4 <activations>
// <ok>. The first 128 successful rail slots also report
// RTR6 <rail index> <flags << 16 | width>.
#include "../../payload/mc3_mod.h"

static __attribute__((noinline)) void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) { unsigned d = (v >> i) & 15u; put((char)(d < 10 ? '0' + d : 'A' + d - 10)); }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}
struct State { mc3_u32 pick, nopick, road, roadok, rail, railok, act, actok, lasttype, detail; };
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }

extern "C" int pick_hook(mc3_u32 a, mc3_u32 road, mc3_u32 n) {
    State *s = st();
    const int r = MC3_CALL3(int, 0x0021B030, mc3_u32, mc3_u32, mc3_u32)(a, road, n);
    ++s->pick; if (r < 0) ++s->nopick;
    s->lasttype = (mc3_u32)r;
    if ((s->pick & 127u) == 1u) {
        mark('R','T','R','1', s->pick, s->nopick);
        mark('R','T','R','2', s->road, s->roadok);
        mark('R','T','R','3', s->rail, s->railok);
        mark('R','T','R','4', s->act, s->actok);
        mark('R','T','R','5', n, (mc3_u32)r);
    }
    return r;
}
MC3_HOOK(0x0021B89C, pick_hook);
MC3_HOOK(0x0021B8E4, pick_hook);

extern "C" int road_hook(mc3_u32 a, mc3_u32 type, mc3_u32 road, mc3_u32 flag) {
    State *s = st();
    const int r = MC3_CALL4(int, 0x0021B9D0, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(a, type, road, flag);
    ++s->road; if (r) ++s->roadok;
    return r;
}
MC3_HOOK(0x0021B8BC, road_hook);

extern "C" int rail_hook(mc3_u32 a, mc3_u32 rail, mc3_u32 at, mc3_u32 type, mc3_u32 flag) {
    State *s = st();
    const int r = MC3_CALL5(int, 0x0021BCD8, mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(a, rail, at, type, flag);
    ++s->rail; if (r) ++s->railok;
    if (r && s->detail++ < 128u) {
        const mc3_u32 net = *(volatile mc3_u32 *)0x0061B170u;
        const mc3_u32 base = net ? *(volatile mc3_u32 *)(net + 60u) : 0u;
        const mc3_u32 count = net ? *(volatile mc3_u16 *)(net + 56u) : 0u;
        const mc3_u32 index = rail < count ? rail : 0xFFFFFFFFu;
        const mc3_u32 record = index != 0xFFFFFFFFu ? base + index * 20u : 0u;
        const mc3_u32 width = record ? *(volatile mc3_u16 *)(record + 10u) : 0u;
        const mc3_u32 rail_flags = record ? *(volatile mc3_u8 *)(record + 18u) : 0u;
        mark('R','T','R','6', index, (rail_flags << 16) | width);
    }
    return r;
}
MC3_HOOK(0x0021BBC4, rail_hook);

extern "C" int act_hook(mc3_u32 a, mc3_u32 b, mc3_u32 type, mc3_u32 rail, mc3_u32 rnd) {
    State *s = st();
    const int r = MC3_CALL5(int, 0x0021C700, mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(a, b, type, rail, rnd);
    ++s->act; if (r >= 0) ++s->actok;
    return r;
}
MC3_HOOK(0x0021BC38, act_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
