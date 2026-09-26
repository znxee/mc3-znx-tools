#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rewrite a ``*_bnd.pck`` in a compact layout, without changing any content.

WHY
===

This project's build flow is incremental: every type9 rebuild APPENDS the new
geometry at the end of the file and leaves the old one orphaned. Nobody deletes
anything, so the file only grows and most of it becomes garbage the game loads
into RAM and never reads.

Measured: the 08/09 `modcity_bnd.pck` was already **42.4 % dead space**, and the
v17..v20 candidates reached **55 %**. Fork City MODS measured the effect on the
console and the result was harsh: freeroam stopped loading: 2.4 MB were free in
total but the LARGEST CONTIGUOUS BLOCK was 0.48 MB, and the 3.60 MB bnd did not
fit. Endless loading, with no exception and no message.

This compactor attacks exactly that and does not cost a single polygon.

HOW
===

There is NO scanning for things that "look like a pointer" - that is forbidden
in this project, and rightly so. The file is REBUILT from the structures
`mc3_bound.py` knows how to read, in canonical order, and every pointer is
rewritten because it is known exactly where it is:

    header      +0x0C   body size
    mcPhysics   +0x0C   -> mcLevelBounds
    mcLevelBnd  +0x00   -> phInst(type10)   +0x04 -> phInst(type9)
                +0x10   -> CurveMgr
    phInst      +0x04   -> phArchetype
    phArchetype +0x0C   -> phBound
    phBound     +0x68   -> vertices         +0x6C -> polygons
                +0x70   -> material table
                +0x78   -> polytree (type10)  or  +0x94 (type9)
                +0x7C   root value (type10)
    polytree    +0x00   -> root slot        +0x04 -> owning phBound
                +0x14   -> index list
    block       each slot: pointer to another block, or a leaf (bit 0 = 1)

The material table comes out with the 24 slots the +0x74 field declares: the
live entries and `0xCDCDCDCD` in the rest, which is retail's shape.

What is not a known structure (the `CurveMgr`) is copied byte for byte, without
interpretation.

VERIFICATION
============

Compacting is a high-risk, high-value operation, so it proves itself: after
writing, the new file is RE-PARSED and compared with the original in geometry
(vertices and polygons byte for byte), tree (leaves, refs, order), material,
AABB and counts. Any difference aborts and the file is not used.

    python compact_bnd.py --in fat.pck --out lean.pck
