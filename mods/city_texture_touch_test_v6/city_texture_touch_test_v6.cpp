// Diagnostic v6 for the generated losangeles texture-demand path.
//
// The v1 bridge had all eight call-site hooks installed, but save 01 still had
// 207/207 datChunkRef::data == 0, all 835 page-state words == 0, and an empty
// request queue. V4 proved that the bridge runs and stock TouchShaders works.
// V5 then showed that all captured calls could belong to the frontend's
// background city while the final savestate pointed at a different mcCity.
// This version retains the totals but starts a new epoch whenever CURRENT_CITY
// changes, separating the frontend scene from the current losangeles.
// It counts wrapper entry before any filter, records
// the first rejected invariant, and calls the stock rmcModel::TouchShaders
// rather than duplicating its material walk.  The original Draw call remains
// unchanged and always runs after the diagnostic.

#include "../../payload/mc3_mod.h"

enum {
    RMC_MODEL_DRAW          = 0x002A9918u,
    RMC_MODEL_DRAW_CPV      = 0x002A9A88u,
    REQUEST_COUNT           = 0x00714358u,
    CURRENT_CITY            = 0x00615B40u,
    TRACE_MAGIC             = 0x36545443u, // bytes "CTT6"
};

struct city_texture_touch_report {
    mc3_u32 magic;
    mc3_u32 wrapper_calls;
    mc3_u32 validated_calls;
    mc3_u32 stock_touch_calls;
    mc3_u32 stock_ready;
    mc3_u32 reject_reason;
    mc3_u32 reject_index;
    mc3_u32 first_model;
    mc3_u32 first_group;
    mc3_u32 first_count;
    mc3_u32 first_indices;
    mc3_u32 first_slots;
    mc3_u32 first_slot_count;
    mc3_u32 first_material;
    mc3_u32 first_shader;
    mc3_u32 queue_before;
    mc3_u32 queue_after;
    mc3_u32 target_calls;
    mc3_u32 target_validated;
    mc3_u32 target_stock_ready;
    mc3_u32 target_city;
    mc3_u32 target_descs;
    mc3_u32 target_group_index;
    mc3_u32 target_model;
    mc3_u32 target_count;
    mc3_u32 target_material;
    mc3_u32 target_shader;
    mc3_u32 target_queue_before;
    mc3_u32 target_queue_after;
    mc3_u32 last_city;
    mc3_u32 last_descs;
    mc3_u32 epoch_serial;
    mc3_u32 epoch_wrapper_calls;
    mc3_u32 epoch_target_calls;
    mc3_u32 epoch_target_validated;
    mc3_u32 epoch_target_stock_ready;
    mc3_u32 epoch_last_group;
    mc3_u32 epoch_last_model;
    mc3_u32 epoch_last_group_index;
    mc3_u32 epoch_queue_before;
    mc3_u32 epoch_queue_after;
};

extern "C" {
volatile city_texture_touch_report g_city_texture_touch = { TRACE_MAGIC };
mc3_u32 touch_shaders_lod0(mc3_u32 model, mc3_u32 group);
}

// The member-call ABI used by the game reads lod from f12 even though model
// and group occupy a0/a1.  A normal C prototype with a third float puts zero in
// f14, so set the exact game register and tail-call with the original RA.
__asm__(
    ".set noreorder\n"
    ".globl touch_shaders_lod0\n"
    ".ent touch_shaders_lod0\n"
    "touch_shaders_lod0:\n"
    "mtc1  $0, $f12\n"
    "lui   $2, 0x002A\n"
    "ori   $2, $2, 0x8AF0\n"
    "jr    $2\n"
    "nop\n"
    ".end touch_shaders_lod0\n"
    ".set reorder\n"
);

static inline int sane_ptr(mc3_u32 p)
{
    return p >= 0x00100000u && p < 0x02000000u && (p & 3u) == 0u;
}

static void record_reject(mc3_u32 reason, mc3_u32 index)
{
    if (!g_city_texture_touch.reject_reason) {
        g_city_texture_touch.reject_reason = reason;
        g_city_texture_touch.reject_index = index;
    }
}

