"""Measure exact retail rmcTexturePS2 donor coverage for a mip-3 manifest."""
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


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def dimensions(tex0: int) -> tuple[int, int, int]:
    return 1 << ((tex0 >> 26) & 0xF), 1 << ((tex0 >> 30) & 0xF), (tex0 >> 20) & 0x3F


def texture_offsets(path: Path):
    data = path.read_bytes()
    base = u32(data, 0)

    def off(pointer: int, need: int = 4) -> int:
        value = pointer - base + 0x80
        if not 0x80 <= value <= len(data) - need:
            raise ValueError(f"{path.name}: pointer outside PCK: {pointer:08X}")
        return value

    groups = u32(data, 0x88)
    group_array = u32(data, 0x8C)
    seen: set[int] = set()
    for group in range(groups):
        descriptor = off(group_array + 16 * group, 16)
        slots = u16(data, descriptor + 8)
        array = off(u32(data, descriptor + 4), slots * 4) if slots else 0
        for slot in range(slots):
            shader_pointer = u32(data, array + 4 * slot)
            if not shader_pointer:
                continue
            shader = off(shader_pointer, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_pointer = u32(data, shader + 8)
            if not texture_pointer or texture_pointer in seen:
                continue
            seen.add(texture_pointer)
            texture = off(texture_pointer, 0xA0)
            if data[texture + 4] != 0:
                continue
            yield data, texture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("pcks", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    donors: dict[tuple[int, int, int, int], tuple[str, int]] = {}
    donor_counts: collections.Counter = collections.Counter()
    for path in args.pcks:
        for data, texture in texture_offsets(path):
            mips = data[texture + 0x86]
            key = (*dimensions(u64(data, texture + 0x10)), mips)
            donor_counts[key] += 1
            donors.setdefault(key, (str(path), texture))

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    missing = []
    matched = []
    for texture in manifest["textures"]:
        key = (int(texture["width"]), int(texture["height"]),
               int(texture["levels"][0]["psm"]), 3)
        donor = donors.get(key)
        row = {"texture_index": texture["texture_index"],
               "texture": texture["texture"], "key": list(key),
               "donor_count": donor_counts[key]}
        if donor is None:
            missing.append(row)
        else:
            row.update({"donor_pck": donor[0], "donor_offset": donor[1]})
            matched.append(row)
    report = {"textures": len(manifest["textures"]), "target_mips": 3,
              "matched": len(matched), "missing": len(missing),
              "missing_rows": missing, "matched_rows": matched}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("textures", "target_mips", "matched", "missing")}, indent=2))
    if missing:
        print(json.dumps(missing[:20], indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
