#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert MC2's text collision (`otgrid`) into MC3's `_bnd.pck`.

MC3 reads city-scale collision ONLY from a .pck (type 9) - the ASCII `.bnd`
reader (`phBound::LoadFromFile` 0x0047E128) serves individual bounds. And a
rejected `_bnd` falls into a `while(1)` at 0x001A93D8, i.e. a black screen, so
the result is checked by rereading the file at the end.

What the MC2 source gives and what has to be computed:

  vertices   come ready, already in WORLD coordinates across the grid's 42 cells
  polygons   the indices come (tri/quad); the NORMAL and the AREA are computed
  winding    MC2's already matches MC3's - measured: 94.1% of the near-horizontal
             ones point +Y, against 97% in retail. Do not flip it (flipping makes
             the car get in and stay stuck inside the solid).
  material   the local index sits in the least significant byte of the area
             float; each MC2 class is mapped to the donor's physical palette

The tree is built from scratch because LA does not fit in any donor's cube. The
rules came from reading retail:

  - the root is a CUBE: the AABB centred and grown until every side equals the largest
  - `side/cell` is a power of 2 (512 atlanta, 1024 sd/detroit, 2048 tokyo)
  - octant: bit0 = x, bit1 = z, bit2 = y (not x/y/z)
  - a polygon goes into EVERY leaf whose cell its AABB crosses
  - leaf = `(start << 16) | (bytes << 1) | 1`, with `count = (v & 0xFFFF) >> 2`
  - an EMPTY octant has to be a zero-count leaf, NEVER a null pointer: the
    reader only tests bit 0 and dereferences any even slot
  - `start` has 16 bits, a ceiling of 65535 entries in the list
