// mcInstCityModel::Render passes Instance+0x20 to DoSetWorld (3x LQ).
// Misaligned Instance arrays cause LQ to silently round DOWN, although the
// scalar scratchpad mirror still matches the source. Preserve all 12 words
// in a 16-byte-aligned temporary before the original copies them into DMA.
#include "../../payload/mc3_mod.h"

enum {
    DO_SET_WORLD = 0x002ADB48u,
    CURRENT_CITY = 0x00615B40u,
    MAGIC = 0x4E4C4149u, // IALN
};

struct align_state {
    mc3_u32 magic, version, size, calls, corrected, forwarded;
    mc3_u32 samples, packet_errors, word_mismatches;
    mc3_u32 source_address, aligned_address, packet_address;
    mc3_u32 source_words[12], packet_words[12];
};

static align_state g_state;
static __attribute__((noinline)) align_state *state() { return &g_state; }

static int sane(mc3_u32 p) { return p >= 0x100000u && p <= 0x01FFFFB0u && !(p & 3u); }
static mc3_u32 word(mc3_u32 p) { return *(volatile mc3_u32 *)p; }

extern "C" void instance_set_world_aligned(mc3_u32 matrix)
{
    const mc3_u32 city = word(CURRENT_CITY);
    if (!sane(matrix) || !sane(city) ||
        *(volatile mc3_u16 *)(city + 8u) != 18u) {
        MC3_CALL1(void, DO_SET_WORLD, mc3_u32)(matrix);
        return;
    }
    align_state *s = state();
    if (s->magic != MAGIC || s->version != 1u || s->size != sizeof(*s)) {
        volatile mc3_u32 *w = (volatile mc3_u32 *)s;
        for (mc3_u32 i = 0u; i < sizeof(*s) / 4u; ++i) w[i] = 0u;
        s->magic = MAGIC; s->version = 1u; s->size = sizeof(*s);
    }
    ++s->calls;
    if (!(matrix & 15u)) {
        ++s->forwarded;
        MC3_CALL1(void, DO_SET_WORLD, mc3_u32)(matrix);
        return;
    }

    // 48 bytes + up to 12 bytes of padding, independent of stack alignment.
    mc3_u32 storage[15];
    const mc3_u32 aligned = ((mc3_u32)storage + 15u) & ~15u;
    volatile mc3_u32 *dest = (volatile mc3_u32 *)aligned;
    for (mc3_u32 i = 0u; i < 12u; ++i) dest[i] = word(matrix + 4u * i);
    ++s->corrected;
    MC3_CALL1(void, DO_SET_WORLD, mc3_u32)(aligned);

    // Capture the DMA copy itself, never the misleading scalar mirror.
    // The original copies synchronously; the temporary is not retained.
    if (s->samples < 64u) {
        ++s->samples;
        s->source_address = matrix;
        s->aligned_address = aligned;
        const mc3_u32 packet = word(0x700005ACu) - 80u;
        s->packet_address = packet;
        if (!sane(packet) || word(packet) != 0x10000004u ||
            word(packet + 12u) != 0x6804000Cu ||
            word(packet + 64u) != 0x1400000Au) {
            ++s->packet_errors;
            return;
        }
        for (mc3_u32 i = 0u; i < 12u; ++i) {
            s->source_words[i] = word(matrix + 4u * i);
            s->packet_words[i] = word(packet + 16u + 4u * i);
            if (s->source_words[i] != s->packet_words[i]) ++s->word_mismatches;
        }
    }
}

MC3_HOOK(0x00256EB8u, instance_set_world_aligned);
