// Digital km/h and RPM overlay for Midnight Club 3: Dub Edition Remix
// (SLUS-21355).
//
// It replaces the three stock gauge displays at run time and draws one compact
// digital cluster.  hudGear::Display is replaced as a whole: Proper Widescreen
// is then free to rewrite its two internal sprite calls without racing this mod.

#include "../../payload/mc3_mod.h"

struct vec3 {
    float x;
    float y;
    float z;
};

enum {
    MCPLAYER_GET_PLAYER       = 0x00514530,
    MCRACER_OBSTACLE_VELOCITY = 0x0051B850,
    TXT_GET_FONT              = 0x002BB880,
    TXT_FONT_DRAW             = 0x002BC8F0,
    TXT_FONT_SET_BILINEAR     = 0x002BDB70,
    GET_FONTS_DIRECTORY      = 0x004B72C8,
    FLUSH_CACHE               = 0x00546C20,
    HUD_GEAR_POSITION         = 0x002728D0,
    OLD_SPEEDOMETER_DISPLAY   = 0x00294838,
    OLD_TACHOMETER_DISPLAY    = 0x00298E78,
    OLD_GEAR_DISPLAY          = 0x0027A080,
    VEH_ENGINE_GET_RPM        = 0x00262948,
    ASSET_MANAGER             = 0x006D557C,
    GFX_LOAD_IMAGE            = 0x00526538,   // gfxLoadImageAll -> gfxImage*
    GFX_SIMPLETEX_CREATE      = 0x00522650,   // gfxSimpleTex::Create(gfxImage*)
    VGL_BIND_TEXTURE          = 0x005304E0,   // vglBindTexture(gfxTexture*)
    RMC_BLIT_RECT             = 0x002AE6C0,   // see blit_rect's bridge
};

// txtFontTex::Draw uses the game's hand-written mixed integer/FPU calling
// convention. A normal C++ function pointer puts the four floats in f17-f20;
// the game reads them from f12-f15. Keep this tiny bridge explicit so position
// and scale cannot be corrupted by the compiler's n32 argument allocation.
struct txt_cursor {
    mc3_u32 flags;
    int cursor_x;
    int cursor_y;
    int clip_top;
    int clip_right;
    int clip_left;
    int clip_bottom;
    int first_character;
    int last_character;
    int line_height;
    int measured_width;
    int measured_height;
    float scale_x;
    float scale_y;
    mc3_u32 colour;
};

struct text_layout {
    mc3_u32 x_bits;
    mc3_u32 y_bits;
    mc3_u32 scale_x_bits;
    mc3_u32 scale_y_bits;
};

extern "C" void draw_font_text(mc3_u32 font, const mc3_u16 *text,
                                txt_cursor *cursor,
                                const text_layout *layout);
__asm__(
    ".set noreorder\n"
    ".text\n"
    ".align 3\n"
    ".globl draw_font_text\n"
    "draw_font_text:\n"
    "  addiu $sp, $sp, -16\n"
    "  sd    $ra, 0($sp)\n"
    "  lwc1  $f12, 0($a3)\n"
    "  lwc1  $f13, 4($a3)\n"
    "  lwc1  $f14, 8($a3)\n"
    "  lwc1  $f15, 12($a3)\n"
    "  move  $a3, $zero\n"   // immediate draw
    // The module is assembled with N32 register names, while the game labels
    // physical register 8 as t0. Spell the number explicitly: `$t0` here would
    // mean physical register 12 and leave the draw-buffer argument as garbage.
    "  move  $8, $zero\n"    // txtDrawBuffer * (physical argument register 8)
    "  lui   $t9, 0x002b\n"
    "  ori   $t9, $t9, 0xc8f0\n"
    "  jalr  $t9\n"
    "  nop\n"
    "  ld    $ra, 0($sp)\n"
    "  jr    $ra\n"
    "  addiu $sp, $sp, 16\n"
    ".set reorder\n");

