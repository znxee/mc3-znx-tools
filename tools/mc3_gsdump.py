#!/usr/bin/env python3
"""Read a PCSX2 GS dump (.gs.zst / .gs.xz not supported: zstd only) and say
what reached the GS, field by field.

    python mc3_gsdump.py DUMP.gs.zst                 summary per field
    python mc3_gsdump.py DUMP.gs.zst --order         draw/transfer sequence
    python mc3_gsdump.py DUMP.gs.zst --low 0x1D00    uploads below a block
                                                     (0x1D00 = start of MC3's
                                                     texture ring; the display
                                                     buffer is block 0)
    python mc3_gsdump.py DUMP.gs.zst --find HEX      transfers carrying bytes

Make the dump in PCSX2 with Shift+F8 (single frame) or Ctrl+Shift+F8; files
land in Documents/PCSX2/snaps. mc3_pcsx2_drive.py can press it: "key shift+f8".

Per draw group the summary gives the target (FBP, width, PSM, FBMSK), Z,
texture, ALPHA register, whether blending is on, and the vertex colours seen
(RGBAQ: min/max of R,G,B and A). On the GS 0x80 is 1.0 for both vertex colour
(TFX MODULATE) and alpha; a city drawn with A = 0x80 leaves 0x80 in the
framebuffer alpha, which MC3 later uses as a mask (Cs*Ad + Cd reflections).

Format (pcsx2/GS/GSDump.h): u32 0xFFFFFFFF, u32 header size, GSDumpHeader
(state_version, state_size, serial_offset, serial_size, crc, screenshot w/h/
offset/size), the GS state, 8 KB of privileged registers, then packets:
id 0 = transfer (path u8, size u32, data), 1 = vsync (field u8), 2 = read
FIFO (size u32), 3 = privileged registers (8 KB).
"""
import argparse
import struct
from collections import Counter, defaultdict

import zstandard

REG_NAMES = {0x00: 'PRIM', 0x06: 'TEX0_1', 0x07: 'TEX0_2', 0x42: 'ALPHA_1', 0x43: 'ALPHA_2',
             0x47: 'TEST_1', 0x48: 'TEST_2', 0x4C: 'FRAME_1', 0x4D: 'FRAME_2',
             0x4E: 'ZBUF_1', 0x4F: 'ZBUF_2', 0x50: 'BITBLTBUF', 0x51: 'TRXPOS',
             0x52: 'TRXREG', 0x53: 'TRXDIR'}


def load(path):
    raw = zstandard.ZstdDecompressor().stream_reader(open(path, 'rb')).read()
    o = 0
    _magic, hsize = struct.unpack_from('<II', raw, o); o += 8
    hdr = raw[o:o + hsize]; o += hsize
    ver, ssize, soff, slen, crc = struct.unpack_from('<5I', hdr)
    serial = hdr[soff:soff + slen].decode('latin1')
    o += ssize          # GS state
    o += 8192           # privileged registers
    packets = []
    while o < len(raw):
        t = raw[o]; o += 1
        if t == 0:
            n = struct.unpack_from('<I', raw, o + 1)[0]; o += 5
            packets.append(('T', raw[o:o + n])); o += n
        elif t == 1:
            packets.append(('V', raw[o])); o += 1
        elif t == 2:
            o += 4
        elif t == 3:
            o += 8192
        else:
            raise SystemExit('unknown packet id %d at %d' % (t, o))
    return dict(serial=serial, crc=crc, version=ver, packets=packets)


class Field:
    def __init__(self, vsync):
        self.vsync = vsync
        self.groups = defaultdict(lambda: dict(n=0, rgba_lo=[255] * 4, rgba_hi=[0] * 4))
        self.xfers = []
        self.order = []


