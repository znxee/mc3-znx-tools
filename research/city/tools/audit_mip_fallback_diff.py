"""Independently prove mip candidates change only TexturePS2 mip fields."""
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


def off(data: bytes, pointer: int, need: int = 4) -> int:
    value = pointer - u32(data, 0) + 0x80
    if not 0x80 <= value <= len(data) - need:
        raise ValueError(f"pointer outside PCK: {pointer:08X}")
    return value


def texture_offsets(data: bytes, manifest: dict) -> list[int]:
    by_ref = {}
    for index, texture in enumerate(manifest["textures"]):
        for level in texture["levels"]:
            by_ref[(int(level["page_offset"]), int(level["page_word"]))] = index
    descriptors = [off(data, u32(data, 0x8C) + 16 * index, 16)
                   for index in range(u32(data, 0x88))]
    if u32(data, 0x90):
        descriptors.append(off(data, u32(data, 0x90), 16))
    seen, found = set(), {}
    for descriptor in descriptors:
        count = u16(data, descriptor + 8)
        slots = off(data, u32(data, descriptor + 4), count * 4) if count else 0
        for slot in range(count):
            shader_pointer = u32(data, slots + 4 * slot)
            if not shader_pointer:
                continue
            shader = off(data, shader_pointer, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_pointer = u32(data, shader + 8)
            if not texture_pointer or texture_pointer in seen:
                continue
            seen.add(texture_pointer)
            texture = off(data, texture_pointer, 0xA0)
            identities = set()
            for level in range(data[texture + 0x86]):
                pointer, page_offset, page_word = struct.unpack_from(
                    "<III", data, texture + 0x48 + 12 * level)
                if pointer == 0 and page_word >> 23:
                    identity = by_ref.get((page_offset, page_word))
                    if identity is not None:
                        identities.add(identity)
            if len(identities) == 1:
                identity = identities.pop()
                if identity in found:
                    raise ValueError(f"duplicate texture owner: {identity}")
                found[identity] = texture
    missing = sorted(set(range(len(manifest["textures"]))) - set(found))
    if missing:
        raise ValueError(f"missing texture identities: {missing[:20]}")
    return [found[index] for index in range(len(manifest["textures"]))]


def sky_graph(data: bytes) -> dict:
    descriptor = off(data, u32(data, 0x90), 16)
    count = u16(data, descriptor + 8)
    counts = [count, u16(data, descriptor + 10), u32(data, descriptor + 12)]
    slots = off(data, u32(data, descriptor + 4), count * 4)
    valid = 0
    for index in range(count):
        shader_pointer = u32(data, slots + 4 * index)
        if not shader_pointer:
            continue
        shader = off(data, shader_pointer, 16)
        if u32(data, shader + 4) != 0:
            raise ValueError("non-basic sky shader")
        texture = off(data, u32(data, shader + 8), 0xA0)
        if data[texture + 4] != 0:
            raise ValueError("non-TexturePS2 sky object")
        mip = off(data, u32(data, texture + 0x48), 0x20)
        if u32(data, mip + 8) != u32(data, 0) - 0x80 + texture:
            raise ValueError("sky MipBuffer owner mismatch")
        valid += 1
    return {"counts": counts, "valid_slots": valid}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-dir", type=Path, required=True)
    parser.add_argument("--after-dir", type=Path, required=True)
    parser.add_argument("--old-manifest", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old_manifest = json.loads(args.old_manifest.read_text(encoding="utf-8"))
    new_manifest = json.loads(args.new_manifest.read_text(encoding="utf-8"))
    records = []
    for after_path in sorted(args.after_dir.glob("losangeles_*.pck")):
        before_path = args.before_dir / after_path.name
        before, after = before_path.read_bytes(), after_path.read_bytes()
        if len(before) != len(after):
            raise ValueError(f"size changed: {after_path.name}")
        offsets = texture_offsets(before, old_manifest)
        allowed = set()
        for old_texture, new_texture, texture in zip(
                old_manifest["textures"], new_manifest["textures"], offsets):
            allowed.update(range(texture + 0x48, texture + 0x78))
            if int(old_texture["mip_count"]) < int(new_texture["mip_count"]):
                allowed.update(range(texture + 0x08, texture + 0x48))
                allowed.update(range(texture + 0x80, texture + 0x85))
                allowed.add(texture + 0x86)
                allowed.update(range(texture + 0x88, texture + 0x90))
        changed = {index for index, pair in enumerate(zip(before, after))
                   if pair[0] != pair[1]}
        unexpected = sorted(changed - allowed)
        sky_before, sky_after = sky_graph(before), sky_graph(after)
        record = {"file": after_path.name, "size_unchanged": True,
                  "sha256_before": hashlib.sha256(before).hexdigest().upper(),
                  "sha256_after": hashlib.sha256(after).hexdigest().upper(),
                  "changed_bytes": len(changed),
                  "unexpected_changed_bytes": len(unexpected),
                  "unexpected_examples": unexpected[:20],
                  "sky_before": sky_before, "sky_after": sky_after,
                  "sky_graph_preserved": sky_before == sky_after}
        record["passed"] = (not unexpected and record["sky_graph_preserved"]
                            and sky_after["valid_slots"] >= 10)
        records.append(record)
    result = {"passed": len(records) == 9 and all(row["passed"] for row in records),
              "method": "Independent byte diff limited to mip descriptors, refs, allocator fields, mip count and TEX1; sky graph walked before/after.",
              "pcks": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "pcks": len(records),
                      "unexpected": sum(row["unexpected_changed_bytes"] for row in records),
                      "sky_valid": [row["sky_after"]["valid_slots"] for row in records]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
