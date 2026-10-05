"""Put a DVD9's second layer back behind a rebuilt first layer.

MC3 Remix is dual layer: layer 0's PVD (sector 16) gives its volume size V,
and layer 1 - its own PVD, directory and the VIDEO folder - starts at V - 16
(PCSX2 looks for layer 1's PVD exactly at sector V). mc3_iso_repack_root.py
rewrites layer 0's tail and changes V, which cut layer 1 off (no intro or
city videos: "open fail name VIDEOROCKSTAR.PSS;1"). This copies layer 1 from
the clean dump to V - 16 of the rebuilt image and truncates after it.

    python mc3_iso_layer1.py REBUILT.iso CLEAN.iso [--apply]
"""
import argparse
import os
import struct

SECTOR = 2048
CHUNK = 16 * 1024 * 1024
LAYER_MAX = 2295104          # sectors per layer of a DVD9


def pvd_volume(f, lba):
    f.seek(lba * SECTOR)
    d = f.read(SECTOR)
    if d[0] != 1 or d[1:6] != b'CD001':
        return None
    return struct.unpack_from('<I', d, 80)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('rebuilt')
    ap.add_argument('clean')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--no-layer-limit', action='store_true',
                    help='accept a layer 0 longer than a DVD9 layer (OPL/PCSX2 read it; '
                         'the image no longer fits a burned disc)')
    a = ap.parse_args()

    with open(a.clean, 'rb') as c:
        v_clean = pvd_volume(c, 16)
        total_clean = os.path.getsize(a.clean) // SECTOR
        if not v_clean or v_clean >= total_clean or pvd_volume(c, v_clean) is None:
            raise SystemExit('clean image has no second layer')
        l1_from, l1_count = v_clean - 16, total_clean - (v_clean - 16)
    with open(a.rebuilt, 'rb') as r:
        v_new = pvd_volume(r, 16)
    if not v_new:
        raise SystemExit('rebuilt image: no PVD at sector 16')
    if v_new > LAYER_MAX and not a.no_layer_limit:
        raise SystemExit(f'layer 0 has {v_new} sectors, a DVD9 layer holds {LAYER_MAX}')
    start = v_new - 16
    print(f'layer 1: clean sectors {l1_from}..{l1_from + l1_count} -> rebuilt {start}..{start + l1_count}')
    print(f'image: {(start + l1_count) * SECTOR} bytes')
    if not a.apply:
        print('dry run: nothing written')
        return
    with open(a.clean, 'rb') as c, open(a.rebuilt, 'r+b') as r:
        r.truncate(start * SECTOR)
        r.seek(start * SECTOR)
        c.seek(l1_from * SECTOR)
        left = l1_count * SECTOR
        while left:
            b = c.read(min(CHUNK, left))
            if not b:
                raise SystemExit('clean image ended early')
            r.write(b)
            left -= len(b)
    with open(a.rebuilt, 'rb') as r:
        if pvd_volume(r, v_new) is None:
            raise SystemExit('layer 1 PVD not found at the new break')
    print(f'layer 1 PVD at sector {v_new}: ok')


if __name__ == '__main__':
    main()
