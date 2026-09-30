#!/usr/bin/env python3
"""Run races of a city one after another with the camera on the AI opponents
and photograph them - a sweep for map problems (holes, missing geometry or
textures, cars falling through) along every route the AI drives.

    python mc3_race_tour.py --out out/tour            # every MC2 race
    python mc3_race_tour.py --out out/tour --races arcade_circuit_one arcade_circuit_two

For each race it writes the [boot] section of mc3boot.ini (race_nofe boots
straight into it: rnam, racetype, time and weather from the city's .locinf,
and city_mc2_rsc_draw_test's spectator camera `mc2s`), boots PCSX2, takes a
screenshot every --every seconds, and saves per race: the PNGs, a contact
sheet (sheet.png), the PCSX2 log and a summary line (cars found, exceptions,
TLB misses beyond the known ones). The .ini is copied first and restored at the
end, also on error or Ctrl+C. Races without opponents (roam) are skipped.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

INI = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc3boot.ini')
RACE_DIR = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/tune/race')
LOG = os.path.join(os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2')), 'logs/cli_test.txt')
HERE = os.path.dirname(os.path.abspath(__file__))


def races_from_locinf(city):
    """[(path, type, time, weather)] from <city>.locinf, in file order."""
    text = open(os.path.join(RACE_DIR, city + '.locinf'), encoding='latin1').read()
    out = []
    for m in re.finditer(r'(\S+)\s*\{\s*Time\s+(\S+)\s+Weather\s+(\S+)\s+RaceType\s+(\S+)', text):
        out.append((m.group(1), m.group(4), m.group(2), m.group(3)))
    return out


def boot_section(ini_bytes, keys):
    """Replace the [boot] section with `keys` (ordered). Keeps line endings."""
    nl = b'\r\n' if b'\r\n' in ini_bytes else b'\n'
    i = ini_bytes.index(b'[boot]')
    j = ini_bytes.find(b'\n[', i + 1)
    tail = ini_bytes[j + 1:] if j >= 0 else b''
    body = b''.join(('%s = %s' % kv).encode() + nl for kv in keys)
    return ini_bytes[:i] + b'[boot]' + nl + body + (nl + tail if tail else b'')


def sheet(paths, out, cols=3, w=595, h=360):
    from PIL import Image
    ims = [Image.open(p).resize((w, h)) for p in paths if os.path.exists(p)]
    if not ims:
        return
    rows = (len(ims) + cols - 1) // cols
    canvas = Image.new('RGB', (w * cols, h * rows))
    for k, im in enumerate(ims):
        canvas.paste(im, ((k % cols) * w, (k // cols) * h))
    canvas.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--out', required=True)
    ap.add_argument('--city', default='losangeles')
    ap.add_argument('--races', nargs='*', help='ends of race names (default: every MC2 race)')
    ap.add_argument('--start', type=float, default=45, help='first screenshot (s)')
    ap.add_argument('--seconds', type=float, default=150, help='time per race (s)')
    ap.add_argument('--every', type=float, default=8, help='screenshot interval (s)')
    ap.add_argument('--spectate', type=int, default=10, help='seconds per opponent')
    ap.add_argument('--car', default='vp_corvettez06_03')
    ap.add_argument('--chase', action='store_true',
                    help="the player's car is put 14 m behind the watched opponent and the game's own "
                         "camera draws (mc2s = <N>c): the only mode where MC3's culling (occluders, "
                         "frustum) is decided from the camera on screen")
    ap.add_argument('--follow', action='store_true',
                    help="the player's car hangs 30 m above the watched opponent (mc2s = <N>f)")
    a = ap.parse_args()

    races = [r for r in races_from_locinf(a.city) if r[1] != 'roam' and '\\mc2\\' in r[0]]
    if a.races:
        races = [r for r in races if any(r[0].endswith(x) for x in a.races)]
    os.makedirs(a.out, exist_ok=True)
    original = open(INI, 'rb').read()
    shutil.copy2(INI, INI + '.tour_backup')
    summary = []
    try:
        for path, rtype, tod, weather in races:
            name = path.split('\\')[-1]
            short = name[-28:]                   # bootargs hold 31 characters
            out = os.path.join(a.out, name)
            os.makedirs(out, exist_ok=True)
            keys = [('rnam', short), ('rscw', 1), ('city', a.city), ('time', tod),
                    ('weather', weather), ('racetype', rtype), ('car', a.car),
                    ('mc2s', '%d%s' % (a.spectate, 'c' if a.chase else 'f' if a.follow else '')), ('nofe', 1)]
            open(INI, 'wb').write(boot_section(original, keys))
            at = []
            t = a.start
            while t <= a.seconds:
                at.append('%g' % t)
                t += a.every
            print('%s  (%s, %s %s)' % (name, rtype, tod, weather), flush=True)
            subprocess.run([sys.executable, os.path.join(HERE, 'mc3_pcsx2_shots.py'),
                            '--at'] + at + ['--out', out],
                           check=True, timeout=a.seconds + 120)
            if os.path.exists(LOG):
                shutil.copy2(LOG, os.path.join(out, 'pcsx2.log'))
            log = open(os.path.join(out, 'pcsx2.log'), encoding='latin1').read() \
                if os.path.exists(os.path.join(out, 'pcsx2.log')) else ''
            cars = re.findall(r'RSPC ([0-9A-F]{8})', log)
            follows = len(re.findall(r'RFOL ', log))
            known = ('0x578648', '0x432808', '0x42f2f4')
            tlb = [l for l in re.findall(r'TLB Miss, pc=(0x[0-9a-f]+)', log) if l not in known]
            exc = log.count('Exception')
            shots = sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith('.png') and f != 'sheet.png')
            sheet(shots, os.path.join(out, 'sheet.png'))
            line = '%-44s cars %-3s shots %-3d unknown TLB %-4d exceptions %d%s' % (
                name, int(cars[0], 16) if cars else '?', len(shots), len(tlb), exc,
                '  follow marks %d' % follows if a.follow else '')
            print('   ' + line, flush=True)
            summary.append(line)
    finally:
        open(INI, 'wb').write(original)
        os.remove(INI + '.tour_backup')
        open(os.path.join(a.out, 'summary.txt'), 'w').write('\n'.join(summary) + '\n')


if __name__ == '__main__':
    main()
