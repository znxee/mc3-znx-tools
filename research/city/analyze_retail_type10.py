#!/usr/bin/env python3
"""Measure how the type10 quadtree relates to retail type9 collision."""

import argparse
import math
import os
import struct
import sys


def qv(v, scale=1000):
    return tuple(round(x * scale) for x in v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tools', required=True)
    ap.add_argument('pcks', nargs='+')
    args = ap.parse_args()
    sys.path.insert(0, args.tools)
    import mc3_bound

    for path in args.pcks:
        b = mc3_bound.Bound(path)
        n9, n10 = b.trees[9], b.trees[10]
        v9, p9 = b.geometry(n9)
        v10, p10 = b.geometry(n10)
        exact9 = {qv(v) for v in v9}
        used10 = {i for _n, _a, idx, _nb in p10 for i in idx}
        exact_vertices = sum(qv(v10[i]) in exact9 for i in used10)
        lo, hi = b.aabb(n10)
        pay = b.off(b.u32(n10 + 0x78))
        grid_lo, grid_hi, cell = mc3_bound.grid(b, n10, 10)
        leaves = b.leaves(n10, 10)
        print('\n%s' % os.path.basename(path))
        print('  type9 %d v / %d p; type10 %d v / %d p; used type10 vertices %d' %
              (len(v9), len(p9), len(v10), len(p10), len(used10)))
        print('  exact type10-used vertices also in type9: %d/%d' %
              (exact_vertices, len(used10)))
        print('  type10 AABB', lo, hi)
        print('  node radii +38/+3c:', b.f32(n10 + 0x38), b.f32(n10 + 0x3C))
        print('  payload owner=%08X poly=%d vert=%d refs=%d cell=%g' %
              (b.u32(pay + 4), b.u32(pay + 8), b.u32(pay + 0x0C),
               b.u32(pay + 0x10), cell))
        print('  grid', tuple(grid_lo), tuple(grid_hi),
              'leaves', len(leaves), 'sum', sum(c for _s, c, _o in leaves))
        ys = [v[1] for v in v10]
        print('  vertex Y: min=%g max=%g unique(.001)=%d' %
              (min(ys), max(ys), len({round(y, 3) for y in ys})))
        areas = [p[1] for p in p10]
        nys = [p[0][1] for p in p10]
        print('  poly area min=%g median=%g max=%g total=%g; ny<0=%d |ny|>.7=%d' %
              (min(areas), sorted(areas)[len(areas)//2], max(areas), sum(areas),
               sum(y < -0.01 for y in nys), sum(abs(y) > .7 for y in nys)))
        print('  materials', b.materials(n10))


if __name__ == '__main__':
    main()
