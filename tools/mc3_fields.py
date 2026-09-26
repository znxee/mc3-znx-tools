"""mc3_fields.py - which offsets of a structure the game REALLY touches

When a field is zeroed in the file and zeroed in RAM, the useful question is not
"what does it hold" but "does anyone reach it". This script answers that by
scanning, and the answer has value: an offset nobody touches is a HOLE, not an
unknown field - and you can stop looking.

How it works: find every read of the structure's global pointer
(`lw rt, 0x5b40(rs)` for mcCity), follow the register to the end of the
function or until it is redefined, and note every `imm(rt)` access. The result
is a map of offset -> (access modes, how many places).

    python mc3_fields.py                 # the whole mcCity map
    python mc3_fields.py 0x1C8           # a single offset
    python mc3_fields.py --ctor 0x259160 # what the constructor touches via `this`

TWO LIMITATIONS THAT MATTER, and scanning cannot get around them:

1. It only catches access through the GLOBAL. A method that gets `this` as an
   argument does not show up - that is how mcCity::InitGlobalLighting escaped,
   and why +0x1A4, +0x1B0 and +0x1BC come out as "nobody touches" even though
   they are the global lighting. Use it together with --ctor and a module scan.
2. Searching for the bare offset in the whole ELF gives false positives galore:
   0x1C8 alone appears 45 times in structures of other modules. Always confirm
   that the base is the global or that the function is in the right module's
   range.

Confirming nobody touches it takes the three scans: global, constructor and
module. Only then is it fair to say "hole".
"""
import argparse
import collections
import os
import struct

ELF = os.environ.get('MC3_ELF', 'SLUS_213.55')
BASE, OFF, SZ = 0x1A0000, 0x80, 0x004D7074
GLOBAL_MCCITY = 0x5B40          # the imm of `lw rt, 0x5b40(rs)`
CITY_MODULE = (0x244000, 0x262000)

OPS = {0x20: 'lb', 0x21: 'lh', 0x23: 'lw', 0x24: 'lbu', 0x25: 'lhu',
       0x28: 'sb', 0x29: 'sh', 0x2B: 'sw', 0x31: 'lwc1', 0x39: 'swc1',
       0x37: 'ld', 0x3F: 'sd'}
WRITE = {'sb', 'sh', 'sw', 'swc1', 'sd'}


def _redefine(w, r):
    """Was register `r` rewritten by this instruction?"""
    op = w >> 26
    if op in (0x09, 0x0D, 0x0F, 0x23, 0x24, 0x25, 0x20, 0x21, 0x37):
        return ((w >> 16) & 31) == r
    if op == 0 and (w & 0x3F) in (0x21, 0x2D, 0x20, 0x25, 0x2A, 0x2B, 0x24):
        return ((w >> 11) & 31) == r
    return False


def mapping(F, imm_global=GLOBAL_MCCITY, ceiling=0x2300):
    acc = collections.defaultdict(lambda: [set(), set()])
    for off in range(OFF, OFF + SZ - 4, 4):
        w = struct.unpack_from('<I', F, off)[0]
        if (w >> 26) != 0x23 or (w & 0xFFFF) != imm_global:
            continue
        rt = (w >> 16) & 31
        o = off + 4
        while o < OFF + SZ - 4 and o - off < 0x1800:
            w2 = struct.unpack_from('<I', F, o)[0]
            op = w2 >> 26
            if op == 0 and (w2 & 0x3F) == 8 and ((w2 >> 21) & 31) == 31:
                break                                   # jr $ra
            if op in OPS and ((w2 >> 21) & 31) == rt:
                i = w2 & 0xFFFF
                if i < ceiling:
                    acc[i][0].add(OPS[op])
                    acc[i][1].add(off - OFF + BASE)
            if _redefine(w2, rt):
                break
            o += 4
    return acc


def via_this(F, start_, end, reg):
    """Offsets a function touches through a fixed register (the `this`)."""
    acc = collections.defaultdict(set)
    for va in range(start_, end, 4):
        w = struct.unpack_from('<I', F, va - BASE + OFF)[0]
        op = w >> 26
        if op in OPS and ((w >> 21) & 31) == reg:
            acc[w & 0xFFFF].add(OPS[op])
    return acc


def in_module(F, imm, range_=CITY_MODULE):
    """Accesses to an offset inside a code range, base != $sp."""
    outside = []
    for va in range(range_[0], range_[1], 4):
        w = struct.unpack_from('<I', F, va - BASE + OFF)[0]
        op = w >> 26
        if op in OPS and (w & 0xFFFF) == imm and ((w >> 21) & 31) != 29:
            outside.append((va, OPS[op], (w >> 21) & 31))
    return outside


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('offset', nargs='?', help='only this offset (hex)')
    ap.add_argument('--ctor', help='address of a constructor, to see what it touches via this')
    ap.add_argument('--reg', type=int, default=17, help='register holding this in the constructor (default s1=17)')
    ap.add_argument('--elf', default=ELF)
    a = ap.parse_args()
    F = open(a.elf, 'rb').read()

    if a.ctor:
        start_ = int(a.ctor, 16)
        acc = via_this(F, start_, start_ + 0x400, a.reg)
        print('offsets touched via $r%d from %08X:' % (a.reg, start_))
        for k in sorted(acc):
            print('   +0x%03X  %s' % (k, ','.join(sorted(acc[k]))))
        return

    acc = mapping(F)
    if a.offset:
        target = int(a.offset, 16)
        if target in acc:
            m, f = acc[target]
            print('+0x%03X through the global: %s, in %d place(s): %s'
                  % (target, ','.join(sorted(m)), len(f),
                     [hex(x) for x in sorted(f)][:8]))
        else:
            print('+0x%03X: nobody touches it THROUGH THE GLOBAL' % target)
        n = in_module(F, target)
        print('+0x%03X in the city module (%06X..%06X): %d access(es)'
              % (target, CITY_MODULE[0], CITY_MODULE[1], len(n)))
        for va, mn, rs in n[:8]:
            print('   %s $r? , 0x%X($r%d) @ %08X' % (mn, target, rs, va))
        return

    print('%d distinct offsets touched through the global' % len(acc))
    for k in sorted(acc):
        m, f = acc[k]
        print('   +0x%03X  %-16s %2d place(s)  %s'
              % (k, ','.join(sorted(m)), len(f), 'WRITE' if m & WRITE else ''))


if __name__ == '__main__':
    main()
