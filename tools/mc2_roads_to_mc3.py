#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replace only the type10 quadtree of a ``*_bnd.pck`` with MC2's streets.

The source ``losangeles.bnd`` is a collision OTGrid; it carries no separate road
quadtree. This tool extracts the horizontal polygons whose material ends in
``roadSG``, removes the copies repeated across OTGrid cells, rebuilds the
neighbours and creates a valid 2D quadtree. The donor's type9 stays byte for
byte intact; the container's global counts are still type9's, as in the four
retail files.

The result is experimental: it reproduces the physical geometry of LA's
streets, not the original simplified mesh MC2 did not provide in the
extraction. Reopen it with ``mc3_bound.py`` and use the ``--audit`` produced
before installing.
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys

from mc2_bnd_parse import read_otgrid
import mc3_bound as MB


def normal_and_area(pts):
    a, b, c = pts[:3]
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1],
         u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0])
    length = math.sqrt(sum(x * x for x in n))
    if length < 1e-10:
        return None, 0.0
    n = tuple(x / length for x in n)
    twice = 0.0
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        twice += ((p[1] * q[2] - p[2] * q[1]) * n[0]
                  + (p[2] * q[0] - p[0] * q[2]) * n[1]
                  + (p[0] * q[1] - p[1] * q[0]) * n[2])
    return n, abs(twice) / 2.0


def prepare_roads(path, suffixes):
    source_verts, source_polys, _materials, header = read_otgrid(path)
    wanted = {x.lower() for x in suffixes}
    selected = [(idx, mat) for idx, mat in source_polys
                if mat.split(':')[-1].lower() in wanted]
    verts = []
    vert_map = {}
    polygons = []
    seen_polys = set()
    stats = dict(source_vertices=len(source_verts),
                 source_polygons=len(source_polys), selected=len(selected),
                 duplicate_polygons=0, degenerate_polygons=0,
                 nonhorizontal_polygons=0, reversed_polygons=0)

    for source_idx, material in selected:
        remapped = []
        for old in source_idx:
            value = tuple(source_verts[old])
            if value not in vert_map:
                vert_map[value] = len(verts)
                verts.append(value)
            remapped.append(vert_map[value])
        if len(set(remapped)) != len(remapped):
            stats['degenerate_polygons'] += 1
            continue
        pts = [verts[i] for i in remapped]
        normal, area = normal_and_area(pts)
        if normal is None:
            stats['degenerate_polygons'] += 1
            continue
        if abs(normal[1]) <= 0.7:
            stats['nonhorizontal_polygons'] += 1
            continue
        if normal[1] < 0:
            remapped.reverse()
            pts = [verts[i] for i in remapped]
            normal, area = normal_and_area(pts)
            stats['reversed_polygons'] += 1
        key = tuple(sorted(remapped))
        if key in seen_polys:
            stats['duplicate_polygons'] += 1
            continue
        seen_polys.add(key)
        polygons.append((normal, area, tuple(remapped), material))

    used = sorted({i for _n, _a, idx, _m in polygons for i in idx})
    compact = {old: new for new, old in enumerate(used)}
    verts = [verts[i] for i in used]
    polygons = [(n, area, tuple(compact[i] for i in idx), material)
                for n, area, idx, material in polygons]
    if not polygons:
        raise ValueError('no street polygon selected')
    if len(verts) > 0xFFFF or len(polygons) > 0xFFFF:
        raise ValueError('type10 exceeds the u16 limit: %d verts, %d polys' %
                         (len(verts), len(polygons)))

    # Rebuild the edge adjacency after the deduplication. The source repeats
    # whole cells, so its local indices cannot be used directly as a key.
    neighbors = [[0xFFFF] * len(idx) for _n, _a, idx, _m in polygons]
    edges = {}
    for pi, (_n, _a, idx, _m) in enumerate(polygons):
        for ei, a in enumerate(idx):
            b = idx[(ei + 1) % len(idx)]
            edges.setdefault((min(a, b), max(a, b)), []).append((pi, ei))
    paired = nonmanifold = 0
    for uses in edges.values():
        if len(uses) == 2 and uses[0][0] != uses[1][0]:
            (a, ae), (b, be) = uses
            neighbors[a][ae] = b
            neighbors[b][be] = a
            paired += 1
        elif len(uses) > 2:
            nonmanifold += 1
    stats.update(output_vertices=len(verts), output_polygons=len(polygons),
                 paired_edges=paired, boundary_edges=sum(x == 0xFFFF
                 for row in neighbors for x in row),
                 nonmanifold_edges=nonmanifold, header=header)
    return verts, polygons, neighbors, stats


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
            child_lo = [midx if bx else lo[0], lo[1],
                        midz if bz else lo[2]]
            child_hi = [hi[0] if bx else midx, hi[1],
                        hi[2] if bz else midz]
            hits = [i for i in ids
                    if boxes[i][0][0] <= child_hi[0]
                    and boxes[i][1][0] >= child_lo[0]
                    and boxes[i][0][2] <= child_hi[2]
                    and boxes[i][1][2] >= child_lo[2]]
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


