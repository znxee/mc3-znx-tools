#!/usr/bin/env python3
"""Inventory the sky shader group of a city PCK."""

import argparse
import struct
import sys


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pck')
    parser.add_argument('--tools', required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.tools)
    import mc2_bnd_parse as mc2

    pck = mc2.Pck(args.pck)
    data = pck.data
    root = 0x80
    value = u32(data, root + 0x10)
    print('%s base=%#x maproot+10=%#x' % (args.pck, pck.base, value))
    if not value or not pck.inside(pck.F(value)):
        return
    desc = pck.F(value)
    slots_value = u32(data, desc + 4)
    n0, n1, n2 = u16(data, desc + 8), u16(data, desc + 10), u32(data, desc + 12)
    print('desc=%#x token=%#x slots=%#x counts=%d/%d/%d' %
          (desc, u32(data, desc), slots_value, n0, n1, n2))
    if not slots_value or not pck.inside(pck.F(slots_value)):
        return
    slots = pck.F(slots_value)
    for index in range(n0):
        pointer = u32(data, slots + 4 * index)
        if not pointer or not pck.inside(pck.F(pointer)):
            print('  [%02d] %#x null/outside' % (index, pointer))
            continue
        shader = pck.F(pointer)
        sub_pointer = u32(data, shader + 8)
        sub = pck.F(sub_pointer) if sub_pointer and pck.inside(pck.F(sub_pointer)) else None
        print('  [%02d] shader=%#x token=%#x w04=%#x sub=%s w0c=%#x' %
              (index, shader, u32(data, shader), u32(data, shader + 4),
               hex(sub) if sub is not None else hex(sub_pointer),
               u32(data, shader + 12)))
        if sub is None:
            continue
        print('       sub token=%#x kind=%d u16_06=%d' %
              (u32(data, sub), data[sub + 4], u16(data, sub + 6)))
        pointers = []
        for offset in range(0, min(0xA0, len(data) - sub), 4):
            candidate = u32(data, sub + offset)
            if candidate and pck.inside(pck.F(candidate)):
                pointers.append('+%02X=%#x->%#x' %
                                (offset, candidate, pck.F(candidate)))
        print('       internal pointers: %s' % (', '.join(pointers) or 'none'))


if __name__ == '__main__':
    main()
