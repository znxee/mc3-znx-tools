"""
mc3_bound.py - Midnight Club 3 PS2 collision and road network (*_bnd.pck)

The PS2 build has no loose .occlude / .graph / .aib next to the city. All of it
is compiled into `<city>_bnd.pck` (container type 9) as two spatial trees of the
same generic class, told apart by a type byte:

    type 9   octree,   8 children   the world collision
    type 10  quadtree, 4 children   the road network

Which is which is not a guess. The quadtree's bounding box is flat - detroit's
is exactly 0 units tall - while the octree reaches 233 units in atlanta and 357
in tokyo, where the skyscrapers are. It also matches the field names left in the
executable: `maxOtEdges/Polys/Verts` for the octree ("Ot"), `%s_quad_0` and
`numroads:` for the quadtree.

Each tree node carries the geometry:

    +08..+13  AABB min          +14..+1F  AABB max
    +20..+37  centre, twice     +38, +3C  two radii
    +4C  vertex count           +50  polygon count
    +68  -> vertices, float3, 12 bytes each
    +6C  -> polygons, 32 bytes each
    +70  -> material id table, +74 = its size field
    +78  -> quadtree payload    +94  -> octree payload

and a polygon is:

    +00  float nx, ny, nz   unit plane normal
    +0C  float              polygon area; its least-significant byte is also
                             the local material-table index
    +10  u16 v0 v1 v2 v3    vertex indices
    +18  u16 n0 n1 n2 n3    neighbouring polygon indices, 0xFFFF for an edge

A polygon is a triangle when `v3 == 0 and n3 == 0xFFFF`, and those four bytes go
unused. Verified over the four shipped cities - 79303 polygons: every area
recomputed from the vertices matches the stored one, every normal is unit
length, and all 195240 neighbour references are mutual with none broken.

The material encoding is deliberately hidden inside the area float. Runtime
method 0x0047A258 returns ``lbu polygon+0x0C``; 0x0047A1F0 uses that byte to
index the u32 global material-id table at node+0x70. Replacing the whole float
with a freshly calculated area therefore also replaces the surface material.

    python mc3_bound.py atlanta_bnd.pck                 report
    python mc3_bound.py atlanta_bnd.pck --obj out.obj   export the collision
    python mc3_bound.py atlanta_bnd.pck --obj r.obj --roads
"""

import argparse
import os
import math
import struct

TREE_KIND = {9: 'octree (collision)', 10: 'quadtree (roads)'}

GLOBAL_MATERIAL_IDS = {
    'default': 0, 'alley': 1, 'cobblestone': 2, 'death': 3, 'dirt': 4,
    'grass': 8, 'interior': 11, 'metal': 13, 'railroad': 16, 'road': 17,
    'sewer': 20, 'sidewalk': 21, 'wall': 25, 'water': 26,
}


def physical_material_name(source_name):
    """Map the MC2/mesh material spelling to an MC3 physical class."""
    name = source_name.rsplit(':', 1)[-1].lower()
    if 'cobblestone' in name:
        return 'cobblestone'
    if 'railroad' in name:
        return 'railroad'
    if 'sidewalk' in name or 'concrete' in name or 'stairs' in name:
        return 'sidewalk'
    if ('road' in name or 'asphalt' in name or 'ashphalt' in name
            or 'crosswalk' in name or 'alwaysdry' in name or 'bridge' in name):
        return 'road'
    if 'building' in name or 'treetrunk' in name or 'wall' in name:
        return 'wall'
    for material in ('water', 'grass', 'dirt', 'metal', 'sewer', 'alley',
                     'interior', 'death'):
        if material in name:
            return material
    return 'default'


def material_index_for_name(source_name, global_ids):
    """Resolve a source material name into a donor's local palette index."""
    physical = physical_material_name(source_name)
    global_id = GLOBAL_MATERIAL_IDS[physical]
    if global_id not in global_ids:
        return 0, 'default'
    return global_ids.index(global_id), physical


def area_with_material(area, material_index):
    """Return *area* with the runtime material index encoded in its low byte."""
    if not 0 <= material_index <= 0xFF:
        raise ValueError('material index outside u8: %d' % material_index)
    raw = bytearray(struct.pack('<f', area))
    raw[0] = material_index
    return struct.unpack('<f', bytes(raw))[0]


