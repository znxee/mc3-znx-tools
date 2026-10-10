#!/usr/bin/env python3
"""Give the opponents of converted MC2 races MC3's own AI tuning.

    python mc2_race_ai_tune.py RACES_DIR_OR_FILE... [--dry-run] [--report]

The MC3 AI is MC2's descendant (same .rac vocabulary, same personalities),
but the races were tuned for MC2's cars: across the LA/Paris races 151 of 517
opponents hold ThrottleLimit 0.80-0.95 and half carry a rubber band with
MC2 numbers (MinThrottle 0.45-0.70, BehindMaxThrottle 0.80-0.95, MinSpeed
50-80). MC3's retail races: ThrottleLimit 1.0 on 3793 of 3801 opponents,
and the rubber band, when used (23%), is always
    MinDist 25  MaxDist 75  MinThrottle 0.6  BehindMaxThrottle 1.0
    MinSpeed 70  BehindMinDist 10  BehindMaxDist 25
With the MC2 numbers an opponent 80 m ahead of the player drops to 45% throttle
and the pack crawls.

Inside every Opponent block this sets ThrottleLimit to 1.0 and, where a
RubberBand block exists, its seven values to the retail ones above. Nothing
else changes (routes, shortcuts added later, personalities, cars). Files are
rewritten in place with their own line endings; a file left as it was is not
touched. --report prints what each file had.
"""
import argparse
import os
import re

RUBBER_BAND = {
    'RubberBandMinDist': '25.000000',
    'RubberBandMaxDist': '75.000000',
    'RubberBandMinThrottle': '0.600000',
    'RubberBandBehindMaxThrottle': '1.000000',
    'RubberBandMinSpeed': '70.000000',
    'RubberBandBehindMinDist': '10.000000',
    'RubberBandBehindMaxDist': '25.000000',
}
FIELD = re.compile(r'^(\s*)(\w+)(\s+)(-?[\d.]+)(\s*)$')


def tune(text):
    nl = '\r\n' if '\r\n' in text else '\n'
    out, changes = [], []
    depth = 0
    in_opp = in_band = False
    opp = -1
    for line in text.split(nl):
        s = line.strip()
        if s.startswith('Opponent') and depth == 0:
            in_opp = True
            opp += 1
        elif in_opp and s == 'RubberBand':
            in_band = True
        if s == '{':
            depth += 1
        elif s == '}':
            depth -= 1
            if in_band and depth == 1:
                in_band = False
            if depth == 0:
                in_opp = False
        m = FIELD.match(line)
        if in_opp and m:
            key, value = m.group(2), m.group(4)
            new = None
            if key == 'ThrottleLimit' and depth == 1:
                new = '1.000000'
            elif in_band and key in RUBBER_BAND:
                new = RUBBER_BAND[key]
            if new is not None and float(value) != float(new):
                changes.append((opp, key, value, new))
                line = m.group(1) + key + m.group(3) + new + m.group(5)
        out.append(line)
    return nl.join(out), changes


def files(paths):
    for p in paths:
        if os.path.isdir(p):
            for d, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith('.rac'):
                        yield os.path.join(d, f)
        else:
            yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    nfiles = nchanged = nfields = 0
    for path in files(a.paths):
        nfiles += 1
        raw = open(path, 'rb').read().decode('latin1')
        new, changes = tune(raw)
        if not changes:
            continue
        nchanged += 1
        nfields += len(changes)
        if a.report:
            print(path)
            for opp, key, old, value in changes:
                print('   opponent %d  %-28s %s -> %s' % (opp, key, old, value))
        if not a.dry_run:
            open(path, 'wb').write(new.encode('latin1'))
    print('%d races, %d changed, %d values%s' % (nfiles, nchanged, nfields,
                                                 ' (dry run)' if a.dry_run else ''))


if __name__ == '__main__':
    main()
