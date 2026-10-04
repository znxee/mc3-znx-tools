#!/usr/bin/env python3
"""Which rectangle of the world a MC2 HUD map (assets/texture/hud_map_<x>.tex)
really covers - fitted against the city's own road collision.

    python mc2_map_fit.py --city losangeles --tex hud_map_la.tex --out out/mapfit
    python mc2_map_fit.py --city paris --tex hud_map_paris.tex --out out/mapfit

MC3 draws the map as a quad from the city's extents (min x, max x, min z,
max z of <city>.lvl; hudMap::DrawMap: u = (x - min x)/span x, v = (max z - z)/
span z, i.e. row 0 of the image is the top = +z). If the artist painted the
image for another rectangle the picture does not fall on the world (icons,
which use the same extents, are still right). This finds the rectangle: the
road pixels of the image (bright green/olive lines) are correlated, blurred,
with the rasterised road polygons of the otgrid BND0 of <city>.rsc, by
coordinate descent on the four numbers. Prints the extents of the .lvl, the
fitted ones, and the correlation for both, and writes <out>/<city>_fit.png
(image with the roads of the world over it, before and after).
"""
import argparse
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

import mc2_rsc as R
import mc3_tex

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
ROAD_WORDS = ('road', 'cobble', 'alwaysdry', 'bridge', 'freeway', 'asphalt')
SCALE = 2.0                       # metres per pixel of the world raster


def lvl_extents(city, mc2=MC2):
    import re
    path = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc2/%s/city/%s.lvl') % (city, city)
    if not os.path.exists(path):        # cities never installed there (tokyo)
        path = os.path.join(mc2, 'city', city, city + '.lvl')
    t = open(path).read()
    lo = [float(v) for v in re.search(r'extents_min\s*\{\s*([^}]*)\}', t).group(1).split()]
    hi = [float(v) for v in re.search(r'extents_max\s*\{\s*([^}]*)\}', t).group(1).split()]
    return [lo[0], hi[0], lo[2], hi[2]]


def road_polys(rsc, words=ROAD_WORDS):
    d = rsc.data
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    grid = [i for i, (off, fcc, size) in enumerate(rsc.entries)
            if fcc == 'BND0' and d[off + 4] == 0x0B][0]
    off = rsc.entries[grid][0]
    cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)

    def pmt(h):
        o = rsc.entries[(h & 0x3FFF) - 1][0]
        return d[o + 8:o + 48].split(b'\0')[0].decode('ascii', 'replace').lower()

    out = []
    for c in range(cells):
        node = off + first + 0x90 * c
        if u(node + 0x84) == 0xFFFFFFFF:
            continue
        nv, npol = u(node + 0x4C), u(node + 0x50)
        vo, po = off + u(node + 0x74), off + u(node + 0x78)
        mats = [pmt(u(off + u(node + 0x80) + 4 * k)) for k in range(u(node + 0x84))]
        ok = [any(w in m for w in words) and 'sidewalk' not in m for m in mats]
        verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
        for k in range(npol):
            if struct.unpack_from('<f', d, po + 32 * k + 4)[0] < 0.3:
                continue
            m = d[po + 32 * k + 12]
            if m >= len(ok) or not ok[m]:
                continue
            idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
            out.append([(verts[j][0], verts[j][2]) for j in idx[:3 if idx[3] == 0 else 4]])
    return out


def aib_lines(city, mc2):
    """Car-lane segments (x0, z0, x1, z1) of <city>.aib: rail record = u16 node a,
    u16 node b, ...; flags at +18 (bit 3 = pedestrian rail); node = x y z at +0."""
    d = open(os.path.join(mc2, 'city', city, city + '.aib'), 'rb').read()
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
        if r[18] & 8 or a >= len(nodes) or b >= len(nodes):
            continue
        out.append((nodes[a][0], nodes[a][2], nodes[b][0], nodes[b][2]))
    return out


def line_raster(lines, ext, width=3):
    x0, x1, z0, z1 = ext
    w, h = int((x1 - x0) / SCALE) + 1, int((z1 - z0) / SCALE) + 1
    img = Image.new('L', (w, h), 0)
    dr = ImageDraw.Draw(img)
    for ax, az, bx, bz in lines:
        dr.line([((ax - x0) / SCALE, (z1 - az) / SCALE), ((bx - x0) / SCALE, (z1 - bz) / SCALE)], fill=255, width=width)
    return np.asarray(img, dtype=np.float32) / 255.0


def world_raster(polys, ext):
    x0, x1, z0, z1 = ext
    w, h = int((x1 - x0) / SCALE) + 1, int((z1 - z0) / SCALE) + 1
    img = Image.new('L', (w, h), 0)
    dr = ImageDraw.Draw(img)
    for p in polys:
        dr.polygon([((x - x0) / SCALE, (z1 - z) / SCALE) for x, z in p], fill=255)
    return np.asarray(img, dtype=np.float32) / 255.0


def blur(a, n):
    k = np.ones(2 * n + 1, dtype=np.float32) / (2 * n + 1)
    a = np.apply_along_axis(lambda r: np.convolve(r, k, mode='same'), 1, a)
    return np.apply_along_axis(lambda c: np.convolve(c, k, mode='same'), 0, a)