def pack_area_with_material(area, material_index):
    """Pack an area while preserving MC3's shared low-byte material field."""
    raw = bytearray(struct.pack('<f', area))
    if not 0 <= material_index <= 0xFF:
        raise ValueError('material index outside u8: %d' % material_index)
    raw[0] = material_index
    return bytes(raw)


class Bound:
    """A parsed *_bnd.pck. Offsets are into the file, pointers are virtual."""

    def __init__(self, path):
        self.data = open(path, 'rb').read()
        self.path = path
        base, self.kind, version, size = struct.unpack_from('<4I', self.data, 0)
        if self.kind != 9 or version != 1:
            raise SystemExit('%s: type %d version %d, not a bound container'
                             % (os.path.basename(path), self.kind, version))
        if size != len(self.data) - 128:
            raise SystemExit('%s: body size %d does not match the file' % (path, size))
        self.base = base - 0x80
        self.verts_total = self.u32(0x80)
        self.polys_total = self.u32(0x84)
        s = self.off(self.u32(0x8C))
        # +00 is the quadtree, +04 the octree; each is a small wrapper whose
        # +04 leads to an inner object whose +0C is the node with the geometry
        self.trees = {}
        for slot in (0x00, 0x04):
            p = self.u32(s + slot)
            if not self.valid(p):
                continue
            node = self.off(self.u32(self.off(self.u32(self.off(p) + 4)) + 0x0C))
            self.trees[self.data[node + 4]] = node

    def u32(self, o):
        return struct.unpack_from('<I', self.data, o)[0]

    def f32(self, o):
        return struct.unpack_from('<f', self.data, o)[0]

    def off(self, virtual):
        return virtual - self.base

    def valid(self, virtual):
        return bool(virtual) and 0x80 <= virtual - self.base < len(self.data) - 4

    def aabb(self, node):
        return (tuple(self.f32(node + 0x08 + 4 * i) for i in range(3)),
                tuple(self.f32(node + 0x14 + 4 * i) for i in range(3)))

    def materials(self, node):
        """Global material ids addressed by the polygon low-byte indices.

        The field at +0x74 overshoots the table.  Inferring its live length from
        the largest index used by the polygons works for both trees, including
        type10 where the payload follows the table with no CD padding.
        """
        at = self.off(self.u32(node + 0x70))
        # The octree table is followed by CD padding; prefer that structural
        # delimiter even when a broken converter has already randomized the
        # polygon bytes. The quadtree has no padding there, so its live length
        # is inferred from the indices as in retail.
        if self.data[node + 4] == 9:
            out = []
            while len(out) < 64:
                value = self.u32(at + 4 * len(out))
                if value == 0xCDCDCDCD:
                    return out
                out.append(value)
        indices = self.material_indices(node)
        count = max(indices) + 1 if indices else 0
        return [self.u32(at + 4 * i) for i in range(count)]

    def material_indices(self, node):
        """Return the local material-table index of every polygon."""
        count = self.u32(node + 0x50)
        at = self.off(self.u32(node + 0x6C))
        return [self.data[at + 32 * i + 0x0C] for i in range(count)]

    def payload(self, node, kind):
        """(tree_root_slot, index_list_offset, index_count) of a tree."""
        p = self.off(self.u32(node + (0x94 if kind == 9 else 0x78)))
        return self.off(self.u32(p)), self.off(self.u32(p + 0x14)), self.u32(p + 0x10)

    def leaves(self, node, kind):
        """[(start, count, slot_offset)] of every leaf, walking the tree.

        A node is just EIGHT consecutive u32 slots (four for a quadtree) - the
        reader does `child[i] = *node + 4*i`, nothing more. Bit 0 of a slot
        tells them apart: clear means a pointer to the next block of slots, set
        means a leaf packed as

            (first_index << 16) | (byte_length << 1) | 1

        indexing the flat u16 list - so the run holds `(v & 0xFFFF) >> 2`
        entries, not `>> 1`. The low half counts BYTES, and a u16 index is two
        of them.

        Reading it as `>> 1` still parses: every leaf comes out with double its
        real length, the ranges overlap their neighbours, and it looks like a
        spatial index storing each face in about two cells. The giveaway is that
        the lengths then add up to exactly twice the list, 113146 against 56573
        in atlanta - not approximately twice, exactly. Decoded properly the
        ranges are disjoint and partition the list: atlanta's quadtree runs
        0, 1, 13, 16, 27, ... 147 and the last one ends on 153, which is the
        index count on the nose.
        """
        root, _idx, _n = self.payload(node, kind)
        fan = 8 if kind == 9 else 4
        out, stack, seen = [], [root], set()
        while stack:
            slot = stack.pop()
            if slot in seen:
                continue
            seen.add(slot)
            v = self.u32(slot)
            if v & 1:
                out.append((v >> 16, (v & 0xFFFF) >> 2, slot))
                continue
            if not self.valid(v):
                continue
            base = self.off(v)
            stack.extend(base + 4 * i for i in range(fan))
        return out

    def geometry(self, node):
        """(vertices, polygons). A polygon is (normal, area, indices, neighbours)
        with three indices for a triangle and four for a quad."""
        nv, npoly = self.u32(node + 0x4C), self.u32(node + 0x50)
        vat, pat = self.off(self.u32(node + 0x68)), self.off(self.u32(node + 0x6C))
        verts = [struct.unpack_from('<3f', self.data, vat + 12 * i) for i in range(nv)]
        polys = []
        for k in range(npoly):
            nx, ny, nz, area = struct.unpack_from('<4f', self.data, pat + 32 * k)
            r = struct.unpack_from('<8H', self.data, pat + 32 * k + 16)
            triangle = r[3] == 0 and r[7] == 0xFFFF
            polys.append(((nx, ny, nz), area, r[:3] if triangle else r[:4], r[4:]))
        return verts, polys


