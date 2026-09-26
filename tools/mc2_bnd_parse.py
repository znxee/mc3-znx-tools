#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read MC2's text `.bnd` (`type: otgrid`) and return ONE global mesh.

The otgrid is a GRID of cells (CellDim x CellDim), each with its own text bound:
`verts:`/`materials:`/`edges:`/`polys:` and then the `v x y z` lines, the
`mtl <name> { ... }` blocks, `edge a b nx ny nz` and the polygons
`tri v0 v1 v2 mtl e0 e1 e2` / `quad v0 v1 v2 v3 mtl e0 e1 e2 e3`.

The vertices are already in WORLD coordinates across Los Angeles' 42 cells, so
joining is just concatenating while shifting the index. Empty cells
(`verts: 0`) are skipped.
"""
import re


def read_otgrid(path):
    L = open(path, encoding='latin1').read().splitlines()
    n = len(L)
    verts = []            # [(x,y,z)]
    polys = []            # [(indices, nome_do_material)]
    materials = []        # names, in global order of appearance
    header = {}
    i = 0
    # grid header
    while i < n and not L[i].strip().startswith('verts:'):
        s = L[i].strip()
        if ':' in s:
            k, v = s.split(':', 1)
            header[k.strip()] = v.strip()
        i += 1

    while i < n:
        s = L[i].strip()
        if not s.startswith('verts:'):
            i += 1
            continue
        nv = int(s.split(':', 1)[1])
        base = len(verts)
        mtl_local = []          # names of this cell, in order
        i += 1
        # the block runs to the next 'verts:'
        while i < n and not L[i].strip().startswith('verts:'):
            t = L[i].strip()
            if t.startswith('v '):
                p = t.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif t.startswith('mtl '):
                mtl_local.append(t.split()[1])
            elif t.startswith('tri ') or t.startswith('quad '):
                p = t.split()
                k = 3 if p[0] == 'tri' else 4
                vi = tuple(base + int(x) for x in p[1:1 + k])
                mi = int(p[1 + k])
                name = mtl_local[mi] if 0 <= mi < len(mtl_local) else '?'
                polys.append((vi, name))
            i += 1
        for m in mtl_local:
            if m not in materials:
                materials.append(m)
        if nv and len(verts) - base != nv:
            raise SystemExit('cell at %d: declared %d verts, read %d'
                             % (i, nv, len(verts) - base))
    return verts, polys, materials, header


if __name__ == '__main__':
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else \
        'output/mc2_losangeles/bound/losangeles.bnd'
    v, po, mt, hdr = read_otgrid(p)
    print('grid header:', hdr)
    print('vertices: %d   polygons: %d' % (len(v), len(po)))
    tri = sum(1 for vi, _ in po if len(vi) == 3)
    print('  triangles %d   quads %d' % (tri, len(po) - tri))
    lo = [min(q[a] for q in v) for a in range(3)]
    hi = [max(q[a] for q in v) for a in range(3)]
    print('AABB  (%.0f %.0f %.0f) .. (%.0f %.0f %.0f)' % tuple(lo + hi))
    print('sides %.0f x %.0f x %.0f' % tuple(hi[a] - lo[a] for a in range(3)))
    print('materials (%d): %s' % (len(mt), mt))