struct digital_speedometer_state {
    mc3_u32 magic;
    mc3_u32 hook_calls;
    mc3_u32 font_attempts;
    mc3_u32 draw_calls;
    mc3_u32 digital_font;
    mc3_u32 old_speedometer_disabled;
    mc3_u32 old_tachometer_redirected;
    mc3_u32 old_gear_redirected;
    mc3_u32 last_kilometres_per_hour;
    mc3_u32 last_digits;
    mc3_u32 tachometer_calls;
    mc3_u32 tachometer_draw_calls;
    mc3_u32 last_rpm;
    mc3_u32 last_gear;
    mc3_u32 badge_tex;
    mc3_u32 badge_image;
    mc3_u32 badge_attempts;
    mc3_u32 badge_draws;
    char badge_name_buf[24];
    char font_name_buf[16];
    txt_cursor cursor;
};

static digital_speedometer_state g_state;

// Keep every access behind one non-inlined address calculation. The current
// v2 packer can relocate one HI16/LO16 pair, but GCC may otherwise reuse that
// HI16 for several LO16 accesses and leave later accesses pointing near zero.
static __attribute__((noinline)) digital_speedometer_state *state()
{
    return &g_state;
}

typedef void (*asset_push_fn)(mc3_u32, const char *);
typedef void (*asset_pop_fn)(mc3_u32);

static int valid_game_pointer(mc3_u32 address)
{
    return address >= 0x00100000u && address < 0x02000000u;
}

static float square_root(float value)
{
    float result;
    __asm__ volatile ("sqrt.s %0, %1" : "=f"(result) : "f"(value));
    return result;
}

static void format_speed(unsigned int speed, mc3_u16 *out)
{
    if (speed > 999)
        speed = 999;

    // Volatile keeps GCC from replacing these tiny bounded loops with its
    // 64-bit reciprocal-multiply division sequence. On the R5900 that sequence
    // produced invalid UTF-16 as soon as the tens digit became non-zero.
    volatile unsigned int remaining = speed;
    unsigned int hundreds = 0;
    unsigned int tens = 0;
    while (remaining >= 100) {
        remaining -= 100;
        ++hundreds;
    }
    while (remaining >= 10) {
        remaining -= 10;
        ++tens;
    }
    out[0] = (mc3_u16)('0' + hundreds);
    out[1] = (mc3_u16)('0' + tens);
    out[2] = (mc3_u16)('0' + remaining);
}

static int format_rpm(unsigned int rpm, mc3_u16 *out)
{
    if (rpm > 99999)
        rpm = 99999;

    // Keep the same division-free conversion used by the speed readout. RPM is
    // variable-width so cars show e.g. `850 rpm`, while high-revving bikes can
    // still show five digits without being clamped at 9999.
    volatile unsigned int remaining = rpm;
    unsigned int ten_thousands = 0;
    unsigned int thousands = 0;
    unsigned int hundreds = 0;
    unsigned int tens = 0;
    while (remaining >= 10000) {
        remaining -= 10000;
        ++ten_thousands;
    }
    while (remaining >= 1000) {
        remaining -= 1000;
        ++thousands;
    }
    while (remaining >= 100) {
        remaining -= 100;
        ++hundreds;
    }
    while (remaining >= 10) {
        remaining -= 10;
        ++tens;
    }

    int length = 0;
    if (ten_thousands)
        out[length++] = (mc3_u16)('0' + ten_thousands);
    if (ten_thousands || thousands)
        out[length++] = (mc3_u16)('0' + thousands);
    if (ten_thousands || thousands || hundreds)
        out[length++] = (mc3_u16)('0' + hundreds);
    if (ten_thousands || thousands || hundreds || tens)
        out[length++] = (mc3_u16)('0' + tens);
    out[length++] = (mc3_u16)('0' + remaining);
    return length;
}

extern "C" void display_digital_tachometer(mc3_u32 tachometer);
extern "C" void display_digital_cluster(mc3_u32 gear_display);

