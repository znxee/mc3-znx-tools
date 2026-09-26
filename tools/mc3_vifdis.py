"""mc3_vifdis.py - read the VIF chains inside a .mesh.pck

Written to have something to diff `mc3_modpath.py` against. A .mesh.pck is a
memory image whose pointers are absolute VAs over the header's baseVA, so a file
offset is `128 + (VA - baseVA)` and no relocation is needed to walk it.

The VIFcodes it knows are the ones mshMaterial::Packetize and
rmcGeometry::Packet::Init emit, decoded from the same tables:

    0x05 STMOD    0 none, 2 difference (each value adds to the one before)
    0x30 STROW    seeds the difference chain, four words follow
    0x04 ITOP
    0x14 MSCAL    6 normally, 8 for a skinned mesh
    0x6x UNPACK   vn from bits 3-2 (S,V2,V3,V4), vl from bits 1-0
                  (32,16,8), NUM in bits 16-23, address in bits 0-9,
                  USN is 0x4000 and the 0x10 in the command byte is the
                  masked form

Usage:

    python mc3_vifdis.py <file.mesh.pck>            summary
    python mc3_vifdis.py <file.mesh.pck> --chain N  disassemble one chain
"""
import os
import struct
import sys

VN = ('S', 'V2', 'V3', 'V4')
VL = ('32', '16', '8', '5')


def unpack_name(cmd):
    vn = (cmd >> 2) & 3
    vl = cmd & 3
    return '%s-%s' % (VN[vn], VL[vl])


def unpack_cols(cmd):
    return ((cmd >> 2) & 3) + 1


def unpack_bits(cmd):
    return {0: 32, 1: 16, 2: 8, 3: 5}[cmd & 3]


class Pck(object):
    def __init__(self, path):
        d = open(path, 'rb').read()
        self.path = path
        self.data = d
        self.base, self.type, self.version, self.body = struct.unpack_from(
            '<IIII', d, 0)
        self.off = 128

    def at(self, va):
        return self.off + (va - self.base)

    def is_ptr(self, v):
        return self.base <= v < self.base + len(self.data) - self.off

    def w(self, o):
        return struct.unpack_from('<I', self.data, self.off + o)[0]

    def h(self, o):
        return struct.unpack_from('<H', self.data, self.off + o)[0]


# Every VIFcode that appears in this data, with how many words follow it.
# Getting this table short is a truncation bug, not a cosmetic one: STMASK was
# missing, walk_chain fell through to its error branch and RETURNED, and a
# packet of 304 vertices was read as the 47 in its first sub-batch. That is how
# a whole city measured as well inside range when it was not.
VIF_EXTRA = {
    0x00: 0,   # NOP
    0x01: 0,   # STCYCL
    0x02: 0,   # OFFSET
    0x03: 0,   # BASE
    0x04: 0,   # ITOP
    0x05: 0,   # STMOD
    0x06: 0,   # MSKPATH3
    0x07: 0,   # MARK
    0x10: 0,   # FLUSHE
    0x11: 0,   # FLUSH
    0x13: 0,   # FLUSHA
    0x14: 0,   # MSCAL
    0x15: 0,   # MSCALF
    0x17: 0,   # MSCNT
    0x20: 1,   # STMASK  - one mask word follows
    0x30: 4,   # STROW
    0x31: 4,   # STCOL
}
VIF_NAME = {
    0x00: 'NOP', 0x01: 'STCYCL', 0x02: 'OFFSET', 0x03: 'BASE', 0x04: 'ITOP',
    0x05: 'STMOD', 0x06: 'MSKPATH3', 0x07: 'MARK', 0x10: 'FLUSHE',
    0x11: 'FLUSH', 0x13: 'FLUSHA', 0x14: 'MSCAL', 0x15: 'MSCALF',
    0x17: 'MSCNT', 0x20: 'STMASK', 0x30: 'STROW', 0x31: 'STCOL',
}


