// -----------------------------------------------------------------------------
//  pck_tool - the frontend, replaced by a conversion tool that draws its own
//             screen. A tool running on top of the game.
//
//  WHAT IT IS
//
//  The game is a packetizer: reading a mesh in TEXT builds the same object graph
//  a .pck holds, at the same offsets (rmcModel::Create -> rmcModelGeom::Load).
//  This module turns that into something you can watch: it takes over the
//  top-level menu's rendering, lists the meshes it is going to convert, and
//  reports each one as it goes, on screen, with the heap figures next to it.
//
//  HOW THE TAKEOVER WORKS
//
//  mcUiListMenu::DoRendering (0x00587418) is what paints the menu, and it is
//  reached by VTABLE, not by a `jal` - so there is nothing for MC3_HOOK to
//  patch. Exactly two words in the image hold its address, 0x0062BD20 and
//  0x00635730, and the module points both at its own renderer and never calls
//  the original. That is the redirection: from the moment it takes, the menu
//  is gone and the screen belongs to the tool. Same technique menu_row used for
//  PopulateMenu, and the guard is the same: the slot must still hold the
//  expected address before it is touched.
//
//  Standing exactly where the menu drew is not a detail. txtFontTex::Draw needs
//  the 2D state the frontend has already set up by that point; drawing from a
//  loose per-frame callback would land somewhere with no viewport and no
//  guarantee of surviving to the flip.
//
//  WHY THE FRONTEND IS NOT UNLOADED HERE
//
//  It is tempting to free the frontend for its memory, and mesh_dump does
//  exactly that. But the frontend IS the screen: unload layer 8 and the menu
//  object goes, the renderer stops being called, and the tool goes blind at the
//  moment it most needs to report. So this module keeps the frontend resident
//  and carves its heap off the TOP of the main heap with mcHeap::CreateMemTopHeap
//  - which is the memory the frontend, allocating from the bottom, is not using.
//
//  If the carve fails, the tool says so ON SCREEN with the two numbers that
//  matter (asked, available) instead of dying quietly. Shrink HEAP_BYTES, or
//  convert fewer models per run, or accept the trade and use mesh_dump, which
//  gives up the screen to get the memory.
//
//  WHAT IT PRODUCES
//
//  The built graph, in a private contiguous heap, whose base and length go in
//  the report. `python mc3_pckbuild.py build <savestate>` cuts the range out and
//  writes the .pck: because the heap is contiguous and starts at a known
//  address, setting the file's baseVA to that address makes the loader's
//  `buffer - baseVA` delta cancel, so there is no relocation pass - the builder
//  is a 128-byte header and a copy.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"

#define MC3_LOG_OWNER 'PCKT'
#include "../../payload/mc3_log.h"

enum {
    // Text. The draw is reached through a hand-written bridge below: the game
    // uses a mixed integer/FPU convention that n32 argument allocation does not
    // reproduce. Addresses and technique from digital_speedometer.mod.
    TXT_GET_FONT        = 0x002BB880,   // (name, bool) -> txtFontTex*
    TXT_FONT_DRAW       = 0x002BC8F0,
    TXT_SET_BILINEAR    = 0x002BDB70,
    GET_FONTS_DIRECTORY = 0x004B72C8,
    ASSET_MANAGER       = 0x006D557C,

    // The two vtable words holding mcUiListMenu::DoRendering.
    DORENDERING         = 0x00587418,
    VT_SLOT_A           = 0x0062BD20,
    VT_SLOT_B           = 0x00635730,

    // Heap. Carved off the top, so it does not fight the frontend for space.
    HEAP_CREATE_MEMTOP  = 0x004BD650,   // (size, name) -> memMemoryAllocator*
    HEAP_PUSH           = 0x004BD5D8,
    HEAP_POP            = 0x004BD5F0,
    HEAP_ACTIVE         = 0x0070E500,
    ALLOC_BASE          = 0x04,
    ALLOC_TOP           = 0x08,
    ALLOC_SIZE          = 0x0C,

