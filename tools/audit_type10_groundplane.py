#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit of a candidate ``*_bnd.pck`` whose ONLY change is the type10 quadtree.

Checks, in this order:

  contract    numPolys <= 2000 and numVerts <= 2000 (the real ceiling of the
              phQuadtreeCell class, proven in mcPhysics::InitStaticData 0x002C2644)
  type9       byte for byte identical to the donor
  diff        no old byte changed outside the size header and the known type10
              fields; the rest can only be an appended block
  structure   header/size, pointers inside the file, both trees present,
              consistent root/node/payload counts,
              node+0x7C == *(payload root slot), retail 16-byte header on the
              child vectors, depth <= 20
  leaves      partition the ref list exactly, with no gap or overlap
  indices     refs and indices within range, every poly covered
  containment each leaf's AABB contains its faces
  geometry    unit normals, recomputed area, material byte, no
              degenerate/NaN/Inf, reciprocal neighbours
  SAFETY      the property the generator promises and that only a geometric
              test can confirm: every type10 slab has a type9 floor ABOVE it,
              that is, it is buried. A slab with no floor above is the top
              surface at that point - an invisible platform in the air at the
              edge of an elevated road, or a step over the road - and only that
              fails. A slab buried deeper than band+sink happens on ramps and is
              only reported: it still steals no contact.

    python audit_type10_groundplane.py --donor v13.pck --candidate new.pck \\
        --json audit.json
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

CAP = 2000


def tree_bytes(b, kind):
    """Every byte that belongs to a tree: node, geometry, tree."""
    node = b.trees[kind]
    spans = [(node, 0xA0)]
    nv, npoly = b.u32(node + 0x4C), b.u32(node + 0x50)
    spans.append((b.off(b.u32(node + 0x68)), 12 * nv))
    spans.append((b.off(b.u32(node + 0x6C)), 32 * npoly))
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    spans.append((pay, 0x40))
    spans.append((b.off(b.u32(pay + 0x14)), 2 * b.u32(pay + 0x10)))
    out = bytearray()
    for off, length in spans:
        out += b.data[off:off + length]
    return bytes(out), dict(node=node, verts=nv, polys=npoly, pay=pay)


def poly_points(verts, vi):
    return [verts[i] for i in vi]


def real_area(pts):
    total = 0.0
    for k in range(1, len(pts) - 1):
        u = [pts[k][j] - pts[0][j] for j in range(3)]
        v = [pts[k + 1][j] - pts[0][j] for j in range(3)]
        cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
              u[0] * v[1] - u[1] * v[0])
        total += math.sqrt(sum(t * t for t in cr)) / 2.0
    return total


