// -----------------------------------------------------------------------------
//  losangeles_minimap - the HUD map (minimap and pause map) for the added cities.
//
//  Two things left it black for city 5 (losangeles) and up, both measured in the
//  ELF:
//
//  1. The map image comes from the streamed hudmap build
//     ($/resources/city/hudmap.pck/.ppf): hudStreamedTextures table +8 count,
//     +12 array of rmcTexturePS2*, indexed by CITY (+3 for the "locked"
//     variant): 0 sd, 1 atlanta, 2 detroit, 3 tokyo, 4 atlanta_locked,
//     5 detroit_locked, then race maps. City 5 got detroit_locked. The four
//     calls of that lookup (sub_28D378: hudMap::DrawMap 0x27D4AC,
//     SetCurrentCity 0x282460, the pause map 0x289C9C/0x289CC8) are hooked:
//     for an added city they return mcTextureFactory's texture
//     "hud_map_<city name>" instead - the same factory call hudMap::Init uses
//     for its loose icons (icon_garage...), an rmcTexturePS2 that loads
//     ASSETS/texture/hud_map_<name>.tex (+ _proxy). For Los Angeles that file is
//     MC2's own LA map, assets/texture/hud_map_la.tex, copied unchanged by the
//     installer (512x256, 4 bpp - the MC2 map already covers the same extents
//     as losangeles.lvl, north up).
//  2. The map's world->texture scale comes from a per-city table of 32 bytes
//     (hudMap::SharedDataAllTypes, 0x6BDE20) that sub_285438 fills for cities
//     0..3 only; city 5 read past its end. Its eleven readers all go through
//     sub_285508(table, city) = table + 32 * city; those calls are hooked and
//     answer an entry of our own for city >= 4, filled by the game's own
//     sub_285540 (from mcCity::GetCityExtendsForMap, which reads the city's
//     extents for any city past 2).
//
//  Markers: RMMP <city> <texture>  (first time a city's map is asked for)
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_registry.h"
#include "../../payload/mc3_heap.h"
#include "../mc2_city_config.h"

enum {
    MAP_LOOKUP = 0x0028D378,          // (streamed, index, locked) -> rmcTexture*
    MAP_ENTRY = 0x00285508,           // (table, city) -> table + 32 * city
    MAP_ENTRY_FILL = 0x00285540,      // (entry, city): extents -> entry
    RACE_CONFIG_CURRENT = 0x00619B10, // mcRaceConfig*; +0 = city index
    CITY_TABLE = 0x00619D4C,          // &record[0]; 76 bytes each, +0 name
    CITY_RECORD = 76,
    TEXTURE_FACTORY = 0x00618954,     // mcTextureFactory::sm_mcInstance; vtable +12 Create(name)
    FIRST_ADDED_CITY = 5,
    RETAIL_ENTRIES = 4,
    MAX_CITY = 8,
};

static __attribute__((noinline)) void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) { unsigned d = (v >> i) & 15u; put((char)(d < 10 ? '0' + d : 'A' + d - 10)); }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }

struct State {
    mc3_u32 texture[MAX_CITY];
    float entry[MAX_CITY][8];
};
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }
static __attribute__((noinline)) const char *prefix() { static const char s[] = "hud_map_"; return s; }

static __attribute__((noinline)) float one_f() { union { mc3_u32 u; float f; } v; v.u = 0x3F800000u; return v.f; }
static mc3_u32 current_city() {
    const mc3_u32 cfg = word(RACE_CONFIG_CURRENT);
    return mc3_ptr_ok(cfg) ? word(cfg) : 0xFFFFFFFFu;
}

// The textures this module made (rmcTexturePS2, 0x10140 bytes with the image
// for a 512x256 4-bit map) were kept for the whole session. Each is created the
// first time the HUD asks, in the middle of a race, when the main heap's cursor
// is high - and stayed there: after Los Angeles, Paris and Tokyo MC2 three of
// them pinned the cursor so high that the front end's 1.1 MB LoadHeap no longer
// fit under the top ("not enough main heap memory for memtop heap", then a
// null heap and the menu never came back, 2026-10-04). They are destroyed
// (virtual dtor, slot +8, flag 3 - the dtor also takes them out of the texture
// factory's name table) when the race is left: release_textures() is exported
// in mc3boot's registry as 'MMRL' and the renderer's front-end hook calls it,
// and the same happens when a retail city asks (it used to forget them).
// Marker RMMR <textures destroyed>.
enum { TEXTURE_VTABLE = 0x00627280 };
extern "C" void release_textures(void) {
    State *s = st();
    mc3_u32 n = 0;
    for (mc3_u32 i = 0; i < MAX_CITY; ++i) {
        const mc3_u32 t = s->texture[i];
        s->texture[i] = 0u;
        if (!mc3_ptr_ok(t) || word(t) != (mc3_u32)TEXTURE_VTABLE) continue;
        MC3_CALL2(void, word(TEXTURE_VTABLE + 8u), mc3_u32, int)(t, 3);
        ++n;
    }
    if (n) mark('R','M','M','R', n, 0u);
}

