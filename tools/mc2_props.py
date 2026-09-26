"""mc2_props.py - inventory of the props of a Midnight Club 2 city.

MC2 props do not live in the .hood files: they have a file of their own,
<city>.prop, with three parts in TEXT:

    prop_count / fixed_prop_count / gfx_prop_count / num_prop_types
    prop_template <name> { animation numparts FixedObject Obstacle
                           GfxOnly Drivable Far }        -- one per KIND
    prop <n> { matrix { 4 rows of 3 floats } prop_template: <name> }

The matrix is the same Matrix34 as the rest of the port: three rows of rotation
and one of translation. Each kind also has a .pdef beside it (name, lods,
sphere) and <name>_<lod>.xmod models in the models folder.

Usage:
    python mc2_props.py --city <.../city/losangeles> --out-path output/la_props
"""
import argparse
import collections
import json
import os
import re


def read_prop(path):
    """(header, kinds, placements) of <city>.prop.

    Two details that only show up reading the whole file:

    - There are THREE placement lists, each renumbering from zero: the normal
      ones (prop_count), the fixed ones (fixed_prop_count) and the
      graphics-only ones (gfx_prop_count). A `prop 0` after the first one opens
      the next list.
    - A template with `numparts` greater than zero contains NESTED `part N {
      name, offset }` blocks, and the flags (FixedObject, Obstacle, GfxOnly,
      Drivable, Far) come AFTER them. Closing the template at the first `}`
      loses the flags of 59 of LA's 96 kinds, and they come out as unknown
      instead of raising an error - which is why the brace depth is counted.
    """
    hdr, kinds, placements = {}, {}, []
    current_name = None
    matrix = None
    depth = 0
    part = None
    sections = ['normal', 'fixed', 'gfx']
    section = 0
    seen_zero = False
    for line in open(path, encoding='latin1'):
        t = line.strip()
        if not t:
            continue
        if t == '}':
            depth = max(0, depth - 1)
            if depth == 0:
                current_name = None
                matrix = None
            elif depth == 1:
                part = None
            continue
        if t.endswith('{'):
            depth += 1
            g = re.match(r'^prop_template\s+(\S+)\s*\{', t)
            if g:
                current_name = g.group(1)
                kinds[current_name] = {'parts': []}
                continue
            g = re.match(r'^prop\s+(\d+)\s*\{', t)
            if g:
                if int(g.group(1)) == 0:
                    if seen_zero:
                        section += 1
                    seen_zero = True
                matrix = []
                continue
            if re.match(r'^part\s+\d+\s*\{', t) and current_name:
                part = {}
                kinds[current_name]['parts'].append(part)
            continue
        g = re.match(r'^(prop_count|fixed_prop_count|gfx_prop_count|'
                     r'num_prop_types):\s*(\d+)', t)
        if g:
            hdr[g.group(1)] = int(g.group(2))
            continue
        g = re.match(r'^prop_template:\s*(\S+)', t)
        if g and matrix is not None:
            placements.append((g.group(1),
                               matrix[-4:] if len(matrix) >= 4 else matrix,
                               sections[min(section, len(sections) - 1)]))
            matrix = None
            continue
        g = re.match(r'^(\w+):\s*(.+?)\s*$', t)
        if g and current_name:
            field, value = g.group(1), g.group(2)
            if part is not None:
                part[field] = value
            else:
                kinds[current_name][field] = value
            continue
        if matrix is not None:
            n = t.split()
            if len(n) == 3:
                try:
                    matrix.append(tuple(float(v) for v in n))
                except ValueError:
                    pass
    return hdr, kinds, placements


