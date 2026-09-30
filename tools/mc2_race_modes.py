#!/usr/bin/env python3
"""Build MC3-only race modes for an MC2 city from its converted MC2 races.

MC2 has no Tag, Paint or Frenzy, but its races already hold what those modes
need. Run after mc2_races_to_mc3.py (which installs the MC2 races, battle
modes included with --add-types capture_the_flag,bomb_tag):

  tag     one per MC2 Capture the Flag arena: the same start grid, opponents and
          power-ups; the flags dropped; ONE checkpoint - where the tag starts -
          at the flag farthest from the start (sd/tag: 1 checkpoint, 7
          opponents, power-ups).
  paint   one per arena: start, opponents and power-ups, and up to 21
          checkpoints - the paint zones - picked from the arena's flags, spread
          by farthest-point sampling (sd/paint: 21 checkpoints, 7 opponents).
  frenzy  from the MC2 ordered checkpoint races (not circuits): their
          checkpoints in order, no opponents, RaceSpecificParams as San Diego's
          easy frenzy; RaceStartTime and each checkpoint's time bonus come from
          the distances at FRENZY_SPEED (sd/frenzy: start 6-11 s, 4-8 s per
          checkpoint).

Every race is written to <city>/<mode>/mc2/<source>_<mode>.rac/.rinf, appended to
<city>.loc/.locinf when not listed yet (the races already there are never
touched) and named in mcstrings01(.strtbl/_pal) as CM_<path>.

    python mc2_race_modes.py --city losangeles
    python mc2_race_modes.py --city paris --mc2-city paris
"""
import argparse
import math
import os
import re
import shutil

import mc2_races_to_mc3 as conv

NUM = r'-?\d+(?:\.\d+)?'
PAINT_ZONES = 21
FRENZY_SPEED = 18.0             # m/s, city driving through traffic
FRENZY_MIN_CHECKPOINTS = 8
FRENZY_PER_CITY = 5
FRENZY_PARAMS = ('RaceSpecificParams\t\n{\n\tFinalAmbientsPassedGoal 100 \n'
                 '\tInitialAmbientsPassedGoal 5 \n\tAmbientsGoalIncrement 1 \n'
                 '\tInitialPlayerLives 3 \n}\n')
CHECKPOINT = ('Checkpoint %f\t%f\t%f  8.000000  25.000000  300.000000  %f  '
              'Arrow 10.000000  0  Type Normal ')


def blocks(text, name):
    """(start, end) character spans of every top-level `name { ... }` block."""
    out = []
    for m in re.finditer(r'^%s\s*\n\{' % name, text, re.M):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = text.find('\n', i)
                    out.append((m.start(), len(text) if end < 0 else end + 1))
                    break
            i += 1
    return out


def powerups(text):
    """[(span, type, (x, y, z))] of the race's PowerUp blocks."""
    out = []
    for a, b in blocks(text, 'PowerUp'):
        body = text[a:b]
        kind = re.search(r'PickupType\s+(\S+)', body).group(1)
        pos = tuple(float(v) for v in re.search(
            r'Position\s+(%s)\s+(%s)\s+(%s)' % (NUM, NUM, NUM), body).groups())
        out.append(((a, b), kind, pos))
    return out


def start_of(text):
    return tuple(float(v) for v in re.search(
        r'PlayerPosDir\s+(%s)\s+(%s)\s+(%s)' % (NUM, NUM, NUM), text).groups())