// "hud_map_<name>" through mcTextureFactory, once per city
static mc3_u32 city_map(mc3_u32 city) {
    State *s = st();
    if (s->texture[city]) return s->texture[city];
    const mc3_u32 table = word(CITY_TABLE);
    const mc3_u32 factory = word(TEXTURE_FACTORY);
    if (!mc3_ptr_ok(table) || !mc3_ptr_ok(factory)) return 0u;
    const char *name = (const char *)word(table + city * CITY_RECORD);
    if (!mc3_ptr_ok((mc3_u32)name)) return 0u;
    char full[48];
    int n = 0;
    for (const char *p = prefix(); *p; ++p) full[n++] = *p;
    while (*name && n < 47) full[n++] = *name++;
    full[n] = 0;
    const mc3_u32 create = word(word(factory) + 12u);
    s->texture[city] = MC3_CALL2(mc3_u32, create, mc3_u32, const char *)(factory, full);
    mark('R','M','M','P', city, s->texture[city]);
    return s->texture[city];
}

extern "C" mc3_u32 map_lookup_hook(mc3_u32 streamed, mc3_u32 index, mc3_u32 locked) {
    // exported from here: a shim module's mod_main never runs
    mc3_export(MC3_ID('M','M','R','L'), (void *)&release_textures);
    const mc3_u32 city = current_city();
    if (city >= FIRST_ADDED_CITY && city < MAX_CITY) {
        const mc3_u32 t = city_map(city);
        if (t) return t;
    } else {
        // a retail city: ours are not needed any more
        release_textures();
    }
    return MC3_CALL3(mc3_u32, MAP_LOOKUP, mc3_u32, mc3_u32, mc3_u32)(streamed, index, locked);
}
MC3_HOOK(0x0027D4AC, map_lookup_hook);    // hudMap::DrawMap
MC3_HOOK(0x00282460, map_lookup_hook);    // hudMap::SetCurrentCity
MC3_HOOK(0x00289C9C, map_lookup_hook);    // pause-menu map
MC3_HOOK(0x00289CC8, map_lookup_hook);

extern "C" mc3_u32 map_entry_hook(mc3_u32 table, mc3_u32 city) {
    if (city >= RETAIL_ENTRIES && city < MAX_CITY) {
        float *e = st()->entry[city];
        MC3_CALL2(void, MAP_ENTRY_FILL, mc3_u32, mc3_u32)((mc3_u32)e, city);
        if (mc2_city_map_extent(city, 1)) {
            // the rectangle the MC2 picture was painted for, not the .lvl's
            mc3_u32 *w = (mc3_u32 *)e;
            w[0] = mc2_city_map_extent(city, 0); w[1] = mc2_city_map_extent(city, 1);
            w[4] = mc2_city_map_extent(city, 2); w[5] = mc2_city_map_extent(city, 3);
            const float one = one_f();
            e[2] = e[1] - e[0]; e[6] = e[5] - e[4];
            e[3] = one / e[2]; e[7] = one / e[6];
        }
        return (mc3_u32)e;
    }
    return table + 32u * city;
}
MC3_HOOK(0x0027D4FC, map_entry_hook);
MC3_HOOK(0x0027E67C, map_entry_hook);
MC3_HOOK(0x0027EB90, map_entry_hook);
MC3_HOOK(0x0027EFD4, map_entry_hook);
MC3_HOOK(0x00282504, map_entry_hook);
MC3_HOOK(0x00283248, map_entry_hook);
MC3_HOOK(0x002836B0, map_entry_hook);
MC3_HOOK(0x00283B38, map_entry_hook);
MC3_HOOK(0x00284204, map_entry_hook);
MC3_HOOK(0x00286988, map_entry_hook);
MC3_HOOK(0x00286C28, map_entry_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { mc3_export(MC3_ID('M','M','R','L'), (void *)&release_textures); }