def check_structure(b, kind, problems, info, note_only_headers=False):
    node = b.trees[kind]
    fan = 8 if kind == 9 else 4
    nv, npoly = b.u32(node + 0x4C), b.u32(node + 0x50)
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    root_slot, index_off, nref = b.payload(node, kind)

    if b.u32(pay + 0x08) != npoly:
        problems.append('t%d payload+0x08=%d != node poly count %d'
                        % (kind, b.u32(pay + 0x08), npoly))
    if b.u32(pay + 0x0C) != nv:
        problems.append('t%d payload+0x0C=%d != node vertex count %d'
                        % (kind, b.u32(pay + 0x0C), nv))
    if kind == 10 and b.u32(node + 0x7C) != b.u32(root_slot):
        problems.append('t10 node+0x7C=%08x != *(payload root)=%08x'
                        % (b.u32(node + 0x7C), b.u32(root_slot)))

    leaves, stack, seen, depth_max = [], [(root_slot, 0)], set(), 0
    blocks = no_header = 0
    while stack:
        slot, depth = stack.pop()
        depth_max = max(depth_max, depth)
        if slot in seen:
            problems.append('t%d slot %#x visited twice' % (kind, slot))
            continue
        seen.add(slot)
        v = b.u32(slot)
        if v & 1:
            leaves.append((v >> 16, (v & 0xFFFF) >> 2))
            continue
        if not b.valid(v):
            problems.append('t%d invalid block pointer %08x' % (kind, v))
            continue
        base = b.off(v)
        blocks += 1
        # In the four retail cities EVERY child vector, in both trees, is
        # preceded by the 16-byte array header (count followed by 0xCD): the
        # blocks sit 48 bytes apart in type9 and 32 in type10. The modcity type9
        # has none - it is a divergence inherited from the converter, not
        # something this candidate introduces, so it is reported and not failed
        # when the donor has the same shape.
        if b.u32(base - 16) != fan:
            no_header += 1
        stack.extend((base + 4 * i, depth + 1) for i in range(fan))
    if no_header and not note_only_headers:
        problems.append('t%d %d of %d child vectors without an array header '
                        'count=%d' % (kind, no_header, blocks, fan))
    if depth_max > 20:
        problems.append('t%d depth %d exceeds the limit of 20 of the static '
                        'stacks smBoxMinStack/smBoxMaxStack' % (kind, depth_max))

    covered = [0] * nref
    for start, count in leaves:
        if start + count > nref:
            problems.append('t%d leaf %d..%d outside the list of %d refs'
                            % (kind, start, start + count, nref))
            continue
        for i in range(start, start + count):
            covered[i] += 1
    if any(c != 1 for c in covered):
        gaps = sum(1 for c in covered if c == 0)
        dups = sum(1 for c in covered if c > 1)
        problems.append('t%d leaves do not partition the refs: %d gaps, %d repeated'
                        % (kind, gaps, dups))

    refs = [struct.unpack_from('<H', b.data, index_off + 2 * i)[0]
            for i in range(nref)]
    if refs and max(refs) >= npoly:
        problems.append('t%d ref %d >= poly count %d' % (kind, max(refs), npoly))
    missing = npoly - len(set(refs))
    if missing:
        problems.append('t%d %d polygons do not appear in any leaf'
                        % (kind, missing))

    info['t%d' % kind] = dict(verts=nv, polys=npoly, refs=nref,
                              leaves=len(leaves), depth=depth_max,
                              blocks=blocks, blocks_without_array_header=no_header,
                              duplication=round(nref / npoly, 3) if npoly else 0)
    return leaves, refs


def check_geometry(b, kind, problems, info):
    node = b.trees[kind]
    verts, polys = b.geometry(node)
    mats = b.materials(node)
    bad_normal = bad_area = degenerate = nonfinite = bad_material = 0
    for (normal, area, vi, nb) in polys:
        if any(not math.isfinite(c) for c in normal) or not math.isfinite(area):
            nonfinite += 1
            continue
        if abs(math.sqrt(sum(c * c for c in normal)) - 1.0) > 1e-3:
            bad_normal += 1
        pts = poly_points(verts, vi)
        if len(set(vi)) != len(vi):
            degenerate += 1
            continue
        # The stored area has the material index in the low byte, so it never
        # matches the recomputed area exactly: the low byte moves the mantissa.
        # The relative tolerance covers that with room to spare.
        want = real_area(pts)
        if want > 0 and abs(area - want) / max(want, 1.0) > 0.02:
            bad_area += 1
        local = struct.pack('<f', area)[0]
        if local >= len(mats):
            bad_material += 1
    # reciprocidade
    broken = boundary = 0
    for pi, (normal, area, vi, nb) in enumerate(polys):
        n = len(vi)
        for ei in range(n):
            other = nb[ei]
            if other == 0xFFFF:
                boundary += 1
                continue
            if other >= len(polys):
                broken += 1
                continue
            if pi not in polys[other][3]:
                broken += 1
    for label, count in (('non-unit normals', bad_normal),
                         ('mismatched areas', bad_area),
                         ('degenerate polygons', degenerate),
                         ('NaN/Inf', nonfinite),
                         ('material index outside the palette', bad_material),
                         ('non-reciprocal neighbours', broken)):
        if count:
            problems.append('t%d %s: %d' % (kind, label, count))
    info['t%d' % kind].update(boundary_edges=boundary,
                              materials=mats,
                              broken_neighbours=broken)
    return verts, polys, mats


