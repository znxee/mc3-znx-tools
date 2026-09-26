#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit of a candidate in which TYPE9 changed (and type10 with it).

`audit_type10_groundplane.py` requires a byte-for-byte identical type9, on
purpose: it audits candidates whose only change is the quadtree. Here type9 is
what changes, so that proof does not apply and is swapped for another one,
stronger for this case:

    the candidate's type9 face set has to CONTAIN the baseline's.

No face may vanish. New faces are expected and are listed.

It also checks:

  u16 ceiling  type9 refs <= 65535. The leaf start is `a1[1]` of an
               `unsigned short*` in phQuadtreeCell/phOctreeCell::
               CullSpherePolysRecurse, so going past 65535 is unreachable, not
               just tight.
  2000 ceiling type10 stays within numPolys<=2000 and numVerts<=2000
  structure    both trees: counts, leaves partitioning the refs, indices in
               range, leaf containment walking down the tree with the box,
               depth <= 20, normals, areas, materials, neighbours
  holes        the points the baseline did not cover become covered

    python audit_collision_v18.py --baseline v13.pck --candidate v18.pck \\
        --holes points.json --json audit.json
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mc3_bound as MB
from audit_type10_groundplane import (check_structure, check_geometry,
                                      check_containment, safety_vs_type9,
                                      poly_points, CAP)

REFS_CAP = 65535


def floor_cells(b, cell):
    """XZ grid cells that have some floor face. Independent of how the surface
    was cut into polygons, which is the point."""
    node = b.trees[9]
    v, p = b.geometry(node)
    g = set()
    for (n, a, vi, nb) in p:
        if n[1] < 0.30:
            continue
        pts = [v[i] for i in vi]
        xs = [q[0] for q in pts]; zs = [q[2] for q in pts]
        for gx in range(int(math.floor(min(xs)/cell)), int(math.floor(max(xs)/cell))+1):
            for gz in range(int(math.floor(min(zs)/cell)), int(math.floor(max(zs)/cell))+1):
                g.add((gx, gz))
    return g


def face_set(b, kind, ndp=2):
    node = b.trees[kind]
    v, p = b.geometry(node)
    return set(tuple(sorted(tuple(round(c, ndp) for c in v[i]) for i in vi))
               for (nm, a, vi, nb) in p)


def floor_index(b):
    node = b.trees[9]
    verts, polys = b.geometry(node)
    mats = b.materials(node)
    CELL = 16.0
    grid = {}
    faces = []
    for (normal, area, vi, nb) in polys:
        if abs(normal[1]) < 0.25:
            continue
        pts = poly_points(verts, vi)
        li = struct.pack('<f', area)[0]
        idx = len(faces)
        faces.append((normal, pts, mats[li] if li < len(mats) else -1))
        xs = [q[0] for q in pts]; zs = [q[2] for q in pts]
        for gx in range(int(math.floor(min(xs)/CELL)), int(math.floor(max(xs)/CELL))+1):
            for gz in range(int(math.floor(min(zs)/CELL)), int(math.floor(max(zs)/CELL))+1):
                grid.setdefault((gx, gz), []).append(idx)
    return faces, grid, CELL