    RMC_MODEL_CREATE    = 0x002A8CA0,   // (name, flags) -> rmcModelGeom*

    // The raw-file readers that survived into retail. Names are the game's own.
    AM_OPEN_SLOT        = 36,           // datAssetManager::Open(this,name,ext,b,b)
    STREAM_CLOSE        = 0x00399748,
    RAW_OPEN            = 0x003984C0,   // _coreRawOpenFile(path, bool)
    GFX_LOAD_IMAGE      = 0x00526538,   // gfxLoadImageAll(name, bool)
    PH_BOUND_LOAD       = 0x0047E128,   // phBound::LoadFromFile(name)

    MAX_MODELS          = 10,   // 40 B header + 10*20 = 240, inside the 256 B window
    WAIT_FRAMES         = 180,          // let the asset manager settle
    HEAP_BYTES          = 0x180000,     // 1.5 MB - small enough to fit beside a
                                        // resident frontend; the screen says so
                                        // if it does not.
};

// Bisection switch.
//   3 = redirect the vtable and count the calls. Nothing else.
//       MEASURED: safe. TLB 0 across several attract cycles.
//   2 = also resolve the font, but do not draw.
//   1 = also draw. No heap, no rmcModel::Create.
//       MEASURED at the old level 1 (font + draw together): 50 TLB misses.
//   0 = the whole tool.
#define TOOL_DRY_RUN 0

#define TOOL_MAGIC  0x4D433354u         /* 'MC3T' */
// The report is 240 bytes of a 256-byte window; this is the word after it. The
// address is spelled out in the trampoline's asm as 0x0061CFF0, so the two must
// move together. It only lands here when this module owns the window - with
// city_slot6.mod or mc2_probe.mod loaded the fallback is used and the font
// never arrives, which shows up as renders > 0 and no text.
#define TOOL_FONT_SLOT 0x0061CFF0u
#define TOOL_ADDR   0x0061CF00u         /* MODREPORT in ../../mc3_inject.py */
#define FOREIGN(x)  ((x) != 0u && (x) != TOOL_MAGIC)

enum { ST_WAIT = 0, ST_READY = 1, ST_CONVERT = 2, ST_DONE = 3, ST_NO_HEAP = 4 };

// What to do with a file once it is known to resolve.
//
// K_PROBE is not a poor relation. datAssetManager::Open takes the extension as
// a parameter, so it reads EVERY raw type the game has - all ~50 - and says
// whether the name resolves and how big it is. The typed readers below only
// exist where a parser was measured and its signature confirmed; anything else
// gets the probe rather than a guessed call.
//
// And the probe runs FIRST, always, even for typed reads. It has to:
// rmcShaderTemplate::Load opens `while (1) ;` on failure, and it is not alone -
// most of this engine answers a missing file by hanging, not by returning. A
// name that does not resolve must never reach a parser.
enum { K_PROBE = 0, K_MODEL = 1, K_IMAGE = 2, K_BOUND = 3 };

// ---------------------------------------------------------------------------
//  the text bridge - verbatim from digital_speedometer, which is proven on screen
// ---------------------------------------------------------------------------

struct txt_cursor {
    mc3_u32 flags;
    int cursor_x, cursor_y;
    int clip_top, clip_right, clip_left, clip_bottom;
    int first_character, last_character;
    int line_height, measured_width, measured_height;
    float scale_x, scale_y;
    mc3_u32 colour;
};

struct text_layout {
    mc3_u32 x_bits, y_bits, scale_x_bits, scale_y_bits;
};

