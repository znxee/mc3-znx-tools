// modderG: read-only loader counter, drawn only in frontend state 7.
#include "../../payload/mc3_mod.h"
#include "status_count.h"

struct txt_cursor {
    mc3_u32 flags;
    int cursor_x, cursor_y, clip_top, clip_right, clip_left, clip_bottom;
    int first_character, last_character, line_height;
    int measured_width, measured_height;
    mc3_u32 scale_x_bits, scale_y_bits, colour;
};
struct text_layout { mc3_u32 x, y, sx, sy; };
struct status_state {
    mc3_u32 installed, calls, draws, loaded, font, missing_font;
};
static status_state g_state;
static __attribute__((noinline)) status_state *state() { return &g_state; }

static int valid(mc3_u32 p) {
    return p >= 0x00100000u && p < 0x02000000u && !(p & 3u);
}

// txtFontTex::Draw: mixed game ABI (floats in f12-f15, not N32 f17-f20).
// Both the cursor and UTF-16 buffer are call-local; draw-buffer arg is null,
// hence the game emits GS commands immediately and retains neither pointer.
extern "C" void status_draw_text(mc3_u32, const mc3_u16 *, txt_cursor *,
                                  const text_layout *);
__asm__(
    ".set noreorder\n"
    ".text\n"
    ".align 3\n"
    ".globl status_draw_text\n"
    "status_draw_text:\n"
    "addiu $sp,$sp,-16\n"
    "sd $ra,0($sp)\n"
    "lwc1 $f12,0($a3)\n"
    "lwc1 $f13,4($a3)\n"
    "lwc1 $f14,8($a3)\n"
    "lwc1 $f15,12($a3)\n"
    "move $a3,$zero\n"
    "move $8,$zero\n"
    "lui $t9,0x002b\n"
    "ori $t9,$t9,0xc8f0\n"
    "jalr $t9\n"
    "nop\n"
    "ld $ra,0($sp)\n"
    "jr $ra\n"
    "addiu $sp,$sp,16\n"
    ".set reorder\n");

extern "C" void status_render_frontend(mc3_u32 surface)
{
    // Finish compositing the menu's offscreen texture onto its animated 3D
    // panel first; drawing before this would skew/move our text with the menu.
    MC3_CALL1(void, 0x00203FC8u, mc3_u32)(surface);
    status_state *s = state();
    ++s->calls;
    const mc3_u32 manager = *(volatile mc3_u32 *)0x00619958u;
    if (!valid(manager) || *(volatile mc3_u32 *)(manager+4u) != 7u) return;

    // Lookup only: GetFont (2BB880) increments refcount / may allocate.
    // Borrow the resident frontend "smallspace" font from the hash table.
    // The global default says arial12, which is NOT resident in this frontend.
    // No cached pointer survives frontend teardown or an attract-mode reload.
    char name[11];
    name[0]='s'; name[1]='m'; name[2]='a'; name[3]='l'; name[4]='l';
    name[5]='s'; name[6]='p'; name[7]='a'; name[8]='c'; name[9]='e';
    name[10]=0;
    const mc3_u32 font = MC3_CALL2(mc3_u32, 0x0042DD60u,
                                  mc3_u32, const char *)(0x006BE848u, name);
    if (!valid(font)) { ++s->missing_font; return; }
    s->font = font;
    const volatile mc3_u32 *boot = (const volatile mc3_u32 *)0x0061CA60u;
    const volatile mc3_u32 *core = (const volatile mc3_u32 *)0x0061CAC0u;
    s->loaded = status_count(boot[0], boot[3], core[0], core[3]);

    mc3_u16 text[21];
    status_text(s->loaded, text);
    txt_cursor cursor;
    volatile mc3_u32 *words = (volatile mc3_u32 *)&cursor;
    for (unsigned i=0; i<sizeof(cursor)/4u; ++i) words[i]=0;
    cursor.last_character=0x7FFFFFFF;
    cursor.scale_x_bits=cursor.scale_y_bits=0x3F800000u; // 1
    cursor.colour=0x80FFFFFFu;
    // Native 640x448 canvas. Left aligned, small, below the main menu.
    text_layout layout;
    layout.x=0x41A00000u; // 20
    layout.y=0x43CB0000u; // 406
    layout.sx=layout.sy=0x3F000000u; // 0.5
    const mc3_u32 old_view=*(volatile mc3_u32 *)0x0070E50Cu;
    const mc3_u32 ui_view=*(volatile mc3_u32 *)0x0070E508u;
    if (!valid(old_view) || !valid(ui_view)) return;
    MC3_CALL1(void,0x00527B50u,mc3_u32)(ui_view);
    status_draw_text(font, text, &cursor, &layout);
    MC3_CALL1(void,0x00527B50u,mc3_u32)(old_view);
    ++s->draws;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    status_state *s=state();
    if (s->installed) return;
    volatile mc3_u32 *site=(volatile mc3_u32 *)0x001A2E2Cu;
    // This compositor call is inside state==7 and the frontend surface gate.
    // Both RenderMenus calls and all pause/gameplay menu paths stay untouched.
    if (site[0]!=0x0C080FF2u || site[1]!=0x8E445708u) return;
    site[0]=0x0C000000u | (((mc3_u32)&status_render_frontend >> 2)&0x03FFFFFFu);
    s->installed=1;
    MC3_CALL1(void,0x00546C20u,int)(0);
    MC3_CALL1(void,0x00546C20u,int)(2);
}
