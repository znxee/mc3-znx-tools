"""
mc3_cityaudit.py - the instance -> kind association, checked one by one

Fork City MODS 2 asked for this in so many words: the geometry of the 729 kinds
is validated and the 8511 records keep their 12 floats, but that does NOT prove
that each `Instance+0x0C` points to the right KIND, nor that the matrix left is
that instance's. A name is no key - the generated city writes `generated_00` in
every ComponentData - so here the key is the CONTENT:

    the 12-float matrix matches the instance to the place.tsv line
    the LOCAL AABB of the kind's mesh, transformed by that matrix, has to
    fall inside the emin/emax the same line declares

If the association is swapped, the transformed box does not fit - a post with a
building's matrix overflows the building's box, and vice versa. It is a test
that does not depend on reading any name and that runs on all 8511, not on a
sample.

The matrix convention is ROW-VECTOR (`world = p * R + T`), verified in the VU
microcode (program 14508803, VU 0x09C0) and in the EE's `Matrix34::Transform4`;
the MC2 fork measured the same on its side, in the `.hood`.

    python mc3_cityaudit.py instances <city.pck> --place la729_place.tsv
    python mc3_cityaudit.py kind <city.pck> --place la729_place.tsv \\
        --name alpha_palm
"""

import argparse
import collections
import importlib.util
import io
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, path=None):
    s = importlib.util.spec_from_file_location(
        name, path or os.path.join(_HERE, name + '.py'))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


def _open(path):
    mc2 = _mod('mc2_to_mc3', os.path.join(_HERE, 'experimental', 'mc2_to_mc3.py'))
    hf = _mod('hf', os.path.join(_HERE, 'mc3_hood_fill.py'))
    return mc2.Pck(path), hf


def read_place(path):
    """The `inst` lines, with matrix and box, in file order."""
    lines, hdr = [], None
    for l in io.open(path, encoding='utf-8', errors='replace'):
        if l.startswith('#'):
            continue
        p = l.rstrip('\n').split('\t')
        if hdr is None:
            hdr = p
            continue
        d = dict(zip(hdr, p))
        if d.get('kind') == 'inst':
            lines.append(d)
    return lines


M12 = ('m00', 'm01', 'm02', 'm10', 'm11', 'm12', 'm20', 'm21', 'm22',
       'tx', 'ty', 'tz')


def _mat(d):
    return tuple(round(float(d[c]), 4) for c in M12)


def _box(d):
    return ([float(d['emin' + a]) for a in 'xyz'],
            [float(d['emax' + a]) for a in 'xyz'])


def _transform(mn, mx, m):
    """The AABB of the 8 corners of the local box passed through `p * R + T`."""
    r = [[m[0], m[1], m[2]], [m[3], m[4], m[5]], [m[6], m[7], m[8]]]
    t = [m[9], m[10], m[11]]
    lo = [1e18] * 3
    hi = [-1e18] * 3
    for i in range(8):
        p = [mn[0] if i & 1 else mx[0],
             mn[1] if i & 2 else mx[1],
             mn[2] if i & 4 else mx[2]]
        for a in range(3):
            v = t[a] + p[0] * r[0][a] + p[1] * r[1][a] + p[2] * r[2][a]
            lo[a] = min(lo[a], v)
            hi[a] = max(hi[a], v)
    return lo, hi


def collect(p, hf):
    """[(offset, type_va, matrix, centre, radius)] of the file's instances."""
    slots, kinds = hf.instance_kinds(p)
    out = []
    for o in slots:
        m = tuple(round(x, 4) for x in struct.unpack_from('<12f', p.data, o + 0x20))
        c = struct.unpack_from('<3f', p.data, o + 0x50)
        radius = struct.unpack_from('<f', p.data, o + 0x18)[0]
        out.append((o, p.U(o + 0x0C), m, c, radius))
    return out, kinds


