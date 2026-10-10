#!/usr/bin/env python3
"""Route legs of converted MC2 races that run into an MC2 obstacle prop.

    python mc2_race_obstacles.py --city losangeles --props PROPS_PREFIX [--radius 3] [--fix]

The MC3 AI aims at a straight line between two waypoints on a leg with no
shortcut (`edge a b p -1`): on a straight road that is the centre line. MC2
puts invisible collision boxes there - `*_prop_dummybound_*` (FixedObject +
Obstacle; LA's l_prop_dummybound_01x is a 1.4 x 6 x 2.7 m box under each pillar
of an overpass: Los Angeles 44, Paris 92, Tokyo 111) - which MC2's AI drove
around on its lanes and MC3's runs into: in LA's Moses Checkpoint One the
opponents stopped ~13 s at the pillars of (700, -332).

For every race of the city it lists the legs (waypoint ids, the aib roads
whose centres are at aib 0x4107 +20/+24) whose segment passes within --radius
metres of an obstacle prop placement (FixedObject + Obstacle in the city's
.prop, read with `mc2_props.py --out-path PROPS_PREFIX`). With --fix it runs
mc2_race_shortcut.add_leg on each of them: a shortcut that follows the aib's
directed car lanes, which sit off the centre line. Legs whose lane path the
shortcut tool refuses (no lane, detour too long, already a shortcut) are
listed for a look. MC3_RACES / MC3_CITIES as in mc2_race_shortcut.py.
"""
import argparse
import csv
import glob
import json
import os
import re

import mc2_race_shortcut as SC

CRLF, LF = chr(13) + chr(10), chr(10)


def obstacles(props_prefix, only='dummybound'):
    kinds = {r['name'] for r in csv.DictReader(open(props_prefix + '_kinds.tsv'), delimiter='\t')
             if r['Obstacle'] == '1' and r['FixedObject'] == '1' and only in r['name']}
    out = []
    for p in json.load(open(props_prefix + '_placement.json')):
        if p['kind'] in kinds:
            t = p['matrix'][3]
            out.append((t[0], t[2], p['kind']))
    return out


def legs(text):
    """[(opponent, [(from road, to road)] of the edges with no shortcut)]."""
    result = []
    for k, block in enumerate(re.findall(r'Route\s*\{(.*?)\n\t\}', text, re.S)):
        wps = [int(m) for m in re.findall(r'Waypoint\s+(-?\d+)', block)]
        pairs = []
        for a, b, sc in re.findall(r'edge\s+(-?\d+)\s+(-?\d+)\s+[-\d.]+\s+(-?\d+)', block):
            a, b, sc = int(a), int(b), int(sc)
            if sc == -1 and 0 <= a < len(wps) and 0 <= b < len(wps):
                pairs.append((wps[a], wps[b]))
        result.append((k, pairs))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', required=True)
    ap.add_argument('--props', required=True, help='prefix written by mc2_props.py --out-path')
    ap.add_argument('--radius', type=float, default=8.0,
                    help='metres from the lane path the AI drives (the road, not the straight line)')
    ap.add_argument('--kinds', default='dummybound', help='obstacle prop types whose name holds this')
    ap.add_argument('--fix', action='store_true')
    a = ap.parse_args()
    nodes, rails, roads = SC.load_aib(a.city)
    obs = obstacles(a.props, a.kinds)
    races = sorted(glob.glob('%s/%s/**/*.rac' % (SC.RACES, a.city), recursive=True))
    total = fixed = refused = 0
    path_cache = {}
    for path in races:
        text = open(path, 'rb').read().decode('latin1').replace(CRLF, LF)
        hits = set()
        for _, pairs in legs(text):
            for src, dst in pairs:
                if not (0 <= src < len(roads) and 0 <= dst < len(roads)):
                    continue
                if (src, dst) not in path_cache:
                    pts = SC.lane_path(nodes, rails, roads[src], roads[dst], 0.0) or []
                    pts = [(roads[src][0], 0.0, roads[src][1])] + pts + [(roads[dst][0], 0.0, roads[dst][1])]
                    hit = None
                    for ox, oz, kind in obs:
                        if any(SC.seg_dist((ox, oz), p, q) < a.radius for p, q in zip(pts, pts[1:])):
                            hit = (kind, round(ox), round(oz))
                            break
                    path_cache[(src, dst)] = hit
                if path_cache[(src, dst)]:
                    hits.add((src, dst) + path_cache[(src, dst)])
        if not hits:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        done = set()
        for src, dst, kind, ox, oz in sorted(hits):
            total += 1
            line = '%-48s leg %4d -> %-4d %s at (%d, %d)' % (name, src, dst, kind, ox, oz)
            if a.fix and (src, dst) not in done:
                done.add((src, dst))
                raw = open(path, 'rb').read().decode('latin1')
                nl = CRLF if CRLF in raw else LF
                try:
                    new, info = SC.add_leg(raw.replace(CRLF, LF), (nodes, rails, roads), src, dst)
                except ValueError as e:
                    refused += 1
                    print(line + '  REFUSED: %s' % e)
                    continue
                open(path, 'wb').write(new.replace(LF, nl).encode('latin1'))
                fixed += 1
                line += '  shortcut %d (%d nodes, %.0f m)' % (info['index'], info['nodes'], info['length'])
            print(line)
    print('%d races, %d legs into an obstacle%s' % (len(races), total,
          ', %d shortcuts added, %d refused' % (fixed, refused) if a.fix else ''))


if __name__ == '__main__':
    main()
