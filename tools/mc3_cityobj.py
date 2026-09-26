"""
mc3_cityobj.py - put the generated city and the MC2 original side by side

The city is wrong on screen and every check on the file passes. That is the
point where a picture beats another audit: the MC2 fork can already dump Los
Angeles as an OBJ (`mc2_rsc.py obj`), so if the generated `.pck` can be dumped
the same way, the difference stops being a description and becomes a
measurement.

The geometry side is not new work: `mc3_view.py` already walks the real graph
(MapRoot -> hood -> component -> data +0x3C -> mesh), decodes the city's planar
VIF packets with the mask handled, and places each mesh through its 3x4. This
only writes what it found as an OBJ and compares two of them.

    python mc3_cityobj.py obj output/modcity_la_v6.pck --out output/la_pck.obj
    python mc3_cityobj.py stats output/la_pck.obj
    python mc3_cityobj.py compare output/la_pck.obj output/mc2_la/losangeles.obj

ONE CAVEAT THAT DECIDES WHETHER A COMPARISON MEANS ANYTHING: `mc3_view.py`
cannot reach every mesh through the graph, and it gives an unreachable one the
position of its nearest placed neighbour so that the viewer does not pile them
on the origin. That is a display convenience and a lie for measurement, so the
groups it marks with a trailing `~` are counted and, by default, LEFT OUT of the
OBJ. `--with-inherited` puts them back, for looking rather than for concluding.
"""

import argparse
import collections
import importlib.util
import io
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, path=None):
    s = importlib.util.spec_from_file_location(
        name, path or os.path.join(_HERE, name + '.py'))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


def write_obj(groups, out_path, with_inherited=False):
    """OBJ in world space, one `o` per mesh, keeping the group names."""
    n_v = n_f = skipped = 0
    with io.open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# mc3_cityobj: %d group(s) of the .pck\n' % len(groups))
        base = 1
        for g in groups:
            name = g.get('name') or ''
            inherited_one = name.rstrip().endswith('~')
            if inherited_one and not with_inherited:
                skipped += 1
                continue
            vs, ts = g['verts'], g['tris']
            if not ts:
                continue
            f.write('o %s\n' % (name.strip() or 'mesh'))
            for v in vs:
                f.write('v %.4f %.4f %.4f\n' % (v[0], v[1], v[2]))
            for t in ts:
                f.write('f %d %d %d\n' % (base + t[0], base + t[1], base + t[2]))
            base += len(vs)
            n_v += len(vs)
            n_f += len(ts)
    return n_v, n_f, skipped


def cmd_obj(a):
    view = _mod('mc3_view')
    groups = view.parse_ps2_city(a.pck, a.limit)
    inherited = sum(1 for g in groups if (g.get('name') or '').rstrip().endswith('~'))
    n_v, n_f, skipped = write_obj(groups, a.out, a.with_inherited)
    print('%s' % os.path.basename(a.pck))
    print('   %d group(s) read, %d with an INHERITED position (%s)'
          % (len(groups), inherited,
             'included' if a.with_inherited else 'left out'))
    print('   %d vertex(es), %d triangle(s) -> %s' % (n_v, n_f, a.out))
    if inherited and not a.with_inherited:
        print('   WARNING: %d group(s) are not reachable through the graph '
              'mc3_view walks.' % inherited)
        print('   Their position would be a guess, so they were left out. If '
              'that number is large,')
        print('   it is a finding in itself: geometry in the file that the draw '
              'path does not reach.')


def read_obj(path):
    """[(x, y, z)] and the face count. No material, no normal."""
    vs, nf = [], 0
    for l in io.open(path, encoding='utf-8', errors='replace'):
        if l.startswith('v '):
            p = l.split()
            vs.append((float(p[1]), float(p[2]), float(p[3])))
        elif l.startswith('f '):
            nf += 1
    return vs, nf


def _box(vs):
    mn = [min(v[i] for v in vs) for i in range(3)]
    mx = [max(v[i] for v in vs) for i in range(3)]
    return mn, mx


def cmd_stats(a):
    vs, nf = read_obj(a.obj)
    if not vs:
        raise SystemExit('%s: no vertex' % a.obj)
    mn, mx = _box(vs)
    print('%s' % os.path.basename(a.obj))
    print('   %d vertex(es), %d face(s)' % (len(vs), nf))
    print('   X %10.1f .. %10.1f   (%.1f)' % (mn[0], mx[0], mx[0] - mn[0]))
    print('   Y %10.1f .. %10.1f   (%.1f)' % (mn[1], mx[1], mx[1] - mn[1]))
    print('   Z %10.1f .. %10.1f   (%.1f)' % (mn[2], mx[2], mx[2] - mn[2]))
    c = [sum(v[i] for v in vs) / len(vs) for i in range(3)]
    print('   centroid (%.1f, %.1f, %.1f)' % tuple(c))


