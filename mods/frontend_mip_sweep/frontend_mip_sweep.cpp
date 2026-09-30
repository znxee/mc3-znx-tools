// Temporary frontend camera sweep for texture mip/streaming diagnosis.
// It cooperates with freecam.mod through its documented v1 state ABI and
// deliberately installs no hook of its own.
#include "../../payload/mc3_mod.h"
#include <stddef.h>

struct Vec3 { float x, y, z; };
struct Matrix { Vec3 right, up, back, pos; };

struct FreecamState {
    mc3_u32 magic, version, size, status;
    mc3_u32 active; float fov; mc3_u32 camera, frame;
    volatile mc3_u32 host_seq, host_keys, host_x, host_y, host_focus, host_beat;
    mc3_u32 rx_keys, rx_focus;
    Matrix matrix, live;
    Vec3 velocity;
    float speed;
    mc3_u32 smoothing, previous, rx_seq, rx_x, rx_y, rx_beat;
    float rx_age;
    mc3_u32 live_camera, live_frame;
    float live_fov;
};
static_assert(offsetof(FreecamState, matrix) == 64 &&
              offsetof(FreecamState, live) == 112 &&
              offsetof(FreecamState, live_camera) == 204 &&
              sizeof(FreecamState) == 216,
              "freecam v1 ABI");

struct SweepReport {
    mc3_u32 magic, version, size, status;
    mc3_u32 frame, sweep_frame, cycles, freecam;
    mc3_u32 camera, session, samples, errors;
    float elapsed, offset, amplitude, duration;
    Matrix anchor;
    Vec3 current;
};

enum {
    SWEEP_MAGIC = 0x3157534Du, // bytes "MSW1"
    FREECAM_MAGIC = 0x4D433346u,
    FREECAM_SITE = 0x001A157Cu,
    SESSION_POINTER = 0x00619B10u,
    FRONTEND_STATE = 0x006144BCu,
    FRAME_DT = 0x00618E20u,
    LOSANGELES_SESSION = 5u,
    STATUS_WAIT_SESSION = 1u,
    STATUS_WAIT_CAMERA = 2u,
    STATUS_RUNNING = 3u,
    STATUS_DONE = 4u,
    STATUS_CONFLICT = 5u,
};

extern "C" volatile SweepReport g_frontend_mip_sweep;
volatile SweepReport g_frontend_mip_sweep __attribute__((aligned(16)));

static __attribute__((noinline)) SweepReport *report()
{
    return (SweepReport *)&g_frontend_mip_sweep;
}

static mc3_u32 word(mc3_u32 address)
{
    return *(volatile mc3_u32 *)address;
}

static bool pointer_ok(mc3_u32 address, mc3_u32 bytes = 4u)
{
    return address >= 0x00100000u && address <= 0x02000000u - bytes &&
           (address & 3u) == 0u;
}

static __attribute__((noinline)) float as_float(mc3_u32 value)
{
    __asm__ volatile("" : "+r"(value));
    union { mc3_u32 u; float f; } bits;
    bits.u = value;
    return bits.f;
}

static bool number(float value)
{
    return value > -as_float(0x49742400u) && value < as_float(0x49742400u);
}

