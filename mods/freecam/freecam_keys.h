// GPL-3.0. modderG, 2026-09-05: held USB keys, independent of text-repeat events.
#ifndef MC3_FREECAM_KEYS_H
#define MC3_FREECAM_KEYS_H
enum Key {
    TOGGLE=1u<<0, FORWARD=1u<<1, BACK=1u<<2, LEFT=1u<<3, RIGHT=1u<<4,
    UP=1u<<5, DOWN=1u<<6, FAST=1u<<7, SLOW=1u<<8, ROLL_L=1u<<9,
    ROLL_R=1u<<10, LEVEL=1u<<11, WIDE=1u<<12, NARROW=1u<<13,
    SPEED_UP=1u<<14, SPEED_DOWN=1u<<15, SMOOTH=1u<<16,
    LOOK_L=1u<<17, LOOK_R=1u<<18, LOOK_U=1u<<19, LOOK_D=1u<<20,
    RECENTER=1u<<21
};
static unsigned int fc_usb_key(unsigned int usage) {
    switch (usage) {
    case 0x13: return TOGGLE; // P
    case 0x1A: return FORWARD; // W
    case 0x16: return BACK; // S
    case 0x04: return LEFT; // A
    case 0x07: return RIGHT; // D
    case 0x15: return UP; // R
    case 0x09: return DOWN; // F
    case 0x14: return ROLL_L; // Q
    case 0x08: return ROLL_R; // E
    case 0x12: return LEVEL; // O
    case 0x1D: return WIDE; // Z
    case 0x1B: return NARROW; // X
    case 0x10: return SPEED_UP; // M
    case 0x11: return SPEED_DOWN; // N
    case 0x17: return SMOOTH; // T
    case 0x50: return LOOK_L;
    case 0x4F: return LOOK_R;
    case 0x52: return LOOK_U;
    case 0x51: return LOOK_D;
    case 0x0B: return RECENTER; // H
    default: return 0;
    }
}
static unsigned int fc_usb_held(unsigned int modifiers,
                               const volatile unsigned short *keys,
                               unsigned int count) {
    if (count>64) return 0;
    unsigned int result=0;
    if (modifiers&0x22u) result|=FAST; // Either Shift
    if (modifiers&0x11u) result|=SLOW; // Either Ctrl
    for (unsigned int i=0;i<count;++i) result|=fc_usb_key(keys[i]);
    return result;
}
#endif
