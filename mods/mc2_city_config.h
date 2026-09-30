#ifndef MC2_CITY_CONFIG_H
#define MC2_CITY_CONFIG_H

// Runtime configuration for the two MC2 cities registered in MC3.
// Keep every string behind its own noinline accessor: the .mod packer rejects
// code where one LUI is shared by relocations for unrelated data accesses.

enum {
    MC2_CITY_LOS_ANGELES = 5,
    MC2_CITY_PARIS = 6,
};

static int mc2_city_supported(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES || city == MC2_CITY_PARIS;
}

static char mc2_city_instance_prefix(mc3_u32 city)
{
    return city == MC2_CITY_PARIS ? 'p' : 'l';
}

static __attribute__((noinline)) const char *mc2_la_root()
{
    static const char s[] = "host0:/mc2/losangeles/";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_root()
{
    static const char s[] = "host0:/mc2/paris/";
    return s;
}
static const char *mc2_city_root(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_root()
         : city == MC2_CITY_PARIS ? mc2_paris_root() : 0;
}

static __attribute__((noinline)) const char *mc2_la_rsc()
{
    static const char s[] = "host0:/mc2/losangeles/losangeles.rsc";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_rsc()
{
    static const char s[] = "host0:/mc2/paris/paris.rsc";
    return s;
}
static const char *mc2_city_rsc(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_rsc()
         : city == MC2_CITY_PARIS ? mc2_paris_rsc() : 0;
}

static __attribute__((noinline)) const char *mc2_la_rnt()
{
    static const char s[] = "host0:/mc2/losangeles/losangeles.rnt";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_rnt()
{
    static const char s[] = "host0:/mc2/paris/paris.rnt";
    return s;
}
static const char *mc2_city_rnt(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_rnt()
         : city == MC2_CITY_PARIS ? mc2_paris_rnt() : 0;
}

static __attribute__((noinline)) const char *mc2_la_city_dir()
{
    static const char s[] = "host0:/mc2/losangeles/city/";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_city_dir()
{
    static const char s[] = "host0:/mc2/paris/city/";
    return s;
}
static const char *mc2_city_dir(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_city_dir()
         : city == MC2_CITY_PARIS ? mc2_paris_city_dir() : 0;
}

static __attribute__((noinline)) const char *mc2_la_lvl()
{
    static const char s[] = "losangeles.lvl";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_lvl()
{
    static const char s[] = "paris.lvl";
    return s;
}
static const char *mc2_city_lvl(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_lvl()
         : city == MC2_CITY_PARIS ? mc2_paris_lvl() : 0;
}

static __attribute__((noinline)) const char *mc2_la_prop()
{
    static const char s[] = "losangeles.prop";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_prop()
{
    static const char s[] = "paris.prop";
    return s;
}
static const char *mc2_city_prop(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_prop()
         : city == MC2_CITY_PARIS ? mc2_paris_prop() : 0;
}

static __attribute__((noinline)) const char *mc2_la_solid_type()
{
    static const char s[] = "l_inst_freeway_support_01x";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_solid_type()
{
    // Paris has no instance named freeway_support. These fourteen explicit
    // catacomb columns are its only pillar-shaped structural instance type.
    static const char s[] = "p_inst_cat_colmn_x";
    return s;
}
static const char *mc2_city_solid_type(mc3_u32 city)
{
    return city == MC2_CITY_LOS_ANGELES ? mc2_la_solid_type()
         : city == MC2_CITY_PARIS ? mc2_paris_solid_type() : 0;
}

// The player's pins (city_mc2_rsc_draw_test): one file per city, so LA's and
// Paris's coordinates never mix. LA's is the file the first pins were written
// to (host0:/mc2_pins.txt, kept as is).
static __attribute__((noinline)) const char *mc2_la_pins()
{
    static const char s[] = "host0:/mc2_pins.txt";
    return s;
}
static __attribute__((noinline)) const char *mc2_paris_pins()
{
    static const char s[] = "host0:/mc2_pins_paris.txt";
    return s;
}
static const char *mc2_city_pins(mc3_u32 city)
{
    return city == MC2_CITY_PARIS ? mc2_paris_pins() : mc2_la_pins();
}

// The MC2 props the pins are drawn as (category 1 texture, 2 collision,
// 3 other), by name in the city's own .prop: LA's cone / stop sign / freeway
// barrel; Paris has no such names, its equivalents are the construction cone,
// the street sign and a bench (the construction barrier is in paris.prop but
// not in the ambients files, so it never loads).
static __attribute__((noinline)) const char *mc2_la_pin_cone()   { static const char s[] = "l_prop_cone_01x"; return s; }
static __attribute__((noinline)) const char *mc2_la_pin_sign()   { static const char s[] = "l_prop_sign_stop_01x"; return s; }
static __attribute__((noinline)) const char *mc2_la_pin_barrel() { static const char s[] = "l_prop_fwy_barrel_01x"; return s; }
static __attribute__((noinline)) const char *mc2_paris_pin_cone()   { static const char s[] = "p_prop_construction_cone_01x"; return s; }
static __attribute__((noinline)) const char *mc2_paris_pin_sign()   { static const char s[] = "p_prop_streetsign_01x"; return s; }
static __attribute__((noinline)) const char *mc2_paris_pin_barrel() { static const char s[] = "p_prop_bench_01x"; return s; }
static const char *mc2_city_pin_prop(mc3_u32 city, mc3_u32 category)
{
    if (city == MC2_CITY_PARIS)
        return category == 1u ? mc2_paris_pin_cone() : category == 2u ? mc2_paris_pin_sign() : mc2_paris_pin_barrel();
    return category == 1u ? mc2_la_pin_cone() : category == 2u ? mc2_la_pin_sign() : mc2_la_pin_barrel();
}

// The world rectangle the city's MC2 HUD map (assets/texture/hud_map_*.tex) was
// painted for, fitted against the city's own road network with
// mc2_map_fit.py - NOT the extents of <city>.lvl, which MC3 would use (the
// picture then sat tens to hundreds of metres off the streets). Bit patterns
// of {min x, max x, min z, max z}; losangeles_minimap puts them in the map's
// extents entry. 0 = no fit: keep what the game computes.
static mc3_u32 mc2_city_map_extent(mc3_u32 city, int k)
{
    static const mc3_u32 la[4] = { 0xC4EC0000u, 0x44C00000u, 0xC4F24000u, 0x44BE0000u };       // x -1888..1536, z -1938..1520
    static const mc3_u32 paris[4] = { 0xC4C7C000u, 0x44B8C000u, 0xC4CA8000u, 0x44BE2000u };    // x -1598..1478, z -1620..1521
    return city == MC2_CITY_LOS_ANGELES ? la[k] : city == MC2_CITY_PARIS ? paris[k] : 0u;
}

static mc3_u32 mc2_city_router_min_y(mc3_u32 city)
{
    if (city == MC2_CITY_LOS_ANGELES) return 0xC1827435u; // -16.306742
    if (city == MC2_CITY_PARIS) return 0xC18A6982u;       // -17.301518
    return 0u;
}

#endif
