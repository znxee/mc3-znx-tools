#!/usr/bin/env python3
"""Install an MC2 city's races into its MC3 city slot (installer step).

Reads the user's own MC2 PS2 copy (assets/tune/race/<city>.loc,
<city>.locinf, <city>/*.rac) and writes MC3 race files for the registered MC3
city. Nothing from MC2 ships with the mod; this runs where the MC2 files are.

MC3's race reader (mcRaceBase::Load 0x3D4A20) is keyword driven and still
knows most of MC2's vocabulary (MultiPlayer, NumPowerUps, Personality,
CarAiInfo, RubberBand...), so a race needs few changes:

  - Waypoint (aiPathGraph::Load 0x4080A0) reads id, flag and FOUR floats; MC2
    writes two -> two -1.0 appended.
  - Shortcut nodes (aiRaceShortcut::LoadShortcut 0x3D1840) read xyz, width,
    flag and THREE floats; MC2 writes one -> two -1.0 appended.
  - PlayerPosDir/PosDir: MC3 stores the direction normalised, MC2 does not.
  - CarType: MC2 cars mapped to MC3 ones (CAR_MAP).
  - RaceType: the career-only cp_ordered_time_* become cp_ordered. The
    battle modes keep their names - MC3's Capture the Flag is still
    capture_the_flag and its Detonator is MC2's bomb_tag (sd/detonate) -
    and go to the ctf/ and detonate/ folders.
  - PowerUp (flags, flag returns, power-ups of the battle modes): the same
    block in both games except Radius, two floats in MC3 (MC2 writes one ->
    written twice); the position is put on the ground like the start.
  - PreRacePath: removed from Opponent.  MC3's reader loses token alignment
    after this MC2-only nested block and gives the next opponent a null car.
  - Only the sections an arcade race needs are kept: header, start
    positions, checkpoints, opponents, shortcuts, ambient/closed roads.
    Triggers, controlled ambients/peds, bangers, power-ups, cameras, sounds
    and helicopters name MC2 props/events and are dropped.

Opponent routes name intersections of the road network, which matches because
the installed <city>_city.aib is the original MC2 file.

<mc2-city>.override (intersection ellipses by road index, same format as
MC3's) is copied as <city>.override.

The .loc must have no ';' comment between races (LoadRaceData loops on
CheckIToken("Race")), and no other city's .loc may say `City losangeles`: the
last file that names a city replaces its race list (paris.loc did).

Races are listed in tune/race/<city>.loc after the existing entries (the
cruise race the free-roam boot uses stays first), with a .locinf block and a
.rinf each. When the destination keeps the MC2 city name (Paris), the
converted arcade roam body is also mirrored over that first standard cruise:
the placeholder from an older route can otherwise spawn inside geometry.
The previous .loc/.locinf and mirrored cruise files are kept as *.before_mc2.
"""
import argparse
import math
import os
import re
import shutil
import json
import subprocess
import sys
import tempfile

CAR_MAP = {
    'vp_civica': 'vp_eclipse_04', 'vp_civicb': 'vp_lancer_04',
    'vp_rx7': 'vp_supra_98', 'vp_integraa': 'vp_golfr32_04',
    'vp_viper': 'vp_viper_03', 'vp_s2000': 'vp_elise_04',
    'vp_jetta': 'vp_jetta_03', 'vp_saleen': 'vp_saleens7_04',
    'vp_cbr929a': 'vp_ninja_03', 'vp_gto': 'vp_gto_70',
    'vp_cop_l': 'vp_impalass_cop_s_96',
}
COPS = {'vp_impalass_cop_s_96'}
# MC2 race type -> (MC3 race type, folder)
TYPES = {
    'cp_ordered': 'cp_ordered', 'cp_ordered_time_global': 'cp_ordered',
    'cp_ordered_time_local': 'cp_ordered', 'cp_unordered': 'cp_unordered',
    'roam': 'roam', 'capture_the_flag': 'capture_the_flag', 'bomb_tag': 'bomb_tag',
}
KEEP = {
    'racetype', 'racestarttime', 'numoflaps', 'racedensity', 'racepreaudio',
    'raceduraudio', 'beforecountdownlength', 'countdownlength',
    'finishedlength', 'cameratransition', 'followmaxdistance',
    'followcountdown', 'dontshowcountdown', 'audiodelaycountdown',
    'playerposdir', 'multiplayer', 'numcheckpoints', 'checkpoint',
    'numopponents', 'opponent', 'numshortcut', 'shortcut',
    'numambientroads', 'ambientroads', 'numclosedroads', 'closedroads',
    'numpowerups', 'powerup',
}
NUM = r'-?\d+(?:\.\d+)?'


