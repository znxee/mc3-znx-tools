# -*- coding: utf-8 -*-
"""Final bisection: RETAIL geometry going through MY whole pipeline.

`test_plumbing` copied the original block bytes, so it proved the
append/table/repoint but did **not** test `make_block` or the stitching. This
one decodes the target's original blocks, pushes everything down the same line
the MC2 conversion uses (re-strip, stitch with degenerates, re-encode) and
writes it.

The geometry is the game's own. If THIS hangs, the bug is in my pipeline and MC2
has nothing to do with it. If it passes, the problem is the data coming from MC2.

    python test_pipeline.py             # re-encode, same block count
    python test_pipeline.py --chop      # re-cut into small blocks (~41)
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
TARGET = 'a_inst_apt_mid_02x'


def append_array(d, body, n, align=16):
    """Append an array WITH THE 16-BYTE HEADER the game expects.

    Found in the mesh constructor (`sub_2A8FA0` in the ELF): it allocates
    `n*8 + 16`, writes the COUNT at the start of the buffer and keeps the
    pointer aimed 16 BYTES PAST it. So `[ptr - 16]` is the element count.
    Checked on retail: 6/6 groups and 6/6 block lists of the target. I never
    wrote it -- 0xCDCDCDCD sat there.
    """
    pad = 0xCD
    while len(d) % align:
        d.append(pad)
    d.extend(struct.pack("<I", n))
    d.extend(bytes([pad]) * 12)
    body_at = len(d)
    d.extend(body)
    while len(d) % align:
        d.append(pad)
    struct.pack_into("<I", d, 0x0C, len(d) - 0x80)
    return body_at



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default=TARGET)
    ap.add_argument('--chop', action='store_true',
                    help='re-cut the geometry into short strips and stitch them (like MC2)')
    ap.add_argument('--out-path', default=None)
    a = ap.parse_args()

    d = bytearray(open(SRC, 'rb').read())
    va = struct.unpack_from('<I', d, 0)[0]
    base = va - 0x80
    F = lambda v: v - base
    V = lambda o: o + base
    U = lambda o: struct.unpack_from('<I', d, o)[0]

    vt = None
    for o in range(0x80, len(d) - 0x80, 4):
        if U(o) == 0x7A0090:
            if bytes(d[o + 0x48:o + 0x70]).split(b'\0')[0].decode('latin1', 'replace') == a.target:
                vt = o
                break
    mesh = F(U(vt + 0x14))
    ngrp = struct.unpack_from('<H', d, mesh + 8)[0]
    g = F(U(mesh + 0x10))

    # decode the original blocks -> strips (positions in world units)
    strip_list = []
    orig_groups = []
    for k in range(ngrp):
        p, c, _ = struct.unpack_from('<IHH', d, g + 8 * k)
        lf = F(p)
        for j in range(c):
            bp, qw, nv = struct.unpack_from('<IHH', d, lf + 8 * j)
            blk = F(bp)
            sc = struct.unpack_from('<f', d, blk + 4)[0]
            cnt = struct.unpack_from('<I', d, blk + 8)[0]
            pos = [tuple(x * sc for x in struct.unpack_from('<hhh', d, blk + 0x10 + 6 * i))
                   for i in range(cnt)]
            u0 = blk + 0x10 + 6 * cnt
            while u0 % 4:
                u0 += 1
            uv = [tuple(x / 4096.0 for x in struct.unpack_from('<hh', d, u0 + 4 + 4 * i))
                  for i in range(cnt)]
            odd = bytes(d[blk + 0x0C:blk + 0x0E]) == b'\x27\x02'
            strip_list.append((pos, uv, odd))
        orig_groups.append(c)
    print('target %s: %d groups, %d original blocks, %d vertices'
          % (a.target, ngrp, len(strip_list), sum(len(p) for p, _u, _o in strip_list)))

    if a.chop:
        # break into 6-vertex strips and let the stitching rebuild them -- the
        # same profile MC2 delivers (many short strips)
        short_ones = []
        for pos, uv, odd in strip_list:
            for s in range(0, len(pos) - 2, 4):
                p2, u2 = pos[s:s + 6], uv[s:s + 6]
                if len(p2) >= 3:
                    short_ones.append((p2, u2, odd if s % 2 == 0 else not odd))
        print('   chopped into %d short strips' % len(short_ones))
        blocks = []
        cur_p, cur_u, cur_odd = [], [], False
        for pos, uv, odd in short_ones:
            if not cur_p:
                cur_p, cur_u, cur_odd = list(pos), list(uv), odd
                continue
            wants_pair = (cur_odd != (not odd))
            bridge = 2 if ((len(cur_p) + 2) % 2 == 0) == wants_pair else 3
            if len(cur_p) + bridge + len(pos) > conv.MAX_VERTS:
                blocks.append((cur_p, cur_u, cur_odd))
                cur_p, cur_u, cur_odd = list(pos), list(uv), odd
                continue
            cur_p += [cur_p[-1]] + [pos[0]] * (bridge - 1)
            cur_u += [cur_u[-1]] + [uv[0]] * (bridge - 1)
            cur_p += pos
            cur_u += uv
        if cur_p:
            blocks.append((cur_p, cur_u, cur_odd))
    else:
        blocks = strip_list
    print('   -> %d blocks' % len(blocks))

    sc = conv.pick_scale(blocks)
    print('   scale %g' % sc)

    def append(blob, align=16):
        while len(d) % align:
            d.extend(b'\xCD')
        at = len(d)
        d.extend(blob)
        while len(d) % align:
            d.extend(b'\xCD')
        struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
        return at

    ptrs = []
    for pos, uv, odd in blocks:
        blob = conv.make_block(pos, uv, sc, odd)
        ptrs.append((V(append(blob)), len(blob) // 16, len(pos)))

    if not a.chop and sum(orig_groups) == len(ptrs):
        slices, i = [], 0          # keep the original grouping
        for c in orig_groups:
            slices.append(ptrs[i:i + c]); i += c
    else:
        slices = [ptrs[i::ngrp] for i in range(ngrp)]
        slices = [f if f else [ptrs[0]] for f in slices]
    tab = bytearray()
    for f in slices:
        lb = bytearray()
        for va_, qw, nv in f:
            lb += struct.pack('<IHH', va_, qw, nv)
        tab += struct.pack('<IHH', V(append(bytes(lb))), len(f), 0xCDCD)
    struct.pack_into('<I', d, mesh + 0x10, V(append(bytes(tab))))

    out = a.out_path or _out_path.path( 'test_pipeline%s_atlanta_midnight_clear.pck'
                                  % ('_chopped' if a.chop else ''))
    open(out, 'wb').write(bytes(d))
    orig = open(SRC, 'rb').read()
    print('output: %s' % out)
    print('   %d bytes changed in the original region'
          % sum(1 for x, y in zip(orig, d) if x != y))


if __name__ == '__main__':
    main()