static void replace_analog_gauges()
{
    digital_speedometer_state *s = state();
    if (s->old_speedometer_disabled && s->old_tachometer_redirected &&
        s->old_gear_redirected)
        return;

    if (!s->old_speedometer_disabled) {
        // `jr ra; nop`: turn hudSpeedometer::Display into an empty function.
        volatile mc3_u32 *speed_code =
            (volatile mc3_u32 *)OLD_SPEEDOMETER_DISPLAY;
        speed_code[0] = 0x03E00008u;
        speed_code[1] = 0x00000000u;
        s->old_speedometer_disabled = 1;
    }

    if (!s->old_tachometer_redirected) {
        // Preserve the caller's return address and jump directly from
        // hudTachometer::Display to our replacement. The replacement receives
        // the original tachometer `this` pointer in a0, which gives us the exact
        // vehEngine used by the stock needle without drawing any analog pieces.
        const mc3_u32 replacement = (mc3_u32)&display_digital_tachometer;
        volatile mc3_u32 *tach_code =
            (volatile mc3_u32 *)OLD_TACHOMETER_DISPLAY;
        tach_code[0] = 0x08000000u |
                       ((replacement >> 2) & 0x03FFFFFFu); // j replacement
        tach_code[1] = 0x00000000u;
        s->old_tachometer_redirected = 1;
    }

    if (!s->old_gear_redirected) {
        // Same shape as the tachometer redirect above, on hudGear::Display, so
        // display_digital_cluster receives the gear display's own `this` and
        // owns the whole gauge area from the next HUD frame.
        const mc3_u32 replacement = (mc3_u32)&display_digital_cluster;
        volatile mc3_u32 *gear_code = (volatile mc3_u32 *)OLD_GEAR_DISPLAY;
        gear_code[0] = 0x08000000u |
                       ((replacement >> 2) & 0x03FFFFFFu); // j replacement
        gear_code[1] = 0x00000000u;
        s->old_gear_redirected = 1;
    }

    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
}

// "spaceout-bold", built the same way and for the same reason as badge_name:
// with the string as a literal, g_state and it are TWO data bases and GCC
// shares one `lui` between them. That built fine while the module was smaller
// and stopped as soon as the plate code was added - measured, at +E4.
static __attribute__((noinline)) const char *font_name(void)
{
    digital_speedometer_state *s = state();
    if (!s->font_name_buf[0]) {
        s->font_name_buf[0] = 115;
        s->font_name_buf[1] = 112;
        s->font_name_buf[2] = 97;
        s->font_name_buf[3] = 99;
        s->font_name_buf[4] = 101;
        s->font_name_buf[5] = 111;
        s->font_name_buf[6] = 117;
        s->font_name_buf[7] = 116;
        s->font_name_buf[8] = 45;
        s->font_name_buf[9] = 98;
        s->font_name_buf[10] = 111;
        s->font_name_buf[11] = 108;
        s->font_name_buf[12] = 100;
        s->font_name_buf[13] = 0;
    }
    return s->font_name_buf;
}

static mc3_u32 load_digital_font()
{
    const mc3_u32 assets = *(volatile mc3_u32 *)ASSET_MANAGER;
    if (!valid_game_pointer(assets))
        return 0;

    const mc3_u32 vtable = *(volatile mc3_u32 *)assets;
    if (!valid_game_pointer(vtable))
        return 0;

    const mc3_u32 push_address = *(volatile mc3_u32 *)(vtable + 0x10);
    const mc3_u32 pop_address = *(volatile mc3_u32 *)(vtable + 0x14);
    if (!valid_game_pointer(push_address) || !valid_game_pointer(pop_address))
        return 0;

    // txtGetFontTex resolves names relative to the current asset directory.
    // Every stock HUD element temporarily enters `fonts` before requesting its
    // font; deferred loading means the asset manager is fully ready here.
    const char *directory = MC3_CALL(const char *, GET_FONTS_DIRECTORY)();
    ((asset_push_fn)push_address)(assets, directory);
    const mc3_u32 font = MC3_CALL2(mc3_u32, TXT_GET_FONT,
                                   const char *, int)(font_name(), 0);
    if (font)
        MC3_CALL2(void, TXT_FONT_SET_BILINEAR, mc3_u32, int)(font, 0);
    ((asset_pop_fn)pop_address)(assets);
    return font;
}

