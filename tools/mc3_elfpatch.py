"""
mc3_elfpatch.py - apply word patches straight into the PS2 ELF, by virtual address

A pnach is convenient but it is one more thing that can silently not apply: a
wrong CRC in the filename, cheats switched off, a `patch=` line the parser
rejects. Writing into the ELF removes that whole class of doubt - if the word is
in the file, it is in memory.

Every patch carries the value it EXPECTS to find. If the file does not already
hold that word the patch is refused, so a stale address or the wrong ELF fails
loudly instead of corrupting a random instruction.

    python mc3_elfpatch.py show  --elf F 1AC2A4 5280F0
    python mc3_elfpatch.py crc   --elf F
    python mc3_elfpatch.py apply --elf IN --out OUT --keep-crc \
           --patch 1AC2A4:3C013D08:3C01BD08 --patch 5280F0:14400008:00000000

## Why --keep-crc
PCSX2 identifies a game by the ELF's CRC, and that CRC is just every 32-bit word
of the file XORed together (`ElfObject::getCRC`). Editing the ELF therefore
changes the CRC and every pnach named after the old one stops applying. Because
the checksum is a plain XOR, the damage is exactly `old ^ new` per patched word,
and XORing that same value into any other word cancels it. `--keep-crc` writes
the accumulated delta into a slack word past the end of PT_LOAD - bytes that are
in the file but never loaded into memory - so the CRC, and every existing pnach,
survives untouched.
"""

import argparse
import io
import os
import struct


def segments(d):
    """[(va, file_off, size)] of the PT_LOAD segments."""
    ph = struct.unpack_from('<I', d, 0x1C)[0]
    phes = struct.unpack_from('<H', d, 0x2A)[0]
    phn = struct.unpack_from('<H', d, 0x2C)[0]
    out = []
    for i in range(phn):
        t, off, va, _pa, fsz, _msz = struct.unpack_from('<IIIIII', d, ph + i * phes)
        if t == 1 and fsz:
            out.append((va, off, fsz))
    return out


def sections(d):
    """[(name_off, type, file_off, size)] from the section header table."""
    sh = struct.unpack_from('<I', d, 0x20)[0]
    shes = struct.unpack_from('<H', d, 0x2E)[0]
    shn = struct.unpack_from('<H', d, 0x30)[0]
    out = []
    for i in range(shn):
        o = sh + i * shes
        name, kind = struct.unpack_from('<II', d, o)
        off, size_ = struct.unpack_from('<II', d, o + 0x10)
        out.append((name, kind, off, size_))
    return out, sh, shn * shes


def offset_of(d, va):
    for base, off, size_ in segments(d):
        if base <= va < base + size_:
            return off + va - base
    raise SystemExit('VA %08X is not in any PT_LOAD' % va)


def crc(d):
    """PCSX2's ElfObject::getCRC - every 32-bit word XORed together."""
    c = 0
    for (w,) in struct.iter_unpack('<I', d[:len(d) // 4 * 4]):
        c ^= w
    return c


def slack(d):
    """A zero word that is in the file but outside PT_LOAD and every section.

    Those bytes are never mapped into memory and belong to no section, so
    flipping one is invisible to the game and to any tool reading the ELF - it
    only moves the checksum.
    """
    end = max(off + size_ for _va, off, size_ in segments(d))
    secs, sh_off, sh_size = sections(d)
    def occupied(o):
        if sh_off <= o < sh_off + sh_size:
            return True
        return any(kind != 8 and s_off <= o < s_off + s_size    # 8 = SHT_NOBITS
                   for _n, kind, s_off, s_size in secs if s_size)
    for o in range(end, len(d) - 4, 4):
        if not occupied(o) and struct.unpack_from('<I', d, o)[0] == 0:
            return o
    return None


def show(elf, vas):
    d = open(elf, 'rb').read()
    print('%s  crc %08X' % (os.path.basename(elf), crc(d)))
    for va in vas:
        o = offset_of(d, va)
        print('   VA %08X  (file %08X)  = %08X'
              % (va, o, struct.unpack_from('<I', d, o)[0]))


def apply(elf, out, patches, keep):
    d = bytearray(open(elf, 'rb').read())
    before = crc(d)
    print('input   : %s  crc %08X' % (os.path.basename(elf), before))

    # check EVERYTHING before writing anything
    plan = []
    for va, expected, new in patches:
        o = offset_of(d, va)
        current = struct.unpack_from('<I', d, o)[0]
        if current != expected:
            raise SystemExit('REFUSED at VA %08X: expected %08X, found %08X'
                             % (va, expected, current))
        plan.append((va, o, current, new))

    delta = 0
    for va, o, current, new in plan:
        struct.pack_into('<I', d, o, new)
        delta ^= current ^ new
        print('   VA %08X  %08X -> %08X' % (va, current, new))

    if keep and delta:
        o = slack(d)
        if o is None:
            print('   (no slack word: the crc WILL change)')
        else:
            struct.pack_into('<I', d, o, delta)
            print('   crc compensation: %08X at offset %08X (outside PT_LOAD)'
                  % (delta, o))

    after = crc(d)
    open(out, 'wb').write(bytes(d))
    print('output  : %s  crc %08X   %s'
          % (os.path.basename(out), after,
             'SAME (the pnach still applies)' if after == before
             else 'CHANGED -> rename the pnach to %08x.pnach' % after))


def par(s):
    p = s.split(':')
    if len(p) != 3:
        raise argparse.ArgumentTypeError('use VA:EXPECTED:NEW')
    return tuple(int(x, 16) for x in p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=('show', 'crc', 'apply'))
    ap.add_argument('args', nargs='*')
    ap.add_argument('--elf', required=True)
    ap.add_argument('--out')
    ap.add_argument('--patch', action='append', type=par, default=[])
    ap.add_argument('--keep-crc', action='store_true')
    a = ap.parse_args()
    if a.action == 'crc':
        print('%08X' % crc(open(a.elf, 'rb').read()))
    elif a.action == 'show':
        show(a.elf, [int(x, 16) for x in a.args])
    else:
        if not a.out or not a.patch:
            raise SystemExit('apply needs --out and at least one --patch')
        apply(a.elf, a.out, a.patch, a.keep_crc)


if __name__ == '__main__':
    main()
