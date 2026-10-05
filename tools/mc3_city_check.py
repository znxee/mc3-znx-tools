#!/usr/bin/env python3
"""Check every city shell of an install against the city convention
(docs/CITY_CONVENTION.md) - what can be checked without booting.

    python mc3_city_check.py "$MC3_HOSTFS"

For each resources/city/<city>_<time>_<weather>.pck (type 0x43):
  - the new[] cookie before the hood array (MapRoot +0x18): mcCity's
    destructor walks that many 20-byte hood entries when the city is unloaded
    whole. 0 (nothing walked) or the hood count (retail) pass; anything else
    walks garbage - Tokyo MC2's first shells had 0xCDCD0008 and the game
    stopped in mcHood::DeleteModels 0x25D418. 1 (Los Angeles, the PSP cities)
    is reported: it destroys hood 0 only and works because that hood is empty;
    so is Paris's 0x80000000, whose x20 wraps to 0 (works, by accident).
  - the nine time/weather shells of a city exist.
Exit code 1 if anything fails.
"""
import argparse
import collections
import glob
import os
import re
import struct
import sys

TIMES = ('dawn', 'dusk', 'midnight')
WEATHERS = ('clear', 'cloudy', 'rainy')


def hood_cookie(path):
    d = open(path, 'rb').read()
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    base, kind = u(0), u(4)
    if kind != 0x43:
        return None
    count, array = u(0x80 + 0x14), u(0x80 + 0x18)
    at = array - base + 0x80
    if not 16 <= at <= len(d):
        return count, None
    return count, u(at - 16)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('hostfs')
    a = ap.parse_args()
    folder = os.path.join(a.hostfs, 'ASSETS', 'resources', 'city')
    pat = re.compile(r'^(.+)_(%s)_(%s)\.pck$' % ('|'.join(TIMES), '|'.join(WEATHERS)))
    cities = collections.defaultdict(dict)
    for f in glob.glob(os.path.join(folder, '*.pck')):
        m = pat.match(os.path.basename(f))
        if m:
            cities[m.group(1)][(m.group(2), m.group(3))] = f
    bad = 0
    for city in sorted(cities):
        shells = cities[city]
        missing = [t + '_' + w for t in TIMES for w in WEATHERS if (t, w) not in shells]
        verdicts = collections.Counter()
        for key, f in shells.items():
            r = hood_cookie(f)
            if r is None:
                verdicts['not a city pck'] += 1
                continue
            n, c = r
            if c is None:
                verdicts['hood array outside the file'] += 1
            elif c == 0 or c == n:
                verdicts['ok'] += 1
            elif c == 1:
                verdicts['cookie 1 (hood 0 only)'] += 1
            elif (20 * c) & 0xFFFFFFFF == 0:
                verdicts['cookie %#x (x20 wraps to 0)' % c] += 1
            else:
                verdicts['BAD cookie %#x for %d hoods' % (c, n)] += 1
        fails = [v for v in verdicts if v.startswith('BAD') or v.startswith('hood array')]
        status = 'FAIL' if fails or missing else ('warn' if any(v.startswith('cookie ') for v in verdicts) else 'ok')
        if status == 'FAIL':
            bad += 1
        print('%-12s %-4s %s%s' % (city, status, ', '.join('%s x%d' % kv for kv in sorted(verdicts.items())),
                                   ('; missing ' + ', '.join(missing)) if missing else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
