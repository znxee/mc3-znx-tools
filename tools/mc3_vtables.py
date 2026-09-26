"""mc3_vtables.py - NAME the .pck structures without opening IDA

The `.pck` keeps no vtable. It keeps a TOKEN: a number like 0x0079FF88 that the
loader swaps for the real vtable at load time (which is why tokyo uses the same
tokens shifted by +0x488 - it is a per-build table, not a pointer). Reading the
token in the file says nothing; reading the SAME field in a savestate with the
city loaded says everything.

    file:     ComponentData+0x28 = 0079FF88     <- token
    RAM:      ComponentData+0x28 = 00625098     <- the real vtable
    ELF:      vtable+0x08 = 0024C1D0            <- first method
    symbols:  0024C1D0 = mcCityModelType__dtor  <- the class

And with 10946 addresses named in `symbols/` (coming from the October 2004 alpha
build's MC.MAP through mc3_symbols.py), the last step is a table lookup.

    python mc3_vtables.py --ram state/eeMemory.bin
    python mc3_vtables.py --vt 0x625098
    python mc3_vtables.py --vt 0x625098 --all

WHAT THIS ALREADY SOLVED, just by running it the first time:

    MapRoot          parFileIO (+ thunk of mcSkyHatClass::FileIO)
    ComponentData    mcCityModelType
    UniqueComponent  mcCityModel
    Instance         mcInstCityModel
    Mesh             rmcModelGeom
    HoodDesc         rmcTexturePS2   <- the "hood descriptor" is a TEXTURE

The last one knocks down an assumption of mine: I had read the 0xB0 block
pointed to by `hood+0x00` as a "neighbourhood descriptor with undecoded GS
state". It is an `rmcTexturePS2` with the neighbourhood's name. So each hood
carries its own atlas.
"""
import argparse
import io
import os
import struct

ROOT = os.path.dirname(os.path.abspath(__file__))
ELF = os.environ.get('MC3_ELF', 'SLUS_213.55')
BASE, OFF = 0x1A0000, 0x80

# Where the tokens live. (label, how to get there) - the walk is the same in the
# file and in RAM, and it is the pairing by POSITION that translates one into
# the other.
SLOTS = [
    ('MapRoot',          lambda M, U: [M]),
    ('HoodDesc',         lambda M, U: [U(h) for h in _hoods(M, U)]),
    ('UniqueComponent',  lambda M, U: _arr(M, U, 4, 8, 28)),
    ('Instance',         lambda M, U: _arr(M, U, 0x0C, 0x10, 96)),
]


def _hoods(M, U):
    hb = U(M + 0x18)
    return [hb + 20 * i for i in range(U(M + 0x14))]


def _arr(M, U, off_n, off_a, step, limit=6):
    outside = []
    for h in _hoods(M, U):
        n, a = U(h + off_n), U(h + off_a)
        for k in range(min(n, limit)):
            outside.append(a + step * k)
    return outside


def load_names():
    """{address: (name, where it came from)} - the first file to name it wins."""
    names = {}
    order = ('names_manual.tsv', 'names.tsv', 'names_token.tsv',
             'names_graph.tsv', 'names_neighbours.tsv')
    for fname in order:
        pck_path = os.path.join(ROOT, 'symbols', fname)
        if not os.path.isfile(pck_path):
            continue
        for ln in io.open(pck_path, encoding='utf-8', errors='replace'):
            if ln.startswith('#') or not ln.strip():
                continue
            c = ln.rstrip('\n').split('\t')
            if len(c) < 2:
                continue
            try:
                a = int(c[0], 16)
            except ValueError:
                continue
            names.setdefault(a, (c[1], fname[6:-4]))
    return names


class Elf:
    def __init__(self, path=ELF):
        self.d = open(path, 'rb').read()

    def u32(self, va):
        o = va - BASE + OFF
        if not (0 <= o <= len(self.d) - 4):
            return None
        return struct.unpack_from('<I', self.d, o)[0]


def methods(elf, vt, names, how_many=14):
    """[(offset, address, name)] of a vtable."""
    outside = []
    for k in range(how_many):
        v = elf.u32(vt + 4 * k)
        if v is None:
            break
        name = ''
        if 0x1A0000 <= v < 0x680000:
            got = names.get(v)
            name = '%s [%s]' % got if got else ''
        outside.append((4 * k, v, name))
    return outside