def build(args):
    suffixes = [x.strip() for x in args.materials.split(',') if x.strip()]
    verts, polys, neighbors, source_stats = prepare_roads(args.mc2, suffixes)
    boxes = []
    for _normal, _area, indices, _material in polys:
        pts = [verts[i] for i in indices]
        boxes.append(([min(p[a] for p in pts) for a in range(3)],
                      [max(p[a] for p in pts) for a in range(3)]))
    lo = [min(v[a] for v in verts) for a in range(3)]
    hi = [max(v[a] for v in verts) for a in range(3)]
    center = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
    side = max(hi[a] - lo[a] for a in range(3))
    grid_lo = [center[a] - side / 2.0 for a in range(3)]
    grid_hi = [center[a] + side / 2.0 for a in range(3)]
    radius = max(math.dist(v, center) for v in verts) * 1.0002
    origin_radius = radius + math.sqrt(sum(x * x for x in center))

    blocks, root, actual_depth = build_quadtree(
        boxes, grid_lo, grid_hi, args.depth, args.per_leaf)
    leaves = walk_leaves(blocks, root)
    refs = []
    for leaf in leaves:
        leaf.start, leaf.count = len(refs), len(leaf.ids)
        refs.extend(leaf.ids)
    if len(refs) > 0xFFFF:
        raise ValueError('%d references exceed the u16 start limit; '
                         'lower --depth or raise --per-leaf' % len(refs))
    cell = side / float(2 ** actual_depth)

    donor = MB.Bound(args.donor)
    node = donor.trees[10]
    material_ids = donor.materials(node)
    road_global_id = MB.GLOBAL_MATERIAL_IDS['road']
    if road_global_id not in material_ids:
        raise ValueError('the donor type10 does not contain road (global id 17)')
    road_material_index = material_ids.index(road_global_id)
    payload = donor.off(donor.u32(node + 0x78))
    before = donor.data
    data = bytearray(before)

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
    for pi, (normal, area, indices, _material) in enumerate(polys):
        vi = list(indices) + ([0] if len(indices) == 3 else [])
        nb = list(neighbors[pi]) + ([0xFFFF] if len(indices) == 3 else [])
        pblob += struct.pack('<3f', normal[0], normal[1], normal[2])
        pblob += MB.pack_area_with_material(area, road_material_index)
        pblob += struct.pack('<4H', *vi)
        pblob += struct.pack('<4H', *nb)
    vert_off = append(vblob)
    poly_off = append(bytes(pblob))
    index_off = append(struct.pack('<%dH' % len(refs), *refs))
    # The executable gets the pointer to the four slots, but each vector was
    # serialised by the game with the 16-byte array header in front of it:
    # count=4 followed by 0xCD. Keeping that layout stops helper routines
    # (including freeing/reinitialisation) from reading garbage before the block.
    array4_header = struct.pack('<I', 4) + bytes([0xCD]) * 12
    block_offs = [append(array4_header + bytes(16)) + 16 for _ in blocks]

    def slot_value(item):
        if isinstance(item, Leaf):
            if item.count > 0x3FFF:
                raise ValueError('a leaf with %d references overflows the 14-bit field'
                                 % item.count)
            return (item.start << 16) | (item.count << 2) | 1
        return virtual(block_offs[item[1]])

    for bi, children in enumerate(blocks):
        for child, item in enumerate(children):
            struct.pack_into('<I', data, block_offs[bi] + child * 4,
                             slot_value(item))
    root_value = slot_value(root)
    root_off = append(struct.pack('<I', root_value))

    struct.pack_into('<3f', data, node + 0x08, *lo)
    struct.pack_into('<3f', data, node + 0x14, *hi)
    struct.pack_into('<3f', data, node + 0x20, *center)
    struct.pack_into('<3f', data, node + 0x2C, *center)
    struct.pack_into('<f', data, node + 0x38, radius)
    struct.pack_into('<f', data, node + 0x3C, origin_radius)
    struct.pack_into('<I', data, node + 0x4C, len(verts))
    struct.pack_into('<I', data, node + 0x50, len(polys))
    struct.pack_into('<I', data, node + 0x68, virtual(vert_off))
    struct.pack_into('<I', data, node + 0x6C, virtual(poly_off))
    # +0x7C is the direct root used by the road query. +0x78 leads to the
    # payload, whose first field points to a slot holding the same root.
    # Leaving +0x7C as the donor's made the geometry LA's but kept the search in
    # Tokyo's quadtree: sidewalks (type9) collided and LA's streets did not.
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

    # Outside the size header and the known type10 fields, no old byte may
    # change. That also proves type9 stayed intact.
    allowed = set(range(0x0C, 0x10))
    for start, length in ((node + 0x08, 0x38), (node + 0x4C, 8),
                          (node + 0x68, 8), (node + 0x7C, 4),
                          (payload + 0x00, 4),
                          (payload + 0x08, 0x2C)):
        allowed.update(range(start, start + length))
    unexpected = [i for i, (old, new) in enumerate(zip(before, data))
                  if old != new and i not in allowed]
    if unexpected:
        raise ValueError('unexpected bytes changed before the append: %s' %
                         unexpected[:20])

    with open(args.out, 'wb') as stream:
        stream.write(data)
    result = validate(args.out, donor, boxes, source_stats, suffixes,
                      road_material_index)
    result.update(dict(depth_requested=args.depth, depth_actual=actual_depth,
                       per_leaf=args.per_leaf, blocks=len(blocks),
                       leaves=len(leaves), references=len(refs),
                       duplication=len(refs) / float(len(polys)),
                       cell_size=cell, aabb=[lo, hi], grid=[grid_lo, grid_hi],
                       radius=radius, origin_radius=origin_radius,
                       donor_sha256=hashlib.sha256(before).hexdigest().upper(),
                       output_sha256=hashlib.sha256(bytes(data)).hexdigest().upper()))
    if args.audit:
        with open(args.audit, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    print('source: %d road polys -> %d unique, %d vertices; %d flipped' %
          (source_stats['selected'], len(polys), len(verts),
           source_stats['reversed_polygons']))
    print('quadtree: %d blocks, %d leaves, %d refs (%.2fx), depth %d, cell %.4f' %
          (len(blocks), len(leaves), len(refs),
           len(refs) / float(len(polys)), actual_depth, cell))
    print('validated: type9 intact; type10 node/payload/leaves/areas/neighbours/containment OK')
    print('written:', args.out, len(data), 'bytes')
    print('SHA256:', result['output_sha256'])
    return result


def validate(path, donor, expected_boxes, source_stats, suffixes,
             road_material_index):
    bound = MB.Bound(path)
    node = bound.trees[10]
    payload = bound.off(bound.u32(node + 0x78))
    verts, polys = bound.geometry(node)
    expected_v = source_stats['output_vertices']
    expected_p = source_stats['output_polygons']
    expected_refs = bound.u32(payload + 0x10)
    checks = {
        'root_vertices_unchanged': bound.verts_total == donor.verts_total,
        'root_polygons_unchanged': bound.polys_total == donor.polys_total,
        'node_vertices': bound.u32(node + 0x4C) == expected_v,
        'node_polygons': bound.u32(node + 0x50) == expected_p,
        'payload_vertices': bound.u32(payload + 0x0C) == expected_v,
        'payload_polygons': bound.u32(payload + 0x08) == expected_p,
        'geometry_vertices': len(verts) == expected_v,
        'geometry_polygons': len(polys) == expected_p,
        'positive_ground_normals': all(p[0][1] > 0.7 for p in polys),
        'direct_root_matches_payload': (
            bound.u32(node + 0x7C) == bound.u32(bound.payload(node, 10)[0])),
        'road_material': all(i == road_material_index
                             for i in bound.material_indices(node)),
        'material_index_in_palette': all(i < len(bound.materials(node))
                                         for i in bound.material_indices(node)),
    }
    old_v, old_p = donor.geometry(donor.trees[9])
    new_v, new_p = bound.geometry(bound.trees[9])
    checks['type9_geometry_unchanged'] = old_v == new_v and old_p == new_p
    area_ok, area_bad = MB.check(bound, 10)
    checks['areas'] = area_bad == 0 and area_ok == expected_p

    root_slot, index_off, index_count = bound.payload(node, 10)
    index = list(struct.unpack_from('<%dH' % index_count, bound.data, index_off))
    checks['payload_reference_count'] = index_count == expected_refs
    checks['reference_range'] = all(i < expected_p for i in index)
    checks['all_polygons_referenced'] = set(index) == set(range(expected_p))
    leaves = [q for q in bound.leaves(node, 10) if q[1]]
    cursor = 0
    for start, count, _slot in sorted(leaves):
        if start != cursor:
            break
        cursor += count
    checks['leaf_partition'] = cursor == index_count

    # Check each reference geometrically against the leaf cell.
    grid_lo, grid_hi, _cell = MB.grid(bound, node, 10)
    stack = [(root_slot, grid_lo, grid_hi)]
    containment_bad = 0
    seen_slots = set()
    while stack:
        slot, lo, hi = stack.pop()
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        value = bound.u32(slot)
        if value & 1:
            start, count = value >> 16, (value & 0xFFFF) >> 2
            for pi in index[start:start + count]:
                box = expected_boxes[pi]
                if (box[0][0] > hi[0] or box[1][0] < lo[0]
                        or box[0][2] > hi[2] or box[1][2] < lo[2]):
                    containment_bad += 1
            continue
        base = bound.off(value)
        midx = (lo[0] + hi[0]) / 2.0
        midz = (lo[2] + hi[2]) / 2.0
        for child in range(4):
            bx, bz = child & 1, (child >> 1) & 1
            child_lo = [midx if bx else lo[0], lo[1],
                        midz if bz else lo[2]]
            child_hi = [hi[0] if bx else midx, hi[1],
                        hi[2] if bz else midz]
            stack.append((base + child * 4, child_lo, child_hi))
    checks['cell_containment'] = containment_bad == 0

    reciprocal_bad = 0
    for pi, poly in enumerate(polys):
        for other in poly[3][:len(poly[2])]:
            if other == 0xFFFF:
                continue
            if other >= len(polys) or pi not in polys[other][3]:
                reciprocal_bad += 1
    checks['neighbor_reciprocity'] = reciprocal_bad == 0
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError('validation failed: ' + ', '.join(failed))
    return dict(path=os.path.abspath(path), materials=suffixes,
                source=source_stats, checks=checks,
                area_bad=area_bad, containment_bad=containment_bad,
                reciprocal_bad=reciprocal_bad,
                type9_vertices=bound.verts_total,
                type9_polygons=bound.polys_total,
                type10_vertices=expected_v, type10_polygons=expected_p,
                road_material_index=road_material_index,
                material_global_ids=bound.materials(node))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--mc2', required=True)
    ap.add_argument('--donor', required=True,
                    help='PCK whose type9 is kept and type10 replaced')
    ap.add_argument('--out', required=True)
    ap.add_argument('--audit')
    ap.add_argument('--materials', default='roadSG',
                    help='comma-separated material suffixes')
    ap.add_argument('--depth', type=int, default=8)
    ap.add_argument('--per-leaf', type=int, default=22)
    args = ap.parse_args()
    build(args)


if __name__ == '__main__':
    main()
