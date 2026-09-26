#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic floor for the crossings whose lod0 mesh DOES NOT EXIST in the source.

THE FINDING
===========

The user noticed that the collision holes left are all **invisible**. The
correlation is real and has a single cause.

`mc2_port.py` emits one placement line per lod, but only when the `.xmod` of
that lod exists:

    for lod in sorted(lods):
        path = available.get(('%s_%d_%s.xmod' % (name, lod, wanted)).lower())
        if wanted in lods[lod] and path:
            parts.append((lod, wanted, path))

**18 models ended up with placements only at lod != 0**, and none of them has an
`_0_` file on disk. The lod1 of those components is a stub:
`l_santamonica_int_12_x#geom` (the crossing at X~-675 Z~-790) has SIX
triangles, all vertical. Nothing to see from inside the car, and nothing to
stand on.

And it is not only the mesh: I checked MC2's `losangeles.bnd`, the source's
global collision, and **it has nothing at those points either**. So the hole is
in the SOURCE, on both sides - visual and physics -, not in this project's
selection. No manifest rule can fix it, because there is no geometry to select.

WHAT THIS DOES, AND WHAT IT DOES NOT
====================================

The lod0 mesh is not on disk and the MC2 source is no longer on the machine, so
recovering the real geometry depends on the user bringing the MC2 PC build.
Until then, this generator does the only possible thing: **fill the missing
floor with a flat slab, at the height measured on the neighbouring streets.**

This is interpolation from a measured neighbour, not invention:

- the outline comes from the component's own lod1 stub - it is the square of
  the crossing;
- the height comes from a **least-squares plane fitted** to the type9 `road`
  faces around and inside the outline. A plane, not a single height: a street
  crossing a 40 m block climbs, and this generator's first cut rejected exactly
  the crossing the user reported for "spread 1.14" when it was a slope, not
  ambiguity. The slab is only produced if the fit **residual** fits in
  `--tol`; a neighbourhood that is not planar is reported, not filled;
- the four corners of each rectangle are evaluated ON the plane, so the quad is
  planar by construction and follows the slope of the street around it;
- only cells that today have **no floor at all** go in, so no competing
  coplanar duplicate of what already exists is created.

**The visuals are still missing.** This gets the car out of the pit; it does
not draw the street.

    python make_floor_patch.py --active <pck> --out patch.json