def dist(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def spread(points, count, start):
    """Farthest-point sampling, beginning with the point farthest from start."""
    left = list(dict.fromkeys(points))
    if not left:
        return []
    picked = [max(left, key=lambda p: dist(p, start))]
    left.remove(picked[0])
    while left and len(picked) < count:
        best = max(left, key=lambda p: min(dist(p, q) for q in picked))
        picked.append(best)
        left.remove(best)
    return picked


def with_checkpoints(text, lines):
    """Replace the numCheckpoints line (and any Checkpoint lines) by these."""
    text = re.sub(r'^Checkpoint .*\n', '', text, flags=re.M)
    body = 'numCheckpoints %d \n\n%s' % (len(lines), ''.join(l + '\n' for l in lines))
    return re.sub(r'^numCheckpoints\s+\d+\s*\n', body, text, count=1, flags=re.M)


def arena_mode(text, mode):
    """A Tag or Paint race from a converted Capture the Flag arena."""
    start = start_of(text)
    pus = powerups(text)
    flags = [p for _s, kind, p in pus if kind in ('Flag', 'FlagReturn')]
    keep = [text[a:b] for (a, b), kind, _p in pus if kind == 'Powerup']
    first = min(a for (a, _b), _k, _p in pus) if pus else None
    last = max(b for (_a, b), _k, _p in pus) if pus else None
    if first is None:
        return None
    text = text[:first] + ''.join(keep) + text[last:]
    text = re.sub(r'^NumPowerUps\s+\d+', 'NumPowerUps %d' % len(keep), text, count=1, flags=re.M)
    text = re.sub(r'^RaceType\s+\S+', 'RaceType %s' % mode, text, count=1, flags=re.M)
    zones = spread(flags, 1 if mode == 'tag' else PAINT_ZONES, start)
    if not zones:
        return None
    return with_checkpoints(text, [CHECKPOINT % (x, y, z, 0.0) for x, y, z in zones])


def frenzy(text):
    """A Frenzy race from a converted ordered checkpoint race."""
    cps = [tuple(float(v) for v in m.groups()) for m in re.finditer(
        r'^Checkpoint\s+(%s)\s+(%s)\s+(%s)' % (NUM, NUM, NUM), text, re.M)]
    if len(cps) < FRENZY_MIN_CHECKPOINTS:
        return None
    start = start_of(text)
    for a, b in reversed(blocks(text, 'Opponent')):
        text = text[:a] + text[b:]
    for a, b in reversed(blocks(text, 'Shortcut')):
        text = text[:a] + text[b:]
    text = re.sub(r'^numOpponents\s+\d+', 'numOpponents 0', text, count=1, flags=re.M)
    text = re.sub(r'^NumShortcut\s+\d+', 'NumShortcut 0', text, count=1, flags=re.M)
    text = re.sub(r'^RaceType\s+\S+', 'RaceType frenzy', text, count=1, flags=re.M)
    text = re.sub(r'^NumOfLaps\s+\d+', 'NumOfLaps 1', text, count=1, flags=re.M)
    first = round(dist(start, cps[0]) / FRENZY_SPEED + 3.0)
    text = re.sub(r'^RaceStartTime\s+%s' % NUM, 'RaceStartTime %f' % first, text,
                  count=1, flags=re.M)
    lines, prev = [], cps[0]
    for i, c in enumerate(cps):
        bonus = 0.0 if i == 0 else max(3.0, min(15.0, round(dist(prev, c) / FRENZY_SPEED + 1.0)))
        lines.append(CHECKPOINT % (c[0], c[1], c[2], bonus))
        prev = c
    text = with_checkpoints(text, lines)
    return re.sub(r'^PlayerPosDir', FRENZY_PARAMS + 'PlayerPosDir', text, count=1, flags=re.M)


def rinf_for(rinf, mode, opponents=None, ambient=None):
    rinf = re.sub(r'^RaceType\s+\S+', 'RaceType %s' % mode, rinf, flags=re.M)
    if opponents is not None:
        rinf = re.sub(r'^OpponentCount\s+\d+', 'OpponentCount %d' % opponents, rinf, flags=re.M)
    if ambient is not None:
        m = re.search(r'^AmbientDensity\s+(%s)' % NUM, rinf, re.M)
        if m and float(m.group(1)) < ambient:
            rinf = rinf.replace(m.group(0), 'AmbientDensity %f' % ambient)
    return rinf


def locinf_block(rel, rinf):
    get = lambda k, d='': (re.search(r'^%s\s+(.*?)\s*$' % k, rinf, re.M) or [None, d])[1]
    return ('%s\n{\n\tTime\t%s\n\tWeather\t%s\n\tRaceType\t%s\n\tAmbientDensity\t%s\n'
            '\tPedDensity\t%s\n\tStartBeaconPosition\t%s\n\tOpponentCount\t%s\n\tNumLaps\t1\n'
            '\tNonCopetitorCarTypes 0\n\t{\n\t}\n\tCopCarTypes 0\n\t{\n\t}\n}\n'
            % (rel, get('Time', 'midnight'), get('Weather', 'clear'), get('RaceType'),
               get('AmbientDensity', '2.0'), get('PedDensity', '2.0'),
               get('StartBeaconPosition'), get('OpponentCount', '0')))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--mc3', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/tune/race'))
    ap.add_argument('--city', default='losangeles', help='MC3 city (losangeles = LA, paris)')
    ap.add_argument('--mc2-city', default=None, help='MC2 city (default losangeles for losangeles)')
    ap.add_argument('--fonts', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/fonts'))
    ap.add_argument('--strtbl', default=os.environ.get('MC3_STRTBL', 'strtbl.py'))
    a = ap.parse_args()
    mc2_city = a.mc2_city or ('losangeles' if a.city == 'losangeles' else a.city)
    root = os.path.join(a.mc3, a.city)
    loc_path = os.path.join(a.mc3, a.city + '.loc')
    inf_path = os.path.join(a.mc3, a.city + '.locinf')
    for p in (loc_path, inf_path):
        if not os.path.exists(p + '.before_modes'):
            shutil.copy2(p, p + '.before_modes')
    loc = open(loc_path, encoding='latin1').read().rstrip() + '\n'
    inf = open(inf_path, encoding='latin1').read().rstrip() + '\n'
    listed = set(n.lower() for n in re.findall(r'Name\s+(\S+)', loc))

    jobs = []           # (mode, source key, rac text, rinf text)
    ctf = os.path.join(root, 'ctf', 'mc2')
    for fn in sorted(os.listdir(ctf)) if os.path.isdir(ctf) else ():
        if fn.endswith('.rac') and 'poweruptest' not in fn:
            key = fn[:-4]
            rac = open(os.path.join(ctf, fn), encoding='latin1').read()
            rinf = open(os.path.join(ctf, key + '.rinf'), encoding='latin1').read()
            for mode in ('tag', 'paint'):
                body = arena_mode(rac, mode)
                if body:
                    jobs.append((mode, key, body, rinf_for(rinf, mode)))
    ordered = os.path.join(root, 'ordered', 'mc2')
    cand = []
    for fn in sorted(os.listdir(ordered)) if os.path.isdir(ordered) else ():
        if fn.endswith('.rac') and 'tutorial' not in fn:
            rac = open(os.path.join(ordered, fn), encoding='latin1').read()
            body = frenzy(rac)
            if body:
                rinf = open(os.path.join(ordered, fn[:-4] + '.rinf'), encoding='latin1').read()
                cand.append((body.count('\nCheckpoint'), fn[:-4], body,
                             rinf_for(rinf, 'frenzy', opponents=0, ambient=2.0)))
    for _n, key, body, rinf in sorted(cand, reverse=True)[:FRENZY_PER_CITY]:
        jobs.append(('frenzy', key, body, rinf))

    strings, added = [], 0
    for mode, key, body, rinf in jobs:
        rel = '%s\\mc2\\%s_%s' % (mode, key, mode)
        out = os.path.join(root, mode, 'mc2', '%s_%s' % (key, mode))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out + '.rac', 'w', newline='\r\n').write(body)
        open(out + '.rinf', 'w', newline='\r\n').write(rinf)
        label = '%s %s' % (mode.capitalize(), conv.display_name(key, mc2_city))
        label = label.replace('Tag CTF ', 'Tag ').replace('Paint CTF ', 'Paint ')
        label = label.replace(' Checkpoint ', ' ').replace('Worldchampla', 'World Champ LA').replace(
            'Worldchampparis', 'World Champ Paris')
        strings.append(('CM_' + rel, label))
        cps = body.count('\nCheckpoint')
        if rel.lower() in listed:
            print('  rewritten (listed)  %-58s %s' % (rel, label))
            continue
        loc += 'Race\n{\n\tName\t%s\n}\n' % rel
        inf += locinf_block(rel, rinf)
        added += 1
        print('  %-7s %-58s %-34s checkpoints %d' % (mode, rel, label, cps))
    open(loc_path, 'w', newline='\r\n').write(loc)
    open(inf_path, 'w', newline='\r\n').write(inf)
    if a.fonts and os.path.exists(a.strtbl) and strings:
        conv.add_strings(a.fonts, a.strtbl, strings)
    print('%d races added to %s.loc (%d written)' % (added, a.city, len(jobs)))


if __name__ == '__main__':
    main()
