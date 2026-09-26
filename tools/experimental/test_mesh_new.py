# -*- coding: utf-8 -*-
"""Build a WHOLE CITY MESH from scratch and only repoint the model at it.

The user's idea: instead of patching the tables of the existing mesh (which
always hung), build everything anew from the `18 1E 7A 00` tag and swap only
the model's LOD0 pointer.

Retail keeps the whole mesh as ONE CONTIGUOUS REGION, in this exact order
(measured on atlanta's `a_inst_low_ghto_06x`):

    [material table: ngrp x u16]          <- ends exactly where the mesh starts
    [mesh struct: 0x20 bytes]
    [group table header: u32 ngrp + 12 bytes of 0xCD]
    [group table: ngrp x 8]
    per group:
        [list header: u32 count + 12 of 0xCD]
        [list: count x 8  (ptr | u16 qwords | u16 vertices)]
        [pad to 16]
        [block 0][block 1]...              <- back to back

The same format `mesh_psptops2_v1.1.py` (ZNXee) uses for vehicles.

    python test_mesh_new.py               # faithful copy (11 blocks, control)
    python test_mesh_new.py --split 2     # 12 blocks
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
    ap.add_argument('--split', type=int, default=0)
    ap.add_argument('--out-path', default=None)
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
    print('original mesh @%#x: %d groups, material %s' % (old, ngrp, materials))

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

    if a.split > 1:
        largest = max((it for gr in groups for it in gr), key=lambda x: x['nv'])
        gi = next(i for i, gr in enumerate(groups) if largest in gr)
        gr = groups[gi]
        idx = gr.index(largest)
        n = largest['nv']
        step = max(4, ((n + a.split - 1) // a.split + 1) // 2 * 2)
        ped = []
        s = 0
        while s < n - 2:
            p2, u2 = largest['pos'][s:s + step + 2], largest['uv'][s:s + step + 2]
            if len(p2) >= 3:
                ped.append(dict(pos=p2, uv=u2, odd=largest['odd'], sc=largest['sc'],
                                nv=len(p2), bytes=None))
            s += step
        gr[idx:idx + 1] = ped
        print('   block of %d vertices (group %d) -> %d pieces' % (n, gi, len(ped)))

    # ---------------- build the whole region, contiguous ----------------
    while len(d) % 16:
        d.append(0xCD)
    reg = len(d)                       # start of the new region

    mat_bytes = b''.join(struct.pack('<H', m) for m in materials)
    # the mesh has to land 16-aligned, and the material table ends at it
    mat_off = reg
    mesh_off = mat_off + len(mat_bytes)
    adjust = (16 - mesh_off % 16) % 16
    mat_off += adjust                  # push the table so the mesh aligns
    mesh_off = mat_off + len(mat_bytes)

    gtab_hdr = mesh_off + 0x20
    gtab = gtab_hdr + 16
    current = gtab + 8 * ngrp

    regions = []
    pointers = []
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
    struct.pack_into('<I', m, 0x04, 0)
    struct.pack_into('<H', m, 0x08, ngrp)
    struct.pack_into('<H', m, 0x0A, hi08)
    struct.pack_into('<I', m, 0x0C, V(mat_off))
    struct.pack_into('<I', m, 0x10, V(gtab))
    struct.pack_into('<I', m, 0x14, f14)          # 4-entry table: reuse the original
    struct.pack_into('<I', m, 0x18, f18)
    struct.pack_into('<I', m, 0x1C, f1c)
    out_path += bytes(m)
    out_path += pad16(struct.pack('<I', ngrp))
    gt = bytearray()
    for p, n in pointers:
        gt += struct.pack('<IHH', p, n, 0xCDCD)
    out_path += bytes(gt)
    out_path += b''.join(regions)

    d.extend(out_path)
    while len(d) % 16:
        d.append(0xCD)
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    struct.pack_into('<I', d, vt + 0x14, V(mesh_off))     # <<< the only change to the original

    out = a.out_path or _out_path.path( 'test_mesh_new%s_atlanta_midnight_clear.pck'
                                  % ('_split%d' % a.split if a.split > 1 else ''))
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    print('new mesh @%#x | %d blocks in %d groups' % (mesh_off, sum(len(x) for x in groups), ngrp))
    print('%s' % os.path.basename(out))
    print('   %d bytes changed in the original region'
          % sum(1 for x, y in zip(orig, d) if x != y))


if __name__ == '__main__':
    main()
