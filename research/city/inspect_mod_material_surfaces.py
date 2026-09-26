#!/usr/bin/env python3
"""Summarise triangles and XZ coverage of each material of a v1.10 .mod."""

import argparse
import json
import math
import os
import sys


def normal_and_area(points):
    a, b, c = points
    u = tuple(b[i] - a[i] for i in range(3))
    v = tuple(c[i] - a[i] for i in range(3))
    n = (u[1] * v[2] - u[2] * v[1],
         u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0])
    mag = math.sqrt(sum(q * q for q in n))
    if mag < 1e-12:
        return None, 0.0
    return tuple(q / mag for q in n), mag / 2.0


def orientation_bands(triangles):
    groups = {
        'up': [t for t in triangles if t['normal'][1] >= 0.70],
        'down': [t for t in triangles if t['normal'][1] <= -0.70],
        'side_or_steep': [t for t in triangles if abs(t['normal'][1]) < 0.70],
    }
    out = {}
    for name, tris in groups.items():
        centers_y = [sum(p[1] for p in t['points']) / 3.0 for t in tris]
        out[name] = {
            'triangles': len(tris),
            'area': sum(t['area'] for t in tris),
            'center_y': ([min(centers_y), max(centers_y)]
                         if centers_y else None),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('model')
    ap.add_argument('--tools', required=True)
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, args.tools)
    import mc3_modpath as modpath
    from build_la_instance_asphalt_collision import load_raw, raw_triangles

    mesh = load_raw(modpath, args.model)
    positions = mesh.positions()
    output = []
    for material in mesh.a(modpath.SLOT_MATERIAL).items:
        tris = []
        for packet in material.packets:
            for primitive in packet.primitives:
                for tri_indices in raw_triangles(modpath, primitive):
                    indices = [packet.adjuncts[i][0] for i in tri_indices]
                    points = [positions[i] for i in indices]
                    normal, area = normal_and_area(points)
                    tris.append({'indices': indices, 'points': points,
                                 'normal': normal, 'area': area})
        flat = [p for tri in tris for p in tri['points']]
        output.append({
            'material': material.name,
            'triangles': len(tris),
            'area': sum(t['area'] for t in tris),
            'normal_y': [min(t['normal'][1] for t in tris),
                         max(t['normal'][1] for t in tris)] if tris else None,
            'orientation_bands': orientation_bands(tris),
            'aabb': [[min(p[c] for p in flat) for c in range(3)],
                     [max(p[c] for p in flat) for c in range(3)]] if flat else None,
            'triangle_data': tris,
        })
    if args.summary:
        for item in output:
            item.pop('triangle_data', None)
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
