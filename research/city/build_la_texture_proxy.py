#!/usr/bin/env python3
"""Add native LA texture fallbacks to an already-built MC3 city PCK.

The LA texture PPF is streamed.  Retail cities pair that stream with an
rmcTextureProxyPS2 table containing one 16x16/4bpp thumbnail per texture.
rmcTexturePS2::Bind selects the table through the 1-based ID in TEX1 bits
21..31 whenever the requested mip is not resident.  The generated LA PCKs
currently have both the table count and all IDs set to zero, which makes a
page miss bind the global null (black) texture instead.

This tool deliberately changes only:
  * mcCity+0x228, to point at a newly appended proxy object;
  * TEX1 bits 21..31 of the 510 Basic TexturePS2 objects;
  * the PCK body size, because the proxy data is appended.

Geometry, shaders, streamed datChunkRefs and the sibling PPF are untouched.
"""

from __future__ import annotations

import os
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


TOOLS = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, str(TOOLS))
import mc3_tex  # noqa: E402


PCK_HEADER = 0x80
CITY_PROXY_PTR = PCK_HEADER + 0x228
PROXY_OBJECT_SIZE = 0x20
PROXY_WIDTH = 16
PROXY_HEIGHT = 16
PROXY_INDEX_BYTES = 128
PROXY_PALETTE_BYTES = 64
TEX1_OFFSET = 0x88
TEX1_PROXY_MASK = 0xFFE00000
MAX_PROXY_ID = 0x7FF


def u16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


class CityPck:
    def __init__(self, path: Path):
        self.path = path
        self.data = bytearray(path.read_bytes())
        if len(self.data) < PCK_HEADER:
            raise ValueError("PCK smaller than the header")
        self.base = u32(self.data, 0)
        if u32(self.data, 8) != 1:
            raise ValueError("PCK version is not 1")
        if u32(self.data, 0x0C) != len(self.data) - PCK_HEADER:
            raise ValueError("body size in the header does not add up")

    def file_offset(self, va: int, size: int = 1) -> int:
        off = va - self.base + PCK_HEADER
        if not PCK_HEADER <= off <= len(self.data) - size:
            raise ValueError(f"pointer outside the PCK: 0x{va:08X}")
        return off

    def virtual_address(self, off: int) -> int:
        if off < PCK_HEADER:
            raise ValueError("offset before the PCK body")
        va = self.base + off - PCK_HEADER
        if not 0 <= va <= 0xFFFFFFFF:
            raise ValueError("VA does not fit in u32")
        return va

    def basic_textures_by_slot(self) -> dict[int, tuple[int, int]]:
        """Return slot -> (TexturePS2 VA, TexturePS2 file offset).

        The current single-owner city format is expected: each material slot
        appears in exactly one shader group and resolves to one Basic texture.
        """
        group_count = u32(self.data, PCK_HEADER + 0x08)
        group_va = u32(self.data, PCK_HEADER + 0x0C)
        if not 1 <= group_count <= 1024:
            raise ValueError(f"implausible group count: {group_count}")

        result: dict[int, tuple[int, int]] = {}
        owner: dict[int, int] = {}
        descriptors = [self.file_offset(group_va + 16 * i, 16)
                       for i in range(group_count)]
        secondary = u32(self.data, PCK_HEADER + 0x10)
        if secondary:
            descriptors.append(self.file_offset(secondary, 16))

        for group_index, desc in enumerate(descriptors):
            count = u16(self.data, desc + 8)
            if not count:
                continue
            array_off = self.file_offset(u32(self.data, desc + 4), count * 4)
            for slot in range(count):
                shader_va = u32(self.data, array_off + slot * 4)
                if not shader_va:
                    continue
                shader_off = self.file_offset(shader_va, 12)
                shader_type = u32(self.data, shader_off + 4) & 0x7F
                if shader_type != 0:
                    raise ValueError(
                        f"slot {slot} of group {group_index} is not Basic: {shader_type}"
                    )
                texture_va = u32(self.data, shader_off + 8)
                texture_off = self.file_offset(texture_va, 0x90)
                if self.data[texture_off + 4] != 0:
                    raise ValueError(
                        f"slot {slot} does not point to a type 0 TexturePS2"
                    )
                previous = result.get(slot)
                if previous is not None and previous[0] != texture_va:
                    raise ValueError(
                        f"slot {slot} points to different textures across groups"
                    )
                previous_slot = owner.get(texture_va)
                if previous_slot is not None and previous_slot != slot:
                    raise ValueError(
                        f"TexturePS2 0x{texture_va:08X} shared by slots "
                        f"{previous_slot} and {slot}"
                    )
                result[slot] = (texture_va, texture_off)
                owner[texture_va] = slot
        return result


