#!/usr/bin/env python3
"""List or remove the trains compiled into a city's *_traffic.pck.

A city's trains do NOT come from tune/city/<city>.train at run time: they are
compiled into <city>_traffic.pck together with the ambient traffic. The file
is an aiAmbientTrafficData resource (header type 0x3C); its root object sits
at file offset 0x80 and root+0x11C points at the train manager, whose first
field is an atArray of mcTrain: pointer, u16 count, u16 capacity. Retail:
sd 2 trains, tokyo 4, detroit 1, atlanta 0 - the one city with no train.

The track a train runs on is built at load time from the city's .aib: every
road of type 3 (mcTrainBuilder::BuildTrainTracks 0x232D48). An MC2 city's
original .aib uses type 3 for other roads, so a traffic pack copied from San
Diego put its two red trams (va_tram_sd) on ordinary Los Angeles and Paris
streets. Setting the count to 0 is enough: the resource then constructs no
train and the game treats the city as it treats Atlanta.

    python mc3_traffic_trains.py losangeles_traffic.pck
    python mc3_traffic_trains.py losangeles_traffic.pck paris_traffic.pck --remove
"""
import argparse
import shutil
import struct
import sys

TRAFFIC_TYPE = 0x3C
ROOT = 0x80
TRAIN_MANAGER = 0x11C


def train_array(data):
    """File offset of the train atArray, or raise ValueError."""
    base, kind = struct.unpack_from('<II', data, 0)
    if kind != TRAFFIC_TYPE:
        raise ValueError('not a traffic pack (type 0x%X)' % kind)
    va = struct.unpack_from('<I', data, ROOT + TRAIN_MANAGER)[0]
    off = va - base + ROOT
    if not ROOT <= off <= len(data) - 8:
        raise ValueError('train manager pointer 0x%08X is outside the file' % va)
    return off


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('packs', nargs='+')
    ap.add_argument('--remove', action='store_true',
                    help='set the train count to 0 (a copy is kept as <pack>.with_trains)')
    a = ap.parse_args()
    bad = 0
    for path in a.packs:
        data = bytearray(open(path, 'rb').read())
        try:
            off = train_array(data)
        except ValueError as e:
            print('%s: %s' % (path, e))
            bad += 1
            continue
        count, cap = struct.unpack_from('<HH', data, off + 4)
        print('%s: %d train(s), capacity %d (atArray at file 0x%X)' % (path, count, cap, off))
        if a.remove and count:
            shutil.copy2(path, path + '.with_trains')
            struct.pack_into('<H', data, off + 4, 0)
            open(path, 'wb').write(data)
            print('  removed; original kept as %s.with_trains' % path)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