static void initialise_overlay()
{
    digital_speedometer_state *s = state();
    if (s->magic != 0x4B504853u) {
        // A null cursor makes txtFontTex::Draw allocate temporary state every
        // frame. Keep one cursor for the life of the module instead. A cursor
        // whose last_character is zero draws only the first glyph.
        s->cursor.flags = 1;
        s->cursor.first_character = 0;
        s->cursor.last_character = 0x7FFFFFFF;
        s->cursor.scale_x = 1.0f;
        s->cursor.scale_y = 1.0f;
        s->cursor.colour = 0x80FFFFFFu;
        s->magic = 0x4B504853u; // 'SHPK': visible in savestates.
    }
}

// "hud_badge" - the BARE name, one character code at a time, INSIDE g_state.
//
// Neither a folder nor an extension belongs here, and both were tried and
// measured before this was understood. gfxLoadTexImage 0x00524928 does not use
// the flat Open: it calls vtable slot +48, datAssetManagerHierNew::Open(this,
// FOLDER, name, ext, 1, 1), and supplies the folder and the extension itself
// from two globals. Read live out of a savestate, they hold:
//
//     off_61C288 = "texture"      off_61C290 = "tex"
//
// So it already asks for `texture/<name>.tex`. Passing "texture/hud_badge.tex"
// made it look for texture/texture/hud_badge.tex, and the `$/` attempt after
// that made it worse, not better - the savestate showed badge_tex 0 both times.
//
// The buffer lives in g_state and the characters go in one assignment each:
// a `static` local is a second data base and a `const unsigned char t[]` lands
// in .rodata, and either one trips mc3_mkmod's "LO16 sem HI16" - measured, at
// +AC and +E4.
static __attribute__((noinline)) const char *badge_name(void)
{
    digital_speedometer_state *s = state();
    if (!s->badge_name_buf[0]) {
        s->badge_name_buf[0] = 104;
        s->badge_name_buf[1] = 117;
        s->badge_name_buf[2] = 100;
        s->badge_name_buf[3] = 95;
        s->badge_name_buf[4] = 98;
        s->badge_name_buf[5] = 97;
        s->badge_name_buf[6] = 100;
        s->badge_name_buf[7] = 103;
        s->badge_name_buf[8] = 101;
        s->badge_name_buf[9] = 0;
    }
    return s->badge_name_buf;
}

// ---------------------------------------------------------------------------
//  The backing plate
//
//  rmcBlitRect 0x002AE6C0 draws a textured quad, and it is __usercall: eight
//  integers in $a0-$a7, the z as a float in $f12, and the colour and shader on
//  the stack. A normal C call puts them somewhere else entirely, so the bridge
//  below pins every one of them by hand - the same reason draw_font_text
//  exists.
//
//  ARGUMENT ORDER AND UV UNITS, both read off real callers rather than guessed:
//
//      mcCityEnvMap::DrawSky        rmcBlitRect(0, 0, w, h, 0,0,8,8, 0.0f, c)
//      sub_2F45A0                   rmcBlitRect(0, 0, 256, 256,
//                                               v0, v2, v0+v3, v2+v1, 0.0f, c)
//
//  In the second the u/v pair is the scissor rectangle it had just set, so the
//  UVs are in TEXELS, not normalised. Neither caller passes a shader: both wrap
//  the blit in a bind/unbind instead - sub_2F45A0 sits between
//  mcFbGlow::BindBackBufferAsTexture and its Unbind - so the quad samples
//  whatever is currently bound. That is the pattern followed here, with
//  vglBindTexture 0x005304E0 doing the binding.
//
//  The plate is 256x128 and its art is the 3:1 band from y 21 to 105; the rest
//  of the canvas is transparent padding to reach a power of two. The UVs below
//  select exactly that band.
// ---------------------------------------------------------------------------

