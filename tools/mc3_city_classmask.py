"""Patch/verify the render-pass masks of the two MC3 city cullable classes."""

import argparse
import os
import struct


BASE_VA = 0x06800000
ROOT_FILE_OFFSET = 0x80
CLASS_FIELDS = ((0x194, "mcCityModelClass"),
                (0x198, "mcInstCityModelClass"))


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def file_offset(va):
    return ROOT_FILE_OFFSET + va - BASE_VA


def inspect(data):
    found = []
    for root_off, name in CLASS_FIELDS:
        va = u32(data, ROOT_FILE_OFFSET + root_off)
        off = file_offset(va)
        if va < BASE_VA or off < ROOT_FILE_OFFSET or off + 0x40 > len(data):
            raise ValueError(f"{name}: pointer 0x{va:08X} is outside the PCK")
        found.append((name, root_off, va, off, u32(data, off)))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--mask", type=lambda s: int(s, 0), default=0x4E)
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        data = bytearray(f.read())
    before = inspect(data)
    for name, root_off, va, off, value in before:
        print(f"{name} root+0x{root_off:X} -> 0x{va:08X}: mask 0x{value:08X}")

    if not args.output:
        bad = [row for row in before if row[4] != args.mask]
        raise SystemExit(1 if bad else 0)

    for _name, _root_off, _va, off, _value in before:
        struct.pack_into("<I", data, off, args.mask)
    with open(args.output, "wb") as f:
        f.write(data)

    after = inspect(data)
    changed_offsets = [row[3] for row in before if row[4] != args.mask]
    print(f"wrote {args.output} ({len(data)} bytes)")
    print("changed file offsets: " + ", ".join(f"0x{x:X}" for x in changed_offsets))
    for name, _root_off, _va, _off, value in after:
        if value != args.mask:
            raise SystemExit(f"{name}: write verification failed")

    if os.path.getsize(args.input) != os.path.getsize(args.output):
        raise SystemExit("output size changed")


if __name__ == "__main__":
    main()
