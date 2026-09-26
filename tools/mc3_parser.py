"""mc3_parser.py - pull the field NAMES out of the FileIO(datParser&) functions

RAGE's serialised classes register every field with a `datParser`, and the
registration carries the name as a string literal. That means the field names
are in the ELF, next to the code that binds them to their offset - nothing has
to be guessed.

The compiled pattern is always the same:

    jal  0x0055F7C0                 <- datParser::AddField
      a0 = the parser
      a1 = type code
      a2 = pointer to the name
      a3 = this + FIELD OFFSET
      t0, t1 = extras

So it is enough to follow `a2` and `a3` backwards up to the `jal` and the whole
table comes out. `mcConditionVariables::FileIO` alone registers 41 fields.

    python mc3_parser.py 0x25CC60
    python mc3_parser.py --items                # every known FileIO
    python mc3_parser.py 0x25CC60 --base 0x40   # add the sub-object base

`--base` matters when the class is a sub-object: `mcConditionVariables` lives
at `mcCity+0x40` (the mcCity constructor does `addiu a0, this, 0x40` and calls
its constructor), so a field of it at `+0x10` is `mcCity+0x50`.
"""
import argparse
import io
import os
import struct

ROOT = os.path.dirname(os.path.abspath(__file__))
ELF = os.environ.get('MC3_ELF', 'SLUS_213.55')
BASE, OFF = 0x1A0000, 0x80
ADDFIELD = 0x0055F7C0

# Seen so far. The meaning is inferred from the size each field takes between
# one offset and the next, so treat it as a hint and not as a certainty.
KINDS = {}


class Elf:
    def __init__(self, path=ELF):
        self.d = open(path, 'rb').read()

    def u32(self, va):
        o = va - BASE + OFF
        if not (0 <= o <= len(self.d) - 4):
            return None
        return struct.unpack_from('<I', self.d, o)[0]

    def text(self, va):
        o = va - BASE + OFF
        if not (0 <= o < len(self.d)):
            return None
        e = self.d.find(b'\0', o)
        if e < 0 or e - o > 96:
            return None
        s = self.d[o:e]
        if not s or any(c < 32 or c > 126 for c in s):
            return None
        return s.decode('latin1')


def fields(elf, start_, limit=0x800):
    """[(offset, name, type)] registered by a FileIO.

    A deliberately dumb register interpreter: only `lui`, `addiu`, `ori`,
    `move` and `addu`. It does not follow branches. A loop that registers an
    array shows up as ONE entry, the first iteration's - which is what we want,
    because the name is the same in all of them.
    """
    reg = [None] * 32
    reg[0] = 0
    this = None
    outside = []
    va = start_
    while va < start_ + limit:
        w = elf.u32(va)
        if w is None:
            break
        op = w >> 26
        rs, rt, rd = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        imm = w & 0xFFFF
        sim = imm - 0x10000 if imm & 0x8000 else imm
        if va == start_:
            reg[4] = 'this'          # a0 = this on entry
        if op == 0x0F:                                   # lui
            reg[rt] = imm << 16
        elif op == 0x09:                                 # addiu
            b = reg[rs]
            if b == 'this':
                reg[rt] = ('this', sim)
            elif isinstance(b, tuple):
                reg[rt] = (b[0], b[1] + sim)
            elif isinstance(b, int):
                reg[rt] = (b + sim) & 0xFFFFFFFF
            else:
                reg[rt] = None
        elif op == 0x0D:                                 # ori
            b = reg[rs]
            reg[rt] = (b | imm) if isinstance(b, int) else None
        elif op == 0 and (w & 0x3F) in (0x21, 0x2D):     # addu / daddu
            a, b = reg[rs], reg[rt]
            if b == 0 or rt == 0:
                reg[rd] = a
            elif a == 0 or rs == 0:
                reg[rd] = b
            else:
                reg[rd] = None
        elif op == 3:                                    # jal
            target = (w & 0x3FFFFFF) << 2
            # the delay slot still runs before the call
            w2 = elf.u32(va + 4)
            if w2 is not None:
                o2, s2, t2 = w2 >> 26, (w2 >> 21) & 31, (w2 >> 16) & 31
                if o2 == 0x09:
                    b = reg[s2]
                    i2 = (w2 & 0xFFFF)
                    i2 = i2 - 0x10000 if i2 & 0x8000 else i2
                    if b == 'this':
                        reg[t2] = ('this', i2)
                    elif isinstance(b, tuple):
                        reg[t2] = (b[0], b[1] + i2)
                    elif isinstance(b, int):
                        reg[t2] = (b + i2) & 0xFFFFFFFF
                elif o2 == 0 and (w2 & 0x3F) in (0x21, 0x2D):
                    a, b = reg[s2], reg[(w2 >> 16) & 31]
                    d2 = (w2 >> 11) & 31
                    reg[d2] = a if (b == 0) else (b if a == 0 else None)
            if target == ADDFIELD:
                name = None
                if isinstance(reg[6], int):
                    name = elf.text(reg[6])
                target_off = None
                if reg[7] == 'this':
                    target_off = 0
                elif isinstance(reg[7], tuple) and reg[7][0] == 'this':
                    target_off = reg[7][1]
                kind = reg[5] if isinstance(reg[5], int) else None
                if name is not None and target_off is not None:
                    outside.append((target_off, name, kind))
            va += 8
            continue
        elif op == 0 and (w & 0x3F) == 8:                # jr $ra
            if rs == 31:
                break
        va += 4
    return outside


KNOWN = [
    (0x0025C7C8, 'mcSkyHatClass::FileIO'),
    (0x0025CC60, 'mcConditionVariables::FileIO'),
    (0x0025CAF0, 'mcStarField::FileIO'),
    (0x0024D8F8, 'mcGlow::FileIO'),
    (0x0024CCE8, 'lightGlowCondition::FileIO'),
    (0x001CDDA0, 'mcFbGlowTuneParams::FileIO'),
    (0x001D35B0, 'mcParticleFogTune::FileIO'),
    (0x001D6650, 'mcSpotlightTuneParams::FileIO'),
    (0x001DC8F0, 'mcSenseOfSpeed::FileIO'),
    (0x001DDBC8, 'mcColorFilter::FileIO'),
    (0x001A4030, 'mcLoadScreenTransition::FileIO'),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('func', nargs='?', help='address of the FileIO (hex)')
    ap.add_argument('--base', default='0', help='offset of the sub-object inside its owner')
    ap.add_argument('--items', action='store_true')
    ap.add_argument('--elf', default=ELF)
    a = ap.parse_args()
    elf = Elf(a.elf)

    if a.items or not a.func:
        for va, name in KNOWN:
            c = fields(elf, va)
            print('%08X  %-34s %d field(s)' % (va, name, len(c)))
        return

    base = int(a.base, 16) if a.base.lower().startswith('0x') else int(a.base)
    c = fields(elf, int(a.func, 16))
    print('%d field(s)%s' % (len(c), ('  (base +0x%X)' % base) if base else ''))
    for off, name, kind in c:
        print('  +0x%03X  %-34s type %s'
              % (off + base, name, ('%d' % kind) if kind is not None else '?'))


if __name__ == '__main__':
    main()
