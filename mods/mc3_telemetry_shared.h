#ifndef MC3_RESHade_TELEMETRY_SHARED_H
#define MC3_RESHade_TELEMETRY_SHARED_H

// Public guest-to-host telemetry ABI.  This 48-byte block occupies the proven
// zero gap immediately after the loader state (0x61C980..0x61C98F) and before
// the payload's private diagnostics at 0x61C9C0.  Keep mc3_inject.py, the PINE
// reader and the ReShade add-on in sync with this exact layout.
#define MC3_TELEMETRY_ADDR    0x0061C990u
#define MC3_TELEMETRY_MAGIC   0x4D433354u /* 'MC3T' */
#define MC3_TELEMETRY_VERSION 1u
#define MC3_TELEMETRY_SIZE    48u

enum mc3_telemetry_flags {
    MC3_TELEMETRY_PLAYER       = 1u << 0,
    MC3_TELEMETRY_VELOCITY     = 1u << 1,
    MC3_TELEMETRY_ENGINE       = 1u << 2,
    MC3_TELEMETRY_TRANSMISSION = 1u << 3,
};

struct mc3_telemetry_public {
    mc3_u32 magic;
    mc3_u16 version;
    mc3_u16 size;
    mc3_u32 sequence;       // odd while writing, even when snapshot is stable
    mc3_u32 flags;
    float speed_kmh;
    float rpm;
    int gear;               // -1=R, 0=N, 1..7=forward; valid with flag bit 3
    float velocity_x;
    float velocity_y;
    float velocity_z;
    mc3_u32 player_address; // diagnostic only; never dereference on the host
    mc3_u32 reserved;
};

#endif
