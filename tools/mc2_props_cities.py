"""
mc2_props_cities.py - inventory of the props of EVERY Midnight Club 2 city

A prop is a thing that stands in the city and is not part of the block geometry:
street lights, trash cans, fences, fire hydrants, phone booths.

This sits beside `mc2_props.py` (Fork City MODS), which reads ONE city out of the
PC build and exports the placement matrices as JSON for the port. This one does
the wider job - all cities, both builds - and does not export matrices:

    python mc2_props_cities.py summary             # one table, every city
    python mc2_props_cities.py city NAME           # every template of one city
    python mc2_props_cities.py tsv  [--out DIR]    # props_templates.tsv
    python mc2_props_cities.py md   [--out FILE]   # Documentation/mc2_props_inventory.md
    python mc2_props_cities.py check               # the structural self-tests

Every number in the report is measured, and `check` re-runs the assertions the
reading rests on.

THE FILES
    city/<c>/<c>.prop      the placements, plus one `prop_template` per type
    city/<c>/<name>.pdef   one per template: LOD list, bounding sphere, lights
    resource/<c>/ambients_*.rsc   PS2 compiled form: PRP0 / BND0 / MOD0
    resource/<c>/race/*.rsc       PS2: props that a RACE spawns, not the city
    bound/<name>.bnd  collision   tune/banger/<name>.bangerdata  tune/phys/<name>.phys
    city/<c>/models/<name>_<lod>.xmod   the PC body meshes

THE `.prop` FILE
    A four-line header, the templates, then THREE runs of `prop N {` that each
    restart at 0 and carry no header of their own:

        prop_count: N         run 1 - dynamic (`FixedObject`/`GfxOnly` both 0)
        fixed_prop_count: N   run 2 - `FixedObject: 1`, most also `Obstacle: 1`
        gfx_prop_count: N     run 3 - `GfxOnly: 1`, drawing only
        num_prop_types: N     how many `prop_template` blocks there are

    `prop_count` is the size of run 1 ONLY. Los Angeles on the PS2 has
    3886 + 689 + 1120 = 5695 placements and the header says 3886.

THE PS2 COMPILED FORM
    A prop's `PRP0` block begins with two handles: the LOD-0 and LOD-2 body,
    each a `MOD0` or 0, one or two entries BEFORE the PRP0. The `.pdef` gives
    the number of LODs and the PRP0 has exactly that many handles.

    Body meshes are `<name>_<lod>` with LOD numbers 0 and 2, never 1, so a
    search for `<name>.xmod` finds nothing.

"NEVER PLACED" IS THREE DIFFERENT THINGS
    A template with no placement in the `.prop` is either placed by a RACE (its
    PRP0/BND0 are in `race/*.rsc`), or defined in the city containers but placed
    by nothing, or found nowhere. The first draft called all three "unused".
"""

import argparse
import collections
import hashlib
import math
import os
import re
import struct
import sys

import mc2_rsc as M
import mc3_output

PS2_DEFAULT = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
PC_DEFAULT = os.environ.get('MC2_PC_ASSETS', 'mc2_pc/assets_p')
MC3_DEFAULT = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS')

CITIES = ('losangeles', 'paris', 'tokyo')
KINDS = ('dynamic', 'fixed', 'gfx')          # the order the three runs appear in
FLAGS = ('FixedObject', 'Obstacle', 'GfxOnly', 'Drivable', 'Far')

NUM = r'(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)'
_ROW = re.compile(r'^\t\t' + r'\t'.join([NUM] * 3) + r'\s*$', re.M)


def _out_path(path):
    """Resolve against the toolkit's output/ unless a path was named, and make
    sure the folder exists."""
    path = mc3_output.path(path)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    return path


# ---------------------------------------------------------------------------
# Text files
# ---------------------------------------------------------------------------

class PropFile(object):
    """A parsed `<city>.prop`."""

    def __init__(self):
        self.header = {}
        self.templates = collections.OrderedDict()     # name -> dict
        self.instances = []                            # dicts, all three runs


