#!/usr/bin/env python3
"""Pillar feet of an MC2 city: the ones MC2 left without a collision box, and
a collision box for every foot.

    python mc2_pillar_legs.py --city tokyo         --out   ASSETS/resources/city/mc2_pillars_tokyo.txt         --solid ASSETS/resources/city/mc2_solid_tokyo.txt

MC2 gives a standing pillar its collision through an invisible prop,
`*_prop_dummybound_*` (a 2.1 x 7 x 2.4 m box, FixedObject), placed by hand -
and not under every pillar: in Tokyo 20 of the 62 railway_pylon01 frames
have none, nor do railway_pylon04/05, roadpylon_01x, ped_pillar_01, the city
hall pillars or the dietcolumn. For every instance whose type name holds
pylon / pillar / column / colmn / support this finds the feet of its LOD 0
geometry (vertices below 1 m in model space, grouped within 2.5 m).

--out: the feet with no dummybound within --near metres, one "x y z" line
each. The renderer adds a dummybound there when the props load
(pillar_feet), so the car collides and the AI steers around it like around
MC2's own. Tokyo 412 feet, Los Angeles 183, Paris none.

--solid: one box per foot ("x0 y0 z0 x1 y1 z1": the foot's own footprint
plus 0.1 m, at least 0.8 m wide, ground to 5 m) for the city bound
(losangeles_bound / city_original_bounds), which used the instance's .hood
box instead - and a railway pylon's box holds its arm: its walls closed the
road under the arm (Tokyo -649, 951, every opponent of the first race stuck).
Tokyo 614, Los Angeles 308, Paris 14.

Both files are read through datAssetManager as $/resources/city/<name>.txt
(ASSETS/resources/city/ on HostFS, ASSETS.DAT from disc).
MC2_PS2_ASSETS = the MC2 PS2 assets folder (resource/<city>/, city/<city>/).
"""
import argparse
import math
import os
import re

import mc2_rsc as R
import mc2_props as P

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
KEYS = re.compile(r'pylon|pillar|column|colmn|support', re.I)


def feet(rsc, model, boxes=False):
    """Feet (x, z) in model space; with boxes=True (x0, z0, x1, z1) per foot."""
    low = [p for p in R.model_points(rsc, model, 0) if p[1] < 1.0]
    groups = []
    for p in low:
        for g in groups:
            if abs(g[0] - p[0]) < 2.5 and abs(g[1] - p[2]) < 2.5:
                g[2].append(p)
                break
        else:
            groups.append([p[0], p[2], [p]])
    if boxes:
        return [(min(q[0] for q in g[2]), min(q[2] for q in g[2]), max(q[0] for q in g[2]), max(q[2] for q in g[2]))
                for g in groups]
    return [(sum(q[0] for q in g[2]) / len(g[2]), sum(q[2] for q in g[2]) / len(g[2])) for g in groups]


def solid_boxes(rsc, insts, models, height=5.0, margin=0.1, min_half=0.4):
    """World boxes (x0 y0 z0 x1 y1 z1) around every foot of every pillar-like
    instance, for the city bound (city_original_bounds): the .hood box of a
    railway pylon holds its arm too, and walls built from it closed the road
    under the arm (Tokyo -649, 951: every opponent of the first race stuck)."""
    cache, out = {}, []
    for inst in insts:
        t = inst.type.lower()
        if not KEYS.search(t) or t not in models:
            continue
        if t not in cache:
            cache[t] = feet(rsc, models[t], boxes=True)
        for x0, z0, x1, z1 in cache[t]:
            cs = [inst.apply((x, 0.0, z)) for x in (x0, x1) for z in (z0, z1)]
            lo = [min(c[k] for c in cs) - margin for k in (0, 2)]
            hi = [max(c[k] for c in cs) + margin for k in (0, 2)]
            for k in range(2):
                if hi[k] - lo[k] < 2 * min_half:
                    mid = (hi[k] + lo[k]) / 2
                    lo[k], hi[k] = mid - min_half, mid + min_half
            y = inst.apply((0.0, 0.0, 0.0))[1]
            out.append((lo[0], y - 0.2, lo[1], hi[0], y + height, hi[1]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', required=True, help='MC2 city: losangeles, paris, tokyo')
    ap.add_argument('--out')
    ap.add_argument('--near', type=float, default=3.0, help='a dummybound this close covers a foot')
    ap.add_argument('--solid', help='also write a box per foot of every pillar (city/mc2_solid_<city>.txt)')
    a = ap.parse_args()
    rsc = R.Rsc(os.path.join(MC2, 'resource', a.city, a.city + '.rsc'))
    comps, insts = R.read_hoods(os.path.join(MC2, 'city', a.city))
    models = R.models_by_name(rsc)
    _, _, placed = P.read_prop(os.path.join(MC2, 'city', a.city, a.city + '.prop'))
    boxes = [(m[3][0], m[3][2]) for name, m, kind in placed if 'dummybound' in name.lower()]
    cache, out, per_type = {}, [], {}
    for inst in insts:
        t = inst.type.lower()
        if not KEYS.search(t) or t not in models:
            continue
        if t not in cache:
            cache[t] = feet(rsc, models[t])
        for fx, fz in cache[t]:
            w = inst.apply((fx, 0.0, fz))
            if any(math.hypot(w[0] - x, w[2] - z) < a.near for x, z in boxes):
                continue
            out.append(w)
            per_type[t] = per_type.get(t, 0) + 1
    for t, n in sorted(per_type.items()):
        print('%-40s %4d feet' % (t, n))
    print('%d feet without a dummybound (%d dummybounds in the city)' % (len(out), len(boxes)))
    if a.solid:
        sb = solid_boxes(rsc, insts, models)
        with open(a.solid, 'w', newline='\n') as f:
            f.write('# mc2_pillar_legs.py --city %s --solid: x0 y0 z0 x1 y1 z1 per pillar foot\n' % a.city)
            for b in sb:
                f.write('%.2f %.2f %.2f %.2f %.2f %.2f\n' % b)
        print('%d solid foot boxes' % len(sb))
    if a.out:
        with open(a.out, 'w', newline='\n') as f:
            f.write('# mc2_pillar_legs.py --city %s: x y z of pillar feet MC2 left without collision\n' % a.city)
            for w in out:
                f.write('%.2f %.2f %.2f\n' % (w[0], w[1], w[2]))


if __name__ == '__main__':
    main()