"""
import os
import argparse
import struct
import sys

from mc2_bnd_parse import read_otgrid


def normal_and_area(pts):
    """Unit normal from the winding and the real area of the polygon."""
    a, b, c = pts[0], pts[1], pts[2]
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * w[2] - u[2] * w[1],
         u[2] * w[0] - u[0] * w[2],
         u[0] * w[1] - u[1] * w[0])
    m = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
    if m < 1e-12:
        return None, 0.0
    n = (n[0] / m, n[1] / m, n[2] / m)
    # the area through the SAME projection mc3_bound's check() recomputes
    s2 = 0.0
    for j in range(len(pts)):
        k = (j + 1) % len(pts)
        s2 += ((pts[j][1] * pts[k][2] - pts[j][2] * pts[k][1]) * n[0]
               + (pts[j][2] * pts[k][0] - pts[j][0] * pts[k][2]) * n[1]
               + (pts[j][0] * pts[k][1] - pts[j][1] * pts[k][0]) * n[2])
    return n, abs(s2 / 2.0)


class Leaf(object):
    """A tree leaf. It is an object so it can be identified by identity when
    the same content shows up in different octants."""

    __slots__ = ('ids', 'start', 'count')

    def __init__(self, ids):
        self.ids = ids
        self.start = 0
        self.count = 0


def build_octree(boxes, clo, chi, max_depth, per_leaf):
    """(blocks, root). Each block is a list of 8 slots; a slot is a `Leaf`
    or the index of another block (wrapped in `('node', i)`)."""
    blocks = []
    background = [0]                       # the depth the tree REALLY uses

    def rec(ids, lo, hi, depth):
        if depth > background[0]:
            background[0] = depth
        if depth >= max_depth or len(ids) <= per_leaf:
            return Leaf(ids)
        mid = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
        children = []
        for oct_ in range(8):
            bx, bz, by = oct_ & 1, (oct_ >> 1) & 1, (oct_ >> 2) & 1
            flo = [mid[0] if bx else lo[0], mid[1] if by else lo[1],
                   mid[2] if bz else lo[2]]
            fhi = [hi[0] if bx else mid[0], hi[1] if by else mid[1],
                   hi[2] if bz else mid[2]]
            inside = [i for i in ids
                      if all(boxes[i][0][a] <= fhi[a] and boxes[i][1][a] >= flo[a]
                             for a in range(3))]
            children.append(rec(inside, flo, fhi, depth + 1))
        blocks.append(children)
        return ('node', len(blocks) - 1)

    root = rec(list(range(len(boxes))), clo, chi, 0)
    return blocks, root, background[0]


def walks(blocks, root):
    """Every leaf, in the order the tree visits them."""
    out_path, stack = [], [root]
    while stack:
        s = stack.pop()
        if isinstance(s, Leaf):
            out_path.append(s)
        else:
            stack.extend(reversed(blocks[s[1]]))
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--mc2', default='output/mc2_losangeles/bound/losangeles.bnd')
    ap.add_argument('--donor',
                    default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/tokyo_bnd.pck'),
                    help='skeleton: header, wrappers and the road tree (type 10)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--depth', type=int, default=10, help='maximum depth')
    ap.add_argument('--per-leaf', type=int, default=22)
    a = ap.parse_args()

    verts, polys, mats, _cab = read_otgrid(a.mc2)
    print('MC2: %d vertices, %d polygons, %d materials'
          % (len(verts), len(polys), len(mats)))

    import mc3_bound as MB
    b = MB.Bound(a.donor)
    node = b.trees[9]
    material_ids = b.materials(node)
    if MB.GLOBAL_MATERIAL_IDS['road'] not in material_ids:
        sys.exit('the donor has no road material (global id 17)')

    out_path, boxes, bad_tags = [], [], 0
    for vi, name in polys:
        pts = [verts[i] for i in vi]
        n, area = normal_and_area(pts)
        if n is None:
            bad_tags += 1
            continue
        idx = list(vi) + ([0] if len(vi) == 3 else [])
        material_index, _physical = MB.material_index_for_name(name, material_ids)
        out_path.append((n, area, idx, material_index))
        boxes.append(([min(p[c] for p in pts) for c in range(3)],
                       [max(p[c] for p in pts) for c in range(3)]))
    if bad_tags:
        print('  %d degenerate polygon(s) dropped' % bad_tags)
    npoly = len(out_path)

    lo = [min(q[c] for q in verts) for c in range(3)]
    hi = [max(q[c] for q in verts) for c in range(3)]
    side = max(hi[c] - lo[c] for c in range(3))
    centre = [(lo[c] + hi[c]) / 2.0 for c in range(3)]
    clo = [centre[c] - side / 2.0 for c in range(3)]
    chi = [centre[c] + side / 2.0 for c in range(3)]
    print('AABB (%.0f %.0f %.0f)..(%.0f %.0f %.0f)  cube side %.1f  cell %.3f'
          % tuple(lo + hi + [side, side / (2 ** a.depth)]))

    blocks, root, background = build_octree(boxes, clo, chi, a.depth, a.per_leaf)
    leaves = walks(blocks, root)
    refs = sum(len(f.ids) for f in leaves)
    # the payload `cell` field is side/2^depth; it has to reflect the depth the
    # tree REALLY uses, not the one asked for with --depth
    cell = side / float(2 ** background)
    print('tree: %d blocks, %d leaves, %d references (dup %.2fx), '
          'real depth %d, cell %.3f'
          % (len(blocks), len(leaves), refs, refs / float(npoly), background, cell))
    if refs > 0xFFFF:
        sys.exit('%d references exceed the 65535 ceiling a 16-bit start can '
                 'address - lower --depth or raise --per-leaf' % refs)

    data = bytearray(b.data)

    def append_blob(blob, align=16):
        while len(data) % align:
            data.append(0xCD)
        off = len(data)
        data.extend(blob)
        return off

    def va(off):
        return off + b.base

    bp = bytearray()
    for n, area, idx, material_index in out_path:
        bp += struct.pack('<3f', n[0], n[1], n[2])
        bp += MB.pack_area_with_material(area, material_index)
        bp += struct.pack('<8H', idx[0], idx[1], idx[2], idx[3],
                          0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
    off_poly = append_blob(bytes(bp))
    off_vert = append_blob(b''.join(struct.pack('<3f', *q) for q in verts))

    items = []
    for f in leaves:
        f.start, f.count = len(items), len(f.ids)
        items.extend(f.ids)
    off_idx = append_blob(struct.pack('<%dH' % len(items), *items))

    block_off = [append_blob(bytes(32)) for _ in blocks]

    def value(slot):
        if isinstance(slot, Leaf):
            # an empty leaf is still a leaf: zero count, never a null pointer
            return (slot.start << 16) | ((slot.count * 2) << 1) | 1
        return va(block_off[slot[1]])

    for bi, children in enumerate(blocks):
        for j, s in enumerate(children):
            struct.pack_into('<I', data, block_off[bi] + 4 * j, value(s))
    root_off = append_blob(struct.pack('<I', value(root)))

    struct.pack_into('<3f', data, node + 0x08, *lo)
    struct.pack_into('<3f', data, node + 0x14, *hi)
    struct.pack_into('<3f', data, node + 0x20, *centre)
    struct.pack_into('<3f', data, node + 0x2C, *centre)
    struct.pack_into('<I', data, node + 0x4C, len(verts))
    struct.pack_into('<I', data, node + 0x50, npoly)
    struct.pack_into('<I', data, node + 0x68, va(off_vert))
    struct.pack_into('<I', data, node + 0x6C, va(off_poly))
    p = b.off(b.u32(node + 0x94))
    struct.pack_into('<I', data, p + 0x00, va(root_off))
    # phPolytree also serialises the geometry counts in the payload. The donor
    # carries Tokyo's numbers here; leaving those two words untouched produces a
    # self-contradicting file (node/root say LA, payload says Tokyo).
    struct.pack_into('<I', data, p + 0x08, npoly)
    struct.pack_into('<I', data, p + 0x0C, len(verts))
    struct.pack_into('<I', data, p + 0x10, len(items))
    struct.pack_into('<I', data, p + 0x14, va(off_idx))
    struct.pack_into('<f', data, p + 0x18, cell)
    struct.pack_into('<3f', data, p + 0x1C, *clo)
    struct.pack_into('<3f', data, p + 0x28, *chi)
    struct.pack_into('<I', data, 0x80, len(verts))
    struct.pack_into('<I', data, 0x84, npoly)

    while len(data) % 16:            # retail aligns the END of the file too
        data.append(0xCD)
    struct.pack_into('<I', data, 0x0C, len(data) - 128)
    open(a.out, 'wb').write(bytes(data))
    print('written %s (%d bytes)' % (a.out, len(data)))

    # Reopen and validate what was really serialised. This catches pointers,
    # sizes and counts forgotten in the converter before the PCK reaches the game.
    c = MB.Bound(a.out)
    cn = c.trees[9]
    cp = c.off(c.u32(cn + 0x94))
    fields = (
        ('root vertices', c.verts_total, len(verts)),
        ('root polygons', c.polys_total, npoly),
        ('node vertices', c.u32(cn + 0x4C), len(verts)),
        ('node polygons', c.u32(cn + 0x50), npoly),
        ('payload polygons', c.u32(cp + 0x08), npoly),
        ('payload vertices', c.u32(cp + 0x0C), len(verts)),
        ('payload refs', c.u32(cp + 0x10), len(items)),
    )
    errors = ['%s: %d != %d' % (name, current, expected)
             for name, current, expected in fields if current != expected]
    leaves_c = sorted(c.leaves(cn, 9))
    cursor = 0
    for start, count, _slot in leaves_c:
        if start != cursor:
            errors.append('leaves: range starts at %d, expected %d'
                          % (start, cursor))
            break
        cursor += count
    if cursor != len(items):
        errors.append('leaves: end at %d, expected %d' % (cursor, len(items)))
    _ok, bad_area = MB.check(c, 9)
    if bad_area:
        errors.append('areas: %d mismatched polygon(s)' % bad_area)
    materials_c = c.material_indices(cn)
    expected_materials = [p[3] for p in out_path]
    if materials_c != expected_materials:
        errors.append('mismatched material indices')
    if any(i >= len(material_ids) for i in materials_c):
        errors.append('material index outside the palette')
    if errors:
        sys.exit('invalid output:\n  ' + '\n  '.join(errors))
    print('validated: consistent root/node/payload counts, %d leaves '
          'partition %d references, 0 mismatched areas; materials %s'
          % (len(leaves_c), len(items), sorted(set(materials_c))))


if __name__ == '__main__':
    main()