def read_prop(path):
    with open(path, encoding='ascii', errors='replace') as fh:
        text = fh.read().replace('\r\n', '\n')
    pf = PropFile()
    for key in ('prop_count', 'fixed_prop_count', 'gfx_prop_count', 'num_prop_types'):
        m = re.search(r'^%s:\s*(\d+)' % key, text, re.M)
        pf.header[key] = int(m.group(1)) if m else None

    # `^\}` closes a template even when it holds nested `part N { }` blocks:
    # those are indented, so only the template's own brace is at column 0.
    for m in re.finditer(r'^prop_template (\S+) \{\n(.*?)^\}', text, re.S | re.M):
        body = m.group(2)
        t = {'name': m.group(1)}
        for flag in FLAGS + ('animation',):
            mm = re.search(r'^\t%s:\s*(-?\d+)' % flag, body, re.M)
            t[flag] = int(mm.group(1)) if mm else 0
        mm = re.search(r'^\tnumparts:\s*(\d+)', body, re.M)
        t['numparts'] = int(mm.group(1)) if mm else 0
        t['parts'] = re.findall(r'^\t\tname:\s*(\S+)', body, re.M)
        pf.templates[m.group(1)] = t

    run, prev = -1, None
    for m in re.finditer(r'^prop (\d+) \{\n(.*?)^\}', text, re.S | re.M):
        n = int(m.group(1))
        if prev is None or n <= prev:              # a new run starts at 0 again
            run += 1
        prev = n
        body = m.group(2)
        rows = [[float(v) for v in r] for r in _ROW.findall(body)]
        tm = re.search(r'prop_template:\s*(\S+)', body)
        pf.instances.append({
            'kind': KINDS[run] if run < len(KINDS) else 'run%d' % run,
            'index': n,
            'template': tm.group(1) if tm else '',
            'rot': rows[:3], 'pos': rows[3] if len(rows) == 4 else None,
        })
    return pf


def read_pdef(path):
    with open(path, encoding='ascii', errors='replace') as fh:
        text = fh.read().replace('\r\n', '\n')
    d = {}
    # `[ \t]`, not `\s`: an EMPTY list is written `lods: 0 :` and `\s*` would run
    # on into the next line and read `sphere:` as the list.
    m = re.search(r'^lods:[ \t]*(\d+)[ \t]*:[ \t]*([^\n]*)$', text, re.M)
    d['lods'] = [int(x) for x in m.group(2).split()] if m else []
    d['nlods'] = int(m.group(1)) if m else 0
    m = re.search(r'^sphere:\s*' + r'\s+'.join([NUM] * 4), text, re.M)
    d['sphere'] = tuple(float(x) for x in m.groups()) if m else None
    m = re.search(r'^num_lights:\s*(\d+)', text, re.M)
    d['lights'] = int(m.group(1)) if m else 0
    return d


