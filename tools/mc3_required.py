"""mc3_required.py - which city .pck fields are NEVER null in retail

The practical question when generating a city from scratch is not "what does
this field mean" but "can I leave it zero?". This script answers from evidence:
it walks the retail cities, counts every pointer field it knows and says how
many times it came out null.

    python mc3_required.py <city.pck> [other.pck ...]

HOW TO READ THE RESULT, carefully:

  * a field with 0 nulls across thousands of objects, in all four cities, is a
    field RETAIL always fills. It is the best available evidence that the code
    dereferences it without checking - but it is evidence, not proof: only
    reading the ELF proves there is no test before it.
  * a field with nulls is one the game TOLERATES empty, and there the proof is
    direct: retail loads those files. A known example: the ComponentData
    `lod[0]` is null in 128 of atlanta's 651 components, so the draw path tests
    it before following.

For whoever generates a city: treat the "always filled" column as an obligation
and the "tolerates null" column as freedom. A file of a few KB with almost
everything zeroed violates dozens of those obligations at once, and there is no
way to know which one brought it down first - which is why replacing inside an
existing city worked and building from scratch did not.
"""
import argparse
import collections
import struct
import sys

BASE = 0x06800000
HDR = 0x80


class Pck:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        self.n = len(self.d)

    def u32(self, o):
        return struct.unpack_from('<I', self.d, o)[0] if 0 <= o <= self.n - 4 else None

    def u16(self, o):
        return struct.unpack_from('<H', self.d, o)[0] if 0 <= o <= self.n - 2 else None

    def off(self, va):
        if not va or not (BASE <= va < BASE + 0x01000000):
            return None
        o = va - BASE + HDR
        return o if 0 <= o < self.n else None