struct blit_args {
    // CORNERS, not width and height. Measured the hard way: passing
    // (372, 366, 176, 58) drew a plate about 196x308 on screen, which is
    // |176-372| by |58-366| - an inverted rectangle. The UV pair in sub_2F45A0
    // says the same thing in the source, `v0, v2, v0+v3, v2+v1`, which is
    // (u0, v0, u0+w, v0+h).
    int x0, y0, x1, y1;
    int u0, v0, u1, v1;
    mc3_u32 z_bits;         // a float, as bits: the module has no libm
    mc3_u32 colour;
    mc3_u32 shader;         // 0 - see above, the texture comes from the bind
};

extern "C" void blit_rect(const blit_args *a);
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".text\n"
    ".align 3\n"
    ".globl blit_rect\n"
    "blit_rect:\n"
    "  addiu $sp, $sp, -32\n"
    "  sd    $ra, 16($sp)\n"
    // Args nine and ten go at the bottom of the frame: n32 has no shadow area,
    // so the callee reads them from 0($sp) and 4($sp).
    "  lw    $12, 36($a0)\n"
    "  lw    $13, 40($a0)\n"
    "  sw    $12, 0($sp)\n"
    "  sw    $13, 4($sp)\n"
    "  lwc1  $f12, 32($a0)\n"
    // $8-$11 are a4-a7 by number. Spelling them $t0-$t3 would pick physical
    // 12-15 under n32 and quietly pass garbage.
    "  lw    $8,  16($a0)\n"
    "  lw    $9,  20($a0)\n"
    "  lw    $10, 24($a0)\n"
    "  lw    $11, 28($a0)\n"
    "  lw    $5,  4($a0)\n"
    "  lw    $6,  8($a0)\n"
    "  lw    $7,  12($a0)\n"
    // $a0 last: it is still holding the struct every load above reads from.
    "  lw    $4,  0($a0)\n"
    "  lui   $t9, 0x002a\n"
    "  ori   $t9, $t9, 0xe6c0\n"
    "  jalr  $t9\n"
    "  nop\n"
    "  ld    $ra, 16($sp)\n"
    "  jr    $ra\n"
    "  addiu $sp, $sp, 32\n"
    ".set at\n"
    ".set reorder\n");

// The plate on screen, in the game's 640x448 virtual canvas. Move these two and
// the text follows: the layouts further down are computed from them.
enum {
    BADGE_X = 372, BADGE_Y = 366,
    BADGE_W = 176, BADGE_H = 58,        // the art's own 3:1

    // The art band inside the 256x128 texture, in texels.
    BADGE_U0 = 0, BADGE_V0 = 21,
    BADGE_U1 = 256, BADGE_V1 = 106,
};

static void draw_badge(void)
{
    digital_speedometer_state *s = state();

    if (!s->badge_tex) {
        if (s->badge_attempts > 8)
            return;                     // asked enough times; stop trying
        ++s->badge_attempts;
        // gfxLoadImageAll switches on the suffix itself, so naming the .tex
        // outright takes the branch that reads one.
        // gfxLoadImageAll hands back a gfxImage, NOT a gfxTexture - the two
        // are different objects and vglBindTexture wants the second. The bridge
        // between them is gfxSimpleTex::Create, which is a one-line forwarder
        // to gfxTexture::Create(image, false).
        const mc3_u32 image = MC3_CALL2(mc3_u32, GFX_LOAD_IMAGE,
                                        const char *, int)(badge_name(), 0);
        if (!valid_game_pointer(image))
            return;
        s->badge_image = image;
        s->badge_tex = MC3_CALL1(mc3_u32, GFX_SIMPLETEX_CREATE, mc3_u32)(image);
        if (!s->badge_tex)
            return;
    }
    if (!valid_game_pointer(s->badge_tex))
        return;

    MC3_CALL1(void, VGL_BIND_TEXTURE, mc3_u32)(s->badge_tex);

    blit_args a;
    a.x0 = BADGE_X;             a.y0 = BADGE_Y;
    a.x1 = BADGE_X + BADGE_W;   a.y1 = BADGE_Y + BADGE_H;
    a.u0 = BADGE_U0; a.v0 = BADGE_V0; a.u1 = BADGE_U1; a.v1 = BADGE_V1;
    a.z_bits = 0u;                      // 0.0f
    a.colour = 0x80FFFFFFu;             // the plate's own colours, unmodulated
    a.shader = 0u;
    blit_rect(&a);
    ++s->badge_draws;
}

