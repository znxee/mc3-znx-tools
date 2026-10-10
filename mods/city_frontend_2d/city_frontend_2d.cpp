// -----------------------------------------------------------------------------
//  city_frontend_2d - the front end of an added city drawn flat on the screen,
//  the way the pause menu is, instead of as a panel standing in the city.
//
//  How the front end draws (mcGame::Draw 0x1A2998, at 0x1A2DC4):
//    - while the front-end Flash is up (*(0x617980)+0x6B4 = mcFlash, +4 its
//      swf context, +9 set), the menus go through mcUIProjector
//      (s_Instance 0x615708): SetupScreen (0x203F08, jal 0x1A2DD4) locks a
//      512x512 render target ("UI Projector Screen"), mcFlash::Draw paints
//      the bg_*.swf backgrounds and prompts, mcMenuBrain::RenderMenus the
//      lists, and TakeDownScreen (0x203FC8, jal 0x1A2E2C) unlocks it and
//      draws it as ONE textured strip of 4 corners in WORLD space
//      (projector +8: Vector3[4] top-left, bottom-left, top-right,
//      bottom-right), seen through the game camera;
//    - otherwise (the pause menu in a race) RenderMenus draws straight onto
//      the screen viewport (0x1A2E58).
//  The corners come from mc3FeView per retail city and menu section
//  (sub_322260, `city < 4` only); an added city keeps the constructor's
//  default (690,6..11,479.7..487.9), and only a camera aimed at that spot
//  (fe_transition in the renderers) shows the menus at all.
//
//  Here, for an added city (race config city >= 4), right before
//  TakeDownScreen the four corners are moved to a rectangle standing in front
//  of the camera that fills the screen: with the camera matrix C (0x6BE7E0,
//  rows right/up/back/position - what UnlockRenderTarget 0x2BA130 restores)
//  and the projection of the viewport it restores (0x6BE818: +16 m00, +36
//  m11, GL style, the camera looks down -z; +316 near), the point at NDC
//  (u, v) and depth d is pos + right*u*d/m00 + up*v*d/m11 - back*d. Same
//  render target, same draw, so the Flash backgrounds stay - only the place
//  changes, and any camera, any city, shows the same flat menu. The corners
//  are put back after the draw (a later retail city sets its own anyway).
//
//  The city behind the menus is not drawn (mcPlayerManager::Draw, jal
//  0x1A2AF4, skipped while the projector is up in an added city): the menus
//  on their own, over the cleared screen.
//
//  [boot] fe2d = 0  the old panel in the city (this module does nothing)
//  [boot] fe2w = 1  keep drawing the city behind the flat menus
//  Marker (once per front-end visit): RF2D <city> <near * 1000>
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"

enum {
    RACE_CONFIG_CURRENT = 0x00619B10,
    FE_HOLDER = 0x00617980, FE_FLASH = 0x6B4,
    PROJECTOR = 0x00615708, PROJ_CORNERS = 8,
    TAKE_DOWN_CALL = 0x001A2E2C, TAKE_DOWN = 0x00203FC8,
    PLAYERS_DRAW_CALL = 0x001A2AF4, PLAYERS_DRAW = 0x0051A4D0,
    GFX_CLEAR = 0x0052A400, GAME_CAMERA = 0x006BE7E0, SAVED_VIEWPORT = 0x006BE818,
    VP_M00 = 16, VP_M11 = 36, VP_NEAR = 316, VP_FAR = 320,
    FIRST_ADDED_CITY = 4, MAX_CITY = 16,
};

static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }
static int ok_ptr(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && !(p & 3u); }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) {
        const mc3_u32 d = (v >> i) & 15u;
        put((char)(d < 10u ? '0' + d : 'A' + d - 10u));
    }
}
static __attribute__((noinline)) mc3_u32 *marked() {
    static mc3_u32 v;
    return &v;
}
static void mark(mc3_u32 city, mc3_u32 value) {
    mc3_u32 *m = marked();
    if (*m == city + 1u) return;
    *m = city + 1u;
    put('R'); put('F'); put('2'); put('D'); put(' '); hex8(city); put(' '); hex8(value); put('\n');
}

static int bootarg_is(mc3_u32 id, char c) {
    const char *v = mc3_bootarg(id);
    return v && v[0] == c;
}

// The added city behind the front end, or -1.
static int added_city() {
    const mc3_u32 cfg = word(RACE_CONFIG_CURRENT);
    if (!ok_ptr(cfg)) return -1;
    const mc3_u32 city = word(cfg);
    return city >= (mc3_u32)FIRST_ADDED_CITY && city < (mc3_u32)MAX_CITY ? (int)city : -1;
}

