#!/usr/bin/env python3
"""Rebuild type10 from the global network and explicitly chosen type9 batches.

v14 proved that copying every type9 ``road`` floor into type10 is not a valid
contract: 12,378 faces stop the loader from reaching streaming. This generator
keeps the right idea (use the already validated type9 geometry), but only
accepts documented index ranges. For the v15 candidate the ranges are:

* 0:20372       the global type9 before the instances; includes roadSG and also
                alwaysdry/alwaysdry_int/alwaysdry_upass/bridge;
* 24826:25336   batch of 510 crosswalk triangles added on 2026-09-08.

Faces without the global ``road`` physical material stay excluded. The ranges
are parameters so a future type9 change does not silently reuse offsets that no
longer mean the same thing.
"""

import argparse
import os
import sys
from types import SimpleNamespace


def point_key(point):
    return tuple(round(value, 5) for value in point)


def polygon_key(points):
    return tuple(sorted(point_key(point) for point in points))


def parse_ranges(specs):
    ranges = []
    for spec in specs:
        first, sep, last = spec.partition(':')
        if not sep:
            raise ValueError('invalid range %r; use START:END' % spec)
        start, end = int(first, 0), int(last, 0)
        if start < 0 or end <= start:
            raise ValueError('invalid range %r' % spec)
        ranges.append((start, end))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            raise ValueError('overlapping ranges: %r and %r' %
                             (previous, current))
    return ranges


def make_prepare(selected_ranges):
    def prepare_type9_roads(path, _suffixes):
        import mc3_bound as MB
        import mc2_roads_to_mc3 as roads

        source = MB.Bound(path)
        node = source.trees[9]
        source_vertices, source_polygons = source.geometry(node)
        palette = source.materials(node)
        material_indices = source.material_indices(node)
        road_global = MB.GLOBAL_MATERIAL_IDS['road']

        if selected_ranges[-1][1] > len(source_polygons):
            raise ValueError(
                'range ends at %d, but type9 has only %d polys' %
                (selected_ranges[-1][1], len(source_polygons)))

        vertices = []
        vertex_map = {}
        polygons = []
        seen = set()
        per_range = {('%d:%d' % item): 0 for item in selected_ranges}
        stats = {
            'source': os.path.abspath(path),
            'source_vertices': len(source_vertices),
            'source_polygons': len(source_polygons),
            'selected_ranges': [list(item) for item in selected_ranges],
            'range_road_polygons': per_range,
            'source_road_polygons': 0,
            'selected': 0,
            'duplicate_polygons': 0,
            'degenerate_polygons': 0,
            'nonhorizontal_polygons': 0,
            'reversed_polygons': 0,
            'material_global_id': road_global,
        }

        for polygon_index, (poly, local_material) in enumerate(
                zip(source_polygons, material_indices)):
            range_key = None
            for start, end in selected_ranges:
                if start <= polygon_index < end:
                    range_key = '%d:%d' % (start, end)
                    break
            if range_key is None:
                continue
            if (local_material >= len(palette)
                    or palette[local_material] != road_global):
                continue
            stats['source_road_polygons'] += 1
            per_range[range_key] += 1

            points = [tuple(source_vertices[index]) for index in poly[2]]
            normal, area = roads.normal_and_area(points)
            if normal is None:
                stats['degenerate_polygons'] += 1
                continue
            if abs(normal[1]) < 0.70:
                stats['nonhorizontal_polygons'] += 1
                continue
            if normal[1] < 0:
                points.reverse()
                normal, area = roads.normal_and_area(points)
                stats['reversed_polygons'] += 1

            key = polygon_key(points)
            if key in seen:
                stats['duplicate_polygons'] += 1
                continue
            seen.add(key)

            indices = []
            for point in points:
                key_point = point_key(point)
                index = vertex_map.get(key_point)
                if index is None:
                    index = len(vertices)
                    vertex_map[key_point] = index
                    vertices.append(point)
                indices.append(index)
            polygons.append((normal, area, tuple(indices),
                             'type9:%s' % range_key))

        if not polygons:
            raise ValueError('the ranges provided no horizontal road floor')
        if len(vertices) > 0xFFFF or len(polygons) > 0xFFFF:
            raise ValueError('type10 exceeds u16: %d vertices, %d polygons' %
                             (len(vertices), len(polygons)))

        neighbors = [[0xFFFF] * len(poly[2]) for poly in polygons]
        edges = {}
        for polygon_index, (_normal, _area, indices, _material) in enumerate(polygons):
            for edge_index, first in enumerate(indices):
                second = indices[(edge_index + 1) % len(indices)]
                edge = (min(first, second), max(first, second))
                edges.setdefault(edge, []).append((polygon_index, edge_index))

        paired = 0
        nonmanifold = 0
        for uses in edges.values():
            if len(uses) == 2 and uses[0][0] != uses[1][0]:
                (left, left_edge), (right, right_edge) = uses
                neighbors[left][left_edge] = right
                neighbors[right][right_edge] = left
                paired += 1
            elif len(uses) > 2:
                nonmanifold += 1

        stats.update(
            selected=len(polygons),
            output_vertices=len(vertices),
            output_polygons=len(polygons),
            paired_edges=paired,
            boundary_edges=sum(value == 0xFFFF
                               for row in neighbors for value in row),
            nonmanifold_edges=nonmanifold,
            header={
                'source_kind': 9,
                'selection': 'global road (17) inside explicit type9 ranges',
            },
        )
        return vertices, polygons, neighbors, stats

    return prepare_type9_roads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True,
                        help='PCK whose type9 will feed type10')
    parser.add_argument('--donor', required=True,
                        help='PCK whose type9 will be kept')
    parser.add_argument('--tools', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--audit')
    parser.add_argument('--range', dest='ranges', action='append', required=True,
                        help='type9 range START:END; repeatable')
    parser.add_argument('--depth', type=int, default=8)
    parser.add_argument('--per-leaf', type=int, default=40)
    args = parser.parse_args()

    selected_ranges = parse_ranges(args.ranges)
    sys.path.insert(0, args.tools)
    import mc2_roads_to_mc3 as roads

    roads.prepare_roads = make_prepare(selected_ranges)
    build_args = SimpleNamespace(
        mc2=args.source,
        donor=args.donor,
        out=args.out,
        audit=args.audit,
        materials=','.join(args.ranges),
        depth=args.depth,
        per_leaf=args.per_leaf,
    )
    roads.build(build_args)


if __name__ == '__main__':
    main()