def _aabb_from_vif(p, mesh):
    """The local box of a mesh, by really DECODING the VIF.

    Uses the same strict `mc3_vifpos` as the viewer and `aabb_local`: the
    sub-batch header picks the VU address of the position and the decoder covers
    V3-16, V3-32 and differential V3-8. A count equal to the descriptor is not
    enough: the old `_vif_batches` picked the first V3, added up to
    301315/301315 and still read attributes as XYZ in 130 models.
    """
    vifpos = _mod('mc3_vifpos')
    ng = struct.unpack_from('<H', p.data, mesh + 8)[0]
    gt = p.F(struct.unpack_from('<I', p.data, mesh + 0x10)[0])
    if not p.inside(gt) or not ng:
        return None
    lo = [1e18] * 3
    hi = [-1e18] * 3
    for g in range(ng):
        va_l, nb, _r = struct.unpack_from('<IHH', p.data, gt + 8 * g)
        items = p.F(va_l)
        if not p.inside(items):
            continue
        for b in range(nb):
            pv, qw, n = struct.unpack_from('<IHH', p.data, items + 8 * b)
            off = p.F(pv)
            if not p.inside(off):
                continue
            load = bytes(p.data[off:off + 16 * qw])
            try:
                pts = vifpos.decode_positions(load)
            except ValueError:
                continue
            if len(pts) != n:
                continue
            for v in pts:
                for k, val in enumerate(v):
                    lo[k] = min(lo[k], val)
                    hi[k] = max(hi[k], val)
    return (lo, hi) if hi[0] > -1e17 else None


def kind_local_box(p, hf, kinds, va, cache):
    if va in cache:
        return cache[va]
    r = None
    info = kinds.get(va)
    if info:
        for mesh in info[2]:
            ab = _aabb_from_vif(p, mesh)
            if ab:
                r = ab
                break
    cache[va] = r
    return r


def cmd_instances(a):
    p, hf = _open(a.pck)
    inst, kinds = collect(p, hf)
    place = read_place(a.place)
    print('%s' % os.path.basename(a.pck))
    print('   %d instance(s) in the file, %d distinct kind(s) reached'
          % (len(inst), len(kinds)))
    print('   %d `inst` line(s) in the place' % len(place))

    by_matrix = collections.defaultdict(list)
    for d in place:
        by_matrix[_mat(d)].append(d)

    matched = unmatched = ambiguous = 0
    fits = overflows = no_box = 0
    ident_ok = bad_ident = 0
    worst_list = []
    byFileKind = collections.Counter()
    byExpectedKind = collections.Counter()
    cache = {}
    for o, va, m, _c, _radius in inst:
        byFileKind[va] += 1
        cands = by_matrix.get(m)
        if not cands:
            unmatched += 1
            continue
        if len(cands) > 1:
            ambiguous += 1
            # A repeated matrix is not a key. Picking cands[0] contaminates the
            # type with an arbitrary model name and fabricates both AABB
            # failures and "mixed types". Only unambiguous rows are judgeable.
            continue
        d = cands[0]
        matched += 1
        byExpectedKind[(va, d['name'], d['lod'], d['part'])] += 1
        local = kind_local_box(p, hf, kinds, va, cache)
        if not local:
            no_box += 1
            continue
        lo, hi = _transform(local[0], local[1], m)
        emin, emax = _box(d)
        slack = max(max(emin[k] - lo[k], lo[k] - emin[k],
                        emax[k] - hi[k], hi[k] - emax[k]) for k in range(3))
        overflowed = any(lo[k] < emin[k] - a.tolerance or hi[k] > emax[k] + a.tolerance
                       for k in range(3))
        is_ident = (abs(m[0] - 1) < 1e-4 and abs(m[4] - 1) < 1e-4
                    and abs(m[8] - 1) < 1e-4 and abs(m[1]) < 1e-4
                    and abs(m[2]) < 1e-4 and abs(m[3]) < 1e-4)
        if overflowed:
            overflows += 1
            bad_ident += is_ident
            if len(worst_list) < 400:
                worst_list.append((slack, d['name'], d['lod'], d['part'], o, va))
        else:
            fits += 1
            ident_ok += is_ident

    print()
    print('MATCHING BY MATRIX (12 floats, 4 decimals)')
    print('   %5d matched UNAMBIGUOUSLY with a place line' % matched)
    print('   %5d without a matching line' % unmatched)
    print('   %5d excluded: more than one candidate line (matrix repeated in the source)'
          % ambiguous)
    print()
    print('FIT OF THE KIND LOCAL BOX INTO THE INSTANCE BOX (tolerance %g)'
          % a.tolerance)
    print('   %5d FIT' % fits)
    print('   %5d OVERFLOW  <- suspicious instance->kind association' % overflows)
    print('   %5d without a readable local AABB in the kind' % no_box)
    if fits + overflows:
        print('   fit rate: %.1f%%' % (100.0 * fits / (fits + overflows)))
    print('   of the identity rotations: %d fit, %d overflow'
          % (ident_ok, bad_ident))
    if worst_list:
        worst_list.sort(reverse=True)
        print()
        print('   worst overflows:')
        print('   %-40s %-4s %-9s %10s %10s' % ('model', 'lod', 'part', 'slack', 'Instance'))
        for f, n, lod, part, o, _va in worst_list[:12]:
            print('   %-40s %-4s %-9s %10.1f   0x%06X' % (n[:40], lod, part, f, o))
        counter_ = collections.Counter(n for _f, n, _l, _p, _o, _v in worst_list)
        print()
        print('   models that overflow most: %s'
              % ', '.join('%s x%d' % (n[:30], c) for n, c in counter_.most_common(5)))

    print()
    print('COUNT PER KIND')
    print('   %d kind(s) referenced by the file instances' % len(byFileKind))
    names_by_kind = collections.defaultdict(set)
    for (va, name, lod, part), _n in byExpectedKind.items():
        names_by_kind[va].add((name, lod, part))
    mixed = {va: s for va, s in names_by_kind.items() if len(s) > 1}
    print('   %d kind(s) receive instances of MORE THAN ONE model <- mixing'
          % len(mixed))
    for va, s in list(mixed.items())[:6]:
        print('      kind %08X: %s' % (va, ', '.join(n for n, _l, _p in list(s)[:4])))