def resize_box_rgba(rgba: bytes, width: int, height: int,
                    out_w: int = PROXY_WIDTH,
                    out_h: int = PROXY_HEIGHT) -> bytearray:
    """Deterministic box resize which preserves RGB even when alpha is zero.

    MC3 uses RGB from some format-14/16 city textures whose stored alpha is all
    zero.  Premultiplying here would turn valid asphalt and sidewalk colours
    into black before the proxy reaches the game.
    """
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA buffer with the wrong size")
    out = bytearray(out_w * out_h * 4)
    for dy in range(out_h):
        y0 = dy * height // out_h
        y1 = max(y0 + 1, (dy + 1) * height // out_h)
        y1 = min(height, y1)
        for dx in range(out_w):
            x0 = dx * width // out_w
            x1 = max(x0 + 1, (dx + 1) * width // out_w)
            x1 = min(width, x1)
            count = 0
            sum_a = 0
            sum_r = sum_g = sum_b = 0
            for sy in range(y0, y1):
                for sx in range(x0, x1):
                    p = (sy * width + sx) * 4
                    r, g, b, a = rgba[p:p + 4]
                    count += 1
                    sum_a += a
                    sum_r += r
                    sum_g += g
                    sum_b += b
            alpha = round(sum_a / count) if count else 0
            red = round(sum_r / count) if count else 0
            green = round(sum_g / count) if count else 0
            blue = round(sum_b / count) if count else 0
            q = (dy * out_w + dx) * 4
            out[q:q + 4] = bytes((red, green, blue, alpha))
    return out


def quantize_proxy(rgba: bytes) -> tuple[bytearray, bytearray]:
    """Quantize without discarding RGB carried by fully transparent pixels."""
    quantizer_input = bytearray(rgba)
    for off in range(3, len(quantizer_input), 4):
        if quantizer_input[off] == 0:
            quantizer_input[off] = 1

    palette, indexes = mc3_tex.quantize(
        quantizer_input, PROXY_WIDTH, PROXY_HEIGHT, colors=16, alpha_max=128
    )

    # Restore the alpha represented by the original pixels.  The temporary
    # value 1 above exists only to keep the quantizer from clearing their RGB.
    alpha_sum = [0] * 16
    alpha_count = [0] * 16
    for pixel_index, palette_index in enumerate(indexes):
        alpha_sum[palette_index] += rgba[pixel_index * 4 + 3]
        alpha_count[palette_index] += 1
    for palette_index, count in enumerate(alpha_count):
        if count:
            alpha = round((alpha_sum[palette_index] / count) * 128 / 255)
            palette[palette_index * 4 + 3] = max(0, min(128, alpha))
    return palette, indexes


def build_proxy(texture_path: Path) -> tuple[bytes, bytes]:
    texture = mc3_tex.read_tex(str(texture_path))
    image = mc3_tex.tex_to_image(texture, 0)
    # Source .tex palettes use PC alpha 0..255.  Do not call Image.to_rgba()
    # with its legacy PS2->PC conversion here.
    rgba = image.to_rgba(fix_alpha=False)
    small = resize_box_rgba(rgba, image.width, image.height)
    palette, indexes = quantize_proxy(small)
    packed = bytes(mc3_tex.pack_4bpp(indexes, PROXY_WIDTH * PROXY_HEIGHT))
    if len(packed) != PROXY_INDEX_BYTES or len(palette) != PROXY_PALETTE_BYTES:
        raise AssertionError("16x16 proxy did not come to 128+64 bytes")
    return packed, bytes(palette)


def patch(input_path: Path, manifest_path: Path, output_path: Path,
          audit_path: Path | None = None) -> dict:
    city = CityPck(input_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    textures = manifest.get("textures")
    if not isinstance(textures, list) or not textures:
        raise ValueError("the manifest has no textures[]")
    count = len(textures)
    if count > MAX_PROXY_ID:
        raise ValueError(f"{count} proxies exceed the 11-bit ID")
    if manifest.get("texture_count") != count:
        raise ValueError("the manifest texture_count does not add up")
    source_root = Path(manifest["source"])

    by_slot = city.basic_textures_by_slot()
    expected_slots = set(range(count))
    if set(by_slot) != expected_slots:
        missing = sorted(expected_slots - set(by_slot))[:12]
        extra = sorted(set(by_slot) - expected_slots)[:12]
        raise ValueError(f"Basic slots do not add up; missing={missing}, extra={extra}")

    indexes = bytearray()
    palettes = bytearray()
    source_hashes = []
    for expected, entry in enumerate(textures):
        if entry.get("texture_index") != expected:
            raise ValueError(f"the manifest order breaks at index {expected}")
        name = entry["texture"]
        texture_path = source_root / name
        if not texture_path.is_file():
            raise FileNotFoundError(texture_path)
        packed, palette = build_proxy(texture_path)
        indexes += packed
        palettes += palette
        source_hashes.append(hashlib.sha256(texture_path.read_bytes()).hexdigest())

    original_sha = hashlib.sha256(city.data).hexdigest()
    old_proxy_va = u32(city.data, CITY_PROXY_PTR)
    old_proxy_off = city.file_offset(old_proxy_va, 0x14) if old_proxy_va else None
    old_count = u16(city.data, old_proxy_off) if old_proxy_off is not None else 0
    old_ids = []
    changed_tex1_offsets = []
    for slot in range(count):
        _, texture_off = by_slot[slot]
        tex1 = u64(city.data, texture_off + TEX1_OFFSET)
        old_ids.append((tex1 >> 21) & MAX_PROXY_ID)
        new_tex1 = (tex1 & ~TEX1_PROXY_MASK) | ((slot + 1) << 21)
        if new_tex1 != tex1:
            changed_tex1_offsets.append(texture_off + TEX1_OFFSET)
            struct.pack_into("<Q", city.data, texture_off + TEX1_OFFSET, new_tex1)

    append_off = align_up(len(city.data), 16)
    city.data += bytes([0xCD]) * (append_off - len(city.data))
    proxy_off = append_off
    index_off = proxy_off + PROXY_OBJECT_SIZE
    palette_off = index_off + len(indexes)
    cache32_off = palette_off + len(palettes)
    cache16_off = cache32_off + count * 4
    end_off = cache16_off + count * 2

    proxy = bytearray([0xCD] * PROXY_OBJECT_SIZE)
    struct.pack_into("<HHIIII", proxy, 0,
                     count, count,
                     city.virtual_address(index_off),
                     city.virtual_address(palette_off),
                     city.virtual_address(cache32_off),
                     city.virtual_address(cache16_off))
    city.data += proxy
    city.data += indexes
    city.data += palettes
    city.data += bytes([0xCD]) * (count * 4)
    city.data += bytes([0xCD]) * (count * 2)
    if len(city.data) != end_off:
        raise AssertionError("proxy layout does not add up")
    final_size = align_up(len(city.data), PCK_HEADER)
    city.data += bytes([0xCD]) * (final_size - len(city.data))

    new_proxy_va = city.virtual_address(proxy_off)
    struct.pack_into("<I", city.data, CITY_PROXY_PTR, new_proxy_va)
    struct.pack_into("<I", city.data, 0x0C, len(city.data) - PCK_HEADER)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(city.data)
    output_sha = hashlib.sha256(city.data).hexdigest()

    # Read the result through a new parser instance.  This catches header,
    # root-pointer and slot mistakes independently of the values above.
    result = CityPck(output_path)
    result_by_slot = result.basic_textures_by_slot()
    result_proxy_va = u32(result.data, CITY_PROXY_PTR)
    result_proxy_off = result.file_offset(result_proxy_va, 0x14)
    result_ids = []
    for slot in range(count):
        _, texture_off = result_by_slot[slot]
        result_ids.append((u64(result.data, texture_off + TEX1_OFFSET) >> 21)
                          & MAX_PROXY_ID)
    expected_ids = list(range(1, count + 1))
    if result_ids != expected_ids:
        raise AssertionError("TEX1 IDs did not come to 1..N")
    if (u16(result.data, result_proxy_off),
            u16(result.data, result_proxy_off + 2)) != (count, count):
        raise AssertionError("proxy counts do not add up")

    pointers = [u32(result.data, result_proxy_off + off)
                for off in (4, 8, 12, 16)]
    expected_offsets = [index_off, palette_off, cache32_off, cache16_off]
    actual_offsets = [result.file_offset(ptr) for ptr in pointers]
    if actual_offsets != expected_offsets:
        raise AssertionError("proxy pointers do not add up")

    report = {
        "input": str(input_path),
        "output": str(output_path),
        "manifest": str(manifest_path),
        "input_sha256": original_sha,
        "output_sha256": output_sha,
        "input_size": input_path.stat().st_size,
        "output_size": output_path.stat().st_size,
        "texture_count": count,
        "old_proxy_va": f"0x{old_proxy_va:08X}",
        "old_proxy_count": old_count,
        "old_proxy_ids_zero": sum(value == 0 for value in old_ids),
        "new_proxy_va": f"0x{result_proxy_va:08X}",
        "new_proxy_count": u16(result.data, result_proxy_off),
        "new_proxy_ids": {"min": min(result_ids), "max": max(result_ids),
                          "unique": len(set(result_ids)), "zero": result_ids.count(0)},
        "layout": {
            "object_file_offset": proxy_off,
            "indexes_file_offset": index_off,
            "indexes_bytes": len(indexes),
            "palettes_file_offset": palette_off,
            "palettes_bytes": len(palettes),
            "runtime_u32_file_offset": cache32_off,
            "runtime_u32_bytes": count * 4,
            "runtime_u16_file_offset": cache16_off,
            "runtime_u16_bytes": count * 2,
        },
        "changed_tex1_count": len(changed_tex1_offsets),
        "changed_tex1_file_offsets": changed_tex1_offsets,
        "source_texture_sha256": source_hashes,
        "unchanged_contract": "geometry, shader objects, datChunkRefs and PPF",
    }
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    report = patch(args.input, args.manifest, args.output, args.audit)
    print(json.dumps({key: report[key] for key in (
        "input_sha256", "output_sha256", "input_size", "output_size",
        "texture_count", "old_proxy_count", "old_proxy_ids_zero",
        "new_proxy_count", "new_proxy_ids", "changed_tex1_count")}, indent=2))


if __name__ == "__main__":
    main()
