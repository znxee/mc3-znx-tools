"""mc3_shaderprog.py - read the byte program of an rmcShaderComplex

Each STEP of an rmcShaderComplex points, at its +0x00, to a little strip of
bytes. It is not a struct: it is a command stream, and what executes it is the
routine at 0x002B1F90, called by rmcShaderComplex::Bind (0x002B2168) after it
walks the chain of steps.

THE FORMAT, read straight from the disassembly:

    command = 1 OPCODE byte + 1 or 4 DATA bytes
        opcode & 0x3F   register index (0..63)
        opcode & 0xC0   if ZERO the data has 1 byte; otherwise 4 (little endian)
        opcode == 0xFF  end of program

    lbu  t1, 0(t0)          ; opcode
    beq  t1, 255, end       ; 0xFF ends
    andi v0, t1, 0xC0       ; data width
    andi v0, t1, 0x3F       ; index
    sll  v0, v0, 2
    addu v0, v0, 0x70000060 ; CURRENT table, in the scratchpad
    addu v1, v0, 0x700000B8 ; DEFAULT table
    sw   a2, 0(v0)          ; write the value
                            ; and set/clear a bit at 0x7000010C depending on
                            ; whether the value differs from the default

Tested on the 66 programs reachable in the sd savestate: all 66 decode up to
the 0xFF with nothing left over or missing. It is not a plausible reading, it is
the format.

    python mc3_shaderprog.py --ram state/eeMemory.bin
    python mc3_shaderprog.py --ram state/eeMemory.bin --prog 0x0081F260

WHAT CAN BE READ FROM THE TABLE, without having found each register's consumer:

    r8 is the step's BASECOLOR, packed AABBGGRR, with HALF the alpha:
    alpha_gs = (a + 1) >> 1. This is no longer a reading by analogy - it matches
    field by field with the .shadert text:

        city/water.shadert  step 0  basecolor 255 255 255 127 -> 40FFFFFF
                            step 1  basecolor 255 255 255 200 -> 64FFFFFF
                            step 2  basecolor 255 255 255 200 -> 64FFFFFF
        city/city_specular  step 0  basecolor 127 127 127   0 -> 007F7F7F

    and when the source has NO basecolor the default 255 255 255 255 applies,
    which gives exactly the 80FFFFFF seen in road_reflector,
    city_texscroll_doublesided, train_ads, train_inside, train_people and
    ambient_wheel. Eleven of the seventeen step shapes match like that. The same
    division by two shows up in drwShaderCarDecal's Draw, found by the car fork
    another way.

    The six that do NOT match (road_reflector 40FFFFFF, city_specular step 1,
    city_specular_prop step 1, train_shadow, black_matte, wheel_inner) are the
    case of the same name with a different revision: the .shadert on disc is
    not the version that compiled that object.

The rest (r0, r1, r3, r4, r5, r7, r9, r11, r12) stays as numbers. r3 is almost
always 0001000A or 00010044.
"""
import argparse
import collections
import os
import struct

VT_COMPLEX = 0x00626EC8
GLOBAL_MCCITY = 0x00615B40
SHADERLIB = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS', 'shaderlib')


class Ram:
    def __init__(self, path):
        self.d = open(path, 'rb').read()

    def u32(self, a):
        if not (0 <= a <= len(self.d) - 4):
            return 0
        return struct.unpack_from('<I', self.d, a)[0]

    def u16(self, a):
        if not (0 <= a <= len(self.d) - 2):
            return 0
        return struct.unpack_from('<H', self.d, a)[0]

    def text(self, a, n=64):
        s = self.d[a:a + n].split(b'\0')[0]
        try:
            return s.decode('latin1')
        except Exception:
            return ''


def decode(ram, p, limit=256):
    """[(index, value, wide)] up to the 0xFF. None if it does not end."""
    outside = []
    i = p
    while i < p + limit:
        op = ram.d[i]
        if op == 0xFF:
            return outside
        if op & 0xC0:
            v = (ram.d[i + 1] | (ram.d[i + 2] << 8)
                 | (ram.d[i + 3] << 16) | (ram.d[i + 4] << 24))
            i += 5
        else:
            v = ram.d[i + 1]
            i += 2
        outside.append((op & 0x3F, v, bool(op & 0xC0)))
    return None


def program_text(regs):
    return ' '.join('r%d=%s' % (i, ('%08X' % v) if wide else ('%02X' % v))
                    for i, v, wide in regs)


def complexes(ram):
    """[(address, name)] of every rmcShaderComplex with a real .shadert name.

    The name filter is NOT a whim: scanning only by vtable finds 62 objects and
    23 of them carry a race or music path at that offset.
    """
    # RECURSIVE on purpose: 32 of the .shadert files - precisely the CITY ones
    # (city_window, city_road, city_facade, city_specular, moon...) - only exist
    # in shaderlib/city. Listing only the root silently dropped the shaders
    # that matter most here.
    valid = None
    if os.path.isdir(SHADERLIB):
        valid = set()
        for root, _dirs, files_ in os.walk(SHADERLIB):
            valid.update(files_)
    target = struct.pack('<I', VT_COMPLEX)
    outside = []
    i = 0
    while True:
        i = ram.d.find(target, i)
        if i < 0 or i > len(ram.d) - 0x40:
            break
        if i % 4 == 0:
            name = ram.text(i + 0x30)
            if valid is None or name in valid:
                outside.append((i, name))
        i += 4
    return outside


def steps(ram, complex_, limit=32):
    """Step addresses, walking the +0x1C chain."""
    outside = []
    r = complex_ + 8
    while r and 0x100000 <= r < len(ram.d) - 0x20 and r not in outside:
        outside.append(r)
        if len(outside) >= limit:
            break
        r = ram.u32(r + 0x1C)
    return outside


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ram', required=True, help='eeMemory.bin of a savestate')
    ap.add_argument('--prog', help='decode only this program address (hex)')
    a = ap.parse_args()
    ram = Ram(a.ram)

    if a.prog:
        p = int(a.prog, 16)
        regs = decode(ram, p)
        if regs is None:
            raise SystemExit('no 0xFF found within 256 bytes - is this really a program?')
        print('%08X  %s' % (p, program_text(regs)))
        return

    found = complexes(ram)
    print('%d rmcShaderComplex with a real .shadert name' % len(found))
    ok = bad = 0
    unique = collections.OrderedDict()
    for c, name in found:
        for k, r in enumerate(steps(ram, c)):
            p = ram.u32(r)
            if not (0x100000 <= p < len(ram.d) - 8):
                continue
            regs = decode(ram, p)
            if regs is None:
                bad += 1
                continue
            ok += 1
            unique.setdefault((name, k), set()).add(program_text(regs))
    print('%d programs decoded up to the 0xFF, %d failed' % (ok, bad))
    print()
    for (name, k), shapes in unique.items():
        for t in sorted(shapes):
            print('  %-42s step %d   %s' % (name, k, t))


if __name__ == '__main__':
    main()
