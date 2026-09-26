// -----------------------------------------------------------------------------
// proper_widescreen_menu - adds a live aspect-ratio selector to the main menu.
//
// It chains the function already stored in mcMenuModeSelect's PopulateMenu
// vtable slot.  That matters because menu_row.mod may already own the slot to
// add "Mod City"; calling the previous handler preserves that row and then adds
// ours.  The init hook runs immediately after menu_row's init hook in the retail
// startup sequence, so the chain is deterministic rather than load-order luck.
//
// Pressing the row cycles 16:9 -> 21:9 -> 4:3 -> 16:9.  The large generated
// patch tables are still owned by proper_widescreen.mod.  This companion also
// intercepts the two perspective calls made by camViewCS::Draw: changing the
// instruction at 0x00527E14 only affects the next gfxPipeline::SetRes, while an
// already-created camera keeps a cached aspect in its gfxViewport.  Supplying
// the selected aspect when the camera rebuilds its projection each frame makes
// the 3D view switch live instead of only changing the patched code bytes.
// -----------------------------------------------------------------------------

#include "../../payload/mc3_mod.h"
#include "../proper_widescreen_shared.h"


enum {
    VTABLE_SLOT       = 0x00629C6C,
    POPULATE_MENU     = 0x00354010,
    ADD_ITEM_SIMPLE   = 0x005881A8,
    SET_TEXT_ELEMENT  = 0x00588540,
    DAT_CALLBACK_MAKE = 0x0042A630,
    SCREEN_ROOT       = 0x00617ADC,
    SCREEN_OWNER      = 12,
    SCREEN_LIST       = 0xF0,
    PMF_TOKYO         = 0x00655C44,
    ITEM_KIND         = 2,

    GFX_PERSPECTIVE   = 0x005311F8,
    PIPE_WIDTH_F      = 0x0070E5F4,
    PIPE_HEIGHT_F     = 0x0070E5F8,
    PIPE_BASE_ASPECT  = 0x0061C318,
    VIEW_ASPECT       = 0x134,
    VIEW_WIDTH        = 0x174,
    VIEW_HEIGHT       = 0x178,

    // Both branches of camViewCS::Draw pass aspect=0 to gfxViewport::Perspective,
    // which means "reuse the value cached by SetWindow".  They execute every
    // frame for the actual scene camera, so these are the live 3D update points.
    CAMERA_PERSPECTIVE_NEAR_FAR = 0x0031F2C0,
    CAMERA_PERSPECTIVE_CAM      = 0x0031F2DC,

    // At 0x001A0F38 the game calls this with a NOP delay slot.  menu_row.mod's
    // init wrapper is the preceding call at 0x001A0F30, so its vtable handler
    // is already installed when this wrapper chains it.
    INIT_HOOK_SITE    = 0x001A0F38,
    INIT_HOOK_ORIG    = 0x00204548, // mc::AllocateAudSfx(void)
};


static mc3_u32 g_previous_populate = (mc3_u32)POPULATE_MENU;
static mc3_u32 g_list = 0;
static mc3_u32 g_item = 0;

// Writable UTF-16-ish text used by txtFontTex.  The game renders lowercase as
// small caps, matching the stock top-level labels.
static mc3_u16 g_label[] = {
    'A', 's', 'p', 'e', 'c', 't', ' ',
    'R', 'a', 't', 'i', 'o', ':', ' ',
    '1', '6', ':', '9', 0
};


static inline int valid_pointer(mc3_u32 value)
{
    return value >= 0x00100000u && value < 0x02000000u && !(value & 3u);
}


static float profile_aspect(mc3_u32 profile)
{
    union float_bits {
        mc3_u32 word;
        float value;
    } bits;

    if (profile == (mc3_u32)WS_PROFILE_4X3)
        bits.word = 0x3FAAAAAAu;
    else if (profile == (mc3_u32)WS_PROFILE_21X9)
        bits.word = 0x40188E34u;
    else
        bits.word = 0x3FE38E34u;
    return bits.value;
}


