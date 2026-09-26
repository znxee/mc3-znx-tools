# -*- coding: utf-8 -*-
"""Bisection: test ONLY the plumbing (append + table + repoint), without MC2.

Takes the target model's original blocks, copies each one, appends them at the
end and rebuilds the group table pointing at the new ones. The geometry is
identical to the original -- the round trip already proved it comes out byte
for byte the same -- so the only thing under test is the plumbing.

  * if THIS loads, the plumbing is right and the problem is the MC2 data
    (count, scale, building size);
  * if THIS hangs, the bug is in my append/table/repoint and the conversion
    does not even come into it.

    python test_plumbing.py
"""
from __future__ import annotations

import os
import struct

SRC = os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.pck')
TARGET = 'a_inst_low_ghto_06x'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'test_plumbing_atlanta_midnight_clear.pck')

MODEL_VTABLE = 0x7A0090
VTABLE_AT = 0x28


def main():
    d = bytearray(open(SRC, 'rb').read())
    va = struct.unpack_from('<I', d, 0)[0]
    base = va - 0x80
    F = lambda v: v - base
    V = lambda o: o + base
    U = lambda o: struct.unpack_from('<I', d, o)[0]

    vt = None
    for o in range(0x80, len(d) - 0x80, 4):
        if U(o) == MODEL_VTABLE:
            if bytes(d[o + 0x48:o + 0x70]).split(b'\0')[0].decode('latin1', 'replace') == TARGET:
                vt = o
                break
    mesh = F(U(vt + 0x14))
    ngrp = struct.unpack_from('<H', d, mesh + 8)[0]
    g = F(U(mesh + 0x10))

    # exact copy of each block, at its DECLARED size (tail and padding included)
    groups = []
    for k in range(ngrp):
        p, c, _pad = struct.unpack_from('<IHH', d, g + 8 * k)
        lf = F(p)
        entries = []
        for j in range(c):
            bp, qw, nv = struct.unpack_from('<IHH', d, lf + 8 * j)
            blk = F(bp)
            entries.append((bytes(d[blk:blk + qw * 16]), qw, nv))
        groups.append(entries)
    print('target %s: %d groups, %d blocks, %d bytes of geometry'
          % (TARGET, ngrp, sum(len(x) for x in groups),
             sum(len(b) for x in groups for b, _q, _n in x)))

    def append(blob, align=16):
        while len(d) % align:
            d.extend(b'\xCD')
        at = len(d)
        d.extend(blob)
        while len(d) % align:
            d.extend(b'\xCD')
        struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
        return at

    lists = []
    for entries in groups:
        new_ones = []
        for blob, qw, nv in entries:
            at = append(blob)
            new_ones.append((V(at), qw, nv))
        lb = bytearray()
        for va_, qw, nv in new_ones:
            lb += struct.pack('<IHH', va_, qw, nv)
        lists.append((append(bytes(lb)), len(new_ones)))

    tab = bytearray()
    for at, n in lists:
        tab += struct.pack('<IHH', V(at), n, 0xCDCD)
    grp_at = append(bytes(tab))
    struct.pack_into('<I', d, mesh + 0x10, V(grp_at))

    open(OUT, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    changed = sum(1 for x, y in zip(orig, d) if x != y)
    print('output: %s' % OUT)
    print('   %d -> %d bytes | %d bytes changed in the original region'
          % (len(orig), len(d), changed))
    print('   (only the body size in the header and the mesh+0x10 pointer)')


if __name__ == '__main__':
    main()
