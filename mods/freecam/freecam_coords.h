// GPL-3.0. Compact fixed-point coordinate formatting without libc.
#ifndef MC3_FREECAM_COORDS_H
#define MC3_FREECAM_COORDS_H
static void fc_format_coord(unsigned short axis, int tenths,
                            unsigned short *out)
{
    bool negative = tenths < 0;
    unsigned remaining = negative ? (unsigned)(-tenths) : (unsigned)tenths;
    if (remaining > 999999u) remaining = 999999u;
    if (!remaining) negative = false;
    unsigned d0=0,d1=0,d2=0,d3=0,d4=0;
    while (remaining >= 100000u) { remaining -= 100000u; ++d0; }
    while (remaining >= 10000u)  { remaining -= 10000u;  ++d1; }
    while (remaining >= 1000u)   { remaining -= 1000u;   ++d2; }
    while (remaining >= 100u)    { remaining -= 100u;    ++d3; }
    while (remaining >= 10u)     { remaining -= 10u;     ++d4; }
    unsigned i=0;
    out[i++]=axis; out[i++]=':'; out[i++]=' ';
    if (negative) out[i++]='-';
    bool started=false;
    if (d0) { out[i++]=(unsigned short)('0'+d0); started=true; }
    if (started || d1) { out[i++]=(unsigned short)('0'+d1); started=true; }
    if (started || d2) { out[i++]=(unsigned short)('0'+d2); started=true; }
    if (started || d3) { out[i++]=(unsigned short)('0'+d3); }
    out[i++]=(unsigned short)('0'+d4);
    out[i++]='.';
    out[i++]=(unsigned short)('0'+remaining);
    out[i]=0;
}
#endif
