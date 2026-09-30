#!/usr/bin/env python3
"""Top-down map of one spot of a MC2 city for AI debugging: collision floor and
walls of ONE level, the aib lane lines, the race's shortcut nodes, and where the
AI cars went and what they aimed at (from a mc3_ai_trace log).

    python mc3_ai_map.py --city paris --race paris_ian_checkpoint_one --center 313 -259 --radius 80 \\
        --log out/ia_ian1/paris_ian_checkpoint_one/pcsx2.log --y -6.8 --out out/ian1_map.png

Colours: floor of the level = dark grey, other levels = not drawn; walls
(polygons with |normal y| < 0.5 whose vertical span reaches the level) = red;
aib car lanes = green, pedestrian rails = blue; race shortcut nodes = yellow,
joined in order, numbered; car samples = cyan dots (numbered by car), the point
each was aiming at = white line from the car. North (+z) is up; 1 px = 0.25 m.
"""
import argparse
import glob
import os
import re
import struct
import sys

from PIL import Image, ImageDraw

import mc2_rsc as R
import mc3_ai_trace as TR

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
RACES = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/tune/race')
PX = 0.5


def collision(rsc, box, level, band):
    d = rsc.data
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    grid = [i for i, (off, fcc, size) in enumerate(rsc.entries)
            if fcc == 'BND0' and d[off + 4] == 0x0B][0]
    off = rsc.entries[grid][0]
    cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)
    floors, walls = [], []
    for c in range(cells):
        node = off + first + 0x90 * c
        if u(node + 0x84) == 0xFFFFFFFF:
            continue
        nv, npol = u(node + 0x4C), u(node + 0x50)
        vo, po = off + u(node + 0x74), off + u(node + 0x78)
        verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
        for k in range(npol):
            ny = struct.unpack_from('<f', d, po + 32 * k + 4)[0]
            idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
            pts = [verts[j] for j in idx[:3 if idx[3] == 0 else 4]]
            if max(p[0] for p in pts) < box[0] or min(p[0] for p in pts) > box[1] or \
                    max(p[2] for p in pts) < box[2] or min(p[2] for p in pts) > box[3]:
                continue
            ymin, ymax = min(p[1] for p in pts), max(p[1] for p in pts)
            if ny >= 0.3:
                ym = sum(p[1] for p in pts) / len(pts)
                if level - band <= ym <= level + band:
                    floors.append((ym, pts))
            elif abs(ny) < 0.5 and ymin <= level + 2.5 and ymax >= level - 0.5:
                walls.append(pts)
    return floors, walls


