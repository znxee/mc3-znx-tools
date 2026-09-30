#!/usr/bin/env python3
"""Where a MC2 city's drivable ground is missing, or drawn without texture,
in the geometry the Los Angeles renderer (city_mc2_rsc_draw_test) draws.

    python mc2_ground_coverage.py --out out/coverage
    python mc2_ground_coverage.py --out out/coverage --at=-339,425 --at=-697,325

Reference = the collision's walkable polygons (BND0 of <city>.rsc, normal
y >= 0.3 - the same surfaces losangeles_bound gives the game). For each cell of a
grid (--cell metres) and each collision height there, it looks for a drawn
triangle within --tol metres of that height, among every PMD0 the renderer
draws (baked components + .hood instances, near LOD, flat triangles only).
Each collision sample ends up as:

    ok        drawn with a texture the renderer can use
    white     drawn with texture handle 0 (flat vertex colour, no texture)
    badtex    drawn, but the texture handle is one the renderer rejects
              (not TEX0, no MIP0/PAL0, size/format it cannot upload) - the
              renderer then keeps the PREVIOUS texture: a wrong one
    hole      nothing drawn at that height nor above it: what shows there
              is the background (ground under a building's roof or a deck
              is not counted - nobody sees it)

Writes <out>/coverage.png (grey ok, yellow white, magenta badtex, red hole),
<out>/clusters.tsv (connected areas of each problem, largest first, with the
centre in world x/z and the models drawn nearest) and prints a summary.
--at x,z ... reports what is at given points (e.g. camera positions of dark
screenshots from mc3_race_tour.py).
"""
import argparse
import collections
import math
import os
import struct
import sys

import mc2_rsc as R

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
OK, WHITE, BADTEX, HOLE = 0, 1, 2, 3
NAMES = ('ok', 'white', 'badtex', 'hole')
COLOURS = {OK: (120, 120, 120), WHITE: (235, 210, 40), BADTEX: (220, 40, 220),
           HOLE: (230, 30, 30)}


def texture_state(rsc, handle, cache):
    """Mirror of the renderer's texture_slot/make_slot acceptance."""
    if handle in cache:
        return cache[handle]
    st = BADTEX
    if not handle & 0x3FFF:
        st = WHITE
    else:
        got = rsc.resolve(handle)
        if got is not None and got[0].entries[got[1]][1] == 'TEX0':
            box, i = got
            off = box.entries[i][0]
            w, h = struct.unpack_from('<2H', box.data, off + 0x88)
            hpal, hmip = struct.unpack_from('<2I', box.data, off + 0x90)

            def sub(hh, fcc):
                # bit 14 = the city container, otherwise the shared bank
                b = rsc if hh & 0x4000 else rsc.shared()
                k = (hh & 0x3FFF) - 1
                if not (hh & 0x3FFF) or b is None or not 0 <= k < len(b.entries):
                    return None
                return b.entries[k][0] if b.entries[k][1] == fcc else None, b

            mip, pal = sub(hmip, 'MIP0'), sub(hpal, 'PAL0')
            pow2 = lambda v: v and not v & (v - 1)
            if mip and pal and mip[0] is not None and pal[0] is not None and \
                    8 <= w <= 256 and 8 <= h <= 256 and pow2(w) and pow2(h) and h <= w:
                fmt = mip[1].data[mip[0] + 0x28] & 15
                if fmt in (5, 6):
                    st = OK
    cache[handle] = st
    return st


def cells_of_triangle(a, b, c, cell):
    """(ix, iz, y) for every cell centre inside the xz projection of abc."""
    x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
    z0, z1 = min(a[2], b[2], c[2]), max(a[2], b[2], c[2])
    d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
    if abs(d) < 1e-9:
        return
    for ix in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
        x = (ix + 0.5) * cell
        for iz in range(int(math.floor(z0 / cell)), int(math.floor(z1 / cell)) + 1):
            z = (iz + 0.5) * cell
            l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
            l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
            l3 = 1.0 - l1 - l2
            if l1 >= -1e-6 and l2 >= -1e-6 and l3 >= -1e-6:
                yield ix, iz, l1 * a[1] + l2 * b[1] + l3 * c[1]


def normal_y(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = math.sqrt(nx * nx + ny * ny + nz * nz)
    return abs(ny) / n if n else 0.0


def collision_ground(rsc, cell):
    """{(ix, iz): [heights]} of the walkable collision polygons."""
    d = rsc.data
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    out = collections.defaultdict(set)
    for i, (off, fcc, size) in enumerate(rsc.entries):
        if fcc != 'BND0' or d[off + 4] not in (0x0A, 0x0B):
            continue
        if d[off + 4] == 0x0A:
            nodes = [off]
        else:
            cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)
            nodes = [off + first + 0x90 * c for c in range(cells)
                     if u(off + first + 0x90 * c + 0x84) != 0xFFFFFFFF]
        for node in nodes:
            nv, npol = u(node + 0x4C), u(node + 0x50)
            vo, po = off + u(node + 0x74), off + u(node + 0x78)
            verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
            for k in range(npol):
                if struct.unpack_from('<f', d, po + 32 * k + 4)[0] < 0.3:
                    continue
                idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
                pts = [verts[j] for j in idx[:3 if idx[3] == 0 else 4]]
                tris = [pts[:3]] if len(pts) == 3 else [pts[:3], [pts[0], pts[2], pts[3]]]
                for t in tris:
                    for ix, iz, y in cells_of_triangle(t[0], t[1], t[2], cell):
                        out[(ix, iz)].add(round(y * 2) / 2.0)
    return out


