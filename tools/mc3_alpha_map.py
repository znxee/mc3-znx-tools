"""
mc3_alpha_map.py - read the October 2004 alpha's linker map (MC.MAP)

The alpha build ships the linker map the retail executable does not have: 20275
symbols with full C++ signatures, the section each one lives in, and the .obj it
was compiled from. `mc3_symbols.py` already carries the FUNCTION names over to
retail. This reads the rest, which nothing else does - of those 20275 symbols
only 16237 are code:

    data    2751 named globals in .data/.bss/.rodata, plus 1043 in
            .gnu.linkonce.d. Every fork here chases raw global addresses
            (dword_615B40, 0x0070E56C); the alpha names them.
    spad    the complete scratchpad layout at 0x70000000, 62 symbols
    vu      19 named VU microprograms with their byte ranges in .vutext
    units   which translation unit each symbol came from - 888 of them

`units` is what makes the neighbour pass in mc3_symbols.py safe. That pass walks
outward from an anchor naming whatever sits beside it and stops at the first
size disagreement, but it has no idea where one .cpp ends and the next begins.
A run that walks off the end of a translation unit names every function after it
wrongly, and nothing in the output says so. The map draws that line.

    python mc3_alpha_map.py census
    python mc3_alpha_map.py tree
    python mc3_alpha_map.py data --grep City
    python mc3_alpha_map.py vu
    python mc3_alpha_map.py spad
    python mc3_alpha_map.py units --grep mccity
    python mc3_alpha_map.py export --out output/alpha2004
"""

import argparse
import collections
import io
import os
import re
import struct
import sys

ALPHA_DIR = os.environ.get('MC3_ALPHA', 'MIDNIGHT CLUB 3 DUB PS2 ALPHA BUILD 102404')
MAP = os.path.join(ALPHA_DIR, 'MC.MAP')

# The map is columnar and the column a name starts in is what says whether it is
# an output section, an input section, an object file or a symbol. Everything
# here depends on these four being right; the header line is
#     Address  Size     Align Out     In      File    Symbol
COL_OUT, COL_IN, COL_FILE, COL_SYM = 24, 32, 40, 48

# A section holding executable code. `.gnu.linkonce.t.*` is one per inline or
# template function - the compiler emits it in every translation unit that uses
# it and the linker keeps one. They are real functions and there are 1949 of them.
CODE_PREFIX = ('.text', '.gnu.linkonce.t')
DATA_PREFIX = ('.data', '.bss', '.rodata', '.sdata', '.sbss', '.rdata',
               '.gnu.linkonce.d', '.gnu.linkonce.r', '.lit4', '.lit8',
               '.scommon', '.sndata', '.ctors', '.dtors', '.gcc_except_table')

Sym = collections.namedtuple('Sym', 'addr size sec obj name')


def read_map(path=MAP):
    """Every symbol the map places, in file order.

    `<default>` rows are kept: they carry no name but they do carry the size of
    an anonymous chunk, which is the only thing that gives the VU overlays their
    page layout. Callers that want names filter with `named()`.
    """
    out = []
    sec_out = sec_in = obj = None
    for raw in io.open(path, encoding='latin1'):
        line = raw.rstrip('\n')
        if len(line) <= COL_OUT:
            continue
        try:
            addr = int(line[0:8], 16)
            size = int(line[9:17], 16)
        except ValueError:
            continue
        rest = line[COL_OUT:]
        if not rest.strip():
            continue
        col = COL_OUT + len(rest) - len(rest.lstrip())
        text = rest.strip()
        if col < COL_IN:
            sec_out, sec_in, obj = text, None, None
        elif col < COL_FILE:
            # `<default>` is the linker's unnamed chunk of the output section,
            # not a section name. Taking it literally costs the whole of
            # `.vutext` and a third of `.spad`: those output sections have no
            # named input section, so every symbol under them lands under
            # `<default>` and the VU microcode disappears from the census.
            sec_in = None if text == '<default>' else text
        elif col < COL_SYM:
            obj = None if text == '<default>' else text
        else:
            out.append(Sym(addr, size, sec_in or sec_out, obj, text))
    return out