// The test mcGame::Draw makes at 0x1A2DC4: the menus go through the projector.
static int projector_up() {
    const mc3_u32 holder = word(FE_HOLDER);
    if (!ok_ptr(holder)) return 0;
    const mc3_u32 flash = word(holder + FE_FLASH);
    if (!ok_ptr(flash) || !word(flash + 4u)) return 0;
    return *(volatile mc3_u8 *)(flash + 9u) != 0u && ok_ptr(word(PROJECTOR));
}

static int flat_wanted() {
    if (bootarg_is(MC3_ID('f','e','2','d'), '0')) return 0;
    return added_city() >= 0 && projector_up();
}

// gfxPipeline::Clear(int what, u32 color, float z, u32 stencil) - the game's
// convention puts z in $f12 and stencil in $a2, which an n32 call does not:
// called the way mcUIProjector::SetupScreen does (0x203F9C): colour + depth,
// black, z 1.0. Without it nothing clears the frame once the city is not
// drawn and the old frames pile up under the menus.
static void clear_screen() {
    asm volatile(
        "li    $4, 3\n"
        "lui   $5, 0x8000\n"
        "move  $6, $0\n"
        "lui   $2, 0x3F80\n"
        "mtc1  $2, $f12\n"
        "lui   $25, %%hi(%0)\n"
        "addiu $25, $25, %%lo(%0)\n"
        "jalr  $25\n"
        "nop\n"
        :: "i"(GFX_CLEAR)
        : "$2", "$3", "$4", "$5", "$6", "$7", "$8", "$9", "$10", "$11", "$12", "$13",
          "$14", "$15", "$24", "$25", "$31", "hi", "lo", "memory",
          "$f0", "$f1", "$f2", "$f3", "$f4", "$f5", "$f6", "$f7", "$f8", "$f9", "$f10",
          "$f11", "$f12", "$f13", "$f14", "$f15", "$f16", "$f17", "$f18", "$f19");
}

extern "C" void players_draw_hook(mc3_u32 manager) {
    if (flat_wanted() && !bootarg_is(MC3_ID('f','e','2','w'), '1')) { clear_screen(); return; }
    MC3_CALL1(void, PLAYERS_DRAW, mc3_u32)(manager);
}
MC3_HOOK(PLAYERS_DRAW_CALL, players_draw_hook);

extern "C" void take_down_hook(mc3_u32 projector) {
    const mc3_u32 vp = word(SAVED_VIEWPORT);
    if (!flat_wanted() || !ok_ptr(vp) || !ok_ptr(projector)) {
        if (added_city() < 0) *marked() = 0u;
        MC3_CALL1(void, TAKE_DOWN, mc3_u32)(projector);
        return;
    }
    float *corners = (float *)word(projector + PROJ_CORNERS);
    const float m00 = *(volatile float *)(vp + VP_M00);
    const float m11 = *(volatile float *)(vp + VP_M11);
    const float near = *(volatile float *)(vp + VP_NEAR);
    const float far = *(volatile float *)(vp + VP_FAR);
    if (!ok_ptr((mc3_u32)corners) || !(m00 > 0.0f) || !(m11 > 0.0f) || !(near > 0.0f)) {
        MC3_CALL1(void, TAKE_DOWN, mc3_u32)(projector);
        return;
    }
    float d = near * 2.0f;                        // just past the near plane
    if (far > near && d > far) d = (near + far) * 0.5f;
    const volatile float *c = (const volatile float *)GAME_CAMERA;
    float saved[12];
    for (int i = 0; i < 12; ++i) saved[i] = corners[i];
    // top-left, bottom-left, top-right, bottom-right, as the retail corners
    static const signed char uv[4][2] = { { -1, 1 }, { -1, -1 }, { 1, 1 }, { 1, -1 } };
    const float sx = d / m00, sy = d / m11;
    for (int k = 0; k < 4; ++k) {
        const float x = uv[k][0] * sx, y = uv[k][1] * sy;
        for (int j = 0; j < 3; ++j)
            corners[3 * k + j] = c[9 + j] + c[j] * x + c[3 + j] * y - c[6 + j] * d;
    }
    mark((mc3_u32)added_city(), (mc3_u32)(near * 1000.0f));
    MC3_CALL1(void, TAKE_DOWN, mc3_u32)(projector);
    for (int i = 0; i < 12; ++i) corners[i] = saved[i];
}
MC3_HOOK(TAKE_DOWN_CALL, take_down_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
