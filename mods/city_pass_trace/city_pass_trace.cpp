// Diagnostic: identify which City render callsite submits each model.
// It changes no draw data and always calls the original Draw/DrawCpv.

#include "../../payload/mc3_mod.h"

enum {
    RMC_MODEL_DRAW = 0x002A9918u,
    RMC_MODEL_DRAW_CPV = 0x002A9A88u,
    CURRENT_CITY = 0x00615B40u,
    GENERATED_GROUP_COUNT = 18u,
    TRACE_MAGIC = 0x53415043u, // bytes "CPAS"
    TRACE_VERSION = 1u,
    ENTRY_COUNT = 256u,
};

struct model_entry {
    mc3_u32 model;
    mc3_u32 callsite_mask;
};

struct pass_trace_state {
    mc3_u32 magic;
    mc3_u32 version;
    mc3_u32 size;
    mc3_u32 unique_models;
    mc3_u32 overflow;
    mc3_u32 calls[8];
    model_entry entries[ENTRY_COUNT];
};

static pass_trace_state g_state;

static __attribute__((noinline)) pass_trace_state *state()
{
    return &g_state;
}

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

static void initialise(pass_trace_state *s)
{
    if (s->magic == TRACE_MAGIC && s->version == TRACE_VERSION &&
        s->size == sizeof(pass_trace_state))
        return;
    mc3_u32 *words = (mc3_u32 *)s;
    for (mc3_u32 i = 0; i < sizeof(pass_trace_state) / 4u; ++i)
        words[i] = 0u;
    s->magic = TRACE_MAGIC;
    s->version = TRACE_VERSION;
    s->size = sizeof(pass_trace_state);
}

static void record(mc3_u32 model, mc3_u32 callsite)
{
    pass_trace_state *s = state();
    initialise(s);
    if (!generated_city_active() || !sane_ptr(model) || callsite >= 8u)
        return;
    ++s->calls[callsite];
    mc3_u32 slot = (model >> 4u) & (ENTRY_COUNT - 1u);
    for (mc3_u32 probe = 0u; probe < ENTRY_COUNT; ++probe) {
        model_entry *entry = &s->entries[slot];
        if (entry->model == model) {
            entry->callsite_mask |= 1u << callsite;
            return;
        }
        if (entry->model == 0u) {
            entry->model = model;
            entry->callsite_mask = 1u << callsite;
            ++s->unique_models;
            return;
        }
        slot = (slot + 1u) & (ENTRY_COUNT - 1u);
    }
    ++s->overflow;
}

#define CPV_HANDLER(name, site)                                               \
extern "C" mc3_u32 name(mc3_u32 model, mc3_u32 group,                       \
                         mc3_u32 shader_data, mc3_u32 bucket,                \
                         mc3_u32 arg5, mc3_u32 cpv_index)                    \
{                                                                            \
    record(model, site);                                                      \
    return MC3_CALL6(mc3_u32, RMC_MODEL_DRAW_CPV,                            \
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(  \
        model, group, shader_data, bucket, arg5, cpv_index);                 \
}

#define DRAW_HANDLER(name, site)                                              \
extern "C" mc3_u32 name(mc3_u32 model, mc3_u32 group,                       \
                         mc3_u32 shader_data, mc3_u32 bucket, mc3_u32 arg5)   \
{                                                                            \
    record(model, site);                                                      \
    return MC3_CALL5(mc3_u32, RMC_MODEL_DRAW,                                \
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(           \
        model, group, shader_data, bucket, arg5);                            \
}

CPV_HANDLER(unique_pass2, 0u)
CPV_HANDLER(unique_pass8_group0, 1u)
DRAW_HANDLER(unique_pass4_or_pass8_group2_or3, 2u)
CPV_HANDLER(unique_pass64, 3u)
CPV_HANDLER(instance_pass2, 4u)
CPV_HANDLER(instance_pass8_group0, 5u)
DRAW_HANDLER(instance_pass4_or_pass8_group2_or3, 6u)
CPV_HANDLER(instance_pass64, 7u)

MC3_HOOK(0x0024C42Cu, unique_pass2);
MC3_HOOK(0x0024C47Cu, unique_pass8_group0);
MC3_HOOK(0x0024C4C0u, unique_pass4_or_pass8_group2_or3);
MC3_HOOK(0x0024C508u, unique_pass64);
MC3_HOOK(0x002570B4u, instance_pass2);
MC3_HOOK(0x00257104u, instance_pass8_group0);
MC3_HOOK(0x00257148u, instance_pass4_or_pass8_group2_or3);
MC3_HOOK(0x00257190u, instance_pass64);
