"""Lift a city's colour-per-vertex palette out of its .pck.

WHY THIS EXISTS

rmcGeometry::Packet::Init - the function that builds the VIF packets - turns
each vertex colour into an INDEX by looking it up in the palette at
rmcCpvPalette::sm_Current (0x0070FA40), which mcCity's constructor installs
from the city object's +0x2C. Build geometry with the wrong palette loaded and
the packets come out with different indices, silently: measured, same model,
same size, different checksum.

The palette is inline in the city's own .pck, at MapRoot+0x290 (file offset
0x310), 256 entries of Vector4 float (r, g, b, 0). Every retail city has
+0x2C = 0x06800290 pointing at it. A second one sits at +0x1290.

AND IT IS PER CONDITION, not just per city: Atlanta's entry 1 is
(0.376, 0.373, 0.784) in dawn/clear and (0.800, 0.404, 0.400) in dusk/rainy.
So the file to feed a packer is chosen by city AND time AND weather - the same
three [boot] keys race_nofe already takes.

    python mc3_cpv_palette.py detroit_dawn_clear.pck -o cpv.bin
    python mc3_cpv_palette.py --compare ASSETS/resources/city
"""
import argparse
import glob
import os
import struct

ROOT = 0x80             # the MapRoot starts here in every city .pck
OFF_LIVE = 0x290        # +0x2C points at ROOT + this
OFF_SOURCE = 0x1290     # +0x30 points at ROOT + this
ENTRIES = 256
SIZE = ENTRIES * 16


def read_palette(path, source=False):
    with open(path, 'rb') as f:
        d = f.read()
    at = ROOT + (OFF_SOURCE if source else OFF_LIVE)
    expect = 0x06801290 if source else 0x06800290
    ptr = struct.unpack_from('<I', d, ROOT + (0x30 if source else 0x2C))[0]
    if ptr != expect:
        raise SystemExit('%s: +0x%02X is %08X, not the inline palette %08X'
                         % (os.path.basename(path), 0x30 if source else 0x2C, ptr, expect))
    return d[at:at + SIZE]


def entries(blob):
    return [struct.unpack_from('<4f', blob, 16 * i) for i in range(ENTRIES)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('pck', nargs='?')
    ap.add_argument('-o', '--out', help='write the raw 4096 bytes here')
    ap.add_argument('--source', action='store_true',
                    help='the second palette, at +0x1290, instead of the live one')
    ap.add_argument('--compare', metavar='DIR',
                    help='print one line per city .pck in DIR, to see what differs')
    a = ap.parse_args()

    if a.compare:
        for f in sorted(glob.glob(os.path.join(a.compare, '*.pck'))):
            try:
                e = entries(read_palette(f, a.source))
            except SystemExit:
                continue        # _bnd.pck and friends are not cities
            print('%-34s distinct=%3d  [1]=%s' % (
                os.path.basename(f), len(set(e)),
                tuple(round(x, 3) for x in e[1])))
        return

    if not a.pck:
        raise SystemExit('give a city .pck, or --compare DIR')
    blob = read_palette(a.pck, a.source)
    e = entries(blob)
    print('%s: %d entries, %d distinct, [0]=%s [255]=%s' % (
        os.path.basename(a.pck), ENTRIES, len(set(e)),
        tuple(round(x, 3) for x in e[0]), tuple(round(x, 3) for x in e[255])))
    if a.out:
        with open(a.out, 'wb') as f:
            f.write(blob)
        print('written %s (%d bytes)' % (a.out, len(blob)))


if __name__ == '__main__':
    main()