extern "C" void draw_font_text(mc3_u32 font, const mc3_u16 *text,
                               txt_cursor *cursor, const text_layout *layout);
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
    // N32 register names would make `$t0` physical 12; the draw-buffer argument
    // is physical 8, so it is spelled by number.
    "  move  $8, $zero\n"
    "  lui   $t9, 0x002b\n"
    "  ori   $t9, $t9, 0xc8f0\n"
    "  jalr  $t9\n"
    "  nop\n"
    "  ld    $ra, 0($sp)\n"
    "  jr    $ra\n"
    "  addiu $sp, $sp, 16\n"
    ".set reorder\n");

// ---------------------------------------------------------------------------
//  state
// ---------------------------------------------------------------------------

struct tool_model {
    mc3_u32 name;
    mc3_u32 obj;
    mc3_u32 groups;
    mc3_u32 matidx;
    mc3_u32 geom;
};

// Keep in sync with the reader in ../../mc3_inject.py and mc3_pckbuild.py.
struct tool_report {
    mc3_u32 magic;
    mc3_u32 ticks;
    mc3_u32 state;
    mc3_u32 free_before;
    mc3_u32 renders;        /* MC3H puts free_after here; the tool has no use
                               for it and counts frames it actually drew, which
                               is how a test proves the vtable redirect fired
                               without anyone looking at the screen. */
    mc3_u32 heap;
    mc3_u32 heap_base;
    mc3_u32 heap_len;
    mc3_u32 count;
    mc3_u32 running;
    tool_model m[MAX_MODELS];
};

struct tool_state {
    tool_report *r;
    tool_report fallback;
    txt_cursor cursor;
    mc3_u32 font;
    mc3_u32 taken;          /* vtable slots redirected */
    mc3_u32 next;           /* model index the converter is on */
    mc3_u32 asked;          /* HEAP_BYTES, for the on-screen failure line */
    mc3_u32 avail;
    mc3_u32 drawn;          /* one draw per frame, not one per widget */
    mc3_u16 line[96];
};

static tool_state g_state;

// One accessor for the one data base: loose symbols make GCC share a `lui`
// across several `%lo` and mc3_mkmod refuses the build with "LO16 sem HI16".
static __attribute__((noinline)) tool_state *st() { return &g_state; }

// Every string, in one struct, behind one accessor - same reason.
struct tool_strings {
    const char *font_name;
    const char *heap_name;
    const char *title;
    const char *waiting;
    const char *ready;
    const char *converting;
    const char *done;
    const char *noheap;
    const char *asked;
    const char *heapline;
    const char *failed;
    const char *log_taken;
    const char *log_heap;
    const char *log_model;
    const char *log_done;
    // Progress markers. PCSX2's HostFS logs EVERY open by name, so opening a
    // file that does not exist is a print statement that survives when PINE
    // will not answer: `open name host0:/.../PCKTOOL_x` lands in emulog.txt.
    const char *mk_alive;
    const char *mk_vtable;
    const char *mk_render;
    const char *mk_heap;
    const char *mk_convert;
    const char *mk_done;
    const char *notfound;
    const char *noparser;
    const char *bytes;
    // The extensions with a measured reader. Order matches ext_kind[] below.
    const char *k_mod;
    const char *k_mesh;
    const char *k_tex;
    const char *k_xtex;
    const char *k_tga;
    const char *k_bmp;
    const char *k_spr;
    const char *k_ipu;
    const char *k_bnd;
    const char *k_bbnd;
    const char *name[MAX_MODELS];
    const char *ext[MAX_MODELS];
};

