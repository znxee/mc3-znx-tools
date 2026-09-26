// Diagnostic v9 for the generated modcity render pipeline.
//
// CTT8 proved that the generated CPV root cardinality is now correct, but it
// also called rmcModel::TouchShaders for every Draw/DrawCpv.  A busy state
// measured 695,233 calls.  CTT9 keeps the CPV guard and all useful counters,
// while rejuvenating a (model, shader-group) pair only once per city frame.
// The cache is epoch tagged: entries are reusable next frame, so this is not a
// permanent "already touched" cache and cannot let texture pages age out.

#include "../../payload/mc3_mod.h"

enum {
    RMC_MODEL_TOUCH_SHADERS = 0x002A8AF0u,
    RMC_MODEL_DRAW          = 0x002A9918u,
    RMC_MODEL_DRAW_CPV      = 0x002A9A88u,
    CITY_MODEL_SELECT       = 0x0024C9C0u,
    CITY_RENDER_DISPATCH    = 0x0024C380u,
    INST_RENDER_DISPATCH    = 0x00257008u,
    CULLABLE_RENDER         = 0x0024A860u,
    CURRENT_CITY            = 0x00615B40u,
    GENERATED_GROUP_COUNT   = 18u,
    CACHE_SIZE              = 1024u,
    CACHE_MASK              = CACHE_SIZE - 1u,
    TRACE_MAGIC             = 0x39545443u, // bytes "CTT9"
};

struct city_texture_touch_report_v9 {
    mc3_u32 magic;
    mc3_u32 version;
    mc3_u32 current_city;
    mc3_u32 current_descs;
    mc3_u32 active_checks;
    mc3_u32 dispatch_calls;
    mc3_u32 selector_calls;
    mc3_u32 selector_nonzero;
    mc3_u32 draw_calls;
    mc3_u32 draw_cpv_calls;
    mc3_u32 touch_calls;       // actual calls into stock TouchShaders
    mc3_u32 touch_ready;
    mc3_u32 frame_calls;
    mc3_u32 frame_epoch;
    mc3_u32 touch_requests;    // Draw/DrawCpv requests before de-duplication
    mc3_u32 touch_skipped;
    mc3_u32 cache_collisions;
    mc3_u32 cache_full;
    mc3_u32 last_dispatch_kind;
    mc3_u32 last_dispatch_object;
    mc3_u32 last_dispatch_owner;
    mc3_u32 last_dispatch_pass;
    mc3_u32 last_dispatch_arg;
    mc3_u32 last_selector_container;
    mc3_u32 last_selector_index;
    mc3_u32 last_selector_lod;
    mc3_u32 last_selector_result;
    mc3_u32 last_cpv_model;
    mc3_u32 last_cpv_group;
    mc3_u32 last_cpv_arg5;
    mc3_u32 last_cpv_index;
    mc3_u32 last_cpv_slot_count;
    mc3_u32 last_cpv_roots;
    mc3_u32 last_cpv_root;
    mc3_u32 max_cpv_index;
    mc3_u32 oob_calls;
    mc3_u32 clamp_calls;
    mc3_u32 first_oob_model;
    mc3_u32 first_oob_group;
    mc3_u32 first_oob_arg5;
    mc3_u32 first_oob_index;
    mc3_u32 first_oob_slot_count;
    mc3_u32 first_oob_roots;
    mc3_u32 first_oob_root;
};

struct touch_cache_entry {
    mc3_u32 model;
    mc3_u32 group;
    mc3_u32 epoch;
};

extern "C" {
volatile city_texture_touch_report_v9 g_city_texture_touch_v9 = {
    TRACE_MAGIC, 9u
};
mc3_u32 touch_shaders_lod0_v9(mc3_u32 model, mc3_u32 group);
}

static touch_cache_entry g_touch_cache[CACHE_SIZE];

// The game ABI reads lod from f12.  Keep zero in that exact register and
// tail-call the stock routine without introducing a C floating argument.
__asm__(
    ".set noreorder\n"
    ".globl touch_shaders_lod0_v9\n"
    ".ent touch_shaders_lod0_v9\n"
    "touch_shaders_lod0_v9:\n"
    "mtc1  $0, $f12\n"
    "lui   $2, 0x002A\n"
    "ori   $2, $2, 0x8AF0\n"
    "jr    $2\n"
    "nop\n"
    ".end touch_shaders_lod0_v9\n"
    ".set reorder\n"
);

static inline int sane_ptr(mc3_u32 value)
{
    return value >= 0x00100000u && value < 0x02000000u &&
           (value & 3u) == 0u;
}