def read_rnt(path):
    """[(fourcc, name)] from a `.rnt`, without loading the `.rsc` beside it."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:4] != b'RNT0':
        return []
    out = []
    for i in range(struct.unpack_from('<I', d, 4)[0]):
        soff, fcc, handle = struct.unpack_from('<I4sI', d, 0x18 + 12 * i)
        out.append((fcc.decode('ascii', 'replace'),
                    d[soff:d.index(b'\0', soff)].decode('ascii', 'replace')))
    return out


def stems(folder, ext):
    """{lowercase stem: filename} for the files of one extension."""
    if not os.path.isdir(folder):
        return {}
    return {os.path.splitext(f)[0].lower(): f for f in os.listdir(folder)
            if f.lower().endswith(ext)}


def family_of(name):
    """`l_prop_streetlight_01x` -> `streetlight_01x`: the city prefix removed, so
    the same prop can be recognised across cities."""
    m = re.match(r'^[a-z0-9]+_prop_(.*)$', name, re.I)
    return (m.group(1) if m else name).lower()


# ---------------------------------------------------------------------------
# One city
# ---------------------------------------------------------------------------

class City(object):
    """Everything known about one city's props, from both builds."""

    def __init__(self, name, ps2, pc):
        self.name = name
        self.ps2_root, self.pc_root = ps2, pc
        self.ps2 = self._prop(ps2, name)
        self.pc = self._prop(pc, name)
        self.main = self.ps2 or self.pc
        folder = os.path.join(ps2, 'city', name)
        if not os.path.isdir(folder):
            folder = os.path.join(pc, 'city', name)
        self.pdef = {stem: read_pdef(os.path.join(folder, f))
                     for stem, f in stems(folder, '.pdef').items()}
        self._ps2_side()
        self._pc_side()
        self.tune = {sub: stems(os.path.join(ps2, 'tune', sub), ext)
                     for sub, ext in (('banger', '.bangerdata'), ('phys', '.phys'))}
        self.bound = stems(os.path.join(ps2, 'bound'), '.bnd')

    @staticmethod
    def _prop(root, name):
        p = os.path.join(root, 'city', name, name + '.prop')
        return read_prop(p) if os.path.isfile(p) else None

    def _ps2_side(self):
        """The PRP0 -> body MOD0 join, and who else defines a template."""
        self.prp = {}                # lowercase name -> (n MOD0 handles, their bytes)
        self.ambients_fcc = collections.defaultdict(set)
        self.race = collections.defaultdict(set)
        self.n_ambients = self.n_race = 0
        rdir = os.path.join(self.ps2_root, 'resource', self.name)
        if not os.path.isdir(rdir):
            return
        amb = sorted(f for f in os.listdir(rdir)
                     if f.startswith('ambients_') and f.endswith('.rsc'))
        self.n_ambients = len(amb)
        if amb:
            pick = 'ambients_midnight.rsc' if 'ambients_midnight.rsc' in amb else amb[0]
            r = M.Rsc(os.path.join(rdir, pick))
            for i, (off, fcc, size) in enumerate(r.entries):
                nm = r.name(i)
                if nm:
                    self.ambients_fcc[nm.lower()].add(fcc)
                if fcc != 'PRP0' or not nm:
                    continue
                n = total = 0
                for h in struct.unpack_from('<II', r.data, off):
                    j = (h & 0x3FFF) - 1
                    if h and 0 <= j < len(r.entries) and r.entries[j][1] == 'MOD0':
                        n += 1
                        total += r.entries[j][2]
                self.prp[nm.lower()] = (n, total)
        race_dir = os.path.join(rdir, 'race')
        if os.path.isdir(race_dir):
            for f in os.listdir(race_dir):
                if f.endswith('.rnt'):
                    self.n_race += 1
                    for fcc, nm in read_rnt(os.path.join(race_dir, f)):
                        self.race[nm.lower()].add(f[:-4])

    def _pc_side(self):
        self.pc_lods = collections.defaultdict(set)
        models = os.path.join(self.pc_root, 'city', self.name, 'models')
        if os.path.isdir(models):
            for f in os.listdir(models):
                m = re.match(r'^(.*)_(\d+)\.xmod$', f, re.I)
                if m:
                    self.pc_lods[m.group(1).lower()].add(int(m.group(2)))

    # -- derived ----------------------------------------------------------
    @staticmethod
    def counts(pf):
        c = collections.Counter()
        for i in pf.instances:
            c[(i['template'].lower(), i['kind'])] += 1
        return c

    def role(self, low, placed):
        """placed / race / ambients_only / none - see the module docstring."""
        if placed:
            return 'placed'
        if self.ps2 is None:
            return 'unknown'          # no PS2 containers to search: say so, not 'none'
        if self.race.get(low):
            return 'race'
        if self.ambients_fcc.get(low):
            return 'ambients_only'
        return 'none'

    def rows(self):
        pf = self.main
        if pf is None:
            return []
        cps = self.counts(pf)
        cpc = self.counts(self.pc) if self.pc else None
        out = []
        for name, t in pf.templates.items():
            low = name.lower()
            by = {k: cps.get((low, k), 0) for k in KINDS}
            total = sum(by.values())
            d = self.pdef.get(low)
            n_mod, mod_bytes = self.prp.get(low, (None, None))
            parts = t['parts']
            row = collections.OrderedDict()
            row['city'] = self.name
            row['template'] = name
            row['family'] = family_of(name)
            row['role'] = self.role(low, total > 0)
            row['dynamic'], row['fixed'], row['gfx'] = by['dynamic'], by['fixed'], by['gfx']
            row['total'] = total
            row['pc_total'] = (sum(cpc.get((low, k), 0) for k in KINDS)
                               if cpc is not None else '')
            for f in ('Obstacle', 'Drivable', 'Far', 'FixedObject', 'GfxOnly', 'animation'):
                row[f.lower()] = t[f]
            row['breakparts'] = sum(1 for p in parts if 'breakpart' in p.lower())
            row['particles'] = sum(1 for p in parts if 'particle' in p.lower())
            row['lights'] = d['lights'] if d else ''
            row['lods'] = ','.join(map(str, d['lods'])) if d else ''
            row['radius'] = ('%.2f' % d['sphere'][3]) if d and d['sphere'] else ''
            row['races'] = len(self.race.get(low, ()))
            row['ps2_prp0'] = int(low in self.prp)
            row['ps2_body_mod0'] = n_mod if n_mod is not None else ''
            row['ps2_body_bytes'] = mod_bytes if mod_bytes is not None else ''
            row['ps2_bnd0'] = int('BND0' in self.ambients_fcc.get(low, ()))
            row['bound_bnd'] = int(low in self.bound)
            row['banger'] = int(low in self.tune['banger'])
            row['phys'] = int(low in self.tune['phys'])
            row['pc_lods'] = ','.join(map(str, sorted(self.pc_lods.get(low, ()))))
            out.append(row)
        return out

    def stats(self):
        """Placement facts about the instances themselves."""
        pf = self.main
        s = collections.Counter()
        if pf is None:
            return s
        seen = collections.Counter()
        xs, zs = [], []
        for i in pf.instances:
            r, p = i['rot'], i['pos']
            if len(r) != 3 or p is None:
                s['malformed'] += 1
                continue
            s['identity'] += all(abs(r[a][b] - (1.0 if a == b else 0.0)) < 1e-6
                                 for a in range(3) for b in range(3))
            det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
                   - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
                   + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
            s['mirrored'] += det < 0
            norms = [math.dist((0, 0, 0), row) for row in r]
            s['scaled'] += (max(norms) - 1.0 > 1e-4 or 1.0 - min(norms) > 1e-4)
            seen[(i['template'].lower(), round(p[0], 3), round(p[1], 3), round(p[2], 3))] += 1
            xs.append(p[0])
            zs.append(p[2])
        s['duplicates'] = sum(v - 1 for v in seen.values() if v > 1)
        if xs:
            s['x0'], s['x1'], s['z0'], s['z1'] = min(xs), max(xs), min(zs), max(zs)
        return s


    def placement_detail(self):
        """Which templates carry a scale, and what the same-position pairs are.

        Returns (scaled, doubles, pairs): `scaled` maps template -> (count, lowest
        row length, highest, how many are uniform); `doubles` counts same-position
        pairs whose rotation is ALSO identical (a real double placement); `pairs`
        maps template -> same-position pairs of any rotation.
        """
        scaled = {}
        at = collections.defaultdict(list)
        for i in (self.main.instances if self.main else []):
            r, p = i['rot'], i['pos']
            if len(r) != 3 or p is None:
                continue
            n = [math.dist((0, 0, 0), row) for row in r]
            if max(n) - 1.0 > 1e-4 or 1.0 - min(n) > 1e-4:
                c, lo, hi, uni = scaled.get(i['template'], (0, 9e9, 0.0, 0))
                scaled[i['template']] = (c + 1, min(lo, min(n)), max(hi, max(n)),
                                         uni + (max(n) - min(n) < 1e-3))
            at[(i['template'].lower(), round(p[0], 3), round(p[1], 3), round(p[2], 3))].append(r)
        groups = {k: v for k, v in at.items() if len(v) > 1}
        same_rot = sum(1 for v in groups.values() if all(x == v[0] for x in v))
        extra = sum(len(v) - 1 for v in groups.values())
        biggest = max(((len(v), k[0]) for k, v in groups.items()), default=(0, ''))
        by_tpl = collections.Counter(k[0] for k in groups)
        return scaled, (len(groups), extra, same_rot, biggest), by_tpl