// EDIT THIS AND REBUILD. Names go to the asset manager exactly as
// rmcModel::Create takes them, so the directory prefix is required:
// "model/checker.mod" loads, "checker" resolves to nothing.
// EDIT THIS AND REBUILD.
//
// `name` is what the asset manager is given, and it needs the directory the
// same way rmcModel::Create does: "model/checker.mod" resolves, "checker" does
// not. `ext` is the extension WITHOUT the dot, passed to datAssetManager::Open
// as its own parameter and used to pick the reader. Leave a row's name null to
// end the list.
static const tool_strings g_str = {
    "arial12",
    "pcktool",
    "PCK TOOL - the game as packetizer",
    "warming up, waiting for the frontend",
    "ready",
    "converting",
    "DONE - take a savestate now",
    "NO HEAP - not enough room at the top",
    "asked/available:",
    "heap:",
    "FAILED",
    "vtable taken",
    "heap carved",
    "item done",
    "all done",
    "host0:/PT1alive",
    "host0:/PT2vtable",
    "host0:/PT3render",
    "host0:/PT4heap",
    "host0:/PT5conv",
    "host0:/PT6done",
    "NOT FOUND",
    "opened, no parser",
    " B",
    "mod", "mesh", "tex", "xtex", "tga", "bmp", "spr", "ipu", "bnd", "bbnd",
    {
        "model/checker.mod",     /* one model: the heap then holds exactly
                                   one object graph and the cut is unambiguous */
        0, 0, 0,
    },
    {
        "mod",
    }
};

static __attribute__((noinline)) const tool_strings *str() { return &g_str; }

// ---------------------------------------------------------------------------
//  tiny text helpers - no libc in a module, and no printf on purpose
// ---------------------------------------------------------------------------

static int put(mc3_u16 *out, int at, const char *s)
{
    if (!s)
        return at;
    while (*s && at < 94)
        out[at++] = (mc3_u16)(unsigned char)*s++;
    return at;
}

