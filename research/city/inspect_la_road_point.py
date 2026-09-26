#!/usr/bin/env python3
"""Compare the collision at the car's position with type9/type10 and MC2's roadSG."""

import argparse
import json
import math
import os
import struct
import sys


def point_in_poly_xz(point, pts, eps=1e-5):
    """2D point-in-polygon test, edge included."""
    x, z = point
    inside = False
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        ax, az = a[0], a[2]
        bx, bz = b[0], b[2]
        dx, dz = bx - ax, bz - az
        if dx * dx + dz * dz <= eps * eps:
            continue
        cross = (x - ax) * dz - (z - az) * dx
        if abs(cross) <= eps * max(1.0, abs(dx), abs(dz)):
            dot = (x - ax) * dx + (z - az) * dz
            if -eps <= dot <= dx * dx + dz * dz + eps:
                return True
        if ((az > z) != (bz > z)):
            hit_x = ax + (z - az) * dx / (bz - az)
            if x < hit_x:
                inside = not inside
    return inside


def plane_y(point, pts):
    a, b, c = pts[:3]
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    if abs(ny) < 1e-8:
        return None
    x, z = point
    return a[1] - (nx * (x - a[0]) + nz * (z - a[2])) / ny


def edge_distance_xz(point, pts):
    x, z = point
    best = float('inf')
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        ax, az = a[0], a[2]
        dx, dz = b[0] - ax, b[2] - az
        den = dx * dx + dz * dz
        t = 0.0 if not den else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / den))
        best = min(best, math.hypot(x - (ax + t * dx), z - (az + t * dz)))
    return best


def summarize(name, verts, polys, point, material_mode=False):
    containing = []
    nearest = []
    for index, poly in enumerate(polys):
        if material_mode:
            indices, material = poly
            normal = area = None
        else:
            normal, area, indices, _neighbors = poly
            material = None
        pts = [verts[i] for i in indices]
        distance = edge_distance_xz(point, pts)
        entry = {
            'index': index,
            'material': material,
            'plane_y': plane_y(point, pts),
            'edge_distance_xz': distance,
        }
        if normal is not None:
            entry['normal'] = normal
            entry['area'] = area
        if point_in_poly_xz(point, pts):
            containing.append(entry)
        nearest.append(entry)
    nearest.sort(key=lambda q: q['edge_distance_xz'])
    return {
        'name': name,
        'polygons': len(polys),
        'containing': containing,
        'nearest': nearest[:10],
    }


def state_car_position(path):
    """Read the validated retail chain in the savestate: manager -> player -> car -> ICS."""
    # The .p2s is a ZIP; the EE RAM dump is the EE.bin member.
    import zipfile
    import zstandard
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        ee_name = next((n for n in names
                        if n.lower() in ('ee.bin', 'eememory.bin')), None)
        if ee_name is None:
            raise ValueError('savestate without EE.bin: %s' % names)
        info = zf.getinfo(ee_name)
        zf.fp.seek(info.header_offset)
        name_len, extra_len = struct.unpack_from('<HH', zf.fp.read(30), 26)
        zf.fp.seek(info.header_offset + 30 + name_len + extra_len)
        ram = zstandard.ZstdDecompressor().decompress(
            zf.fp.read(info.compress_size), max_output_size=info.file_size)

    def u32(addr):
        return struct.unpack_from('<I', ram, addr)[0]

    manager = u32(0x0061B1E0)
    player = u32(manager + 0x08)
    car = u32(player + 0x14)
    inertial = u32(car + 0x08)
    position = struct.unpack_from('<3f', ram, inertial + 0xC4)
    return {
        'manager': manager,
        'player': player,
        'car': car,
        'inertial': inertial,
        'position': position,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state')
    ap.add_argument('--x', type=float)
    ap.add_argument('--z', type=float)
    ap.add_argument('--active', required=True)
    ap.add_argument('--mc2', required=True)
    ap.add_argument('--tools', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    sys.path.insert(0, args.tools)
    import mc3_bound
    from mc2_bnd_parse import read_otgrid

    if args.state:
        chain = state_car_position(args.state)
        x, y, z = chain['position']
    elif args.x is not None and args.z is not None:
        chain = None
        x, y, z = args.x, None, args.z
    else:
        ap.error('informe --state ou o par --x/--z')
    point = (x, z)
    bound = mc3_bound.Bound(args.active)
    report = {
        'state': os.path.abspath(args.state) if args.state else None,
        'chain': chain,
        'point_xz': point,
        'layers': [],
    }
    for kind in (9, 10):
        verts, polys = bound.geometry(bound.trees[kind])
        report['layers'].append(summarize('type%d' % kind, verts, polys, point))

    verts, polys, _materials, _header = read_otgrid(args.mc2)
    roads = [p for p in polys if p[1].split(':')[-1] == 'roadSG']
    report['layers'].append(summarize('mc2 roadSG', verts, roads, point, True))
    report['layers'].append(summarize('mc2 all', verts, polys, point, True))
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    if y is None:
        print('point=(%.6f, %.6f)' % (x, z))
    else:
        print('car=(%.6f, %.6f, %.6f)' % (x, y, z))
    for layer in report['layers']:
        print('%s: %d polys, %d containing' %
              (layer['name'], layer['polygons'], len(layer['containing'])))
        for entry in layer['containing'][:12]:
            print('  hit #%d material=%s y=%s edge=%.6f' %
                  (entry['index'], entry.get('material'), entry['plane_y'],
                   entry['edge_distance_xz']))
        print('  nearest:', [(q['index'], q.get('material'),
                              round(q['edge_distance_xz'], 4), q['plane_y'])
                             for q in layer['nearest'][:5]])


if __name__ == '__main__':
    main()