static void draw_text(const mc3_u16 *text, const text_layout *layout)
{
    digital_speedometer_state *s = state();
    s->cursor.cursor_x = 0;
    s->cursor.cursor_y = 0;
    s->cursor.first_character = 0;
    s->cursor.last_character = 0x7FFFFFFF;
    draw_font_text(s->digital_font, text, &s->cursor, layout);
}

static mc3_u32 player_for_gear_display(mc3_u32 gear_display)
{
    int player_index = 0;
    if (valid_game_pointer(gear_display))
        player_index = *(volatile int *)(gear_display + 0x0C);
    return MC3_CALL1(mc3_u32, MCPLAYER_GET_PLAYER, int)(player_index);
}

static unsigned int current_gear(mc3_u32 player)
{
    // This is the same pointer chain used by retail hudGear::Display before it
    // selects one of the nine atlas cells: R, N, then forward gears 1..7.
    if (!valid_game_pointer(player))
        return 0;
    mc3_u32 node = *(volatile mc3_u32 *)(player + 0x14);
    if (!valid_game_pointer(node))
        return 0;
    node = *(volatile mc3_u32 *)(node + 0x04);
    if (!valid_game_pointer(node))
        return 0;
    node = *(volatile mc3_u32 *)(node + 0x14);
    if (!valid_game_pointer(node))
        return 0;
    const unsigned int gear = *(volatile mc3_u32 *)(node + 0x14);
    return gear <= 8 ? gear : 0;
}