static void touch_model_stock(mc3_u32 model, mc3_u32 group)
{
    g_city_texture_touch.wrapper_calls += 1u;

    int target = 0;
    mc3_u32 target_index = 0u;
    const mc3_u32 city = *(volatile mc3_u32 *)CURRENT_CITY;
    mc3_u32 descs = 0u;
    mc3_u32 groups = 0u;
    if (sane_ptr(city)) {
        groups = *(volatile mc3_u16 *)(city + 8u);
        descs = *(volatile mc3_u32 *)(city + 12u);
    }

    if (g_city_texture_touch.last_city != city) {
        g_city_texture_touch.last_city = city;
        g_city_texture_touch.epoch_serial += 1u;
        g_city_texture_touch.epoch_wrapper_calls = 0u;
        g_city_texture_touch.epoch_target_calls = 0u;
        g_city_texture_touch.epoch_target_validated = 0u;
        g_city_texture_touch.epoch_target_stock_ready = 0u;
        g_city_texture_touch.epoch_last_group = 0u;
        g_city_texture_touch.epoch_last_model = 0u;
        g_city_texture_touch.epoch_last_group_index = 0u;
        g_city_texture_touch.epoch_queue_before = 0u;
        g_city_texture_touch.epoch_queue_after = 0u;
    }
    g_city_texture_touch.last_descs = descs;
    g_city_texture_touch.epoch_wrapper_calls += 1u;

    if (groups && groups <= 64u && sane_ptr(descs) &&
        group >= descs && group < descs + 16u * groups &&
        ((group - descs) & 15u) == 0u) {
        target = 1;
        target_index = (group - descs) / 16u;
        g_city_texture_touch.target_calls += 1u;
        g_city_texture_touch.epoch_target_calls += 1u;
        g_city_texture_touch.epoch_last_group = group;
        g_city_texture_touch.epoch_last_model = model;
        g_city_texture_touch.epoch_last_group_index = target_index;
        if (!g_city_texture_touch.target_city) {
            g_city_texture_touch.target_city = city;
            g_city_texture_touch.target_descs = descs;
            g_city_texture_touch.target_group_index = target_index;
            g_city_texture_touch.target_model = model;
        }
    }

    if (!g_city_texture_touch.first_model) {
        g_city_texture_touch.first_model = model;
        g_city_texture_touch.first_group = group;
    }
    if (!sane_ptr(model)) {
        record_reject(1u, 0u);
        return;
    }
    if (!sane_ptr(group)) {
        record_reject(2u, 0u);
        return;
    }

    const mc3_u32 count = *(volatile mc3_u16 *)(model + 8u);
    const mc3_u32 indices = *(volatile mc3_u32 *)(model + 12u);
    const mc3_u32 slots = *(volatile mc3_u32 *)(group + 4u);
    const mc3_u32 slot_count = *(volatile mc3_u16 *)(group + 8u);
    if (target && !g_city_texture_touch.target_count)
        g_city_texture_touch.target_count = count;
    if (!g_city_texture_touch.first_count) {
        g_city_texture_touch.first_count = count;
        g_city_texture_touch.first_indices = indices;
        g_city_texture_touch.first_slots = slots;
        g_city_texture_touch.first_slot_count = slot_count;
    }
    if (!count) {
        record_reject(3u, 0u);
        return;
    }
    if (count > 0x10000u) {
        record_reject(4u, count);
        return;
    }
    if (!sane_ptr(indices)) {
        record_reject(5u, 0u);
        return;
    }
    if (!sane_ptr(slots)) {
        record_reject(6u, 0u);
        return;
    }

    // Validate exactly what stock TouchShaders will dereference so the tracer
    // reports malformed generated data instead of turning it into a new crash.
    for (mc3_u32 i = 0u; i < count; ++i) {
        const mc3_u32 material =
            *(volatile mc3_u16 *)(indices + 2u * i);
        if (i == 0u)
            g_city_texture_touch.first_material = material;
        if (target && i == 0u)
            g_city_texture_touch.target_material = material;
        if (material >= slot_count) {
            record_reject(7u, i);
            return;
        }
        const mc3_u32 shader =
            *(volatile mc3_u32 *)(slots + 4u * material);
        if (i == 0u)
            g_city_texture_touch.first_shader = shader;
        if (target && i == 0u)
            g_city_texture_touch.target_shader = shader;
        if (!sane_ptr(shader)) {
            record_reject(8u, i);
            return;
        }
        const mc3_u32 vtable = *(volatile mc3_u32 *)shader;
        if (!sane_ptr(vtable) ||
            !sane_ptr(*(volatile mc3_u32 *)(vtable + 20u))) {
            record_reject(9u, i);
            return;
        }
    }

    g_city_texture_touch.validated_calls += 1u;
    if (target) {
        g_city_texture_touch.target_validated += 1u;
        g_city_texture_touch.epoch_target_validated += 1u;
    }
    g_city_texture_touch.queue_before =
        *(volatile mc3_u32 *)REQUEST_COUNT;
    const mc3_u32 ready = touch_shaders_lod0(model, group);
    g_city_texture_touch.stock_touch_calls += 1u;
    if (ready) {
        g_city_texture_touch.stock_ready += 1u;
        if (target) {
            g_city_texture_touch.target_stock_ready += 1u;
            g_city_texture_touch.epoch_target_stock_ready += 1u;
        }
    }
    g_city_texture_touch.queue_after =
        *(volatile mc3_u32 *)REQUEST_COUNT;
    if (target) {
        g_city_texture_touch.target_queue_before =
            g_city_texture_touch.queue_before;
        g_city_texture_touch.target_queue_after =
            g_city_texture_touch.queue_after;
        g_city_texture_touch.epoch_queue_before =
            g_city_texture_touch.queue_before;
        g_city_texture_touch.epoch_queue_after =
            g_city_texture_touch.queue_after;
    }
}

extern "C" mc3_u32 city_draw_touch_v6(mc3_u32 model, mc3_u32 group,
                                        mc3_u32 shader_data, mc3_u32 pass,
                                        mc3_u32 arg5)
{
    touch_model_stock(model, group);
    return MC3_CALL5(mc3_u32, RMC_MODEL_DRAW,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        model, group, shader_data, pass, arg5);
}

extern "C" mc3_u32 city_draw_cpv_touch_v6(mc3_u32 model, mc3_u32 group,
                                            mc3_u32 shader_data,
                                            mc3_u32 pass, mc3_u32 cpv,
                                            mc3_u32 arg6)
{
    touch_model_stock(model, group);
    return MC3_CALL6(mc3_u32, RMC_MODEL_DRAW_CPV,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                     mc3_u32, mc3_u32)(
        model, group, shader_data, pass, cpv, arg6);
}

MC3_HOOK(0x0024C42Cu, city_draw_cpv_touch_v6);
MC3_HOOK(0x0024C47Cu, city_draw_cpv_touch_v6);
MC3_HOOK(0x0024C4C0u, city_draw_touch_v6);
MC3_HOOK(0x0024C508u, city_draw_cpv_touch_v6);
MC3_HOOK(0x002570B4u, city_draw_cpv_touch_v6);
MC3_HOOK(0x00257104u, city_draw_cpv_touch_v6);
MC3_HOOK(0x00257148u, city_draw_touch_v6);
MC3_HOOK(0x00257190u, city_draw_cpv_touch_v6);
