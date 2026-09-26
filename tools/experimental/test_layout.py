# -*- coding: utf-8 -*-
"""Rewrite the mesh with the retail LAYOUT -- found in ZNXee's mesh_psptops2.

The vehicle converter (`mesh_psptops2_v1.1.py`, ZNXee / mc3rxx / offlbruno)
already built it this way, and it has worked in game for a long time:

    add_padding(count_u32, 16, 0xCD) + entry_list + BLOCKS

that is, per group, everything in ONE CONTIGUOUS REGION:

    [16 B header with the count]
    [N entries of 8 B: ptr, u16 qwords, u16 vertices]
    [pad to 16]
    [block 0][block 1]...[block N-1]   <- right after, back to back

and the stored pointer aims 16 bytes past the start (skipping the header) --
`offset - block_pointers_size + 16` in that script.

Checked byte for byte on atlanta: each group's blocks start 8 bytes after the
end of the list (padding to 16) and sit back to back.

I had been appending block by block and the list afterwards, all scattered -- a
"valid" structure but a layout retail never uses. It is the last difference left.

    python test_layout.py                 # 11 blocks (control)
    python test_layout.py --split 2       # 12 blocks
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
    mesh = F(U(vt + 0x14))
    ngrp = struct.unpack_from('<H', d, mesh + 8)[0]
    g = F(U(mesh + 0x10))

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
            entries_.append(dict(bytes=bytes(d[blk:blk + qw * 16]), qw=qw, nv=nv,
                              pos=pos, uv=uv, odd=odd, sc=sc))
        groups.append(entries_)

    if a.split > 1:
        largest = max((it for gr in groups for it in gr), key=lambda x: x['nv'])
        gi = next(i for i, gr in enumerate(groups) if largest in gr)
        gr = groups[gi]
        idx = gr.index(largest)
        n = largest['nv']
        step = max(4, ((n + a.split - 1) // a.split + 1) // 2 * 2)
        chunks = []
        s = 0
        while s < n - 2:
            p2, u2 = largest['pos'][s:s + step + 2], largest['uv'][s:s + step + 2]
            if len(p2) >= 3:
                chunks.append(dict(pos=p2, uv=u2, odd=largest['odd'], sc=largest['sc'],
                                    nv=len(p2), bytes=None, qw=None))
            s += step
        gr[idx:idx + 1] = chunks
        print('block of %d vertices (group %d) -> %d pieces' % (n, gi, len(chunks)))

    # ---- build each group as ONE contiguous region, in the retail layout ----
    while len(d) % 16:
        d.append(0xCD)

    regions = []          # (list offset, region bytes)
    for gr in groups:
        blobs = []
        for it in gr:
            if it['bytes'] is not None:
                blobs.append(it['bytes'])
            else:
                blobs.append(conv.make_block(it['pos'], it['uv'], it['sc'], it['odd']))
        hdr = pad16(struct.pack('<I', len(gr)))            # 16 B with the count
        list_size = len(pad16(b'\x00' * (8 * len(gr))))
        base_reg = len(d) + sum(len(x) for x in regions)   # where this region lands
        list_off = base_reg + 16
        blocks_off = list_off + list_size
        entries = bytearray()
        pos = blocks_off
        for it, blob in zip(gr, blobs):
            entries += struct.pack('<IHH', V(pos), len(blob) // 16, it['nv'])
            pos += len(blob)
        region = hdr + pad16(bytes(entries)) + b''.join(blobs)
        regions.append(region)

    size_until = 0
    pointers = []
    for gr, reg in zip(groups, regions):
        pointers.append((V(len(d) + size_until + 16), len(gr)))
        size_until += len(reg)
    for reg in regions:
        d.extend(reg)

    # group table, also with a header
    while len(d) % 16:
        d.append(0xCD)
    tab_base = len(d)
    tab = bytearray()
    for p, n in pointers:
        tab += struct.pack('<IHH', p, n, 0xCDCD)
    d.extend(pad16(struct.pack('<I', ngrp)))
    tab_off = len(d)
    d.extend(pad16(bytes(tab)))
    struct.pack_into('<I', d, mesh + 0x10, V(tab_off))
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)

    out = a.out_path or _out_path.path( 'test_layout%s_atlanta_midnight_clear.pck'
                                  % ('_split%d' % a.split if a.split > 1 else ''))
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    print('%d blocks in %d groups | %s' % (sum(len(x) for x in groups), ngrp, os.path.basename(out)))
    print('   %d bytes changed in the original region'
          % sum(1 for x, y in zip(orig, d) if x != y))


if __name__ == '__main__':
    main()
