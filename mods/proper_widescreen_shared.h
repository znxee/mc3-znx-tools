#ifndef MC3_PROPER_WIDESCREEN_SHARED_H
#define MC3_PROPER_WIDESCREEN_SHARED_H

// The last 16 bytes of MODREPORT. city_place ends at 0x61CFC8 and menu_row's
// trace ends at 0x61CFF0, so this completes the existing partition without
// overlapping either module.
#define WS_MAILBOX_ADDR 0x0061CFF0u
#define WS_MAILBOX_MAGIC 0x4D433357u /* 'MC3W' */

enum ws_profile {
    WS_PROFILE_4X3 = 0,
    WS_PROFILE_16X9 = 1,
    WS_PROFILE_21X9 = 2,
    WS_PROFILE_PENDING = 0xFFFFFFFFu,
};

struct ws_mailbox {
    volatile mc3_u32 magic;
    volatile mc3_u32 requested;
    volatile mc3_u32 current;
    volatile mc3_u32 switches;
};

static inline volatile ws_mailbox *ws_get_mailbox()
{
    return (volatile ws_mailbox *)WS_MAILBOX_ADDR;
}

static inline int ws_profile_valid(mc3_u32 profile)
{
    return profile <= (mc3_u32)WS_PROFILE_21X9;
}

#endif
