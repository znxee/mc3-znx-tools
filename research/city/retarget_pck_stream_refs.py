"""Retarget MC3 city TexturePS2 datChunkRefs between two PPF manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

PATTERN = re.compile(r"^modcity_(dawn|dusk|midnight)_(clear|cloudy|rainy)\.pck$")


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def mapping(old_manifest: dict, new_manifest: dict) -> dict[tuple[int, int, int], tuple[int, int]]:
    new_by_name = {texture["texture"]: texture for texture in new_manifest["textures"]}
    result = {}
    for old_texture in old_manifest["textures"]:
        new_texture = new_by_name.get(old_texture["texture"])
        if new_texture is None or len(new_texture["levels"]) != len(old_texture["levels"]):
            raise ValueError(f"texture mismatch: {old_texture['texture']}")
        for old_level, new_level in zip(old_texture["levels"], new_texture["levels"]):
            if old_level["level"] != new_level["level"]:
                raise ValueError(f"level mismatch: {old_texture['texture']}")
            key = (int(old_level["file_id"]), int(old_level["page"]),
                   int(old_level["page_offset"]))
            if key in result:
                raise ValueError(f"duplicate source key: {key}")
            result[key] = (int(new_level["page_offset"]), int(new_level["page_word"]))
    return result


def retarget(path: Path, output: Path, reloc: dict) -> dict:
    data = bytearray(path.read_bytes())
    before = bytes(data)
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
    if u32(data, 0x90):
        descriptors.append(off(u32(data, 0x90), 16))
    seen_textures = set()
    matched = set()
    other_refs = 0
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
            if texture_ptr in seen_textures:
                continue
            seen_textures.add(texture_ptr)
            texture = off(texture_ptr, 0xA0)
            for level in range(data[texture + 0x86]):
                at = texture + 0x48 + 12 * level
                _, page_offset, page_word = struct.unpack_from("<III", data, at)
                key = (page_word >> 23, page_word & 0x7FFFFF, page_offset)
                target = reloc.get(key)
                if target is None:
                    if key[0]:
                        other_refs += 1
                    continue
                if key in matched:
                    raise ValueError(f"{path.name}: multi-owner key: {key}")
                matched.add(key)
                struct.pack_into("<II", data, at + 4, *target)
    if matched != set(reloc):
        raise ValueError(f"{path.name}: matched {len(matched)}/{len(reloc)} refs")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.write_bytes(data)
    return {"file": path.name, "size": len(data), "replacements": len(matched),
            "other_streamed_refs_preserved": other_refs,
            "sha256_before": hashlib.sha256(before).hexdigest().upper(),
            "sha256_after": hashlib.sha256(data).hexdigest().upper()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-manifest", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    old = json.loads(args.old_manifest.read_text(encoding="utf-8"))
    new = json.loads(args.new_manifest.read_text(encoding="utf-8"))
    reloc = mapping(old, new)
    inputs = [path for path in sorted(args.input_dir.glob("modcity_*.pck"))
              if PATTERN.match(path.name)]
    if len(inputs) != 9:
        raise ValueError(f"expected nine PCK variants, found {len(inputs)}")
    records = [retarget(path, args.output_dir / path.name, reloc) for path in inputs]
    result = {"mapping_count": len(reloc), "pcks": records}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mapping_count": len(reloc), "pcks": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