def decode(dump):
    st = {k: 0 for k in REG_NAMES.values()}
    rgba = [0x80, 0x80, 0x80, 0x80]
    fields = [Field(None)]
    cur = fields[0]

    def kick():
        prim = st['PRIM']; ctx = (prim >> 9) & 1
        fr = st['FRAME_%d' % (ctx + 1)]; zb = st['ZBUF_%d' % (ctx + 1)]
        tx = st['TEX0_%d' % (ctx + 1)]; al = st['ALPHA_%d' % (ctx + 1)]
        tme, abe = (prim >> 4) & 1, (prim >> 6) & 1
        key = 'FBP %03X W%d PSM%02X msk%08X | ZBP %03X zmsk%d | %s | %s' % (
            fr & 0x1FF, (fr >> 16) & 0x3F, (fr >> 24) & 0x3F, fr >> 32, zb & 0x1FF, (zb >> 32) & 1,
            ('TEX %04X PSM%02X TCC%d TFX%d' % (tx & 0x3FFF, (tx >> 20) & 0x3F, (tx >> 34) & 1, (tx >> 35) & 3))
            if tme else 'notex',
            ('ABE ALPHA %02X FIX %02X' % (al & 0xFF, (al >> 32) & 0xFF)) if abe else 'no blend')
        g = cur.groups[key]; g['n'] += 1
        for k in range(4):
            g['rgba_lo'][k] = min(g['rgba_lo'][k], rgba[k]); g['rgba_hi'][k] = max(g['rgba_hi'][k], rgba[k])
        if not cur.order or cur.order[-1][0] != key:
            cur.order.append([key, 0])
        cur.order[-1][1] += 1

    def write(addr, val, where):
        name = REG_NAMES.get(addr)
        if name:
            st[name] = val
        if addr == 0x01:
            rgba[:] = [val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF]
        if addr == 0x53 and (val & 3) == 0:
            bb, tr, pos = st['BITBLTBUF'], st['TRXREG'], st['TRXPOS']
            x = dict(dbp=(bb >> 32) & 0x3FFF, dbw=(bb >> 48) & 0x3F, psm=(bb >> 56) & 0x3F,
                     w=tr & 0xFFF, h=(tr >> 32) & 0xFFF, dx=(pos >> 32) & 0x7FF, dy=(pos >> 48) & 0x7FF,
                     where=where)
            cur.xfers.append(x)
            cur.order.append(['XFER H->L DBP %04X W%d PSM%02X %dx%d' % (x['dbp'], x['dbw'], x['psm'], x['w'], x['h']), 1])

    for pi, p in enumerate(dump['packets']):
        if p[0] == 'V':
            cur = Field(p[1]); fields.append(cur); continue
        b = p[1]; o = 0
        while o + 16 <= len(b):
            lo, hi = struct.unpack_from('<QQ', b, o); o += 16
            nloop = lo & 0x7FFF; pre = (lo >> 46) & 1; flg = (lo >> 58) & 3
            nreg = (lo >> 60) & 0xF or 16
            if pre and flg == 0:
                st['PRIM'] = (lo >> 47) & 0x7FF
            regs = [(hi >> (4 * i)) & 0xF for i in range(nreg)]
            if flg == 0:                                   # PACKED
                for _ in range(nloop):
                    for r in regs:
                        a, c = struct.unpack_from('<QQ', b, o)
                        where = (pi, o); o += 16
                        if r == 0xE:
                            write(c & 0xFF, a, where)
                        elif r == 0x0:
                            st['PRIM'] = a & 0x7FF
                        elif r == 0x1:
                            rgba[:] = [a & 0xFF, (a >> 32) & 0xFF, c & 0xFF, (c >> 32) & 0xFF]
                        elif r in (0x5, 0xD):
                            kick()
                        elif r in (0x4, 0xC) and not (c >> 47) & 1:
                            kick()
            elif flg == 1:                                 # REGLIST
                n = nloop * nreg
                for i in range(n):
                    a = struct.unpack_from('<Q', b, o + 8 * i)[0]; r = regs[i % nreg]
                    if r == 0x0:
                        st['PRIM'] = a & 0x7FF
                    elif r == 0x1:
                        rgba[:] = [a & 0xFF, (a >> 8) & 0xFF, (a >> 16) & 0xFF, (a >> 24) & 0xFF]
                    elif r in (0x4, 0x5, 0xC, 0xD):
                        kick()
                o += 8 * (n + (n & 1))
            else:                                          # IMAGE
                o += 16 * nloop
    return fields


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('dump')
    ap.add_argument('--order', action='store_true')
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--low', type=lambda s: int(s, 0))
    ap.add_argument('--find')
    a = ap.parse_args()
    dump = load(a.dump)
    print('serial %r  crc %08X  (no disc serial = no GameDB fixes)' % (dump['serial'], dump['crc']))
    if a.find:
        sig = bytes.fromhex(a.find)
        hits = [i for i, p in enumerate(dump['packets']) if p[0] == 'T' and sig in p[1]]
        print('%d transfer packets carry %s: %s' % (len(hits), a.find, hits[:20]))
        return
    fields = decode(dump)
    for i, f in enumerate(fields):
        draws = sum(g['n'] for g in f.groups.values())
        print('=== field %d (vsync byte %s): %d vertex kicks, %d uploads' % (i, f.vsync, draws, len(f.xfers)))
        if a.low is not None:
            for x in f.xfers:
                if x['dbp'] < a.low:
                    print('   upload DBP %04X W%d PSM%02X %dx%d at %d,%d  (packet %d +%d)' % (
                        x['dbp'], x['dbw'], x['psm'], x['w'], x['h'], x['dx'], x['dy'], *x['where']))
            continue
        if a.order:
            for key, n in f.order:
                print('   %6d  %s' % (n, key))
            continue
        for key, g in sorted(f.groups.items(), key=lambda kv: -kv[1]['n'])[:a.top]:
            print('   %6d  %s | RGBA %s..%s' % (g['n'], key, '%02X%02X%02X/%02X' % tuple(g['rgba_lo']),
                                                '%02X%02X%02X/%02X' % tuple(g['rgba_hi'])))
        sizes = Counter('DBP %04X PSM%02X %dx%d' % (x['dbp'], x['psm'], x['w'], x['h']) for x in f.xfers)
        for k, n in sizes.most_common(8):
            print('   %6d  upload %s' % (n, k))


if __name__ == '__main__':
    main()