def lift(b, cx, cz, radius, dy, kind=9):
    """Translate every vertex within `radius` of (cx, cz) by `dy`, in place.

    The safest possible edit to a bound file, and a good first test of whether
    the game accepts one that was rewritten at all: a polygon record stores the
    plane normal, the area, the vertex indices and the neighbours - and a pure
    translation changes none of them. No count moves, the tree is untouched, and
    every leaf keeps pointing at the same primitives. If the game takes this, the
    container itself is fine and anything that breaks later is the edit, not the
    format.

    The one thing that does shift is where the geometry sits relative to its
    octree cell, so keep the move small enough that it stays in the same
    neighbourhood. The node AABB is widened when the lift pushes past it.
    """
    node = b.trees[kind]
    nv = b.u32(node + 0x4C)
    vat = b.off(b.u32(node + 0x68))
    data = bytearray(b.data)
    every = radius <= 0                 # radius 0 or less means the whole mesh
    moved = 0
    for i in range(nv):
        x, _y, z = struct.unpack_from('<3f', data, vat + 12 * i)
        if every or (x - cx) ** 2 + (z - cz) ** 2 <= radius * radius:
            struct.pack_into('<f', data, vat + 12 * i + 4, _y + dy)
            moved += 1
    if every:
        # Moving everything keeps the file exactly consistent: no polygon is
        # left half-moved, and shifting the node AABB by the same amount drags
        # every octree cell along with the geometry, so each face stays in the
        # cell its leaf says it is in. A partial lift cannot do that - the mesh
        # is connected, so any radius leaves polygons with some corners moved
        # and some not (28 of 71 at radius 120 around one building).
        for off in (0x0C, 0x18, 0x24, 0x30):
            struct.pack_into('<f', data, node + off, b.f32(node + off) + dy)
    elif moved:
        struct.pack_into('<f', data, node + 0x0C, min(b.f32(node + 0x0C),
                                                      b.f32(node + 0x0C) + dy))
        struct.pack_into('<f', data, node + 0x18, max(b.f32(node + 0x18),
                                                      b.f32(node + 0x18) + dy))
    b.data = bytes(data)
    return moved