def named(syms):
    return [s for s in syms if s.name and s.name != '<default>']


def is_code(s):
    return bool(s.sec) and s.sec.startswith(CODE_PREFIX)


def is_data(s):
    return bool(s.sec) and s.sec.startswith(DATA_PREFIX)


LIB_RE = re.compile(r'([A-Za-z0-9_]+\.lib)\(([^)]+)\)')


def unit(obj):
    """('mcgame.lib', 'game.obj', 'c:/soft/mc3/src/mcgame') for one map path.

    The map writes the full build path of the library plus the object inside it,
    which is the original source tree: `c:/soft/age/src/` is the shared engine
    and `c:/soft/mc3/src/` is the game. The `psx2_final` leaf is the build
    configuration, not a source directory, so it comes off.
    """
    if not obj:
        return (None, None, None)
    p = obj.replace(chr(92), '/')
    m = LIB_RE.search(p)
    lib, name = (m.group(1), m.group(2)) if m else (None, p.rsplit('/', 1)[-1])
    head = p.rsplit('/', 1)[0] if '/' in p else ''
    head = re.sub(r'/psx2_final$', '', head)
    return (lib, name, head)


def cmd_census(a):
    syms = read_map(a.map)
    nm = named(syms)
    print('%s' % a.map)
    print('%d rows, %d named' % (len(syms), len(nm)))
    code = [s for s in nm if is_code(s)]
    data = [s for s in nm if is_data(s)]
    remainder = [s for s in nm if not is_code(s) and not is_data(s)]
    print('   code   %5d   (.text %d, .gnu.linkonce.t %d)'
          % (len(code),
             sum(1 for s in code if s.sec.startswith('.text')),
             sum(1 for s in code if s.sec.startswith('.gnu.linkonce.t'))))
    print('   data   %5d' % len(data))
    print('   other  %5d   (scratchpad, VU, overlays, absolutes)' % len(remainder))
    print()
    por_sec = collections.Counter()
    for s in nm:
        k = s.sec or '?'
        for p in CODE_PREFIX + DATA_PREFIX:
            if k.startswith(p):
                k = p + '*' if k != p else p
                break
        if k.startswith('.DVP'):
            k = '.DVP.overlay*'
        por_sec[k] += 1
    for k, v in por_sec.most_common():
        print('   %-24s %5d' % (k, v))
    print()
    libs = collections.defaultdict(set)
    for s in syms:
        lib, obj, _d = unit(s.obj)
        if obj:
            libs[lib or '(direct)'].add(obj)
    print('%d translation units in %d libraries'
          % (sum(len(v) for v in libs.values()), len(libs)))
    for k, v in sorted(libs.items(), key=lambda kv: -len(kv[1]))[:a.top]:
        print('   %-24s %3d obj' % (k, len(v)))


def cmd_tree(a):
    syms = read_map(a.map)
    dirs = collections.defaultdict(set)
    for s in syms:
        lib, obj, d = unit(s.obj)
        if obj:
            dirs[d].add(obj)
    print('%d source directories, %d translation units'
          % (len(dirs), sum(len(v) for v in dirs.values())))
    for d in sorted(dirs):
        print('   %-50s %3d' % (d, len(dirs[d])))


def _grep(rows, pat, key):
    if not pat:
        return rows
    r = re.compile(pat, re.I)
    return [x for x in rows if r.search(key(x))]


def cmd_data(a):
    rows = [s for s in named(read_map(a.map)) if is_data(s)]
    rows = _grep(rows, a.grep, lambda s: s.name)
    print('%d named globals%s' % (len(rows), (' matching %r' % a.grep) if a.grep else ''))
    for s in sorted(rows):
        lib, obj, _d = unit(s.obj)
        print('   %08X %6X  %-16s %-22s %s'
              % (s.addr, s.size, s.sec[:16], (obj or '')[:22], s.name))


