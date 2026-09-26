#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate selection manifest v18 = the 58 rules of v13 + the streets in `comp`.

WHY
===

`build_la_instance_asphalt_collision.py` matches the global material tokens
(`ashphalt`, `asphalt`, `crosswalk`) like this:

    global_match = (row['kind'] == 'inst'
                    and any(token in material_l for token in material_tokens))

The `row['kind'] == 'inst'` is the root cause of the hole. Every street and every
crossing MC2 authored as a COMPONENT (`kind == 'comp'`) never made it into the
collision, however much the material is called `l_roads_ashphalt00_SG`. The
model names give away what was lost: `l_hollywood_int_04_x#geom`,
`l_santamonica_int_14_x#geom`, `l_downtown_int_11_x#geom` - `int` for
intersection - and `l_downtown_rd_01_x#geom`, `l_hollywood_rd_25_x#geom` - `rd`
for road.

Measured: 1,399 upward-facing asphalt/lane triangles in `comp` rows, and NONE of
them duplicates an `inst` triangle. They are floor that simply does not exist
for the physics.

WHY A RULE AND NOT `kind in (inst, comp)`
=========================================

Relaxing `global_match` would be one line, but the builder's global path does
NOT filter orientation: it accepts the triangle with any normal and still marks
the `road` material. In `inst` that works because instanced asphalt is a flat
plate; in `comp` the same material also shows up on the underside, and an
underside accepted as road is exactly the defect this project already documented.

The `scoped` path filters `orientation` and `min_abs_normal_y`. So each
(comp, model, material) pair becomes an explicit, auditable and reproducible
rule - which is the form the brief itself requires.

    python make_comp_road_manifest.py --base <v13 manifest> --out <v18 manifest>
"""

import argparse
import csv
import collections
import json
import math
import os
import sys

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city'))
sys.path.insert(0, TOOLS)
import mc3_modpath as modpath
from build_la_instance_asphalt_collision import load_raw, raw_triangles

TOKENS = ('ashphalt', 'asphalt', 'crosswalk')


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--base', required=True)
    ap.add_argument('--place', default=TOOLS + '/output/mc2_losangeles/losangeles_place.tsv')
    ap.add_argument('--models', default=TOOLS + '/output/mc2_losangeles/model')
    ap.add_argument('--out', required=True)
    ap.add_argument('--min-ny', type=float, default=0.70)
    a = ap.parse_args()

    base = json.load(open(a.base, encoding='utf-8'))
    rules = list(base['rules'])
    have = {(r['kind'], r['model'].lower(), r['material'].lower()) for r in rules}

    with open(a.place, encoding='utf-8') as s:
        rows = list(csv.DictReader((l for l in s if not l.startswith('#')), delimiter='\t'))

    cache = {}

    def model_data(row):
        p = os.path.join(a.models, '%s_%s_%s.mod' % (row['name'], row['lod'], row['part']))
        if p in cache:
            return cache[p]
        if not os.path.exists(p):
            cache[p] = []
            return []
        mesh = load_raw(modpath, p)
        pos = mesh.positions()
        out = []
        for mat in mesh.a(modpath.SLOT_MATERIAL).items:
            tris = []
            for pk in mat.packets:
                for pr in pk.primitives:
                    for ti in raw_triangles(modpath, pr):
                        tris.append(tuple(pos[pk.adjuncts[i][0]] for i in ti))
            out.append((mat.name, tris))
        cache[p] = out
        return out

    found = collections.OrderedDict()
    for row in rows:
        if row['kind'] != 'comp':
            continue
        m = [float(row['m%d%d' % (i, j)]) for i in range(3) for j in range(3)]
        t = tuple(float(row[c]) for c in ('tx', 'ty', 'tz'))
        for name, tris in model_data(row):
            if not any(tok in name.lower() for tok in TOKENS):
                continue
            up = down = 0
            for lt in tris:
                w = tuple((m[0]*p[0] + m[3]*p[1] + m[6]*p[2] + t[0],
                           m[1]*p[0] + m[4]*p[1] + m[7]*p[2] + t[1],
                           m[2]*p[0] + m[5]*p[1] + m[8]*p[2] + t[2]) for p in lt)
                x, y, z = w
                u = [y[i]-x[i] for i in range(3)]
                v = [z[i]-x[i] for i in range(3)]
                n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                L = math.sqrt(sum(q*q for q in n))
                if L < 1e-9:
                    continue
                if n[1]/L >= a.min_ny:
                    up += 1
                elif n[1]/L <= -a.min_ny:
                    down += 1
            key = ('comp', row['name'], name)
            e = found.setdefault(key, dict(up=0, down=0, rows=0))
            e['up'] += up
            e['down'] += down
            e['rows'] += 1

    added = 0
    skipped_no_up = 0
    for (kind, model, material), e in found.items():
        if e['up'] == 0:
            # only an underside: it is a belly, not road. It stays out.
            skipped_no_up += 1
            continue
        if (kind, model.lower(), material.lower()) in have:
            continue
        rules.append({
            'kind': kind, 'model': model, 'material': material,
            'orientation': 'up', 'min_abs_normal_y': a.min_ny,
            'note': 'street/crossing authored as a component; the builder\'s global '
                    'match is restricted to kind==inst and left this without collision',
        })
        added += 1

    out = {
        'description': base.get('description', '') +
        ' | v18: adds the streets and crossings authored as a COMPONENT '
        '(kind=comp), which the global match restricted to kind==inst left with '
        'no collision at all. Only upward faces.',
        'rules': rules,
    }
    with open(a.out, 'w', encoding='utf-8') as s:
        json.dump(out, s, indent=1, ensure_ascii=False)
        s.write('\n')

    print('base rules           : %d' % len(base['rules']))
    print('comp+asphalt pairs   : %d' % len(found))
    print('  no upward face     : %d (dropped: belly)' % skipped_no_up)
    print('rules added          : %d' % added)
    print('total in manifest    : %d' % len(rules))
    print('upward triangles     : %d' % sum(e['up'] for e in found.values()))
    print('written              : %s' % a.out)


if __name__ == '__main__':
    main()
