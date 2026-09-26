# -*- coding: utf-8 -*-
"""Rebuild the mesh AND the `mesh+0x14` chain, which is PER BLOCK.

History: with the VU1 double buffer fixed, removing a block started working
(10 and 11 blocks pass) but ADDING one still hung (12 hangs). The reason showed
up in the relocator `sub_2A9D18`:

    mesh+0x14 -> array of `mesh+0x0A` pointers (slots)
      each slot -> ngrp entries of 8 bytes:  [ptrA][ptrB]
         ptrA -> [u32 count][u32 vertices per block] ... and the payloads right after
         ptrB -> array of `count` pointers, one per block

Measured on atlanta: **6893 of 6893 entries have `count` equal to the number of
blocks in that group** -- 100%. And each block's payload is **1 byte per
vertex** (a 48-vert block -> 48 bytes; a 4-vert one -> 4 bytes and the rest
zero), all aligned to 16.

I was reusing the whole original table. With fewer blocks there were spare
entries and nothing broke; with more, the game indexed past the end -- exactly
10 passes, 11 passes, 12 hangs.

A curiosity that confirms the reading: the "three loose pointers" I had found at
`model+0x64` are the `ptrB` of group 2 of slot 0, living in the slack of the
model object itself.

    python test_vu2.py --mode faithful 11 blocks (control)
    python test_vu2.py --mode split2   12 blocks
    python test_vu2.py --mode clone    12 blocks
    python test_vu2.py --mode fewer    10 blocks
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
    base = struct.unpack_from('<I', d, 0)[0] - 0x80
    F = lambda v: v - base
    V = lambda o: o + base
    U = lambda o: struct.unpack_from('<I', d, o)[0]

    for o in range(0x80, len(d) - 0x80, 4):
        if U(o) == 0x7A0090 and bytes(d[o + 0x48:o + 0x70]).split(b'\0')[0] == TARGET.encode():
            vt = o
            break
    old = F(U(vt + 0x14))
    ngrp, nslot = struct.unpack_from('<HH', d, old + 8)
    f18, f1c = U(old + 0x18), U(old + 0x1C)
    matf = F(U(old + 0x0C))
    materials = [struct.unpack_from('<H', d, matf + 2 * i)[0] for i in range(ngrp)]
    g = F(U(old + 0x10))
    t14 = F(U(old + 0x14))

    # ---- read the blocks and, with them, each slot's per-block payload ----
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
            entries_.append(dict(nv=nv, pos=pos, uv=uv, sc=sc, extra=[]))
        groups.append(entries_)

    for s in range(nslot):
        tab = F(U(t14 + 4 * s))
        for k in range(ngrp):
            pa, pb = struct.unpack_from('<II', d, tab + 8 * k)
            n = U(F(pa))
            assert n == len(groups[k]), 'slot %d group %d: %d != %d' % (s, k, n, len(groups[k]))
            for j in range(n):
                q = F(U(F(pb) + 4 * j))
                nv = groups[k][j]['nv']
                groups[k][j]['extra'].append(bytes(d[q:q + nv]))   # 1 byte per vertex

    # ---------------------------- the change ----------------------------
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
            end = s + step + 2
            if end - s >= 3:
                ped.append(dict(pos=big['pos'][s:end], uv=big['uv'][s:end], sc=big['sc'],
                                nv=len(big['pos'][s:end]),
                                # the payload is per vertex, so slice it the same way
                                extra=[e[s:end] for e in big['extra']]))
            s += step
        groups[gi48][ji48:ji48 + 1] = ped

    # ------------------------- build everything anew -------------------------
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
        # the VU1 buffer comes from the POSITION in the list, never from the source block
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

    # the mesh+0x14 chain, rebuilt: [slot array][per slot: table, then per
    # group the ptrA with the payloads appended, and the ptrB]
    t14_off = current
    current += len(pad16(b'\x00' * (4 * nslot)))
    slots, chain = [], bytearray()
    for s in range(nslot):
        slots.append(V(current + len(chain)))
        table = bytearray()
        body = bytearray()
        after = current + len(chain) + len(pad16(b'\x00' * (8 * ngrp)))
        for gr in groups:
            a_off = after + len(body)
            hdr = pad16(struct.pack('<I', len(gr)) + b''.join(
                struct.pack('<I', it['nv']) for it in gr), 0x00)
            body += hdr
            targets = []
            for j, it in enumerate(gr):
                targets.append(V(after + len(body)))
                body += pad16(it['extra'][s], 0x00)
            b_off = after + len(body)
            body += pad16(b''.join(struct.pack('<I', x) for x in targets), 0x00)
            table += struct.pack('<II', V(a_off), V(b_off))
        chain += pad16(bytes(table)) + bytes(body)
    current += len(chain)

    out_path = bytearray(bytes([0xCD]) * adjust)
    for m in materials:
        out_path += struct.pack('<H', m)
    m = bytearray(0x20)
    struct.pack_into('<I', m, 0x00, MESH_TAG)
    struct.pack_into('<H', m, 0x08, ngrp)
    struct.pack_into('<H', m, 0x0A, nslot)
    struct.pack_into('<I', m, 0x0C, V(mat_off))
    struct.pack_into('<I', m, 0x10, V(gtab))
    struct.pack_into('<I', m, 0x14, V(t14_off))       # <<< now it is the new chain
    struct.pack_into('<I', m, 0x18, f18)
    struct.pack_into('<I', m, 0x1C, f1c)
    out_path += bytes(m) + pad16(struct.pack('<I', ngrp))
    for p, n in pointers:
        out_path += struct.pack('<IHH', p, n, 0xCDCD)
    out_path += b''.join(regions)
    out_path += pad16(b''.join(struct.pack('<I', x) for x in slots), 0x00)
    out_path += bytes(chain)

    d.extend(out_path)
    while len(d) % 16:
        d.append(0xCD)
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    struct.pack_into('<I', d, vt + 0x14, V(mesh_off))

    # ---- self-check: reopen what was written and validate the chain ----
    U2 = lambda o: struct.unpack_from('<I', d, o)[0]
    mesh2 = F(U2(vt + 0x14))
    ng2, ns2 = struct.unpack_from('<HH', d, mesh2 + 8)
    g2, t2 = F(U2(mesh2 + 0x10)), F(U2(mesh2 + 0x14))
    blk2 = [struct.unpack_from('<IHH', d, g2 + 8 * k)[1] for k in range(ng2)]
    errors = 0
    for s in range(ns2):
        tab = F(U2(t2 + 4 * s))
        for k in range(ng2):
            pa, pb = struct.unpack_from('<II', d, tab + 8 * k)
            if U2(F(pa)) != blk2[k]:
                errors += 1
            for j in range(blk2[k]):
                nv = struct.unpack_from('<IHH', d, F(U2(g2 + 8 * k)) + 8 * j)[2]
                if U2(F(pa) + 4 + 4 * j) != nv:
                    errors += 1

    out = _out_path.path( 'test_vu2_%s_atlanta_midnight_clear.pck' % a.mode)
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    print('%-7s %2d blocks %s | %d slots rebuilt | %s'
          % (a.mode, sum(len(x) for x in groups), [len(x) for x in groups], nslot,
             'chain OK' if not errors else '%d ERRORS' % errors))
    print('        %s  (%d bytes changed from the original)'
          % (os.path.basename(out), sum(1 for x, y in zip(orig, d) if x != y)))


if __name__ == '__main__':
    main()
