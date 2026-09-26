#!/usr/bin/env python3
"""Inventory LA's horizontal visual surfaces and compare them with the active collision."""

import argparse
import csv
import json
import math
import os
import re
import sys


ROAD_RE = re.compile(
    r'(road|fwy|freeway|highway|highwy|ramp|bridge|tunnel|wayint|corner|junction|lane)',
    re.I)
ACTIVE_TOKENS = ('ashphalt', 'asphalt', 'crosswalk')


def normal_area(points):
    a, b, c = points
    u = tuple(b[i] - a[i] for i in range(3))
    v = tuple(c[i] - a[i] for i in range(3))
    n = (u[1] * v[2] - u[2] * v[1],
         u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0])
    mag = math.sqrt(sum(q * q for q in n))
    if mag < 1e-10:
        return None, 0.0
    return tuple(q / mag for q in n), mag / 2.0


def matrix(row):
    return [float(row['m%d%d' % (i, j)]) for i in range(3) for j in range(3)]


def transform_row(point, m, t):
    x, y, z = point
    return (m[0] * x + m[3] * y + m[6] * z + t[0],
            m[1] * x + m[4] * y + m[7] * z + t[1],
            m[2] * x + m[5] * y + m[8] * z + t[2])


def aggregate_slot(table, key):
    if key not in table:
        table[key] = {
            'material': key, 'placements': set(), 'models': set(),
            'triangles': 0, 'horizontal_triangles': 0,
            'horizontal_area': 0.0, 'covered_triangles': 0,
            'covered_area': 0.0, 'uncovered_triangles': 0,
            'uncovered_area': 0.0, 'upward_triangles': 0,
            'upward_area': 0.0, 'downward_triangles': 0,
            'downward_area': 0.0, 'examples_uncovered': [],
        }
    return table[key]