def box_polys(lo, hi, floor=True):
    """A closed box as 12 triangles, ready for `insert`.

    Enough to give a building collision, and the stored area is the real one -
    that field is not decorative, it matches the geometry to within 0.0000 on
    retail triangles.

    WINDING MATTERS, and getting it backwards does not fail loudly - it builds a
    box you can drive into and then cannot drive out of. The convention is not a
    guess: across all 79303 polygons of the four shipped cities, without a single
    exception, `cross(v1 - v0, v2 - v0)` equals the stored normal, and 97% of the
    near-horizontal ones point +Y. So the normal faces OUT of the solid and the
    vertices run counter-clockwise seen from outside.
    """
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
         (x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
    quads = [(7, 6, 5, 4), (4, 5, 1, 0), (5, 6, 2, 1),
             (6, 7, 3, 2), (7, 4, 0, 3)]
    if floor:
        quads.append((0, 1, 2, 3))
    tris = []
    for a, bb, cc, dd in quads:
        tris.append((a, bb, cc))
        tris.append((a, cc, dd))
    out = []
    for a, bb, cc in tris:
        A, B, C = c[a], c[bb], c[cc]
        u = [B[k] - A[k] for k in range(3)]
        v = [C[k] - A[k] for k in range(3)]
        cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
              u[0] * v[1] - u[1] * v[0])
        ln = math.sqrt(sum(t * t for t in cr)) or 1.0
        out.append(([t / ln for t in cr], ln / 2.0, (a, bb, cc), None))
    return c, out


# Which slot of a node is which octant. Bit 0 is x, bit 1 is z, bit 2 is y -
# not the obvious x/y/z. Checked by walking the whole tree and asking whether
# every polygon's AABB really does cross the cell of the leaf holding it:
# 56572 of atlanta's 56573 entries do (the one miss sits exactly on a boundary),
# against 0.8% for the x/y/z reading.
SPLIT = {9: (0, 2, 1), 10: (0, 2)}


def grid(b, node, kind=9):
    """(cube_lo, cube_hi, cell) - the box the tree actually subdivides.

    It is NOT the node's AABB at +0x08. The payload carries its own box at
    +0x1C..+0x30 and a cell size at +0x18, and that box is a CUBE: the node AABB
    centred and grown until every side equals its longest one. Its side divided
    by the cell size lands on a power of two in all four cities - 512 in
    atlanta, 1024 in detroit and san diego, 2048 in tokyo - which is the depth
    the tree subdivides to.

    This is why the AABB must never be widened to fit new geometry, by the way:
    it is the seed the cube is derived from.
    """
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    return (list(struct.unpack_from('<3f', b.data, pay + 0x1C)),
            list(struct.unpack_from('<3f', b.data, pay + 0x28)),
            struct.unpack_from('<f', b.data, pay + 0x18)[0])


def cells(b, node, lo, hi, kind=9):
    """[(start, count, slot, cell_lo, cell_hi)] for every leaf the box reaches.

    A geometric descent, so it finds the leaves a piece of geometry BELONGS in
    rather than the ones that happen to hold something nearby.
    """
    root, _idxoff, _nidx = b.payload(node, kind)
    bits = SPLIT[kind]
    fan = 1 << len(bits)
    clo, chi, _cell = grid(b, node, kind)
    out, stack = [], [(root, clo, chi, 0)]
    while stack:
        slot, l, h, d = stack.pop()
        if any(lo[a] > h[a] or hi[a] < l[a] for a in range(3)):
            continue
        v = b.u32(slot)
        if v & 1:
            out.append((v >> 16, (v & 0xFFFF) >> 2, slot, list(l), list(h)))
            continue
        if not b.valid(v) or d > 16:
            continue
        base = b.off(v)
        mid = [(l[a] + h[a]) / 2 for a in range(3)]
        for k in range(fan):
            nl, nh = list(l), list(h)
            for axis, sh in enumerate(bits):
                if (k >> sh) & 1:
                    nl[axis] = mid[axis]
                else:
                    nh[axis] = mid[axis]
            stack.append((base + 4 * k, nl, nh, d + 1))
    return out


