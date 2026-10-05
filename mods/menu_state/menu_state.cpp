// -----------------------------------------------------------------------------
//  menu_state - what the front end shows, for a script reading the RAM (PINE).
//
//  The menus (main menu, Arcade, pause, Yes/No...) are mcUiListMenu objects -
//  several classes, with their own DoRendering, so hooking the drawing misses
//  most of them - driven by the menu shell. The shell knows which one has the
//  pad (mcMenuShell::ChangeState 0x339278 reads it the same way):
//    widgets = *(*(0x617ADC) + 12), +240 the active list menu; +268 the
//    message box (Yes/No...): while it shows (+68 bit 0, as AnyListsActive
//    0x33A710 tests) its list *(*(box + 96) + 8) is the one read instead -
//    the same path GetConfirmationSimpleChoice 0x33B560 takes
//    mcMenuShell (the `this` of its Update) +216 current MenuStates - one
//    number per screen
//  mcUiListMenu (GetRow 0x5879F8, AddItem 0x5882C8, SetCursorByUniqueId 0x5880E0):
//    +660 rows per page, +664 columns, +668 + 4*page row count
//    +680 columns, 12 bytes: +0 u16 text column, +4 max chars
//    +684 rows, 16 bytes: +8 items (8 bytes per column, +0 = UCS-2 text);
//         row r of page p at 16*(p*rows_per_page + r)
//    +704 page shown, +720 + 16*page cursor {row, +4 valid}
//  Every list class has mcUiListMenu::AfterLoadingTuningData (0x5879F0) in
//  vtable slot 13: that is how the active widget is checked before reading.
//  The screen titles ("ARCADE", "PAUSE") are Flash, not readable here: the
//  state number stands for the screen.
//
//  mcMenuShell::Update (0x33A838) is virtual - its one vtable word 0x6286A4
//  points to update_hook, which runs the original and refreshes the block
//  below, every frame the menus run (a `defer` mod_main runs once). Text is
//  ASCII (anything past 0x7E becomes '?'). The block is exported in
//  mc3boot's registry (header 0x61D050) as id 'MNUS'.
//
//  Block (little endian), version 2:
//    +0 'MNUS'  +4 version  +8 seq (bumped on every change)  +12 frames
//    +16 menu  +20 page  +24 cursor row  +28 rows  +32 shell state
//    +36 1 if the menu is the message box  +40 selected[48]
//    +88 rows text, MAX_ROWS x 32
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_registry.h"

enum {
    SHELL_HOLDER = 0x00617ADC, LIST_MARK = 0x005879F0,
    SHELL_UPDATE = 0x0033A838, SHELL_UPDATE_SLOT = 0x006286A4, FLUSH_CACHE = 0x00546C20,
    MAX_ROWS = 24, ROW_CHARS = 32, SELECTED = 48,
};

struct Block {
    mc3_u32 magic, version, seq, frames;
    mc3_u32 menu, page, row, rows, state, box;
    char selected[SELECTED];
    char row_text[MAX_ROWS][ROW_CHARS];
};
struct State { mc3_u32 exported; Block b; };
static State g_state;
static __attribute__((noinline)) State *st() { return &g_state; }

static int ok_ptr(mc3_u32 p) { return p >= 0x00100000u && p < 0x02000000u && !(p & 3u); }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }

// UCS-2 -> ASCII into dst[n]; returns 1 if dst changed.
static int copy_wide(char *dst, int n, mc3_u32 src) {
    int changed = 0, i = 0;
    if (src >= 0x00100000u && src < 0x02000000u && !(src & 1u)) {
        const volatile unsigned short *s = (const volatile unsigned short *)src;
        for (; i < n - 1 && s[i]; ++i) {
            const char c = (char)(s[i] >= 0x20u && s[i] < 0x7Fu ? s[i] : '?');
            if (dst[i] != c) { dst[i] = c; changed = 1; }
        }
    }
    for (; i < n; ++i) if (dst[i]) { dst[i] = 0; changed = 1; }
    return changed;
}
static int set_word(mc3_u32 *w, mc3_u32 v) { if (*w == v) return 0; *w = v; return 1; }

// Text of row r of page p, first text column (0 if none).
static mc3_u32 row_text(mc3_u32 m, mc3_u32 p, mc3_u32 r) {
    const mc3_u32 rows = word(m + 684u), cols = word(m + 680u);
    const mc3_u32 ncols = word(m + 664u), rpp = word(m + 660u);
    if (!ok_ptr(rows) || !ok_ptr(cols) || ncols > 8u || rpp > 256u) return 0u;
    const mc3_u32 items = word(rows + 16u * (p * rpp + r) + 8u);
    if (!ok_ptr(items)) return 0u;
    for (mc3_u32 c = 0; c < ncols; ++c)
        if (*(volatile unsigned short *)(cols + 12u * c))
            return word(items + 8u * c);
    return 0u;
}

static int is_list(mc3_u32 m) {
    return ok_ptr(m) && ok_ptr(word(m)) && word(word(m) + 52u) == (mc3_u32)LIST_MARK;
}

static void refresh(Block *b, mc3_u32 shell) {
    const mc3_u32 holder = word(SHELL_HOLDER);
    const mc3_u32 widgets = ok_ptr(holder) ? word(holder + 12u) : 0u;
    mc3_u32 m = ok_ptr(widgets) ? word(widgets + 240u) : 0u;
    mc3_u32 box = 0u;
    const mc3_u32 msg = ok_ptr(widgets) ? word(widgets + 268u) : 0u;
    if (ok_ptr(msg) && (word(msg + 68u) & 1u) && ok_ptr(word(msg + 96u))) {
        const mc3_u32 ml = word(word(msg + 96u) + 8u);
        if (is_list(ml)) { m = ml; box = 1u; }
    }
    if (m && !is_list(m))
        m = 0u;                                   // not a list menu
    int changed = set_word(&b->state, ok_ptr(shell) ? word(shell + 216u) : 0xFFFFFFFFu);
    mc3_u32 page = 0u, rows = 0u, cur = 0u;
    if (m) {
        page = word(m + 704u);
        if (page < 8u) {
            rows = word(m + 668u + 4u * page);
            cur = word(m + 720u + 16u * page);
        }
        if (page >= 8u || rows > 256u) { m = 0u; page = rows = cur = 0u; box = 0u; }
    }
    changed |= set_word(&b->menu, m) | set_word(&b->page, page) | set_word(&b->row, cur) |
               set_word(&b->rows, rows) | set_word(&b->box, box);
    changed |= copy_wide(b->selected, SELECTED, m && cur < rows ? row_text(m, page, cur) : 0u);
    for (mc3_u32 r = 0; r < (mc3_u32)MAX_ROWS; ++r)
        changed |= copy_wide(b->row_text[r], ROW_CHARS, m && r < rows ? row_text(m, page, r) : 0u);
    ++b->frames;
    if (changed) ++b->seq;
}

extern "C" void update_hook(mc3_u32 self) {
    MC3_CALL1(void, SHELL_UPDATE, mc3_u32)(self);
    refresh(&st()->b, self);
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    State *s = st();
    if (!s->exported) {
        s->b.magic = MC3_ID('M','N','U','S');
        s->b.version = 3u;
        if (mc3_export(MC3_ID('M','N','U','S'), &s->b)) s->exported = 1u;
    }
    volatile mc3_u32 *slot = (volatile mc3_u32 *)SHELL_UPDATE_SLOT;
    if (*slot == (mc3_u32)SHELL_UPDATE) {
        *slot = (mc3_u32)&update_hook;
        MC3_CALL1(void, FLUSH_CACHE, int)(0);
    }
}