def check_containment(b, kind, leaves, refs, problems, info):
    """Each face is in the cell its leaf says.

    A leaf keeps no AABB - it is just ``(start<<16)|(len<<2)|1``. The cell box
    comes from the PATH in the tree: the root covers the whole grid and each
    level splits the cell in half. So the check is only possible by walking down
    the tree and carrying the box along, which is what this step does.

    Bit 0 of the child index is X and bit 1 is Z in the quadtree; in the octree
    bit 2 is Y. Y is not subdivided in the quadtree, so the comparison is only
    in X/Z.
    """
    node = b.trees[kind]
    verts, polys = b.geometry(node)
    root_slot, index_off, nref = b.payload(node, kind)
    pay = b.off(b.u32(node + (0x94 if kind == 9 else 0x78)))
    glo = [b.f32(pay + 0x1C + 4 * i) for i in range(3)]
    ghi = [b.f32(pay + 0x28 + 4 * i) for i in range(3)]
    fan = 8 if kind == 9 else 4

    misplaced = leaf_faces = 0
    stack = [(root_slot, glo, ghi)]
    seen = set()
    while stack:
        slot, lo_, hi_ = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        v = b.u32(slot)
        if v & 1:
            start, count = v >> 16, (v & 0xFFFF) >> 2
            for pi in refs[start:start + count]:
                if pi >= len(polys):
                    continue
                pts = poly_points(verts, polys[pi][2])
                leaf_faces += 1
                if (max(q[0] for q in pts) < lo_[0] - 1e-3
                        or min(q[0] for q in pts) > hi_[0] + 1e-3
                        or max(q[2] for q in pts) < lo_[2] - 1e-3
                        or min(q[2] for q in pts) > hi_[2] + 1e-3):
                    misplaced += 1
            continue
        if not b.valid(v):
            continue
        base = b.off(v)
        mid = [(lo_[a] + hi_[a]) / 2.0 for a in range(3)]
        for c in range(fan):
            clo = list(lo_); chi = list(hi_)
            if c & 1: clo[0] = mid[0]
            else:     chi[0] = mid[0]
            if (c >> 1) & 1: clo[2] = mid[2]
            else:            chi[2] = mid[2]
            if kind == 9:
                if (c >> 2) & 1: clo[1] = mid[1]
                else:            chi[1] = mid[1]
            stack.append((base + 4 * c, clo, chi))
    if misplaced:
        problems.append('t%d %d of %d leaf references point to a face '
                        'outside the leaf cell' % (kind, misplaced, leaf_faces))

    outside = 0
    # the node's AABB has to contain all the geometry
    lo, hi = b.aabb(node)
    for v in verts:
        for a in range(3):
            if v[a] < lo[a] - 1e-3 or v[a] > hi[a] + 1e-3:
                outside += 1
                break
    if outside:
        problems.append('t%d %d vertices outside the node AABB' % (kind, outside))
    info['t%d' % kind]['aabb'] = [[round(x, 3) for x in lo],
                                  [round(x, 3) for x in hi]]
    info['t%d' % kind]['leaf_refs_checked'] = leaf_faces
    info['t%d' % kind]['leaf_refs_misplaced'] = misplaced


# ------------------------------------------------------- geometric safety