PREFIXES = {
    'losangeles': ('lamarc_battle_modes_la_', 'lachrisbeta_', 'lamaurobeta_', 'lachris_',
                   'lamauro_', 'lawc_', 'lawing_'),
    'paris': ('pariswingbeta_battle_modes_', 'parischris_', 'pariswing_', 'paris_'),
}
# battle map names: ctf_industrial -> "CTF Industrial", bt_full -> "Detonator Full"
WORDS = {'ctf': 'CTF', 'bt': 'Detonator', 'full': 'Full Map', 'montmarte': 'Montmartre',
         'leftbank': 'Left Bank', 'rightbank': 'Right Bank'}


def display_name(key, mc2_city):
    """lachris_arcade_circuit_two -> Arcade Circuit Two"""
    for p in PREFIXES.get(mc2_city, (mc2_city + '_',)):
        if key.startswith(p):
            key = key[len(p):]
            break
    return ' '.join(WORDS.get(w, w.capitalize()) for w in key.split('_'))


def add_strings(fonts, strtbl_tool, entries):
    """Add/replace keys in mcstrings01(.strtbl/_pal) and mcloadstrings.strtbl
    with the tool at strtbl_tool (Edness strtbl.py, dec/enc). New keys copy the
    font block of CM_sd (or of `sd` in mcloadstrings, which has no CM_sd).

    mcloadstrings is the LOADING SCREEN's table: the city's bare name and every
    CM_<race path> the loading screen shows are looked up there, not in
    mcstrings01 - missing, it prints `!!key!!`."""
    for name in ('mcstrings01', 'mcstrings01_pal', 'mcloadstrings'):
        path = os.path.join(fonts, name + '.strtbl')
        if not os.path.exists(path):
            continue
        if not os.path.exists(path + '.before_mc2'):
            shutil.copy2(path, path + '.before_mc2')
        work = tempfile.mkdtemp()
        shutil.copy2(path, os.path.join(work, name + '.strtbl'))
        subprocess.run([sys.executable, strtbl_tool, 'dec', name + '.strtbl'], cwd=work,
                       check=True, stdout=subprocess.DEVNULL)
        jpath = os.path.join(work, name + '.json')
        j = json.load(open(jpath, encoding='utf-8'))
        before = set(j['data'])
        for key, text in entries:
            if key not in j['data']:
                template = 'CM_sd' if 'CM_sd' in j['data'] else 'sd'
                j['data'][key] = json.loads(json.dumps(j['data'][template]))
            for lang in j['data'][key].values():
                lang['text'] = text
        json.dump(j, open(jpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.remove(os.path.join(work, name + '.strtbl'))
        subprocess.run([sys.executable, strtbl_tool, 'enc', name + '.json'], cwd=work,
                       check=True, stdout=subprocess.DEVNULL)
        shutil.copy2(os.path.join(work, name + '.strtbl'), path)
        print('  %s: %d keys, %d new' % (name, len(j['data']), len(set(j['data']) - before)))


def normalised(line, key):
    """`key x y z dx dy dz` with the direction scaled to length 1."""
    m = re.match(r'(\s*%s\s+)(.*)$' % key, line)
    v = [float(t) for t in m.group(2).split()[:6]]
    n = math.sqrt(v[3] ** 2 + v[4] ** 2 + v[5] ** 2) or 1.0
    d = [v[3] / n, v[4] / n, v[5] / n]
    return '%s%f\t%f\t%f  %f\t%f\t%f ' % (m.group(1), v[0], v[1], v[2], d[0], d[1], d[2])


class Ground(object):
    """Heights of an MC2 city's collision from its own .rsc (the BND0
    otgrid cells and quadtree - the same data losangeles_bound turns into MC3
    collision). MC2's races keep start positions and shortcut
    nodes at y = 0 (its AI is 2D); MC3 uses the height, and 8 opponents piled
    up for good on a shortcut node 16 m above the sunken road near Santa
    Monica (-826, -1170). height() answers the walkable surface under (x, z)
    closest to a preferred height."""

    CELL = 16.0

    def __init__(self, rsc_path):
        import struct
        d = open(rsc_path, 'rb').read()
        u = lambda o: struct.unpack_from('<I', d, o)[0]
        count = u(4)
        self.buckets = {}
        for i in range(count):
            off, fc = u(8 + 8 * i), d[12 + 8 * i:16 + 8 * i]
            if fc != b'BND0' or d[off + 4] not in (0x0A, 0x0B):
                continue
            if d[off + 4] == 0x0A:
                nodes = [off]
            else:
                cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)
                nodes = [off + first + 0x90 * c for c in range(cells)
                         if u(off + first + 0x90 * c + 0x84) != 0xFFFFFFFF]
            for node in nodes:
                nv, np_ = u(node + 0x4C), u(node + 0x50)
                vo, po = off + u(node + 0x74), off + u(node + 0x78)
                verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
                for k in range(np_):
                    ny = struct.unpack_from('<f', d, po + 32 * k + 4)[0]
                    if ny < 0.3:
                        continue
                    idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
                    pts = [verts[j] for j in idx[:3 if idx[3] == 0 else 4]]
                    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
                    z0, z1 = min(p[2] for p in pts), max(p[2] for p in pts)
                    for cx in range(int(x0 // self.CELL), int(x1 // self.CELL) + 1):
                        for cz in range(int(z0 // self.CELL), int(z1 // self.CELL) + 1):
                            self.buckets.setdefault((cx, cz), []).append(pts)

    def heights(self, x, z):
        out = []
        for pts in self.buckets.get((int(x // self.CELL), int(z // self.CELL)), ()):
            inside = False
            for i in range(len(pts)):
                a, c = pts[i], pts[(i + 1) % len(pts)]
                if (a[2] > z) != (c[2] > z) and x < a[0] + (z - a[2]) * (c[0] - a[0]) / (c[2] - a[2]):
                    inside = not inside
            if inside:
                # plane through the first three points
                (ax, ay, az), (bx, by, bz), (qx, qy, qz) = pts[0], pts[1], pts[2]
                ux, uy, uz = bx - ax, by - ay, bz - az
                vx, vy, vz = qx - ax, qy - ay, qz - az
                nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
                if abs(ny) > 1e-6:
                    out.append(ay - (nx * (x - ax) + nz * (z - az)) / ny)
        return out

    def height(self, x, z, prefer):
        hs = self.heights(x, z)
        return min(hs, key=lambda y: abs(y - prefer)) if hs else prefer


def checkpoint_heights(text):
    """[(x, y, z)] of the race's checkpoints - MC2 keeps their real height."""
    out = []
    for m in re.finditer(r'^Checkpoint\s+(%s)\s+(%s)\s+(%s)' % (NUM, NUM, NUM), text, re.M):
        out.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
    return out


def grounded(line, ground, cps, stats):
    """The line's leading x y z with y put on the ground under it: the surface
    closest to the height of the nearest checkpoint."""
    m = re.match(r'(\s*(?:[A-Za-z]+\s+)?)(%s)(\s+)(%s)(\s+)(%s)(.*)$' % (NUM, NUM, NUM), line)
    if not m or ground is None:
        return line
    x, y, z = float(m.group(2)), float(m.group(4)), float(m.group(6))
    prefer = min(cps, key=lambda c: (c[0] - x) ** 2 + (c[2] - z) ** 2)[1] if cps else y
    ny = ground.height(x, z, prefer)
    if abs(ny - y) > 0.5:
        stats[0] += 1
    return '%s%s%s%f%s%s%s' % (m.group(1), m.group(2), m.group(3), ny, m.group(5), m.group(6), m.group(7))


def sections(text):
    """Top-level sections: a keyword at column 0 and every indented or brace
    line after it."""
    out = []
    for line in text.splitlines():
        if line and not line[0].isspace() and line[0] not in '{}':
            out.append([line])
        elif out:
            out[-1].append(line)
    return out


def without_nested_blocks(lines, names):
    """Drop named `Token { ... }` blocks while preserving their parent.

    MC2's PreRacePath lives inside Opponent, so the top-level KEEP filter cannot
    remove it.  MC3 does not consume that nested grammar: the next Opponent is
    then parsed with a null CarType and aiOpponentFactory hangs in LookupCar.
    """
    out, skipping, opened, depth = [], False, False, 0
    for line in lines:
        if not skipping and line.strip().lower() in names:
            skipping, opened, depth = True, False, 0
            continue
        if skipping:
            opens, closes = line.count('{'), line.count('}')
            opened = opened or opens > 0
            depth += opens - closes
            if opened and depth <= 0:
                skipping = False
            continue
        out.append(line)
    return out


def convert_rac(text, cars_seen, ground=None, stats=None):
    kept, cops, laps, start, opponents = [], set(), 1, None, 0
    cps = checkpoint_heights(text)
    stats = stats if stats is not None else [0]
    for sec in sections(text):
        key = sec[0].split()[0].lower()
        if key not in KEEP:
            continue
        in_shortcut = key == 'shortcut'
        in_powerup = key == 'powerup'
        lines = []
        source_lines = without_nested_blocks(sec, {'preracepath'}) if key == 'opponent' else sec
        for line in source_lines:
            s = line.strip()
            low = s.lower()
            if low.startswith('racetype'):
                line = 'RaceType %s' % TYPES[s.split()[1].lower()]
            elif low.startswith('numoflaps') and line == sec[0]:
                laps = int(s.split()[1])
            elif low.startswith('playerposdir'):
                line = grounded(normalised(line, 'PlayerPosDir'), ground, cps, stats)
                start = line.split()[1:4]
            elif low.startswith('posdir'):
                line = grounded(normalised(line, 'PosDir'), ground, cps, stats)
            elif low.startswith('cartype'):
                old = s.split()[1].lower()
                new = CAR_MAP.get(old, 'vp_eclipse_04')
                cars_seen[old] = new
                if new in COPS:
                    cops.add(new)
                line = line.replace(s, 'CarType %s' % new)
            elif low.startswith('waypoint') and len(s.split()) == 5:
                line = line.rstrip() + '  -1.000000  -1.000000  '
            elif in_shortcut and re.fullmatch(r'(%s\s+){5}%s' % (NUM, NUM), s):
                line = grounded(line.rstrip(), ground, cps, stats) + '  -1.000000  -1.000000  '
            elif in_powerup and low.startswith('radius') and len(s.split()) == 2:
                line = line.replace(s, 'Radius %s  %s ' % (s.split()[1], s.split()[1]))
            elif in_powerup and low.startswith('position'):
                line = grounded(line.rstrip(), ground, cps, stats) + ' '
            elif low.startswith('numopponents'):
                opponents = int(s.split()[1])
            lines.append(line)
        kept.extend(lines)
    return '\n'.join(kept) + '\n', laps, start, opponents, sorted(cops)


def fix_paris_siderace_starts(rac):
    """Keep both low-level Ian side-race opponents on the working upper road.

    The MC2 start grid spans two stacked surfaces. MC3 repeatedly steers the
    two cars placed on the lower surface into an unrailed dead end, respawns
    them near the start, and repeats. The other three start and route on the
    upper road. The replacement slots are spaced 10 m from those cars.
    """
    slots = {
        (-84.602669, 354.666168): (-63.0, 0.0, 353.0),
        (-75.746414, 353.171356): (-55.0, 0.0, 347.0),
    }
    lines, changed = [], set()
    pattern = re.compile(r'^(\s*PosDir\s+)(%s)(\s+)(%s)(\s+)(%s)(.*)$' % (NUM, NUM, NUM))
    for line in rac.splitlines():
        match = pattern.match(line)
        if match:
            x, z = float(match.group(2)), float(match.group(6))
            for old, new in slots.items():
                if abs(x - old[0]) < 0.001 and abs(z - old[1]) < 0.001:
                    line = '%s%.6f%s%.6f%s%.6f%s' % (
                        match.group(1), new[0], match.group(3), new[1],
                        match.group(5), new[2], match.group(7))
                    changed.add(old)
                    break
        lines.append(line)
    if changed != set(slots):
        raise ValueError('Paris Ian side-race start positions changed in source race')
    return '\n'.join(lines) + '\n'


def read_loc(path):
    """(race type, name) of every hookman race in MC2's .loc."""
    races = []
    for line in open(path, encoding='latin1'):
        t = line.split()
        if len(t) >= 2 and t[0].lower() in TYPES:
            races.append((t[0].lower(), t[1]))
    return races


def read_locinf(path):
    """name -> (time, weather, ambient, ped)"""
    text = open(path, encoding='latin1').read()
    out = {}
    for m in re.finditer(r'(\S+)\s*\{(.*?)\}', text, re.S):
        body = m.group(2).split()
        amb = re.search(r'AmbientDensity:\s*(%s)' % NUM, m.group(2))
        ped = re.search(r'PedDensity:\s*(%s)' % NUM, m.group(2))
        out[m.group(1).lower()] = (body[0], body[1],
                                   float(amb.group(1)) if amb else 10.0,
                                   float(ped.group(1)) if ped else 10.0)
    return out


def folder_for(mc3_type, laps):
    if mc3_type == 'roam':
        return 'cruise'
    if mc3_type == 'capture_the_flag':
        return 'ctf'
    if mc3_type == 'bomb_tag':
        return 'detonate'
    if mc3_type == 'cp_unordered':
        return 'unordered'
    return 'circuit' if laps > 1 else 'ordered'


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--rsc', default=None,
                    help="MC2 city's .rsc (default: HostFS mc2/<mc2-city>/<mc2-city>.rsc); '' keeps MC2's y")
    ap.add_argument('--mc2', default=os.path.join(os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets'), 'tune/race'),
                    help="MC2's assets/tune/race")
    ap.add_argument('--mc3', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/tune/race'),
                    help="MC3's ASSETS/tune/race")
    ap.add_argument('--mc2-city', default='losangeles',
                    help='source MC2 city name (losangeles or paris)')
    ap.add_argument('--city', default=None,
                    help='destination MC3 city name (default: losangeles for Los Angeles, otherwise source name)')
    ap.add_argument('--fonts', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/fonts'),
                    help="MC3's ASSETS/fonts (string tables); '' to skip")
    ap.add_argument('--strtbl', default=os.environ.get('MC3_STRTBL', 'strtbl.py'),
                    help='Edness strtbl.py (dec/enc)')
    ap.add_argument('--label', default=None, help='city name shown in menus')
    ap.add_argument('--add-types', default=None,
                    help='comma list of MC2 race types (e.g. capture_the_flag,bomb_tag): '
                         'convert only those and APPEND them to the installed .loc/.locinf, '
                         'leaving every race already installed untouched (hand fixes such as '
                         'the AI shortcuts of mc2_race_shortcut.py survive)')
    a = ap.parse_args()
    add_types = set(t.strip().lower() for t in a.add_types.split(',')) if a.add_types else None

    a.mc2_city = a.mc2_city.lower()
    if a.city is None:
        a.city = 'losangeles' if a.mc2_city == 'losangeles' else a.mc2_city
    if a.label is None:
        a.label = 'Los Angeles' if a.mc2_city == 'losangeles' else a.mc2_city.title()
    if a.rsc is None:
        a.rsc = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc2/{0}/{0}.rsc').format(a.mc2_city)

    races = read_loc(os.path.join(a.mc2, a.mc2_city + '.loc'))
    ground = Ground(a.rsc) if a.rsc and os.path.exists(a.rsc) else None
    if ground is None:
        print('  no %s.rsc: start positions and shortcut nodes keep y = 0' % a.mc2_city)
    info = read_locinf(os.path.join(a.mc2, a.mc2_city + '.locinf'))
    loc_path = os.path.join(a.mc3, a.city + '.loc')
    inf_path = os.path.join(a.mc3, a.city + '.locinf')
    for p in (loc_path, inf_path):
        if not os.path.exists(p + '.before_mc2'):
            shutil.copy2(p, p + '.before_mc2')
    base_loc = loc_path if add_types else loc_path + '.before_mc2'
    base_inf = inf_path if add_types else inf_path + '.before_mc2'
    loc = open(base_loc, encoding='latin1').read().rstrip() + '\n'
    inf = open(base_inf, encoding='latin1').read().rstrip() + '\n'
    listed = set(n.lower() for n in re.findall(r'Name\s+(\S+)', loc))
    # no ';' comment here: mc::mcCityData::LoadRaceData loops on CheckIToken("Race")
    # and a comment between races ends the list

    cars, done, seen = {}, 0, set()
    converted_roam = None
    strings = [(a.city, a.label), ('CM_' + a.city, a.label),
               ('CM_cruise\\easy\\%s_cruise_race01' % a.city, a.label + ' Cruise')]
    for mc2_type, name in races:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        if add_types is not None and mc2_type not in add_types:
            continue
        src = os.path.join(a.mc2, a.mc2_city, key + '.rac')
        if not os.path.exists(src):
            print('  missing %s' % src)
            continue
        moved = [0]
        rac, laps, start, opponents, cops = convert_rac(
            open(src, encoding='latin1').read(), cars, ground, moved)
        if a.mc2_city == 'paris' and key == 'paris_ian_siderace':
            rac = fix_paris_siderace_starts(rac)
        mc3_type = TYPES[mc2_type]
        rel = '%s\\mc2\\%s' % (folder_for(mc3_type, laps), key)
        if add_types is not None and rel.lower() in listed:
            print('  already listed: %s' % rel)
            continue
        out = os.path.join(a.mc3, a.city, *rel.split('\\'))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        time, weather, amb, ped = info.get(key, ('midnight', 'clear', 10.0, 10.0))
        cop_block = 'CopCarTypes %d\n{\n%s}\n' % (
            len(cops), ''.join('\t%s\n' % c for c in cops))
        rinf = (
            'City %s\nTime %s\nWeather %s\nRaceType %s\nAmbientDensity %f \n'
            'PedDensity %f \nForcedCar Default\nStartBeaconPosition %s\t%s\t%s  \n'
            'OpponentCount %d\nNumLaps %d\nNonCopetitorCarTypes 0\n{\n}\n%s'
            % (a.city, time, weather, mc3_type, amb, ped, start[0], start[1], start[2],
               opponents, laps, cop_block))
        open(out + '.rac', 'w', newline='\r\n').write(rac)
        open(out + '.rinf', 'w', newline='\r\n').write(rinf)
        if mc3_type == 'roam' and converted_roam is None:
            converted_roam = (rac, rinf)
        loc += 'Race\n{\n\tName\t%s\n}\n' % rel
        strings.append(('CM_' + rel, display_name(key, a.mc2_city)))
        inf += ('%s\n{\n\tTime\t%s\n\tWeather\t%s\n\tRaceType\t%s\n'
                '\tAmbientDensity\t%f\n\tPedDensity\t%f\n'
                '\tStartBeaconPosition\t%s\t%s\t%s\n\tOpponentCount\t%d\n\tNumLaps\t%d\n'
                '\tNonCopetitorCarTypes 0\n\t{\n\t}\n\tCopCarTypes %d\n\t{\n%s\t}\n}\n'
                % (rel, time, weather, mc3_type, amb, ped, start[0], start[1], start[2],
                   opponents, laps, len(cops), ''.join('\t\t%s\n' % c for c in cops)))
        done += 1
        print('  %-14s %-44s laps %d  opponents %d  %s %s  heights fixed %d'
              % (mc3_type, rel, laps, opponents, time, weather, moved[0]))
    if add_types is None and a.city == a.mc2_city and converted_roam is not None:
        base = os.path.join(a.mc3, a.city, 'cruise', 'easy',
                            a.city + '_cruise_easy_cruise_race01')
        os.makedirs(os.path.dirname(base), exist_ok=True)
        for ext, body in (('.rac', converted_roam[0]), ('.rinf', converted_roam[1])):
            path = base + ext
            if os.path.exists(path) and not os.path.exists(path + '.before_mc2'):
                shutil.copy2(path, path + '.before_mc2')
            open(path, 'w', newline='\r\n').write(body)
        print('  mirrored MC2 roam into %s' % os.path.relpath(base, a.mc3))
    # Intersection shapes, indexed by the roads of the original MC2 .aib.
    ovr = os.path.join(a.mc3, a.city + '.override')
    if os.path.exists(ovr) and not os.path.exists(ovr + '.before_mc2'):
        shutil.copy2(ovr, ovr + '.before_mc2')
    if add_types is None:
        shutil.copyfile(os.path.join(a.mc2, a.mc2_city + '.override'), ovr)
    open(loc_path, 'w', newline='\r\n').write(loc)
    open(inf_path, 'w', newline='\r\n').write(inf)
    # menu/loading labels: the loading screen asks the table for the bare city
    # name ('losangeles'), the menus for CM_<city> and CM_<race path>
    if a.fonts and os.path.exists(a.strtbl):
        add_strings(a.fonts, a.strtbl, strings)
    print('%d races written; cars: %s' % (done, ', '.join('%s->%s' % kv for kv in sorted(cars.items()))))


if __name__ == '__main__':
    main()
