r"""Install a city .pck into the weather variants - and ONLY into them.

WHY THIS EXISTS. I installed a city with a `startswith('losangeles_')` loop that
only excluded `_fog` and `_bnd`, and it OVERWROTE `losangeles_peds.pck` (kind 0x21)
and `losangeles_traffic.pck` (kind 0x3C) with a city file (kind 0x43). The game
opened the peds, found a city, and stayed in ENDLESS LOADING - with the EE alive
at 60 Hz, which made the symptom look like a geometry problem. Filtering by
prefix is fragile; this script filters by the KIND in the header, which is what
the game actually reads.

THE CELL CONTRACT IS CHECKED TOO: cell_count sits at MapRoot+0x268 and has to be
the same in <city>.pck, _props.pck and _peds.pck. A new city with different
extents changes the grid and breaks the contract silently.

    python mc3_install_city.py city.pck --dest <ASSETS/resources/city> --prefix losangeles
"""
import argparse
import os
import struct

CITY_KIND = 0x43

# What each sibling of a city MUST be. Installing by name prefix already
# overwrote peds (0x21), traffic (0x3C) and even the texture .ppf files with a
# city file - and the symptom was endless loading, not an error.
KINDS = {0x43: 'city', 0x04: 'fog', 0x21: 'peds', 0x3C: 'traffic',
         0x09: 'bnd', 0x40: 'props'}


def expected_by_name(f):
    if f.endswith('.ppf'):
        return 'ppf'
    for suf, rot in (('_peds.pck', 'peds'), ('_traffic.pck', 'traffic'),
                     ('_bnd.pck', 'bnd')):
        if f.endswith(suf):
            return rot
    if 'props' in f:
        return 'props'
    return 'fog' if '_fog' in f else 'city'


REQUIRED_SIBLINGS = ('_props.pck', '_peds.pck', '_traffic.pck', '_bnd.pck')


def check_set(dest, prefix):
    """Warn if a REQUIRED sibling of the city does not exist. A whole missing
    props (not just a wrong cell_count) gave endless loading and took me a while
    to find, because I only looked at the files that WERE there."""
    present = os.listdir(dest)
    missing = []
    for suf in REQUIRED_SIBLINGS:
        if suf == '_props.pck':
            # there are 18: 9 weathers x (props, garage_props)
            n = sum(1 for f in present if f.startswith(prefix) and 'props' in f)
            if n < 18:
                missing.append('props (found %d of 18)' % n)
        elif not any(f.startswith(prefix) and f.endswith(suf) for f in present):
            missing.append(suf)
    return missing


def audit(dest, prefix):
    """Check the KIND of each sibling against what the name promises. Returns the errors."""
    errors = []
    print('%-36s %-9s %-11s %s' % ('file', 'kind', 'bytes', 'state'))
    for f in sorted(os.listdir(dest)):
        if not f.startswith(prefix):
            continue
        p = os.path.join(dest, f)
        n = os.path.getsize(p)
        wants = expected_by_name(f)
        if wants == 'ppf':
            good_run = open(p, 'rb').read(4) == b'pf05'
            present = 'ppf' if good_run else 'CORRUPT'
        else:
            t = header(p)[1]
            present = KINDS.get(t, '?%02X' % t)
            good_run = present == wants
        if not good_run:
            errors.append('%s: is %s, should be %s' % (f, present, wants))
        print('%-36s %-9s %-11d %s' % (f, present, n, 'OK' if good_run else 'EXPECTED ' + wants))
    return errors


def header(p):
    with open(p, 'rb') as f:
        d = f.read(16)
    return struct.unpack('<4I', d) if len(d) == 16 else (0, 0, 0, 0)


def city_cells(p):
    """The city cell_count: MapRoot+0x268, which is the grid W*H."""
    with open(p, 'rb') as f:
        d = f.read(0x400)
    return struct.unpack_from('<I', d, 0x80 + 0x268)[0]


def peds_cells(p):
    """Entries of the peds per-cell array. The offset is DIFFERENT - reading the
    city's here gave false alarms. Pointer at 0x2A4, object at 0x280; the count is
    the distance to the next object, as mc3_cellcount.py does."""
    d = open(p, 'rb').read()
    if len(d) <= 0x2B0:
        return None
    base = struct.unpack_from('<I', d, 0)[0]
    start_ = struct.unpack_from('<I', d, 0x2A4)[0] - base + 0x80
    if not (0 < start_ <= len(d)):
        return None
    return (len(d) - start_) // 4


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('city', nargs='?', default='')
    ap.add_argument('--dest', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--apply', dest='apply_', action='store_true')
    ap.add_argument('--audit-only', action='store_true',
                    help='only check the kind of each sibling and exit')
    a = ap.parse_args()

    if a.audit_only:
        errors = audit(a.dest, a.prefix)
        missing = check_set(a.dest, a.prefix)
        for f in missing:
            errors.append('MISSING SIBLING: %s' % f)
        print('')
        print('set %s' % ('CONSISTENT' if not errors else 'HAS PROBLEMS'))
        for e in errors:
            print('   ' + e)
        raise SystemExit(1 if errors else 0)

    _, kind, _, _ = header(a.city)
    if kind != CITY_KIND:
        raise SystemExit('%s has kind %02X, not %02X - not a city'
                         % (a.city, kind, CITY_KIND))
    n_cells = city_cells(a.city)
    print('%s: kind %02X, %d cells' % (os.path.basename(a.city), kind, n_cells))

    targets, others = [], []
    for f in sorted(os.listdir(a.dest)):
        if not f.startswith(a.prefix) or not f.endswith('.pck'):
            continue
        p = os.path.join(a.dest, f)
        t = header(p)[1]
        (targets if t == CITY_KIND else others).append((f, t, p))

    for f, t, p in others:
        marks = ''
        if f.endswith('_peds.pck') or f.endswith('_props.pck'):
            c = peds_cells(p)
            if c is None:
                marks = '  (could not read the count)'
            else:
                marks = '  %d entries %s' % (
                    c, 'OK' if c >= n_cells else '< %d OF THE CITY - run mc3_cellcount.py' % n_cells)
        print('  kept      %-34s kind %02X%s' % (f, t, marks))

    print('  %d city variant(s) to replace' % len(targets))
    if not a.apply_:
        print('')
        print('  DRY RUN - nothing written. Repeat with --apply.')
        return
    data = open(a.city, 'rb').read()
    for f, t, p in targets:
        open(p, 'wb').write(data)
    print('  written to %d file(s)' % len(targets))


if __name__ == '__main__':
    main()