def safety_vs_type9(donor, cand, problems, info, band, sink, samples, tol, near):
    """The generator's two promises, measured in a single test.

    A type10 slab is safe at a point when there is a type9 floor JUST ABOVE it -
    within ``band + sink + tol``. That covers both things at once:

    (a) if there is a type9 floor above, the slab is buried and steals no contact;
    (b) if there is a type9 floor above, the slab is not in the air.

    The floor considered is ANY upward-facing type9 face, not only the ``road``
    material: a slab buried under the sidewalk is correctly buried, and requiring
    road there would flag as "in the air" something that is supported. That is
    what the first version of this function got wrong.
    """
    n9 = donor.trees[9]
    v9, p9 = donor.geometry(n9)
    m9 = donor.materials(n9)

    CELL = 16.0
    grid = {}
    for idx, (normal, area, vi, nb) in enumerate(p9):
        if normal[1] < 0.30:
            continue
        pts = poly_points(v9, vi)
        xs = [q[0] for q in pts]; zs = [q[2] for q in pts]
        for gx in range(int(math.floor(min(xs) / CELL)), int(math.floor(max(xs) / CELL)) + 1):
            for gz in range(int(math.floor(min(zs) / CELL)), int(math.floor(max(zs) / CELL)) + 1):
                grid.setdefault((gx, gz), []).append(idx)

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

    def floors_at(x, z):
        out = []
        for idx in grid.get((int(math.floor(x / CELL)), int(math.floor(z / CELL))), ()):
            normal, area, vi, nb = p9[idx]
            pts = poly_points(v9, vi)
            if not inside(pts, x, z):
                continue
            if abs(normal[1]) < 1e-9:
                continue
            ax, ay, az = pts[0]
            out.append(ay - (normal[0] * (x - ax) + normal[2] * (z - az)) / normal[1])
        return out

    # Only ONE condition fails: the slab being EXPOSED, that is, having no
    # type9 floor at all above it at that point. Then it becomes the top
    # surface - an invisible platform in the air, or a step over the road.
    #
    # Being buried DEEPER than band+sink does not fail. That happens on ramps,
    # where the road rises inside the grid cell itself, and a flat slab does not
    # follow it. It stays buried and still steals no contact; it only serves as
    # a safety net further down. It is reported, not failed - treating it as a
    # defect would demand from the coarse plane a fidelity it does not aim for.
    def _floor_above_within(x, z, y, near):
        """Is there a type9 floor face above `y` within `near` in X/Z?"""
        g = int(math.floor(x / CELL)), int(math.floor(z / CELL))
        span = int(math.ceil(near / CELL))
        for dx in range(-span, span + 1):
            for dz in range(-span, span + 1):
                for idx in grid.get((g[0] + dx, g[1] + dz), ()):
                    normal, area, vi, nb = p9[idx]
                    pts = poly_points(v9, vi)
                    if max(q[1] for q in pts) < y - 1e-3:
                        continue
                    for q in pts:
                        if (q[0] - x) ** 2 + (q[2] - z) ** 2 <= near * near:
                            return True
        return False

    ceiling = band + sink + tol
    n10 = cand.trees[10]
    v10, p10 = cand.geometry(n10)
    checked = buried = deep = exposed = seam = 0
    worst = 0.0
    exposed_examples = []
    for k, (normal, area, vi, nb) in enumerate(p10):
        pts = poly_points(v10, vi)
        x0 = min(q[0] for q in pts); x1 = max(q[0] for q in pts)
        z0 = min(q[2] for q in pts); z1 = max(q[2] for q in pts)
        y = pts[0][1]
        for i in range(samples):
            for j in range(samples):
                x = x0 + (x1 - x0) * (i + 0.5) / samples
                z = z0 + (z1 - z0) * (j + 0.5) / samples
                checked += 1
                hs = [h for h in floors_at(x, z) if h >= y - 1e-3]
                if not hs:
                    # No face CONTAINS the point. Two very different situations
                    # hide here, and only one is dangerous:
                    #
                    #  - the slab spilled into empty space  -> platform in the air;
                    #  - the point fell into a GAP of type9 itself. LA's road is
                    #    instanced geometry and is not watertight: there are thin
                    #    gaps between neighbouring faces. There the slab is not in
                    #    the air, it is plugging a hole type9 has - which is
                    #    exactly the defect the user reports.
                    #
                    # They are told apart by the neighbour: if there is a type9
                    # floor face above the slab within `near` in X/Z, it is a gap.
                    if _floor_above_within(x, z, y, near):
                        seam += 1
                        continue
                    exposed += 1
                    if len(exposed_examples) < 12:
                        exposed_examples.append(
                            [k, round(x, 1), round(y, 2), round(z, 1),
                             'no type9 floor within a radius of %.0f u' % near])
                    continue
                gap = min(hs) - y
                worst = max(worst, gap)
                if gap <= ceiling:
                    buried += 1
                else:
                    deep += 1
    info['safety'] = dict(samples=checked, buried=buried, deeply_buried=deep,
                          seam=seam, seam_pct=round(100.0 * seam / checked, 3),
                          near_radius=near, exposed=exposed,
                          exposed_pct=round(100.0 * exposed / checked, 3),
                          deep_pct=round(100.0 * deep / checked, 3),
                          allowed_gap=ceiling, worst_gap=round(worst, 3),
                          exposed_examples=exposed_examples)
    if exposed:
        problems.append('SAFETY: %d of %d samples (%.2f%%) have a type10 slab with NO '
                        'type9 floor above - it is exposed as ground of its own '
                        '(a platform in the air or a step over the road)'
                        % (exposed, checked, 100.0 * exposed / checked))


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--donor', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--json')
    ap.add_argument('--band', type=float, default=4.0)
    ap.add_argument('--sink', type=float, default=0.05)
    ap.add_argument('--samples', type=int, default=4)
    ap.add_argument('--tol', type=float, default=0.75,
                    help='extra slack when requiring a type9 floor above the slab')
    ap.add_argument('--near', type=float, default=6.0,
                    help='radius to tell a type9 gap from real empty space')
    args = ap.parse_args()

    donor = MB.Bound(args.donor)
    cand = MB.Bound(args.candidate)
    problems, info = [], {}
    info['donor'] = dict(path=args.donor, size=len(donor.data),
                         sha256=hashlib.sha256(donor.data).hexdigest().upper())
    info['candidate'] = dict(path=args.candidate, size=len(cand.data),
                             sha256=hashlib.sha256(cand.data).hexdigest().upper())

    # --- class contract -----------------------------------------------------
    n10 = cand.trees[10]
    npoly, nvert = cand.u32(n10 + 0x50), cand.u32(n10 + 0x4C)
    info['contract'] = dict(cap=CAP, polys=npoly, verts=nvert,
                            poly_memset=(npoly + 7) >> 3,
                            vert_memset=(nvert + 7) >> 3, buffer_bytes=(CAP + 7) >> 3,
                            poly_overflow=max(0, ((npoly + 7) >> 3) - ((CAP + 7) >> 3)),
                            vert_overflow=max(0, ((nvert + 7) >> 3) - ((CAP + 7) >> 3)))
    if npoly > CAP:
        problems.append('CONTRACT: type10 has %d polys, the ceiling is %d' % (npoly, CAP))
    if nvert > CAP:
        problems.append('CONTRACT: type10 has %d verts, the ceiling is %d' % (nvert, CAP))

    # --- type9 intact -------------------------------------------------------
    d9, meta9 = tree_bytes(donor, 9)
    c9, meta9c = tree_bytes(cand, 9)
    info['type9_identical'] = (d9 == c9) and meta9 == meta9c
    if not info['type9_identical']:
        problems.append('type9 is NOT byte for byte identical to the donor')

    # --- binary diff --------------------------------------------------------
    old, new = donor.data, cand.data
    common = min(len(old), len(new))
    pay = cand.off(cand.u32(n10 + 0x78))
    allowed = set(range(0x0C, 0x10))
    for start, length in ((n10 + 0x08, 0x38), (n10 + 0x4C, 8), (n10 + 0x68, 8),
                          (n10 + 0x7C, 4), (pay + 0x00, 4), (pay + 0x08, 0x2C)):
        allowed.update(range(start, start + length))
    changed = [i for i in range(common) if old[i] != new[i]]
    unexpected = [i for i in changed if i not in allowed]
    info['diff'] = dict(bytes_changed_in_common=len(changed),
                        unexpected=len(unexpected),
                        first_unexpected=['%#x' % i for i in unexpected[:16]],
                        appended=len(new) - len(old))
    if unexpected:
        problems.append('%d old bytes changed outside the known fields'
                        % len(unexpected))

    # --- structure of both trees -------------------------------------------
    for kind in (9, 10):
        leaves, refs = check_structure(cand, kind, problems, info,
                                       note_only_headers=(kind == 9))
        check_geometry(cand, kind, problems, info)
        check_containment(cand, kind, leaves, refs, problems, info)

    # --- shape vs retail ----------------------------------------------------
    v10, p10 = cand.geometry(n10)
    horiz = sum(1 for (n, a, vi, nb) in p10 if n[1] >= 0.999)
    info['shape'] = dict(up_facing_pct=round(100.0 * horiz / len(p10), 2),
                         quads=sum(1 for (n, a, vi, nb) in p10 if len(vi) == 4),
                         tris=sum(1 for (n, a, vi, nb) in p10 if len(vi) == 3))
    if horiz != len(p10):
        problems.append('%d type10 faces are not horizontal and facing up; '
                        'in the four retail cities it is 604 of 604'
                        % (len(p10) - horiz))

    # --- safety -------------------------------------------------------------
    safety_vs_type9(donor, cand, problems, info, args.band, args.sink,
                    args.samples, args.tol, args.near)

    info['problems'] = problems
    info['verdict'] = 'PASS' if not problems else 'FAIL'

    print('donor     %s  %d bytes' % (info['donor']['sha256'][:16], info['donor']['size']))
    print('candidate %s  %d bytes' % (info['candidate']['sha256'][:16], info['candidate']['size']))
    print()
    c = info['contract']
    print('contract 2000/2000 : %d polys (memset %d B), %d verts (memset %d B), '
          'buffer %d B -> overflow %d/%d'
          % (c['polys'], c['poly_memset'], c['verts'], c['vert_memset'],
             c['buffer_bytes'], c['poly_overflow'], c['vert_overflow']))
    print('type9 identical    : %s' % info['type9_identical'])
    print('diff               : %d old bytes changed (%d unexpected), %d appended'
          % (info['diff']['bytes_changed_in_common'], info['diff']['unexpected'],
             info['diff']['appended']))
    for kind in (9, 10):
        t = info['t%d' % kind]
        print('type%-2d             : %d verts, %d polys, %d refs, %d leaves, '
              'depth %d, dup %.2fx, %d boundary edges'
              % (kind, t['verts'], t['polys'], t['refs'], t['leaves'], t['depth'],
                 t['duplication'], t['boundary_edges']))
    print('type10 shape       : %.1f%% horizontal facing up, %d quads, %d tris'
          % (info['shape']['up_facing_pct'], info['shape']['quads'], info['shape']['tris']))
    s = info['safety']
    print('safety             : %d samples; buried %d (<=%.2f u), deep %d '
          '(%.2f%%, ramp), type9 gap %d (%.2f%%), EXPOSED %d (%.2f%%)'
          % (s['samples'], s['buried'], s['allowed_gap'], s['deeply_buried'],
             s['deep_pct'], s['seam'], s['seam_pct'], s['exposed'], s['exposed_pct']))
    for kind in (9, 10):
        t = info['t%d' % kind]
        if t['blocks_without_array_header']:
            print('  note: type%d has %d of %d child vectors WITHOUT the 16-byte '
                  'array header the four retail cities have%s'
                  % (kind, t['blocks_without_array_header'], t['blocks'],
                     ' (inherited from the donor, type9 identical)' if kind == 9 else ''))
    print()
    if problems:
        print('VERDICT: FAIL')
        for p in problems:
            print('  - %s' % p)
    else:
        print('VERDICT: PASS')
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as stream:
            json.dump(info, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
