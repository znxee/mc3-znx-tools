#!/usr/bin/env python3
"""Report mcSkyHatClass and the sky shader group in a savestate."""

import os
import argparse
import importlib.util
import struct
from pathlib import Path


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def load_ram(path):
    tool = Path(os.path.join(os.environ.get('MC3BOOT', 'mc3boot'), 'mc3_inject.py'))
    spec = importlib.util.spec_from_file_location('mc3_inject_sky_scan', tool)
    if spec is None or spec.loader is None:
        raise RuntimeError(tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ee_from_savestate(Path(path))


def cstring(data, pointer, limit=160):
    if not 0 < pointer < len(data):
        return None
    end = data.find(b'\0', pointer, min(len(data), pointer + limit))
    if end < 0:
        end = min(len(data), pointer + limit)
    return data[pointer:end].decode('ascii', errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state')
    args = parser.parse_args()
    ram = load_ram(args.state)
    city = u32(ram, 0x615B40)
    sky_group = u32(ram, city + 0x10)
    sky_class = u32(ram, city + 0x190)
    print('city=%#x sky_group=%#x sky_class=%#x' %
          (city, sky_group, sky_class))

    if 0 < sky_group < len(ram) - 16:
        slots = u32(ram, sky_group + 4)
        n0, n1, n2 = (u16(ram, sky_group + 8),
                      u16(ram, sky_group + 10),
                      u32(ram, sky_group + 12))
        values = [u32(ram, slots + 4 * index)
                  for index in range(min(n0, 64))]
        print('sky shader group: vtable=%#x slots=%#x counts=%d/%d/%d' %
              (u32(ram, sky_group), slots, n0, n1, n2))
        print('  nonzero=%d/%d values=%s' %
              (sum(value != 0 for value in values), len(values),
               ','.join(hex(value) for value in values)))

    if 0 < sky_class < len(ram) - 0x40:
        name_pointer = u32(ram, sky_class + 0x28)
        print('sky class: head=%d/%#x/%#x vtable=%#x instances=%d' %
              (u32(ram, sky_class), u32(ram, sky_class + 4),
               u32(ram, sky_class + 8), u32(ram, sky_class + 0x18),
               u32(ram, sky_class + 0x1C)))
        print('  name_ptr=%#x name=%r' %
              (name_pointer, cstring(ram, name_pointer)))
        print('  words20_3c=%s' % ' '.join(
            '%08X' % u32(ram, sky_class + offset)
            for offset in range(0x20, 0x40, 4)))


if __name__ == '__main__':
    main()