static int put_u(mc3_u16 *out, int at, mc3_u32 v, int hex)
{
    char tmp[12];
    int n = 0;
    if (!v)
        tmp[n++] = '0';
    while (v && n < 11) {
        const mc3_u32 d = hex ? (v & 0xF) : (v % 10u);
        tmp[n++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
        v = hex ? (v >> 4) : (v / 10u);
    }
    while (n && at < 94)
        out[at++] = (mc3_u16)tmp[--n];
    return at;
}

static void line_at(float x, float y, const mc3_u16 *text)
{
    tool_state *const s = st();
    text_layout L;
    // The bit patterns of the floats: the bridge feeds them straight to f12-f15
    // and the module has no libm to build them any other way.
    union { float f; mc3_u32 u; } cx, cy;
    cx.f = x; cy.f = y;
    L.x_bits = cx.u;
    L.y_bits = cy.u;
    cx.f = 1.0f;
    L.scale_x_bits = cx.u;
    L.scale_y_bits = cx.u;
    s->cursor.cursor_x = 0;
    s->cursor.cursor_y = 0;
    s->cursor.first_character = 0;
    s->cursor.last_character = 0x7FFFFFFF;
    draw_font_text(s->font, text, &s->cursor, &L);
}

// ---------------------------------------------------------------------------
//  reading a raw file
// ---------------------------------------------------------------------------

static int same(const char *a, const char *b)
{
    if (!a || !b)
        return 0;
    while (*a && *b) {
        char x = *a++, y = *b++;
        if (x >= 'A' && x <= 'Z') x = (char)(x + 32);
        if (y >= 'A' && y <= 'Z') y = (char)(y + 32);
        if (x != y)
            return 0;
    }
    return *a == *b;
}

static int kind_for_ext(const char *e)
{
    const tool_strings *const S = str();
    if (same(e, S->k_mod) || same(e, S->k_mesh))
        return K_MODEL;
    // One reader covers six extensions: gfxLoadImageAll switches on the suffix
    // itself and falls through .tex .xtex .tga .ipu .bmp .spr. Texture is never
    // a .pck in this engine, so this is the ONLY way textures load.
    if (same(e, S->k_tex) || same(e, S->k_xtex) || same(e, S->k_tga) ||
        same(e, S->k_bmp) || same(e, S->k_spr) || same(e, S->k_ipu))
        return K_IMAGE;
    if (same(e, S->k_bnd) || same(e, S->k_bbnd))
        return K_BOUND;
    return K_PROBE;
}

// datAssetManager::Open through the vtable, not by absolute address: slot +36
// is the flat manager's Open, while slot +48 is the HierNew one with a THIRD
// string parameter. Calling the wrong one shifts every argument. This is the
// same indirect call rmcShaderTemplate::Load makes.
static mc3_u32 open_raw(const char *name, const char *ext)
{
    const mc3_u32 am = *(volatile mc3_u32 *)ASSET_MANAGER;
    if (am < 0x00100000u || am >= 0x02000000u)
        return 0;
    const mc3_u32 vt = *(volatile mc3_u32 *)am;
    if (vt < 0x00100000u || vt >= 0x02000000u)
        return 0;
    const mc3_u32 fn = *(volatile mc3_u32 *)(vt + AM_OPEN_SLOT);
    if (fn < 0x00100000u || fn >= 0x02000000u)
        return 0;
    typedef mc3_u32 (*open_fn)(mc3_u32, const char *, const char *, int, int);
    return ((open_fn)fn)(am, name, ext, 0, 1);
}

// ---------------------------------------------------------------------------
//  the screen
// ---------------------------------------------------------------------------

static void draw_ui()
{
    tool_state *const s = st();
    const tool_strings *const S = str();
    tool_report *const r = s->r;
    if (!s->font)
        return;

    float y = 60.0f;
    int n = put(s->line, 0, S->title);
    s->line[n] = 0;
    line_at(48.0f, y, s->line);
    y += 28.0f;

    const char *estado = S->waiting;
    if (r->state == ST_READY)        estado = S->ready;
    else if (r->state == ST_CONVERT) estado = S->converting;
    else if (r->state == ST_DONE)    estado = S->done;
    else if (r->state == ST_NO_HEAP) estado = S->noheap;
    n = put(s->line, 0, estado);
    s->line[n] = 0;
    line_at(48.0f, y, s->line);
    y += 24.0f;

    if (r->state == ST_NO_HEAP) {
        n = put(s->line, 0, S->asked);
        n = put_u(s->line, n, s->asked, 0);
        n = put(s->line, n, " / ");
        n = put_u(s->line, n, s->avail, 0);
        s->line[n] = 0;
        line_at(48.0f, y, s->line);
        return;
    }

    if (r->heap_base) {
        n = put(s->line, 0, S->heapline);
        n = put_u(s->line, n, r->heap_base, 1);
        n = put(s->line, n, " + ");
        n = put_u(s->line, n, r->heap_len, 0);
        s->line[n] = 0;
        line_at(48.0f, y, s->line);
    }
    y += 26.0f;

    // One line per model: index, name, and what came of it.
    for (int i = 0; i < MAX_MODELS; ++i) {
        const char *nm = S->name[i];
        if (!nm)
            break;
        n = put(s->line, 0, " [");
        n = put_u(s->line, n, (mc3_u32)i, 0);
        n = put(s->line, n, "] ");
        n = put(s->line, n, nm);
        if (S->ext[i]) {
            n = put(s->line, n, " .");
            n = put(s->line, n, S->ext[i]);
        }
        n = put(s->line, n, "  ");
        if ((mc3_u32)i < r->count) {
            // Three outcomes worth telling apart: the name did not resolve, it
            // resolved but no parser is wired for that extension, or it loaded
            // and here is the object.
            if (r->m[i].groups == 0xFFFFu)
                n = put(s->line, n, S->notfound);
            else if (r->m[i].obj)
                n = put_u(s->line, n, r->m[i].obj, 1);
            else if (r->m[i].matidx == (mc3_u32)K_PROBE)
                n = put(s->line, n, S->noparser);
            else
                n = put(s->line, n, S->failed);
        } else if ((mc3_u32)i == s->next && r->state == ST_CONVERT) {
            n = put(s->line, n, "...");
        }
        s->line[n] = 0;
        line_at(48.0f, y, s->line);
        y += 20.0f;
    }
}

// Announce a stage by trying to open a file named after it. The open fails -
// nothing by that name exists - but HostFS has already logged the attempt, and
// that line is readable from outside without PINE, a savestate or a screen.
static void mark(const char *what)
{
    // _coreRawOpenFile, NOT the asset manager: a marker must not depend on the
    // subsystem it is reporting on, and the first thing worth knowing is
    // whether the module runs at all - long before the asset manager is up. A
    // path starting `host0:` goes straight through, and PCSX2 logs the attempt
    // by name whether or not the file exists.
    MC3_CALL2(mc3_u32, RAW_OPEN, const char *, int)(what, 0);
}


static mc3_u32 heap_free(mc3_u32 alloc)
{
    if (alloc < 0x00100000u || alloc >= 0x02000000u)
        return 0u;
    // The same two words mcHeap::CreateMemTopHeap tests before it agrees to
    // carve: free = top - used.
    const mc3_u32 top = *(volatile mc3_u32 *)(alloc + ALLOC_TOP);
    const mc3_u32 used = *(volatile mc3_u32 *)(alloc + ALLOC_SIZE);
    return top > used ? top - used : 0u;
}

// HOW THE FONT IS OBTAINED, AND WHY NOT THE OBVIOUS WAY
//
// Not by asking. MEASURED, twice: txtGetFontTex faults here - with the asset
// manager's `fonts` push around it, and without - 50 TLB misses at 0x0042E760
// storing from 0x02000000 up, the top of EE RAM, and PCSX2 dies. The font is
// not resident under that name at this point and the lookup goes off to load
// it from a context that cannot. digital_speedometer gets away with asking
// because it asks from inside a HUD draw during gameplay; a frontend menu's
// render is not that context.
//
// So the tool never asks. It watches. mcUiHeading::DoRendering draws the title
// at the top of every frontend screen, every frame, and 0x00581728 is its call
// to txtFontTex::Draw - where a0 is a font the game has already proved good.
// The trampoline below writes that pointer down and tail-jumps to the real
// Draw: `jr`, not `jalr`, so Draw returns straight to the heading and every
// argument, the four floats included, passes through untouched. Costs the
// heading nothing and cannot fail.
extern "C" void capture_font();
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".text\n"
    ".align 3\n"
    ".globl capture_font\n"
    "capture_font:\n"
    "  lui   $at, 0x0061\n"
    "  ori   $at, $at, 0xcff0\n"   // TOOL_FONT_SLOT, absolute
    "  sw    $a0, 0($at)\n"
    "  lui   $t9, 0x002b\n"
    "  ori   $t9, $t9, 0xc8f0\n"   // txtFontTex::Draw
    "  jr    $t9\n"
    "  nop\n"
    ".set at\n"
    ".set reorder\n");