def cmd_spad(a):
    rows = [s for s in named(read_map(a.map)) if s.sec == '.spad']
    spr = sorted(s for s in rows if 0x70000000 <= s.addr < 0x70004000)
    remainder = sorted(s for s in rows if s not in spr)
    print('scratchpad, %d symbols at 0x70000000' % len(spr))
    end = 0
    for s in spr:
        if s.addr > end and end:
            print('   %08X %6X   -- %d bytes unaccounted --'
                  % (end, s.addr - end, s.addr - end))
        lib, obj, _d = unit(s.obj)
        print('   %08X %6X  %-22s %s' % (s.addr, s.size, (obj or '')[:22], s.name))
        end = max(end, s.addr + s.size)
    print()
    print('linker constants carried in the same section:')
    for s in remainder:
        print('   %08X %6X  %s' % (s.addr, s.size, s.name))


VU_START = ('_DmaTag', '_start_')
VU_END = ('_DmaEnd', '_end_')


def vu_programs(syms):
    """{name: {...}} for each VU microprogram the map places.

    A program appears twice. In `.vutext` it is a DMA chain in main memory,
    bracketed by `X_DmaTag`/`X_DmaEnd` (the older gfx ones use `X_start_`/
    `X_end_`) - that is what a DMA transfer hands to the VIF, and it is why
    searching the executable for an MPG tag finds nothing: the upload is built
    by the chain, not stamped in the data. In a `.DVP.overlay` section the same
    program appears as VU-local code, bracketed by `X_CodeStart`/`X_CodeEnd`,
    paged in 0x800-byte chunks; that pair gives the size in VU instruction
    memory, and any other symbol in that section is a named entry point.
    """
    prog = {}
    for s in named(syms):
        sec = s.sec or ''
        if sec == '.vutext':
            for suf in VU_START:
                if s.name.endswith(suf):
                    p = prog.setdefault(s.name[:-len(suf)], {})
                    p['elf_start'], p['obj'] = s.addr, s.obj
            for suf in VU_END:
                if s.name.endswith(suf):
                    prog.setdefault(s.name[:-len(suf)], {})['elf_end'] = s.addr
        elif sec.startswith('.DVP'):
            if s.name.endswith('_CodeStart'):
                prog.setdefault(s.name[:-len('_CodeStart')], {})['vu_start'] = s.addr
            elif s.name.endswith('_CodeEnd'):
                prog.setdefault(s.name[:-len('_CodeEnd')], {})['vu_end'] = s.addr
            else:
                # a named address inside the microprogram: an entry point, or a
                # routine the C++ side jumps to (vu0_sincos, rv1_BindNextState)
                lib, obj, _d = unit(s.obj)
                prog.setdefault('@' + (obj or '?'), {}).setdefault(
                    'entries', []).append((s.addr, s.name))
    return prog


def cmd_vu(a):
    syms = read_map(a.map)
    prog = vu_programs(syms)
    owner_ = {}
    for k, v in prog.items():
        if not k.startswith('@'):
            _l, o, _d = unit(v.get('obj'))
            if o:
                owner_['@' + o] = k
    print('VU microcode in the alpha ELF')
    print('%-26s %-19s %8s %8s %s'
          % ('program', 'ELF range', 'ELF sz', 'VU sz', 'object'))
    for k in sorted(prog):
        if k.startswith('@'):
            continue
        v = prog[k]
        s0, s1 = v.get('elf_start'), v.get('elf_end')
        lib, obj, _d = unit(v.get('obj'))
        print('%-26s %08X..%08X %8X %8X %s'
              % (k[:26], s0 or 0, s1 or 0, (s1 - s0) if s0 and s1 else 0,
                 v.get('vu_end', 0), obj or ''))
    print()
    print('named addresses inside the microprograms (VU-local):')
    for k in sorted(prog):
        if not k.startswith('@'):
            continue
        for addr, item_name in sorted(prog[k].get('entries', [])):
            print('   %-26s +%04X  %s' % (owner_.get(k, k[1:])[:26], addr, item_name))