def census(p, c):
    """c[(structure, field)] = [total, nulls, outside_the_file]"""
    u32, u16, off = p.u32, p.u16, p.off

    def count(structure, field, value):
        r = c.setdefault((structure, field), [0, 0, 0])
        r[0] += 1
        if not value:
            r[1] += 1
        elif off(value) is None:
            r[2] += 1

    R = HDR
    for field, o in (('shader_group_va', 0x0C), ('sky_shader_group', 0x10),
                     ('hood_array_va', 0x18), ('cell_index_va', 0x1C),
                     ('palette_live', 0x2C), ('palette_source', 0x30),
                     ('sky_class', 0x190), ('city_model_class', 0x194),
                     ('inst_city_model_class', 0x198), ('camera_matrix', 0x19C),
                     ('env_map', 0x1D0), ('texture', 0x1D4),
                     ('texture_proxy', 0x228)):
        count('MapRoot', field, u32(R + o))
    for k in range(20):
        count('MapRoot', 'textures[k]', u32(R + 0x1D8 + 4 * k))

    ci = off(u32(R + 0x1C))
    cell_count = u32(R + 0x268)
    if ci is not None:
        for field, o in (('unique_array', 0x08), ('instance_array', 0x0C),
                         ('cells', 0x10), ('extra', 0x14)):
            count('CellIndex', field, u32(ci + o))
        cells = off(u32(ci + 0x10))
        extra = off(u32(ci + 0x14))
        if cells is not None and cell_count and cell_count < 0x100000:
            for i in range(cell_count):
                e = cells + 0x0C * i
                count('CellRecord', 'unique_list', u32(e + 4))
                count('CellRecord', 'instance_list', u32(e + 8))
        if extra is not None and cell_count and cell_count < 0x100000:
            for i in range(cell_count):
                count('CellIndex+0x14', 'pointer per cell', u32(extra + 4 * i))

    views = set()

    def texture(o):
        if o is None or o in views:
            return
        views.add(o)
        count('TexturePS2', 'name', u32(o + 0x78))
        for k in range(4):
            count('TexturePS2', 'level[k].data', u32(o + 0x48 + 12 * k))

    def mesh(o):
        if o is None or o in views:
            return
        views.add(o)
        count('rmcModelGeom', '+0x0C material_table', u32(o + 0x0C))
        count('rmcModelGeom', '+0x10 group_table', u32(o + 0x10))
        count('rmcModelGeom', '+0x14 per_block', u32(o + 0x14))
        count('rmcModelGeom', '+0x18', u32(o + 0x18))
        count('rmcModelGeom', '+0x1C', u32(o + 0x1C))
        gc = u16(o + 8)
        gt = off(u32(o + 0x10))
        if gt is None or not gc or gc > 4096:
            return
        for g in range(gc):
            e = gt + 8 * g
            count('GroupEntry', 'list', u32(e))
            lst, nb = off(u32(e)), u16(e + 4)
            if lst is None or not nb or nb > 4096:
                continue
            for k in range(nb):
                count('BlockEntry', 'packet', u32(lst + 8 * k))
        pb = off(u32(o + 0x14))
        sc = u16(o + 0x0A)
        if pb is not None and sc and sc <= 4096:
            for k in range(sc):
                count('+0x14 level 1', 'slot[k]', u32(pb + 4 * k))
                n2 = off(u32(pb + 4 * k))
                if n2 is None:
                    continue
                for g in range(gc):
                    count('+0x14 level 2', 'counts', u32(n2 + 8 * g))
                    count('+0x14 level 2', 'vectors', u32(n2 + 8 * g + 4))
                    ea, eb = off(u32(n2 + 8 * g)), off(u32(n2 + 8 * g + 4))
                    if ea is None or eb is None:
                        continue
                    nb2 = u32(ea)
                    if not nb2 or nb2 > 4096:
                        continue
                    for j in range(nb2):
                        count('+0x14 vector', 'pointer per block', u32(eb + 4 * j))

    def component(o):
        if o is None or o in views:
            return
        views.add(o)
        for field, x in (('+0x04', 0x04), ('+0x08 name', 0x08), ('+0x0C current', 0x0C),
                         ('+0x10', 0x10), ('+0x14', 0x14), ('+0x18', 0x18),
                         ('+0x28 kind', 0x28)):
            count('ComponentData', field, u32(o + x))
        for k in range(10):
            count('ComponentData', 'lod[0]' if k == 0 else 'lod[1..9]',
                  u32(o + 0x3C + 4 * k))
        for k in range(10):
            mesh(off(u32(o + 0x3C + 4 * k)))

    ngrp, vgrp = u32(R + 0x08), off(u32(R + 0x0C))
    if vgrp is not None:
        for g in range(ngrp or 0):
            e = vgrp + 0x10 * g
            count('ShaderGroupDesc', 'array_va', u32(e + 4))
            arr, ns = off(u32(e + 4)), u16(e + 8)
            if arr is None or not ns:
                continue
            for k in range(ns):
                count('shader group', 'slot[k]', u32(arr + 4 * k))
                s = off(u32(arr + 4 * k))
                if s is None:
                    continue
                f = u32(s + 4) or 0
                for i in range(max((f >> 22) & 7, 1)):
                    count('shader', 'parameter[i]', u32(s + 8 + 4 * i))
                    texture(off(u32(s + 8 + 4 * i)))

    nh, vh = u32(R + 0x14), off(u32(R + 0x18))
    if vh is not None:
        for h in range(nh or 0):
            e = vh + 0x14 * h
            for field, x in (('desc_va', 0x00), ('unique_array', 0x08),
                             ('instance_array', 0x10)):
                count('HoodEntry', field, u32(e + x))
            nu, au = u32(e + 4), off(u32(e + 8))
            ni, ai = u32(e + 0x0C), off(u32(e + 0x10))
            desc = off(u32(e))
            if desc is not None:
                texture(desc + 4)
            if au is not None and nu and nu < 0x100000:
                for k in range(nu):
                    count('UniqueComponent', 'data_va', u32(au + 0x1C * k + 0x0C))
                    count('UniqueComponent', 'type_va', u32(au + 0x1C * k + 0x10))
                    component(off(u32(au + 0x1C * k + 0x0C)))
            if ai is not None and ni and ni < 0x100000:
                for k in range(ni):
                    count('Instance', 'data_va', u32(ai + 0x60 * k + 0x0C))
                    count('Instance', 'type_va', u32(ai + 0x60 * k + 0x10))
                    component(off(u32(ai + 0x60 * k + 0x0C)))


