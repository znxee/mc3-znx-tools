#!/usr/bin/env python3
"""Install the MC3 side of an MC2 city that the city mods draw from MC2's own
files (geometry, collision, props and lighting stay in MC2.DAT).

    python mc2_city_install.py --hostfs $MC3_HOSTFS \
        --mc2-city tokyo --city tokyo_mc2 --code k --label "Tokyo MC2"

What an MC2 city needs in ASSETS, with an installed MC2 city (--template,
default paris) as the model:
  - copied from the template, its name replaced in the path and in text
    files: lighting and HDR tunes, effects, traffic densities and packs,
    pedestrian tunes, progress, HUD map tune, weather...;
  - its code letter (--code) in the five loading screens flash/*_<code>.pck
    and the ambient audio bank audio/banks/Ambient_<code>.bnk/.td;
  - MC2's originals, unchanged: city/<city>/<city>_city.aib (MC2's <mc2>.aib),
    <city>.algt and texture/hud_map_<city>.tex (+ _proxy);
  - generated from MC2's data: the nine shells resources/city/<city>_<time>_
    <weather>.pck (mc3_city_build.py, Atlanta donor, one 1 m placeholder cube,
    grid from the extents of MC2's <mc2>.lvl), the occluders <city>.occlude
    (mc2_occluders.py) and the pedestrian graph <city>.graph, also compiled
    into <city>_peds.pck (mc2_ped_graph.py + mc3_peds_graph.py);
  - a race catalogue holding only Cruise (tune/race/<city>.loc/.locinf); the
    MC2 races come next with mc2_races_to_mc3.py --mc2-city <mc2> --city
    <city>, the modes with --add-types and mc2_race_modes.py.
The city still needs registering (mods/city_paris_slot7 here; city_psp_slots
in the MCLA PSP fork's combined install) and its slot in mods/mc2_city_config.h. Every file written is listed in
<hostfs>/<city>_install.json (and, as it goes, <city>_install.log); a file
that was there before is backed up once as <file>.before_<city>.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TIMES = ('dawn', 'dusk', 'midnight')
WEATHERS = ('clear', 'cloudy', 'rainy')
CHARS = ('\0 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~\x7f'
         + '\0' * 14)


def dave_entries(path):
    """{name: (offset, size, stored)} of a Dave archive (MC2's ASSETS.DAT)."""
    f = open(path, 'rb')
    head = f.read(16)
    n, info = struct.unpack_from('<II', head, 4)
    f.seek(0x800)
    toc = f.read(n * 16)
    names_at = 0x800 + info
    out, name = {}, ''
    for i in range(n):
        no, fo, full, comp = struct.unpack_from('<4I', toc, i * 16)
        f.seek(names_at + no)
        raw = f.read(160)
        at = 0

        def bits():
            nonlocal at
            v = raw[at] | raw[at + 1] << 8 | raw[at + 2] << 16
            at += 3
            return [v >> (6 * k) & 63 for k in range(4)]
        if head[:4] == b'DAVE':
            name = raw.split(b'\0')[0].decode('latin1')
        else:
            q = bits()
            prev, name = name, ''
            if q[0] >= 0x38:
                keep = (q.pop(1) - 0x20) * 8 + q.pop(0) - 0x38
                name = prev[:keep]
            while q[0]:
                name += CHARS[q.pop(0)]
                if not q:
                    q = bits()
        out[name] = (fo, full, comp)
    return out


class Mc2Dat:
    def __init__(self, path):
        self.path, self.index = path, dave_entries(path)

    def read(self, name):
        fo, full, comp = self.index[name]
        with open(self.path, 'rb') as f:
            f.seek(fo)
            d = f.read(comp)
        return zlib.decompress(d, -15) if full != comp else d


class Writer:
    """Writes under ASSETS; a file that was there before this city's first
    install is kept once as <file>.before_<city>. What this tool wrote is
    logged as it goes (<hostfs>/<city>_install.log), so a second run neither
    backs up its own output nor loses track of it."""
    def __init__(self, hostfs, city, dry):
        self.hostfs, self.city, self.dry, self.written = hostfs, city, dry, []
        self.log = os.path.join(hostfs, city + '_install.log')
        self.ours = set(open(self.log).read().splitlines()) if os.path.exists(self.log) else set()

    def mine(self, rel):
        if rel not in self.ours and not self.dry:
            with open(self.log, 'a') as f:
                f.write(rel + '\n')
            self.ours.add(rel)
        self.written.append(rel)

    def keep(self, rel, dst):
        if rel not in self.ours and os.path.exists(dst) and not os.path.exists(dst + '.before_' + self.city):
            shutil.copy2(dst, dst + '.before_' + self.city)

    def put(self, rel, data):
        dst = os.path.join(self.hostfs, 'ASSETS', *rel.split('/'))
        if self.dry:
            print('  would write', rel, len(data))
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self.keep(rel, dst)
        with open(dst, 'wb') as f:
            f.write(data)
        self.mine(rel)


def is_text(b):
    head = b[:4096]
    printable = sum(32 <= x < 127 or x in (9, 10, 13) for x in head)
    return b'\0' not in head and printable >= 0.95 * min(len(b), 4096)


def lvl_extents(text):
    lo = re.search(r'extents_min\s*\{\s*(\S+)\s+(\S+)\s+(\S+)', text)
    hi = re.search(r'extents_max\s*\{\s*(\S+)\s+(\S+)\s+(\S+)', text)
    return tuple(map(float, lo.groups())), tuple(map(float, hi.groups()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--hostfs', required=True)
    ap.add_argument('--mc2-dat', help='MC2 PS2 ASSETS.DAT (default <hostfs>/MC2.DAT)')
    ap.add_argument('--mc2-tree', default=os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets'),
                    help="MC2 PS2 assets extracted (for the generators' --mc2)")
    ap.add_argument('--mc2-city', required=True, help='MC2 name: losangeles, paris, tokyo')
    ap.add_argument('--city', required=True, help='MC3 name the city is registered as')
    ap.add_argument('--code', required=True, help="the city's one-letter code")
    ap.add_argument('--label', required=True, help='name shown in menus')
    ap.add_argument('--template', default='paris', help='an installed MC2 city to copy')
    ap.add_argument('--template-code', default='p')
    ap.add_argument('--donor', default='atlanta', help='retail city the shells take their shader from')
    ap.add_argument('--hud-map', help="MC2 texture name (default hud_map_<mc2-city>.tex)")
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    dat = Mc2Dat(a.mc2_dat or os.path.join(a.hostfs, 'MC2.DAT'))
    assets = os.path.join(a.hostfs, 'ASSETS')
    w = Writer(a.hostfs, a.city, a.dry_run)
    tpl, city, mc2 = a.template, a.city, a.mc2_city

    # 1. the template's files, renamed
    skip = [re.compile(p) for p in (
        r'^tune/race/%s/' % tpl, r'^tune/race/%s\.(loc|locinf|override)' % tpl,
        r'^resources/city/%s_(dawn|dusk|midnight)_(clear|cloudy|rainy)\.pck$' % tpl,
        r'^city/%s/%s_city\.aib$' % (tpl, tpl), r'^city/%s/%s\.(algt|occlude|graph)$' % (tpl, tpl),
        r'^texture/hud_map_%s(_proxy)?\.tex$' % tpl, r'\.before_', r'\.bak$')]
    for root, dirs, files in os.walk(assets):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), assets).replace(os.sep, '/')
            if not re.search(r'(^|[/_])%s([/_.]|$)' % tpl, rel) or any(s.search(rel) for s in skip):
                continue
            b = open(os.path.join(root, fn), 'rb').read()
            if is_text(b):
                b = re.sub(rb'\b' + tpl.encode() + rb'\b', city.encode(), b)
                b = b.replace(tpl.encode() + b'_', city.encode() + b'_')
            w.put(re.sub(r'(^|[/_])%s(?=[/_.]|$)' % tpl, r'\g<1>' + city, rel), b)
    # 2. its code letter
    for rel in ('flash/loading_generic_%s.pck', 'flash/loading_network_%s.pck',
                'flash/loadscreen_arcade_%s.pck', 'flash/loadscreen_career_filler_%s.pck',
                'flash/loadscreen_garage_%s.pck', 'audio/banks/Ambient_%s.bnk',
                'audio/banks/ambient_%s.td'):
        src = os.path.join(assets, *(rel % a.template_code).split('/'))
        if os.path.exists(src):
            w.put(rel % a.code, open(src, 'rb').read())
    # 3. MC2's originals
    w.put('city/%s/%s_city.aib' % (city, city), dat.read('city/%s/%s.aib' % (mc2, mc2)))
    w.put('city/%s/%s.algt' % (city, city), dat.read('city/%s/%s.algt' % (mc2, mc2)))
    hud = dat.read('texture/' + (a.hud_map or 'hud_map_%s.tex' % mc2))
    w.put('texture/hud_map_%s.tex' % city, hud)
    w.put('texture/hud_map_%s_proxy.tex' % city, hud)
    # 4. a catalogue with Cruise only (the races come from mc2_races_to_mc3.py)
    cruise = 'cruise\\easy\\%s_cruise_easy_cruise_race01' % city
    w.put('tune/race/%s.loc' % city, ('City %s\r\n\r\nRace\r\n{\r\n\tName\t%s\r\n}\r\n'
                                      % (city, cruise)).encode())
    tinf = open(os.path.join(assets, 'tune', 'race', tpl + '.locinf'), encoding='latin1').read()
    block = re.search(r'cruise\\easy\\%s_cruise_easy_cruise_race01\s*\{.*?\n\}\s*\n' % tpl, tinf, re.S)
    w.put('tune/race/%s.locinf' % city, block.group(0).replace(tpl + '_', city + '_')
          .replace('\r\n', '\n').replace('\n', '\r\n').encode('latin1'))
    for ext in ('.rac', '.rinf'):
        src = os.path.join(assets, 'tune', 'race', tpl, 'cruise', 'easy',
                           '%s_cruise_easy_cruise_race01%s' % (tpl, ext))
        b = open(src, 'rb').read().replace(tpl.encode(), city.encode())
        w.put('tune/race/%s/cruise/easy/%s_cruise_easy_cruise_race01%s' % (city, city, ext), b)
    if a.dry_run:
        return
    # 5. generated: shells, occluders, pedestrian graph
    lo, hi = lvl_extents(dat.read('city/%s/%s.lvl' % (mc2, mc2)).decode('latin1'))
    for t in TIMES:
        for wx in WEATHERS:
            donor = os.path.join(assets, 'resources', 'city', '%s_%s_%s.pck' % (a.donor, t, wx))
            rel = 'resources/city/%s_%s_%s.pck' % (city, t, wx)
            out = os.path.join(a.hostfs, 'ASSETS', *rel.split('/'))
            w.keep(rel, out)
            subprocess.run([sys.executable, os.path.join(HERE, 'mc3_city_build.py'), '--donor', donor,
                            '--out-path', out, '--side', '1',
                            '--extents-min=%g,%g,%g' % lo, '--extents-max=%g,%g,%g' % hi],
                           check=True, stdout=subprocess.DEVNULL)
            w.mine(rel)
    occ = os.path.join(assets, 'city', city, city + '.occlude')
    subprocess.run([sys.executable, os.path.join(HERE, 'mc2_occluders.py'), '--city', mc2,
                    '--out', occ], check=True, stdout=subprocess.DEVNULL)
    w.mine('city/%s/%s.occlude' % (city, city))
    graph = os.path.join(assets, 'city', city, city + '.graph')
    subprocess.run([sys.executable, os.path.join(HERE, 'mc2_ped_graph.py'), '--city', mc2,
                    '--mc2', a.mc2_tree, '--name', a.code + '_graph', '--out', graph],
                   check=True, stdout=subprocess.DEVNULL)
    w.mine('city/%s/%s.graph' % (city, city))
    peds = os.path.join(assets, 'resources', 'city', city + '_peds.pck')
    if os.path.exists(peds):
        subprocess.run([sys.executable, os.path.join(HERE, 'mc3_peds_graph.py'), peds, graph],
                       check=True, stdout=subprocess.DEVNULL)
    json.dump({'city': city, 'mc2_city': mc2, 'code': a.code, 'label': a.label,
               'extents': [lo, hi], 'written': sorted(set(w.written))},
              open(os.path.join(a.hostfs, city + '_install.json'), 'w'), indent=1)
    print('%d files for %s (%s)' % (len(set(w.written)), city, mc2))


if __name__ == '__main__':
    main()
