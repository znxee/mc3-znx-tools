#!/usr/bin/env python3
"""Fog factor per textured draw group in a PCSX2 GS dump.

    python mc3_gsdump_fog.py DUMP.gs.zst [--field N] [--top 40]

A draw with fog on (FGE=1, from PRIM or PRMODE) takes the colour (F/255)*texel +
(1-F/255)*FOGCOL, F being the fog byte of each XYZF2/XYZF3 vertex. F = 0 for
every vertex means the draw comes out as FOGCOL, whatever its texture: the
grey (night: dark) ambient cars of the added cities, whose VU fog vector q32
the city renderer's bootstrap had zeroed (2026-10-06 PSP, 2026-10-09 MC2).

Per group (target, texture, FGE) it prints the vertex count, how many had
F = 0 and the F range, then the groups with fog on and F always 0 - the ones
drawn as the fog colour. FOGCOL (register 0x3D) is printed per field.
Reads the dump with mc3_gsdump.load.
"""
import argparse
import struct
from collections import defaultdict

import mc3_gsdump

FOGCOL = 0x3D


def analyse(dump):
    st = defaultdict(int)
    fields = [dict(groups=defaultdict(lambda: [0, 0, 255, 0]), fogcol=set())]
    cur = fields[0]

    st[0x1A] = 1                                   # PRMODECONT: AC=1 at reset
    fog = [0]           # the FOG register: XYZ2/XYZ3 draws use it as it stands

    def kick(f):
        # attributes from PRIM, or from PRMODE when PRMODECONT.AC = 0 (MC3
        # sets them that way: rmcState::SetPRMODE)
        prim = st[0x00] if st[0x1A] & 1 else st[0x1B]
        ctx = (prim >> 9) & 1
        fge, tme = (prim >> 5) & 1, (prim >> 4) & 1
        tx = st[0x06 + ctx]
        fr = st[0x4C + ctx]
        key = 'FBP %03X | %s | FGE%d' % (
            fr & 0x1FF, ('TEX %04X CBP %04X PSM%02X' % (tx & 0x3FFF, (tx >> 37) & 0x3FFF, (tx >> 20) & 0x3F))
            if tme else 'notex', fge)
        g = cur['groups'][key]
        g[0] += 1
        if f == 0:
            g[1] += 1
        g[2] = min(g[2], f)
        g[3] = max(g[3], f)

    for p in dump['packets']:
        if p[0] == 'V':
            cur = dict(groups=defaultdict(lambda: [0, 0, 255, 0]), fogcol=set())
            fields.append(cur)
            continue
        if p[0] != 'T':
            continue
        b = p[1]
        o = 0
        while o + 16 <= len(b):
            lo, hi = struct.unpack_from('<QQ', b, o)
            o += 16
            nloop = lo & 0x7FFF
            pre = (lo >> 46) & 1
            flg = (lo >> 58) & 3
            nreg = (lo >> 60) & 0xF or 16
            if pre and flg == 0:
                st[0x00] = (lo >> 47) & 0x7FF
            regs = [(hi >> (4 * i)) & 0xF for i in range(nreg)]
            if flg == 0:                                   # PACKED
                for _ in range(nloop):
                    for r in regs:
                        a, c = struct.unpack_from('<QQ', b, o)
                        o += 16
                        if r == 0xE:
                            st[c & 0xFF] = a
                            if (c & 0xFF) == 0x0A:
                                fog[0] = (a >> 56) & 0xFF
                            if (c & 0xFF) == FOGCOL:
                                cur['fogcol'].add(a & 0xFFFFFF)
                        elif r == 0x0:
                            st[0x00] = a & 0x7FF
                        elif r == 0xA:                     # FOG
                            fog[0] = (c >> 36) & 0xFF
                        elif r in (0x4, 0xC):              # XYZF2/3
                            fog[0] = (c >> 36) & 0xFF
                            if not (c >> 47) & 1:
                                kick(fog[0])
                        elif r in (0x5, 0xD):              # XYZ2/3
                            kick(fog[0])
            elif flg == 1:                                 # REGLIST
                n = nloop * nreg
                for i in range(n):
                    a = struct.unpack_from('<Q', b, o + 8 * i)[0]
                    r = regs[i % nreg]
                    if r == 0x0:
                        st[0x00] = a & 0x7FF
                    elif r == 0xA:
                        fog[0] = (a >> 56) & 0xFF
                    elif r in (0x4, 0xC):
                        fog[0] = (a >> 56) & 0xFF
                        kick(fog[0])
                    elif r in (0x5, 0xD):
                        kick(fog[0])
                o += 8 * (n + (n & 1))
            else:                                          # IMAGE
                o += 16 * nloop
    return fields


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('dump')
    ap.add_argument('--field', type=int, default=None, help='only this field (default: the busiest)')
    ap.add_argument('--top', type=int, default=40)
    a = ap.parse_args()
    fields = analyse(mc3_gsdump.load(a.dump))
    if a.field is None:
        a.field = max(range(len(fields)), key=lambda i: sum(g[0] for g in fields[i]['groups'].values()))
    f = fields[a.field]
    print('field %d of %d, FOGCOL %s' % (a.field, len(fields),
                                          ' '.join('%06X' % c for c in sorted(f['fogcol'])) or '-'))
    rows = sorted(f['groups'].items(), key=lambda kv: -kv[1][0])
    fog_on = [kv for kv in rows if kv[0].endswith('FGE1')]
    print('groups %d, with fog on %d' % (len(rows), len(fog_on)))
    for key, (n, zero, lo, hi) in fog_on[:a.top]:
        print('  %6d vtx  F=0 %6d  F %3d..%3d  %s' % (n, zero, lo, hi, key))
    dead = [kv for kv in fog_on if kv[1][1] == kv[1][0] and not kv[0].split('|')[1].strip() == 'notex']
    print('textured, fog on, F always 0 (drawn as FOGCOL): %d groups, %d vertices'
          % (len(dead), sum(kv[1][0] for kv in dead)))
    for key, (n, zero, lo, hi) in dead[:a.top]:
        print('  %6d vtx  %s' % (n, key))


if __name__ == '__main__':
    main()
