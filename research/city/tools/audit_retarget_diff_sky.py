"""Prove that PCK retargeting changed only streamed-ref fields and kept sky valid."""
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


def pointer_offset(data: bytes, pointer: int, need: int = 4) -> int:
    value = pointer - u32(data, 0) + 0x80
    if not 0x80 <= value <= len(data) - need:
        raise ValueError(f"pointer outside PCK: {pointer:08X}")
    return value


def streamed_ref_fields(data: bytes, file_id: int) -> tuple[set[int], int]:
    descriptors = []
    group_va = u32(data, 0x8C)
    for group in range(u32(data, 0x88)):
        descriptors.append(pointer_offset(data, group_va + 16 * group, 16))
    if u32(data, 0x90):
        descriptors.append(pointer_offset(data, u32(data, 0x90), 16))
    seen = set()
    fields = set()
    refs = 0
    for descriptor in descriptors:
        slots = u16(data, descriptor + 8)
        array = pointer_offset(data, u32(data, descriptor + 4), slots * 4) if slots else 0
        for slot in range(slots):
            shader_ptr = u32(data, array + 4 * slot)
            if not shader_ptr:
                continue
            shader = pointer_offset(data, shader_ptr, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_ptr = u32(data, shader + 8)
            if texture_ptr in seen:
                continue
            seen.add(texture_ptr)
            texture = pointer_offset(data, texture_ptr, 0xA0)
            for level in range(data[texture + 0x86]):
                at = texture + 0x48 + 12 * level
                if u32(data, at + 8) >> 23 != file_id:
                    continue
                fields.update(range(at + 4, at + 12))
                refs += 1
    return fields, refs


def sky_graph(data: bytes) -> dict:
    descriptor = pointer_offset(data, u32(data, 0x90), 16)
    count = u16(data, descriptor + 8)
    counts = [count, u16(data, descriptor + 10), u32(data, descriptor + 12)]
    slots = pointer_offset(data, u32(data, descriptor + 4), count * 4)
    valid = 0
    for index in range(count):
        shader_ptr = u32(data, slots + 4 * index)
        if not shader_ptr:
            continue
        shader = pointer_offset(data, shader_ptr, 16)
        if u32(data, shader + 4) != 0:
            raise ValueError("non-basic shader in validated sky slot")
        texture = pointer_offset(data, u32(data, shader + 8), 0xA0)
        if data[texture + 4] != 0:
            raise ValueError("non-TexturePS2 object in validated sky slot")
        mip = pointer_offset(data, u32(data, texture + 0x48), 0x20)
        if u32(data, mip + 8) != u32(data, 0) - 0x80 + texture:
            raise ValueError("sky MipBuffer owner mismatch")
        valid += 1
    return {"counts": counts, "valid_slots": valid}


def audit(before_dir: Path, after_dir: Path) -> dict:
    records = []
    for after_path in sorted(after_dir.glob("losangeles_*.pck")):
        before_path = before_dir / after_path.name
        before, after = before_path.read_bytes(), after_path.read_bytes()
        if len(before) != len(after):
            raise ValueError(f"size changed: {after_path.name}")
        allowed, refs = streamed_ref_fields(before, 123)
        changed = {index for index, pair in enumerate(zip(before, after))
                   if pair[0] != pair[1]}
        unexpected = sorted(changed - allowed)
        sky_before, sky_after = sky_graph(before), sky_graph(after)
        record = {
            "file": after_path.name,
            "sha256_before": hashlib.sha256(before).hexdigest().upper(),
            "sha256_after": hashlib.sha256(after).hexdigest().upper(),
            "size_unchanged": True, "stream_refs_examined": refs,
            "changed_bytes": len(changed),
            "unexpected_changed_bytes": len(unexpected),
            "unexpected_examples": unexpected[:20],
            "sky_before": sky_before, "sky_after": sky_after,
            "sky_graph_preserved": sky_before == sky_after,
        }
        record["passed"] = (refs == 965 and not unexpected and
                            record["sky_graph_preserved"] and
                            sky_after["valid_slots"] >= 10)
        records.append(record)
    return {"passed": len(records) == 9 and all(r["passed"] for r in records),
            "method": "Byte diff restricted to fileId-123 page_offset/page_word fields; sky graph independently walked before and after.",
            "pcks": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-dir", type=Path, required=True)
    parser.add_argument("--after-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.before_dir, args.after_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "pcks": len(result["pcks"]),
                      "unexpected": sum(r["unexpected_changed_bytes"] for r in result["pcks"]),
                      "sky_valid": [r["sky_after"]["valid_slots"]
                                    for r in result["pcks"]]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