RETAIL_ELF = os.environ.get('MC3_ELF', 'SLUS_213.55')
ALPHA_ELF = os.path.join(ALPHA_DIR, 'SLUS_123.45')

OV_RE = re.compile(r'^[.]DVP[.]overlay[.][.]([^.]+)[.](\d+)[.](\d+)[.](\d+)$')

# The retail names, read out of retail itself. `mcTextureFactory::mcTextureFactory`
# (0x003B5750) registers the shader microcode with
# `rmcShader::AddMicrocode(char const*, unsigned int const*)`, and the two
# arguments of that call are the name string and the chain address:
#
#     li  $a0, aCarbonFiberDc      # "carbon_fiber_dc"
#     jal rmcShader__AddMicrocode
#     li  $a1, dword_602290        # the DMA chain, in the delay slot
#
# So this table owes nothing to the alpha. It is here as the answer key that the
# overlay-id route below is checked against - and the two agree 10 for 10.
VERIFIED = {
    0x005F8ED0: 'noise_pointlight_dc',
    0x005F5DA0: 'noise_cn_pointlight_dc',
    0x005F2D10: 'noise_cn_lighttexgen_dc',
    0x00604640: 'carpaint_3pass_dc',
    0x00609860: 'cpv_distort_vtx_dc',
    0x00606B00: 'city_reflect_ci',
    0x005FBF00: 'skinned_fresnel',
    0x00600210: 'texgen_fresnel_dc',
    0x0060E9A0: 'light_texgen_dc',
    0x0060C890: 'distort_dc',
    0x00602290: 'carbon_fiber_dc',
    0x005FE010: 'specular2_dc',
}

# Who consumes the nine that AddMicrocode does not register, from IDA xrefs.
CONSUMER = {
    0x005E9610: 'rmcSetCpvMode',
    0x005EC4F0: 'rmcSetCpvMode',
    0x005EF9A0: 'mcInstBucket::Draw',
    0x005EFD40: '(no direct xref)',
    0x005F1390: 'ptxMicrocode',
    0x005F29C0: 'mcOccluderSystem::PrepOccluders_VU0',
    0x00610A60: 'lowPsxGfx::DoMicrocode',
    0x00612010: 'lowPsxGfx::DoMicrocode, MainMicrocode+0',
    0x00613FB0: 'gfxPointSpriteBegin, MainMicrocode+4',
}


def sections(path):
    """[(name, type, addr, offset, size)] from an ELF section header table.

    Both executables keep theirs - retail is not stripped, which is what makes
    any of this possible without the map.
    """
    d = io.open(path, 'rb').read()
    shoff = struct.unpack_from('<I', d, 0x20)[0]
    shent, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2E)
    so = shoff + shstrndx * shent
    st_off, st_size = struct.unpack_from('<II', d, so + 0x10)
    strs = d[st_off:st_off + st_size]
    out = []
    for i in range(shnum):
        o = shoff + i * shent
        nm, typ, _flg, addr, off, size = struct.unpack_from('<6I', d, o)
        out.append((strs[nm:strs.find(b'\0', nm)].decode('latin1'),
                    typ, addr, off, size))
    return d, out


def overlays(secl):
    """{overlay id: total VU code size}, in section order.

    The `.DVP.overlay` sections are SHT_NOBITS - they carry no bytes, only the
    reservation of VU instruction memory, split into 0x800 pages (256
    instructions, which is one MPG packet). The number in the section name is
    stable across builds and is what identifies a program.
    """
    g = collections.OrderedDict()
    for n, _t, _a, _o, s in secl:
        m = OV_RE.match(n)
        if m:
            k = int(m.group(2))
            g[k] = g.get(k, 0) + s
    return g