def load_all(ps2, pc):
    names = list(CITIES)
    extra = os.path.join(pc, 'city')
    if os.path.isdir(extra):                        # e.g. `test2`, PC only
        for c in sorted(os.listdir(extra)):
            if c not in names and os.path.isfile(os.path.join(extra, c, c + '.prop')):
                names.append(c)
    return [City(c, ps2, pc) for c in names]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _roles(rows):
    return collections.Counter(r['role'] for r in rows)


def cmd_summary(args):
    print('%-11s %-4s %6s | %7s %6s %6s %7s | %7s %5s %9s %5s'
          % ('city', 'src', 'kinds', 'dynam.', 'fixed', 'gfx', 'total',
             'placed', 'race', 'amb_only', 'none'))
    for c in load_all(args.ps2, args.pc):
        for tag, pf in (('PS2', c.ps2), ('PC', c.pc)):
            if pf is None:
                continue
            k = collections.Counter(i['kind'] for i in pf.instances)
            line = '%-11s %-4s %6d | %7d %6d %6d %7d |' % (
                c.name, tag, len(pf.templates), k['dynamic'], k['fixed'], k['gfx'],
                len(pf.instances))
            if tag == 'PS2':
                r = _roles(c.rows())
                line += ' %7d %5d %9d %5d' % (r['placed'], r['race'],
                                              r['ambients_only'], r['none'])
            print(line)
    return 0


