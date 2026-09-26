"""
mc3_vu.py - Midnight Club 3 PS2 vector unit microcode

The PS2 executable was linked WITHOUT stripping, and it still carries the whole
VU toolchain layout:

    .vutext            175568 bytes of microcode, 21946 instructions
    .DVP.ovlytab       97 records of 12 bytes, the overlay table
    .DVP.ovlystrtab    the overlay names
    .DVP.overlay..*    97 sections, one per chunk
    .vudata / .vubss   both empty in the shipped build

A record of the overlay table is

    +00  u32  offset of the name inside .DVP.ovlystrtab
    +04  u32  EE address of the chunk, inside .vutext
    +08  u32  destination address in VU micro memory

and the name spells out the rest:

    .DVP.overlay..<vma>.<program id>.<source line>.<chunk index>

`vma` is the VU address of the first chunk and `unknvma` on the others, the
program id is the same for every chunk of one program, and the chunk index puts
them back in order. Do NOT derive the destination from the chunk index: chunks
are 2048 bytes and usually land on 0x000, 0x800, 0x1000 ... but program 12963
puts its second chunk at 0x1F40, so the address has to come from the record.

That gives 21 microprograms of 256 to 1798 instructions. Each one holds SEVERAL
routines rather than one - the E bit ends a microprogram, and these images carry
9 to 18 of them, which is what lets the engine MSCAL a different entry point per
effect without re-uploading.

Worth knowing before hunting for this the hard way: there are no MPG VIFcodes to
find. Scanning the ELF and 400 .pck files for VIF command 0x4A, requiring the E
bit to fall on exactly the last instruction of the run, finds nothing at all -
the upload packet is built at runtime. And `ps2microcode`, which looks like a
promising string, is just a keyword of the .shadert grammar.

The disassembler decodes 21791 of 21791 instructions in both pipes with nothing
left over, every one of the 1442 branches lands inside its own program, and the
output reads the way VU code is supposed to: MULAx / MADDAy / MADDAz / MADDw is
a 4x4 matrix times a vector, OPMULA with MSUB is a cross product, and the vertex
loop fetches with LQI (VIxx++).

    python mc3_vu.py SLUS_213.55.ELF                 list the programs
    python mc3_vu.py SLUS_213.55.ELF --dump DIR      write each one as .bin
    python mc3_vu.py SLUS_213.55.ELF --id 14508803   detail one program
    python mc3_vu.py SLUS_213.55.ELF --coverage      decode everything, report gaps
    python mc3_vu.py SLUS_213.55.ELF --disasm 428787 --from 24 --count 30
"""

import argparse
import collections
import os
import re
import struct

# Names registered by `get_microcode(name, address)` in sub_3B5750, found by
# AlgumCorrupto. The address is the microprogram's 24-byte HEADER, so it sits
# exactly 0x18 before the first chunk the overlay table points at - that offset
# holds for all twelve without exception, which is what confirms the pairing.
#
# `city_reflect_ci` landing on 54609411 is an independent confirmation of the
# city program: 54609411 and 14508803 are the two that read the +50 UV and +98
# position offsets, found earlier by walking their code.
MICROCODE_NAMES = {
    0x5F2D10: 'noise_cn_lighttexgen_dc', 0x5F5DA0: 'noise_cn_pointlight_dc',
    0x5F8ED0: 'noise_pointlight_dc', 0x5FBF00: 'skinned_fresnel',
    0x5FE010: 'specular2_dc', 0x600210: 'texgen_fresnel_dc',
    0x602290: 'carbon_fiber_dc', 0x604640: 'carpaint_3pass_dc',
    0x606B00: 'city_reflect_ci', 0x609860: 'cpv_distort_vtx_dc',
    0x60C890: 'distort_dc', 0x60E9A0: 'light_texgen_dc',
    # The other nine, which no registration function names because they are not
    # shader microcode - each is uploaded by the subsystem that owns it. These
    # come from the October 2004 alpha: MC.MAP names every program in its
    # `.vutext`, and the `.DVP.overlay` id in the section name is identical in
    # both builds, so the id carries the name across. Checked two ways: the
    # overlay id agrees with AddMicrocode on all ten they share, with no
    # disagreement; and each program's consumer here agrees with its name.
    0x5E9610: 'pointlight_ci',        # rmcSetCpvMode
    0x5EC4F0: 'pointlight_dc',        # rmcSetCpvMode
    0x5EF9A0: 'DrawInst',             # mcInstBucket::Draw
    0x5EFD40: 'shadowvu1',            # no direct xref
    0x5F1390: 'ptxDrawUcode',         # ptxMicrocode
    0x5F29C0: 'occludeTestBound',     # mcOccluderSystem::PrepOccluders_VU0
    0x610A60: 'rv1_code_main',        # lowPsxGfx::DoMicrocode
    0x612010: 'rv1_code',             # lowPsxGfx::DoMicrocode, MainMicrocode+0
    0x613FB0: 'rv1_code_drawptx',     # gfxPointSpriteBegin, MainMicrocode+4
}
# The 0x18 holds for a single-page program. In general the run-up to the first
# chunk is one 16-byte DMA tag plus one 8-byte VIFcode, and every later page
# costs another 8 - so a program's whole chain is 0x10 + 8*pages longer than its
# code. Measured on all 21, and it is why a page is 0x800: one MPG packet of 256
# instructions.
HEADER_BYTES = 0x18       # from the registered address to the first chunk