def chain(d, vaddr, voff, vsize):
    """Walk `.vutext` as a DMA chain: [(vaddr, size)] per program.

    Each program starts with a source-chain DMA tag whose low 16 bits are the
    qword count of everything that follows it, so one tag read gives the whole
    program's length and the next tag's position. Checked against the alpha,
    where MC.MAP gives the true boundaries: 16 of 16 exact. The three `rv1_*`
    blocks at the end of the segment are not chains - they are the older
    gfx.lib VU1 code, bracketed by `_start_`/`_end_` instead - and the walk
    stops when it stops seeing tags.
    """
    blocks, p = [], 0
    while p + 4 <= vsize:
        tag = struct.unpack_from('<I', d, voff + p)[0]
        if (tag >> 16) != 0x6000:
            break
        n = ((tag & 0xFFFF) + 1) * 16
        if n <= 0 or p + n > vsize:
            break
        blocks.append((vaddr + p, n))
        p += n
    return blocks, p


def cmd_vuretail(a):
    dA, sA = sections(a.alpha)
    dR, sR = sections(a.retail)
    ovA, ovR = overlays(sA), overlays(sR)
    vtR = [x for x in sR if x[0] == '.vutext'][0]
    _n, _t, va, vo, vs = vtR
    blocks, stopped = chain(dR, va, vo, vs)

    prog = vu_programs(read_map(a.map))
    order = [k for _s, k, _v in
             sorted((v.get('elf_start', 0), k, v)
                    for k, v in prog.items() if not k.startswith('@'))]
    name_by_id = dict(zip(list(ovA), order))

    print('retail .vutext %08X..%08X (%X bytes); %d chained programs, '
          '%X bytes of rv1_* at the end' % (va, va + vs, vs, len(blocks), vs - stopped))
    print('overlays: alpha %d ids, retail %d ids; new in retail: %s'
          % (len(ovA), len(ovR), ' '.join(str(k) for k in ovR if k not in ovA)))
    print()
    print('%-26s %-19s %-8s %-8s %-6s %s'
          % ('program', 'retail range', 'chain', 'code', 'id ok', 'name source'))
    idsR = list(ovR)
    matches = disagrees = 0
    for i, (start_, n) in enumerate(blocks):
        oid = idsR[i] if i < len(idsR) else None
        by_id = name_by_id.get(oid)
        ver = VERIFIED.get(start_)
        item_name = ver or by_id or '?'
        if ver and by_id:
            if ver == by_id:
                matches += 1
                origin = 'AddMicrocode = overlay'
            else:
                disagrees += 1
                origin = 'DISAGREES: overlay says %s' % by_id
        elif ver:
            origin = 'AddMicrocode (id %s is new)' % oid
        else:
            origin = CONSUMER.get(start_, 'overlay id')
        print('%-26s %08X..%08X %-8X %-8X %-6s %s'
              % (item_name[:26], start_, start_ + n, n, ovR.get(oid, 0),
                 'yes' if oid in ovA else 'new', origin))
    # The rv1_* ones, which are not chained, sit at the tail of the segment. With
    # no DMA tag there is no size to read, so it comes from the retail overlay plus
    # the excess the SAME program has in the alpha (where MC.MAP gives both
    # numbers). If the sum closes exactly at the end of the segment, the tail is
    # right.
    p = va + stopped
    for oid in idsR[len(blocks):]:
        k = name_by_id.get(oid, '? id %s' % oid)
        v = prog.get(k, {})
        slack = (v.get('elf_end', 0) - v.get('elf_start', 0)) - ovA.get(oid, 0)
        size_ = ovR.get(oid, 0) + max(slack, 0)
        print('%-26s %08X..%08X %-8X %-8X %-6s %s'
              % (k[:26], p, p + size_, size_, ovR.get(oid, 0),
                 'yes' if oid in ovA else 'new',
                 CONSUMER.get(p, 'not chained (_start_/_end_)')))
        p += size_
    print()
    print('cross-check: %d names equal by both routes, %d disagreeing'
          % (matches, disagrees))
    if p != va + vs:
        print('WARNING: the computed end %08X does not match the segment end %08X'
              % (p, va + vs))
    else:
        print('the segment closes exactly at %08X' % p)


