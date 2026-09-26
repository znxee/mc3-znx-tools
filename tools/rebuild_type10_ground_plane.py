#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the ``type10`` quadtree of a ``*_bnd.pck`` as a coarse ground plane,
within the 2000/2000 ceiling of the ``phQuadtreeCell`` class.

WHY THIS EXISTS
===============

``mcPhysics::InitStaticData`` (0x002C2644) runs on every ``_bnd.pck`` load and does

    phOctreeCell::ClassInit  (a1[1], a1[2], *a1);        // values FROM THE FILE
    phQuadtreeCell::ClassInit(2000, 2000, 2000);          // CONSTANT

``ClassInit`` (0x004A5A08) allocates ``smPolyTouched = new byte[(n+7)>>3]`` and
``smVertTouched`` the same way: 250 bytes each for n=2000. Both bitmaps are
indexed by the GLOBAL INDEX of the polygon and of the vertex, with no range
check (``CullSpherePolysRecurse`` 0x004A6390, ``FinishCullingVerts`` 0x004A6AE8),
and both cull entry points call ``ClearTouched`` = ``memset(buf, 0, (n+7)>>3)``
with the bound's DECLARED count.

    => a phBoundQuadtree with more than 2000 polys or 2000 verts does a memset
       outside a 250-byte block on EVERY collision query.

The octree does not have this problem: ``header+0x80/+0x84`` are exactly the
type9 counts and size its bitmaps. The quadtree has no such field.

That explains the three LA builds: v13 (1873 polys / 2652 verts) overruns 82
bytes of the vertex bitmap and survives; v15 (2699/3832) overruns 88+229 and
freezes; v14 (12378/12991) overruns 1298+1375 and hangs the loading.

WHAT THIS GENERATOR DOES
========================

It measures the shape retail gives type10 and reproduces it. In the four shipped
cities, without a single exception across 604 polygons, every type10 face is
perfectly horizontal, facing up, and large (median span of 102 to 280 units) -
they are slabs on discrete height levels covering the whole city footprint.

So: take the type9 floor faces with the ``road`` material (the truth about
where there is road), rasterise their footprint into a grid, group them by
height band, merge neighbouring cells into maximal rectangles (greedy meshing)
and emit one flat quad per rectangle.

A quad's height is ``band_index * band - sink``, the band floor and not the
minimum of the rectangle itself, so that neighbouring slabs of the same band sit
at the same height and can share vertices. Since every cell of band ``k`` has
``min_y`` in ``[k*band, (k+1)*band)``, the quad always sits AT OR BELOW every
type9 road face inside it, with an error bounded by ``band + sink``. That is
deliberate and checked by the audit: this way it never steals a contact from
type9 nor creates a step. Where type9 has a hole, it works as a safety net just
below, instead of letting the car fall out of the world.

The grid occupancy is conservative (see ``rasterize``): covering too little is
harmless, covering too much would create an invisible platform in the air at
the edge of elevated roads.

The type9 comes out byte for byte identical.

    python rebuild_type10_ground_plane.py --donor modcity_bnd.pck \\
        --out out.pck --audit audit.json
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc3_bound as MB

ROAD_GLOBAL_ID = MB.GLOBAL_MATERIAL_IDS['road']


# ---------------------------------------------------------------- type9 source

def road_floors(bound, min_ny):
    """type9 floor faces whose global material is ``road``.

    Returns (xz_y points, ...) per face. Only upward-facing faces are taken: a
    face with a downward normal is the belly of an overpass, not road.
    """
    node = bound.trees[9]
    verts, polys = bound.geometry(node)
    mats = bound.materials(node)
    out = []
    skipped_material = skipped_normal = 0
    for (normal, area, vi, nb) in polys:
        local = struct.pack('<f', area)[0]
        if local >= len(mats) or mats[local] != ROAD_GLOBAL_ID:
            skipped_material += 1
            continue
        if normal[1] < min_ny:
            skipped_normal += 1
            continue
        out.append([verts[i] for i in vi])
    return out, dict(type9_polys=len(polys), road_floors=len(out),
                     skipped_not_road=skipped_material,
                     skipped_not_up=skipped_normal)


# ------------------------------------------------------------- rasterisation

