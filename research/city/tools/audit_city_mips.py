"""Report rmcTexturePS2 mip-count distributions in MC3 city PCKs."""
from __future__ import annotations

import argparse
import collections
import json
import struct
from pathlib import Path


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def audit(path: Path) -> dict:
    data = path.read_bytes()
    header_base = u32(data, 0)

    def off(pointer: int, need: int = 4) -> int:
        value = pointer - header_base + 0x80
        if not 0x80 <= value <= len(data) - need:
            raise ValueError(f"{path.name}: pointer outside PCK: {pointer:08X}")
        return value

    descriptors = []
    group_va = u32(data, 0x8C)
    for group in range(u32(data, 0x88)):
        descriptors.append(off(group_va + 16 * group, 16))
    seen = set()
    counts = collections.Counter()
    shapes = collections.Counter()
    for descriptor in descriptors:
        slots = u16(data, descriptor + 8)
        array = off(u32(data, descriptor + 4), slots * 4) if slots else 0
        for slot in range(slots):
            shader_ptr = u32(data, array + 4 * slot)
            if not shader_ptr:
                continue
            shader = off(shader_ptr, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_ptr = u32(data, shader + 8)
            if texture_ptr in seen:
                continue
            seen.add(texture_ptr)
            texture = off(texture_ptr, 0xA0)
            if data[texture + 4] != 0:
                continue
            mip_count = data[texture + 0x86]
            width, height = u16(data, texture + 0x0C), u16(data, texture + 0x0E)
            counts[mip_count] += 1
            shapes[(width, height, mip_count)] += 1
    return {
        "file": str(path),
        "groups": len(descriptors),
        "unique_basic_textures": sum(counts.values()),
        "mip_counts": dict(sorted(counts.items())),
        "shapes": [
            {"width": key[0], "height": key[1], "mips": key[2], "count": value}
            for key, value in sorted(shapes.items())
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcks", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [audit(path) for path in args.pcks]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