def walk_chain(pck, off, quadwords=0, limit=4096):
    """Yield (offset, word, text) across a packet.

    `quadwords` is the length the packet's own slot declares. Use it: stopping
    at the first MSCAL is wrong, because a packet is a SEQUENCE of sub-batches -
    unpack, MSCAL, unpack, MSCNT - and only the slot says where it really ends.
    """
    i = off
    end = min(off + (quadwords * 16 if quadwords else limit * 4),
              len(pck.data) - pck.off)
    while i < end:
        w = pck.w(i)
        cmd = (w >> 24) & 0xFF
        num = (w >> 16) & 0xFF
        imm = w & 0xFFFF
        addr = imm & 0x3FF
        usn = bool(imm & 0x4000)
        if cmd in VIF_EXTRA:
            extra = VIF_EXTRA[cmd]
            name = VIF_NAME[cmd]
            if cmd == 0x30 or cmd == 0x31:
                yield i, w, '%s %d %d %d %d' % ((name,) + tuple(
                    pck.w(i + 4 + k * 4) for k in range(4)))
            elif cmd == 0x05:
                yield i, w, 'STMOD %d' % (w & 0xFF)
            elif cmd == 0x04:
                yield i, w, 'ITOP %03X' % addr
            elif cmd in (0x14, 0x15):
                yield i, w, '%s %d' % (name, w & 0xFFFF)
            elif cmd == 0x20:
                yield i, w, 'STMASK %08X' % pck.w(i + 4)
            else:
                yield i, w, name
            i += 4 + extra * 4
            # With no declared length there is nothing better to stop on.
            if cmd == 0x14 and not quadwords:
                return
        elif 0x60 <= cmd <= 0x7F:
            cols = unpack_cols(cmd)
            bits = unpack_bits(cmd)
            n = num * cols
            payload = {32: n * 4, 16: n * 2, 8: n}[bits]
            payload = 4 * ((payload + 3) // 4)
            yield i, w, 'UNPACK %-5s x%-3d addr %03X%s%s  (%d B)' % (
                unpack_name(cmd), num, addr, ' USN' if usn else '',
                ' masked' if cmd & 0x10 else '', payload)
            i += 4 + payload
        else:
            yield i, w, '?? %02X' % cmd
            return


# ---------------------------------------------------------------------------
#  Walking the graph instead of scanning for chains
#
#  find_chains() below sweeps the file for anything shaped like a header. That
#  is fine on a retail mesh and WRONG on a generated one: measured against the
#  729 pcks the game built for LA it produced ~94000 vertices for a single city
#  block, which is false positives being read as UNPACK. Scanning cannot tell
#  VIF payload from a word that merely looks like a tag.
#
#  The contract below is the Fork Alpha 2004 measurement of
#  rmcModelGeom::rmcModelGeom(datResource&) 0x002A9D18 and
#  rmcGeometry::rmcGeometry(datResource&) 0x002AD148, checked here against
#  model/probe1.mesh.pck: +0x08 reads 0x1B = 27, and 27 is the material count
#  that pck is known to have.
#
#      rmcModelGeom  +0x00 vtable (the ctor overwrites it; the file value is
#                                  not meaningful)
#                    +0x08 u16 material groups
#                    +0x0A u16 rmcModelCpv count
#                    +0x0C ptr -> u16[groups], the shader slots
#                    +0x10 ptr -> rmcGeometry[groups]
#      rmcGeometry   +0x00 ptr -> packet slots
#                    +0x04 u16 packet count
#      packet slot   +0x00 ptr -> the VIF chain
#                    +0x04 u16 quadwords
#                    +0x06 u16 vertices
# ---------------------------------------------------------------------------

def walk_graph(pck):
    """Yield (chain offset, quadwords, vertices) for every packet, in order."""
    groups = pck.h(0x08)
    geoms = pck.w(0x10)
    if not groups or not pck.is_ptr(geoms):
        return
    g0 = pck.at(geoms) - pck.off
    for k in range(groups):
        slots = pck.w(g0 + k * 8)
        npk = pck.h(g0 + k * 8 + 4)
        if not pck.is_ptr(slots):
            continue
        s0 = pck.at(slots) - pck.off
        for i in range(npk):
            data = pck.w(s0 + i * 8)
            qw = pck.h(s0 + i * 8 + 4)
            nv = pck.h(s0 + i * 8 + 6)
            if pck.is_ptr(data):
                yield pck.at(data) - pck.off, qw, nv


def model_extent(pck, pos_addrs=(0x100, 0x227)):
    """The model's own extent, decoded through the graph.

    Decodes BOTH position encodings. Reading only the V3-16 raw stream is a
    trap: PackData picks 8-bit DELTA whenever it is smaller, and on this data it
    often is - a first pass that skipped the delta runs measured every model as
    well inside range and found nothing, which was wrong. A delta run is STROW
    seeding the row, STMOD 2 putting the unit in difference mode, then a V3-8
    whose bytes accumulate onto the previous value.

    Only the streams landing at a POSITION address count - 0x100 on one VU
    buffer and 0x227 on the other, plus whatever a delta run advances to.
    Colour and uv share the same opcodes and would otherwise be folded in.
    """
    lo = [1e30] * 3
    hi = [-1e30] * 3
    scale = None
    n = 0
    for off, qw, nv in walk_graph(pck):
        mode = 0
        row = [0, 0, 0, 0]
        prev = None
        base = None
        for o, w, t in walk_chain(pck, off, qw):
            cmd = (w >> 24) & 0xFF
            if cmd == 0x05:
                mode = w & 0xFF
                continue
            if cmd == 0x30:
                row = [_s32(pck.w(o + 4 + k * 4)) for k in range(4)]
                continue
            if not t.startswith('UNPACK'):
                continue
            addr = w & 0x3FF
            if cmd in (0x60, 0x6C) and ((w >> 16) & 0xFF) == 2:
                f = struct.unpack('<f', struct.pack('<I', pck.w(o + 4)))[0]
                if 0.0 < f <= 1.0:
                    scale = f
                continue
            if scale is None:
                continue
            # Classify by VU address RANGE, not by an exact match plus a small
            # window. A packet can hold far more vertices than one UNPACK can
            # carry - NUM is 8 bits - so the position stream comes out as
            # several unpacks with the address ADVANCING, and a fixed window
            # silently drops the tail. Measured cost of getting this wrong:
            # 1019 of 4150 vertices decoded on
            # l_beverlyhills_blk_hotel_01_x, about a quarter, which made every
            # model look well inside range.
            #
            # The layout is the measured one: buffer 0 has colour 0x0A0, uv
            # 0x0D0, position 0x100, and buffer 1 repeats it from 0x1C5 with
            # colour 0x1C7, uv 0x1F7, position 0x227. So a position is anything
            # from 0x100 up to the start of the second buffer, and anything at
            # 0x227 or beyond.
            if not (0x100 <= addr < 0x1C5 or addr >= 0x227):
                continue
            cnt = (w >> 16) & 0xFF
            b = pck.off + o + 4
            if cmd == 0x69:                     # V3-16 raw
                vals = [[_s16(struct.unpack_from('<H', pck.data,
                                                 b + (i * 3 + c) * 2)[0])
                         for c in range(3)] for i in range(cnt)]
                prev = None
            elif cmd == 0x68:                   # V3-32, a single absolute row
                vals = [[_s32(pck.w(o + 4 + c * 4)) for c in range(3)]]
                prev = vals[0]
            elif cmd == 0x6A:                   # V3-8, absolute or delta
                vals = []
                for i in range(cnt):
                    r = []
                    for c in range(3):
                        v = pck.data[b + i * 3 + c]
                        v = v - 256 if v & 0x80 else v
                        if mode == 2:
                            r.append((row[c] if i == 0 else vals[i - 1][c]) + v)
                        else:
                            r.append(v)
                    vals.append(r)
                prev = vals[-1] if vals else prev
            else:
                continue
            for v in vals:
                for c in range(3):
                    f = v[c] * scale
                    if f < lo[c]:
                        lo[c] = f
                    if f > hi[c]:
                        hi[c] = f
                n += 1
    if not n:
        return None
    return max(hi[c] - lo[c] for c in range(3)), scale, n


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _s32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def find_chains(pck):
    """Chains start on the header unpack that carries the inverse scale: a
    32-bit unpack of two elements at the ITOP address. Scanning for it is
    cruder than following the geometry array, but it does not depend on having
    the object layout exactly right."""
    out = []
    n = (len(pck.data) - pck.off) // 4
    for k in range(n):
        w = pck.w(k * 4)
        if (w >> 24) not in (0x60, 0x6C):
            continue
        if ((w >> 16) & 0xFF) != 2:
            continue
        nxt = pck.w(k * 4 + 4)
        # the inverse scale, 1.0 / (1 << exponent): a float whose mantissa is
        # zero and whose exponent is below 1.0
        f = struct.unpack('<f', struct.pack('<I', nxt))[0]
        if not (0.0 < f <= 1.0):
            continue
        if nxt & 0x7FFFFF:
            continue
        out.append((k * 4, f))
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pck = Pck(argv[1])
    print('%s  %d bytes' % (os.path.basename(pck.path), len(pck.data)))
    print('  baseVA %08X  type %d  version %d  body %d'
          % (pck.base, pck.type, pck.version, pck.body))
    chains = find_chains(pck)
    print('  %d chains' % len(chains))
    if not chains:
        return 0
    exps = sorted({round(1.0 / f).bit_length() - 1 for _, f in chains})
    print('  exponent(s) %s' % exps)

    if '--chain' in argv:
        k = int(argv[argv.index('--chain') + 1])
        off, f = chains[k]
        print()
        print('chain %d at body+%06X, inverse scale %g (exponent %d)'
              % (k, off, f, round(1.0 / f).bit_length() - 1))
        for o, w, t in walk_chain(pck, off):
            print('  +%06X  %08X  %s' % (o, w, t))
        return 0

    tot = {}
    for off, f in chains:
        for o, w, t in walk_chain(pck, off):
            key = t.split('addr')[0].strip() if t.startswith('UNPACK') else \
                t.split()[0]
            tot[key] = tot.get(key, 0) + 1
    print()
    print('  what the chains are made of:')
    for k in sorted(tot, key=lambda x: -tot[x]):
        print('    %-28s %5d' % (k, tot[k]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