def cmd_kind(a):
    p, hf = _open(a.pck)
    inst, kinds = collect(p, hf)
    place = [d for d in read_place(a.place) if a.name.lower() in d['name'].lower()]
    if not place:
        raise SystemExit('no place line matches %r' % a.name)
    target = {_mat(d) for d in place}
    print('%s   filter %r' % (os.path.basename(a.pck), a.name))
    print('   %d line(s) in the place, %d distinct matrix(es)'
          % (len(place), len(target)))
    found_parts = [(o, va, m, c, r) for o, va, m, c, r in inst if m in target]
    print('   %d instance(s) in the file with one of those matrices' % len(found_parts))
    if not found_parts:
        return
    tv = collections.Counter(va for _o, va, _m, _c, _r in found_parts)
    print('   they point to %d kind(s): %s'
          % (len(tv), ', '.join('%08X x%d' % (v, n) for v, n in tv.most_common(6))))
    xs = [m[9] for _o, _v, m, _c, _r in found_parts]
    zs = [m[11] for _o, _v, m, _c, _r in found_parts]
    ys = [m[10] for _o, _v, m, _c, _r in found_parts]
    print('   translation in the FILE:   X %.1f..%.1f  Y %.1f..%.1f  Z %.1f..%.1f'
          % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))
    px = [float(d['tx']) for d in place]
    pz = [float(d['tz']) for d in place]
    py = [float(d['ty']) for d in place]
    print('   translation in the SOURCE: X %.1f..%.1f  Y %.1f..%.1f  Z %.1f..%.1f'
          % (min(px), max(px), min(py), max(py), min(pz), max(pz)))
    print('   %d distinct XZ translation(s) in the file'
          % len({(m[9], m[11]) for _o, _v, m, _c, _r in found_parts}))
    cache = {}
    for o, va, m, c, r in found_parts[:a.show]:
        local = kind_local_box(p, hf, kinds, va, cache)
        name = kinds.get(va, ('?',))[0]
        print('      Instance 0x%06X  kind %08X (%s)  T=(%.1f, %.1f, %.1f)  '
              'centre=(%.1f, %.1f, %.1f) radius %.1f  local AABB %s'
              % (o, va, name[:14], m[9], m[10], m[11], c[0], c[1], c[2], r,
                 ('%.0f x %.0f x %.0f' % tuple(local[1][k] - local[0][k] for k in range(3)))
                 if local else 'UNREADABLE'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='action')
    i = sub.add_parser('instances', help='audit of the 8511, one by one')
    i.add_argument('pck')
    i.add_argument('--place', required=True)
    i.add_argument('--tolerance', type=float, default=1.0)
    t = sub.add_parser('kind', help='focus on one model by name')
    t.add_argument('pck')
    t.add_argument('--place', required=True)
    t.add_argument('--name', required=True)
    t.add_argument('--show', type=int, default=6)
    a = ap.parse_args()
    fn = {'instances': cmd_instances, 'kind': cmd_kind}.get(a.action)
    if not fn:
        ap.print_help()
        return 2
    fn(a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