def check(references, target):
    """Measure the obligation on retail and point out what the target file violates.

    A field only counts as required if retail filled it in EVERY occurrence. A
    field the target does not even have (zero count) is not a violation: it is
    an absence, listed separately, because a city with zero instances is not
    wrong - it just does not exercise that field.
    """
    ref = collections.OrderedDict()
    for r in references:
        census(Pck(r), ref)
    # TWO classes of "never null", and the difference matters:
    #   pointer    never zero AND always a valid address in the file
    #   non-zero   never zero, but not always a pointer - rmcModelGeom+0x18 is
    #              1 in 624 meshes, a pointer in 408 and CDCDCDCD in 190. A field
    #              that comes as CD is a field nobody initialises; calling it
    #              required would be too strong.
    required_fields = {k for k, (tot, nulls, f) in ref.items() if tot and nulls == 0 and not f}
    nonzero = {k for k, (tot, nulls, f) in ref.items() if tot and nulls == 0 and f}

    target_census = collections.OrderedDict()
    census(Pck(target), target_census)

    violates, absent, ok = [], [], 0
    for k in sorted(required_fields):
        if k not in target_census or target_census[k][0] == 0:
            absent.append(k)
            continue
        tot, nulls, _f = target_census[k]
        if nulls:
            violates.append((k, tot, nulls))
        else:
            ok += 1

    print('reference: %d city(ies)' % len(references))
    print('  %d fields ALWAYS a valid pointer (required)' % len(required_fields))
    print('  %d fields never zero but not always a pointer (checked separately)'
          % len(nonzero))
    for struct_name, field in sorted(nonzero):
        tot, nulls, f = ref[(struct_name, field)]
        target_v = target_census.get((struct_name, field))
        status = ('target: %d of %d null' % (target_v[1], target_v[0])) if target_v and target_v[0] else 'target does not exercise it'
        print('     %-18s %-24s %d of %d outside the file   %s'
              % (struct_name, field, f, tot, status))
    print()
    print('target: %s' % target)
    print()
    print('MEETS     %d of %d' % (ok, len(required_fields)))
    print('VIOLATES  %d' % len(violates))
    for (struct_name, field), tot, nulls in violates:
        print('   %-18s %-24s %d of %d null' % (struct_name, field, nulls, tot))
    print('NOT EXERCISED  %d  (the file has no occurrence)' % len(absent))
    for struct_name, field in absent:
        print('   %-18s %s' % (struct_name, field))
    return 1 if violates else 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcks', nargs='+')
    ap.add_argument('--check', dest='check_', metavar='PCK',
                    help='measure the obligation on the reference pcks and then '
                         'CHECK this file against it')
    a = ap.parse_args()

    if a.check_:
        return check(a.pcks, a.check_)

    c = collections.OrderedDict()
    for path in a.pcks:
        census(Pck(path), c)

    print('%d cities' % len(a.pcks))
    print()
    print('%-18s %-24s %9s %9s %8s' % ('structure', 'field', 'total', 'nulls', '%'))
    required_fields, free = [], []
    for (struct_name, field), (tot, nulls, outside) in c.items():
        pct = 100.0 * nulls / tot if tot else 0.0
        line = '%-18s %-24s %9d %9d %7.1f%%' % (struct_name, field, tot, nulls, pct)
        if outside:
            line += '   (%d outside the file)' % outside
        print(line)
        (required_fields if nulls == 0 else free).append('%s.%s' % (struct_name, field))

    print()
    print('NEVER NULL in retail - treat as required (%d fields):' % len(required_fields))
    for x in required_fields:
        print('   %s' % x)
    print()
    print('TOLERATES NULL - the game loads it like that (%d fields):' % len(free))
    for x in free:
        print('   %s' % x)
    return 0


if __name__ == '__main__':
    sys.exit(main())
