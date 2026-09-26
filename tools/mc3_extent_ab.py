"""mc3_extent_ab.py - why two decoders disagree about the extent

Fork Alpha 2004 measured 120 of LA's 729 `.mesh.pck` with a local extent of
~4096 and called it a quantisation overflow. With my fixed decoder I measure
ZERO above 2000 in the same files, and on `l_beverlyhills_blk_hotel_01_x` I
match the exact vertex count (4150/4150) but get an extent of 198.8.

This script does not decide who is right. It runs BOTH readings over the same
bytes and shows which assumption produces which number, so the disagreement
becomes a command line instead of two claims.

The difference is in ONE choice: which VIF streams are position.

  A  V3 stream only at POSITION addresses   (0x100..0x1C4 and >= 0x227)
  B  every V3 stream, including the MASKED V3-8 at 0x0A0/0x1C7

In both, V3-16 and V3-8 count: position comes in both encodings, because
PackData picks an 8-bit delta when it comes out smaller. A first version of this
script accepted only V3-16 in reading A and so lost 733 of hotel_01's 4150
vertices - the same kind of mistake that had already cost me the whole decoder.

The layout, measured on a retail `.mesh.pck`: each VU buffer has colour at +2, uv
at +0x32 and position at +0x62 from ITOP - 0x0A0/0x0D0/0x100 in the first and
0x1C7/0x1F7/0x227 in the second. The stream at 0x0A0 is a masked V3-8, with
STMASK and STROW in front. If it is the normal, reading A is right; if it is
position, reading B is.

USAGE

    python mc3_extent_ab.py <file.mesh.pck>      one file, in detail
    python mc3_extent_ab.py --all <folder>       the whole folder, both counts
    python mc3_extent_ab.py --city <city.pck>    inside a city .pck

EXAMPLE THAT REPRODUCES THE DISAGREEMENT

    python mc3_extent_ab.py "output/la729_pck/l_beverlyhills_blk_hotel_01_x#geom_0_main.mesh.pck"
    python mc3_extent_ab.py --all output/la729_pck
"""
import collections
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc3_vifdis as V


# The VU addresses measured on a retail .mesh.pck.
BUF = ((0x09E, 0x0A0, 0x0D0, 0x100), (0x1C5, 0x1C7, 0x1F7, 0x227))


def classify(addr):
    """'pos', 'uv', 'colour' or None, by the destination address in the VU."""
    for itop, colour, uv, pos in BUF:
        if addr == colour:
            return 'colour'
        if addr == uv:
            return 'uv'
        if addr == itop:
            return 'hdr'
    if 0x100 <= addr < 0x1C5 or addr >= 0x227:
        return 'pos'
    return None


def le(pck, mode):
    """Return (extent, scale, vertices, streams) under reading A or B."""
    lo = [1e30] * 3
    hi = [-1e30] * 3
    raw_lo, raw_hi = 32767, -32768     # the values BEFORE the scale
    scale = None
    n = 0
    streams = collections.Counter()

    for off, qw, nv in V.walk_graph(pck):
        vif_mode = 0
        row = [0, 0, 0, 0]
        for o, w, t in V.walk_chain(pck, off, qw):
            cmd = (w >> 24) & 0xFF
            if cmd == 0x05:
                vif_mode = w & 0xFF
                continue
            if cmd == 0x30:
                row = [V._s32(pck.w(o + 4 + k * 4)) for k in range(4)]
                continue
            if not (0x60 <= cmd <= 0x7F):
                continue

            addr = w & 0x3FF
            cnt = (w >> 16) & 0xFF
            what_ = classify(addr)

            if cmd in (0x60, 0x6C) and cnt == 2:
                f = struct.unpack('<f', struct.pack('<I', pck.w(o + 4)))[0]
                if 0.0 < f <= 1.0:
                    scale = f
                continue
            if scale is None:
                continue

            masked = bool(cmd & 0x10)
            streams['%s %s%s' % (V.unpack_name(cmd & ~0x10), what_ or '?',
                                ' masked' if masked else '')] += 1

            # HERE IS THE DIFFERENCE BETWEEN THE TWO READINGS.
            if ((cmd & ~0x10) & 0x0C) != 0x08:      # only V3 streams
                continue
            if mode == 'A' and what_ != 'pos':
                continue

            b = pck.off + o + 4
            bits = V.unpack_bits(cmd)
            vals = []
            for i in range(cnt):
                r = []
                for c in range(3):
                    if bits == 16:
                        v = V._s16(struct.unpack_from(
                            '<H', pck.data, b + (i * 3 + c) * 2)[0])
                    elif bits == 8:
                        v = pck.data[b + i * 3 + c]
                        v = v - 256 if v & 0x80 else v
                        if vif_mode == 2:
                            v += row[c] if i == 0 else vals[i - 1][c]
                    else:
                        v = V._s32(pck.w(o + 4 + (i * 3 + c) * 4))
                    r.append(v)
                vals.append(r)
            for r in vals:
                for c in range(3):
                    if r[c] < raw_lo:
                        raw_lo = r[c]
                    if r[c] > raw_hi:
                        raw_hi = r[c]
                    f = r[c] * scale
                    if f < lo[c]:
                        lo[c] = f
                    if f > hi[c]:
                        hi[c] = f
                n += 1

    if not n:
        return None
    return (max(hi[c] - lo[c] for c in range(3)), scale, n, streams,
            raw_lo, raw_hi)