def tri_rect_overlap(tri, x0, z0, x1, z1):
    """SAT between a 2D triangle and an axis-aligned rectangle."""
    tx = [p[0] for p in tri]
    tz = [p[1] for p in tri]
    if max(tx) < x0 or min(tx) > x1 or max(tz) < z0 or min(tz) > z1:
        return False
    rect = ((x0, z0), (x1, z0), (x1, z1), (x0, z1))
    for i in range(3):
        ax, az = tri[i]
        bx, bz = tri[(i + 1) % 3]
        nx, nz = -(bz - az), (bx - ax)
        if nx == 0.0 and nz == 0.0:
            continue
        tmin = min(nx * p[0] + nz * p[1] for p in tri)
        tmax = max(nx * p[0] + nz * p[1] for p in tri)
        rmin = min(nx * p[0] + nz * p[1] for p in rect)
        rmax = max(nx * p[0] + nz * p[1] for p in rect)
        if tmax < rmin or rmax < tmin:
            return False
    return True


def point_in_tri(px, pz, tri):
    (ax, az), (bx, bz), (cx, cz) = tri
    d1 = (px - bx) * (az - bz) - (ax - bx) * (pz - bz)
    d2 = (px - cx) * (bz - cz) - (bx - cx) * (pz - cz)
    d3 = (px - ax) * (cz - az) - (cx - ax) * (pz - az)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def rasterize(faces, cell, origin, sub, fill):
    """XZ grid -> lowest road height in that cell, with measured coverage.

    The occupancy is CONSERVATIVE on purpose, and this is the most delicate
    point of the generator. Marking the cell because "some face touched it"
    makes the slab spill up to a whole cell off the road - and a slab spilling
    over the edge of an elevated road becomes an invisible platform in the air,
    with the car landing on it. Covering too little is harmless (type9 still
    has the road); covering too much creates ground where there should be none.

    Sampling uses a LATTICE with step ``cell/sub`` whose points fall on the cell
    edges, not at sub-cell centres. A cell only gets in if ``fill`` of its
    ``(sub+1)^2`` points - **the four corners included** - are inside some
    road face.

    This matters more than it seems. Sampling sub-cell centres, the extreme
    points sit half a step from the edge: with ``sub=4`` the outer 12.5 % of the
    cell are never tested, and a "fully covered" cell still spills over at the
    corners. Measured: 0.52 % of the audit samples were exposed exactly like
    that. With the lattice, the edge is tested.

    The height is the lowest Y among the covered points - the lowest, not the
    mean: the quad has to pass under everything type9 has there, and a mean
    would put the slab above part of the road.
    """
    ox, oz = origin
    step = cell / float(sub)
    lattice = {}
    for pts in faces:
        xz = [(p[0], p[2]) for p in pts]
        ymin = min(p[1] for p in pts)
        lx0 = int(math.ceil((min(p[0] for p in xz) - ox) / step))
        lx1 = int(math.floor((max(p[0] for p in xz) - ox) / step))
        lz0 = int(math.ceil((min(p[1] for p in xz) - oz) / step))
        lz1 = int(math.floor((max(p[1] for p in xz) - oz) / step))
        tris = [(xz[0], xz[k], xz[k + 1]) for k in range(1, len(xz) - 1)]
        for lx in range(lx0, lx1 + 1):
            px = ox + lx * step
            for lz in range(lz0, lz1 + 1):
                pz = oz + lz * step
                if not any(point_in_tri(px, pz, t) for t in tris):
                    continue
                cur = lattice.get((lx, lz))
                if cur is None or ymin < cur:
                    lattice[(lx, lz)] = ymin
    per_cell = (sub + 1) * (sub + 1)
    need = max(1, int(round(fill * per_cell)))
    counts, lowest = {}, {}
    for (lx, lz), y in lattice.items():
        # A lattice point belongs to every cell that touches it - four at the
        # corners, two on the edges, one inside - which is why neighbouring cells
        # agree on the edge instead of each judging its own half.
        #
        # The `set` is not decorative: for an interior point the two candidates
        # `(lx-1)//sub` and `lx//sub` are the SAME index, and without
        # deduplicating the point was counted four times for the same cell. The
        # count went past 25 without the cell being covered, and the occupancy
        # became looser than the one it was supposed to replace.
        cells_x = {(lx - 1) // sub, lx // sub}
        cells_z = {(lz - 1) // sub, lz // sub}
        for gx in cells_x:
            for gz in cells_z:
                if not (gx * sub <= lx <= gx * sub + sub
                        and gz * sub <= lz <= gz * sub + sub):
                    continue
                key = (gx, gz)
                counts[key] = counts.get(key, 0) + 1
                if key not in lowest or y < lowest[key]:
                    lowest[key] = y
    return {k: lowest[k] for k, c in counts.items() if c >= need}


# -------------------------------------------------------- greedy rectangles

def greedy_rectangles(cells):
    """Merge cells with the same key into maximal rectangles.

    Row scan: find horizontal runs and extend them downwards while the next row
    has exactly the same run. It is the classic greedy meshing algorithm; it
    produces few large slabs, which is exactly the shape retail uses.
    """
    remaining = dict(cells)
    rects = []
    for (gx, gz) in sorted(remaining, key=lambda k: (k[1], k[0])):
        if (gx, gz) not in remaining:
            continue
        key = remaining[(gx, gz)]
        width = 0
        while remaining.get((gx + width, gz)) == key:
            width += 1
        height = 1
        while True:
            row = [(gx + i, gz + height) for i in range(width)]
            if all(remaining.get(c) == key for c in row):
                height += 1
            else:
                break
        for i in range(width):
            for j in range(height):
                del remaining[(gx + i, gz + j)]
        rects.append((gx, gz, width, height, key))
    return rects


# ------------------------------------------------------------------- geometry

def build_geometry(rects, cell, origin, heights, sink, band):
    """One flat quad per rectangle, with shared vertices.

    A slab's height is ``band_index * band - sink``, and NOT the minimum of the
    rectangle itself. The two properties this buys:

    - **it is still guaranteed** that the slab sits at or below every type9 road
      face inside it. By construction of the band, every cell of the group has
      ``min_y`` in ``[k*band, (k+1)*band)``, so ``k*band <= min_y`` always.

      Mind the reach of that guarantee: ``band + sink`` bounds how far the slab
      sits below the cell's SAMPLED MINIMUM, not below the road at every point.
      On a ramp the road rises inside the cell itself, and there the slab ends
      up deeper - measured, about 8 % of the samples sit between 5 and 10 units
      below. It stays buried, which is what matters; it is just not "at most
      band+sink" at every point.
    - **all slabs of the same band sit at exactly the same height**, so the
      corners fall on the same grid lines and neighbouring rectangles share
      vertices. Giving each slab its own Y, no corner coincides: it comes out at
      4 vertices per polygon and zero reciprocal neighbours - it fits under the
      ceiling, but wastes the vertex budget.
    """
    ox, oz = origin
    verts, vmap, polys = [], {}, []
    for (gx, gz, w, h, band_index) in rects:
        y = band_index * band - sink
        x0, x1 = ox + gx * cell, ox + (gx + w) * cell
        z0, z1 = oz + gz * cell, oz + (gz + h) * cell
        # Counter-clockwise seen from above, so the normal comes out +Y, which is
        # the convention verified in the four shipped cities.
        corners = ((x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0))
        idx = []
        for c in corners:
            if c not in vmap:
                vmap[c] = len(verts)
                verts.append(c)
            idx.append(vmap[c])
        if len(set(idx)) != 4:
            continue
        area = (x1 - x0) * (z1 - z0)
        polys.append(((0.0, 1.0, 0.0), area, tuple(idx)))
    return verts, polys


def all_floors(bound):
    """Every upward-facing type9 face, of any material, in an XZ grid.

    Any material, not only ``road``: a slab buried under the sidewalk is
    correctly buried, and requiring road there would flag as exposed a slab that
    is supported.
    """
    node = bound.trees[9]
    verts, polys = bound.geometry(node)
    CELL = 16.0
    grid, faces = {}, []
    for (normal, area, vi, nb) in polys:
        if normal[1] < 0.30:
            continue
        pts = [verts[i] for i in vi]
        idx = len(faces)
        faces.append((normal, pts))
        xs = [q[0] for q in pts]; zs = [q[2] for q in pts]
        for gx in range(int(math.floor(min(xs) / CELL)), int(math.floor(max(xs) / CELL)) + 1):
            for gz in range(int(math.floor(min(zs) / CELL)), int(math.floor(max(zs) / CELL)) + 1):
                grid.setdefault((gx, gz), []).append(idx)
    return faces, grid, CELL


def drop_exposed(rects, cell, origin, band, sink, faces, grid, gcell, samples, near):
    """Remove the slabs that would be EXPOSED, that is, with no type9 floor above.

    The generator is already conservative in the grid occupancy, but the
    rectangle is a union of cells and its edge can still escape the road here
    and there. Instead of letting the audit fail over 0.02 % of the samples, the
    slab that fails is simply dropped: it is redundant with type9 anyway, so
    losing one costs nothing and guarantees the property by construction.

    A point with no face containing it but WITH a type9 floor closer than `near`
    is in a gap of LA's instanced mesh, not in the air - there the slab is
    plugging a type9 hole, which is useful, and so it does not count as exposed.
    """
    ox, oz = origin

    def inside(pts, x, z):
        n = len(pts); s = 0
        for i in range(n):
            ax, az = pts[i][0], pts[i][2]
            bx, bz = pts[(i + 1) % n][0], pts[(i + 1) % n][2]
            cr = (bx - ax) * (z - az) - (bz - az) * (x - ax)
            if cr > 1e-9: s |= 1
            elif cr < -1e-9: s |= 2
            if s == 3: return False
        return True

    def ok(x, z, y):
        g = int(math.floor(x / gcell)), int(math.floor(z / gcell))
        for idx in grid.get(g, ()):
            normal, pts = faces[idx]
            if not inside(pts, x, z) or abs(normal[1]) < 1e-9:
                continue
            ax, ay, az = pts[0]
            h = ay - (normal[0] * (x - ax) + normal[2] * (z - az)) / normal[1]
            if h >= y - 1e-3:
                return True
        span = int(math.ceil(near / gcell))
        for dx in range(-span, span + 1):
            for dz in range(-span, span + 1):
                for idx in grid.get((g[0] + dx, g[1] + dz), ()):
                    normal, pts = faces[idx]
                    if max(q[1] for q in pts) < y - 1e-3:
                        continue
                    for q in pts:
                        if (q[0] - x) ** 2 + (q[2] - z) ** 2 <= near * near:
                            return True
        return False

    kept, dropped = [], 0
    for r in rects:
        gx, gz, w, h, band_index = r
        y = band_index * band - sink
        x0, x1 = ox + gx * cell, ox + (gx + w) * cell
        z0, z1 = oz + gz * cell, oz + (gz + h) * cell
        good = True
        for i in range(samples):
            for j in range(samples):
                x = x0 + (x1 - x0) * (i + 0.5) / samples
                z = z0 + (z1 - z0) * (j + 0.5) / samples
                if not ok(x, z, y):
                    good = False
                    break
            if not good:
                break
        if good:
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def build_neighbours(polys):
    """Reciprocal neighbourhood by exactly coincident edges.

    Rectangles of different sizes form a T-junction, and a T-junction has no
    reciprocal neighbour. In those cases the edge stays 0xFFFF, which is what
    retail does too (atlanta has polygons with all four sides loose).
    """
    neighbours = [[0xFFFF] * 4 for _ in polys]
    edges = {}
    for pi, (_n, _a, idx) in enumerate(polys):
        for ei in range(4):
            a, b = idx[ei], idx[(ei + 1) % 4]
            edges.setdefault((min(a, b), max(a, b)), []).append((pi, ei))
    paired = nonmanifold = 0
    for uses in edges.values():
        if len(uses) == 2 and uses[0][0] != uses[1][0]:
            (a, ae), (b, be) = uses
            neighbours[a][ae] = b
            neighbours[b][be] = a
            paired += 1
        elif len(uses) > 2:
            nonmanifold += 1
    return neighbours, paired, nonmanifold


# ------------------------------------------------------------------- quadtree

class Leaf:
    __slots__ = ('ids', 'start', 'count')

    def __init__(self, ids):
        self.ids = ids
        self.start = 0
        self.count = 0


def build_quadtree(boxes, root_lo, root_hi, max_depth, per_leaf):
    blocks = []
    deepest = [0]

    def rec(ids, lo, hi, depth):
        deepest[0] = max(deepest[0], depth)
        if depth >= max_depth or len(ids) <= per_leaf:
            return Leaf(ids)
        midx = (lo[0] + hi[0]) / 2.0
        midz = (lo[2] + hi[2]) / 2.0
        children = []
        for child in range(4):
            bx, bz = child & 1, (child >> 1) & 1
            child_lo = [midx if bx else lo[0], lo[1], midz if bz else lo[2]]
            child_hi = [hi[0] if bx else midx, hi[1], hi[2] if bz else midz]
            hits = [i for i in ids
                    if boxes[i][0][0] <= child_hi[0] and boxes[i][1][0] >= child_lo[0]
                    and boxes[i][0][2] <= child_hi[2] and boxes[i][1][2] >= child_lo[2]]
            children.append(rec(hits, child_lo, child_hi, depth + 1))
        blocks.append(children)
        return ('node', len(blocks) - 1)

    return blocks, rec(list(range(len(boxes))), root_lo, root_hi, 0), deepest[0]


def walk_leaves(blocks, root):
    result, stack = [], [root]
    while stack:
        item = stack.pop()
        if isinstance(item, Leaf):
            result.append(item)
        else:
            stack.extend(reversed(blocks[item[1]]))
    return result


# ---------------------------------------------------------------- serialisation

def serialise(donor, verts, polys, neighbours, blocks, root, depth, out_path):
    node = donor.trees[10]
    material_ids = donor.materials(node)
    if ROAD_GLOBAL_ID not in material_ids:
        raise SystemExit('the donor type10 local palette does not contain road (17)')
    road_index = material_ids.index(ROAD_GLOBAL_ID)
    payload = donor.off(donor.u32(node + 0x78))
    before = donor.data
    data = bytearray(before)

    leaves = walk_leaves(blocks, root)
    refs = []
    for leaf in leaves:
        leaf.start, leaf.count = len(refs), len(leaf.ids)
        refs.extend(leaf.ids)
    if len(refs) > 0xFFFF:
        raise SystemExit('%d refs overflow the u16 start' % len(refs))

    lo = [min(v[a] for v in verts) for a in range(3)]
    hi = [max(v[a] for v in verts) for a in range(3)]
    centre = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
    side = max(hi[a] - lo[a] for a in range(3))
    grid_lo = [centre[a] - side / 2.0 for a in range(3)]
    grid_hi = [centre[a] + side / 2.0 for a in range(3)]
    radius = max(math.dist(v, centre) for v in verts) * 1.0002
    origin_radius = radius + math.sqrt(sum(x * x for x in centre))
    cell = side / float(2 ** depth) if depth else side

    def append(blob, align=16):
        while len(data) % align:
            data.append(0xCD)
        offset = len(data)
        data.extend(blob)
        return offset

    def virtual(offset):
        return offset + donor.base

    vblob = b''.join(struct.pack('<3f', *v) for v in verts)
    pblob = bytearray()
    for pi, (normal, area, idx) in enumerate(polys):
        pblob += struct.pack('<3f', *normal)
        pblob += MB.pack_area_with_material(area, road_index)
        pblob += struct.pack('<4H', *idx)
        pblob += struct.pack('<4H', *neighbours[pi])
    vert_off = append(vblob)
    poly_off = append(bytes(pblob))
    index_off = append(struct.pack('<%dH' % len(refs), *refs))
    # Each four-child vector was serialised by the game with the 16-byte array
    # header in front of it: count=4 and 12 bytes of 0xCD. Reproduce it.
    array4 = struct.pack('<I', 4) + bytes([0xCD]) * 12
    block_offs = [append(array4 + bytes(16)) + 16 for _ in blocks]

    def slot_value(item):
        if isinstance(item, Leaf):
            if item.count > 0x3FFF:
                raise SystemExit('a leaf with %d refs overflows the 14-bit field'
                                 % item.count)
            return (item.start << 16) | (item.count << 2) | 1
        return virtual(block_offs[item[1]])

    for bi, children in enumerate(blocks):
        for ci, item in enumerate(children):
            struct.pack_into('<I', data, block_offs[bi] + ci * 4, slot_value(item))
    root_value = slot_value(root)
    root_off = append(struct.pack('<I', root_value))

    struct.pack_into('<3f', data, node + 0x08, *lo)
    struct.pack_into('<3f', data, node + 0x14, *hi)
    struct.pack_into('<3f', data, node + 0x20, *centre)
    struct.pack_into('<3f', data, node + 0x2C, *centre)
    struct.pack_into('<f', data, node + 0x38, radius)
    struct.pack_into('<f', data, node + 0x3C, origin_radius)
    struct.pack_into('<I', data, node + 0x4C, len(verts))
    struct.pack_into('<I', data, node + 0x50, len(polys))
    struct.pack_into('<I', data, node + 0x68, virtual(vert_off))
    struct.pack_into('<I', data, node + 0x6C, virtual(poly_off))
    struct.pack_into('<I', data, node + 0x7C, root_value)
    struct.pack_into('<I', data, payload + 0x00, virtual(root_off))
    struct.pack_into('<I', data, payload + 0x08, len(polys))
    struct.pack_into('<I', data, payload + 0x0C, len(verts))
    struct.pack_into('<I', data, payload + 0x10, len(refs))
    struct.pack_into('<I', data, payload + 0x14, virtual(index_off))
    struct.pack_into('<f', data, payload + 0x18, cell)
    struct.pack_into('<3f', data, payload + 0x1C, *grid_lo)
    struct.pack_into('<3f', data, payload + 0x28, *grid_hi)
    while len(data) % 16:
        data.append(0xCD)
    struct.pack_into('<I', data, 0x0C, len(data) - 0x80)

    allowed = set(range(0x0C, 0x10))
    for start, length in ((node + 0x08, 0x38), (node + 0x4C, 8), (node + 0x68, 8),
                          (node + 0x7C, 4), (payload + 0x00, 4), (payload + 0x08, 0x2C)):
        allowed.update(range(start, start + length))
    unexpected = [i for i, (old, new) in enumerate(zip(before, data))
                  if old != new and i not in allowed]
    if unexpected:
        raise SystemExit('unexpected bytes changed: %s' % unexpected[:20])

    with open(out_path, 'wb') as stream:
        stream.write(data)
    return dict(leaves=len(leaves), blocks=len(blocks), refs=len(refs),
                depth=depth, cell_size=cell, aabb=[lo, hi],
                grid=[grid_lo, grid_hi], radius=radius,
                origin_radius=origin_radius, road_local_index=road_index,
                material_palette=material_ids,
                bytes_appended=len(data) - len(before),
                size=len(data),
                sha256=hashlib.sha256(bytes(data)).hexdigest().upper())


# ------------------------------------------------------------------------ main

def run(args):
    donor = MB.Bound(args.donor)
    donor_sha = hashlib.sha256(donor.data).hexdigest().upper()
    faces, source_stats = road_floors(donor, args.min_ny)
    if not faces:
        raise SystemExit('no road face in type9')
    floors, fgrid, fcell = all_floors(donor)

    ox = min(min(p[0] for p in f) for f in faces)
    oz = min(min(p[2] for p in f) for f in faces)

    attempts = []
    chosen = None
    for cell in args.cells:
        for band in args.bands:
            grid = rasterize(faces, cell, (ox, oz), args.sub, args.fill)
            banded = {k: int(math.floor(v / band)) for k, v in grid.items()}
            rects = greedy_rectangles(banded)
            rects, dropped = drop_exposed(rects, cell, (ox, oz), band, args.sink,
                                          floors, fgrid, fcell, args.verify, args.near)
            heights = {}
            for (gx, gz, w, h, _b) in rects:
                heights[(gx, gz, w, h)] = min(
                    grid[(gx + i, gz + j)] for i in range(w) for j in range(h))
            verts, polys = build_geometry(rects, cell, (ox, oz), heights,
                                          args.sink, band)
            attempts.append(dict(cell=cell, band=band,
                                 cells=len(grid), dropped_exposed=dropped,
                                 rects=len(rects), verts=len(verts),
                                 polys=len(polys),
                                 fits=len(verts) <= args.max_verts
                                 and len(polys) <= args.max_polys))
            if (len(verts) <= args.max_verts and len(polys) <= args.max_polys
                    and chosen is None):
                chosen = (cell, band, grid, rects, heights, verts, polys, dropped)
        if chosen:
            break
    if chosen is None:
        for a in attempts:
            print('  cell=%-5g band=%-4g -> %5d cells %5d rects %5d verts %5d polys'
                  % (a['cell'], a['band'], a['cells'], a['rects'], a['verts'],
                     a['polys']))
        raise SystemExit('no combination fit in %d verts / %d polys'
                         % (args.max_verts, args.max_polys))

    cell, band, grid, rects, heights, verts, polys, dropped = chosen
    neighbours, paired, nonmanifold = build_neighbours(polys)

    boxes = []
    for (_n, _a, idx) in polys:
        pts = [verts[i] for i in idx]
        boxes.append(([min(p[a] for p in pts) for a in range(3)],
                      [max(p[a] for p in pts) for a in range(3)]))
    lo = [min(v[a] for v in verts) for a in range(3)]
    hi = [max(v[a] for v in verts) for a in range(3)]
    centre = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
    side = max(hi[a] - lo[a] for a in range(3))
    glo = [centre[a] - side / 2.0 for a in range(3)]
    ghi = [centre[a] + side / 2.0 for a in range(3)]
    blocks, root, depth = build_quadtree(boxes, glo, ghi, args.depth, args.per_leaf)

    info = serialise(donor, verts, polys, neighbours, blocks, root, depth, args.out)
    info.update(source=source_stats, donor=args.donor, donor_sha256=donor_sha,
                grid_cell=cell, height_band=band, sink=args.sink,
                subsamples=args.sub, fill_threshold=args.fill,
                min_ny=args.min_ny, grid_cells=len(grid), rectangles=len(rects),
                dropped_exposed=dropped, verify_samples=args.verify,
                near_radius=args.near,
                verts=len(verts), polys=len(polys), paired_edges=paired,
                nonmanifold_edges=nonmanifold, attempts=attempts,
                cap_polys=args.max_polys, cap_verts=args.max_verts)

    print('type9 road        : %d upward road faces (of %d polys)'
          % (source_stats['road_floors'], source_stats['type9_polys']))
    print('grid              : cell=%g, band=%g -> %d occupied cells'
          % (cell, band, len(grid)))
    print('slabs             : %d rectangles -> %d polys, %d verts '
          '(%d dropped for being exposed)'
          % (len(rects), len(polys), len(verts), dropped))
    print('class ceiling     : %d/2000 polys, %d/2000 verts  %s'
          % (len(polys), len(verts),
             'OK' if len(polys) <= 2000 and len(verts) <= 2000 else 'OVERFLOWS'))
    print('quadtree          : %d blocks, %d leaves, %d refs, depth %d, cell %.4f'
          % (info['blocks'], info['leaves'], info['refs'], depth, info['cell_size']))
    print('edges             : %d paired, %d non-manifold' % (paired, nonmanifold))
    print('written           : %s  %d bytes' % (args.out, info['size']))
    print('SHA256            : %s' % info['sha256'])
    if args.audit:
        with open(args.audit, 'w', encoding='utf-8') as stream:
            json.dump(info, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--audit')
    ap.add_argument('--cells', type=float, nargs='+',
                    default=[16, 20, 24, 32, 40, 48, 64, 80])
    ap.add_argument('--bands', type=float, nargs='+', default=[2.0, 4.0, 8.0])
    ap.add_argument('--sink', type=float, default=0.05,
                    help='how far below the lowest type9 floor the slab sits')
    ap.add_argument('--min-ny', type=float, default=0.70)
    ap.add_argument('--sub', type=int, default=4,
                    help='samples per side inside each cell')
    ap.add_argument('--fill', type=float, default=1.00,
                    help='minimum fraction of the cell lattice points '
                         'covered by road; 1.0 requires all four corners')
    ap.add_argument('--verify', type=int, default=6,
                    help='samples per side when checking each slab against '
                         'type9; an exposed slab is dropped')
    ap.add_argument('--near', type=float, default=6.0,
                    help='radius separating a gap in the type9 mesh from empty space')
    ap.add_argument('--max-polys', type=int, default=1800)
    ap.add_argument('--max-verts', type=int, default=1800)
    ap.add_argument('--depth', type=int, default=8)
    ap.add_argument('--per-leaf', type=int, default=12)
    run(ap.parse_args())


if __name__ == '__main__':
    main()
