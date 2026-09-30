#!/usr/bin/env python3
"""The pins marked in game (city_mc2_rsc_draw_test: keys 1/2/3, Shift = on
the wall, Backspace = remove the last one) - list them, map them, and set up
a camera tour that photographs each one.

    python mc3_pins.py                                  # LA: $MC3_HOSTFS/mc2_pins.txt
    python mc3_pins.py --city paris                     # $MC3_HOSTFS/mc2_pins_paris.txt
    python mc3_pins.py --state "$MC3_PCSX2_DATA/sstates/mc3boot (0641698A).01.p2s"
    python mc3_pins.py --map out/pins_map.png --camtour $MC3_HOSTFS/mc2_camtour.txt

Sources: the text file the game rewrites at every pin (one per city:
host0:/mc2_pins.txt for LA, host0:/mc2_pins_paris.txt for Paris),
or a PCSX2 savestate - the pin table in EE memory starts with "MC2PINS1",
then the count and 4 more words, then 128 pins of 44 bytes (category, wall,
pin xyz, forward xyz, car xyz).
--map draws them on the city's own MC2 map (hud_map_la / hud_map_paris, the
extents of <city>.lvl);
--camtour writes the points for [boot] mc2t (mc3_cam_tour.py), each pin seen
from 8 m behind where the car stood.
"""
import argparse
import os
import struct
import sys
import zipfile

NAMES = {1: 'texture', 2: 'collision', 3: 'other'}
COLOURS = {1: (255, 140, 0), 2: (230, 30, 30), 3: (60, 140, 255)}
MC2_ASSETS = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
CITIES = {   # key: (pins file, MC2 map texture, MC2 city folder)
    'la': (os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc2_pins.txt'), 'hud_map_la.tex', 'losangeles'),
    'paris': (os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc2_pins_paris.txt'), 'hud_map_paris.tex', 'paris'),
}


def lvl_extents(folder):
    """(min x, max x, min z, max z) from <folder>.lvl of the installed originals."""
    import re
    t = open(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc2/%s/city/%s.lvl') % (folder, folder)).read()
    lo = [float(v) for v in re.search(r'extents_min\s*\{\s*([^}]*)\}', t).group(1).split()]
    hi = [float(v) for v in re.search(r'extents_max\s*\{\s*([^}]*)\}', t).group(1).split()]
    return lo[0], hi[0], lo[2], hi[2]


def from_text(path):
    pins = []
    for line in open(path):
        w = line.split()
        if not w or w[0].startswith('#'):
            continue
        v = [float(x) for x in w[3:11]]
        pins.append({'category': int(w[1]), 'wall': w[2] == 'wall', 'pos': v[0:3],
                     'forward': (v[3], 0.0, v[4]), 'car': v[5:8]})
    return pins


def ee_memory(path):
    import zstandard
    z = zipfile.ZipFile(path)
    for info in z.infolist():
        if info.filename.endswith('eeMemory.bin'):
            z.fp.seek(info.header_offset)
            h = z.fp.read(30)
            n, e = struct.unpack_from('<HH', h, 26)
            z.fp.seek(info.header_offset + 30 + n + e)
            data = z.fp.read(info.compress_size)
            if info.compress_type == 93:
                return zstandard.ZstdDecompressor().decompress(data, max_output_size=64 << 20)
            return data if info.compress_type == 0 else z.read(info.filename)
    raise SystemExit('no eeMemory.bin in %s' % path)


def from_state(path):
    ee = ee_memory(path)
    at = ee.find(b'MC2PINS1')
    if at < 0:
        raise SystemExit('no pin table in the savestate (no pin marked since the city loaded?)')
    count = struct.unpack_from('<I', ee, at + 8)[0]
    base = at + 8 + 5 * 4                      # count, keys, mem, mats, saved
    pins = []
    for i in range(min(count, 128)):
        c, wall = struct.unpack_from('<2I', ee, base + 44 * i)
        f = struct.unpack_from('<9f', ee, base + 44 * i + 8)
        pins.append({'category': c, 'wall': bool(wall), 'pos': f[0:3], 'forward': f[3:6], 'car': f[6:9]})
    return pins


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', choices=sorted(CITIES), default='la')
    ap.add_argument('--pins', help='pins file (default: the file of --city)')
    ap.add_argument('--state', help='a PCSX2 .p2s instead of the text file')
    ap.add_argument('--map', help="PNG of the pins on the city's map")
    ap.add_argument('--camtour', help='write mc2t points (eye, target) looking at each pin')
    a = ap.parse_args()
    pins_file, map_tex, folder = CITIES[a.city]

    pins = from_state(a.state) if a.state else from_text(a.pins or pins_file)
    print('%d pins' % len(pins))
    for i, p in enumerate(pins):
        print('  %2d  %-9s %-6s at %8.1f %6.1f %8.1f' % (
            i, NAMES.get(p['category'], '?'), 'wall' if p['wall'] else 'ground', *p['pos']))

    if a.camtour:
        with open(a.camtour, 'w') as fh:
            fh.write('# mc3_pins.py: each pin from 8 m behind the car that marked it\n')
            for p in pins:
                fx, fz = p['forward'][0], p['forward'][2]
                x, y, z = p['pos']
                fh.write('%.2f %.2f %.2f %.2f %.2f %.2f\n' % (
                    x - fx * 8, y + 3, z - fz * 8, x, y + 0.5, z))
        print('camera tour -> %s' % a.camtour)

    if a.map:
        from PIL import Image, ImageDraw
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import mc3_tex
        try:
            t = mc3_tex.tex_to_image(mc3_tex.read_tex('%s/texture/%s' % (MC2_ASSETS, map_tex)))
            img = Image.frombytes('RGBA', (t.width, t.height), bytes(t.to_rgba()))
            bg = Image.new('RGBA', img.size, (25, 25, 30, 255))
            img = Image.alpha_composite(bg, img).convert('RGB')
        except Exception as e:
            print('  no MC2 map (%s), plain background' % e)
            img = Image.new('RGB', (512, 256), (30, 30, 30))
        img = img.resize((1024, 512))
        W, H = img.size
        dr = ImageDraw.Draw(img)
        x0, x1, z0, z1 = lvl_extents(folder)
        for i, p in enumerate(pins):
            u = (p['pos'][0] - x0) / (x1 - x0) * W
            v = H - (p['pos'][2] - z0) / (z1 - z0) * H
            dr.ellipse([u - 6, v - 6, u + 6, v + 6], fill=COLOURS.get(p['category'], (255, 255, 255)),
                       outline=(255, 255, 255))
            dr.text((u + 8, v - 6), str(i), fill=(255, 255, 255))
        img.save(a.map)
        print('map -> %s' % a.map)
    return 0


if __name__ == '__main__':
    sys.exit(main())
