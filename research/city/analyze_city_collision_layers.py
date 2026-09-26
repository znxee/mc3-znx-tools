#!/usr/bin/env python3
"""Compare MC3 collision/road layers and inventory MC2 ground materials."""
import os
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, str(TOOLKIT))
from mc3_bound import Bound
from mc2_bnd_parse import read_otgrid
from mc2_bnd_to_mc3 import normal_and_area


def point_in_xz(point, verts, eps=1e-5):
    """Convex polygon inclusion, accepting either winding."""
    px, pz = point
    signs = []
    for i, a in enumerate(verts):
        b = verts[(i + 1) % len(verts)]
        cross = (b[0] - a[0]) * (pz - a[2]) - (b[2] - a[2]) * (px - a[0])
        if abs(cross) > eps:
            signs.append(cross > 0)
    return not signs or all(s == signs[0] for s in signs)


def plane_y(poly, verts, x, z):
    normal, _area, indices, _neighbors = poly
    nx, ny, nz = normal
    if abs(ny) < 1e-6:
        return None
    p = verts[indices[0]]
    return p[1] - (nx * (x - p[0]) + nz * (z - p[2])) / ny


def compare_pck(path):
    bound = Bound(path)
    collision_v, collision_p = bound.geometry(bound.trees[9])
    road_v, road_p = bound.geometry(bound.trees[10])
    ground = []
    for index, poly in enumerate(collision_p):
        if abs(poly[0][1]) < 0.7:
            continue
        pts = [collision_v[i] for i in poly[2]]
        lo = (min(p[0] for p in pts), min(p[2] for p in pts))
        hi = (max(p[0] for p in pts), max(p[2] for p in pts))
        ground.append((index, poly, pts, lo, hi))

    deltas = []
    unmatched = []
    road_rows = []
    for index, poly in enumerate(road_p):
        pts = [road_v[i] for i in poly[2]]
        x = sum(p[0] for p in pts) / len(pts)
        z = sum(p[2] for p in pts) / len(pts)
        y = plane_y(poly, road_v, x, z)
        candidates = []
        for ci, cp, cpts, lo, hi in ground:
            if lo[0] - 1e-5 <= x <= hi[0] + 1e-5 and lo[1] - 1e-5 <= z <= hi[1] + 1e-5:
                if point_in_xz((x, z), cpts):
                    cy = plane_y(cp, collision_v, x, z)
                    if cy is not None:
                        candidates.append((abs(y - cy), y - cy, ci, cy))
        if candidates:
            best = min(candidates)
            deltas.append(best[1])
            match = dict(collision_poly=best[2], collision_y=best[3], delta=best[1])
        else:
            unmatched.append(index)
            match = None
        road_rows.append(dict(index=index, centroid=[x, y, z], normal=poly[0],
                              area=poly[1], match=match))
    return dict(
        path=str(path),
        collision_polygons=len(collision_p),
        collision_ground_polygons=len(ground),
        road_polygons=len(road_p),
        road_area=sum(p[1] for p in road_p),
        road_up=sum(p[0][1] > 0.7 for p in road_p),
        road_down=sum(p[0][1] < -0.7 for p in road_p),
        road_vertical=sum(abs(p[0][1]) <= 0.7 for p in road_p),
        road_centroids_with_collision_ground=len(deltas),
        road_centroids_without_collision_ground=len(unmatched),
        overlap_delta_y=(dict(min=min(deltas), max=max(deltas),
                              mean=sum(deltas) / len(deltas)) if deltas else None),
        unmatched_road_polygons=unmatched,
        road_examples=road_rows[:12],
    )


def inventory_mc2(path):
    verts, polys, _materials, header = read_otgrid(path)
    by_material = defaultdict(lambda: dict(polygons=0, ground=0, up=0, down=0,
                                           area=0.0, ground_area=0.0,
                                           lo=[math.inf] * 3, hi=[-math.inf] * 3))
    for indices, material in polys:
        pts = [verts[i] for i in indices]
        normal, area = normal_and_area(pts)
        row = by_material[material]
        row['polygons'] += 1
        row['area'] += area
        for axis in range(3):
            row['lo'][axis] = min(row['lo'][axis], *(p[axis] for p in pts))
            row['hi'][axis] = max(row['hi'][axis], *(p[axis] for p in pts))
        if normal and abs(normal[1]) > 0.7:
            row['ground'] += 1
            row['ground_area'] += area
            row['up' if normal[1] > 0 else 'down'] += 1
    return dict(path=str(path), header=header, vertices=len(verts), polygons=len(polys),
                materials=dict(sorted(by_material.items(), key=lambda item: -item[1]['ground_area'])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pck', action='append', default=[])
    ap.add_argument('--mc2')
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    result = dict(pcks=[compare_pck(p) for p in args.pck],
                  mc2=inventory_mc2(args.mc2) if args.mc2 else None)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        args.out.write_text(rendered + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