static int generated_city_active()
{
    const mc3_u32 city = *(volatile mc3_u32 *)CURRENT_CITY;
    if (!sane_ptr(city) ||
        *(volatile mc3_u16 *)(city + 8u) != GENERATED_GROUP_COUNT)
        return 0;
    g_city_texture_touch_v9.current_city = city;
    g_city_texture_touch_v9.current_descs =
        *(volatile mc3_u32 *)(city + 12u);
    g_city_texture_touch_v9.active_checks += 1u;
    return 1;
}

static mc3_u32 touch_hash(mc3_u32 model, mc3_u32 group)
{
    return ((model >> 4) ^ ((group >> 4) * 0x9E3779B9u)) & CACHE_MASK;
}

static void touch_model_once(mc3_u32 model, mc3_u32 group)
{
    if (!sane_ptr(model) || !sane_ptr(group))
        return;

    g_city_texture_touch_v9.touch_requests += 1u;
    const mc3_u32 epoch = g_city_texture_touch_v9.frame_epoch;
    mc3_u32 slot = touch_hash(model, group);
    for (mc3_u32 probe = 0u; probe < CACHE_SIZE; ++probe) {
        touch_cache_entry &entry = g_touch_cache[slot];
        if (entry.epoch != epoch) {
            entry.model = model;
            entry.group = group;
            entry.epoch = epoch;
            g_city_texture_touch_v9.touch_calls += 1u;
            if (touch_shaders_lod0_v9(model, group))
                g_city_texture_touch_v9.touch_ready += 1u;
            return;
        }
        if (entry.model == model && entry.group == group) {
            g_city_texture_touch_v9.touch_skipped += 1u;
            return;
        }
        g_city_texture_touch_v9.cache_collisions += 1u;
        slot = (slot + 1u) & CACHE_MASK;
    }

    // Never drop rejuvenation if a frame really contains more unique pairs
    // than the table can hold.  The fallback preserves CTT8 behaviour.
    g_city_texture_touch_v9.cache_full += 1u;
    g_city_texture_touch_v9.touch_calls += 1u;
    if (touch_shaders_lod0_v9(model, group))
        g_city_texture_touch_v9.touch_ready += 1u;
}

static mc3_u32 record_cpv_and_guard(mc3_u32 model, mc3_u32 group,
                                    mc3_u32 arg5, mc3_u32 index)
{
    mc3_u32 slots = 0u;
    mc3_u32 roots = 0u;
    mc3_u32 root = 0u;
    if (sane_ptr(model)) {
        slots = *(volatile mc3_u16 *)(model + 10u);
        roots = *(volatile mc3_u32 *)(model + 20u);
        if (sane_ptr(roots) && index < slots)
            root = *(volatile mc3_u32 *)(roots + 4u * index);
    }

    g_city_texture_touch_v9.last_cpv_model = model;
    g_city_texture_touch_v9.last_cpv_group = group;
    g_city_texture_touch_v9.last_cpv_arg5 = arg5;
    g_city_texture_touch_v9.last_cpv_index = index;
    g_city_texture_touch_v9.last_cpv_slot_count = slots;
    g_city_texture_touch_v9.last_cpv_roots = roots;
    g_city_texture_touch_v9.last_cpv_root = root;
    if (index > g_city_texture_touch_v9.max_cpv_index)
        g_city_texture_touch_v9.max_cpv_index = index;

    if (!slots || index >= slots || !sane_ptr(roots) || !sane_ptr(root)) {
        g_city_texture_touch_v9.oob_calls += 1u;
        if (!g_city_texture_touch_v9.first_oob_model) {
            g_city_texture_touch_v9.first_oob_model = model;
            g_city_texture_touch_v9.first_oob_group = group;
            g_city_texture_touch_v9.first_oob_arg5 = arg5;
            g_city_texture_touch_v9.first_oob_index = index;
            g_city_texture_touch_v9.first_oob_slot_count = slots;
            g_city_texture_touch_v9.first_oob_roots = roots;
            g_city_texture_touch_v9.first_oob_root = root;
        }
        if (slots && sane_ptr(roots) &&
            sane_ptr(*(volatile mc3_u32 *)roots)) {
            g_city_texture_touch_v9.clamp_calls += 1u;
            return 0u;
        }
    }
    return index;
}

extern "C" void city_frame_v9()
{
    mc3_u32 epoch = g_city_texture_touch_v9.frame_epoch + 1u;
    if (!epoch)
        epoch = 1u;
    g_city_texture_touch_v9.frame_epoch = epoch;
    g_city_texture_touch_v9.frame_calls += 1u;
    MC3_CALL(void, CULLABLE_RENDER)();
}

