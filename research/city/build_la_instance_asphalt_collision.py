#!/usr/bin/env python3
"""Merge MC2's global collision with instanced floors selected from the .hood."""

import argparse
import collections
import csv
import json
import math
import os
import struct
import sys


GLOBAL_MATERIAL_IDS = {
    'default': 0,
    'alley': 1,
    'cobblestone': 2,
    'death': 3,
    'dirt': 4,
    'grass': 8,
    'interior': 11,
    'metal': 13,
    'railroad': 16,
    'road': 17,
    'sewer': 20,
    'sidewalk': 21,
    'wall': 25,
    'water': 26,
}


def physical_material_name(source_name):
    """Translate MC2/mesh material names to MC3 physical material classes."""
    name = source_name.rsplit(':', 1)[-1].lower()
    if 'cobblestone' in name:
        return 'cobblestone'
    if 'railroad' in name:
        return 'railroad'
    if 'sidewalk' in name or 'concrete' in name or 'stairs' in name:
        return 'sidewalk'
    if ('road' in name or 'asphalt' in name or 'ashphalt' in name
            or 'crosswalk' in name or 'alwaysdry' in name or 'bridge' in name):
        return 'road'
    if 'building' in name or 'treetrunk' in name or 'wall' in name:
        return 'wall'
    for material in ('water', 'grass', 'dirt', 'metal', 'sewer', 'alley',
                     'interior', 'death'):
        if material in name:
            return material
    return 'default'


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


def transform_row(p, m, t):
    x, y, z = p
    return (m[0] * x + m[3] * y + m[6] * z + t[0],
            m[1] * x + m[4] * y + m[7] * z + t[1],
            m[2] * x + m[5] * y + m[8] * z + t[2])


def tri_key(points):
    return tuple(sorted(tuple(round(c, 5) for c in p) for p in points))


def point_in_triangle_xz(point, tri, eps=1e-5):
    px, pz = point
    a, b, c = [(v[0], v[2]) for v in tri]

    def cross(o, u, v):
        return ((u[0] - o[0]) * (v[1] - o[1])
                - (u[1] - o[1]) * (v[0] - o[0]))

    d1 = cross(a, b, (px, pz))
    d2 = cross(b, c, (px, pz))
    d3 = cross(c, a, (px, pz))
    return ((d1 >= -eps and d2 >= -eps and d3 >= -eps)
            or (d1 <= eps and d2 <= eps and d3 <= eps))


def plane_y(tri, normal, x, z):
    if abs(normal[1]) < 1e-8:
        return None
    p = tri[0]
    return p[1] - (normal[0] * (x - p[0]) + normal[2] * (z - p[2])) / normal[1]


