#!/usr/bin/env python3
"""Opponent speeds from a PCSX2 log of a spectated MC2-city race.

    python mc3_ai_speed.py LOG [--csv OUT]

The city renderer (spectator mode, [boot] mc2s=N) logs every opponent every
2 s: RAAS <index | aiStuck state...> then RAAV <speed*10 << 16 | wanted*10>
<target distance*10> - `speed` is the car's (aiOpponent+232, m/s) and
`wanted` the speed the brain asks for (aiBrain+228). Per opponent this prints
the mean/max of both while moving, and how often the brain asked for less
than the car could do (wanted < 60% of the opponent's own top speed in the
run) - a low `wanted` means the AI chose to go slow (route, corners, road
data), a high `wanted` with a low `speed` points at the car/physics.
"""
import argparse
import re
from collections import defaultdict

LINE = re.compile(r'\[\s*([\d,]+)\]\s+(RAAS|RAAV)\s+([0-9A-F]{8})\s+([0-9A-F]{8})')


def parse(path):
    rows = []
    cur = None
    for line in open(path, 'rb').read().decode('latin1').splitlines():
        m = LINE.search(line)
        if not m:
            continue
        t = float(m.group(1).replace(',', '.'))
        a, b = int(m.group(3), 16), int(m.group(4), 16)
        if m.group(2) == 'RAAS':
            cur = a >> 24
        elif cur is not None:
            speed = (a >> 16) / 10.0
            wanted = (a & 0xFFFF) / 10.0
            rows.append((t, cur, speed, wanted, b / 10.0))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('log')
    ap.add_argument('--csv')
    a = ap.parse_args()
    rows = parse(a.log)
    if a.csv:
        with open(a.csv, 'w') as f:
            f.write('time,opponent,speed,wanted,target_dist\n')
            for r in rows:
                f.write('%.2f,%d,%.1f,%.1f,%.1f\n' % r)
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    print('opp  samples  moving  speed mean/max (m/s)  wanted mean/max  wanted<60%%top  km/h mean')
    for i in sorted(by):
        mv = [r for r in by[i] if r[2] > 1.0 or r[3] > 1.0]
        if not mv:
            print('%3d  %7d  %6d  -' % (i, len(by[i]), 0))
            continue
        sp = [r[2] for r in mv]
        wa = [r[3] for r in mv]
        top = max(sp)
        low = sum(1 for w in wa if w < 0.6 * top)
        print('%3d  %7d  %6d  %6.1f / %5.1f        %6.1f / %5.1f   %4d (%3d%%)    %5.0f'
              % (i, len(by[i]), len(mv), sum(sp) / len(sp), top, sum(wa) / len(wa), max(wa),
                 low, 100 * low // len(mv), 3.6 * sum(sp) / len(sp)))


if __name__ == '__main__':
    main()
