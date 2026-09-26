# -*- coding: utf-8 -*-
"""Final bisection: the block count or the block contents?

Reuses the from-scratch build of `test_mesh_new.py`, which ALREADY PASSED in the
game with 11 blocks. The only difference between the variants is what goes into
the list.

The target `a_inst_low_ghto_06x` has 11 blocks in 6 groups:
    g0[40] g1[17] g2[48,46,4] g3[46,39] g4[44,47,15] g5[44]

    --mode clone   12 blocks, the extra one is a BYTE-FOR-BYTE COPY of the
                   4-vert block (group 2). No make_block. Only the count changes.
    --mode swap    11 blocks: the 48-vert one becomes 2 pieces (make_block) and
                   the 4-vert one goes. Same count, new content.
    --mode fewer   10 blocks: the 4-vert one goes. No make_block.

Reading:
    clone hangs + swap passes  -> it is the count, nothing else.
    clone passes + swap hangs  -> it is make_block on a piece of strip.
    fewer hangs                -> the count has to match the original
                                  (something outside keeps the number).
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
    ap.add_argument('--mode', choices=['clone', 'swap', 'fewer'], required=True)
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
    hi08 = struct.unpack_from('<H', d, old + 0x0A)[0]
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
            odd = bytes(d[blk + 0x0C:blk + 0x0E]) == b'\x27\x02'
            entries_.append(dict(bytes=bytes(d[blk:blk + qw * 16]), nv=nv,
                              pos=pos, uv=uv, odd=odd, sc=sc))
        groups.append(entries_)

    # ---- find the 4-vert block and the 48-vert one (both in group 2) ----
    gi4 = ji4 = gi48 = ji48 = None
    for gi, gr in enumerate(groups):
        for ji, it in enumerate(gr):
            if it['nv'] == 4 and gi4 is None:
                gi4, ji4 = gi, ji
            if it['nv'] == 48 and gi48 is None:
                gi48, ji48 = gi, ji
    assert gi4 is not None and gi48 is not None

    if a.mode == 'clone':
        it = groups[gi4][ji4]
        groups[gi4].insert(ji4 + 1, dict(it))          # byte-for-byte copy
        print('clone: duplicated the 4-vert block of group %d' % gi4)
    elif a.mode == 'fewer':
        groups[gi4].pop(ji4)
        print('fewer: removed the 4-vert block of group %d' % gi4)
    else:  # swap
        big = groups[gi48][ji48]
        n = big['nv']
        step = ((n // 2) + 1) // 2 * 2                # even, keeps the parity
        ped = []
        s = 0
        while s < n - 2:
            p2, u2 = big['pos'][s:s + step + 2], big['uv'][s:s + step + 2]
            if len(p2) >= 3:
                ped.append(dict(pos=p2, uv=u2, odd=big['odd'], sc=big['sc'],
                                nv=len(p2), bytes=None))
            s += step
        groups[gi48][ji48:ji48 + 1] = ped
        # drop the 4-vert one so the count goes back to 11
        for gi, gr in enumerate(groups):
            for ji, it in enumerate(gr):
                if it['nv'] == 4 and it['bytes'] is not None:
                    gr.pop(ji)
                    break
            else:
                continue
            break
        print('swap: 48 verts -> %d pieces, removed the 4-vert one' % len(ped))

    # ---------------- build the whole region, contiguous ----------------
    while len(d) % 16:
        d.append(0xCD)
    reg = len(d)

    mat_bytes = b''.join(struct.pack('<H', m) for m in materials)
    mat_off = reg
    mesh_off = mat_off + len(mat_bytes)
    adjust = (16 - mesh_off % 16) % 16
    mat_off += adjust
    mesh_off = mat_off + len(mat_bytes)

    gtab = mesh_off + 0x20 + 16
    current = gtab + 8 * ngrp

    regions, pointers = [], []
    for gr in groups:
        blobs = [it['bytes'] if it['bytes'] is not None
                 else conv.make_block(it['pos'], it['uv'], it['sc'], it['odd']) for it in gr]
        list_off = current + 16
        blocks_off = list_off + len(pad16(b'\x00' * (8 * len(gr))))
        ent = bytearray()
        pos = blocks_off
        for it, blob in zip(gr, blobs):
            ent += struct.pack('<IHH', V(pos), len(blob) // 16, it['nv'])
            pos += len(blob)
        body = pad16(struct.pack('<I', len(gr))) + pad16(bytes(ent)) + b''.join(blobs)
        regions.append(body)
        pointers.append((V(list_off), len(gr)))
        current += len(body)

    out_path = bytearray()
    out_path += bytes([0xCD]) * adjust
    out_path += mat_bytes
    m = bytearray(0x20)
    struct.pack_into('<I', m, 0x00, MESH_TAG)
    struct.pack_into('<H', m, 0x08, ngrp)
    struct.pack_into('<H', m, 0x0A, hi08)
    struct.pack_into('<I', m, 0x0C, V(mat_off))
    struct.pack_into('<I', m, 0x10, V(gtab))
    struct.pack_into('<I', m, 0x14, f14)
    struct.pack_into('<I', m, 0x18, f18)
    struct.pack_into('<I', m, 0x1C, f1c)
    out_path += bytes(m)
    out_path += pad16(struct.pack('<I', ngrp))
    for p, n in pointers:
        out_path += struct.pack('<IHH', p, n, 0xCDCD)
    out_path += b''.join(regions)

    d.extend(out_path)
    while len(d) % 16:
        d.append(0xCD)
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    struct.pack_into('<I', d, vt + 0x14, V(mesh_off))

    out = _out_path.path( 'test_%s_atlanta_midnight_clear.pck' % a.mode)
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    nb = sum(len(x) for x in groups)
    print('   new mesh @%#x | %d blocks in %d groups | %s' % (mesh_off, nb, ngrp,
                                                               [len(x) for x in groups]))
    print('   %s  (%d bytes changed from the original)'
          % (os.path.basename(out), sum(1 for x, y in zip(orig, d) if x != y)))


if __name__ == '__main__':
    main()