def drawn_ground(rsc, cell):
    """{(ix, iz): [(y, state, label)]} of flat drawn triangles."""
    cache = {}
    out = collections.defaultdict(list)
    for label, batches in R._gather(rsc, None, None, True):
        for b, inst in batches:
            st = texture_state(rsc, b.texture, cache)
            pts = R._place(b, inst)
            for t in b.triangles():
                a, bb, c = pts[t[0]], pts[t[1]], pts[t[2]]
                if normal_y(a, bb, c) < 0.5:
                    continue
                for ix, iz, y in cells_of_triangle(a, bb, c, cell):
                    out[(ix, iz)].append((y, st, label))
    return out, cache


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='losangeles')
    ap.add_argument('--mc2', default=MC2, help="MC2 PS2 assets/ folder")
    ap.add_argument('--textures', default='midnight_clear')
    ap.add_argument('--cell', type=float, default=2.0)
    ap.add_argument('--tol', type=float, default=1.5)
    ap.add_argument('--min-cells', type=int, default=4, help='smallest cluster listed')
    ap.add_argument('--at', action='append', default=[], help='x,z point to report (repeat; use --at=-339,425)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--drop-step', type=float, default=24.0,
                    help='spacing of the drop-test grid over drawn ground (m)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rsc = R.Rsc(os.path.join(a.mc2, 'resource', a.city, a.city + '.rsc'))
    rsc._shared = R.Rsc(os.path.join(a.mc2, 'resource', a.city,
                                     'textures_%s.rsc' % a.textures))
    print('collision ground...', flush=True)
    coll = collision_ground(rsc, a.cell)
    print('drawn geometry...', flush=True)
    drawn, cache = drawn_ground(rsc, a.cell)
    bad = sorted(h for h, s in cache.items() if s == BADTEX)
    print('  texture handles: %d, rejected %d, handle 0 %s' % (
        len(cache), len(bad), WHITE in cache.values()))

    state = {}
    for key, heights in coll.items():
        here = drawn.get(key, ())
        worst = OK
        for h in heights:
            near = [s for y, s, _ in here if abs(y - h) <= a.tol]
            if near:
                s = min(near)                    # best surface drawn there
            elif any(y > h for y, _, _ in here):
                s = OK      # under a roof/deck: collision floor nobody sees
            else:
                s = HOLE
            worst = max(worst, s)
        state[key] = worst
    count = collections.Counter(state.values())
    total = len(state)
    for s in (OK, WHITE, BADTEX, HOLE):
        print('  %-7s %7d cells  %5.1f%%  (%.0f m2)' % (
            NAMES[s], count[s], 100.0 * count[s] / total, count[s] * a.cell * a.cell))

    # image
    from PIL import Image
    xs = [k[0] for k in state]; zs = [k[1] for k in state]
    x0, z0 = min(xs), min(zs)
    img = Image.new('RGB', (max(xs) - x0 + 1, max(zs) - z0 + 1), (0, 0, 0))
    px = img.load()
    for (ix, iz), s in state.items():
        px[ix - x0, img.size[1] - 1 - (iz - z0)] = COLOURS[s]
    img.save(os.path.join(a.out, 'coverage.png'))

    # clusters (4-connected) of each problem state
    rows = []
    seen = set()
    for key, s in state.items():
        if s == OK or key in seen:
            continue
        stack, comp = [key], []
        seen.add(key)
        while stack:
            k = stack.pop()
            comp.append(k)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (k[0] + dx, k[1] + dz)
                if n not in seen and state.get(n) == s:
                    seen.add(n)
                    stack.append(n)
        if len(comp) < a.min_cells:
            continue
        cx = (sum(k[0] for k in comp) / len(comp) + 0.5) * a.cell
        cz = (sum(k[1] for k in comp) / len(comp) + 0.5) * a.cell
        y = min(min(coll[k]) for k in comp)
        labels = collections.Counter(l for k in comp for _, _, l in drawn.get(k, ()))
        rows.append((NAMES[s], len(comp), cx, y, cz,
                     ' '.join(n for n, _ in labels.most_common(3))))
    rows.sort(key=lambda r: -r[1])
    with open(os.path.join(a.out, 'clusters.tsv'), 'w') as fh:
        fh.write('state\tcells\tm2\tx\ty\tz\tmodels_here\n')
        for st, n, x, y, z, lab in rows:
            fh.write('%s\t%d\t%.0f\t%.1f\t%.1f\t%.1f\t%s\n' % (st, n, n * a.cell ** 2, x, y, z, lab))
    print('  %d clusters >= %d cells -> %s' % (len(rows), a.min_cells,
                                               os.path.join(a.out, 'clusters.tsv')))
    for st, n, x, y, z, lab in rows[:15]:
        print('    %-6s %6.0f m2  at %7.1f %6.1f %7.1f  %s' % (st, n * a.cell ** 2, x, y, z, lab))

    # The other way round: drawn flat ground with no collision near it - where
    # a car would fall through. Also writes the drop-test points for the
    # renderer ([boot] mc2g): every no-collision cluster, plus a grid over all
    # drawn ground that has collision, at the collision height.
    # Only the LOWEST drawn surface of a cell (roofs have no collision in MC2
    # either), and only inside the collision's area: some collision in the
    # cell or within 3 cells (the ocean and the scenery past the world's edge
    # are drawn with nothing under them on purpose).
    nocoll = {}
    for key, here in drawn.items():
        heights = coll.get(key, ())
        low = min(y for y, _, _ in here)
        if any(abs(low - h) <= a.tol for h in heights):
            continue
        if any(low < h <= low + 6.0 for h in heights):
            continue            # underside of a deck whose top has collision
        if any(h < low for h in heights):
            continue            # collision below it: a deck over a street
        if not heights and not any((key[0] + dx, key[1] + dz) in coll
                                   for dx in range(-3, 4) for dz in range(-3, 4)):
            continue
        nocoll[key] = low
    print('  drawn ground without collision: %d cells (%.0f m2)' % (
        len(nocoll), len(nocoll) * a.cell * a.cell))
    seen, nrows = set(), []
    for key in nocoll:
        if key in seen:
            continue
        stack, comp = [key], []
        seen.add(key)
        while stack:
            k = stack.pop()
            comp.append(k)
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (k[0] + dx, k[1] + dz)
                if n in nocoll and n not in seen:
                    seen.add(n)
                    stack.append(n)
        if len(comp) < a.min_cells:
            continue
        # the member closest to the centre, so the point is inside the area
        cx = sum(k[0] for k in comp) / len(comp)
        cz = sum(k[1] for k in comp) / len(comp)
        k = min(comp, key=lambda k: (k[0] - cx) ** 2 + (k[1] - cz) ** 2)
        labels = collections.Counter(l for kk in comp for _, _, l in drawn.get(kk, ()))
        nrows.append((len(comp), (k[0] + 0.5) * a.cell, nocoll[k], (k[1] + 0.5) * a.cell,
                      ' '.join(n for n, _ in labels.most_common(3))))
    nrows.sort(key=lambda r: -r[0])
    with open(os.path.join(a.out, 'no_collision.tsv'), 'w') as fh:
        fh.write('cells\tm2\tx\ty\tz\tmodels_here\n')
        for n, x, y, z, lab in nrows:
            fh.write('%d\t%.0f\t%.1f\t%.1f\t%.1f\t%s\n' % (n, n * a.cell ** 2, x, y, z, lab))
    print('  %d no-collision clusters >= %d cells -> no_collision.tsv' % (len(nrows), a.min_cells))
    for n, x, y, z, lab in nrows[:10]:
        print('    %6.0f m2  at %7.1f %6.1f %7.1f  %s' % (n * a.cell ** 2, x, y, z, lab))
    step = max(1, int(round(a.drop_step / a.cell)))
    drops = [(x, y, z, 'nocoll') for n, x, y, z, lab in nrows]
    for (ix, iz), s in sorted(state.items()):
        if s == OK and ix % step == 0 and iz % step == 0 and (ix, iz) in drawn:
            h = [y for y, _, _ in drawn[(ix, iz)] if any(abs(y - c) <= a.tol for c in coll[(ix, iz)])]
            if h:
                drops.append(((ix + 0.5) * a.cell, max(h), (iz + 0.5) * a.cell, 'grid'))
    with open(os.path.join(a.out, 'drop_points.txt'), 'w') as fh:
        fh.write('# x ground_y z  (mc2_ground_coverage.py; for [boot] mc2g)\n')
        for x, y, z, kind in drops:
            fh.write('%.2f %.2f %.2f\n' % (x, y, z))
    print('  %d drop-test points (%d no-collision + grid every %g m) -> drop_points.txt' % (
        len(drops), len(nrows), step * a.cell))

    for p in a.at:
        x, z = (float(v) for v in p.split(','))
        k = (int(math.floor(x / a.cell)), int(math.floor(z / a.cell)))
        near = collections.Counter()
        for dx in range(-5, 6):
            for dz in range(-5, 6):
                s = state.get((k[0] + dx, k[1] + dz))
                if s is not None:
                    near[NAMES[s]] += 1
        print('  at %s: %s' % (p, dict(near) or 'no collision ground within 10 m'))


if __name__ == '__main__':
    sys.exit(main())
