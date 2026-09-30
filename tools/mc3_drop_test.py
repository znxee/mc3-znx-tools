#!/usr/bin/env python3
"""Collision drop test of Los Angeles in the running game: the player's car is
dropped on a list of points and where it ends up tells whether the collision
the game built (losangeles_bound, from the MC2 .rsc) holds it there.

    python mc2_ground_coverage.py --out out/coverage        # makes drop_points.txt
    python mc3_drop_test.py --points out/coverage/drop_points.txt --out out/drop --max 600

Uses the renderer's [boot] mc2g = F (city_mc2_rsc_draw_test): the car is put
1.5 m above each point and left F frames. Result per point:
    ok      ended within 2.5 m of the ground height (a car rests ~0.5 m up)
    fell    ended more than 3 m below it (went through)
    moved   ended more than 10 m away sideways (respawned / slid off)
    high    ended more than 2.5 m above it (something drawn-less under it?)
Writes drop.tsv, drop_map.png (north up; green ok, red fell, blue moved,
yellow high) and prints the summary. The .ini
and the points file on the HostFS are restored at the end.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

import mc3_race_tour as T

HOSTFS = os.environ.get('MC3_HOSTFS', 'MC3HostFS')
DROP = os.path.join(HOSTFS, 'mc2_droptest.txt')
HERE = os.path.dirname(os.path.abspath(__file__))


def s32(h):
    v = int(h, 16)
    return v - (1 << 32) if v >> 31 else v


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--points', required=True,
                    help='x ground_y z [vx vz] per line (with a velocity: wall test)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--city', default='losangeles', help='registered MC3 city name')
    ap.add_argument('--stride', type=int, default=1,
                    help='test every Nth input point (useful for a city-wide sample)')
    ap.add_argument('--max', type=int, default=0, help='first N points only')
    ap.add_argument('--frames', type=int, default=30, help='frames per drop')
    ap.add_argument('--wall-pass', type=float, default=6.5,
                    help='with a velocity: metres along the throw that count as passed through')
    ap.add_argument('--cell', type=float, default=2.0)
    a = ap.parse_args()

    pts = []
    for line in open(a.points):
        if line.strip() and not line.startswith('#'):
            v = [float(x) for x in line.split()[:5]]
            pts.append(tuple(v + [0.0] * (5 - len(v))))
    if a.stride < 1:
        ap.error('--stride must be at least 1')
    pts = pts[::a.stride]
    if a.max:
        pts = pts[:a.max]
    os.makedirs(a.out, exist_ok=True)
    ini = open(T.INI, 'rb').read()
    old = open(DROP, 'rb').read() if os.path.exists(DROP) else None
    run = 60 + len(pts) * a.frames / 30.0 * 1.15 + 20
    try:
        open(DROP, 'w').write('\n'.join('%.2f %.2f %.2f %.2f %.2f' % p for p in pts) + '\n')
        keys = [('rscw', 1), ('city', a.city), ('time', 'midnight'), ('weather', 'clear'),
                ('racetype', 'roam'), ('car', 'vp_corvettez06_03'), ('nofe', 1),
                ('mc2q', 0), ('mc2g', a.frames)]
        open(T.INI, 'wb').write(T.boot_section(ini, keys))
        at = ['%g' % t for t in range(60, int(run) + 1, 120)] + ['%g' % run]
        print('%d points, ~%.0f s' % (len(pts), run), flush=True)
        subprocess.run([sys.executable, os.path.join(HERE, 'mc3_pcsx2_shots.py'), '--at'] + at +
                       ['--out', a.out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=run + 240)
    finally:
        open(T.INI, 'wb').write(ini)
        if old is None:
            os.remove(DROP)
        else:
            open(DROP, 'wb').write(old)
    if os.path.exists(T.LOG):
        shutil.copy2(T.LOG, os.path.join(a.out, 'pcsx2.log'))
    log = open(os.path.join(a.out, 'pcsx2.log'), encoding='latin1').read()
    ys = {int(i, 16): s32(y) / 16.0 for i, y in re.findall(r'RDRP ([0-9A-F]{8}) ([0-9A-F]{8})', log)}
    xz = [(s32(x), s32(z)) for x, z in re.findall(r'RDRX ([0-9A-F]{8}) ([0-9A-F]{8})', log)]
    idx = [int(i, 16) for i, _ in re.findall(r'RDRP ([0-9A-F]{8}) ([0-9A-F]{8})', log)]
    fx = dict(zip(idx, xz))
    rows, count = [], {}
    for i, (x, y, z, vx, vz) in enumerate(pts):
        if i not in ys:
            continue
        ey, (ex, ez) = ys[i], fx.get(i, (x, z))
        d = ((ex - x) ** 2 + (ez - z) ** 2) ** 0.5
        if vx or vz:
            # thrown at a wall: how far it got along the throw (0 = start)
            sp = (vx * vx + vz * vz) ** 0.5
            along = ((ex - x) * vx + (ez - z) * vz) / sp
            r = 'passed' if along > a.wall_pass else 'blocked'
            count[r] = count.get(r, 0) + 1
            rows.append((i, r, x, y, z, ey, ex, ez))
            continue
        if d > 10:
            r = 'moved'
        elif ey < y - 3:
            r = 'fell'
        elif ey > y + 2.5:
            r = 'high'
        else:
            r = 'ok'
        count[r] = count.get(r, 0) + 1
        rows.append((i, r, x, y, z, ey, ex, ez))
    with open(os.path.join(a.out, 'drop.tsv'), 'w') as fh:
        fh.write('index\tresult\tx\tground_y\tz\tfinal_y\tfinal_x\tfinal_z\n')
        for r in rows:
            fh.write('%d\t%s\t%.1f\t%.1f\t%.1f\t%.1f\t%d\t%d\n' % r)
    print('  tested %d of %d: %s' % (len(rows), len(pts), count))
    for r in [r for r in rows if r[1] not in ('ok', 'blocked')][:25]:
        print('    #%d %-5s at %7.1f %6.1f %7.1f -> y %.1f (x %d z %d)' % r)
    if 'RDRE' not in log:
        print('  RDRE missing: the run ended before the last point')

    from PIL import Image, ImageDraw
    colour = {'ok': (40, 200, 60), 'fell': (240, 30, 30), 'moved': (60, 120, 255),
              'high': (240, 220, 40)}
    xs = [p[0] for p in pts]; zs = [p[2] for p in pts]
    img = Image.new('RGB', (int((max(xs) - min(xs)) / a.cell) + 9,
                            int((max(zs) - min(zs)) / a.cell) + 9), (20, 20, 24))
    dr = ImageDraw.Draw(img)
    colour.update({'blocked': (40, 200, 60), 'passed': (240, 30, 30)})
    for i, r, x, y, z, *_ in sorted(rows, key=lambda r: r[1] not in ('ok', 'blocked')):
        px = (x - min(xs)) / a.cell + 4
        pz = img.size[1] - 5 - (z - min(zs)) / a.cell      # north (+z) up
        rad = 3 if r in ('ok', 'blocked') else 7
        dr.ellipse([px - rad, pz - rad, px + rad, pz + rad], fill=colour[r])
    img.save(os.path.join(a.out, 'drop_map.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