def merge_coplanar_quads(polys, boxes, verts):
    """Merge pairs of coplanar triangles that share an edge into one quad.

    This is a RAM budget, not aesthetics: the whole `_bnd.pck` is loaded into
    PS2 memory, every polygon costs a fixed 32 bytes, and Fork City MODS
    measured that freeroam STOPS LOADING when the bound grows - 2.4 MB were free
    but the largest contiguous block was 0.48 MB. A quad holds the same surface
    as two triangles for half the cost, and MC2 itself already ships 12,575
    quads, so the format and the physics handle them.

    The guards, all of them needed:

    - only triangle with triangle (``idx[3] == 0``);
    - same material index, or the merge would change the friction of half the face;
    - practically equal normals, or the quad is not planar;
    - exactly two shared vertices, which is the edge;
    - **convexity**: the four corners must always turn the same way. A concave
      or self-intersecting quad makes the runtime point-in-polygon test answer
      wrong, and it errs INWARDS - the car falls through;
    - **``v3`` cannot be 0**, because the game itself tells a triangle from a
      quad by ``v3 == 0`` (read in CullSpherePolysRecurse). If the fourth index
      lands on zero, the quad is rotated cyclically, which keeps the polygon.
    """
    edge = {}
    for i, (_n, _a, idx, _s, _m) in enumerate(polys):
        if idx[3] != 0:
            continue
        for e in range(3):
            k = (min(idx[e], idx[(e + 1) % 3]), max(idx[e], idx[(e + 1) % 3]))
            edge.setdefault(k, []).append(i)

    dead = set()
    merged = rejected_convex = rejected_plane = rejected_mat = 0
    for pair in edge.values():
        if len(pair) != 2:
            continue
        i, j = pair
        if i in dead or j in dead:
            continue
        na, aa, ia, sa, ma = polys[i]
        nb, ab, ib, sb, mb = polys[j]
        if ia[3] != 0 or ib[3] != 0:
            continue
        if ma != mb:
            rejected_mat += 1
            continue
        if sum(x * y for x, y in zip(na, nb)) < 0.99999:
            rejected_plane += 1
            continue
        a3, b3 = ia[:3], ib[:3]
        shared = [v for v in a3 if v in b3]
        if len(shared) != 2:
            continue
        oa = [v for v in a3 if v not in shared][0]
        ob = [v for v in b3 if v not in shared][0]
        # rotate A to (oa, p, q), so the shared edge is p->q
        r = a3.index(oa)
        oa, p, q = a3[r], a3[(r + 1) % 3], a3[(r + 2) % 3]
        quad = [oa, p, ob, q]
        pts = [verts[v] for v in quad]
        ok = True
        for k in range(4):
            u = [pts[(k + 1) % 4][c] - pts[k][c] for c in range(3)]
            v = [pts[(k + 2) % 4][c] - pts[(k + 1) % 4][c] for c in range(3)]
            cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                  u[0] * v[1] - u[1] * v[0])
            if sum(cr[c] * na[c] for c in range(3)) <= 0.0:
                ok = False
                break
        if not ok:
            rejected_convex += 1
            continue
        while quad[3] == 0:                 # v3 == 0 would be read as a triangle
            quad = quad[1:] + quad[:1]
        polys[i] = (na, aa + ab, quad, sa, ma)
        pts = [verts[v] for v in quad]
        boxes[i] = ([min(w[c] for w in pts) for c in range(3)],
                     [max(w[c] for w in pts) for c in range(3)])
        dead.add(j)
        merged += 1

    if dead:
        keep = [i for i in range(len(polys)) if i not in dead]
        polys[:] = [polys[i] for i in keep]
        boxes[:] = [boxes[i] for i in keep]
    return dict(merged=merged, rejected_convex=rejected_convex,
                rejected_plane=rejected_plane, rejected_material=rejected_mat,
                polygons_after=len(polys))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mc2', required=True)
    ap.add_argument('--place', required=True)
    ap.add_argument('--models', required=True)
    ap.add_argument('--tools', required=True)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--audit')
    ap.add_argument('--depth', type=int, default=10)
    ap.add_argument('--per-leaf', type=int, default=26)
    ap.add_argument('--test-x', type=float)
    ap.add_argument('--test-z', type=float)
    ap.add_argument('--extra-material-token', action='append', default=[],
                    help='include another drivable material token (e.g. crosswalk)')
    ap.add_argument('--scoped-material', action='append', default=[],
                    metavar='MODEL=MATERIAL',
                    help='include one exact material only on one exact model; repeatable')
    ap.add_argument('--selection-manifest',
                    help='JSON with rules per kind/model/material, orientation and winding')
    ap.add_argument('--merge-quads', action='store_true',
                    help='merge neighbouring coplanar triangles into quads; cuts '
                         'about 240 KB from the file without losing surface')
    ap.add_argument('--floor-patch',
                    help='JSON with SYNTHETIC floor quads (make_floor_patch.py). '
                         'For the crossings whose lod0 mesh does not exist in the '
                         'source: there is no geometry to select, so the floor is '
                         'interpolated from the plane of the neighbouring streets. '
                         'Each quad becomes two triangles with all four corners '
                         'already on the plane.')
    a = ap.parse_args()
    material_tokens = ['ashphalt', 'asphalt'] + [
        token.lower() for token in a.extra_material_token]
    scoped_materials = {}

    def add_scoped(kind, model_name, material_name, orientation='any',
                   min_abs_normal_y=0.0, flip_downward=False, source='cli',
                   physical_material=None):
        # `physical_material` names the MC3 physical class (road, sidewalk,
        # wall, ...) for THIS rule. Absent = road, which was the only possible
        # behaviour before and is what every earlier rule expects - changing the
        # default would rewrite the material of all of them.
        if physical_material is not None:
            physical_material = physical_material.strip().lower()
            if physical_material not in GLOBAL_MATERIAL_IDS:
                ap.error('unknown physical_material: %r (valid: %s)'
                         % (physical_material,
                            ', '.join(sorted(GLOBAL_MATERIAL_IDS))))
        kind = kind.strip().lower()
        model_name = model_name.strip()
        material_name = material_name.strip()
        orientation = orientation.strip().lower()
        if kind not in ('inst', 'comp'):
            ap.error('invalid scoped kind: %r' % kind)
        if orientation not in ('any', 'up', 'down', 'both'):
            ap.error('invalid scoped orientation: %r' % orientation)
        if not model_name or not material_name:
            ap.error('empty scoped model/material')
        key = (kind, model_name.lower(), material_name.lower())
        if key in scoped_materials:
            ap.error('repeated scoped rule: %s:%s=%s' %
                     (kind, model_name, material_name))
        scoped_materials[key] = {
            'kind': kind, 'model': model_name, 'material': material_name,
            'orientation': orientation, 'physical_material': physical_material,
            'min_abs_normal_y': float(min_abs_normal_y),
            'flip_downward': bool(flip_downward), 'source': source,
            'placements': 0, 'triangles_seen': 0, 'filtered_out': 0,
            'triangles_added': 0, 'duplicates_skipped': 0,
            'degenerate_skipped': 0, 'downward_input': 0,
            'flipped_downward': 0, 'downward_output': 0,
        }

    for spec in a.scoped_material:
        model_name, sep, material_name = spec.partition('=')
        model_name = model_name.strip()
        material_name = material_name.strip()
        if not sep or not model_name or not material_name:
            ap.error('--scoped-material needs MODEL=MATERIAL: %r' % spec)
        add_scoped('inst', model_name, material_name)
    if a.selection_manifest:
        with open(a.selection_manifest, encoding='utf-8') as stream:
            manifest = json.load(stream)
        for index, rule in enumerate(manifest.get('rules', [])):
            try:
                add_scoped(
                    rule.get('kind', 'inst'), rule['model'], rule['material'],
                    rule.get('orientation', 'any'),
                    rule.get('min_abs_normal_y', 0.0),
                    rule.get('flip_downward', False),
                    'manifest:%d' % index,
                    rule.get('physical_material'))
            except (KeyError, TypeError, ValueError) as exc:
                ap.error('rule %d invalid in the manifest: %s' % (index, exc))

    sys.path.insert(0, a.tools)
    import mc3_bound as MB
    import mc3_modpath as modpath
    from mc2_bnd_parse import read_otgrid
    from mc2_bnd_to_mc3 import Leaf, build_octree, normal_and_area, walks

    b = MB.Bound(a.donor)
    node = b.trees[9]
    material_ids = b.materials(node)
    if GLOBAL_MATERIAL_IDS['road'] not in material_ids:
        sys.exit('the donor does not contain the road physical material (global id 17)')

    def local_material(source_name):
        physical = physical_material_name(source_name)
        global_id = GLOBAL_MATERIAL_IDS[physical]
        if global_id not in material_ids:
            return 0, 'default'
        return material_ids.index(global_id), physical

    road_material_index = material_ids.index(GLOBAL_MATERIAL_IDS['road'])

    # LOCAL index in the donor palette for each rule's physical class. Resolved
    # here, once, to fail early if the donor palette lacks the requested class -
    # and not in the middle of expanding 8,511 instances.
    for _stat in scoped_materials.values():
        _name = _stat.get('physical_material')
        if _name is None:
            _stat['material_index'] = road_material_index
            _stat['resolved_material'] = 'road'
            continue
        _gid = GLOBAL_MATERIAL_IDS[_name]
        if _gid not in material_ids:
            sys.exit('the donor type9 palette has no %s (global id %d); '
                     'rule %s:%s=%s cannot be applied'
                     % (_name, _gid, _stat['kind'], _stat['model'],
                        _stat['material']))
        _stat['material_index'] = material_ids.index(_gid)
        _stat['resolved_material'] = _name

    floor_patch = []
    if a.floor_patch:
        with open(a.floor_patch, encoding='utf-8') as stream:
            floor_patch = json.load(stream).get('quads', [])
        for q in floor_patch:
            name = q.get('material', 'road')
            if name not in GLOBAL_MATERIAL_IDS:
                ap.error('unknown material in the floor patch: %r' % name)
            if GLOBAL_MATERIAL_IDS[name] not in material_ids:
                sys.exit('the donor palette has no %s for the floor patch' % name)

    verts0, polys0, mats, _cab = read_otgrid(a.mc2)
    verts = [tuple(v) for v in verts0]
    polys = []
    boxes = []
    known_tris = set()
    base_degenerates = 0

    source_material_counts = collections.Counter()
    for vi, item_name in polys0:
        pts = [verts[i] for i in vi]
        normal, area = normal_and_area(pts)
        if normal is None:
            base_degenerates += 1
            continue
        idx = list(vi) + ([0] if len(vi) == 3 else [])
        material_index, physical = local_material(item_name)
        source_material_counts[(item_name, physical, material_index)] += 1
        polys.append((normal, area, idx, 'mc2_global', material_index))
        boxes.append(([min(p[c] for p in pts) for c in range(3)],
                       [max(p[c] for p in pts) for c in range(3)]))
        if len(vi) == 3:
            known_tris.add(tri_key(pts))

    vertex_map = {}
    for i, p in enumerate(verts):
        vertex_map.setdefault(tuple(round(c, 5) for c in p), i)

    with open(a.place, encoding='utf-8') as stream:
        rows = list(csv.DictReader((s for s in stream if not s.startswith('#')),
                                   delimiter='\t'))
    rows = [r for r in rows if r['kind'] in ('inst', 'comp')]
    cache = {}
    asphalt_rows = 0
    asphalt_models = set()
    asphalt_materials = {}
    added = duplicate = degenerate = downward = 0
    point_hits = []

    for row in rows:
        name = '%s_%s_%s.mod' % (row['name'], row['lod'], row['part'])
        path = os.path.join(a.models, name)
        mesh = cache.get(path)
        if mesh is None:
            mesh = cache[path] = load_raw(modpath, path)
        pos = mesh.positions()
        m = matrix(row)
        t = tuple(float(row[c]) for c in ('tx', 'ty', 'tz'))
        used = False
        for mat in mesh.a(modpath.SLOT_MATERIAL).items:
            material_l = mat.name.lower()
            global_match = (row['kind'] == 'inst'
                            and any(token in material_l for token in material_tokens))
            scoped_key = (row['kind'].lower(), row['name'].lower(), material_l)
            scoped_stat = scoped_materials.get(scoped_key)
            if not global_match and scoped_stat is None:
                continue
            used = True
            asphalt_materials[mat.name] = asphalt_materials.get(mat.name, 0) + 1
            if scoped_stat is not None:
                scoped_stat['placements'] += 1
            for packet in mat.packets:
                for primitive in packet.primitives:
                    for ti in raw_triangles(modpath, primitive):
                        if scoped_stat is not None:
                            scoped_stat['triangles_seen'] += 1
                        vi = [packet.adjuncts[i][0] for i in ti]
                        tri = tuple(transform_row(pos[i], m, t) for i in vi)
                        normal, area = normal_and_area(tri)
                        if normal is None:
                            degenerate += 1
                            if scoped_stat is not None:
                                scoped_stat['degenerate_skipped'] += 1
                            continue
                        if scoped_stat is not None and not global_match:
                            ny = normal[1]
                            min_ny = scoped_stat['min_abs_normal_y']
                            orientation = scoped_stat['orientation']
                            accepted = abs(ny) >= min_ny
                            if orientation == 'up':
                                accepted = accepted and ny >= 0
                            elif orientation == 'down':
                                accepted = accepted and ny <= 0
                            if not accepted:
                                scoped_stat['filtered_out'] += 1
                                continue
                            if ny < 0:
                                scoped_stat['downward_input'] += 1
                                if scoped_stat['flip_downward']:
                                    tri = (tri[0], tri[2], tri[1])
                                    normal, area = normal_and_area(tri)
                                    scoped_stat['flipped_downward'] += 1
                        key = tri_key(tri)
                        if key in known_tris:
                            duplicate += 1
                            if scoped_stat is not None:
                                scoped_stat['duplicates_skipped'] += 1
                            continue
                        known_tris.add(key)
                        if normal[1] < 0:
                            downward += 1
                            if scoped_stat is not None:
                                scoped_stat['downward_output'] += 1
                        idx = []
                        for p in tri:
                            vk = tuple(round(c, 5) for c in p)
                            n = vertex_map.get(vk)
                            if n is None:
                                n = len(verts)
                                vertex_map[vk] = n
                                verts.append(p)
                            idx.append(n)
                        # Material per rule. The global path (asphalt token)
                        # has no rule and stays road; a manifest rule can ask
                        # for another class - a crossing curb is sidewalk, not
                        # road, and giving it road would put asphalt sound and
                        # friction on a step.
                        poly_material = road_material_index
                        if scoped_stat is not None and not global_match:
                            poly_material = scoped_stat['material_index']
                        polys.append((normal, area, idx + [0], 'instance_asphalt',
                                      poly_material))
                        boxes.append(([min(p[c] for p in tri) for c in range(3)],
                                       [max(p[c] for p in tri) for c in range(3)]))
                        added += 1
                        if scoped_stat is not None:
                            scoped_stat['triangles_added'] += 1
                        if a.test_x is not None and a.test_z is not None:
                            if point_in_triangle_xz((a.test_x, a.test_z), tri):
                                point_hits.append({
                                    'model': row['name'],
                                    'file': name,
                                    'material': mat.name,
                                    'translation': t,
                                    'triangle': tri,
                                    'normal': normal,
                                    'surface_y': plane_y(tri, normal, a.test_x, a.test_z),
                                })
        if used:
            asphalt_rows += 1
            asphalt_models.add(name)

    # ---- synthetic floor ---------------------------------------------------
    # It comes from no model: it fills the crossings whose lod0 mesh never
    # existed in the source (neither in the .xmod nor in losangeles.bnd). It goes
    # in last, after all the real geometry, and through the SAME deduplication -
    # if there is already a face there, the quad is dropped.
    patch_added = patch_dup = 0
    for q in floor_patch:
        mi = material_ids.index(GLOBAL_MATERIAL_IDS[q.get('material', 'road')])
        corners = ((q['x0'], q['y00'], q['z0']), (q['x0'], q['y01'], q['z1']),
                   (q['x1'], q['y11'], q['z1']), (q['x1'], q['y10'], q['z0']))
        for tri in ((corners[0], corners[1], corners[2]),
                    (corners[0], corners[2], corners[3])):
            normal, area = normal_and_area(tri)
            if normal is None:
                degenerate += 1
                continue
            if normal[1] < 0:                 # the slab has to face upwards
                tri = (tri[0], tri[2], tri[1])
                normal, area = normal_and_area(tri)
            key = tri_key(tri)
            if key in known_tris:
                patch_dup += 1
                continue
            known_tris.add(key)
            idx = []
            for pt in tri:
                vk = tuple(round(c, 5) for c in pt)
                n = vertex_map.get(vk)
                if n is None:
                    n = len(verts)
                    vertex_map[vk] = n
                    verts.append(pt)
                idx.append(n)
            polys.append((normal, area, idx + [0], 'floor_patch', mi))
            boxes.append(([min(pt[c] for pt in tri) for c in range(3)],
                           [max(pt[c] for pt in tri) for c in range(3)]))
            added += 1
            patch_added += 1
    if floor_patch:
        print('synthetic floor: %d quads -> %d triangles (%d already existed)'
              % (len(floor_patch), patch_added, patch_dup))

    merge_stats = None
    if a.merge_quads:
        before = len(polys)
        merge_stats = merge_coplanar_quads(polys, boxes, verts)
        print('quad merge: %d pairs merged, %d -> %d polygons '
              '(-%d KB); refused %d for convexity, %d for plane, %d for material'
              % (merge_stats['merged'], before, len(polys),
                 (before - len(polys)) * 32 // 1024,
                 merge_stats['rejected_convex'], merge_stats['rejected_plane'],
                 merge_stats['rejected_material']))

    if len(verts) > 0xFFFF:
        sys.exit('%d vertices exceed the 16-bit index' % len(verts))
    if not added:
        sys.exit('no instanced asphalt triangle was added')
    if a.test_x is not None and not point_hits:
        sys.exit('the test point did not land on any new triangle')

    lo = [min(q[c] for q in verts) for c in range(3)]
    hi = [max(q[c] for q in verts) for c in range(3)]
    side = max(hi[c] - lo[c] for c in range(3))
    centre = [(lo[c] + hi[c]) / 2.0 for c in range(3)]
    clo = [centre[c] - side / 2.0 for c in range(3)]
    chi = [centre[c] + side / 2.0 for c in range(3)]
    blocks, root, background = build_octree(boxes, clo, chi, a.depth, a.per_leaf)
    leaves = walks(blocks, root)
    refs = sum(len(f.ids) for f in leaves)
    if refs > 0xFFFF:
        sys.exit('%d references exceed the 16-bit start; raise --per-leaf' % refs)
    cell = side / float(2 ** background)

    data = bytearray(b.data)

    def append(blob, align=16):
        while len(data) % align:
            data.append(0xCD)
        off = len(data)
        data.extend(blob)
        return off

    def va(off):
        return off + b.base

    bp = bytearray()
    for normal, area, idx, _src, material_index in polys:
        bp += struct.pack('<3f', normal[0], normal[1], normal[2])
        bp += MB.pack_area_with_material(area, material_index)
        bp += struct.pack('<8H', idx[0], idx[1], idx[2], idx[3],
                          0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
    off_poly = append(bytes(bp))
    off_vert = append(b''.join(struct.pack('<3f', *q) for q in verts))

    index_list = []
    for leaf in leaves:
        leaf.start, leaf.count = len(index_list), len(leaf.ids)
        index_list.extend(leaf.ids)
    off_idx = append(struct.pack('<%dH' % len(index_list), *index_list))
    block_off = [append(bytes(32)) for _ in blocks]

    def value(slot):
        if isinstance(slot, Leaf):
            return (slot.start << 16) | ((slot.count * 2) << 1) | 1
        return va(block_off[slot[1]])

    for bi, children in enumerate(blocks):
        for j, slot in enumerate(children):
            struct.pack_into('<I', data, block_off[bi] + 4 * j, value(slot))
    root_off = append(struct.pack('<I', value(root)))

    npoly = len(polys)
    struct.pack_into('<3f', data, node + 0x08, *lo)
    struct.pack_into('<3f', data, node + 0x14, *hi)
    struct.pack_into('<3f', data, node + 0x20, *centre)
    struct.pack_into('<3f', data, node + 0x2C, *centre)
    radius = max(math.sqrt(sum((q[c] - centre[c]) ** 2 for c in range(3)))
                 for q in verts) * 1.0002
    struct.pack_into('<f', data, node + 0x38, radius)
    struct.pack_into('<f', data, node + 0x3C,
                     radius + math.sqrt(sum(q * q for q in centre)))
    struct.pack_into('<I', data, node + 0x4C, len(verts))
    struct.pack_into('<I', data, node + 0x50, npoly)
    struct.pack_into('<I', data, node + 0x68, va(off_vert))
    struct.pack_into('<I', data, node + 0x6C, va(off_poly))
    payload = b.off(b.u32(node + 0x94))
    struct.pack_into('<I', data, payload + 0x00, va(root_off))
    struct.pack_into('<I', data, payload + 0x08, npoly)
    struct.pack_into('<I', data, payload + 0x0C, len(verts))
    struct.pack_into('<I', data, payload + 0x10, len(index_list))
    struct.pack_into('<I', data, payload + 0x14, va(off_idx))
    struct.pack_into('<f', data, payload + 0x18, cell)
    struct.pack_into('<3f', data, payload + 0x1C, *clo)
    struct.pack_into('<3f', data, payload + 0x28, *chi)
    struct.pack_into('<I', data, 0x80, len(verts))
    struct.pack_into('<I', data, 0x84, npoly)

    while len(data) % 16:
        data.append(0xCD)
    struct.pack_into('<I', data, 0x0C, len(data) - 128)
    open(a.out, 'wb').write(bytes(data))

    check = MB.Bound(a.out)
    cn = check.trees[9]
    cp = check.off(check.u32(cn + 0x94))
    expected = [
        ('root vertices', check.verts_total, len(verts)),
        ('root polygons', check.polys_total, npoly),
        ('node vertices', check.u32(cn + 0x4C), len(verts)),
        ('node polygons', check.u32(cn + 0x50), npoly),
        ('payload polygons', check.u32(cp + 0x08), npoly),
        ('payload vertices', check.u32(cp + 0x0C), len(verts)),
        ('payload refs', check.u32(cp + 0x10), len(index_list)),
    ]
    errors = ['%s: %d != %d' % item for item in expected if item[1] != item[2]]
    _ok, bad_areas = MB.check(check, 9)
    if bad_areas:
        errors.append('%d areas disagree' % bad_areas)
    actual_materials = check.material_indices(cn)
    expected_materials = [p[4] for p in polys]
    if actual_materials != expected_materials:
        errors.append('material indices disagree')
    invalid_materials = [m for m in actual_materials if m >= len(material_ids)]
    if invalid_materials:
        errors.append('%d material indices outside the palette' % len(invalid_materials))
    if errors:
        sys.exit('invalid output: ' + '; '.join(errors))

    report = {
        'source_global': {
            'vertices': len(verts0), 'polygons_input': len(polys0),
            'polygons_kept': len(polys) - added,
            'materials': len(mats), 'degenerate': base_degenerates,
            'physical_materials': [
                {'source': source, 'physical': physical,
                 'local_index': index, 'polygons': count}
                for (source, physical, index), count
                in sorted(source_material_counts.items())],
        },
        'instance_asphalt': {
            'material_tokens': material_tokens,
            'scoped_materials': list(scoped_materials.values()),
            'placements': asphalt_rows, 'model_types': len(asphalt_models),
            'triangles_added': added, 'duplicates_skipped': duplicate,
            'degenerate_skipped': degenerate, 'downward': downward,
            'materials': asphalt_materials,
        },
        'output': {
            'vertices': len(verts), 'polygons': npoly, 'refs': refs,
            'octree_blocks': len(blocks), 'leaves': len(leaves),
            'depth': background, 'cell': cell, 'aabb_min': lo, 'aabb_max': hi,
            'radius': radius, 'bytes': len(data),
            'material_global_ids': material_ids,
            'material_index_counts': dict(sorted(collections.Counter(
                actual_materials).items())),
            'road_material_index': road_material_index,
        },
        'test_point': [a.test_x, a.test_z],
        'test_point_hits': point_hits,
        'validated': not errors and bad_areas == 0,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.audit:
        with open(a.audit, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write('\n')


if __name__ == '__main__':
    main()
