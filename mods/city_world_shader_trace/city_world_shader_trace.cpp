// Read-only diagnostic for the generated City render path.
//
// It records the Matrix34 handed to DoSetWorld and summarizes the shader
// classes selected by every rmcModel group.  It never replaces a matrix,
// shader, texture, CPV index or draw argument.

#include "../../payload/mc3_mod.h"

#define MC3_LOG_OWNER 0x57534844u  // "WSHD"
#include "../../payload/mc3_log.h"

enum {
    DO_SET_WORLD          = 0x002ADB48u,
    RMC_MODEL_DRAW        = 0x002A9918u,
    RMC_MODEL_DRAW_CPV    = 0x002A9A88u,
    CURRENT_CITY          = 0x00615B40u,
    GENERATED_GROUP_COUNT = 18u,
    MAX_LOGGED_DRAWS      = 128u,
};

static mc3_u32 g_world;
static mc3_u32 g_world_kind;  // 0 unique component, 1 instance
static mc3_u32 g_draws;
static mc3_u32 g_logged;

static int sane_ptr(mc3_u32 value)
{
    return value >= 0x00100000u && value < 0x02000000u &&
           (value & 3u) == 0u;
}

static int generated_city_active()
{
    const mc3_u32 city = *(volatile mc3_u32 *)CURRENT_CITY;
    return sane_ptr(city) &&
           *(volatile mc3_u16 *)(city + 8u) == GENERATED_GROUP_COUNT;
}

static mc3_u32 word(mc3_u32 addr)
{
    return *(volatile mc3_u32 *)addr;
}

static int magnitude_below_one(mc3_u32 raw)
{
    return (raw & 0x7FFFFFFFu) < 0x3F800000u;
}

extern "C" void city_world_unique_trace(mc3_u32 matrix)
{
    g_world = matrix;
    g_world_kind = 0u;
    MC3_CALL1(void, DO_SET_WORLD, mc3_u32)(matrix);
}

extern "C" void city_world_instance_trace(mc3_u32 matrix)
{
    g_world = matrix;
    g_world_kind = 1u;
    MC3_CALL1(void, DO_SET_WORLD, mc3_u32)(matrix);
}

static void record_draw(mc3_u32 model, mc3_u32 group)
{
    if (!generated_city_active() || !sane_ptr(model) || !sane_ptr(group) ||
        !sane_ptr(g_world))
        return;

    ++g_draws;
    const mc3_u32 tx = word(g_world + 0x24u);
    const mc3_u32 ty = word(g_world + 0x28u);
    const mc3_u32 tz = word(g_world + 0x2Cu);
    const int near_origin = magnitude_below_one(tx) && magnitude_below_one(tz);
    if (g_logged >= MAX_LOGGED_DRAWS || (g_draws > 16u && !near_origin))
        return;

    const mc3_u32 groups = *(volatile mc3_u16 *)(model + 8u);
    const mc3_u32 materials = word(model + 0x0Cu);
    const mc3_u32 slots = word(group + 4u);
    const mc3_u32 slot_count = *(volatile mc3_u16 *)(group + 8u);
    if (!sane_ptr(materials) || !sane_ptr(slots) || groups > 256u ||
        slot_count > 4096u)
        return;

    mc3_u32 basic = 0u;
    mc3_u32 instance = 0u;
    mc3_u32 complex = 0u;
    mc3_u32 null_shader = 0u;
    mc3_u32 invalid = 0u;
    mc3_u32 first_material = 0xFFFFFFFFu;
    mc3_u32 first_shader = 0u;

    for (mc3_u32 i = 0u; i < groups; ++i) {
        const mc3_u32 material = *(volatile mc3_u16 *)(materials + 2u * i);
        if (i == 0u)
            first_material = material;
        if (material >= slot_count) {
            ++invalid;
            continue;
        }
        const mc3_u32 shader = word(slots + 4u * material);
        if (i == 0u)
            first_shader = shader;
        if (!shader) {
            ++null_shader;
            continue;
        }
        if (!sane_ptr(shader)) {
            ++invalid;
            continue;
        }
        const mc3_u32 type = word(shader + 4u) & 0x7Fu;
        if (type == 0u)
            ++basic;
        else if (type == 2u || type == 0x10u || type == 0x11u)
            ++instance;
        else if (type == 1u || type == 0x13u || type == 0x14u)
            ++complex;
        else
            ++invalid;
    }

    ++g_logged;
    MC3_LOG4("world/kind/model/group", g_world, g_world_kind, model, group);
    MC3_LOG4("tx/ty/tz/draw", tx, ty, tz, g_draws);
    MC3_LOG4("groups/basic/inst/complex", groups, basic, instance, complex);
    MC3_LOG4("null/bad/mat0/shader0", null_shader, invalid,
             first_material, first_shader);
}

extern "C" mc3_u32 city_draw_trace(mc3_u32 model, mc3_u32 group,
                                     mc3_u32 shader_data, mc3_u32 pass,
                                     mc3_u32 arg5)
{
    record_draw(model, group);
    return MC3_CALL5(mc3_u32, RMC_MODEL_DRAW,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        model, group, shader_data, pass, arg5);
}

extern "C" mc3_u32 city_draw_cpv_trace(mc3_u32 model, mc3_u32 group,
                                         mc3_u32 shader_data, mc3_u32 pass,
                                         mc3_u32 arg5, mc3_u32 cpv_index)
{
    record_draw(model, group);
    return MC3_CALL6(mc3_u32, RMC_MODEL_DRAW_CPV,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                     mc3_u32, mc3_u32)(
        model, group, shader_data, pass, arg5, cpv_index);
}

MC3_HOOK(0x0024BFA8u, city_world_unique_trace);
MC3_HOOK(0x00256EB8u, city_world_instance_trace);

MC3_HOOK(0x0024C42Cu, city_draw_cpv_trace);
MC3_HOOK(0x0024C47Cu, city_draw_cpv_trace);
MC3_HOOK(0x0024C4C0u, city_draw_trace);
MC3_HOOK(0x0024C508u, city_draw_cpv_trace);
MC3_HOOK(0x002570B4u, city_draw_cpv_trace);
MC3_HOOK(0x00257104u, city_draw_cpv_trace);
MC3_HOOK(0x00257148u, city_draw_trace);
MC3_HOOK(0x00257190u, city_draw_cpv_trace);
