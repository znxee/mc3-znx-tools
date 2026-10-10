// -----------------------------------------------------------------------------
//  mc2_loading.h - the loading screen stays up until the MC2 city is loaded,
//  with a status line saying what is being loaded.
//
//  Adapted from the MCLA PSP fork's psp_loading.h (2026-10-09) for the MC2
//  cities only (Fork City, 2026-10-10). The native flow (MC3 Remix, SLUS-21355):
//    - mcLoadingThread draws the loading SWF; EnterStateGame (0x1A5700)
//      calls StopLoading (jal 0x1A585C -> 0x1AAB08) and only then StartRace
//      (jal 0x1A58F0) - where this renderer used to load its city, with the
//      loading screen already gone;
//    - the animation is asked to end early by 0x1AAE28 (jal 0x1AA184 and
//      0x1BE490: thread+0x4004), which the loading thread's SWF update
//      consumes.
//  Here: enter_game_hook calls ml_begin for an MC2 destination; the two
//  requests are held back (ml_signal_hook) while `ready` is 0; the StopLoading
//  hook (in the renderer) loads the whole city with the screen up, publishing
//  the stage, then sets `ready`. The loading thread draws the status line
//  (ml_draw_hook, jal 0x1AAD5C after the SWF) with a font the front end
//  already cached (txtGetFontTex 0x2BB880 / release 0x2BB9C8): asking for a
//  new atlas here costs megabytes of the city's memory.
//  Only counters cross between the threads. [boot] mlgt = 0 turns the gate
//  off (load at StartRace as before); mltx = 0 hides only the text.
//  Markers MLBG <city> <font>, MLPH <stage> <serial>, MLRQ <serial> <thread>.
//  Needs: ptr, word, mark, boot_int, mc2_city_supported, mc3_heap_*.
// -----------------------------------------------------------------------------

enum { ML_NATIVE = 1, ML_PACKAGE, ML_TEXTURES, ML_PLACING, ML_COLOURS, ML_PROPS, ML_SKY,
       ML_READY };
struct MLoading {
    volatile mc3_u32 magic, city, serial, phase, current, total;
    volatile mc3_u32 ready, active, enabled, text, font, frames, draws, requests;
};
static MLoading g_mloading;
static __attribute__((noinline)) MLoading *ml() { return &g_mloading; }

static void ml_phase(mc3_u32 stage, mc3_u32 current = 0u, mc3_u32 total = 0u) {
    MLoading *l = ml();
    if (!l->active) return;
    l->current = current;
    l->total = total;
    if (l->phase != stage) {
        l->phase = stage;
        mark('M','L','P','H', stage, l->serial);
    }
}
static void ml_font_release() {
    MLoading *l = ml();
    const mc3_u32 font = l->font;
    l->font = 0u;
    if (ptr(font)) MC3_CALL1(void, 0x002BB9C8u, mc3_u32)(font);
}
static void ml_begin(mc3_u32 city) {
    MLoading *l = ml();
    ml_font_release();
    const mc3_u32 serial = l->serial + 1u;
    volatile mc3_u32 *w = (volatile mc3_u32 *)l;
    for (mc3_u32 i = 0; i < sizeof(MLoading) / 4u; ++i) w[i] = 0u;
    l->magic = 0x4D4C4447u;                                    // 'MLDG'
    l->city = city;
    l->serial = serial;
    int enabled = 1, text = 1;
    boot_int(MC3_ID('m','l','g','t'), &enabled);
    boot_int(MC3_ID('m','l','t','x'), &text);
    l->enabled = enabled != 0;
    l->text = text != 0;
    l->active = l->enabled;
    if (l->active) {
        // the front end's font list (0x61602C): take a reference to one that
        // is already resident, never load a new one
        const mc3_u32 head = word(0x0061602Cu), name = ptr(head) ? word(head + 368u) : 0u;
        if (ptr(name)) l->font = MC3_CALL2(mc3_u32, 0x002BB880u, const char *, int)((const char *)name, 0);
    }
    ml_phase(ML_NATIVE);
    mark('M','L','B','G', city, l->font);
}

// txtFontTex::Draw 0x2BC8F0: a0 font, a1 UCS-2 text, a2 cursor, a3 0, $8 0,
// f12..f15 x, y, scale x, scale y - a mixed convention n32 does not produce.
struct MLTextCursor { mc3_u32 flags; int x, y, top, right, left, bottom, first, last, height, width, measured;
    mc3_u32 sx, sy, color; };
