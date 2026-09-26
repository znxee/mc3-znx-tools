#!/usr/bin/env python3
"""Compare City TexturePS2 traversal order and PPF references across PCKs."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def scan(path: Path, target_file_id: int) -> dict:
    data = path.read_bytes()
    base = u32(data, 0)

    def off(pointer: int, size: int = 4) -> int:
        result = pointer - base + 0x80
        if not 0x80 <= result <= len(data) - size:
            raise ValueError(f"pointer outside {path}: 0x{pointer:08X}")
        return result

    group_base = u32(data, 0x8C)
    descriptors = [off(group_base + 16 * i, 16) for i in range(u32(data, 0x88))]
    if u32(data, 0x90):
        descriptors.append(off(u32(data, 0x90), 16))

    seen = set()
    rows = []
    for group, descriptor in enumerate(descriptors):
        count = u16(data, descriptor + 8)
        array = off(u32(data, descriptor + 4), count * 4) if count else 0
        for slot in range(count):
            shader_va = u32(data, array + 4 * slot)
            if not shader_va:
                continue
            shader = off(shader_va, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_va = u32(data, shader + 8)
            if texture_va in seen:
                continue
            seen.add(texture_va)
            texture = off(texture_va, 0xA0)
            mip_count = data[texture + 0x86]
            refs = []
            descriptors_hex = []
            for level in range(mip_count):
                ptr, page_offset, page_word = struct.unpack_from(
                    "<III", data, texture + 0x48 + 12 * level
                )
                file_id = page_word >> 23
                page = page_word & 0x7FFFFF
                if file_id == target_file_id:
                    refs.append([file_id, page, page_offset])
                    descriptors_hex.append(
                        data[texture + 8 + 16 * level:texture + 24 + 16 * level].hex()
                    )
            rows.append(
                {
                    "ordinal": len(rows),
                    "group": group,
                    "slot": slot,
                    "texture_va": f"0x{texture_va:08X}",
                    "mip_count": mip_count,
                    "refs": refs,
                    "descriptors": descriptors_hex,
                }
            )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "texture_count": len(rows),
        "streamed_texture_count": sum(bool(row["refs"]) for row in rows),
        "streamed_ref_count": sum(len(row["refs"]) for row in rows),
        "rows": rows,
    }


def compare(baseline: dict, other: dict) -> dict:
    a = baseline["rows"]
    b = other["rows"]
    limit = min(len(a), len(b))
    order_mismatches = []
    reference_mismatches = []
    descriptor_mismatches = []
    for index in range(limit):
        if (a[index]["group"], a[index]["slot"]) != (b[index]["group"], b[index]["slot"]):
            order_mismatches.append(index)
        if a[index]["refs"] != b[index]["refs"]:
            reference_mismatches.append(index)
        if a[index]["descriptors"] != b[index]["descriptors"]:
            descriptor_mismatches.append(index)
    return {
        "baseline": baseline["path"],
        "other": other["path"],
        "same_texture_count": len(a) == len(b),
        "same_traversal_positions": not order_mismatches and len(a) == len(b),
        "same_ppf_reference_sequence": not reference_mismatches and len(a) == len(b),
        "same_mip_descriptors": not descriptor_mismatches and len(a) == len(b),
        "order_mismatch_count": len(order_mismatches) + abs(len(a) - len(b)),
        "reference_mismatch_count": len(reference_mismatches) + abs(len(a) - len(b)),
        "descriptor_mismatch_count": len(descriptor_mismatches) + abs(len(a) - len(b)),
        "first_order_mismatches": order_mismatches[:20],
        "first_reference_mismatches": reference_mismatches[:20],
        "first_descriptor_mismatches": descriptor_mismatches[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("others", nargs="+", type=Path)
    parser.add_argument("--file-id", type=int, default=123)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    baseline = scan(args.baseline, args.file_id)
    others = [scan(path, args.file_id) for path in args.others]
    report = {
        "file_id": args.file_id,
        "files": [
            {key: value for key, value in item.items() if key != "rows"}
            for item in [baseline, *others]
        ],
        "comparisons": [compare(baseline, item) for item in others],
    }
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