def texture_mask(path):
    t = mc3_tex.tex_to_image(mc3_tex.read_tex(path))
    img = Image.frombytes('RGBA', (t.width, t.height), bytes(t.to_rgba()))
    a = np.asarray(img).astype(np.int32)
    road = (a[..., 3] > 128) & (a[..., 1] >= 120)          # bright green / olive lines
    return img, road.astype(np.float32)


class Fit:
    def __init__(self, world, world_ext, tex_road, blur_px=5):
        self.w = blur(world, 1)
        self.wx = world_ext
        self.t = blur(tex_road, blur_px)
        self.h, self.wd = tex_road.shape
        v, u = np.mgrid[0:self.h, 0:self.wd]
        self.u = (u + 0.5) / self.wd
        self.v = (v + 0.5) / self.h
        tv = self.t - self.t.mean()
        self.tv = tv / (np.sqrt((tv * tv).sum()) + 1e-9)

    def sample(self, e):
        a, b, c, d = e
        x = a + self.u * (b - a)
        z = d - self.v * (d - c)
        ix = ((x - self.wx[0]) / SCALE).astype(int)
        iz = ((self.wx[3] - z) / SCALE).astype(int)
        ok = (ix >= 0) & (ix < self.w.shape[1]) & (iz >= 0) & (iz < self.w.shape[0])
        out = np.zeros_like(self.u)
        out[ok] = self.w[iz[ok], ix[ok]]
        return out

    def score(self, e):
        s = self.sample(e)
        s = s - s.mean()
        return float((s * self.tv).sum() / (np.sqrt((s * s).sum()) + 1e-9))


def optimise(fit, start, span=200.0):
    best, bs = list(start), fit.score(start)
    step = span / 2
    while step > 0.5:
        improved = True
        while improved:
            improved = False
            for i in range(4):
                for sgn in (-1, 1):
                    cand = list(best)
                    cand[i] += sgn * step
                    if cand[1] <= cand[0] or cand[3] <= cand[2] or abs(cand[i] - start[i]) > span:
                        continue
                    s = fit.score(cand)
                    if s > bs + 1e-6:
                        best, bs, improved = cand, s, True
        step /= 2
    return best, bs


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', required=True, help='MC2 folder name: losangeles, paris')
    ap.add_argument('--tex', required=True, help='hud_map_la.tex, hud_map_paris.tex')
    ap.add_argument('--mc2', default=MC2)
    ap.add_argument('--source', choices=('collision', 'aib'), default='aib',
                    help='roads to fit against: the aib lane lines (default) or the road collision polygons')
    ap.add_argument('--span', type=float, default=200.0, help='max distance of each fitted edge from the .lvl one (m)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rsc = R.Rsc(os.path.join(a.mc2, 'resource', a.city, a.city + '.rsc'))
    ext = lvl_extents(a.city, a.mc2)
    big = [ext[0] - 1500, ext[1] + 1500, ext[2] - 1500, ext[3] + 1500]
    if a.source == 'aib':
        lines = aib_lines(a.city, a.mc2)
        print('%d lane segments, .lvl extents x %.0f..%.0f z %.0f..%.0f' % (len(lines), ext[0], ext[1], ext[2], ext[3]))
        world = line_raster(lines, big)
    else:
        polys = road_polys(rsc)
        print('%d road polygons, .lvl extents x %.0f..%.0f z %.0f..%.0f' % (len(polys), ext[0], ext[1], ext[2], ext[3]))
        world = world_raster(polys, big)
    img, road = texture_mask(os.path.join(a.mc2, 'texture', a.tex))
    print('image %dx%d, road pixels %d' % (img.size[0], img.size[1], int(road.sum())))
    fit = Fit(world, big, road)
    s0 = fit.score(ext)
    best, s1 = optimise(fit, ext, a.span)
    print('correlation with the .lvl extents: %.3f' % s0)
    print('fitted extents: x %.1f..%.1f  z %.1f..%.1f   correlation %.3f' % (best[0], best[1], best[2], best[3], s1))
    print('  vs .lvl: min x %+.1f max x %+.1f min z %+.1f max z %+.1f  (span x %.1f -> %.1f, span z %.1f -> %.1f)' % (
        best[0] - ext[0], best[1] - ext[1], best[2] - ext[2], best[3] - ext[3],
        ext[1] - ext[0], best[1] - best[0], ext[3] - ext[2], best[3] - best[2]))

    # overlay: the image, the world's roads in red over it, before and after
    def over(e):
        s = fit.sample(e)
        rgb = np.asarray(img.convert('RGB')).astype(np.float32)
        red = np.zeros_like(rgb); red[..., 0] = 255
        m = (s > 0.5)[..., None]
        return Image.fromarray(np.where(m, 0.5 * rgb + 0.5 * red, rgb).astype(np.uint8)).resize((1024, 512), Image.NEAREST)
    o0, o1 = over(ext), over(best)
    canvas = Image.new('RGB', (1024, 1034), (0, 0, 0))
    canvas.paste(o0, (0, 0)); canvas.paste(o1, (0, 522))
    canvas.save(os.path.join(a.out, '%s_fit.png' % a.city))
    print('overlay (top: .lvl extents, bottom: fitted) -> %s' % os.path.join(a.out, '%s_fit.png' % a.city))
    print('EXTENTS %s %.2f %.2f %.2f %.2f' % (a.city, best[0], best[1], best[2], best[3]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