"""

import argparse
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc3_bound as MB

MAT_SLOTS = 24          # what the +0x74 field declares in the four retail cities
PAD = 0xCD


def tree_parts(b, kind):
    """Every object of a tree, already read, ready to be emitted again."""
    node = b.trees[kind]
    fan = 8 if kind == 9 else 4
    nv, npoly = b.u32(node + 0x4C), b.u32(node + 0x50)
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    root_slot, idx_off, nref = b.payload(node, kind)

    # blocks, in the order the tree reaches them, so the new file keeps a
    # locality similar to the original's
    order, seen, stack = [], set(), [root_slot]
    while stack:
        slot = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        v = b.u32(slot)
        if v & 1 or not b.valid(v):
            continue
        base = b.off(v)
        if base not in order:
            order.append(base)
        stack.extend(base + 4 * i for i in range(fan))

    return dict(
        kind=kind, fan=fan, node=node, nverts=nv, npolys=npoly, pay=pay,
        root_slot=root_slot, nrefs=nref,
        inst=b.off(b.u32(b.off(b.u32(0x8C)) + (0x00 if kind == 10 else 0x04))),
        verts=b.data[b.off(b.u32(node + 0x68)):b.off(b.u32(node + 0x68)) + 12 * nv],
        polys=b.data[b.off(b.u32(node + 0x6C)):b.off(b.u32(node + 0x6C)) + 32 * npoly],
        mats=b.materials(node),
        idx=b.data[idx_off:idx_off + 2 * nref],
        blocks=order,
    )


def weld_vertices(t):
    """Merge vertices that sit at EXACTLY the same position.

    Only exact duplicates, and that is what makes the operation safe: two
    distinct vertices of the same polygon never share a position unless the
    polygon is already degenerate, so merging cannot create a degenerate one -
    and no tolerance is needed, nor the risk that would come with it. The gain
    comes from the flow: MC2's global mesh and the instanced surfaces are
    deduplicated in separate passes, and where one touches the other the same
    point goes in twice. Measured on v21: 3,726 repeated vertices, 44 KB.
    """
    nv, raw = t['nverts'], t['verts']
    pos = [raw[12*i:12*i+12] for i in range(nv)]
    first, remap, keep = {}, [0]*nv, []
    for i, q in enumerate(pos):
        j = first.get(q)
        if j is None:
            first[q] = len(keep)
            remap[i] = len(keep)
            keep.append(q)
        else:
            remap[i] = j
    if len(keep) == nv:
        return 0
    polys = bytearray(t['polys'])
    for k in range(t['npolys']):
        o = 32*k + 16
        vi = list(struct.unpack_from('<4H', polys, o))
        tri = vi[3] == 0
        n = 3 if tri else 4
        new_vi = [remap[x] for x in vi[:n]]
        if len(set(new_vi)) != n:
            return -1                       # would become degenerate: abort everything
        if not tri:
            # The game tells a quad from a triangle by v3 == 0. If the remap puts
            # vertex 0 in the fourth position, the quad is read as a triangle
            # and one half of the face vanishes. Rotating is cyclic, so it keeps
            # the polygon and its winding; a quad has 4 distinct indices, so at
            # most one of them is 0 and the rotation always finds a position.
            # The first version did not do this and the verification failed - it
            # was the verification that caught it, not the game.
            while new_vi[3] == 0:
                new_vi = new_vi[1:] + new_vi[:1]
        struct.pack_into('<4H', polys, o, *(new_vi + ([0] if tri else [])))
    t['polys'] = bytes(polys)
    t['verts'] = b''.join(keep)
    saved = nv - len(keep)
    t['nverts'] = len(keep)
    return saved


def compact(src_path, dst_path, verbose=True, weld=False):
    b = MB.Bound(src_path)
    base_field = b.u32(0x00)
    lb = b.off(b.u32(0x8C))
    curve = b.u32(lb + 0x10)
    curve_off = b.off(curve) if curve and b.valid(curve) else None

    parts = {k: tree_parts(b, k) for k in sorted(b.trees)}
    for k, t in parts.items():
        t['arch'] = b.off(b.u32(t['inst'] + 4))
    welded = {}
    if weld:
        for k, t in parts.items():
            n = weld_vertices(t)
            if n < 0:
                raise SystemExit('t%d: merging vertices would create a degenerate '
                                 'polygon; aborted without writing' % k)
            welded[k] = n

    # ---- pass 1: where each thing will go ----------------------------------
    layout, cur = {}, 0

    def place(name, size, align=16):
        nonlocal cur
        if cur % align:
            cur += align - (cur % align)
        layout[name] = cur
        cur += size
        return layout[name]

    place('header', 0x80, 1)
    place('mcPhysics', 0x18, 16)
    place('mcLevelBounds', 0x14, 16)
    if curve_off is not None:
        place('curve', 0x20, 16)
    for k in (10, 9):
        if k not in parts:
            continue
        t = parts[k]
        place('inst%d' % k, 0x40, 16)
        place('arch%d' % k, 0x20, 16)
        place('node%d' % k, 0xA0, 16)
        place('verts%d' % k, len(t['verts']), 16)
        place('polys%d' % k, len(t['polys']), 16)
        place('mats%d' % k, 4 * MAT_SLOTS, 16)
        place('pay%d' % k, 0x40, 16)
        place('idx%d' % k, len(t['idx']), 16)
        for n, _blk in enumerate(t['blocks']):
            place('blk%d_%d' % (k, n), 16 + 4 * t['fan'], 16)
            layout['blk%d_%d' % (k, n)] += 16      # the pointer points to the slots
        place('root%d' % k, 4, 16)
    total = cur if cur % 16 == 0 else cur + 16 - (cur % 16)

    data = bytearray(b'\xCD' * total)

    def va(name):
        return layout[name] + base_field - 0x80

    def copy(name, src_off, size):
        data[layout[name]:layout[name] + size] = b.data[src_off:src_off + size]

    # ---- pass 2: write -----------------------------------------------------
    copy('header', 0, 0x80)
    struct.pack_into('<I', data, 0x0C, total - 0x80)
    copy('mcPhysics', 0x80, 0x18)
    struct.pack_into('<I', data, layout['mcPhysics'] + 0x0C, va('mcLevelBounds'))
    copy('mcLevelBounds', lb, 0x14)
    if curve_off is not None:
        copy('curve', curve_off, 0x20)
        struct.pack_into('<I', data, layout['mcLevelBounds'] + 0x10, va('curve'))
    for slot, k in ((0x00, 10), (0x04, 9)):
        if k in parts:
            struct.pack_into('<I', data, layout['mcLevelBounds'] + slot, va('inst%d' % k))

    for k in (10, 9):
        if k not in parts:
            continue
        t = parts[k]
        copy('inst%d' % k, t['inst'], 0x40)
        struct.pack_into('<I', data, layout['inst%d' % k] + 0x04, va('arch%d' % k))
        copy('arch%d' % k, t['arch'], 0x20)
        struct.pack_into('<I', data, layout['arch%d' % k] + 0x0C, va('node%d' % k))
        copy('node%d' % k, t['node'], 0xA0)
        n = layout['node%d' % k]
        data[layout['verts%d' % k]:layout['verts%d' % k] + len(t['verts'])] = t['verts']
        data[layout['polys%d' % k]:layout['polys%d' % k] + len(t['polys'])] = t['polys']
        data[layout['idx%d' % k]:layout['idx%d' % k] + len(t['idx'])] = t['idx']
        for i, gid in enumerate(t['mats']):
            struct.pack_into('<I', data, layout['mats%d' % k] + 4 * i, gid)
        struct.pack_into('<I', data, n + 0x4C, t['nverts'])
        struct.pack_into('<I', data, n + 0x68, va('verts%d' % k))
        struct.pack_into('<I', data, n + 0x6C, va('polys%d' % k))
        struct.pack_into('<I', data, n + 0x70, va('mats%d' % k))
        struct.pack_into('<I', data, n + 0x74, MAT_SLOTS)
        struct.pack_into('<I', data, n + (0x94 if k == 9 else 0x78), va('pay%d' % k))

        copy('pay%d' % k, t['pay'], 0x40)
        p = layout['pay%d' % k]
        struct.pack_into('<I', data, p + 0x00, va('root%d' % k))
        struct.pack_into('<I', data, p + 0x04, va('node%d' % k))
        struct.pack_into('<I', data, p + 0x0C, t['nverts'])
        struct.pack_into('<I', data, p + 0x14, va('idx%d' % k))

        old2new = {old: va('blk%d_%d' % (k, i)) for i, old in enumerate(t['blocks'])}

        def slot_value(v):
            """A leaf stays the same; a block pointer becomes the new address."""
            if v & 1 or not b.valid(v):
                return v
            return old2new[b.off(v)]

        for i, old in enumerate(t['blocks']):
            dst = layout['blk%d_%d' % (k, i)]
            struct.pack_into('<I', data, dst - 16, t['fan'])   # array header
            for c in range(t['fan']):
                struct.pack_into('<I', data, dst + 4 * c,
                                 slot_value(b.u32(old + 4 * c)))
        rootv = slot_value(b.u32(t['root_slot']))
        struct.pack_into('<I', data, layout['root%d' % k], rootv)
        if k == 10:
            struct.pack_into('<I', data, n + 0x7C, rootv)

    with open(dst_path, 'wb') as s:
        s.write(data)

    # ---- verification: the new one has to read the same as the old one -----
    c = MB.Bound(dst_path)
    problems = []
    if sorted(c.trees) != sorted(b.trees):
        problems.append('set of trees changed')
    for k in sorted(b.trees):
        nb, nc = b.trees[k], c.trees[k]
        conf = ((('AABB', 0x08, 0x38),) if weld else
                (('AABB', 0x08, 0x38), ('counts', 0x4C, 8)))
        for label, off, size in conf:
            if b.data[nb+off:nb+off+size] != c.data[nc+off:nc+off+size]:
                problems.append('t%d %s differs' % (k, label))
        vb, pb = b.u32(nb+0x4C), b.u32(nb+0x50)
        if weld:
            # the vertex array changes on purpose; what has to stay the same is
            # the POSITION of each corner of each polygon, and the area/material
            gv, gp = b.geometry(nb)
            hv, hp = c.geometry(nc)
            if len(gp) != len(hp):
                problems.append('t%d polygon count changed' % k)
            else:
                for (n1, a1, i1, _x), (n2, a2, i2, _y) in zip(gp, hp):
                    p1 = [gv[i] for i in i1]
                    p2 = [hv[i] for i in i2]
                    # the quad may come out ROTATED, because v3 cannot be 0; a
                    # cyclic rotation is the same polygon with the same winding,
                    # so the comparison has to be cyclic and not positional
                    same = len(p1) == len(p2) and any(
                        p1 == p2[r:] + p2[:r] for r in range(len(p2)))
                    if not same or struct.pack('<f', a1) != struct.pack('<f', a2):
                        problems.append('t%d corner/area/material changed' % k)
                        break
        else:
            if (b.data[b.off(b.u32(nb+0x68)):b.off(b.u32(nb+0x68))+12*vb]
                    != c.data[c.off(c.u32(nc+0x68)):c.off(c.u32(nc+0x68))+12*vb]):
                problems.append('t%d vertices differ' % k)
            if (b.data[b.off(b.u32(nb+0x6C)):b.off(b.u32(nb+0x6C))+32*pb]
                    != c.data[c.off(c.u32(nc+0x6C)):c.off(c.u32(nc+0x6C))+32*pb]):
                problems.append('t%d polygons differ' % k)
        if b.materials(nb) != c.materials(nc):
            problems.append('t%d palette differs' % k)
        rb, ib, nrb = b.payload(nb, k)
        rc, ic, nrc = c.payload(nc, k)
        if nrb != nrc or b.data[ib:ib+2*nrb] != c.data[ic:ic+2*nrc]:
            problems.append('t%d index list differs' % k)
        lb_ = sorted(b.leaves(nb, k)); lc_ = sorted(c.leaves(nc, k))
        if [(s, n) for s, n, _ in lb_] != [(s, n) for s, n, _ in lc_]:
            problems.append('t%d leaves differ (%d vs %d)' % (k, len(lb_), len(lc_)))
        if k == 10 and c.u32(nc+0x7C) != c.u32(rc):
            problems.append('t10 node+0x7C != root')

    src = len(b.data)
    if verbose:
        print('source   : %s  %d bytes' % (os.path.basename(src_path), src))
        print('compact  : %s  %d bytes' % (os.path.basename(dst_path), total))
        print('saved    : %d bytes (%.1f%%)' % (src - total, 100.0*(src-total)/src))
        for k in sorted(b.trees):
            t = parts[k]
            print('  t%-2d %6d verts, %6d polys, %6d refs, %4d blocks%s'
                  % (k, t['nverts'], t['npolys'], t['nrefs'], len(t['blocks']),
                     ('  (%d merged)' % welded[k]) if welded.get(k) else ''))
        print('SHA256   : %s' % hashlib.sha256(bytes(data)).hexdigest().upper())
        if problems:
            print('VERIFICATION: FAILED')
            for p in problems:
                print('  - %s' % p)
        else:
            print('VERIFICATION: OK - geometry, tree, indices, palette and AABB identical')
    return total, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--out', dest='dst', required=True)
    ap.add_argument('--weld', action='store_true',
                    help='merge vertices at exactly the same position')
    a = ap.parse_args()
    _size, problems = compact(a.src, a.dst, weld=a.weld)
    if problems:
        os.remove(a.dst)
        print('file removed; nothing was delivered')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
