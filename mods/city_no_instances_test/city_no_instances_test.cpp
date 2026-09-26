// Diagnostic A/B: suppress only mcInstCityModel draw submissions.
// Unique/component city geometry continues through the original callsites.

#include "../../payload/mc3_mod.h"

enum {
    CURRENT_CITY = 0x00615B40u,
    GENERATED_GROUP_COUNT = 18u,
    TRACE_MAGIC = 0x534E494Eu, // bytes "NINS"
    TRACE_VERSION = 1u,
};

struct no_instances_state {
    mc3_u32 magic;
    mc3_u32 version;
    mc3_u32 size;
    mc3_u32 calls[4];
};

static no_instances_state g_state;

static __attribute__((noinline)) no_instances_state *state()
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

static __attribute__((noinline)) void record(mc3_u32 slot)
{
    no_instances_state *s = state();
    if (s->magic != TRACE_MAGIC ||
        s->version != TRACE_VERSION ||
        s->size != sizeof(no_instances_state)) {
        s->magic = TRACE_MAGIC;
        s->version = TRACE_VERSION;
        s->size = sizeof(no_instances_state);
        for (mc3_u32 i = 0; i < 4u; ++i)
            s->calls[i] = 0u;
    }
    ++s->calls[slot];
}

#define SKIP_CPV(name, slot)                                                  \
extern "C" mc3_u32 name(mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32,       \
                         mc3_u32)                                             \
{                                                                             \
    if (!generated_city_active())                                              \
        return MC3_CALL6(mc3_u32, 0x002A9A88u,                                \
                         mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32,         \
                         mc3_u32)(0u, 0u, 0u, 0u, 0u, 0u);                    \
    record(slot);                                                              \
    return 0u;                                                                 \
}

// Spell the handlers out so the original arguments are preserved if this
// diagnostic happens to run outside the generated 18-group city.
extern "C" mc3_u32 instance_pass2(mc3_u32 model, mc3_u32 group,
                                    mc3_u32 shader_data, mc3_u32 bucket,
                                    mc3_u32 arg5, mc3_u32 cpv_index)
{
    if (!generated_city_active())
        return MC3_CALL6(mc3_u32, 0x002A9A88u,
                         mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                         mc3_u32)(model, group, shader_data, bucket, arg5,
                                  cpv_index);
    record(0u);
    return 0u;
}

extern "C" mc3_u32 instance_pass8_group0(mc3_u32 model, mc3_u32 group,
                                           mc3_u32 shader_data,
                                           mc3_u32 bucket, mc3_u32 arg5,
                                           mc3_u32 cpv_index)
{
    if (!generated_city_active())
        return MC3_CALL6(mc3_u32, 0x002A9A88u,
                         mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                         mc3_u32)(model, group, shader_data, bucket, arg5,
                                  cpv_index);
    record(1u);
    return 0u;
}

extern "C" mc3_u32 instance_pass4_or_pass8_group2_or3(
    mc3_u32 model, mc3_u32 group, mc3_u32 shader_data, mc3_u32 bucket,
    mc3_u32 arg5)
{
    if (!generated_city_active())
        return MC3_CALL5(mc3_u32, 0x002A9918u,
                         mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
            model, group, shader_data, bucket, arg5);
    record(2u);
    return 0u;
}

extern "C" mc3_u32 instance_pass64(mc3_u32 model, mc3_u32 group,
                                     mc3_u32 shader_data, mc3_u32 bucket,
                                     mc3_u32 arg5, mc3_u32 cpv_index)
{
    if (!generated_city_active())
        return MC3_CALL6(mc3_u32, 0x002A9A88u,
                         mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32,
                         mc3_u32)(model, group, shader_data, bucket, arg5,
                                  cpv_index);
    record(3u);
    return 0u;
}

MC3_HOOK(0x002570B4u, instance_pass2);
MC3_HOOK(0x00257104u, instance_pass8_group0);
MC3_HOOK(0x00257148u, instance_pass4_or_pass8_group2_or3);
MC3_HOOK(0x00257190u, instance_pass64);