// What the vtable slots point at instead of mcUiListMenu::DoRendering. The
// original is deliberately NOT called: this IS the redirection.
//
// Slot 8 is uiWidget::DoRendering and two classes reach mcUiListMenu's
// implementation, so this runs once per list-menu widget on screen - several
// times in one frame. The text is the same every time and drawing it twice
// only doubles the alpha, so the first call in each frame wins and the rest
// return. Widgets with their own renderer are untouched: the heading and the
// background still draw, and the menu rows become the tool.
extern "C" void tool_render(mc3_u32 /*menu*/)
{
    tool_state *const s = st();
    if (s->drawn)
        return;
    s->drawn = 1u;
    s->r->renders += 1u;
#if TOOL_DRY_RUN >= 3
    return;                     // counted the call; touch nothing else
#else
    if (!s->font)
        s->font = *(volatile mc3_u32 *)TOOL_FONT_SLOT;
    if (s->font < 0x00100000u || s->font >= 0x02000000u) {
        s->font = 0u;
        return;                 // the heading has not drawn yet - nothing to
    }                           // draw with, and asking for one faults
#if TOOL_DRY_RUN >= 2
    return;                     // have the font; do not draw with it yet
#else
    draw_ui();
#endif
#endif
}

static void take_vtable()
{
    tool_state *const s = st();
    // Guard: only touch a slot that still holds what it is supposed to. A slot
    // holding anything else means the layout is not what was measured, and
    // writing it would redirect something unrelated.
    volatile mc3_u32 *const a = (volatile mc3_u32 *)VT_SLOT_A;
    volatile mc3_u32 *const b = (volatile mc3_u32 *)VT_SLOT_B;
    if (*a == DORENDERING) { *a = (mc3_u32)&tool_render; s->taken |= 1u; }
    if (*b == DORENDERING) { *b = (mc3_u32)&tool_render; s->taken |= 2u; }
    MC3_LOG1(str()->log_taken, s->taken);
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    tool_state *const s = st();
    const tool_strings *const S = str();

    if (!s->r) {
        tool_report *w = (tool_report *)TOOL_ADDR;
        if (FOREIGN(w->magic))
            w = &s->fallback;           // another module owns the window
        if (w->magic != TOOL_MAGIC) {
            for (mc3_u32 *p = (mc3_u32 *)w;
                 p < (mc3_u32 *)((char *)w + sizeof(tool_report)); ++p)
                *p = 0u;
            w->magic = TOOL_MAGIC;
        }
        s->r = w;
        s->cursor.flags = 1;
        s->cursor.scale_x = 1.0f;
        s->cursor.scale_y = 1.0f;
        s->cursor.colour = 0x80FFFFFFu;
    }
    tool_report *const r = s->r;
    s->drawn = 0u;

    switch (r->state) {
    case ST_WAIT: {
        r->ticks += 1u;
        if (r->ticks == WAIT_FRAMES)
            mark(S->mk_alive);
        if (r->ticks < WAIT_FRAMES)
            return;
        // A frame counter is worthless as a readiness signal. Measured: at the
        // 180th call the game was still loading IRX modules, nowhere near a
        // file system, and calling rmcModel::Create there walked off the end of
        // EE RAM - 50 TLB misses at 0x0042E760 storing from 0x02000000 up, then
        // PCSX2 died.
        //
        // So the tool waits for PROOF instead: it takes the vtable, and does
        // nothing more until its own renderer has actually run. A frame drawn
        // through mcUiListMenu::DoRendering means the frontend is alive, which
        // means the asset manager, the fonts and the file system are all up -
        // exactly the things Create needs. If the menu never draws, the tool
        // never converts, and the report says so (renders = 0).
        const mc3_u32 principal = *(volatile mc3_u32 *)HEAP_ACTIVE;
        if (principal < 0x00100000u || principal >= 0x02000000u)
            return;
        if (!s->taken) {
            take_vtable();
            mark(S->mk_vtable);
        }
        if (!r->renders)
            return;
        mark(S->mk_render);
#if TOOL_DRY_RUN
        return;                 // proved the screen; touch nothing else
#endif
        r->free_before = heap_free(principal);
        r->state = ST_READY;
        return;
    }

    case ST_READY: {
        // Do the game's own admission test BEFORE calling it. mcHeap::
        // CreateMemTopHeap starts with `*(dword_70E500 + 8)`: called while the
        // active allocator is still null it reads address 8, derives a base
        // from the garbage and memsets from it - measured as 50 TLB misses at
        // 0x0042E760 storing from 0x02000000 upwards, which is the top of EE
        // RAM. Never hand it a heap it would choke on.
        const mc3_u32 principal = *(volatile mc3_u32 *)HEAP_ACTIVE;
        if (principal < 0x00100000u || principal >= 0x02000000u)
            return;                 // still not up; stay here and re-check
        s->asked = HEAP_BYTES;
        s->avail = heap_free(principal);
        if (s->avail < (mc3_u32)HEAP_BYTES + 16u) {
            r->state = ST_NO_HEAP;
            MC3_LOG2(S->log_heap, s->asked, s->avail);
            return;
        }
        const mc3_u32 h = MC3_CALL2(mc3_u32, HEAP_CREATE_MEMTOP,
                                    mc3_u32, const char *)(HEAP_BYTES, S->heap_name);
        if (!h) {
            r->state = ST_NO_HEAP;
            MC3_LOG2(S->log_heap, s->asked, s->avail);
            return;
        }
        mark(S->mk_heap);
        r->heap = h;
        r->heap_base = *(volatile mc3_u32 *)(h + ALLOC_BASE);
        r->heap_len = *(volatile mc3_u32 *)(h + ALLOC_SIZE);
        MC3_LOG2(S->log_heap, r->heap_base, r->heap_len);
        r->state = ST_CONVERT;
        return;
    }

    case ST_CONVERT: {
        // One item per frame, so the screen shows progress and a hang leaves
        // `running` naming the file that caused it.
        const int i = (int)s->next;
        const char *nm = (i < MAX_MODELS) ? S->name[i] : 0;
        if (!nm) {
            r->state = ST_DONE;
            mark(S->mk_done);
            MC3_LOG2(S->log_done, r->heap_base, r->count);
            return;
        }
        const char *ex = S->ext[i];
        tool_model *const d = &r->m[i];
        d->name = (mc3_u32)nm;
        r->running = (mc3_u32)(i + 1);

        // PROBE FIRST, ALWAYS. A name that does not resolve must never reach a
        // parser: this engine answers a missing file by hanging - 179 copies of
        // `while (1) ;` in .text, and most of the readers sit on one. Open
        // returns 0 instead, which is a result the tool can report.
        const mc3_u32 stream = open_raw(nm, ex);
        d->geom = stream;
        if (!stream) {
            d->obj = 0u;
            d->groups = 0xFFFFu;        // 0xFFFF: did not resolve
            r->running = 0u;
            r->count = (mc3_u32)(i + 1);
            s->next = (mc3_u32)(i + 1);
            MC3_LOG1(S->notfound, (mc3_u32)nm);
            return;
        }
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);

        const int kind = kind_for_ext(ex);
        d->matidx = (mc3_u32)kind;
        mc3_u32 o = 0u;

        // Everything the reader allocates has to land in the private heap, or
        // the dump is a heap image with holes in it.
        MC3_CALL1(void, HEAP_PUSH, mc3_u32)(r->heap);
        if (kind == K_MODEL)
            o = MC3_CALL2(mc3_u32, RMC_MODEL_CREATE, const char *, int)(nm, 0);
        else if (kind == K_IMAGE)
            o = MC3_CALL2(mc3_u32, GFX_LOAD_IMAGE, const char *, int)(nm, 0);
        else if (kind == K_BOUND)
            o = MC3_CALL1(mc3_u32, PH_BOUND_LOAD, const char *)(nm);
        MC3_CALL(void, HEAP_POP)();

        d->obj = o;
        if (kind == K_MODEL && o)
            d->groups = *(volatile mc3_u16 *)(o + 0x08);
        else
            d->groups = 0u;
        r->running = 0u;
        r->count = (mc3_u32)(i + 1);
        s->next = (mc3_u32)(i + 1);
        MC3_LOG2(S->log_model, o, (mc3_u32)kind);
        mark(S->mk_convert);
        return;
    }

    default:
        return;                         // DONE / NO HEAP: the screen says it
    }
}

// The vtable redirect is done from the per-frame callback - there is no `jal`
// to DoRendering in the image to patch. This hook is a separate thing: it is
// how the font is captured, and it forwards to the real drawer.
MC3_HOOK(0x00581728, capture_font);