def insert(b, new_verts, new_polys, kind=9, material_global_id=17):
    """Add geometry to the collision and hook it into the tree.

    `new_polys` is [(normal, area, (i0, i1, i2), neighbours[, local_material])]
    with the indices numbered from 0 inside `new_verts`; they are rebased on the
    way in, and `neighbours` may be None for an isolated polygon.  If a polygon
    omits its local material index, `material_global_id` is resolved through the
    donor palette (road by default). Isolated is legal - 196 of atlanta's 21617
    polygons have all four neighbour slots at 0xFFFF.

    A triangle is written the way retail writes one: fourth index 0, fourth
    neighbour 0xFFFF (9723 of atlanta's polygons look exactly like that). The
    area field is not decorative - it matches the geometry to within 0.0000 on
    retail triangles - so `box_polys` computes it.

    The vertex array, the polygon array and the leaf index list are each rebuilt
    at the end of the file and repointed, so nothing that already exists moves.

    Each polygon is filed in every leaf whose cell its AABB crosses, which is
    the rule the retail data follows, and the leaf ranges partition the index
    list, so re-basing is just: shift each start past whatever was spliced in
    ahead of it, and grow the count of the leaves that got something.

    The 16-bit start is the real budget here. Atlanta already spends 56573 of
    the 65535 entries a start can address; the whole building box costs 312.
    """
    node = b.trees[kind]
    nv, npoly = b.u32(node + 0x4C), b.u32(node + 0x50)
    vat, pat = b.off(b.u32(node + 0x68)), b.off(b.u32(node + 0x6C))
    _root, idxoff, nidx = b.payload(node, kind)
    idx = list(struct.unpack_from('<%dH' % nidx, b.data, idxoff))

    pbox = []
    for poly in new_polys:
        _nrm, _area, vi, _nb = poly[:4]
        pts = [new_verts[k] for k in vi]
        pbox.append(([min(p[a] for p in pts) for a in range(3)],
                     [max(p[a] for p in pts) for a in range(3)]))
    nlo = [min(bx[0][a] for bx in pbox) for a in range(3)]
    nhi = [max(bx[1][a] for bx in pbox) for a in range(3)]

    blocks = {}
    for start, count, slot, clo, chi in cells(b, node, nlo, nhi, kind):
        got = [npoly + k for k, (plo, phi) in enumerate(pbox)
               if all(plo[a] <= chi[a] and phi[a] >= clo[a] for a in range(3))]
        if got:
            blocks[slot] = (start, count, got)
    if not blocks:
        raise ValueError('the new geometry reaches no leaf - is it inside the '
                         'city at all?')

    # The ranges of the leaves that hold something partition the index list
    # exactly: atlanta has 9892 leaves of which 4312 are empty, and the other
    # 5580 cover all 56573 entries with no gap, no overlap and no entry used
    # twice. An empty leaf's start is meaningless, so it is never spliced into -
    # it is given a range of its own at the end of the list instead.
    all_items = b.leaves(node, kind)
    full_ = [t for t in all_items if t[1]]
    if sum(c for _s, c, _sl in full_) != nidx:
        raise ValueError('leaf ranges do not partition the index list (%d vs %d)'
                         % (sum(c for _s, c, _sl in full_), nidx))

    seams = sorted((st + ct, blocks[sl][2]) for st, ct, sl in full_
                     if sl in blocks)
    new_idx, prev = [], 0
    for pos, block in seams:
        new_idx.extend(idx[prev:pos])
        new_idx.extend(block)
        prev = pos
    new_idx.extend(idx[prev:])

    def shift(x):
        return sum(len(bl) for pos, bl in seams if pos <= x)

    # a chosen leaf that was empty gets a fresh range parked at the end
    end = {}
    for start, count, slot in all_items:
        if count or slot not in blocks:
            continue
        end[slot] = len(new_idx)
        new_idx.extend(blocks[slot][2])

    if len(new_idx) > 0xFFFF:
        raise ValueError('index list would reach %d, past what a 16-bit start '
                         'can address' % len(new_idx))

    data = bytearray(b.data)
    material_ids = b.materials(node)
    if material_global_id not in material_ids:
        raise ValueError('global material id %d is absent from the donor palette'
                         % material_global_id)
    default_material = material_ids.index(material_global_id)

    def stick(blob, align=16):
        while len(data) % align:
            data.append(0xCD)
        off = len(data)
        data.extend(blob)
        return off

    vblob = bytearray(data[vat:vat + 12 * nv])
    for v in new_verts:
        vblob += struct.pack('<3f', v[0], v[1], v[2])
    pblob = bytearray(data[pat:pat + 32 * npoly])
    for poly in new_polys:
        nrm, area, vi, nb = poly[:4]
        material_index = poly[4] if len(poly) > 4 else default_material
        if material_index >= len(material_ids):
            raise ValueError('local material index %d is outside donor palette'
                             % material_index)
        pblob += struct.pack('<3f', nrm[0], nrm[1], nrm[2])
        pblob += pack_area_with_material(area, material_index)
        pblob += struct.pack('<4H', vi[0] + nv, vi[1] + nv, vi[2] + nv, 0)
        if nb:
            r = [x + npoly if x != 0xFFFF else 0xFFFF for x in list(nb)[:3]]
            pblob += struct.pack('<4H', r[0], r[1], r[2], 0xFFFF)
        else:
            pblob += struct.pack('<4H', 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)

    nvoff = stick(bytes(vblob))
    npoff = stick(bytes(pblob))
    nioff = stick(struct.pack('<%dH' % len(new_idx), *new_idx))

    def V(o):
        return o + b.base

    struct.pack_into('<I', data, node + 0x68, V(nvoff))
    struct.pack_into('<I', data, node + 0x6C, V(npoff))
    struct.pack_into('<I', data, node + 0x4C, nv + len(new_verts))
    struct.pack_into('<I', data, node + 0x50, npoly + len(new_polys))
    struct.pack_into('<I', data, 0x80, nv + len(new_verts))
    struct.pack_into('<I', data, 0x84, npoly + len(new_polys))
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    struct.pack_into('<I', data, pay + 0x08, npoly + len(new_polys))
    struct.pack_into('<I', data, pay + 0x0C, nv + len(new_verts))
    struct.pack_into('<I', data, pay + 0x10, len(new_idx))
    struct.pack_into('<I', data, pay + 0x14, V(nioff))

    for start, count, slot in all_items:
        got = blocks.get(slot)
        if count:
            start += shift(start)
            count += len(got[2]) if got else 0
        elif slot in end:
            start, count = end[slot], len(got[2])
        struct.pack_into('<I', data, slot, (start << 16) | (count << 2) | 1)

    # The node AABB is deliberately left alone - see grid().
    #
    # Pad the tail too, not just the gaps between blobs. Every one of the 6718
    # .pck files that ship with the game is a multiple of 16 bytes; leaving the
    # last blob short made the body size an odd 2294558, which is not even
    # 4-aligned - exactly the sort of thing a console loader is entitled to
    # choke on.
    while len(data) % 16:
        data.append(0xCD)
    struct.pack_into('<I', data, 0x0C, len(data) - 0x80)
    b.data = bytes(data)
    return dict(leaves=len(blocks), polys=len(new_polys), verts=len(new_verts),
                indices=len(new_idx), added=len(new_idx) - nidx,
                box=(nlo, nhi))


