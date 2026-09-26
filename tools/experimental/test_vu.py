# -*- coding: utf-8 -*-
"""The same from-scratch build as `test_mesh_new.py`, but with the VU1 DOUBLE
BUFFER fixed.

What was wrong in every earlier test: the four block fields I treated as "strip
parity" are a VU1 memory address, and retail alternates the two halves
A,B,A,B... by the POSITION OF THE BLOCK IN THE GROUP'S LIST (785 of atlanta's
786 groups). I copied the value from the source block; when the list changed
size the alternation broke and the DMA overwrote the half the VU was still
running.

Here every block is re-encoded with `odd = (j % 2 == 1)`.

    python test_vu.py --mode faithful 11 blocks (control, has to keep passing)
    python test_vu.py --mode split2   12 blocks (the one that hung)
    python test_vu.py --mode clone    12 blocks, the extra one copies the 4-vert one
    python test_vu.py --mode fewer    10 blocks
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import struct
import importlib.util as _iu
_SP = _iu.spec_from_file_location(
    'mc3_output', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '..', 'mc3_output.py'))
_out_path = _iu.module_from_spec(_SP)
_SP.loader.exec_module(_out_path)


HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('conv', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc2_to_mc3.py'))
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)

SRC = os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.pck')
TARGET = 'a_inst_low_ghto_06x'
MESH_TAG = 0x007A1E18


def pad16(b, ch=0xCD):
    return b + bytes([ch]) * ((16 - len(b) % 16) % 16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['faithful', 'split2', 'clone', 'fewer'], default='split2')
    a = ap.parse_args()

    d = bytearray(open(SRC, 'rb').read())
    va = struct.unpack_from('<I', d, 0)[0]
    base = va - 0x80
    F = lambda v: v - base
    V = lambda o: o + base
    U = lambda o: struct.unpack_from('<I', d, o)[0]

    for o in range(0x80, len(d) - 0x80, 4):
        if U(o) == 0x7A0090 and bytes(d[o + 0x48:o + 0x70]).split(b'\0')[0] == TARGET.encode():
            vt = o
            break
    old = F(U(vt + 0x14))
    ngrp = struct.unpack_from('<H', d, old + 8)[0]
    hi0a = struct.unpack_from('<H', d, old + 0x0A)[0]
    f14, f18, f1c = U(old + 0x14), U(old + 0x18), U(old + 0x1C)
    matf = F(U(old + 0x0C))
    materials = [struct.unpack_from('<H', d, matf + 2 * i)[0] for i in range(ngrp)]
    g = F(U(old + 0x10))

    groups = []
    for k in range(ngrp):
        p, c, _ = struct.unpack_from('<IHH', d, g + 8 * k)
        lf = F(p)
        entries_ = []
        for j in range(c):
            bp, qw, nv = struct.unpack_from('<IHH', d, lf + 8 * j)
            blk = F(bp)
            sc = struct.unpack_from('<f', d, blk + 4)[0]
            pos = [tuple(x * sc for x in struct.unpack_from('<hhh', d, blk + 0x10 + 6 * i))
                   for i in range(nv)]
            u0 = blk + 0x10 + 6 * nv
            while u0 % 4:
                u0 += 1
            uv = [tuple(x / 4096.0 for x in struct.unpack_from('<hh', d, u0 + 4 + 4 * i))
                  for i in range(nv)]
            buf = struct.unpack_from('<H', d, blk + 0x0C)[0]
            entries_.append(dict(nv=nv, pos=pos, uv=uv, sc=sc, buf=buf))
        expected_items = [256 if i % 2 == 0 else 551 for i in range(c)]
        assert [x['buf'] for x in entries_] == expected_items, 'group %d breaks the alternation' % k
        groups.append(entries_)

    gi4 = ji4 = gi48 = ji48 = None
    for gi, gr in enumerate(groups):
        for ji, it in enumerate(gr):
            if it['nv'] == 4 and gi4 is None:
                gi4, ji4 = gi, ji
            if it['nv'] == 48 and gi48 is None:
                gi48, ji48 = gi, ji

    if a.mode == 'clone':
        groups[gi4].insert(ji4 + 1, dict(groups[gi4][ji4]))
    elif a.mode == 'fewer':
        groups[gi4].pop(ji4)
    elif a.mode == 'split2':
        big = groups[gi48][ji48]
        n = big['nv']
        step = ((n // 2) + 1) // 2 * 2
        ped, s = [], 0
        while s < n - 2:
            p2, u2 = big['pos'][s:s + step + 2], big['uv'][s:s + step + 2]
            if len(p2) >= 3:
                ped.append(dict(pos=p2, uv=u2, sc=big['sc'], nv=len(p2), buf=None))
            s += step
        groups[gi48][ji48:ji48 + 1] = ped

    while len(d) % 16:
        d.append(0xCD)
    mat_off = len(d)
    mesh_off = mat_off + 2 * ngrp
    adjust = (16 - mesh_off % 16) % 16
    mat_off += adjust
    mesh_off = mat_off + 2 * ngrp
    gtab = mesh_off + 0x20 + 16
    current = gtab + 8 * ngrp

    regions, pointers = [], []
    for gr in groups:
        # <<< the fix: the buffer half comes from the index, never from the source >>>
        blobs = [conv.make_block(it['pos'], it['uv'], it['sc'], j % 2 == 1)
                 for j, it in enumerate(gr)]
        list_off = current + 16
        blocks_off = list_off + len(pad16(b'\x00' * (8 * len(gr))))
        ent, pos = bytearray(), blocks_off
        for it, blob in zip(gr, blobs):
            ent += struct.pack('<IHH', V(pos), len(blob) // 16, it['nv'])
            pos += len(blob)
        regions.append(pad16(struct.pack('<I', len(gr))) + pad16(bytes(ent)) + b''.join(blobs))
        pointers.append((V(list_off), len(gr)))
        current += len(regions[-1])

    out_path = bytearray(bytes([0xCD]) * adjust)
    for m in materials:
        out_path += struct.pack('<H', m)
    m = bytearray(0x20)
    struct.pack_into('<I', m, 0x00, MESH_TAG)
    struct.pack_into('<H', m, 0x08, ngrp)
    struct.pack_into('<H', m, 0x0A, hi0a)
    struct.pack_into('<I', m, 0x0C, V(mat_off))
    struct.pack_into('<I', m, 0x10, V(gtab))
    struct.pack_into('<I', m, 0x14, f14)
    struct.pack_into('<I', m, 0x18, f18)
    struct.pack_into('<I', m, 0x1C, f1c)
    out_path += bytes(m) + pad16(struct.pack('<I', ngrp))
    for p, n in pointers:
        out_path += struct.pack('<IHH', p, n, 0xCDCD)
    out_path += b''.join(regions)

    d.extend(out_path)
    while len(d) % 16:
        d.append(0xCD)
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    struct.pack_into('<I', d, vt + 0x14, V(mesh_off))

    out = _out_path.path( 'test_vu_%s_atlanta_midnight_clear.pck' % a.mode)
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    print('%-8s %2d blocks %s | buffers %s'
          % (a.mode, sum(len(x) for x in groups), [len(x) for x in groups],
             ['A' if j % 2 == 0 else 'B' for gr in groups for j in range(len(gr))]))
    print('         %s  (%d bytes changed from the original)'
          % (os.path.basename(out), sum(1 for x, y in zip(orig, d) if x != y)))


if __name__ == '__main__':
    main()