def read_pdef(path):
    """name / lods / sphere of a .pdef."""
    outside = {}
    for line in open(path, encoding='latin1'):
        t = line.strip()
        g = re.match(r'^name:\s*(\S+)', t)
        if g:
            outside['name'] = g.group(1)
        g = re.match(r'^lods:\s*(\d+)\s*:\s*([\d ]*)', t)
        if g:
            outside['n_lods'] = int(g.group(1))
            outside['lods'] = [int(v) for v in g.group(2).split()]
        g = re.match(r'^sphere:\s*(.+)', t)
        if g:
            v = g.group(1).split()
            if len(v) == 4:
                outside['sphere'] = [float(x) for x in v]
    return outside


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--city', required=True, help='the .../city/<name> folder')
    p.add_argument('--out-path', required=True, help='prefix of the output files')
    a = p.parse_args(argv)

    city_name = os.path.basename(a.city.rstrip('/\\'))
    prop = os.path.join(a.city, city_name + '.prop')
    models_dir = os.path.join(a.city, 'models')
    hdr, kinds, placements = read_prop(prop)
    print('%s.prop: %s' % (city_name, hdr))
    print('  templates read: %d   placements read: %d' % (len(kinds), len(placements)))

    available = {f.lower() for f in os.listdir(models_dir)} if os.path.isdir(models_dir) else set()
    usage_count = collections.Counter(n.lower() for n, _, _ in placements)
    by_section = collections.Counter(s for _, _, s in placements)
    print('  per section: %s' % dict(by_section))

    lines = []
    for name in sorted(kinds):
        t = kinds[name]
        pdef = os.path.join(a.city, name + '.pdef')
        d = read_pdef(pdef) if os.path.isfile(pdef) else {}
        lods = d.get('lods', [])
        found = [l for l in lods
                   if ('%s_%d.xmod' % (name, l)).lower() in available]
        lines.append({
            'name': name,
            'instances': usage_count.get(name.lower(), 0),
            'FixedObject': t.get('FixedObject', '?'),
            'Obstacle': t.get('Obstacle', '?'),
            'GfxOnly': t.get('GfxOnly', '?'),
            'Drivable': t.get('Drivable', '?'),
            'Far': t.get('Far', '?'),
            'animation': t.get('animation', '?'),
            'numparts': t.get('numparts', '?'),
            'pdef': os.path.isfile(pdef),
            'lods_declared': lods,
            'lods_with_xmod': found,
            'radius': round(d['sphere'][3], 2) if 'sphere' in d else None,
        })

    tsv = a.out_path + '_kinds.tsv'
    with open(tsv, 'w', encoding='utf-8', newline='\n') as f:
        cols = ['name', 'instances', 'FixedObject', 'Obstacle', 'GfxOnly',
                'Drivable', 'Far', 'animation', 'numparts', 'pdef',
                'lods_declared', 'lods_with_xmod', 'radius']
        f.write('\t'.join(cols) + '\n')
        for r in sorted(lines, key=lambda x: -x['instances']):
            f.write('\t'.join(str(r[c]) for c in cols) + '\n')

    js = a.out_path + '_placement.json'
    json.dump([{'kind': n, 'section': s, 'matrix': m} for n, m, s in placements],
              open(js, 'w'), indent=0)

    print('  -> %s' % tsv)
    print('  -> %s (%d placements)' % (js, len(placements)))

    # check against what the file itself declares
    if hdr.get('num_prop_types') != len(kinds):
        print('  WARNING: num_prop_types says %s, read %d'
              % (hdr.get('num_prop_types'), len(kinds)))
    expected = {'normal': hdr.get('prop_count'),
                'fixed': hdr.get('fixed_prop_count'),
                'gfx': hdr.get('gfx_prop_count')}
    for k, v in expected.items():
        if v is not None and by_section.get(k, 0) != v:
            print('  WARNING: %s_count says %s, read %d' % (k, v, by_section.get(k, 0)))
    without_model = [r['name'] for r in lines if not r['lods_with_xmod']]
    without_pdef = [r['name'] for r in lines if not r['pdef']]
    never = [r['name'] for r in lines if r['instances'] == 0]
    print('  kinds without any .xmod: %d %s' % (len(without_model), without_model[:6]))
    print('  kinds without .pdef:     %d %s' % (len(without_pdef), without_pdef[:6]))
    print('  kinds never placed:      %d %s' % (len(never), never[:6]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