def clean_entry(entry):
    out = dict(entry)
    out['placements'] = len(entry['placements'])
    out['model_types'] = len(entry['models'])
    del out['models']
    for key in ('horizontal_area', 'covered_area', 'uncovered_area',
                'upward_area', 'downward_area'):
        out[key] = round(out[key], 3)
    if out['horizontal_triangles']:
        out['covered_percent'] = round(
            100.0 * out['covered_triangles'] / out['horizontal_triangles'], 2)
    else:
        out['covered_percent'] = None
    out['already_selected'] = any(t in entry['material'].lower() for t in ACTIVE_TOKENS)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--place', required=True)
    ap.add_argument('--models', required=True)
    ap.add_argument('--active', required=True)
    ap.add_argument('--tools', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--md')
    ap.add_argument('--horizontal-ny', type=float, default=0.70)
    ap.add_argument('--height-epsilon', type=float, default=0.75)
    ap.add_argument('--grid-cell', type=float, default=32.0)
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, args.tools)
    import mc3_bound
    import mc3_modpath as modpath
    from build_la_instance_asphalt_collision import load_raw, raw_triangles
    from inspect_la_road_point import plane_y, point_in_poly_xz

    bound = mc3_bound.Bound(args.active)
    collision_vertices, collision_polys_raw = bound.geometry(bound.trees[9])
    collision_polys = []
    grid = {}
    cell = args.grid_cell
    for poly_id, (normal, area, indices, _neighbors) in enumerate(collision_polys_raw):
        if normal[1] < 0.25:
            continue
        points = [collision_vertices[i] for i in indices]
        item_id = len(collision_polys)
        collision_polys.append((points, normal, poly_id))
        x0 = math.floor(min(p[0] for p in points) / cell)
        x1 = math.floor(max(p[0] for p in points) / cell)
        z0 = math.floor(min(p[2] for p in points) / cell)
        z1 = math.floor(max(p[2] for p in points) / cell)
        for gx in range(x0, x1 + 1):
            for gz in range(z0, z1 + 1):
                grid.setdefault((gx, gz), []).append(item_id)

    def collision_at(point):
        x, y, z = point
        key = (math.floor(x / cell), math.floor(z / cell))
        best = None
        for item_id in grid.get(key, []):
            points, normal, poly_id = collision_polys[item_id]
            if not point_in_poly_xz((x, z), points):
                continue
            surface_y = plane_y((x, z), points)
            if surface_y is None:
                continue
            delta = abs(surface_y - y)
            if best is None or delta < best[0]:
                best = (delta, poly_id, surface_y)
        return best if best is not None and best[0] <= args.height_epsilon else None

    with open(args.place, encoding='utf-8') as stream:
        rows = list(csv.DictReader((line for line in stream if not line.startswith('#')),
                                   delimiter='\t'))

    model_cache = {}

    def model_data(row):
        filename = '%s_%s_%s.mod' % (row['name'], row['lod'], row['part'])
        path = os.path.join(args.models, filename)
        cached = model_cache.get(path)
        if cached is not None:
            return filename, cached
        mesh = load_raw(modpath, path)
        positions = mesh.positions()
        materials = []
        for material in mesh.a(modpath.SLOT_MATERIAL).items:
            triangles = []
            for packet in material.packets:
                for primitive in packet.primitives:
                    for tri_indices in raw_triangles(modpath, primitive):
                        indices = [packet.adjuncts[i][0] for i in tri_indices]
                        triangles.append(tuple(positions[i] for i in indices))
            materials.append((material.name, triangles))
        model_cache[path] = materials
        return filename, materials

    all_instance = {}
    road_materials = {}
    road_models = {}
    road_model_materials = {}
    processed_rows = {'inst': 0, 'comp': 0}
    road_rows = {'inst': 0, 'comp': 0}
    triangles_seen = 0

    for row_index, row in enumerate(rows):
        kind = row['kind']
        if kind not in ('inst', 'comp'):
            continue
        processed_rows[kind] += 1
        is_road = bool(ROAD_RE.search(row['name']))
        if is_road:
            road_rows[kind] += 1
        # Every instance goes into the broad inventory. Components are only
        # expanded in the road subset, so roofs are not mistaken for road.
        if kind == 'comp' and not is_road:
            continue
        filename, materials = model_data(row)
        m = matrix(row)
        t = tuple(float(row[c]) for c in ('tx', 'ty', 'tz'))
        placement_key = '%d:%s' % (row_index, filename)
        for material_name, local_tris in materials:
            wide = aggregate_slot(all_instance, material_name) if kind == 'inst' else None
            road = aggregate_slot(road_materials, material_name) if is_road else None
            model_material = None
            if is_road:
                mm_key = '%s|%s|%s' % (kind, row['name'], material_name)
                model_material = aggregate_slot(road_model_materials, mm_key)
                model_material['kind'] = kind
                model_material['model'] = row['name']
                model_material['material'] = material_name
            for entry in (wide, road, model_material):
                if entry is not None:
                    entry['placements'].add(placement_key)
                    entry['models'].add(filename)
                    entry['triangles'] += len(local_tris)
            for local_tri in local_tris:
                tri = tuple(transform_row(p, m, t) for p in local_tri)
                normal, area = normal_area(tri)
                triangles_seen += 1
                if normal is None or abs(normal[1]) < args.horizontal_ny:
                    continue
                center = tuple(sum(p[c] for p in tri) / 3.0 for c in range(3))
                hit = collision_at(center) if is_road else None
                for entry in (wide, road, model_material):
                    if entry is None:
                        continue
                    entry['horizontal_triangles'] += 1
                    entry['horizontal_area'] += area
                    if normal[1] >= 0:
                        entry['upward_triangles'] += 1
                        entry['upward_area'] += area
                    else:
                        entry['downward_triangles'] += 1
                        entry['downward_area'] += area
                    if is_road and hit:
                        entry['covered_triangles'] += 1
                        entry['covered_area'] += area
                    elif is_road:
                        entry['uncovered_triangles'] += 1
                        entry['uncovered_area'] += area
                        if len(entry['examples_uncovered']) < 8:
                            entry['examples_uncovered'].append({
                                'kind': kind, 'model': row['name'], 'file': filename,
                                'hood': row['hood'], 'center': [round(q, 4) for q in center],
                                'normal_y': round(normal[1], 6), 'area': round(area, 4),
                            })

            if is_road:
                model_key = '%s|%s' % (kind, row['name'])
                model_entry = road_models.setdefault(model_key, {
                    'kind': kind, 'model': row['name'], 'placements': set(),
                    'materials': set(), 'horizontal_triangles': 0,
                    'covered_triangles': 0, 'uncovered_triangles': 0,
                    'uncovered_area': 0.0, 'upward_triangles': 0,
                    'downward_triangles': 0, 'examples_uncovered': [],
                })
                model_entry['placements'].add(placement_key)
                model_entry['materials'].add(material_name)
                for local_tri in local_tris:
                    tri = tuple(transform_row(p, m, t) for p in local_tri)
                    normal, area = normal_area(tri)
                    if normal is None or abs(normal[1]) < args.horizontal_ny:
                        continue
                    center = tuple(sum(p[c] for p in tri) / 3.0 for c in range(3))
                    hit = collision_at(center)
                    model_entry['horizontal_triangles'] += 1
                    if normal[1] >= 0:
                        model_entry['upward_triangles'] += 1
                    else:
                        model_entry['downward_triangles'] += 1
                    if hit:
                        model_entry['covered_triangles'] += 1
                    else:
                        model_entry['uncovered_triangles'] += 1
                        model_entry['uncovered_area'] += area
                        if len(model_entry['examples_uncovered']) < 5:
                            model_entry['examples_uncovered'].append({
                                'material': material_name, 'hood': row['hood'],
                                'center': [round(q, 4) for q in center],
                                'area': round(area, 4),
                            })

    all_list = [clean_entry(v) for v in all_instance.values()]
    all_list.sort(key=lambda x: x['horizontal_area'], reverse=True)
    road_list = [clean_entry(v) for v in road_materials.values()]
    road_list.sort(key=lambda x: x['uncovered_area'], reverse=True)
    model_material_list = [clean_entry(v) for v in road_model_materials.values()]
    model_material_list.sort(key=lambda x: x['uncovered_area'], reverse=True)
    model_list = []
    for value in road_models.values():
        entry = dict(value)
        entry['placements'] = len(value['placements'])
        entry['materials'] = sorted(value['materials'])
        entry['uncovered_area'] = round(value['uncovered_area'], 3)
        if value['horizontal_triangles']:
            entry['covered_percent'] = round(
                100.0 * value['covered_triangles'] / value['horizontal_triangles'], 2)
        else:
            entry['covered_percent'] = None
        model_list.append(entry)
    model_list.sort(key=lambda x: x['uncovered_area'], reverse=True)

    candidates = [x for x in road_list
                  if not x['already_selected'] and x['uncovered_area'] >= 1.0]
    report = {
        'inputs': {'place': os.path.abspath(args.place),
                   'models': os.path.abspath(args.models),
                   'active': os.path.abspath(args.active)},
        'criteria': {'road_name_regex': ROAD_RE.pattern,
                     'horizontal_normal_y_min': args.horizontal_ny,
                     'collision_height_epsilon': args.height_epsilon,
                     'already_selected_tokens': ACTIVE_TOKENS},
        'counts': {'placement_rows': processed_rows,
                   'roadlike_rows': road_rows,
                   'parsed_model_files': len(model_cache),
                   'expanded_triangles_seen': triangles_seen,
                   'active_type9_polygons': len(collision_polys_raw)},
        'candidate_materials_not_in_active_filter': candidates,
        'roadlike_materials': road_list,
        'roadlike_model_materials': model_material_list,
        'roadlike_models': model_list,
        'all_instance_materials': all_list,
    }
    with open(args.out, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')

    if args.md:
        lines = [
            '# Los Angeles collision surface inventory', '',
            'Active collision: `%s`' % args.active, '',
            'Criterion: road models by name, triangles with |normal Y| >= %.2f; '
            'covered when the active type9 has a floor at the centroid within %.2f unit(s) of height.'
            % (args.horizontal_ny, args.height_epsilon), '',
            '## Summary', '',
            '- Placements: %d instances and %d components.'
            % (processed_rows['inst'], processed_rows['comp']),
            '- Road subset: %d instances and %d components.'
            % (road_rows['inst'], road_rows['comp']),
            '- Models read: %d; triangles expanded: %d.'
            % (len(model_cache), triangles_seen),
            '- Candidate materials still outside the filter: %d.' % len(candidates), '',
            '## Horizontal road materials still uncovered', '',
            '| material | placements | models | uncovered tris | horiz. +Y total | horiz. -Y total | uncovered area | coverage |',
            '|---|---:|---:|---:|---:|---:|---:|---:|',
        ]
        for item in candidates:
            lines.append('| `%s` | %d | %d | %d | %d | %d | %.3f | %s%% |' % (
                item['material'], item['placements'], item['model_types'],
                item['uncovered_triangles'], item['upward_triangles'],
                item['downward_triangles'], item['uncovered_area'],
                item['covered_percent']))
        lines += ['', '## Road models with the largest uncovered area', '',
                  '| kind | model | placements | uncovered tris | uncovered area | coverage |',
                  '|---|---|---:|---:|---:|---:|']
        for item in model_list[:80]:
            if item['uncovered_area'] < 1.0:
                continue
            lines.append('| %s | `%s` | %d | %d | %.3f | %s%% |' % (
                item['kind'], item['model'], item['placements'],
                item['uncovered_triangles'], item['uncovered_area'],
                item['covered_percent']))
        lines += ['', '## Note', '',
                  'An uncovered centroid alone does not prove the face should become collision: '
                  'decals, markings, water, ceilings and sloped walls can show up. '
                  'The examples and materials should be reviewed before the next PCK.', '']
        with open(args.md, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(lines))

    print('placements inst=%d comp=%d; roadlike inst=%d comp=%d' % (
        processed_rows['inst'], processed_rows['comp'], road_rows['inst'], road_rows['comp']))
    print('model files=%d; expanded triangles=%d; candidate materials=%d' % (
        len(model_cache), triangles_seen, len(candidates)))
    for item in candidates[:30]:
        print('%8d tris  %12.1f area  %6.2f%% covered  %s' % (
            item['uncovered_triangles'], item['uncovered_area'],
            item['covered_percent'], item['material']))


if __name__ == '__main__':
    main()
