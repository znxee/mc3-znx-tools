#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate manifest v19 = v18 + the crossing CURBS, as `sidewalk`.

WHAT WAS MISSING
================

After v18 (which closed the streets and crossings authored as components), the
only class of hole still open at drivable height was the corners of the
crossings. Probing the installed v18:

    crossing corner     -> NO COLLISION
    neighbours at 1.5 u -> ROAD at y=0.00 and sidewalk at y=0.15

So the hole is the CURB STRIP between the road and the pavement, exactly where
a car cutting the corner goes. Measured: 1,176 triangles in 24 model|material
pairs, all between y=0.05 and y=0.10, area 3,828.

The materials are `l_shared_concrete03_SG`, `lambert2SG` and
`x:l_shared_concrete03_SG` - generic, which is exactly why they were left out:
turning on `concrete` or `lambert2` globally would pick up walls, ceilings and
decoration too. So each pair becomes an exact rule, with `orientation: up`.

WHY `sidewalk` AND NOT `road`
=============================

Before this manifest the builder marked EVERYTHING it added as `road`, and that
is why the curb was left out of v18 on purpose: a curb step with the `road`
material would give asphalt friction and sound where the game should give
pavement. `build_la_instance_asphalt_collision.py` now accepts
`physical_material` per rule (absent = `road`, as before), and these rules ask
for `sidewalk` - which is what `physical_material_name` would already deduce
from "concrete", and what the neighbouring pavement at y=0.15 uses.

    python make_curb_manifest.py --base <v18 manifest> --holes <v18 measurement> \\
        --out <v19 manifest>
"""

import argparse
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--base', required=True)
    ap.add_argument('--holes', required=True,
                    help='road_adjacent_holes_v18.json, the v18 measurement')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pattern', default='wayint',
                    help='substring that identifies the crossing model')
    ap.add_argument('--max-y', type=float, default=0.5,
                    help='curb only: drop any face above this')
    ap.add_argument('--material', default='sidewalk')
    ap.add_argument('--min-ny', type=float, default=0.70)
    a = ap.parse_args()

    base = json.load(open(a.base, encoding='utf-8'))
    rules = list(base['rules'])
    have = {(r['kind'], r['model'].lower(), r['material'].lower()) for r in rules}

    holes = json.load(open(a.holes, encoding='utf-8'))
    picked, skipped_high, already = [], 0, 0
    for e in holes:
        if e['kind'] != 'inst':
            continue
        if a.pattern not in e['model'].lower():
            continue
        if not e.get('adj'):
            continue
        if e['y_max'] is None or e['y_max'] > a.max_y:
            # above the curb is not curb; it does not go in without its own evidence
            skipped_high += 1
            continue
        key = ('inst', e['model'].lower(), e['material'].lower())
        if key in have:
            already += 1
            continue
        picked.append(e)
        rules.append({
            'kind': 'inst', 'model': e['model'], 'material': e['material'],
            'orientation': 'up', 'min_abs_normal_y': a.min_ny,
            'physical_material': a.material,
            'note': 'crossing curb: strip between the road at y=0.00 and the '
                    'pavement at y=0.15, with no collision at all up to v18',
        })

    out = {
        'description': base.get('description', '') +
        ' | v19: adds the crossing CURBS (%s), physical material %s, '
        'only upward faces and only below y=%.2f.'
        % (a.pattern, a.material, a.max_y),
        'rules': rules,
    }
    with open(a.out, 'w', encoding='utf-8') as s:
        json.dump(out, s, indent=1, ensure_ascii=False)
        s.write('\n')

    print('base rules             : %d' % len(base['rules']))
    print('curb rules             : %d' % len(picked))
    print('  already present      : %d' % already)
    print('  above y=%.2f         : %d (dropped)' % (a.max_y, skipped_high))
    print('total in the manifest : %d' % len(rules))
    print('target triangles       : %d  (area %.0f)'
          % (sum(e['adj'] for e in picked), sum(e['adj_area'] for e in picked)))
    print('physical material      : %s' % a.material)
    print('written                : %s' % a.out)


if __name__ == '__main__':
    main()