OVLY_REC = 12
E_BIT = 1 << 30          # upper instruction: end of microprogram
NAME_RE = re.compile(r'\.DVP\.overlay\.\.(\S+?)\.(\d+)\.(\d+)\.(\d+)$')


class Elf:
    """Just enough ELF to reach the sections by name."""

    def __init__(self, path):
        self.data = open(path, 'rb').read()
        self.path = path
        if self.data[:4] != b'\x7fELF':
            raise SystemExit('%s is not an ELF' % path)
        shoff, = struct.unpack_from('<I', self.data, 32)
        shentsize, shnum, shstrndx = struct.unpack_from('<3H', self.data, 46)
        if not shnum:
            raise SystemExit('%s has no section table (stripped)' % path)
        stroff, = struct.unpack_from('<I', self.data, shoff + shentsize * shstrndx + 16)
        self.sections = {}
        for i in range(shnum):
            o = shoff + shentsize * i
            nm, _t, _f, addr, off, size = struct.unpack_from('<6I', self.data, o)
            p = stroff + nm
            e = self.data.index(b'\0', p)
            self.sections[self.data[p:e].decode('latin1')] = (addr, off, size)

    def section(self, name):
        if name not in self.sections:
            raise SystemExit('%s has no %s section' % (os.path.basename(self.path), name))
        return self.sections[name]


class Program:
    """One VU microprogram, reassembled from its chunks."""

    def __init__(self, pid, chunks, body):
        self.id = pid
        self.chunks = chunks              # [(index, ee, vu_addr, source_line, size)]
        self.body = body
        self.vu_base = chunks[0][2]

    def __len__(self):
        return len(self.body) // 8

    def instruction(self, i):
        """(lower, upper) of instruction i. The LOWER half sits first."""
        return struct.unpack_from('<2I', self.body, 8 * i)

    def entries(self):
        """Instruction indices whose UPPER carries the E bit.

        The E bit stops the microprogram, so every one of these ends a routine -
        and the instruction after it starts the next.
        """
        return [i for i in range(len(self))
                if struct.unpack_from('<I', self.body, 8 * i + 4)[0] & E_BIT]

    def contiguous(self):
        """True when the chunks tile VU memory with no gap and no overlap."""
        pos = self.chunks[0][2]
        for _i, _ee, vu, _line, size in self.chunks:
            if vu != pos:
                return False
            pos += size
        return True


