// Publishes MC3 vehicle telemetry to a fixed EE-RAM mailbox for host tools.
// It draws nothing and has no hooks, so load it as `defer`: core.mod places it
// once, then invokes mod_main once per frame through the shared dispatcher.

#include "../../payload/mc3_mod.h"
#include "../mc3_telemetry_shared.h"

struct vec3 {
    float x;
    float y;
    float z;
};

enum {
    MCPLAYER_GET_PLAYER       = 0x00514530,
    MCRACER_OBSTACLE_VELOCITY = 0x0051B850,
    VEH_ENGINE_GET_RPM        = 0x00262948,
};

static int valid_game_pointer(mc3_u32 address)
{
    return address >= 0x00100000u && address < 0x02000000u && !(address & 3u);
}

static float square_root(float value)
{
    float result;
    __asm__ volatile ("sqrt.s %0, %1" : "=f"(result) : "f"(value));
    return result;
}

static int display_gear(mc3_u32 raw)
{
    // Runtime enum confirmed against the HUD: 0=R, 1=N, 2..8=gears 1..7.
    if (raw == 0)
        return -1;
    if (raw == 1)
        return 0;
    return raw <= 8 ? (int)raw - 1 : 0;
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_telemetry_public *const out =
        (volatile mc3_telemetry_public *)MC3_TELEMETRY_ADDR;

    if (out->magic != MC3_TELEMETRY_MAGIC ||
        out->version != MC3_TELEMETRY_VERSION ||
        out->size != MC3_TELEMETRY_SIZE) {
        out->magic = MC3_TELEMETRY_MAGIC;
        out->version = MC3_TELEMETRY_VERSION;
        out->size = MC3_TELEMETRY_SIZE;
        out->sequence = 0;
    }

    mc3_u32 sequence = out->sequence;
    if (sequence & 1u)
        ++sequence;
    out->sequence = sequence + 1u; // odd: host must retry

    out->flags = 0;
    out->speed_kmh = 0.0f;
    out->rpm = 0.0f;
    out->gear = 0;
    out->velocity_x = 0.0f;
    out->velocity_y = 0.0f;
    out->velocity_z = 0.0f;
    out->player_address = 0;
    out->reserved = 0;

    const mc3_u32 player = MC3_CALL1(mc3_u32, MCPLAYER_GET_PLAYER, int)(0);
    if (valid_game_pointer(player)) {
        out->flags |= MC3_TELEMETRY_PLAYER;
        out->player_address = player;

        const vec3 *velocity = (const vec3 *)MC3_CALL1(
            mc3_u32, MCRACER_OBSTACLE_VELOCITY, mc3_u32)(player);
        if (valid_game_pointer((mc3_u32)velocity)) {
            out->velocity_x = velocity->x;
            out->velocity_y = velocity->y;
            out->velocity_z = velocity->z;
            out->speed_kmh = square_root(
                velocity->x * velocity->x +
                velocity->y * velocity->y +
                velocity->z * velocity->z) * 3.6f;
            out->flags |= MC3_TELEMETRY_VELOCITY;
        }

        // Same chain used by hudTachometer::Init and hudGear::Display:
        // player+14 -> owner, owner+4 -> vehSim, then engine/transmission.
        const mc3_u32 owner = *(volatile mc3_u32 *)(player + 0x14);
        if (valid_game_pointer(owner)) {
            const mc3_u32 vehicle = *(volatile mc3_u32 *)(owner + 0x04);
            if (valid_game_pointer(vehicle)) {
                const mc3_u32 engine = *(volatile mc3_u32 *)(vehicle + 0x10);
                if (valid_game_pointer(engine)) {
                    const float rpm = MC3_CALL1(
                        float, VEH_ENGINE_GET_RPM, mc3_u32)(engine);
                    if (rpm >= 0.0f && rpm < 100000.0f) {
                        out->rpm = rpm;
                        out->flags |= MC3_TELEMETRY_ENGINE;
                    }
                }

                const mc3_u32 transmission =
                    *(volatile mc3_u32 *)(vehicle + 0x14);
                if (valid_game_pointer(transmission)) {
                    const mc3_u32 raw =
                        *(volatile mc3_u32 *)(transmission + 0x14);
                    if (raw <= 8) {
                        out->gear = display_gear(raw);
                        out->flags |= MC3_TELEMETRY_TRANSMISSION;
                    }
                }
            }
        }
    }

    out->sequence = sequence + 2u; // even: complete snapshot
}