extern "C" float mc3_select_camera_aspect(
    mc3_u32 viewport, mc3_u32 original_aspect_bits)
{
    union float_bits {
        mc3_u32 word;
        float value;
    } original;
    original.word = original_aspect_bits;

    float selected_aspect = original.value;
    volatile ws_mailbox *const box = ws_get_mailbox();

    if (box->magic == WS_MAILBOX_MAGIC &&
        ws_profile_valid(box->requested) && valid_pointer(viewport)) {
        const float pipe_width = *(volatile float *)PIPE_WIDTH_F;
        const float pipe_height = *(volatile float *)PIPE_HEIGHT_F;
        const int view_width = *(volatile int *)(viewport + VIEW_WIDTH);
        const int view_height = *(volatile int *)(viewport + VIEW_HEIGHT);

        if (pipe_width > 0.0f && pipe_height > 0.0f &&
            view_width > 0 && view_height > 0) {
            // SetWindow's exact formula is:
            //   base = pipe_height * display_aspect / pipe_width
            //   viewport_aspect = base * view_width / view_height
            // Keep it so split-screen and sub-viewports retain their shape.
            const float base =
                (pipe_height * profile_aspect(box->requested)) / pipe_width;
            selected_aspect =
                (base * (float)view_width) / (float)view_height;

            *(volatile float *)PIPE_BASE_ASPECT = base;
            *(volatile float *)(viewport + VIEW_ASPECT) = selected_aspect;
        }
    }

    return selected_aspect;
}


// camViewCS calls the retail Perspective method with its hand-written FPU ABI:
//   a0=this, f12=fov, f13=aspect, f14=near, f15=far.
// A normal free C++ function with an integer first argument shifts those float
// registers by one under this toolchain.  Keep an explicit bridge so the hook
// changes only f13 and cannot accidentally turn the aspect into the near clip.
extern "C" mc3_u32 mc3_aspect_camera_perspective();
__asm__(
    ".set noreorder\n"
    ".text\n"
    ".align 3\n"
    ".globl mc3_aspect_camera_perspective\n"
    "mc3_aspect_camera_perspective:\n"
    "  addiu $sp, $sp, -32\n"
    "  sd    $ra, 0($sp)\n"
    "  sw    $a0, 8($sp)\n"
    "  swc1  $f12, 12($sp)\n"
    "  swc1  $f14, 16($sp)\n"
    "  swc1  $f15, 20($sp)\n"
    "  mfc1  $a1, $f13\n"
    "  jal   mc3_select_camera_aspect\n"
    "  nop\n"
    "  lw    $a0, 8($sp)\n"
    "  lwc1  $f12, 12($sp)\n"
    "  mov.s $f13, $f0\n"
    "  lwc1  $f14, 16($sp)\n"
    "  lwc1  $f15, 20($sp)\n"
    "  lui   $t9, 0x0053\n"
    "  ori   $t9, $t9, 0x11f8\n"
    "  jalr  $t9\n"
    "  nop\n"
    "  ld    $ra, 0($sp)\n"
    "  jr    $ra\n"
    "  addiu $sp, $sp, 32\n"
    ".set reorder\n");


static void ensure_mailbox()
{
    volatile ws_mailbox *const box = ws_get_mailbox();
    if (box->magic == WS_MAILBOX_MAGIC)
        return;
    box->requested = (mc3_u32)WS_PROFILE_16X9;
    box->current = (mc3_u32)WS_PROFILE_PENDING;
    box->switches = 0;
    box->magic = WS_MAILBOX_MAGIC;
}


static void update_label()
{
    volatile ws_mailbox *const box = ws_get_mailbox();
    mc3_u32 profile = box->requested;
    if (!ws_profile_valid(profile))
        profile = (mc3_u32)WS_PROFILE_16X9;

    if (profile == (mc3_u32)WS_PROFILE_4X3) {
        g_label[14] = '4';
        g_label[15] = ':';
        g_label[16] = '3';
        g_label[17] = 0;
        g_label[18] = 0;
    } else if (profile == (mc3_u32)WS_PROFILE_21X9) {
        g_label[14] = '2';
        g_label[15] = '1';
        g_label[16] = ':';
        g_label[17] = '9';
        g_label[18] = 0;
    } else {
        g_label[14] = '1';
        g_label[15] = '6';
        g_label[16] = ':';
        g_label[17] = '9';
        g_label[18] = 0;
    }
}