def cmd_city(args):
    cities = {c.name: c for c in load_all(args.ps2, args.pc)}
    if args.name not in cities:
        sys.exit('unknown city: %s (%s)' % (args.name, ', '.join(cities)))
    rows = cities[args.name].rows()
    rows.sort(key=lambda r: (-r['total'], r['template'].lower()))
    print('%-40s %-13s %5s %5s %5s %5s | %-4s %2s %2s %3s'
          % ('template', 'role', 'dyn', 'fix', 'gfx', 'PC', 'lods', 'bk', 'pt', 'lgt'))
    for r in rows:
        if args.filter_ and args.filter_.lower() not in r['template'].lower():
            continue
        print('%-40s %-13s %5d %5d %5d %5s | %-4s %2d %2d %3s'
              % (r['template'][:40], r['role'], r['dynamic'], r['fixed'], r['gfx'],
                 r['pc_total'], r['lods'], r['breakparts'], r['particles'], r['lights']))
    return 0


def cmd_tsv(args):
    rows = []
    for c in load_all(args.ps2, args.pc):
        rows.extend(c.rows())
    out = args.out or os.path.join(mc3_output.folder(), 'mc2_props')
    path = _out_path(out if out.endswith('.tsv') else os.path.join(out, 'props_templates.tsv'))
    cols = list(rows[0].keys())
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\t'.join(cols) + '\n')
        for r in rows:
            fh.write('\t'.join(str(r[c]) for c in cols) + '\n')
    print('%s  %d lines, %d cities' % (path, len(rows), len({r['city'] for r in rows})))
    return 0