static float dot(const Vec3 &a, const Vec3 &b)
{
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

static bool matrix_ok(const Matrix &matrix)
{
    const float *values = (const float *)&matrix;
    for (int index = 0; index < 12; ++index)
        if (!number(values[index])) return false;
    const float minimum = as_float(0x3F000000u);
    const float maximum = as_float(0x3FC00000u);
    const float right = dot(matrix.right, matrix.right);
    const float up = dot(matrix.up, matrix.up);
    const float back = dot(matrix.back, matrix.back);
    return right > minimum && right < maximum &&
           up > minimum && up < maximum &&
           back > minimum && back < maximum;
}

static __attribute__((noinline)) void sio_put(char value)
{
    *(volatile mc3_u8 *)0x1000F180u = (mc3_u8)value;
}

static void sio_hex(mc3_u32 value)
{
    for (int shift = 28; shift >= 0; shift -= 4) {
        const mc3_u32 digit = (value >> shift) & 15u;
        sio_put((char)(digit < 10u ? '0' + digit : 'A' + digit - 10u));
    }
}

static void sio_value(mc3_u32 value)
{
    sio_put(' ');
    sio_hex(value);
}

static void sample(SweepReport *state)
{
    sio_put('M'); sio_put('S'); sio_put('W'); sio_put('1');
    sio_value(state->frame);
    sio_value(state->status);
    sio_value(word((mc3_u32)&state->offset));
    sio_value(word((mc3_u32)&state->current.x));
    sio_value(word((mc3_u32)&state->current.y));
    sio_value(word((mc3_u32)&state->current.z));
    sio_put('\n');
    ++state->samples;
}

static FreecamState *find_freecam()
{
    const mc3_u32 instruction = word(FREECAM_SITE);
    if ((instruction & 0xFC000000u) != 0x0C000000u)
        return 0;
    const mc3_u32 gate = (instruction & 0x03FFFFFFu) << 2;
    if (!pointer_ok(gate, 24u) || word(gate + 8u) != FREECAM_MAGIC)
        return 0;
    const mc3_u32 address = word(gate + 12u);
    if (!pointer_ok(address, sizeof(FreecamState)))
        return 0;
    FreecamState *freecam = (FreecamState *)address;
    if (freecam->magic != FREECAM_MAGIC || freecam->version != 1u ||
        freecam->size != sizeof(FreecamState) || freecam->status != 1u)
        return 0;
    return freecam;
}

static void restore(SweepReport *state, FreecamState *freecam)
{
    freecam->matrix = state->anchor;
    freecam->velocity.x = 0.0f;
    freecam->velocity.y = 0.0f;
    freecam->velocity.z = 0.0f;
    freecam->active = 1u;
    state->offset = 0.0f;
    state->current = state->anchor.pos;
    state->status = STATUS_DONE;
    sample(state);
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    SweepReport *state = report();
    if (state->magic != SWEEP_MAGIC) {
        state->version = 1u;
        state->size = sizeof(SweepReport);
        state->status = STATUS_WAIT_SESSION;
        state->amplitude = as_float(0x42F00000u); // 120 world units
        state->duration = as_float(0x41C00000u);  // 24 seconds
        state->magic = SWEEP_MAGIC;
    }
    ++state->frame;
    if (state->status == STATUS_DONE || state->status == STATUS_CONFLICT)
        return;

    const mc3_u32 session = word(SESSION_POINTER);
    state->session = session;
    if (!pointer_ok(session) || word(session) != LOSANGELES_SESSION) {
        state->status = STATUS_WAIT_SESSION;
        return;
    }

    FreecamState *freecam = find_freecam();
    if (!freecam) {
        state->status = STATUS_WAIT_CAMERA;
        return;
    }
    state->freecam = (mc3_u32)freecam;
    if (word(FRONTEND_STATE) != 0u || !freecam->live_camera ||
        freecam->frame - freecam->live_frame > 3u ||
        !matrix_ok(freecam->live)) {
        state->status = STATUS_WAIT_CAMERA;
        state->errors = 0u;
        return;
    }
    if (state->camera && state->camera != freecam->live_camera) {
        state->camera = 0u;
        state->errors = 0u;
    }
    if (state->errors < 30u) {
        ++state->errors; // consecutive valid-matrix frames before arming
        state->status = STATUS_WAIT_CAMERA;
        return;
    }

    if (!state->camera || state->camera != freecam->live_camera) {
        freecam->matrix = freecam->live;
        freecam->camera = freecam->live_camera;
        freecam->fov = freecam->live_fov;
        freecam->velocity.x = 0.0f;
        freecam->velocity.y = 0.0f;
        freecam->velocity.z = 0.0f;
        freecam->active = 1u;
        state->anchor = freecam->matrix;
        state->camera = freecam->camera;
        state->elapsed = 0.0f;
        state->offset = 0.0f;
        state->current = state->anchor.pos;
        state->sweep_frame = 0u;
        state->cycles = 0u;
        state->status = STATUS_RUNNING;
        sample(state);
    }

    float dt = *(volatile float *)FRAME_DT;
    if (!(dt > as_float(0x38D1B717u) && dt < as_float(0x3DCCCCCDu)))
        dt = as_float(0x3C888889u); // 1/60
    state->elapsed += dt;
    ++state->sweep_frame;
    if (state->elapsed >= state->duration) {
        state->cycles = 2u;
        restore(state, freecam);
        return;
    }

    // Two identical 12-second cycles:
    // near 2 s, move away 4 s, far 2 s, move back 4 s.
    float phase = state->elapsed;
    if (phase >= as_float(0x41400000u)) {
        phase -= as_float(0x41400000u);
        state->cycles = 1u;
    }
    float factor;
    if (phase < as_float(0x40000000u)) {
        factor = 0.0f;
    } else if (phase < as_float(0x40C00000u)) {
        factor = (phase - as_float(0x40000000u)) * as_float(0x3E800000u);
    } else if (phase < as_float(0x41000000u)) {
        factor = 1.0f;
    } else {
        factor = (as_float(0x41400000u) - phase) * as_float(0x3E800000u);
    }
    state->offset = state->amplitude * factor;

    freecam->matrix = state->anchor;
    freecam->matrix.pos.x += state->anchor.back.x * state->offset;
    freecam->matrix.pos.y += state->anchor.back.y * state->offset;
    freecam->matrix.pos.z += state->anchor.back.z * state->offset;
    state->current = freecam->matrix.pos;
    freecam->velocity.x = 0.0f;
    freecam->velocity.y = 0.0f;
    freecam->velocity.z = 0.0f;
    freecam->camera = state->camera;
    freecam->active = 1u;

    if (state->sweep_frame % 300u == 0u)
        sample(state);
}