static __attribute__((noinline)) const mc3_u16 *label_address()
{
    return g_label;
}


static mc3_u32 populate_handler_address();
static mc3_u32 selector_callback_address();


extern "C" void mc3_aspect_ratio_chosen(mc3_u32 self)
{
    (void)self;
    ensure_mailbox();
    volatile ws_mailbox *const box = ws_get_mailbox();
    mc3_u32 next = box->requested;
    if (!ws_profile_valid(next))
        next = (mc3_u32)WS_PROFILE_16X9;
    next = (next + 1u) % 3u;
    box->requested = next;
    update_label();

    // AddItemSimple copies the text into the row; changing g_label alone does
    // not change what is already on screen. Keep the returned row pointer and
    // rewrite its text immediately so the label always matches the mailbox.
    if (valid_pointer(g_list) && valid_pointer(g_item))
        MC3_CALL4(void, SET_TEXT_ELEMENT, mc3_u32, mc3_u32, int,
                  const mc3_u16 *)(g_list, g_item, ITEM_KIND, label_address());
}


extern "C" void mc3_populate_aspect_ratio(mc3_u32 self)
{
    mc3_u32 previous = g_previous_populate;
    if (!valid_pointer(previous) || previous == populate_handler_address())
        previous = (mc3_u32)POPULATE_MENU;
    MC3_CALL1(void, previous, mc3_u32)(self);

    ensure_mailbox();
    update_label();

    const mc3_u32 root = *(volatile mc3_u32 *)SCREEN_ROOT;
    if (!valid_pointer(root))
        return;
    const mc3_u32 screen = *(volatile mc3_u32 *)(root + SCREEN_OWNER);
    if (!valid_pointer(screen))
        return;
    const mc3_u32 list = *(volatile mc3_u32 *)(screen + SCREEN_LIST);
    if (!valid_pointer(list))
        return;

    // Build the same eight-byte pointer-to-member shape used by every retail
    // row. PMF_TOKYO supplies its adjustment word; the function is ours.
    const mc3_u32 adjustment =
        *(const volatile mc3_u32 *)(PMF_TOKYO + 0);
    const unsigned long long pmf =
        ((unsigned long long)selector_callback_address() << 32) | adjustment;

    mc3_u32 callback[8];
    MC3_CALL3(void, DAT_CALLBACK_MAKE, void *, unsigned long long, mc3_u32)
        (callback, pmf, self);

    g_list = list;
    g_item = MC3_CALL6(mc3_u32, ADD_ITEM_SIMPLE, mc3_u32, int, int, void *, int,
                       const mc3_u16 *)
        (list, 0, 0, callback, ITEM_KIND, label_address());
}


static __attribute__((noinline)) mc3_u32 populate_handler_address()
{
    return (mc3_u32)(void *)&mc3_populate_aspect_ratio;
}


static __attribute__((noinline)) mc3_u32 selector_callback_address()
{
    return (mc3_u32)(void *)&mc3_aspect_ratio_chosen;
}


extern "C" void mc3_aspect_menu_install()
{
    MC3_CALL(void, INIT_HOOK_ORIG)();
    ensure_mailbox();

    volatile mc3_u32 *const slot = (volatile mc3_u32 *)VTABLE_SLOT;
    const mc3_u32 current = *slot;
    const mc3_u32 ours = populate_handler_address();
    if (current == ours)
        return;
    if (!valid_pointer(current))
        return;

    g_previous_populate = current;
    *slot = ours;
}


MC3_HOOK(INIT_HOOK_SITE, mc3_aspect_menu_install);
MC3_HOOK(CAMERA_PERSPECTIVE_NEAR_FAR, mc3_aspect_camera_perspective);
MC3_HOOK(CAMERA_PERSPECTIVE_CAM, mc3_aspect_camera_perspective);
