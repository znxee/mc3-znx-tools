#!/usr/bin/env python3
"""Inspect the two 0x40-byte City cullable class records in a PCK."""

import argparse
import struct
from pathlib import Path


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pck", nargs="+", type=Path)
    args = ap.parse_args()
    for path in args.pck:
        data = path.read_bytes()
        base = u32(data, 0) - 0x80
        print(path)
        print(f"  base={base:08X} root_file=00000080")
        for label, field in (("mcCityModelClass", 0x194),
                             ("mcInstCityModelClass", 0x198)):
            va = u32(data, 0x80 + field)
            off = va - base
            if not va or off < 0 or off + 0x20 > len(data):
                print(f"  {label}: invalid {va:08X}")
                continue
            values = struct.unpack_from("<8I", data, off)
            print(f"  {label}: root+{field:03X}={va:08X} file+{off:08X} "
                  f"types={values[0]} head={values[1]:08X} tail={values[2]:08X} "
                  f"vtable/token={values[6]:08X} instances={values[7]}")


if __name__ == "__main__":
    main()