def aib(city):
    folder = 'losangeles' if city == 'losangeles' else city
    path = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/city/%s/%s_city.aib') % (folder, folder)
    d = open(path, 'rb').read()
    p, nodes, rails = 4, [], []
    while p + 6 <= len(d):
        tag, ln = struct.unpack_from('<HI', d, p)
        pl = d[p + 6:p + 6 + ln]
        if tag == 0x4101:
            nodes = [struct.unpack_from('<3f', pl, 28 * i) for i in range(ln // 28)]
        if tag == 0x4109:
            rails = [pl[20 * i:20 * i + 20] for i in range(ln // 20)]
        p += 6 + ln
    out = []
    for r in rails:
        a, b = struct.unpack_from('<HH', r, 0)
        if a < len(nodes) and b < len(nodes):
            out.append((nodes[a], nodes[b], bool(r[18] & 8)))
    return out


def shortcuts(race):
    hits = glob.glob(os.path.join(RACES, '**', race + '.rac'), recursive=True)
    if not hits:
        return []
    t = open(hits[0], encoding='latin1').read()
    out = []
    for m in re.finditer(r'Shortcut\s*\{\s*SourceIntersection (\d+)\s*DestinationIntersection (\d+)\s*numShortcutNodes (\d+)([^}]*)\}', t):
        nodes = []
        for line in m.group(4).strip().splitlines():
            w = line.split()
            if len(w) >= 3:
                nodes.append((float(w[0]), float(w[1]), float(w[2])))
        out.append((int(m.group(1)), int(m.group(2)), nodes))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='paris')
    ap.add_argument('--race', required=True)
    ap.add_argument('--center', nargs=2, type=float, required=True, metavar=('X', 'Z'))
    ap.add_argument('--radius', type=float, default=80.0)
    ap.add_argument('--y', type=float, default=0.0, help='height of the level to show')
    ap.add_argument('--band', type=float, default=3.0, help='floors within +-band of --y are drawn (coloured by height)')
    ap.add_argument('--log', help='pcsx2.log with RAA markers')
    ap.add_argument('--from', dest='t0', type=float, default=0.0)
    ap.add_argument('--to', dest='t1', type=float, default=1e9)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    cx, cz, rad = a.center[0], a.center[1], a.radius
    box = (cx - rad, cx + rad, cz - rad, cz + rad)
    size = int(2 * rad / PX)
    img = Image.new('RGB', (size, size), (12, 14, 12))
    dr = ImageDraw.Draw(img)
    to_px = lambda x, z: ((x - box[0]) / PX, (box[3] - z) / PX)

    rsc = R.Rsc(os.path.join(MC2, 'resource', a.city, a.city + '.rsc'))
    floors, walls = collision(rsc, box, a.y, a.band)
    for ym, pts in sorted(floors, key=lambda f: f[0]):
        # low = dark blue-grey, high = light grey-brown: two stacked roads read apart
        k = max(0.0, min(1.0, (ym - (a.y - a.band)) / (2 * a.band)))
        dr.polygon([to_px(p[0], p[2]) for p in pts],
                   fill=(int(30 + 60 * k), int(38 + 50 * k), int(70 - 10 * k)))
    for pts in walls:
        dr.polygon([to_px(p[0], p[2]) for p in pts], fill=(200, 40, 40), outline=(255, 90, 90))
    for na, nb, ped in aib(a.city):
        if max(na[0], nb[0]) < box[0] or min(na[0], nb[0]) > box[1] or max(na[2], nb[2]) < box[2] or min(na[2], nb[2]) > box[3]:
            continue
        if abs((na[1] + nb[1]) / 2 - a.y) > a.band + 2:
            continue
        pa, pb = to_px(na[0], na[2]), to_px(nb[0], nb[2])
        col = (60, 110, 255) if ped else (60, 200, 90)
        dr.line([pa, pb], fill=col, width=1)
        # direction tick: a small dot two thirds of the way to the end of the rail
        tx, ty = pa[0] + (pb[0] - pa[0]) * 0.75, pa[1] + (pb[1] - pa[1]) * 0.75
        dr.ellipse([tx - 2, ty - 2, tx + 2, ty + 2], fill=col)
    for sc, (src, dst, nodes) in enumerate(shortcuts(a.race)):
        pts = [to_px(n[0], n[2]) for n in nodes]
        if not any(0 <= x < size and 0 <= y < size for x, y in pts):
            continue
        dr.line(pts, fill=(230, 210, 40), width=1)
        for k, (x, y) in enumerate(pts):
            dr.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(230, 210, 40))
            dr.text((x + 4, y - 4), '%d.%d' % (src, k), fill=(230, 210, 40))
    if a.log:
        cars = TR.parse(a.log)
        for car, rows in cars.items():
            for r in rows:
                if 'x' not in r or not a.t0 <= r['t'] <= a.t1:
                    continue
                if abs(r.get('y', a.y) - a.y) > a.band + 3:
                    continue
                x, y = to_px(r['x'], r['z'])
                if not (0 <= x < size and 0 <= y < size):
                    continue
                if 'tx' in r:
                    tx, ty = to_px(r['tx'], r['tz'])
                    dr.line([(x, y), (tx, ty)], fill=(235, 235, 235), width=1)
                dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(40, 220, 230))
                dr.text((x + 4, y - 4), '%d@%.0f' % (car, r['t']), fill=(40, 220, 230))
    img.save(a.out)
    print('%s: %d floor / %d wall polygons at y %.1f +-%.1f, map %.0f..%.0f x %.0f..%.0f -> %s' % (
        a.race, len(floors), len(walls), a.y, a.band, box[0], box[1], box[2], box[3], a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
