#!/usr/bin/env python3
"""Per-opponent timeline of a race log made with the spectator renderer
(city_mc2_rsc_draw_test, [boot] mc2s): position, the point the AI steers at,
its speed and the speed it asks for, and the aiStuck state.

    python mc3_ai_trace.py out/ia_ian1/paris_ian_checkpoint_one/pcsx2.log
    python mc3_ai_trace.py LOG --car 3 --from 60 --to 100     # one car, a time window
    python mc3_ai_trace.py LOG --stalls                       # only where a car stood still

Markers (every 2 s, every opponent): RAAS <car<<24 | stuck state<<16 | stuck
count<<8 | really stuck> - RAAP <x> <z> - RAAU <up.y*100> <height*16> -
RAAT <car<<24 | target x (12 bit) <<12 | target z (12 bit)> <brain target x,z
(16 bit each)> - RAAV <speed*10 <<16 | wanted*10> <target distance*10> - RAAG <car<<24 | road id
of the path point it heads for> <that road's centre x<<16 | z>.
Coordinates are whole metres, signed.
"""
import argparse
import collections
import math
import re
import sys

LINE = re.compile(r'\[\s*([\d,]+)\] (RAA[SPUTVG]) ([0-9A-F]{8}) ([0-9A-F]{8})')


def s32(v):
    return v - (1 << 32) if v >> 31 else v


def s16(v):
    return v - (1 << 16) if v >> 15 else v


def s12(v):
    return v - (1 << 12) if v >> 11 else v


def parse(path):
    """{car: [dict(t, x, z, ...)]} in time order."""
    cars = collections.defaultdict(list)
    cur = {}
    for m in LINE.finditer(open(path, encoding='latin1').read()):
        t = float(m.group(1).replace(',', '.'))
        kind, a, b = m.group(2), int(m.group(3), 16), int(m.group(4), 16)
        if kind == 'RAAS':
            car = a >> 24
            cur = {'t': t, 'car': car, 'state': (a >> 16) & 255, 'count': (a >> 8) & 255, 'really': a & 255}
            cars[car].append(cur)
        elif not cur:
            continue
        elif kind == 'RAAP':
            cur['x'], cur['z'] = s32(a), s32(b)
        elif kind == 'RAAU':
            cur['up'], cur['y'] = s32(a) / 100.0, s32(b) / 16.0
        elif kind == 'RAAT':
            cur['tx'], cur['tz'] = s12((a >> 12) & 0xFFF), s12(a & 0xFFF)
            cur['bx'], cur['bz'] = s16(b >> 16), s16(b & 0xFFFF)
        elif kind == 'RAAG':
            cur['road'] = a & 0xFFFF
            cur['rx'], cur['rz'] = s16(b >> 16), s16(b & 0xFFFF)
        elif kind == 'RAAV':
            cur['speed'], cur['wanted'] = s16(a >> 16) / 10.0, s16(a & 0xFFFF) / 10.0
            cur['dist'] = s32(b) / 10.0
    return cars


def stalls(rows, seconds=6.0, radius=3.0):
    """[(t0, t1, x, z)] where a car stayed within `radius` m for `seconds`."""
    out, i = [], 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and 'x' in rows[j + 1] and 'x' in rows[i] and \
                math.hypot(rows[j + 1]['x'] - rows[i]['x'], rows[j + 1]['z'] - rows[i]['z']) <= radius:
            j += 1
        if j > i and rows[j]['t'] - rows[i]['t'] >= seconds:
            out.append((rows[i]['t'], rows[j]['t'], rows[i].get('x'), rows[i].get('z')))
        i = j + 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('log')
    ap.add_argument('--car', type=int, help='opponent index')
    ap.add_argument('--from', dest='t0', type=float, default=0.0)
    ap.add_argument('--to', dest='t1', type=float, default=1e9)
    ap.add_argument('--stalls', action='store_true', help='list the stalls only')
    a = ap.parse_args()
    cars = parse(a.log)
    for car in sorted(cars):
        if a.car is not None and car != a.car:
            continue
        rows = [r for r in cars[car] if a.t0 <= r['t'] <= a.t1]
        st = stalls(rows)
        print('car %d: %d samples, %d stalls of 6 s+, aiStuck count max %d' % (
            car, len(rows), len(st), max([r['count'] for r in rows] or [0])))
        for t0, t1, x, z in st:
            print('    stalled %.0f-%.0f s at (%s, %s)' % (t0, t1, x, z))
        if a.stalls:
            continue
        for r in rows:
            print('  %6.1f  (%5s,%5s) y%5.1f  target (%5s,%5s) d%5.1f  speed %5.1f  road %s (%s,%s)  stuck %d/%d%s' % (
                r['t'], r.get('x'), r.get('z'), r.get('y', 0), r.get('tx'), r.get('tz'), r.get('dist', 0),
                r.get('speed', 0), r.get('road'), r.get('rx'), r.get('rz'), r['state'], r['count'],
                ' REALLY' if r['really'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