def klass(elf, vt, names):
    """A class name guess: the common prefix of the named methods."""
    seen = []
    for _o, _v, n in methods(elf, vt, names):
        if not n:
            continue
        base = n.split(' [')[0]
        if '__' in base:
            seen.append(base.split('__')[0])
    if not seen:
        return ''
    # the most frequent one that is not a thunk
    from collections import Counter
    c = Counter(v for v in seen if not v.startswith('virtual_function_thunk'))
    return c.most_common(1)[0][0] if c else seen[0]


def scan(pck, ram, names, elf):
    """Pair token (file) with vtable (RAM) by walking both the same way."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, 'experimental'))
    from mc2_to_mc3 import Pck
    p = Pck(pck)
    d = open(ram, 'rb').read()

    def Uram(a):
        return struct.unpack_from('<I', d, a)[0]

    def Upck(a):
        o = p.F(a)
        if not (0x80 <= o <= len(p.data) - 4):
            return 0
        return struct.unpack_from('<I', p.data, o)[0]

    def Uram2(a):
        if not (0x100000 <= a <= len(d) - 4):
            return 0
        return struct.unpack_from('<I', d, a)[0]

    M_ram = struct.unpack_from('<I', d, 0x615B40)[0]
    M_pck = p.V(0x80)
    if not (0x100000 <= M_ram < 0x2000000):
        raise SystemExit('the savestate has no city loaded')

    # fields that carry a token, per structure
    fields = {'MapRoot': (0x00,), 'HoodDesc': (0x04,),
              'UniqueComponent': (0x10,), 'Instance': (0x10,),
              'ComponentData': (0x28,), 'Mesh': (0x00,)}
    outside = []
    for rot, find in SLOTS:
        a_ram = find(M_ram, Uram)
        a_pck = find(M_pck, Upck)
        for ar, ap in list(zip(a_ram, a_pck))[:8]:
            if not (0x100000 <= ar < 0x2000000):
                continue
            for c in fields[rot]:
                tok, vt = Upck(ap + c), Uram2(ar + c)
                if tok and vt:
                    outside.append((rot, tok, vt))
            if rot in ('UniqueComponent', 'Instance'):
                cdr, cdp = Uram2(ar + 0x0C), Upck(ap + 0x0C)
                t, v = Upck(cdp + 0x28), Uram2(cdr + 0x28)
                if t and v:
                    outside.append(('ComponentData', t, v))
                mr, mp = Uram2(cdr + 0x3C), Upck(cdp + 0x3C)
                t, v = (Upck(mp), Uram2(mr)) if (mr and mp) else (0, 0)
                if t and v:
                    outside.append(('Mesh', t, v))
    # unique per (label, token)
    seen_one, out_path = set(), []
    for rot, tok, vt in outside:
        k = (rot, tok)
        if k in seen_one:
            continue
        seen_one.add(k)
        out_path.append((rot, tok, vt, klass(elf, vt, names)))
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ram', help='eeMemory.bin of a savestate WITH the city loaded')
    ap.add_argument('--pck', default='output/atlanta_backup/atlanta_midnight_clear.pck')
    ap.add_argument('--vt', help='only show the methods of this vtable (hex)')
    ap.add_argument('--all', dest='all_', action='store_true', help='show unnamed methods too')
    ap.add_argument('--elf', default=ELF)
    a = ap.parse_args()

    names = load_names()
    elf = Elf(a.elf)
    print('%d named addresses in symbols/' % len(names))

    if a.vt:
        vt = int(a.vt, 16)
        print('vtable %08X   class: %s' % (vt, klass(elf, vt, names) or '?'))
        for o, v, n in methods(elf, vt, names, 20):
            if n or a.all_:
                print('   +%02X %08X  %s' % (o, v, n))
        return

    if not a.ram:
        ap.error('--ram is required (or use --vt)')
    for rot, tok, vt, cls in scan(a.pck, a.ram, names, elf):
        print('%-16s token %08X -> vtable %08X   %s' % (rot, tok, vt, cls or '?'))
        for o, v, n in methods(elf, vt, names):
            if n:
                print('      +%02X %s' % (o, n))


if __name__ == '__main__':
    main()
