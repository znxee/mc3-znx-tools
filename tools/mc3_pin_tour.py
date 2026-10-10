"""Camera tour over a city's pins (+ extra views), through the menus.

    python pin_tour.py CITY_LABEL PINS_FILE OUT [--extra ex,ey,ez,tx,ty,tz ...] [--boot k=v ...]

Writes $MC3_HOSTFS/mc2_camtour.txt (removed at the end), drives
Arcade -> CITY_LABEL -> Ordered Race with mc2t=SLOT, takes one shot in the
middle of every slot (matched to RTOU markers) named v<slot>.png.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get('MC3_HOSTFS', 'MC3HostFS')
TOUR = os.path.join(HOST, 'mc2_camtour.txt')
SLOT = 8

ap = argparse.ArgumentParser()
ap.add_argument('city')
ap.add_argument('pins')
ap.add_argument('out')
ap.add_argument('--extra', action='append', default=[])
ap.add_argument('--boot', action='append', default=[])
ap.add_argument('--only', help='comma list of pin indices')
a = ap.parse_args()

views = []
only = set(int(x) for x in a.only.split(',')) if a.only else None
for line in open(a.pins):
    if line.startswith('#') or not line.strip():
        continue
    r = line.split()
    if only is not None and int(r[0]) not in only:
        continue
    px, py, pz, fx, fz, cx, cy, cz = map(float, r[3:])
    back, up = (9, 3.0) if r[2] == 'wall' else (11, 7.0)
    views.append(('pin%s' % r[0], (cx - fx * back, cy + up, cz - fz * back, px, py, pz)))
for k, e in enumerate(a.extra):
    views.append(('extra%d' % k, tuple(float(v) for v in e.split(','))))
open(TOUR, 'w').write(''.join('%.1f %.1f %.1f %.1f %.1f %.1f\n' % v for _, v in views))
os.makedirs(a.out, exist_ok=True)
n = len(views)
steps = ['until state:41 120', 'push cross havefun', 'push cross career', 'select arcade',
         'push cross row:players', 'select =%s right 12' % a.city, 'press down',
         'select =ordered_race right 8', 'push cross row:time_of_day', 'push cross start',
         'press cross', 'until state:1 300', 'wait 4']
shots = 0
for k in range(n + 1):
    steps += ['wait %.1f' % (SLOT - 0.6), 'shot s%02d' % k]
cmd = [sys.executable, os.path.join(TOOLS, 'mc3_pcsx2_drive.py'), '--elf', os.path.join(HOST, 'mc3boot.elf'),
       '--skip-fmv', '--add-mod', 'frontend_no_timeout.mod = defer', '--boot', 'mc2t=%d' % SLOT,
       '--out', a.out] + sum([['--boot', b] for b in a.boot], []) + steps
try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    print('\n'.join(l for l in r.stdout.splitlines() if 'until' in l or 'error' in l.lower())[-600:])
finally:
    if os.path.exists(TOUR):
        os.remove(TOUR)
sys.path.insert(0, TOOLS)
import mc3_pcsx2_shots as S
shutil.copy2(S.LOG, os.path.join(a.out, 'pcsx2.log'))
log = open(os.path.join(a.out, 'pcsx2.log'), 'rb').read().decode('latin1')
rt = [(float(m.group(1).replace(',', '.')), int(m.group(2), 16))
      for m in re.finditer(r'\[\s*([\d,]+)\]\s+RTOU ([0-9A-F]{8})', log)]
m = re.search(r'until state:1: ok', r.stdout)
t1 = float(re.search(r'([\d.]+)s\s+until state:1: ok', r.stdout).group(1))
files = sorted(f for f in os.listdir(a.out) if re.match(r's\d\d\.png$', f))
base = os.path.getmtime(os.path.join(a.out, files[0])) - (t1 + 4 + SLOT - 0.6 + 0.4)
done = set()
for f in files:
    rel = os.path.getmtime(os.path.join(a.out, f)) - base
    c = [x for x in rt if x[0] <= rel]
    if not c or rel - c[-1][0] < 2.0:
        continue
    idx = c[-1][1]
    if idx < n and idx not in done:
        done.add(idx)
        shutil.copy2(os.path.join(a.out, f), os.path.join(a.out, '%s.png' % views[idx][0]))
print('views', n, 'matched', sorted(views[i][0] for i in done))