def hits_at(faces, grid, cell, x, z):
    out = []
    for idx in grid.get((int(math.floor(x/cell)), int(math.floor(z/cell))), ()):
        normal, pts, mat = faces[idx]
        n = len(pts); s = 0; inside = True
        for i in range(n):
            ax, az = pts[i][0], pts[i][2]
            bx, bz = pts[(i+1) % n][0], pts[(i+1) % n][2]
            cr = (bx-ax)*(z-az) - (bz-az)*(x-ax)
            if cr > 1e-9: s |= 1
            elif cr < -1e-9: s |= 2
            if s == 3:
                inside = False
                break
        if not inside or abs(normal[1]) < 1e-9:
            continue
        ax, ay, az = pts[0]
        out.append((round(ay - (normal[0]*(x-ax) + normal[2]*(z-az))/normal[1], 3),
                    mat, 'up' if normal[1] > 0 else 'down'))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--holes', help='JSON with [[label,x,z],...] that the baseline did not cover')
    ap.add_argument('--json')
    ap.add_argument('--band', type=float, default=4.0)
    ap.add_argument('--sink', type=float, default=0.05)
    ap.add_argument('--samples', type=int, default=6)
    ap.add_argument('--tol', type=float, default=0.75)
    ap.add_argument('--near', type=float, default=6.0)
    ap.add_argument('--merged-quads', action='store_true',
                    help='the candidate merged triangles into quads: swaps the face '
                         'identity proof for the spatial coverage one')
    ap.add_argument('--cell', type=float, default=8.0)
    a = ap.parse_args()

    base = MB.Bound(a.baseline)
    cand = MB.Bound(a.candidate)
    problems, info = [], {}
    info['baseline'] = dict(path=a.baseline, size=len(base.data),
                            sha256=hashlib.sha256(base.data).hexdigest().upper())
    info['candidate'] = dict(path=a.candidate, size=len(cand.data),
                             sha256=hashlib.sha256(cand.data).hexdigest().upper())

    # --- structure of both trees -----------------------------------------
    for kind in (9, 10):
        check_structure(cand, kind, problems, info, note_only_headers=True)
        check_geometry(cand, kind, problems, info)
        leaves = cand.leaves(cand.trees[kind], kind)
        _r, ioff, nref = cand.payload(cand.trees[kind], kind)
        refs = [struct.unpack_from('<H', cand.data, ioff + 2*i)[0] for i in range(nref)]
        check_containment(cand, kind, leaves, refs, problems, info)

    # --- u16 ceiling of the type9 refs -----------------------------------
    t9 = info['t9']
    info['refs_cap'] = dict(cap=REFS_CAP, refs=t9['refs'],
                            headroom=REFS_CAP - t9['refs'])
    if t9['refs'] > REFS_CAP:
        problems.append('u16 CEILING: type9 has %d refs, the leaf start only goes to %d'
                        % (t9['refs'], REFS_CAP))

    # --- the quadtree 2000 ceiling ---------------------------------------
    t10 = info['t10']
    info['quadtree_cap'] = dict(cap=CAP, polys=t10['polys'], verts=t10['verts'])
    if t10['polys'] > CAP:
        problems.append('CONTRACT: type10 has %d polys, ceiling %d' % (t10['polys'], CAP))
    if t10['verts'] > CAP:
        problems.append('CONTRACT: type10 has %d verts, ceiling %d' % (t10['verts'], CAP))

    # --- nothing was lost from type9 -------------------------------------
    #
    # Face identity only holds when the polygons were not rebuilt. With
    # --merge-quads two triangles become ONE quad: the surface is the same, but
    # the two old keys vanish and a new one appears, and the identity count
    # reports a loss that does not exist (measured: 2,762 "losses" with 0 of
    # surface lost). In that case the proof becomes SPATIAL: every grid cell
    # that had a floor in the baseline has to have a floor in the candidate.
    if a.merged_quads:
        gb = floor_cells(base, a.cell)
        gc = floor_cells(cand, a.cell)
        missing = [k for k in gb if k not in gc]
        info['type9_floor_cells'] = dict(cell=a.cell, baseline=len(gb),
                                         candidate=len(gc), missing=len(missing),
                                         gained=len([k for k in gc if k not in gb]),
                                         examples=[[k[0]*a.cell, k[1]*a.cell]
                                                   for k in missing[:10]])
        if missing:
            problems.append('%d cells of %.0f u with a floor in the baseline ended up WITHOUT '
                            'a floor in the candidate' % (len(missing), a.cell))
    else:
        fb, fc = face_set(base, 9), face_set(cand, 9)
        lost, gained = fb - fc, fc - fb
        info['type9_faces'] = dict(baseline=len(fb), candidate=len(fc),
                                   lost=len(lost), gained=len(gained))
        if lost:
            problems.append('%d baseline type9 faces VANISHED in the candidate'
                            % len(lost))

    # --- vertex/poly indices within u16 ----------------------------------
    for kind in (9, 10):
        t = info['t%d' % kind]
        if t['verts'] > 0xFFFF:
            problems.append('t%d has %d vertices, the index is u16' % (kind, t['verts']))
        if t['polys'] > 0xFFFF:
            problems.append('t%d has %d polygons, the index is u16' % (kind, t['polys']))

    # --- are the known holes covered now? --------------------------------
    if a.holes:
        pts = json.load(open(a.holes, encoding='utf-8'))
        fb_i = floor_index(base)
        fc_i = floor_index(cand)
        fixed = still = 0
        rows = []
        for label, x, z in pts:
            hb = hits_at(*fb_i, x, z)
            hc = hits_at(*fc_i, x, z)
            ok = bool(hc)
            fixed += 1 if (ok and not hb) else 0
            still += 0 if ok else 1
            rows.append(dict(label=label, x=x, z=z, baseline=hb, candidate=hc,
                             fixed=bool(ok and not hb)))
        info['holes'] = dict(checked=len(pts), fixed=fixed, still_open=still,
                             detail=rows)
        if still:
            problems.append('%d of the %d hole points still have NO collision'
                            % (still, len(pts)))

    # --- safety of the type10 plane against the NEW type9 ----------------
    safety_vs_type9(cand, cand, problems, info, a.band, a.sink, a.samples, a.tol, a.near)

    info['problems'] = problems
    info['verdict'] = 'PASS' if not problems else 'FAIL'

    print('baseline  %s  %d bytes' % (info['baseline']['sha256'][:16], info['baseline']['size']))
    print('candidate %s  %d bytes' % (info['candidate']['sha256'][:16], info['candidate']['size']))
    print()
    for kind in (9, 10):
        t = info['t%d' % kind]
        print('type%-2d            : %6d verts, %6d polys, %6d refs, %5d leaves, depth %d, dup %.2fx'
              % (kind, t['verts'], t['polys'], t['refs'], t['leaves'], t['depth'], t['duplication']))
    r = info['refs_cap']
    print('u16 refs ceiling  : %d of %d, headroom %d' % (r['refs'], r['cap'], r['headroom']))
    q = info['quadtree_cap']
    print('type10 2000 cap   : %d polys, %d verts' % (q['polys'], q['verts']))
    if 'type9_floor_cells' in info:
        f = info['type9_floor_cells']
        print('type9 floor       : %d cells of %.0f u in the baseline -> %d in the candidate; '
              'LOST %d, new %d'
              % (f['baseline'], f['cell'], f['candidate'], f['missing'], f['gained']))
    else:
        f = info['type9_faces']
        print('type9 faces       : baseline %d -> candidate %d; lost %d, new %d'
              % (f['baseline'], f['candidate'], f['lost'], f['gained']))
    for kind in (9, 10):
        t = info['t%d' % kind]
        print('containment t%-2d   : %d leaf refs checked, %d outside the cell'
              % (kind, t['leaf_refs_checked'], t['leaf_refs_misplaced']))
    if a.holes:
        h = info['holes']
        print('holes             : %d checked, %d now have collision, %d still open'
              % (h['checked'], h['fixed'], h['still_open']))
    s = info['safety']
    print('type10 plane      : %d samples, exposed %d (%.2f%%)'
          % (s['samples'], s['exposed'], s['exposed_pct']))
    print()
    if problems:
        print('VERDICT: FAIL')
        for p in problems:
            print('  - %s' % p)
    else:
        print('VERDICT: PASS')
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as stream:
            json.dump(info, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
