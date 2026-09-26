"""Swap the two per-vertex colour palettes of an already built city .pck.

MC3 keeps the palette INLINE in the MapRoot, at +0x290 and +0x1290 - 256 entries
of four floats, and `rmcCpvPalette::Download(*(mcCity+0x2C))` sends it to the top
of VU1 memory, where the fourth byte of the ITOP+2 stream indexes it.

MC2 uses the SAME design: a 1-byte index per vertex and a 256-entry RGBA CPP0
palette. So the palette ports pair for pair. PS2 colour convention: alpha 0x80
and components in 0..127, so 128 is 1.0 - dividing by 255 would leave the city
at half brightness.

    python mc3_palette.py <city.pck> --cpvs <hour>_cpvs.rsc
"""
import argparse
import importlib.util
import os
import struct
import sys

SECTOR_PAL = (0x290, 0x1290)


def palette_from_cpvs(path):
    here = os.path.dirname(os.path.abspath(__file__))
    sp = importlib.util.spec_from_file_location('mc2_rsc',
                                                os.path.join(here, 'mc2_rsc.py'))
    m = importlib.util.module_from_spec(sp)
    sys.modules['mc2_rsc'] = m
    sp.loader.exec_module(m)
    r = m.Rsc(path)
    for off, fcc, size in r.entries:
        if fcc == 'CPP0':
            buf = r.data[off:off + size]
            return [struct.unpack_from('<3B', buf, k * 4) for k in range(256)]
    raise SystemExit('%s has no CPP0' % os.path.basename(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('pck')
    ap.add_argument('--cpvs', required=True)
    ap.add_argument('--out-path')
    a = ap.parse_args()
    colours = palette_from_cpvs(a.cpvs)
    d = bytearray(open(a.pck, 'rb').read())
    for base in SECTOR_PAL:
        off = 0x80 + base
        for k, (r, g, b) in enumerate(colours):
            struct.pack_into('<4f', d, off + 16 * k,
                             r / 128.0, g / 128.0, b / 128.0, 0.0)
    out_path = a.out_path or a.pck
    open(out_path, 'wb').write(bytes(d))
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in colours]
    print('%s: 2 palettes replaced by %s' % (os.path.basename(out_path),
                                             os.path.basename(a.cpvs)))
    print('  256 colours, luminance %.1f..%.1f (0..127 on the PS2, 128 = 1.0)'
          % (min(lum), max(lum)))


if __name__ == '__main__':
    main()