"""

import argparse
import collections
import csv
import glob
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city'))
TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
sys.path.insert(0, TOOLS)
import mc3_bound as MB
import mc3_modpath as modpath
from build_la_instance_asphalt_collision import load_raw, raw_triangles

ROAD = 17


def floors(bound):
    v, p = bound.geometry(bound.trees[9])
    mats = bound.materials(bound.trees[9])
    CELL = 16.0
    grid, faces = {}, []
    for (n, a, vi, nb) in p:
        if abs(n[1]) < 0.25:
            continue
        pts = [v[i] for i in vi]
        li = struct.pack('<f', a)[0]
        idx = len(faces)
        faces.append((n, pts, mats[li] if li < len(mats) else -1))
        xs = [q[0] for q in pts]; zs = [q[2] for q in pts]
        for gx in range(int(math.floor(min(xs)/CELL)), int(math.floor(max(xs)/CELL))+1):
            for gz in range(int(math.floor(min(zs)/CELL)), int(math.floor(max(zs)/CELL))+1):
                grid.setdefault((gx, gz), []).append(idx)
    return faces, grid, CELL


def inside(pts, x, z):
    n = len(pts); s = 0
    for i in range(n):
        ax, az = pts[i][0], pts[i][2]
        bx, bz = pts[(i+1) % n][0], pts[(i+1) % n][2]
        cr = (bx-ax)*(z-az) - (bz-az)*(x-ax)
        if cr > 1e-9: s |= 1
        elif cr < -1e-9: s |= 2
        if s == 3: return False
    return True


def at(faces, grid, cell, x, z, road_only=False):
    out = []
    for idx in grid.get((int(math.floor(x/cell)), int(math.floor(z/cell))), ()):
        n, pts, mat = faces[idx]
        if road_only and mat != ROAD:
            continue
        if not inside(pts, x, z):
            continue
        ax, ay, az = pts[0]
        out.append(ay - (n[0]*(x-ax) + n[2]*(z-az))/n[1])
    return out


def greedy(cells):
    rem = dict(cells)
    out = []
    for k in sorted(rem, key=lambda t: (t[1], t[0])):
        if k not in rem:
            continue
        gx, gz = k
        val = rem[k]
        w = 0
        while rem.get((gx+w, gz)) == val:
            w += 1
        h = 1
        while all(rem.get((gx+i, gz+h)) == val for i in range(w)):
            h += 1
        for i in range(w):
            for j in range(h):
                del rem[(gx+i, gz+j)]
        out.append((gx, gz, w, h, val))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--active', required=True)
    ap.add_argument('--place', default=TOOLS + '/output/mc2_losangeles/losangeles_place.tsv')
    ap.add_argument('--models', default=TOOLS + '/output/mc2_losangeles/model')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pattern', default='_int_',
                    help='only crossing components, by default')
    ap.add_argument('--step', type=float, default=4.0)
    ap.add_argument('--ring', type=float, default=14.0,
                    help='radius around the outline where the neighbouring road is measured')
    ap.add_argument('--tol', type=float, default=0.60,
                    help='maximum spread of the neighbouring heights')
    ap.add_argument('--material', default='road')
    a = ap.parse_args()

    bound = MB.Bound(a.active)
    faces, grid, cell = floors(bound)

    with open(a.place, encoding='utf-8') as s:
        rows = list(csv.DictReader((l for l in s if not l.startswith('#')), delimiter='\t'))
    lods = collections.defaultdict(set)
    for r in rows:
        lods[r['name']].add(r['lod'])
    targets = [n for n, l in lods.items() if '0' not in l and a.pattern in n.lower()]

    patch, report = [], []
    for name in sorted(targets):
        xs, zs = [], []
        for r in [x for x in rows if x['name'] == name]:
            p = os.path.join(a.models, '%s_%s_%s.mod' % (r['name'], r['lod'], r['part']))
            if not os.path.exists(p):
                continue
            m = [float(r['m%d%d' % (i, j)]) for i in range(3) for j in range(3)]
            t = tuple(float(r[c]) for c in ('tx', 'ty', 'tz'))
            mesh = load_raw(modpath, p)
            pos = mesh.positions()
            for mat in mesh.a(modpath.SLOT_MATERIAL).items:
                for pk in mat.packets:
                    for pr in pk.primitives:
                        for ti in raw_triangles(modpath, pr):
                            for i in ti:
                                q = pos[pk.adjuncts[i][0]]
                                xs.append(m[0]*q[0] + m[3]*q[1] + m[6]*q[2] + t[0])
                                zs.append(m[2]*q[0] + m[5]*q[1] + m[8]*q[2] + t[2])
        if not xs:
            continue
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)

        # road samples: inside the outline and in a ring around it
        ring = []
        n = max(4, int((x1-x0)/a.step))
        mm = max(4, int((z1-z0)/a.step))
        for i in range(n+1):
            for j in range(mm+1):
                px = x0 - a.ring + (x1-x0+2*a.ring)*i/n
                pz = z0 - a.ring + (z1-z0+2*a.ring)*j/mm
                for h in at(faces, grid, cell, px, pz, road_only=True):
                    ring.append((px, pz, h))
        entry = dict(model=name, x0=round(x0, 2), x1=round(x1, 2),
                     z0=round(z0, 2), z1=round(z1, 2), ring_samples=len(ring))
        if len(ring) < 6:
            entry['skipped'] = 'not enough neighbouring type9 road (%d samples)' % len(ring)
            report.append(entry)
            continue

        # plane y = A*x + B*z + C by least squares (3x3 normal equations)
        sx = sz = sy = sxx = szz = sxz = sxy = szy = 0.0
        for (px, pz, h) in ring:
            sx += px; sz += pz; sy += h
            sxx += px*px; szz += pz*pz; sxz += px*pz
            sxy += px*h; szy += pz*h
        k = float(len(ring))
        M = [[sxx, sxz, sx], [sxz, szz, sz], [sx, sz, k]]
        V = [sxy, szy, sy]
        singular = False
        for c in range(3):                      # Gauss with partial pivoting
            piv = max(range(c, 3), key=lambda r: abs(M[r][c]))
            if abs(M[piv][c]) < 1e-9:
                singular = True
                break
            M[c], M[piv] = M[piv], M[c]
            V[c], V[piv] = V[piv], V[c]
            for r in range(3):
                if r == c:
                    continue
                f = M[r][c]/M[c][c]
                for q in range(c, 3):
                    M[r][q] -= f*M[c][q]
                V[r] -= f*V[c]
        if singular:
            entry['skipped'] = 'singular plane fit'
            report.append(entry)
            continue
        A, B, C = (V[i]/M[i][i] for i in range(3))
        res = sorted(abs(h - (A*px + B*pz + C)) for (px, pz, h) in ring)
        resid = res[int(0.9*(len(res)-1))]
        entry.update(plane=[round(A, 5), round(B, 5), round(C, 3)],
                     residual_p90=round(resid, 3),
                     slope_pct=round(100.0*math.hypot(A, B), 2))
        if resid > a.tol:
            entry['skipped'] = ('neighbourhood is not planar: residual p90 %.2f > tol %.2f'
                                % (resid, a.tol))
            report.append(entry)
            continue

        def plane_y(px, pz, _A=A, _B=B, _C=C):
            return _A*px + _B*pz + _C
        y = plane_y((x0+x1)/2.0, (z0+z1)/2.0)
        entry['y_center'] = round(y, 3)

        # cells with no floor at all today
        cells, holes, tot = {}, 0, 0
        gx0 = int(math.floor(x0/a.step)); gx1 = int(math.ceil(x1/a.step))
        gz0 = int(math.floor(z0/a.step)); gz1 = int(math.ceil(z1/a.step))
        for gx in range(gx0, gx1):
            for gz in range(gz0, gz1):
                cx = (gx + 0.5)*a.step; cz = (gz + 0.5)*a.step
                if not (x0 <= cx <= x1 and z0 <= cz <= z1):
                    continue
                tot += 1
                if at(faces, grid, cell, cx, cz):
                    continue
                holes += 1
                cells[(gx, gz)] = 0
        entry.update(cells_total=tot, cells_empty=holes)
        if not cells:
            entry['skipped'] = 'already has floor in every cell'
            report.append(entry)
            continue
        rects = greedy(cells)
        for (gx, gz, w, h, _v) in rects:
            rx0, rx1 = gx*a.step, (gx+w)*a.step
            rz0, rz1 = gz*a.step, (gz+h)*a.step
            # the four corners evaluated ON the plane: a planar quad that follows the slope
            patch.append(dict(model=name,
                              x0=round(rx0, 3), x1=round(rx1, 3),
                              z0=round(rz0, 3), z1=round(rz1, 3),
                              y00=round(plane_y(rx0, rz0), 4),
                              y10=round(plane_y(rx1, rz0), 4),
                              y11=round(plane_y(rx1, rz1), 4),
                              y01=round(plane_y(rx0, rz1), 4),
                              material=a.material))
        entry['rects'] = len(rects)
        report.append(entry)

    out = dict(
        note='synthetic floor for crossings whose lod0 mesh does not exist in the source; '
             'outline from the lod1 stub, height from the neighbouring road faces',
        step=a.step, ring=a.ring, tol=a.tol,
        report=report, quads=patch)
    with open(a.out, 'w', encoding='utf-8') as s:
        json.dump(out, s, indent=1, ensure_ascii=False)
        s.write('\n')

    print('%-32s %6s %6s %6s %8s %8s %6s %5s %s'
          % ('component', 'cells', 'empty', 'samp', 'y', 'residual', 'slope%', 'slabs', 'status'))
    for e in report:
        print('%-32s %6s %6s %6d %8s %8s %6s %5s %s'
              % (e['model'][:32], e.get('cells_total', '-'), e.get('cells_empty', '-'),
                 e['ring_samples'], e.get('y_center', '-'), e.get('residual_p90', '-'),
                 e.get('slope_pct', '-'), e.get('rects', '-'), e.get('skipped', 'OK')))
    print()
    print('floor quads generated : %d' % len(patch))
    print('written               : %s' % a.out)


if __name__ == '__main__':
    main()
