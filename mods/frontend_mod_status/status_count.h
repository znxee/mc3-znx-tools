#ifndef MC3_FRONTEND_STATUS_COUNT_H
#define MC3_FRONTEND_STATUS_COUNT_H

// Trace layouts are owned by mc3_inject.py and core.cpp. Read only.
// Count successful placements, not requested entries or frame callbacks.
static unsigned status_count(unsigned boot_magic, unsigned boot_loaded,
                             unsigned core_magic, unsigned core_loaded)
{
    unsigned count = 0;
    if (boot_magic == 0x4D433354u && boot_loaded <= 999u)
        count += boot_loaded;
    if (core_magic == 0x4D433352u && core_loaded <= 999u)
        count += core_loaded;
    return count > 999u ? 999u : count;
}

// Buffer capacity: 21 u16s, including terminator. No libc or division sequence.
static void status_text(unsigned count, unsigned short *text)
{
    text[0]='M'; text[1]='o'; text[2]='d'; text[3]='s'; text[4]=' ';
    text[5]='l'; text[6]='o'; text[7]='a'; text[8]='d'; text[9]='e';
    text[10]='d'; text[11]=':'; text[12]=' ';
    if (count > 999u) count = 999u;
    volatile unsigned remaining = count;
    unsigned hundreds=0, tens=0, i=13;
    while (remaining >= 100u) { remaining -= 100u; ++hundreds; }
    while (remaining >= 10u) { remaining -= 10u; ++tens; }
    if (hundreds) text[i++] = (unsigned short)('0'+hundreds);
    if (hundreds || tens) text[i++] = (unsigned short)('0'+tens);
    text[i++] = (unsigned short)('0'+remaining);
    text[i] = 0;
}
#endif