def one(path):
    pck = V.Pck(path)
    declared_n = sum(nv for _, _, nv in V.walk_graph(pck))
    print('%s' % os.path.basename(path))
    print('  %d groups, %d packets, %d vertices declared in the slots'
          % (pck.h(0x08), len(list(V.walk_graph(pck))), declared_n))
    print()
    for mode, text in (('A', 'V3 only at position addresses'),
                        ('B', 'every V3, including the masked one at 0x0A0')):
        r = le(pck, mode)
        if r is None:
            print('  %s  no geometry' % mode)
            continue
        ext, esc, n, streams, cl, ch = r
        reach = 32767.0 * esc
        print('  reading %s (%s)' % (mode, text))
        print('     extent %10.1f   scale %-12g reach +-%.0f'
              % (ext, esc, reach))
        print('     %d vertices  %s' % (n, 'MATCHES the declared count'
                                        if n == declared_n else
                                        '(declared: %d)' % declared_n))
        # The physical proof: overflow is a raw value hitting the s16 rail. If
        # the raw value does not get near +-32767, there was no overflow - and
        # that does not depend on who is right about which stream is position.
        rail = max(abs(cl), abs(ch))
        print('     raw s16: %d to %d   (the rail is -32768 to 32767; uses %.1f%% of it)'
              % (cl, ch, 100.0 * rail / 32767.0))
        print('     overflowed: %s'
              % ('YES - the raw value hits the rail' if rail > 32000 else
                 'no - %.0f%% margin left' % (100.0 - 100.0 * rail / 32767.0)))
        print()
    print('  streams found in the file:')
    r = le(pck, 'B')
    if r:
        for k, v in sorted(r[3].items()):
            print('     %-22s %d' % (k, v))
    print()
    print('  Both numbers come from the SAME bytes. The only difference is whether')
    print('  the masked V3-8 at 0x0A0/0x1C7 counts as position or as normal.')