def occupancy(vs, cell, origin, grid):
    """{(cx, cz)} - the game grid cells that have some vertex."""
    ox, oz = origin
    gw, gh = grid
    occ = collections.Counter()
    for x, _y, z in vs:
        cx = int(math.floor(x / cell)) - ox
        cz = int(math.floor(z / cell)) - oz
        if 0 <= cx < gw and 0 <= cz < gh:
            occ[(cx, cz)] += 1
    return occ


def cmd_compare(a):
    va, fa = read_obj(a.obj_a)
    vb, fb = read_obj(a.obj_b)
    if not va or not vb:
        raise SystemExit('one of the two is empty')
    na, nb = os.path.basename(a.obj_a), os.path.basename(a.obj_b)
    print('A = %s   %d vertex(es), %d face(s)' % (na, len(va), fa))
    print('B = %s   %d vertex(es), %d face(s)' % (nb, len(vb), fb))
    print()
    ma, xa = _box(va)
    mb, xb = _box(vb)
    print('%-6s %-28s %-28s %s' % ('axis', 'A', 'B', 'difference'))
    for i, axis in enumerate('XYZ'):
        print('%-6s %10.1f ..%10.1f  %10.1f ..%10.1f   min %+8.1f  max %+8.1f'
              % (axis, ma[i], xa[i], mb[i], xb[i], ma[i] - mb[i], xa[i] - xb[i]))
    print()

    # THE GAME GRID as the unit of comparison, not an invented number: the
    # 40-unit cell is confirmed by three sources (mcCity::s_fCellSize in the
    # ELF, the `dimensions` of MC2's losangeles.lvl, and the invariant measured
    # in the four retail cities), and the origin comes from the .pck itself.
    oa = occupancy(va, a.cell, (a.origin_x, a.origin_z), (a.grid_w, a.grid_h))
    ob = occupancy(vb, a.cell, (a.origin_x, a.origin_z), (a.grid_w, a.grid_h))
    sa, sb = set(oa), set(ob)
    print('occupancy on a %dx%d grid of %g-unit cells, origin (%d, %d):'
          % (a.grid_w, a.grid_h, a.cell, a.origin_x, a.origin_z))
    print('   %5d cell(s) with geometry in A' % len(sa))
    print('   %5d cell(s) with geometry in B' % len(sb))
    print('   %5d in both' % len(sa & sb))
    print('   %5d ONLY in A (geometry MC2 does not have there)' % len(sa - sb))
    print('   %5d ONLY in B (MC2 geometry that vanished in the .pck)' % len(sb - sa))
    if sb:
        print('   coverage: %.1f%% of the B cells have something in A'
              % (100.0 * len(sa & sb) / len(sb)))
    if a.mapping:
        lines = []
        for cz in range(a.grid_h):
            line = []
            for cx in range(a.grid_w):
                k = (cx, cz)
                line.append('#' if k in sa and k in ob else
                             'A' if k in sa else
                             'B' if k in ob else '.')
            lines.append(''.join(line))
        io.open(a.mapping, 'w', encoding='utf-8', newline='\n').write(
            '# . empty   # in both   A only in the pck   B only in MC2\n'
            + '\n'.join(lines) + '\n')
        print('   map -> %s' % a.mapping)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='action')
    o = sub.add_parser('obj', help='export the city of a .pck as OBJ')
    o.add_argument('pck')
    o.add_argument('--out', required=True)
    o.add_argument('--limit', type=int, default=0)
    o.add_argument('--with-inherited', action='store_true',
                   help='include the groups whose position mc3_view guessed')
    s = sub.add_parser('stats', help='box and centroid of an OBJ')
    s.add_argument('obj')
    c = sub.add_parser('compare', help='two OBJ files, on the game grid')
    c.add_argument('obj_a')
    c.add_argument('obj_b')
    c.add_argument('--cell', type=float, default=40.0)
    c.add_argument('--origin-x', type=int, default=-50)
    c.add_argument('--origin-z', type=int, default=-43)
    c.add_argument('--grid-w', type=int, default=91)
    c.add_argument('--grid-h', type=int, default=82)
    c.add_argument('--mapping', help='write a text occupancy map')
    a = ap.parse_args()
    fn = {'obj': cmd_obj, 'stats': cmd_stats, 'compare': cmd_compare}.get(a.action)
    if not fn:
        ap.print_help()
        return 2
    fn(a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