extern "C" void ml_draw_font(mc3_u32, const mc3_u16 *, MLTextCursor *, const mc3_u32 *);
__asm__(
    ".set noreorder\n.text\n.align 3\n.globl ml_draw_font\nml_draw_font:\n"
    "addiu $sp,$sp,-16\nsd $ra,0($sp)\n"
    "lwc1 $f12,0($a3)\nlwc1 $f13,4($a3)\nlwc1 $f14,8($a3)\nlwc1 $f15,12($a3)\n"
    "move $a3,$zero\nmove $8,$zero\nlui $t9,0x002b\nori $t9,$t9,0xc8f0\n"
    "jalr $t9\nnop\nld $ra,0($sp)\njr $ra\naddiu $sp,$sp,16\n.set reorder\n");
static mc3_u32 ml_append(mc3_u16 *out, mc3_u32 at, const char *s) {
    while (*s && at < 110u) out[at++] = (mc3_u8)*s++;
    out[at] = 0u;
    return at;
}
static mc3_u32 ml_number(mc3_u16 *out, mc3_u32 at, mc3_u32 n) {
    char digits[10];
    mc3_u32 k = 0u;
    do { digits[k++] = (char)('0' + n % 10u); n /= 10u; } while (n && k < 10u);
    while (k && at < 110u) out[at++] = digits[--k];
    out[at] = 0u;
    return at;
}
static __attribute__((noinline)) const char *ml_label(mc3_u32 phase) {
    switch (phase) {
        case ML_PACKAGE: return "Reading map";
        case ML_TEXTURES: return "Preparing textures";
        case ML_PLACING: return "Placing buildings";
        case ML_COLOURS: return "Applying colours";
        case ML_PROPS: return "Loading objects";
        case ML_SKY: return "Loading sky";
        case ML_READY: return "Map ready";
        default: return "Loading race";
    }
}
static __attribute__((noinline)) const mc3_u32 *ml_layout() {
    // native font coordinates are 512x448; cursor.flags 1 centres on x
    static const mc3_u32 layout[4] = { 0x43800000u, 0x43CC0000u, 0x3F666666u, 0x3F666666u }; // 256,408,.9,.9
    return layout;
}

// The loading thread's SWF draw, then the status line.
extern "C" mc3_u32 ml_draw_hook(mc3_u32 screen) {
    const mc3_u32 r = MC3_CALL1(mc3_u32, 0x001AC2A0u, mc3_u32)(screen);
    MLoading *l = ml();
    if (!l->active) return r;
    ++l->frames;
    if (!ptr(l->font) || !l->text) return r;
    mc3_u16 line[112];
    mc3_u32 n = ml_append(line, 0u, ml_label(l->phase));
    if (l->total) {
        n = ml_append(line, n, "  "); n = ml_number(line, n, l->current);
        n = ml_append(line, n, " / "); n = ml_number(line, n, l->total);
        if (l->phase == ML_PACKAGE) n = ml_append(line, n, " KiB");
    } else if (l->phase == ML_PROPS && l->current) {
        n = ml_append(line, n, "  "); n = ml_number(line, n, l->current);
    } else if (l->phase != ML_READY) {
        n = ml_append(line, n, (l->frames / 15u) % 3u == 0u ? "." : ((l->frames / 15u) % 3u == 1u ? ".." : "..."));
    }
    MLTextCursor cursor;
    volatile mc3_u32 *cw = (volatile mc3_u32 *)&cursor;
    for (mc3_u32 i = 0; i < sizeof(cursor) / 4u; ++i) cw[i] = 0u;
    cursor.flags = 1u;
    cursor.last = 0x7FFFFFFF;
    cursor.sx = cursor.sy = 0x3F800000u;
    cursor.color = 0xC0FFFFFFu;
    ml_draw_font(l->font, line, &cursor, ml_layout());
    ++l->draws;
    return r;
}
MC3_HOOK(0x001AAD5Cu, ml_draw_hook);

// The early "end the animation" requests wait for the city.
extern "C" mc3_u32 ml_signal_hook(mc3_u32 thread) {
    MLoading *l = ml();
    if (l->enabled && l->active && !l->ready && mc2_city_supported(l->city)) {
        ++l->requests;
        mark('M','L','R','Q', l->serial, thread);
        return 0u;
    }
    return MC3_CALL1(mc3_u32, 0x001AAE28u, mc3_u32)(thread);
}
MC3_HOOK(0x001AA184u, ml_signal_hook);
MC3_HOOK(0x001BE490u, ml_signal_hook);
