"""Generate the C++ aspect-ratio tables from the credited PNACH sources.

The PNACH files are the authority for the SuperType1/Remco cave and HUD work.
Their 16:9 file is explicitly a HUD companion and assumes a separate 16:9 3D
projection patch, so the two verified projection words from the toolkit's
``Video/Widescreen 16:9`` group are added without modifying the preserved
source file.  This generator deliberately refuses anything except 32-bit EE
writes, duplicate addresses with different values, or an address which cannot
be restored from the retail ELF.  Addresses below the ELF image are the PNACH
code cave and restore to zero for the 4:3 profile.

Usage:
    python generate_profiles.py --elf $MC3_HOSTFS/slus_213.55.ELF /
        --16x9 sources/16x9.pnach --21x9 sources/21x9.pnach \
        --out profiles.generated.h
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import struct


PATCH_RE = re.compile(
    r"^\s*patch=(\d+),EE,([0-9A-Fa-f]{8}),word,([0-9A-Fa-f]{8})(?:\s|$)"
)
GAME_VA = 0x001A0000

# The supplied 16:9 PNACH is titled "Widescreen 16:9 HUD" and does not carry
# the game's 3D aspect constant. These are the already-established values from
# payload/patches.h's "Video/Widescreen 16:9" group (1.777777...).
WS_16X9_PROJECTION = (
    (0x00527E14, 0x3C013FE3),
    (0x00527E18, 0x34218E34),
)


def elf_segments(data: bytes):
    phoff = struct.unpack_from("<I", data, 0x1C)[0]
    phentsize = struct.unpack_from("<H", data, 0x2A)[0]
    phnum = struct.unpack_from("<H", data, 0x2C)[0]
    result = []
    for i in range(phnum):
        off = phoff + i * phentsize
        kind, file_off, va, _pa, file_size, memory_size = struct.unpack_from(
            "<IIIIII", data, off
        )
        if kind == 1 and memory_size:
            result.append((va, file_off, file_size, memory_size))
    return result


def elf_word(data: bytes, segments, address: int) -> int:
    for va, file_off, file_size, memory_size in segments:
        if va <= address < va + memory_size:
            relative = address - va
            if relative + 4 <= file_size:
                return struct.unpack_from("<I", data, file_off + relative)[0]
            # The zero-filled tail of a PT_LOAD segment is stock BSS.
            return 0
    if address < GAME_VA:
        # Both supplied PNACH files use 0x00180000 as an injected code cave.
        return 0
    raise ValueError("address %08X is outside the retail ELF and code cave" % address)


def read_pnach(path: str):
    writes = []
    seen = {}
    with open(path, "r", encoding="utf-8-sig", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "#", "[")):
                continue
            if stripped.lower().startswith(("author=", "comment=", "gametitle=")):
                continue
            match = PATCH_RE.match(line)
            if not match:
                raise ValueError("%s:%d: unsupported line: %s" %
                                 (path, line_number, stripped))
            mode = int(match.group(1))
            if mode not in (0, 1, 2):
                raise ValueError("%s:%d: unsupported patch mode %d" %
                                 (path, line_number, mode))
            address = int(match.group(2), 16)
            value = int(match.group(3), 16)
            if address & 3:
                raise ValueError("%s:%d: unaligned word %08X" %
                                 (path, line_number, address))
            previous = seen.get(address)
            if previous is not None:
                if previous != value:
                    raise ValueError(
                        "%s:%d: %08X changes from %08X to %08X inside one profile" %
                        (path, line_number, address, previous, value)
                    )
                # PCSX2 files sometimes repeat a write as patch=0 and patch=1.
                continue
            seen[address] = value
            writes.append((address, value))
    return writes


def digest(path: str) -> str:
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest().upper()


def add_overlay(writes, overlay, profile: str):
    """Append required profile words while rejecting an accidental conflict."""
    result = list(writes)
    seen = dict(result)
    for address, value in overlay:
        previous = seen.get(address)
        if previous is not None and previous != value:
            raise ValueError(
                "%s overlay conflicts at %08X: %08X != %08X" %
                (profile, address, previous, value)
            )
        if previous is None:
            result.append((address, value))
            seen[address] = value
    return result


def emit_array(out, name: str, writes):
    out.write("static const ws_write %s[] = {\n" % name)
    for address, value in writes:
        out.write("    { 0x%08Xu, 0x%08Xu },\n" % (address, value))
    out.write("};\n")
    out.write("#define %s_COUNT %du\n\n" % (name.upper(), len(writes)))


def generate(elf_path: str, p16_path: str, p21_path: str, out_path: str):
    with open(elf_path, "rb") as stream:
        elf = stream.read()
    segments = elf_segments(elf)
    p16 = add_overlay(read_pnach(p16_path), WS_16X9_PROJECTION, "16:9")
    p21 = read_pnach(p21_path)

    union = sorted(set(address for address, _ in p16) |
                   set(address for address, _ in p21))
    stock = [(address, elf_word(elf, segments, address)) for address in union]

    with open(out_path, "w", encoding="ascii", newline="\n") as out:
        out.write("// GENERATED by generate_profiles.py. Do not edit by hand.\n")
        out.write("// Original widescreen work: SuperType1/Remco.\n")
        out.write("// 16:9 SHA-256: %s\n" % digest(p16_path))
        out.write("// 21:9 SHA-256: %s\n" % digest(p21_path))
        out.write("#ifndef MC3_PROPER_WIDESCREEN_PROFILES_H\n")
        out.write("#define MC3_PROPER_WIDESCREEN_PROFILES_H\n\n")
        out.write("struct ws_write { mc3_u32 address; mc3_u32 value; };\n\n")
        emit_array(out, "ws_stock", stock)
        emit_array(out, "ws_16x9", p16)
        emit_array(out, "ws_21x9", p21)
        out.write("#endif\n")

    print("16:9 : %d unique word writes" % len(p16))
    print("21:9 : %d unique word writes" % len(p21))
    print("stock: %d addresses restored for 4:3" % len(stock))
    print("wrote : %s" % os.path.abspath(out_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True)
    parser.add_argument("--16x9", dest="p16", required=True)
    parser.add_argument("--21x9", dest="p21", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    generate(args.elf, args.p16, args.p21, args.out)


if __name__ == "__main__":
    main()
