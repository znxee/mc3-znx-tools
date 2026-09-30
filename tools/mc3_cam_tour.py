#!/usr/bin/env python3
"""Photograph a list of places of Los Angeles in one boot, with the renderer's
camera tour ([boot] mc2t, city_mc2_rsc_draw_test).

    python mc3_cam_tour.py --out out/camtour --clusters out/coverage/clusters.tsv --top 12
    python mc3_cam_tour.py --out out/camtour --at=-339,0,425 --at=538,0,-512

Each place is looked at from straight above (--height metres) - or from
--oblique metres south of it, 45 degrees down - for --seconds. The points go to
host0:/mc2_camtour.txt, the [boot] section boots straight into Los Angeles roam
(race_nofe), one screenshot is taken in the middle of every slot and named
after the point it shows (matched through the RTOU markers of the log, not
by assuming when the race started). The .ini and camtour file are restored at
the end, also on error or Ctrl+C.
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time

import mc3_race_tour as T

HOSTFS = os.environ.get('MC3_HOSTFS', 'MC3HostFS')
TOUR = os.path.join(HOSTFS, 'mc2_camtour.txt')
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--out', required=True)
    ap.add_argument('--clusters', help='clusters.tsv of mc2_ground_coverage.py')
    ap.add_argument('--state', default='hole', help='cluster state to visit')
    ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--at', action='append', default=[], help='x,y,z (use --at=-339,0,425)')
    ap.add_argument('--view', action='append', default=[],
                    help='eye and target ex,ey,ez,tx,ty,tz (use --view=-493,8.8,892,-493,6.3,900), e.g. from mc3_pins.py --camtour')
    ap.add_argument('--height', type=float, default=120.0)
    ap.add_argument('--oblique', type=float, default=0.0,
                    help='look from this many metres south, 45 degrees down')
    ap.add_argument('--seconds', type=int, default=10)
    ap.add_argument('--first', type=float, default=60.0,
                    help='seconds after launch before the first screenshot')
    ap.add_argument('--time', default='midnight')
    ap.add_argument('--weather', default='clear')
    ap.add_argument('--city', default='losangeles', help='registered MC3 city name (losangeles = LA, paris)')
    ap.add_argument('--boot', action='append', default=[],
                    help='extra [boot] key=value (e.g. --boot mc2p=0 for no CPVS)')
    a = ap.parse_args()

    points = []
    if a.clusters:
        with open(a.clusters) as fh:
            for row in csv.DictReader(fh, delimiter='\t'):
                if row['state'] == a.state:
                    points.append(('%s_%s' % (row['state'], row['m2']),
                                   float(row['x']), float(row['y']), float(row['z'])))
        points = points[:a.top]
    for p in a.at:
        x, y, z = (float(v) for v in p.split(','))
        points.append(('at', x, y, z))
    views = [[float(v) for v in p.split(',')] for p in a.view]
    if not points and not views:
        sys.exit('no points')
    lines = ['# mc3_cam_tour.py: eye xyz, target xyz']
    for v in views:
        lines.append('%.2f %.2f %.2f %.2f %.2f %.2f' % tuple(v))
    for _, x, y, z in points:
        if a.oblique:
            lines.append('%.2f %.2f %.2f %.2f %.2f %.2f' % (x, y + a.oblique, z - a.oblique, x, y, z))
        else:
            lines.append('%.2f %.2f %.2f %.2f %.2f %.2f' % (x, y + a.height, z, x, y, z))
    points = [('view', v[3], v[4], v[5]) for v in views] + points

    os.makedirs(a.out, exist_ok=True)
    ini = open(T.INI, 'rb').read()
    old_tour = open(TOUR, 'rb').read() if os.path.exists(TOUR) else None
    try:
        open(TOUR, 'w').write('\n'.join(lines) + '\n')
        # the city's Arcade cruise: without rnam the boot keeps the first roam race,
        # a copy of tokyo's cruise_race01 (7 opponents at tokyo spots, density
        # 25/25) - no traffic or pedestrians like the cruise has
        extra = [tuple(kv.split('=', 1)) for kv in a.boot]
        cruise = {'losangeles': 'lamauro_arcade_cruise', 'paris': 'paris_arcade_cruise'}.get(a.city)
        if cruise and not any(k == 'rnam' for k, _ in extra):
            extra.insert(0, ('rnam', cruise))
        keys = [('rscw', 1), ('city', a.city), ('time', a.time), ('weather', a.weather),
                ('racetype', 'roam'), ('car', 'vp_corvettez06_03'), ('nofe', 1),
                ('mc2t', a.seconds)] + extra
        open(T.INI, 'wb').write(T.boot_section(ini, keys))
        # a shot every half slot: enough to have one well inside each slot
        span = a.seconds * len(points) * 1.3 + 30
        at, t = [], a.first
        while t <= a.first + span:
            at.append('%g' % t)
            t += a.seconds / 2.0
        print('%d points, %d screenshots, ~%.0f s' % (len(points), len(at), a.first + span), flush=True)
        subprocess.run([sys.executable, os.path.join(HERE, 'mc3_pcsx2_shots.py'), '--at'] + at +
                       ['--out', a.out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=a.first + span + 180)
    finally:
        open(T.INI, 'wb').write(ini)
        if old_tour is None:
            os.remove(TOUR)
        else:
            open(TOUR, 'wb').write(old_tour)

    if os.path.exists(T.LOG):
        shutil.copy2(T.LOG, os.path.join(a.out, 'pcsx2.log'))
    log = open(os.path.join(a.out, 'pcsx2.log'), encoding='latin1').read()
    switches = [(float(t.replace(',', '.')), int(i, 16))
                for t, i in re.findall(r'\[\s*([\d,]+)\] RTOU ([0-9A-F]{8})', log)]
    if not switches:
        print('no RTOU in the log - did the tour run? (RTOE/RTOL markers)')
        return 1
    # keep, per point, the shot closest to the middle of its first slot
    best = {}
    for fn in sorted(os.listdir(a.out)):
        m = re.match(r't(\d+)\.png$', fn)
        if not m:
            continue
        t = float(m.group(1))
        cur = [s for s in switches if s[0] <= t - 1.0]
        if not cur:
            continue
        start, idx = cur[-1]
        nxt = [s[0] for s in switches if s[0] > start]
        end = nxt[0] if nxt else start + a.seconds
        if t > end - 1.0:
            continue
        score = abs(t - (start + end) / 2)
        if idx not in best or score < best[idx][0]:
            best[idx] = (score, fn)
    kept = []
    for idx, (_, fn) in sorted(best.items()):
        if idx >= len(points):
            continue
        name, x, y, z = points[idx]
        dst = '%02d_%s_%d_%d.png' % (idx, name, round(x), round(z))
        shutil.copy2(os.path.join(a.out, fn), os.path.join(a.out, dst))
        kept.append(os.path.join(a.out, dst))
        print('  %s  <- %s' % (dst, fn))
    T.sheet(kept, os.path.join(a.out, 'sheet.png'))
    missing = [i for i in range(len(points)) if i not in best]
    if missing:
        print('  no screenshot for points %s' % missing)
    return 0


if __name__ == '__main__':
    sys.exit(main())