def cmd_units(a):
    syms = read_map(a.map)
    rows = [s for s in named(syms) if is_code(s)]
    rows = _grep(rows, a.grep, lambda s: (s.obj or '') + ' ' + s.name)
    by_obj = collections.defaultdict(list)
    for s in rows:
        by_obj[s.obj].append(s)
    print('%d code symbols in %d translation units' % (len(rows), len(by_obj)))
    for obj in sorted(by_obj, key=lambda o: min(x.addr for x in by_obj[o])):
        rs = sorted(by_obj[obj])
        lib, item_name, d = unit(obj)
        print('   %08X..%08X  %-22s %-34s %d sym'
              % (rs[0].addr, rs[-1].addr + rs[-1].size, (item_name or '')[:22],
                 (d or '')[:34], len(rs)))
        if a.verbose:
            for s in rs:
                print('        %08X %6X  %s' % (s.addr, s.size, s.name))


def cmd_export(a):
    syms = read_map(a.map)
    nm = named(syms)
    if not os.path.isdir(a.out):
        os.makedirs(a.out)

    def write_file(item_name, lines, hdr):
        p = os.path.join(a.out, item_name)
        with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(hdr + '\n')
            f.write('\n'.join(lines) + '\n')
        print('   %-22s %5d lines -> %s' % (item_name, len(lines), p))

    code = [s for s in nm if is_code(s)]
    write_file('alpha_units.tsv',
          ['%08X\t%d\t%s\t%s' % (s.addr, s.size, unit(s.obj)[1] or '', s.name)
           for s in sorted(code)],
          '# addr\tsize\tobj\tname   (alpha .text symbols, translation unit)')

    data = [s for s in nm if is_data(s)]
    write_file('alpha_data.tsv',
          ['%08X\t%d\t%s\t%s\t%s'
           % (s.addr, s.size, s.sec, unit(s.obj)[1] or '', s.name)
           for s in sorted(data)],
          '# addr\tsize\tsection\tobj\tname   (alpha named globals)')

    spad = [s for s in nm if s.sec == '.spad']
    write_file('alpha_spad.tsv',
          ['%08X\t%d\t%s\t%s' % (s.addr, s.size, unit(s.obj)[1] or '', s.name)
           for s in sorted(spad)],
          '# addr\tsize\tobj\tname   (scratchpad + linker constants)')

    prog = vu_programs(syms)
    lines = []
    for k in sorted(prog):
        if k.startswith('@'):
            continue
        v = prog[k]
        lines.append('%08X\t%08X\t%X\t%s\t%s'
                      % (v.get('elf_start', 0), v.get('elf_end', 0),
                         v.get('vu_end', 0), unit(v.get('obj'))[1] or '', k))
    write_file('alpha_vu.tsv', lines,
          '# elf_start\telf_end\tvu_size\tobj\tprogram   (VU microcode)')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map', default=MAP)
    sub = ap.add_subparsers(dest='action')

    c = sub.add_parser('census', help='summary by section and library')
    c.add_argument('--top', type=int, default=20)
    sub.add_parser('tree', help='the original source tree')
    d = sub.add_parser('data', help='named globals')
    d.add_argument('--grep')
    sub.add_parser('spad', help='the scratchpad layout')
    sub.add_parser('vu', help='the VU microcode map')
    u = sub.add_parser('units', help='symbols grouped by translation unit')
    u.add_argument('--grep')
    u.add_argument('-v', '--verbose', action='store_true')
    e = sub.add_parser('export', help='write the tsv files other tools read')
    e.add_argument('--out', default='output/alpha2004')
    vr = sub.add_parser('vuretail', help='carry the VU microcode map to retail')
    vr.add_argument('--alpha', default=ALPHA_ELF)
    vr.add_argument('--retail', default=RETAIL_ELF)

    a = ap.parse_args()
    if not os.path.isfile(a.map):
        raise SystemExit('map not found: %s' % a.map)
    fn = {'census': cmd_census, 'tree': cmd_tree, 'data': cmd_data,
          'spad': cmd_spad, 'vu': cmd_vu, 'units': cmd_units,
          'export': cmd_export, 'vuretail': cmd_vuretail}.get(a.action)
    if not fn:
        ap.print_help()
        return 2
    fn(a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