extern "C" mc3_u32 city_model_select_v9(mc3_u32 container,
                                         mc3_u32 index, mc3_u32 lod)
{
    const int active = generated_city_active();
    const mc3_u32 result =
        MC3_CALL3(mc3_u32, CITY_MODEL_SELECT,
                  mc3_u32, mc3_u32, mc3_u32)(container, index, lod);
    if (active) {
        g_city_texture_touch_v9.selector_calls += 1u;
        g_city_texture_touch_v9.last_selector_container = container;
        g_city_texture_touch_v9.last_selector_index = index;
        g_city_texture_touch_v9.last_selector_lod = lod;
        g_city_texture_touch_v9.last_selector_result = result;
        if (result)
            g_city_texture_touch_v9.selector_nonzero += 1u;
    }
    return result;
}

static void record_dispatch(mc3_u32 kind, mc3_u32 object,
                            mc3_u32 owner, mc3_u32 pass, mc3_u32 arg)
{
    if (!generated_city_active())
        return;
    g_city_texture_touch_v9.dispatch_calls += 1u;
    g_city_texture_touch_v9.last_dispatch_kind = kind;
    g_city_texture_touch_v9.last_dispatch_object = object;
    g_city_texture_touch_v9.last_dispatch_owner = owner;
    g_city_texture_touch_v9.last_dispatch_pass = pass;
    g_city_texture_touch_v9.last_dispatch_arg = arg;
}

extern "C" mc3_u32 city_render_dispatch_v9(mc3_u32 object,
                                            mc3_u32 owner, mc3_u32 pass,
                                            mc3_u32 arg)
{
    record_dispatch(0u, object, owner, pass, arg);
    return MC3_CALL4(mc3_u32, CITY_RENDER_DISPATCH,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        object, owner, pass, arg);
}

extern "C" mc3_u32 inst_render_dispatch_v9(mc3_u32 object,
                                            mc3_u32 owner, mc3_u32 pass,
                                            mc3_u32 arg)
{
    record_dispatch(1u, object, owner, pass, arg);
    return MC3_CALL4(mc3_u32, INST_RENDER_DISPATCH,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        object, owner, pass, arg);
}

extern "C" mc3_u32 city_draw_touch_v9(mc3_u32 model, mc3_u32 group,
                                       mc3_u32 shader_data, mc3_u32 pass,
                                       mc3_u32 arg5)
{
    if (generated_city_active()) {
        g_city_texture_touch_v9.draw_calls += 1u;
        touch_model_once(model, group);
    }
    return MC3_CALL5(mc3_u32, RMC_MODEL_DRAW,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        model, group, shader_data, pass, arg5);
}

extern "C" mc3_u32 city_draw_cpv_touch_v9(mc3_u32 model, mc3_u32 group,
                                           mc3_u32 shader_data,
                                           mc3_u32 pass, mc3_u32 arg5,
                                           mc3_u32 cpv_index)
{
    mc3_u32 guarded_index = cpv_index;
    if (generated_city_active()) {
        g_city_texture_touch_v9.draw_cpv_calls += 1u;
        guarded_index = record_cpv_and_guard(model, group, arg5, cpv_index);
        touch_model_once(model, group);
    }
    return MC3_CALL6(mc3_u32, RMC_MODEL_DRAW_CPV,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                     mc3_u32, mc3_u32)(
        model, group, shader_data, pass, arg5, guarded_index);
}

MC3_HOOK(0x00257BACu, city_frame_v9);

MC3_HOOK(0x0024BFC4u, city_render_dispatch_v9);
MC3_HOOK(0x00256F78u, inst_render_dispatch_v9);

MC3_HOOK(0x0024C3F8u, city_model_select_v9);
MC3_HOOK(0x0024C448u, city_model_select_v9);
MC3_HOOK(0x0024C490u, city_model_select_v9);
MC3_HOOK(0x0024C4D4u, city_model_select_v9);
MC3_HOOK(0x00257080u, city_model_select_v9);
MC3_HOOK(0x002570D0u, city_model_select_v9);
MC3_HOOK(0x00257118u, city_model_select_v9);
MC3_HOOK(0x0025715Cu, city_model_select_v9);

MC3_HOOK(0x0024C42Cu, city_draw_cpv_touch_v9);
MC3_HOOK(0x0024C47Cu, city_draw_cpv_touch_v9);
MC3_HOOK(0x0024C4C0u, city_draw_touch_v9);
MC3_HOOK(0x0024C508u, city_draw_cpv_touch_v9);
MC3_HOOK(0x002570B4u, city_draw_cpv_touch_v9);
MC3_HOOK(0x00257104u, city_draw_cpv_touch_v9);
MC3_HOOK(0x00257148u, city_draw_touch_v9);
MC3_HOOK(0x00257190u, city_draw_cpv_touch_v9);