def programs(elf):
    """Every microprogram in the ELF, keyed by the id in its section names."""
    _a, tab_off, tab_size = elf.section('.DVP.ovlytab')
    _a, str_off, str_size = elf.section('.DVP.ovlystrtab')
    vt_addr, vt_off, vt_size = elf.section('.vutext')
    if tab_size % OVLY_REC:
        raise SystemExit('.DVP.ovlytab is %d bytes, not a multiple of %d'
                         % (tab_size, OVLY_REC))
    names = elf.data[str_off:str_off + str_size]

    # The strings in .DVP.ovlystrtab ARE section names, so each chunk's real
    # size is in the section table. Inferring it from the gap to the next chunk
    # instead comes out short - chunks sit 2048 bytes apart in VU memory but the
    # last one of every program is a remainder, and the ordering by EE address
    # crosses program boundaries.
    out = collections.OrderedDict()
    for i in range(tab_size // OVLY_REC):
        noff, ee, vu = struct.unpack_from('<3I', elf.data, tab_off + OVLY_REC * i)
        end = names.index(b'\0', noff)
        nm = names[noff:end].decode('latin1')
        m = NAME_RE.match(nm)
        if not m:
            continue
        _vma, pid, line, part = m.groups()
        if nm in elf.sections:
            size = elf.sections[nm][2]
        else:
            size = min(2048, vt_addr + vt_size - ee)
        out.setdefault(pid, []).append((int(part), ee, vu, int(line), size))
    progs = collections.OrderedDict()
    for pid, chunks in out.items():
        chunks.sort()
        body = b''
        for _part, ee, _vu, _line, size in chunks:
            o = vt_off + (ee - vt_addr)
            body += elf.data[o:o + size]
        progs[pid] = Program(pid, chunks, body)
    return progs


# ---------------------------------------------------------------------------
# The instruction set
#
# A 64-bit instruction is LOWER at +0 and UPPER at +4. Confirmed twice over: the
# padding pairs at the head of a program read `lower 0x8000033C` +
# `upper 0x000002FF`, the canonical NOP/NOP, and testing the E bit (bit 30) on
# the +4 half is what produces a sane count of routines per image.
#
# UPPER
#     31 I   30 E   29 M   28 D   27 T
#     24..21 dest (w,z,y,x)   20..16 ft   15..11 fs   10..6 fd   5..0 opcode
# Opcodes 0x3C..0x3F escape to a second table addressed by
# ((instr >> 6) & 0x1F) << 2 | (opcode & 3) - the destination register field is
# free there because those operations write the accumulator.
#
# LOWER
#     bit 31 clear -> LOWER1, opcode in bits 30..25
#     bit 31 set   -> LOWER2, opcode in bits 5..0, escaping the same way
#
# The LOWER tables are NOT reconstructed from memory: they were lifted from
# ps2dis.exe, which carries an index table at 0x451730 addressed by
# (instr >> 25) & 0x3F, feeding a pointer array of mnemonics. That array starts
# at 0x451618, not at the first entry that looks like a string - index 1 is the
# one-character "b", and missing it shifts every name by two and quietly turns
# `lq` into `lqi`.
BC = 'xyzw'

UPPER1 = {}
for _i, _m in enumerate(('ADD', 'SUB', 'MADD', 'MSUB', 'MAX', 'MINI', 'MUL')):
    for _b in range(4):
        UPPER1[_i * 4 + _b] = _m + BC[_b]
UPPER1.update({
    0x1C: 'MULq', 0x1D: 'MAXi', 0x1E: 'MULi', 0x1F: 'MINIi',
    0x20: 'ADDq', 0x21: 'MADDq', 0x22: 'ADDi', 0x23: 'MADDi',
    0x24: 'SUBq', 0x25: 'MSUBq', 0x26: 'SUBi', 0x27: 'MSUBi',
    0x28: 'ADD', 0x29: 'MADD', 0x2A: 'MUL', 0x2B: 'MAX',
    0x2C: 'SUB', 0x2D: 'MSUB', 0x2E: 'OPMSUB', 0x2F: 'MINI',
})

UPPER2 = {}
for _i, _m in enumerate(('ADDA', 'SUBA', 'MADDA', 'MSUBA')):
    for _b in range(4):
        UPPER2[_i * 4 + _b] = _m + BC[_b]
for _b in range(4):
    UPPER2[0x10 + _b] = 'ITOF%d' % (0, 4, 12, 15)[_b]
    UPPER2[0x14 + _b] = 'FTOI%d' % (0, 4, 12, 15)[_b]
    UPPER2[0x18 + _b] = 'MULA' + BC[_b]
UPPER2.update({
    0x1C: 'MULAq', 0x1D: 'ABS', 0x1E: 'MULAi', 0x1F: 'CLIP',
    0x20: 'ADDAq', 0x21: 'MADDAq', 0x22: 'ADDAi', 0x23: 'MADDAi',
    0x24: 'SUBAq', 0x25: 'MSUBAq', 0x26: 'SUBAi', 0x27: 'MSUBAi',
    0x28: 'ADDA', 0x29: 'MADDA', 0x2A: 'MULA',
    0x2C: 'SUBA', 0x2D: 'MSUBA', 0x2E: 'OPMULA', 0x2F: 'NOP',
})

# straight out of the ps2dis index table at 0x451730
LOWER1 = {
    0x00: 'LQ', 0x01: 'SQ', 0x04: 'ILW', 0x05: 'ISW',
    0x08: 'IADDIU', 0x09: 'ISUBIU',
    0x10: 'FCEQ', 0x11: 'FCSET', 0x12: 'FCAND', 0x13: 'FCOR',
    0x14: 'FSEQ', 0x15: 'FSSET', 0x16: 'FSAND', 0x17: 'FSOR',
    0x18: 'FMEQ', 0x1A: 'FMAND', 0x1B: 'FMOR', 0x1C: 'FCGET',
    0x20: 'B', 0x21: 'BAL', 0x24: 'JR', 0x25: 'JALR',
    0x28: 'IBEQ', 0x29: 'IBNE',
    0x2C: 'IBLTZ', 0x2D: 'IBGTZ', 0x2E: 'IBLEZ', 0x2F: 'IBGEZ',
}

# out of the switch tree of sub_439420, row = (instr >> 6) & 0x1F, col = instr & 3
LOWER2 = {
    0x30: 'IADD', 0x31: 'ISUB', 0x32: 'IADDI', 0x34: 'IAND', 0x35: 'IOR',
}
LOWER2X = {
    48: 'MOVE', 49: 'MR32',
    52: 'LQI', 53: 'SQI', 54: 'LQD', 55: 'SQD',
    56: 'DIV', 57: 'SQRT', 58: 'RSQRT', 59: 'WAITQ',
    60: 'MTIR', 61: 'MFIR', 62: 'ILWR', 63: 'ISWR',
    64: 'RNEXT', 65: 'RGET', 66: 'RINIT', 67: 'RXOR',
    # ps2dis tests the whole low byte here: 0x7C, 0xBC, 0xBD and 0xFC, which put
    # these four on 100/104/105/108. Writing them one column further along still
    # decodes 98.3% of the microcode and loses exactly these - including every
    # XGKICK, the instruction that hands a packet to the GS, which is the one a
    # vertex program cannot do without.
    100: 'MFP', 104: 'XTOP', 105: 'XITOP', 108: 'XGKICK',
    112: 'ESADD', 113: 'ERSADD', 114: 'ELENG', 115: 'ERLENG',
    116: 'ESUM', 117: 'EATANxy', 118: 'EATANxz',
    120: 'ESQRT', 121: 'ERSQRT', 122: 'ERCPR', 123: 'WAITP',
    124: 'ESIN', 125: 'EATAN', 126: 'EEXP',
}


def dest(d):
    """The four-bit field as the xyzw suffix. Bit 3 is x, bit 0 is w."""
    return ''.join(c for c, b in zip('xyzw', (8, 4, 2, 1)) if d & b)


def _vf(n, d=None):
    return 'VF%02d' % n + ('.' + dest(d) if d else '')


def disasm_upper(up):
    op = up & 0x3F
    d = (up >> 21) & 0xF
    ft, fs, fd = (up >> 16) & 0x1F, (up >> 11) & 0x1F, (up >> 6) & 0x1F
    flags = ''.join(c for c, b in (('I', 31), ('E', 30), ('M', 29),
                                   ('D', 28), ('T', 27)) if up >> b & 1)
    if (op & 0x3C) == 0x3C:
        name = UPPER2.get((fd << 2) | (op & 3))
        args = '' if name == 'NOP' else 'ACC.%s, %s, %s' % (dest(d), _vf(fs, d), _vf(ft, d))
        if name in ('ABS', 'CLIP', 'ITOF0', 'ITOF4', 'ITOF12', 'ITOF15',
                    'FTOI0', 'FTOI4', 'FTOI12', 'FTOI15'):
            args = '%s, %s' % (_vf(ft, d), _vf(fs, d))
    else:
        name = UPPER1.get(op)
        args = '%s, %s, %s' % (_vf(fd, d), _vf(fs, d), _vf(ft, d)) if name else ''
    if not name:
        return flags, '.word 0x%08X' % up
    if name and name[-1] in 'xyzw' and name[:-1] in (
            'ADD', 'SUB', 'MADD', 'MSUB', 'MAX', 'MINI', 'MUL',
            'ADDA', 'SUBA', 'MADDA', 'MSUBA', 'MULA'):
        args = args.rsplit(', ', 1)[0] + ', VF%02d%s' % (ft, name[-1])
    return flags, ('%-9s %s' % (name, args)).rstrip()


def disasm_lower(lo, pc=0):
    # There is no NOP in the lower pipe - the assembler emits a MOVE of VF00
    # onto itself, which is why ps2dis has 68 lower mnemonics and none of them
    # is `nop`. Folding the exact word keeps 5955 lines of it readable without
    # inventing an instruction.
    if lo == 0x8000033C:
        return 'NOP'
    if not (lo >> 31):
        op = (lo >> 25) & 0x3F
        name = LOWER1.get(op)
        d = (lo >> 21) & 0xF
        ft, fs = (lo >> 16) & 0x1F, (lo >> 11) & 0x1F
        imm11 = lo & 0x7FF
        if imm11 & 0x400:
            imm11 -= 0x800
        if name in ('LQ', 'SQ'):
            a, b = (_vf(ft, d), 'VI%02d' % fs) if name == 'LQ' else (_vf(fs, d), 'VI%02d' % ft)
            return '%-9s %s, %d(%s)' % (name, a, imm11, b)
        if name in ('ILW', 'ISW'):
            return '%-9s VI%02d, %d(VI%02d).%s' % (name, ft, imm11, fs, dest(d))
        if name in ('IADDIU', 'ISUBIU'):
            return '%-9s VI%02d, VI%02d, %d' % (name, ft, fs, (lo & 0x7FF) | ((lo >> 10) & 0x7800))
        if name in ('B', 'BAL'):
            t = pc + 1 + imm11
            extra = ', VI%02d' % ft if name == 'BAL' else ''
            return '%-9s %s0x%04X' % (name, extra + (' ' if extra else ''), t * 8)
        if name in ('JR', 'JALR'):
            return '%-9s VI%02d' % (name, fs)
        if name and name.startswith('IB'):
            t = pc + 1 + imm11
            two = name in ('IBEQ', 'IBNE')
            regs = 'VI%02d, VI%02d' % (ft, fs) if two else 'VI%02d' % fs
            return '%-9s %s, 0x%04X' % (name, regs, t * 8)
        if name:
            return '%-9s VI%02d, VI%02d' % (name, ft, fs)
        return '.word 0x%08X' % lo
    op = lo & 0x3F
    d = (lo >> 21) & 0xF
    ft, fs, fd = (lo >> 16) & 0x1F, (lo >> 11) & 0x1F, (lo >> 6) & 0x1F
    if (op & 0x3C) == 0x3C:
        name = LOWER2X.get((fd << 2) | (op & 3))
        if name in ('MOVE', 'MR32'):
            return '%-9s %s, %s' % (name, _vf(ft, d), _vf(fs, d))
        if name in ('LQI', 'LQD'):
            return '%-9s %s, (VI%02d%s)' % (name, _vf(ft, d), fs,
                                            '++' if name == 'LQI' else '--')
        if name in ('SQI', 'SQD'):
            return '%-9s %s, (VI%02d%s)' % (name, _vf(fs, d), ft,
                                            '++' if name == 'SQI' else '--')
        if name in ('DIV', 'SQRT', 'RSQRT'):
            a = 'VF%02d%s' % (fs, BC[(lo >> 21) & 3])
            b = 'VF%02d%s' % (ft, BC[(lo >> 23) & 3])
            return '%-9s Q, %s' % (name, b if name == 'SQRT' else '%s, %s' % (a, b))
        if name in ('MTIR', 'MFIR'):
            return '%-9s VI%02d, VF%02d' % (name, ft, fs) if name == 'MTIR' \
                else '%-9s %s, VI%02d' % (name, _vf(ft, d), fs)
        if name in ('ILWR', 'ISWR'):
            return '%-9s VI%02d, (VI%02d).%s' % (name, ft, fs, dest(d))
        if name in ('XGKICK', 'XTOP', 'XITOP', 'MFP', 'RINIT', 'RGET',
                    'RNEXT', 'RXOR'):
            return '%-9s %s' % (name, 'VI%02d' % (fs if name == 'XGKICK' else ft))
        if name in ('WAITQ', 'WAITP'):
            return name
        if name:
            return '%-9s P, %s' % (name, _vf(fs, d))
        return '.word 0x%08X' % lo
    name = LOWER2.get(op)
    if name:
        if name == 'IADDI':
            imm5 = (lo >> 6) & 0x1F
            return '%-9s VI%02d, VI%02d, %d' % (name, ft, fs,
                                                imm5 - 32 if imm5 & 0x10 else imm5)
        return '%-9s VI%02d, VI%02d, VI%02d' % (name, fd, fs, ft)
    return '.word 0x%08X' % lo


def disasm(program, start=0, count=None):
    """[(index, lower, upper, text)] for a Program."""
    n = len(program) if count is None else min(len(program), start + count)
    out = []
    i = start
    while i < n:
        lo, up = program.instruction(i)
        flags, u = disasm_upper(up)
        if up >> 31 & 1:          # I bit: the lower half is a float constant
            l = 'LOI       %.6g' % struct.unpack('<f', struct.pack('<I', lo))[0]
        else:
            l = disasm_lower(lo, i)
        out.append((i, lo, up, '%-6s %-38s %s' % (flags, u, l)))
        i += 1
    return out


def coverage(progs):
    """How much of the real microcode decodes? The honest test of the tables."""
    tot = bad_u = bad_l = 0
    seen_u, seen_l = collections.Counter(), collections.Counter()
    unknown_u, unknown_l = collections.Counter(), collections.Counter()
    for p in progs.values():
        for i in range(len(p)):
            lo, up = p.instruction(i)
            tot += 1
            _f, u = disasm_upper(up)
            if u.startswith('.word'):
                bad_u += 1
                unknown_u[up & 0x3F] += 1
            else:
                seen_u[u.split()[0]] += 1
            if up >> 31 & 1:
                seen_l['LOI'] += 1
                continue
            l = disasm_lower(lo, i)
            if l.startswith('.word'):
                bad_l += 1
                unknown_l[(lo >> 25) & 0x7F] += 1
            else:
                seen_l[l.split()[0]] += 1
    return tot, bad_u, bad_l, seen_u, seen_l, unknown_u, unknown_l

def report(elf, progs):
    _a, _o, vt = elf.section('.vutext')
    print('%s' % os.path.basename(elf.path))
    print('  .vutext %d bytes, %d instructions; %d programs over %d chunks'
          % (vt, vt // 8, len(progs), sum(len(p.chunks) for p in progs.values())))
    print()
    print('  %-12s %-24s %6s %6s %7s  %s'
          % ('id', 'name', 'bytes', 'instr', 'routines', 'layout'))
    for pid, p in progs.items():
        e = p.entries()
        # Two run-ups exist. A program uploaded as a DMA chain has the 16-byte
        # tag plus an 8-byte VIFcode ahead of its first chunk, which is the
        # 0x18 the registered address assumes. The three rv1_* blocks are not
        # chains and carry only the VIFcode, so they sit 8 bytes in.
        c0 = p.chunks[0][1]
        item_name = (MICROCODE_NAMES.get(c0 - HEADER_BYTES)
                or MICROCODE_NAMES.get(c0 - 8) or '-')
        print('  %-12s %-24s %6d %6d %7d  %s'
              % (pid, item_name, len(p.body), len(p), len(e),
                 'contiguous' if p.contiguous() else 'SPLIT'))


def detail(p):
    print('program %s' % p.id)
    print('  %d instructions from VU 0x%04X, %d chunks' % (len(p), p.vu_base, len(p.chunks)))
    for part, ee, vu, line, size in p.chunks:
        print('     chunk %d  EE %08X -> VU 0x%04X  %5d bytes  (source line %d)'
              % (part, ee, vu, size, line))
    e = p.entries()
    print('  %d E-bit instructions (one per routine):' % len(e))
    print('     %s' % ' '.join(str(i) for i in e))
    print('  first four instructions, lower then upper:')
    for i in range(min(4, len(p))):
        lo, up = p.instruction(i)
        print('     %4d  %08X %08X' % (i, lo, up))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('elf')
    ap.add_argument('--dump', metavar='DIR', help='write each program as a .bin')
    ap.add_argument('--id', help='show one program in detail')
    ap.add_argument('--disasm', metavar='ID', help='disassemble one program')
    ap.add_argument('--from', dest='start', type=int, default=0)
    ap.add_argument('--count', type=int, default=64)
    ap.add_argument('--coverage', action='store_true',
                    help='decode every instruction and report what the tables miss')
    a = ap.parse_args()
    elf = Elf(a.elf)
    progs = programs(elf)
    if a.disasm:
        if a.disasm not in progs:
            raise SystemExit('no program %s; have %s' % (a.disasm, ', '.join(progs)))
        p = progs[a.disasm]
        print('program %s, %d instructions from VU 0x%04X' % (p.id, len(p), p.vu_base))
        print('%-6s %-6s %-38s %s' % ('addr', 'flags', 'upper', 'lower'))
        for i, _lo, _up, text in disasm(p, a.start, a.count):
            print('%04X   %s' % ((p.vu_base + 8 * i) & 0xFFFF, text))
        return
    if a.coverage:
        tot, bu, bl, su, sl, uu, ul = coverage(progs)
        print('%d instructions across %d programs' % (tot, len(progs)))
        print('  upper decoded %d (%.3f%%), unknown %d'
              % (tot - bu, 100.0 * (tot - bu) / tot, bu))
        print('  lower decoded %d (%.3f%%), unknown %d'
              % (tot - bl, 100.0 * (tot - bl) / tot, bl))
        if uu:
            print('  unknown upper opcodes: %s'
                  % ', '.join('0x%02X x%d' % (k, v) for k, v in uu.most_common(10)))
        if ul:
            print('  unknown lower opcodes: %s'
                  % ', '.join('0x%02X x%d' % (k, v) for k, v in ul.most_common(10)))
        print()
        print('  most used upper: %s'
              % ', '.join('%s %d' % kv for kv in su.most_common(12)))
        print('  most used lower: %s'
              % ', '.join('%s %d' % kv for kv in sl.most_common(12)))
        return
    if a.id:
        if a.id not in progs:
            raise SystemExit('no program %s; have %s' % (a.id, ', '.join(progs)))
        detail(progs[a.id])
        return
    report(elf, progs)
    if a.dump:
        os.makedirs(a.dump, exist_ok=True)
        for pid, p in progs.items():
            out = os.path.join(a.dump, 'vu_%s.bin' % pid)
            open(out, 'wb').write(p.body)
        print()
        print('wrote %d files to %s' % (len(progs), a.dump))


if __name__ == '__main__':
    main()