def all_(folder):
    """The sweep. The signal that matters is NOT the extent - it is the rail usage.

    A model far from the origin has a huge raw value and a small extent: it is
    not big, it is just far away. Measuring the extent makes them all look safe.
    What tells whether there is a risk of overflow is how much of the s16 range
    the raw value already uses, because past the rail it wraps around silently.
    """
    files_ = sorted(glob.glob(os.path.join(folder, '*.pck')))
    if not files_:
        print('no .pck in %s' % folder)
        return 1
    lines = []
    exact = 0
    for f in files_:
        pck = V.Pck(f)
        declared_n = sum(nv for _, _, nv in V.walk_graph(pck))
        r = le(pck, 'A')
        if r is None:
            continue
        ext, esc, n, _, cl, ch = r
        if n == declared_n:
            exact += 1
        lines.append((max(abs(cl), abs(ch)) / 32767.0,
                       os.path.basename(f)[:44], ext, esc, cl, ch))
    lines.sort(reverse=True)

    range_list = ((0.98, 'on the rail  >= 98%'),
              (0.90, 'dangerous    90-98%'),
              (0.75, 'tight        75-90%'),
              (0.00, 'comfortable   < 75%'))
    print('%d files in %s, %d with an exactly matching vertex count'
          % (len(files_), folder, exact))
    print()
    print('  s16 RAIL USAGE - the signal that matters:')
    prev_ = 1.01
    for threshold, name in range_list:
        c = sum(1 for x in lines if threshold <= x[0] < prev_)
        print('     %-22s %4d' % (name, c))
        prev_ = threshold
    print()
    print('  the closest to the rail:')
    for u, n, ext, esc, cl, ch in lines[:8]:
        print('     %5.1f%%  %-46s raw %7d..%-7d scale %g'
              % (100 * u, n, cl, ch, esc))
    print()
    print('  Extent, for comparison - and why it MISLEADS here:')
    print('     above 2000: %d' % sum(1 for x in lines if x[2] > 2000))
    print('     A model far from the origin has a big raw value and a small extent.')
    print('     It is not big, just far away - and it is exactly that one that is')
    print('     at risk of overflowing.')
    return 0


def city(path):
    """Rail usage inside a CITY .pck (kind 0x43).

    A city has no mesh root at body+0, so here scanning for headers is
    legitimate instead of lazy: each SUB-BATCH has its own 'UNPACK S-32 x2'
    with the inverse scale, which is why hotel_01 had seven of them in a single
    packet. Finding the headers finds the sub-batches, and the scale comes along
    - which is all the rail measurement needs.

    The scale float is the filter against false positives: it has to be a power
    of two in (0, 1].
    """
    pck = V.Pck(path)
    print('%s  kind 0x%02X  %d body bytes'
          % (os.path.basename(path), pck.type, pck.body))
    headers = V.find_chains(pck)
    print('  %d sub-batches found' % len(headers))
    if not headers:
        return 1

    scales = collections.Counter()
    worst_ = 0
    worst_off = 0
    nv = 0
    usage_count = []
    for off, esc in headers:
        scales[esc] += 1
        raw = 0
        for o, w, t in V.walk_chain(pck, off):
            cmd = (w >> 24) & 0xFF
            if not (0x60 <= cmd <= 0x7F):
                continue
            addr = w & 0x3FF
            if classify(addr) != 'pos':
                continue
            if ((cmd & ~0x10) & 0x0C) != 0x08:
                continue
            cnt = (w >> 16) & 0xFF
            b = pck.off + o + 4
            bits = V.unpack_bits(cmd)
            if bits != 16:
                continue          # the delta is relative; the rail belongs to the absolute value
            for i in range(cnt * 3):
                v = abs(V._s16(struct.unpack_from('<H', pck.data,
                                                  b + i * 2)[0]))
                if v > raw:
                    raw = v
                nv += 1
        if raw:
            usage_count.append(raw / 32767.0)
            if raw > worst_:
                worst_, worst_off = raw, off

    print('  %d position components read' % nv)
    print()
    print('  s16 RAIL USAGE, per sub-batch:')
    prev_ = 1.01
    for threshold, name in ((0.98, 'on the rail  >= 98%'), (0.90, 'dangerous    90-98%'),
                      (0.75, 'tight        75-90%'), (0.00, 'comfortable   < 75%')):
        c = sum(1 for u in usage_count if threshold <= u < prev_)
        print('     %-22s %5d   %5.1f%%' % (name, c, 100.0 * c / max(1, len(usage_count))))
        prev_ = threshold
    print()
    print('  worst raw value: %d of 32767 (%.1f%%), in the sub-batch at body+%06X'
          % (worst_, 100.0 * worst_ / 32767.0, worst_off))
    print()
    print('  scales in use:')
    for e, c in scales.most_common(6):
        print('     %-12g %5d sub-batches   reach +-%.0f' % (e, c, 32767 * e))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == '--all':
        return all_(argv[2])
    if argv[1] == '--city':
        return city(argv[2])
    one(argv[1])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