def cmd_check(args):
    fails = []

    def test(label, ok, detail=''):
        print('  %-60s %s%s' % (label, 'PASS' if ok else 'FAIL',
                                ('   ' + detail) if detail else ''))
        if not ok:
            fails.append(label)

    for c in load_all(args.ps2, args.pc):
        print('=== %s ===' % c.name)
        for tag, pf in (('PS2', c.ps2), ('PC', c.pc)):
            if pf is None:
                continue
            k = collections.Counter(i['kind'] for i in pf.instances)
            h = pf.header
            test('%s: header == count of the 3 runs and of the kinds' % tag,
                 (h['prop_count'], h['fixed_prop_count'], h['gfx_prop_count'],
                  h['num_prop_types'])
                 == (k['dynamic'], k['fixed'], k['gfx'], len(pf.templates)),
                 '%d + %d + %d = %d, %d kinds'
                 % (k['dynamic'], k['fixed'], k['gfx'], len(pf.instances), len(pf.templates)))
        pf = c.main
        low = {n.lower() for n in pf.templates}
        test('every instance points to a defined template',
             all(i['template'].lower() in low for i in pf.instances),
             '%d instances' % len(pf.instances))
        test('.pdef and template match 1:1 (case-insensitive)', set(c.pdef) == low,
             '%d pdef, %d templates' % (len(c.pdef), len(low)))

        bad = collections.Counter()
        for i in pf.instances:
            t = pf.templates.get(i['template'])
            if t is None:
                continue
            if i['kind'] == 'fixed' and not t['FixedObject']:
                bad['fixed without FixedObject'] += 1
            if i['kind'] == 'gfx' and not t['GfxOnly']:
                bad['gfx without GfxOnly'] += 1
            if i['kind'] == 'dynamic' and (t['FixedObject'] or t['GfxOnly']):
                bad['dynamic with a flag of another class'] += 1
        test('the run of each instance matches the template flags',
             not bad, str(dict(bad)) if bad else 'runs %s' % ', '.join(KINDS))

        if c.ps2 is None or not c.prp:
            print('  (no PS2/ambients: PS2 tests skipped)')
            continue
        both = [n for n in c.prp if n in c.pdef]
        ok = sum(1 for n in both if c.pdef[n]['nlods'] == c.prp[n][0])
        test('number of .pdef LODs == number of MOD0 in the PRP0', ok == len(both),
             '%d of %d' % (ok, len(both)))

        unplaced = low - {i['template'].lower() for i in pf.instances}
        noprp = low - set(c.prp)
        test('never placed <=> no PRP0 in the ambients', unplaced == noprp,
             '%d and %d, %d on one side only' % (len(unplaced), len(noprp), len(unplaced ^ noprp)))

        rdir = os.path.join(args.ps2, 'resource', c.name)
        sets = {frozenset(nm.lower() for fcc, nm in read_rnt(os.path.join(rdir, f[:-4] + '.rnt'))
                          if fcc == 'PRP0')
                for f in os.listdir(rdir) if f.startswith('ambients_') and f.endswith('.rsc')}
        test('same PRP0 set in every ambients variant', len(sets) == 1,
             '%d variants, %d sets' % (c.n_ambients, len(sets)))

        # Informational, not pass/fail: the two builds are different revisions.
        print('  role of the %d templates: %s' % (len(low), dict(_roles(c.rows()))))
        div = [(n, c.pdef[n]['lods'], sorted(c.pc_lods.get(n, ())))
               for n in sorted(c.pdef) if set(c.pdef[n]['lods']) != c.pc_lods.get(n, set())]
        print('  INFO  .pdef LODs != PC .xmod: %d  %s' % (len(div), div[:3]))
        if c.pc:
            a, b = c.counts(c.ps2), c.counts(c.pc)
            print('  INFO  (run, template) with a different PS2 x PC count: %d'
                  % sum(1 for k in set(a) | set(b) if a.get(k, 0) != b.get(k, 0)))
    print('\n%d tests failed' % len(fails))
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# The markdown report
# ---------------------------------------------------------------------------

def _mc3_section(args, cities):
    """What the MC3 tree has, measured live so a re-run stays true."""
    out = ['## MC3 today', '']
    root = args.mc3
    if not os.path.isdir(root):
        return out + ['`%s` not found; nothing measured.' % root, '']
    model = os.path.join(root, 'model')
    props = sorted(f for f in os.listdir(model)
                   if re.match(r'^[a-z0-9]+_prop_', f, re.I)) if os.path.isdir(model) else []
    out.append('Loose prop models in `ASSETS/model/`: **%d**.' % len(props))

    def digest(p):
        with open(p, 'rb') as fh:
            return hashlib.md5(fh.read().replace(b'\r\n', b'\n')).hexdigest()

    for f in props:
        stem = re.sub(r'\.mod$', '', f, flags=re.I)
        src = next((p for p in (os.path.join(args.pc, 'city', c.name, 'models', stem + '.xmod')
                                for c in cities) if os.path.isfile(p)), None)
        how = ('no PC `.xmod` of that name' if not src else
               'byte-identical to the PC `.xmod` (line endings normalised)'
               if digest(src) == digest(os.path.join(model, f))
               else 'differs from the PC `.xmod`')
        out.append('- `%s` - %s' % (f, how))
    banger = sum(1 for _, _, fs in os.walk(root) for f in fs if f.lower().endswith('.bangerdata'))
    out += ['', 'Files named `*.bangerdata` anywhere under `ASSETS/`: **%d**. MC2 gives every '
            'breakable prop one in `tune/banger/`.' % banger]
    city_dir = os.path.join(root, 'resources', 'city')
    pcks = sorted(f for f in os.listdir(city_dir)
                  if 'prop' in f.lower() and 'midnight_clear' in f.lower()) \
        if os.path.isdir(city_dir) else []
    out += ['', 'Prop `.pck` files in `resources/city/` (midnight/clear only): %s. Their '
            'contents were not inspected by this tool; `mc2_props.py` (Fork City MODS) '
            'reports that the installed `losangeles` ones are Tokyo\'s.'
            % (', '.join('`%s`' % p for p in pcks) or 'none'), '']
    return out