def check(b, kind=9):
    """Recompute every area from the vertices and compare with what is stored."""
    node = b.trees[kind]
    verts, polys = b.geometry(node)
    bad = 0
    for n, area, idx, _nb in polys:
        p = [verts[i] for i in idx]
        s2 = 0.0
        for j in range(len(p)):
            k = (j + 1) % len(p)
            s2 += ((p[j][1] * p[k][2] - p[j][2] * p[k][1]) * n[0]
                   + (p[j][2] * p[k][0] - p[j][0] * p[k][2]) * n[1]
                   + (p[j][0] * p[k][1] - p[j][1] * p[k][0]) * n[2])
        if abs(abs(s2 / 2) - area) > max(0.02, 0.005 * area):
            bad += 1
    return len(polys) - bad, bad


def report(b):
    print('%s  %d bytes' % (os.path.basename(b.path), len(b.data)))
    print('  root counts: %d vertices, %d polygons' % (b.verts_total, b.polys_total))
    for kind in sorted(b.trees):
        node = b.trees[kind]
        lo, hi = b.aabb(node)
        verts, polys = b.geometry(node)
        tris = sum(1 for _n, _a, i, _x in polys if len(i) == 3)
        edges = sum(1 for _n, _a, _i, nb in polys for x in nb if x == 0xFFFF)
        print('\n  type %-2d  %s' % (kind, TREE_KIND.get(kind, 'unknown')))
        print('     box    (%.0f %.0f %.0f) .. (%.0f %.0f %.0f)   %.0f tall'
              % (lo + hi + (hi[1] - lo[1],)))
        print('     mesh   %d vertices, %d polygons (%d triangles, %d quads)'
              % (len(verts), len(polys), tris, len(polys) - tris))
        print('     edges  %d boundary sides' % edges)
        print('     ids    %s' % b.materials(node))
        try:
            lv = b.leaves(node, kind)
            _r, _i, ni = b.payload(node, kind)
            print('     tree   %d leaves over %d indices, %d references'
                  % (len(lv), ni, sum(c for _s, c, _o in lv)))
        except Exception as exc:
            print('     tree   unreadable (%s)' % exc)


