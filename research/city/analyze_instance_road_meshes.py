#!/usr/bin/env python3
"""Audit the asphalt surfaces that live in instances of MC2's .hood."""

import argparse
import csv
import json
import math
import os
import sys


def load_raw(modpath, path):
    blob = open(path, 'rb').read()
    kind, version, newfmt = modpath.sniff_version(blob)
    if kind != modpath.VERSION_110:
        raise ValueError('unsupported version in %s' % path)
    mesh = modpath.MshMesh()
    tok = modpath.DatAsciiTokenizer(blob.decode('latin-1'))
    tok.get_token()
    tok.get_token()
    modpath.msh_mesh_load_mod(mesh, tok, newfmt, version)
    return mesh


def raw_triangles(modpath, primitive):
    idx = primitive.indices
    if primitive.type == modpath.PRIM_TRI:
        return [tuple(idx)]
    odd = primitive.type == modpath.PRIM_STP
    out = []
    for k in range(2, len(idx)):
        a, b, c = idx[k - 2], idx[k - 1], idx[k]
        if a == b or b == c or a == c:
            continue
        out.append((b, a, c) if ((k + (1 if odd else 0)) & 1)
                   else (a, b, c))
    return out


def matrix(row):
    return [float(row['m%d%d' % (i, j)]) for i in range(3) for j in range(3)]


def transform(p, m, t, column):
    x, y, z = p
    if column:
        return (m[0] * x + m[1] * y + m[2] * z + t[0],
                m[3] * x + m[4] * y + m[5] * z + t[1],
                m[6] * x + m[7] * y + m[8] * z + t[2])
    return (m[0] * x + m[3] * y + m[6] * z + t[0],
            m[1] * x + m[4] * y + m[7] * z + t[1],
            m[2] * x + m[5] * y + m[8] * z + t[2])


def bounds(points):
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])


def bound_error(actual, row):
    expected = ([float(row['emin%s' % c]) for c in 'xyz'],
                [float(row['emax%s' % c]) for c in 'xyz'])
    return sum(abs(actual[k][i] - expected[k][i]) for k in range(2) for i in range(3))


def normal_area(points):
    a, b, c = points
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1],
         u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0])
    mag = math.sqrt(sum(q * q for q in n))
    if mag < 1e-9:
        return None, 0.0
    return tuple(q / mag for q in n), mag / 2.0


def point_in_triangle_xz(point, tri, eps=1e-5):
    px, pz = point
    a, b, c = [(v[0], v[2]) for v in tri]
    def cross(o, u, v):
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
    d1 = cross(a, b, (px, pz))
    d2 = cross(b, c, (px, pz))
    d3 = cross(c, a, (px, pz))
    return ((d1 >= -eps and d2 >= -eps and d3 >= -eps) or
            (d1 <= eps and d2 <= eps and d3 <= eps))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--place', required=True)
    ap.add_argument('--models', required=True)
    ap.add_argument('--tools', required=True)
    ap.add_argument('--x', type=float, required=True)
    ap.add_argument('--z', type=float, required=True)
    ap.add_argument('--out')
    args = ap.parse_args()
    sys.path.insert(0, args.tools)
    import mc3_modpath as modpath

    with open(args.place, encoding='utf-8') as stream:
        rows = list(csv.DictReader((s for s in stream if not s.startswith('#')),
                                   delimiter='\t'))
    rows = [r for r in rows if r['kind'] == 'inst']
    cache = {}
    err_row = err_col = 0.0
    road_rows = road_models = triangles = downward = degenerate = 0
    hits = []
    unique = set()
    material_counts = {}
    model_set = set()

    for row in rows:
        name = '%s_%s_%s.mod' % (row['name'], row['lod'], row['part'])
        path = os.path.join(args.models, name)
        mesh = cache.get(path)
        if mesh is None:
            mesh = cache[path] = load_raw(modpath, path)
        pos = mesh.positions()
        m = matrix(row)
        t = tuple(float(row[c]) for c in ('tx', 'ty', 'tz'))
        err_row += bound_error(bounds([transform(p, m, t, False) for p in pos]), row)
        err_col += bound_error(bounds([transform(p, m, t, True) for p in pos]), row)

    use_column = err_col < err_row
    for row in rows:
        name = '%s_%s_%s.mod' % (row['name'], row['lod'], row['part'])
        path = os.path.join(args.models, name)
        mesh = cache[path]
        pos = mesh.positions()
        m = matrix(row)
        t = tuple(float(row[c]) for c in ('tx', 'ty', 'tz'))
        used = False
        for mat in mesh.a(modpath.SLOT_MATERIAL).items:
            if 'ashphalt' not in mat.name.lower() and 'asphalt' not in mat.name.lower():
                continue
            used = True
            material_counts[mat.name] = material_counts.get(mat.name, 0) + 1
            for packet in mat.packets:
                for primitive in packet.primitives:
                    for ti in raw_triangles(modpath, primitive):
                        try:
                            vi = [packet.adjuncts[i][0] for i in ti]
                            tri = tuple(transform(pos[i], m, t, use_column) for i in vi)
                        except (IndexError, TypeError):
                            raise ValueError('invalid index in %s / %s' % (name, mat.name))
                        normal, area = normal_area(tri)
                        if normal is None:
                            degenerate += 1
                            continue
                        triangles += 1
                        if normal[1] < 0:
                            downward += 1
                        key = tuple(sorted(tuple(round(c, 5) for c in p) for p in tri))
                        unique.add(key)
                        if point_in_triangle_xz((args.x, args.z), tri):
                            hits.append(dict(model=row['name'], file=name, material=mat.name,
                                             translation=t, triangle=tri, normal=normal,
                                             area=area))
        if used:
            road_rows += 1
            model_set.add(name)

    report = dict(instance_rows=len(rows), model_types=len(cache),
                  matrix_error_row_vector=err_row,
                  matrix_error_column_vector=err_col,
                  transform='column' if use_column else 'row-vector',
                  asphalt_placements=road_rows,
                  asphalt_model_types=len(model_set), triangles=triangles,
                  unique_triangles=len(unique), downward=downward,
                  degenerate=degenerate, materials=material_counts,
                  point=[args.x, args.z], point_hits=hits)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write('\n')


if __name__ == '__main__':
    main()