def cmd_md(args):
    cities = load_all(args.ps2, args.pc)
    L = ['# MC2 props by city', '',
         'Generated by `mc2_props_cities.py`. Sources: `%s` (PS2) and `%s` (PC).'
         % (args.ps2, args.pc), '',
         'For the placement matrices of one city as JSON, see `mc2_props.py`.', '',
         '## What a prop is, and where each part lives', '',
         'The placements are in `city/<c>/<c>.prop`, split into THREE runs that restart at 0: '
         'dynamic, fixed and graphics-only. The header field `prop_count` counts only the '
         'first run.', '',
         "A template's body meshes are `<name>_0` and `<name>_2` (LOD 0 and 2, never 1), so a "
         'search for `<name>.xmod` finds nothing. On the PS2 the same thing is a `PRP0` block in '
         '`ambients_*.rsc` whose first two handles are the LOD `MOD0` blocks; the number of '
         'handles equals the number of LODs in the `.pdef`, on every template of every city '
         '(`check`).', '',
         '## Totals', '',
         '| city | build | templates | dynamic | fixed | gfx-only | placements |',
         '|---|---|---|---|---|---|---|']
    for c in cities:
        for tag, pf in (('PS2', c.ps2), ('PC', c.pc)):
            if pf is None:
                continue
            k = collections.Counter(i['kind'] for i in pf.instances)
            L.append('| `%s` | %s | %d | %d | %d | %d | %d |' % (
                c.name, tag, len(pf.templates), k['dynamic'], k['fixed'], k['gfx'],
                len(pf.instances)))
    L += ['', '`test2` exists only in the PC build and only as dynamic props.', '',
          '## Where each template is defined', '',
          'A template with no placement is not necessarily unused. There are three cases:', '',
          '- `race` - a race places it; its `PRP0`/`BND0` are inside `race/*.rsc`',
          '- `ambients_only` - present in the ambients containers, placed by nothing',
          '- `none` - found in no container of that city', '',
          '| city | placed | race | ambients_only | none |', '|---|---|---|---|---|']
    ps2_cities = [c for c in cities if c.ps2 is not None]
    for c in ps2_cities:
        r = _roles(c.rows())
        L.append('| `%s` | %d | %d | %d | %d |' % (
            c.name, r['placed'], r['race'], r['ambients_only'], r['none']))
    L.append('')

    for c in ps2_cities:
        rows = c.rows()
        L += ['## `%s`' % c.name, '']
        un = sorted((r for r in rows if r['role'] != 'placed'), key=lambda r: r['template'].lower())
        L.append('%d of %d templates are never placed by the `.prop`.' % (len(un), len(rows)))
        L.append('')
        for role in ('race', 'ambients_only', 'none'):
            items = []
            for r in un:
                if r['role'] != role:
                    continue
                items.append('`%s`' % r['template'] if role != 'race' else
                             '`%s` (%d race%s)' % (r['template'], r['races'],
                                                   '' if r['races'] == 1 else 's'))
            if items:
                L.append('- **%s** (%d): %s' % (role, len(items), ', '.join(items)))
        L.append('')
        top = sorted((r for r in rows if r['total']), key=lambda r: -r['total'])[:12]
        L += ['Most placed:', '',
              '| template | placements | runs | lods | breakable parts |', '|---|---|---|---|---|']
        for r in top:
            L.append('| `%s` | %d | %s | %s | %d |' % (
                r['template'], r['total'], '/'.join(k for k in KINDS if r[k]),
                r['lods'] or '-', r['breakparts']))
        L.append('')
        s = c.stats()
        L += ['Placement facts (PS2 `.prop`): %d identity rotations, %d mirrored (negative '
              'determinant), %d scaled, %d duplicates (same template at the same position). '
              'Extent x %.0f..%.0f, z %.0f..%.0f.'
              % (s['identity'], s['mirrored'], s['scaled'], s['duplicates'],
                 s['x0'], s['x1'], s['z0'], s['z1']), '']
        scaled, (n_groups, n_extra, n_same_rot, biggest), by_tpl = c.placement_detail()
        if scaled:
            n_all = sum(v[0] for v in scaled.values())
            n_uni = sum(v[3] for v in scaled.values())
            L += ['Scale: %d matrices in %d templates, %d of them uniform (%s).'
                  % (n_all, len(scaled), n_uni,
                     ', '.join('`%s` x%d at %.3f' % (t, v[0], v[2]) if v[2] - v[1] < 1e-3
                               else '`%s` x%d, %.3f..%.3f' % (t, v[0], v[1], v[2])
                               for t, v in sorted(scaled.items(), key=lambda kv: -kv[1][0]))), '']
        if n_groups:
            anim = c.main.templates.get(biggest[1], {}).get('animation') if biggest[1] else 0
            L += ['Same-position groups (one template, one position, several placements): %d, '
                  'adding %d placements beyond the first of each; %d of the groups also repeat '
                  'the rotation. Largest: `%s` x%d%s. By template: %s.'
                  % (n_groups, n_extra, n_same_rot, biggest[1], biggest[0],
                     ' (`animation: 1`)' if anim else '',
                     ', '.join('`%s` %d' % (t, n) for t, n in by_tpl.most_common(6))), '']
        if c.pc:
            a, b = c.counts(c.ps2), c.counts(c.pc)
            dif = sorted((k, b.get(k, 0), a.get(k, 0)) for k in set(a) | set(b)
                         if a.get(k, 0) != b.get(k, 0))
            if dif:
                L += ['PS2 and PC placement counts differ for %d (run, template) pairs:' % len(dif), '']
                L += ['- `%s` (%s): PC %d, PS2 %d (%+d)' % (t, kind, pcn, ps2n, ps2n - pcn)
                      for (kind, t), pcn, ps2n in dif]
                L.append('')
        div = [(n, c.pdef[n]['lods'], sorted(c.pc_lods.get(n, ())))
               for n in sorted(c.pdef) if set(c.pdef[n]['lods']) != c.pc_lods.get(n, set())]
        if div:
            L += ["The `.pdef` LOD list differs from the PC `.xmod` files for %d templates:" % len(div), '']
            L += ['- `%s`: pdef %s, PC xmod %s' % d for d in div]
            L.append('')

    fam = collections.defaultdict(set)
    for c in cities:
        if c.name in CITIES and c.main:
            for name in c.main.templates:
                fam[family_of(name)].add(c.name)
    counts = collections.Counter(len(v) for v in fam.values())
    L += ['## Props shared across the three cities', '',
          'Comparing names with the city prefix removed (`l_prop_streetlight_01x` and '
          '`p_prop_streetlight_01x` are one family): **%d** families exist in all three cities, '
          '**%d** in two, **%d** in one. Same name is not the same mesh; only the name was '
          'compared.' % (counts[3], counts[2], counts[1]), '']
    la = next((c for c in cities if c.name == 'losangeles'), None)
    if la and la.main:
        only = [n for n in la.main.templates if len(fam[family_of(n)]) == 1]
        L += ['Los Angeles has %d props whose family exists in no other city.' % len(only), '']
    L += _mc3_section(args, cities)

    path = _out_path(args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           'Documentation', 'mc2_props_inventory.md'))
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(L) + '\n')
    print('%s  (%d lines)' % (path, len(L)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--ps2', default=PS2_DEFAULT, help='assets/ root of MC2 PS2')
    ap.add_argument('--pc', default=PC_DEFAULT, help='assets_p/ root of MC2 PC')
    ap.add_argument('--mc3', default=MC3_DEFAULT, help='ASSETS/ root of MC3')
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('summary')
    sub.add_parser('check')
    p = sub.add_parser('city')
    p.add_argument('name')
    p.add_argument('--filter', dest='filter_')
    for name in ('tsv', 'md'):
        p = sub.add_parser(name)
        p.add_argument('--out')
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    return {'summary': cmd_summary, 'city': cmd_city, 'tsv': cmd_tsv,
            'md': cmd_md, 'check': cmd_check}[args.cmd](args) or 0


if __name__ == '__main__':
    sys.exit(main())