def write_obj(b, kind, path):
    node = b.trees[kind]
    verts, polys = b.geometry(node)
    with open(path, 'w') as f:
        f.write('# %s - %s\n' % (os.path.basename(b.path), TREE_KIND.get(kind, kind)))
        f.write('# %d vertices, %d polygons\n' % (len(verts), len(polys)))
        for x, y, z in verts:
            f.write('v %.4f %.4f %.4f\n' % (x, y, z))
        # OBJ normals are per vertex; the bound stores one per face, so each
        # polygon gets its own and every corner points at it
        for n, _a, _i, _x in polys:
            f.write('vn %.6f %.6f %.6f\n' % n)
        f.write('g collision\n' if kind == 9 else 'g roads\n')
        for k, (_n, _a, idx, _x) in enumerate(polys):
            f.write('f %s\n' % ' '.join('%d//%d' % (i + 1, k + 1) for i in idx))
    print('%s: %d vertices, %d faces' % (path, len(verts), len(polys)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bnd')
    ap.add_argument('--obj', help='write the mesh as Wavefront OBJ')
    ap.add_argument('--roads', action='store_true',
                    help='export the quadtree (roads) instead of the collision')
    ap.add_argument('--lift', metavar='X,Z,RADIUS,DY',
                    help='raise vertices within RADIUS of (X,Z) by DY; '
                         'RADIUS 0 lifts the whole mesh, which is the only '
                         'edit that leaves no polygon half-moved')
    ap.add_argument('--box', metavar='X0,Y0,Z0,X1,Y1,Z1',
                    help='add a solid box of collision (12 triangles) and hook '
                         'it into every octree leaf it overlaps - this is what '
                         'gives an imported building something to hit')
    ap.add_argument('--out', help='where to write the edited .pck')
    a = ap.parse_args()
    b = Bound(a.bnd)
    report(b)
    if a.lift:
        cx, cz, rad, dy = (float(x) for x in a.lift.split(','))
        ok0, bad0 = check(b)
        n = lift(b, cx, cz, rad, dy)
        ok1, bad1 = check(b)
        print()
        print('lifted %d vertices within %.0f of (%.0f, %.0f) by %+.1f' % (n, rad, cx, cz, dy))
        print('areas still matching: %d -> %d  (mismatched %d -> %d)' % (ok0, ok1, bad0, bad1))
        out = a.out or (os.path.splitext(a.bnd)[0] + '_lift.pck')
        open(out, 'wb').write(b.data)
        print('wrote %s' % out)
    if a.box:
        q = [float(x) for x in a.box.split(',')]
        lo = [min(q[i], q[i + 3]) for i in range(3)]
        hi = [max(q[i], q[i + 3]) for i in range(3)]
        # clamped into the root box on purpose - see the note in insert()
        rlo, rhi = b.aabb(b.trees[9])
        clamped = [max(lo[i], rlo[i]) for i in range(3)], \
                  [min(hi[i], rhi[i]) for i in range(3)]
        if clamped[0] != lo or clamped[1] != hi:
            print()
            print('box clamped into the root box %s .. %s'
                  % (['%.1f' % x for x in rlo], ['%.1f' % x for x in rhi]))
        lo, hi = clamped
        v, p = box_polys(lo, hi)
        r = insert(b, v, p)
        print()
        print('box %s .. %s' % (['%.1f' % x for x in lo], ['%.1f' % x for x in hi]))
        print('added %d polygons across %d leaves; index list %d -> %d (+%d, '
              'of the %d a 16-bit start can address)'
              % (r['polys'], r['leaves'], r['indices'] - r['added'],
                 r['indices'], r['added'], 0xFFFF))
        out = a.out or (os.path.splitext(a.bnd)[0] + '_box.pck')
        open(out, 'wb').write(b.data)
        print('wrote %s' % out)
    if a.obj:
        kind = 10 if a.roads else 9
        if kind not in b.trees:
            raise SystemExit('this file has no type %d tree' % kind)
        print()
        write_obj(b, kind, a.obj)


if __name__ == '__main__':
    main()