extern "C" void display_digital_cluster(mc3_u32 gear_display)
{
    digital_speedometer_state *s = state();
    initialise_overlay();
    ++s->hook_calls;

    if (valid_game_pointer(gear_display) &&
        !*(volatile mc3_u8 *)(gear_display + 0x28))
        return;

    const mc3_u32 player = player_for_gear_display(gear_display);
    if (!valid_game_pointer(player))
        return;

    // mcRacer::ObstacleVelocity returns the live physics velocity vector. The
    // game's world units are metres per second, hence the 3.6 conversion.
    const vec3 *velocity = (const vec3 *)MC3_CALL1(mc3_u32,
                                                     MCRACER_OBSTACLE_VELOCITY,
                                                     mc3_u32)(player);
    if (!valid_game_pointer((mc3_u32)velocity))
        return;

    const float metres_per_second = square_root(
        velocity->x * velocity->x +
        velocity->y * velocity->y +
        velocity->z * velocity->z);
    const unsigned int kilometres_per_hour =
        (unsigned int)(metres_per_second * 3.6f + 0.5f);

    mc3_u16 speed_text[9];
    format_speed(kilometres_per_hour, speed_text);
    speed_text[3] = ' ';
    speed_text[4] = 'k';
    speed_text[5] = 'm';
    speed_text[6] = '/';
    speed_text[7] = 'h';
    speed_text[8] = 0;
    s->last_kilometres_per_hour = kilometres_per_hour;
    s->last_digits = (mc3_u32)speed_text[0] |
                     ((mc3_u32)speed_text[1] << 8) |
                     ((mc3_u32)speed_text[2] << 16);

    const unsigned int gear = current_gear(player);
    s->last_gear = gear;
    mc3_u16 gear_text[2];
    gear_text[0] = gear == 0 ? 'R' :
                   (gear == 1 ? 'N' : '0' + (gear - 1));
    gear_text[1] = 0;

    mc3_u16 rpm_text[10];
    int rpm_length = format_rpm(s->last_rpm, rpm_text);
    rpm_text[rpm_length++] = ' ';
    rpm_text[rpm_length++] = 'r';
    rpm_text[rpm_length++] = 'p';
    rpm_text[rpm_length++] = 'm';
    rpm_text[rpm_length] = 0;

    // If the HUD assets are not ready on the very first deferred frame, retry
    // gently instead of acquiring the font every frame and exhausting refs.
    if (!s->digital_font && (s->hook_calls == 1 || !(s->hook_calls % 30))) {
        ++s->font_attempts;
        s->digital_font = load_digital_font();
    }
    if (!s->digital_font)
        return;

    // Layout derived from the backing plate, not eyeballed, so the two stay
    // consistent if either moves. The plate is `ASSETS/texture/hud_badge.tex`,
    // 256x128, built by `python mc3_hud_badge.py` and converted with
    // `python mc3_tex.py make`. Its art is 3:1 inside the power-of-two canvas:
    // the gear pill spans texels x 0..92 and the two right pills x 96..251.
    //
    // On screen, in the game's 640x448 virtual canvas:
    //
    //     plate      x 372..548   y 366..424   (176x58, the art's own 3:1)
    //     gear pill  x 372..435   centre 403
    //     right pill x 438..544
    //
    // Nudge BADGE_X/BADGE_Y and everything below follows.
    text_layout gear_layout;
    gear_layout.x_bits = 0x43C5D000u;       // 395.6f  - centred in the left pill
    gear_layout.y_bits = 0x43BC47AEu;       // 376.6f
    gear_layout.scale_x_bits = 0x3F666666u; // 0.90f
    gear_layout.scale_y_bits = 0x3F666666u;
    text_layout rpm_layout;
    rpm_layout.x_bits = 0x43DE0000u;        // 444.0f  - inside the upper right pill
    rpm_layout.y_bits = 0x43B9EEEFu;        // 371.9f
    rpm_layout.scale_x_bits = 0x3EC28F5Cu;  // 0.38f
    rpm_layout.scale_y_bits = 0x3EC28F5Cu;
    text_layout speed_layout;
    speed_layout.x_bits = 0x43DE0000u;      // 444.0f  - inside the lower right pill
    speed_layout.y_bits = 0x43C640DAu;      // 396.5f
    speed_layout.scale_x_bits = 0x3EE147AEu; // 0.44f
    speed_layout.scale_y_bits = 0x3EE147AEu;
    draw_badge();                       // behind the numbers, so it goes first
    draw_text(gear_text, &gear_layout);
    draw_text(rpm_text, &rpm_layout);
    draw_text(speed_text, &speed_layout);
    ++s->draw_calls;
    ++s->tachometer_draw_calls;
}

extern "C" void display_digital_tachometer(mc3_u32 tachometer)
{
    digital_speedometer_state *s = state();
    ++s->tachometer_calls;

    if (!valid_game_pointer(tachometer))
        return;

    // The first stock instruction checks this same active byte. Mirroring it
    // keeps RPM hidden in menus and in every mode where the original gauge was
    // not supposed to be visible.
    if (!*(volatile mc3_u8 *)(tachometer + 0x28))
        return;

    const mc3_u32 engine = *(volatile mc3_u32 *)(tachometer + 0x43C);
    if (!valid_game_pointer(engine))
        return;

    const float rpm_float =
        MC3_CALL1(float, VEH_ENGINE_GET_RPM, mc3_u32)(engine);
    unsigned int rpm = 0;
    if (rpm_float > 0.0f) {
        rpm = rpm_float >= 99999.0f
                  ? 99999u
                  : (unsigned int)(rpm_float + 0.5f);
    }
    s->last_rpm = rpm;
}

// Bootstrap from a stable call that every widescreen profile leaves alone.
// The first invocation installs the three display redirects; from the next HUD
// frame onward display_digital_cluster owns the complete gauge area.
extern "C" void position_gear_then_speed(
    int x, int y, int *position, int *size)
{
    MC3_CALL4(void, HUD_GEAR_POSITION, int, int, int *, int *)
        (x, y, position, size);
    replace_analog_gauges();
    display_digital_cluster(0);
}

MC3_HOOK(0x0027A3DC, position_gear_then_speed);
