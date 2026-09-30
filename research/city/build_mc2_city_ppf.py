from __future__ import annotations

import os
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path


PF_MAGIC = b"pf05"
PF_BLOCK = 0x800
PF_HEADER = 0x10000
PF_STRIDE = 0x11000
PF_USED_UNITS = PF_STRIDE // 0x80
FILE_ID = 123


MIPS_MAX = 0          # 0 = no limit; see the comment in build()


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def load_mc3_tex(tool_path: Path):
    spec = importlib.util.spec_from_file_location("mc3_tex_for_ppf", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {tool_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_chunk(texmod, tex, level_index: int) -> tuple[bytes, dict]:
    width, height, source = tex.levels[level_index]
    total = min(len(tex.levels), MIPS_MAX) if MIPS_MAX else len(tex.levels)
    last = level_index == total - 1
    if tex.bpp == 8:
        pixels = bytes(texmod.swizzle8(source, width, height))
        psm = 19  # PSMT8
    else:
        pixels = bytes(source)  # MC3 type 6 / PSMT4 is kept linear
        psm = 20  # PSMT4

    palette = b""
    if last:
        palette = bytes(texmod.alpha_to_ps2(tex.palette))
        if tex.bpp == 8:
            palette = bytes(texmod.swizzle_palette(palette, 256))

    payload = pixels + palette
    # Retail chunk chains keep every following header 0x80-aligned.
    # Include the inter-chunk padding in this chunk's declared size.
    chunk_size = align_up(0x100 + len(payload), 0x80)
    header = bytearray(b"\xCD" * 0x90)
    struct.pack_into("<III", header, 0x00, 0, 0, 0)
    struct.pack_into("<I", header, 0x0C, chunk_size)
    struct.pack_into("<H", header, 0x14, tex.fmt)
    struct.pack_into("<H", header, 0x16, 0xFFFF)
    # datCallback at +0x18 is invoked when the page cache releases a resident
    # chunk.  Retail and the last stable Mod City PPF store NULL here.  Leaving
    # the 0xCD debug fill in this one pointer makes eviction jump to CDCDCDCD.
    struct.pack_into("<I", header, 0x18, 0)
    tail = b"\xCD" * (chunk_size - len(header) - len(payload))
    block = bytes(header) + payload + tail
    assert len(block) == chunk_size

    pixel_blocks = align_up(len(pixels), 0x100) // 0x100
    clut_blocks = (len(palette) + 0xFF) // 0x100
    return block, {
        "level": level_index,
        "width": width,
        "height": height,
        "bpp": tex.bpp,
        "psm": psm,
        "pixel_bytes": len(pixels),
        "pixel_blocks": pixel_blocks,
        "palette_bytes": len(palette),
        "clut_blocks": clut_blocks,
        "chunk_size": chunk_size,
        "pixels_offset_in_chunk": 0x90,
        "last_mip": last,
    }


def build(texmod, source_dir: Path, output: Path, manifest_tsv: Path,
          manifest_json: Path) -> dict:
    pages: list[bytearray] = []
    page_cursors: list[int] = []
    records: list[dict] = []
    textures: list[dict] = []

    def new_page() -> int:
        pages.append(bytearray(b"\xCD" * PF_STRIDE))
        page_cursors.append(0)
        return len(pages) - 1

    source_paths = sorted(source_dir.glob("*.tex"))
    if not source_paths:
        raise ValueError(f"no .tex texture in {source_dir}")
    # A page is a streaming/cache unit, not a texture owner. Retail city PPFs
    # fill pages densely; starting a page per texture turns a 14-page runtime
    # cache into a 14-texture cache and leaves most geometry black.
    page = new_page()
    for tex_index, path in enumerate(source_paths):
        tex = texmod.read_tex(str(path))
        # LEVEL LIMIT. The engine only has rmcTexturePS2 donors with 1 or 3
        # mips: measuring atlanta+sd+detroit, the LA shapes with mips=2 have ZERO
        # donors (11.2% coverage); with mips=1 it goes to 100%. Cutting here is
        # better than inventing a third level.
        n_levels = len(tex.levels)
        if MIPS_MAX:
            n_levels = min(n_levels, MIPS_MAX)
        chunks = [make_chunk(texmod, tex, level) for level in range(n_levels)]
        tex_records = []
        for chunk, meta in chunks:
            if len(chunk) + 0x20 > PF_STRIDE:
                raise ValueError(
                    f"chunk larger than a page: {path.name} mip "
                    f"{meta['level']} = 0x{len(chunk):X} bytes"
                )
            cursor = page_cursors[page]
            # Reserve a zero-sized terminator header after the last chunk.
            if cursor + len(chunk) + 0x20 > PF_STRIDE:
                pages[page][cursor:cursor + 0x20] = bytes(0x20)
                page = new_page()
                cursor = 0
            if cursor % 0x80:
                raise AssertionError(f"misaligned chunk: page {page} +0x{cursor:X}")
            pages[page][cursor:cursor + len(chunk)] = chunk
            page_cursors[page] = cursor + len(chunk)
            rec = {
                "texture_index": tex_index,
                "texture": path.name,
                "source_format": tex.fmt,
                "mip_count": (min(tex.mipmaps, MIPS_MAX) if MIPS_MAX else tex.mipmaps),
                "page": page,
                "page_offset": cursor,
                "file_id": FILE_ID,
                "page_word": (FILE_ID << 23) | page,
                **meta,
            }
            records.append(rec)
            tex_records.append(rec)
        cursor = page_cursors[page]
        pages[page][cursor:cursor + 0x20] = bytes(0x20)

        # Used by TexturePS2 MipDesc: current level + every smaller level + CLUT.
        running = 0
        for rec in reversed(tex_records):
            running += rec["pixel_blocks"] + rec["clut_blocks"]
            rec["blocks_through_end"] = running
        textures.append({
            "texture_index": tex_index,
            "texture": path.name,
            "width": tex.width,
            "height": tex.height,
            "source_format": tex.fmt,
            "bpp": tex.bpp,
            "mip_count": (min(tex.mipmaps, MIPS_MAX) if MIPS_MAX else tex.mipmaps),
            "source_flags": [tex.f8, tex.f10, tex.f12],
            "levels": tex_records,
        })

    if len(pages) > 0xFFFF:
        raise ValueError("page count exceeds u16")
    last_block = (PF_HEADER + (len(pages) - 1) * PF_STRIDE) // PF_BLOCK
    if last_block > 0xFFFF:
        raise ValueError("block_index excede u16")

    header = bytearray(PF_HEADER)
    struct.pack_into("<4sII", header, 0, PF_MAGIC, len(pages), PF_STRIDE)
    for index in range(len(pages)):
        file_offset = PF_HEADER + index * PF_STRIDE
        struct.pack_into("<HH", header, 0x0C + index * 4,
                         file_offset // PF_BLOCK, PF_USED_UNITS)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        stream.write(header)
        for page in pages:
            stream.write(page)

    manifest_tsv.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "texture_index", "texture", "source_format", "mip_count", "level",
        "width", "height", "bpp", "psm", "page", "page_offset", "file_id",
        "page_word", "chunk_size", "pixels_offset_in_chunk", "pixel_bytes",
        "pixel_blocks", "palette_bytes", "clut_blocks", "blocks_through_end",
        "last_mip",
    ]
    with manifest_tsv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for rec in records:
            writer.writerow({key: rec[key] for key in columns})

    sha256 = hashlib.sha256(output.read_bytes()).hexdigest().upper()
    summary = {
        "format": "pf05",
        "source": str(source_dir),
        "output": str(output),
        "sha256": sha256,
        "file_id_for_current_losangeles": FILE_ID,
        "page_stride": PF_STRIDE,
        "page_count": len(pages),
        "page_packing": "dense_sequential",
        "texture_count": len(textures),
        "mip_chunk_count": len(records),
        "file_size": output.stat().st_size,
        "format_counts": dict(Counter(str(t["source_format"]) for t in textures)),
        "bpp_counts": dict(Counter(str(t["bpp"]) for t in textures)),
        "ownership_warning": (
            "Each (page,page_offset) may belong to only one datChunkRef. "
            "If the PCK creates another TexturePS2 for the same image, duplicate the chunks."
        ),
        "textures": textures,
    }
    manifest_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return summary


def verify(texmod, ppf: Path, source_dir: Path, manifest_json: Path) -> None:
    data = ppf.read_bytes()
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    if data[:4] != PF_MAGIC:
        raise AssertionError("pf05 magic missing")
    count, stride = struct.unpack_from("<II", data, 4)
    if count != manifest["page_count"] or stride != PF_STRIDE:
        raise AssertionError("pf05 header does not match the manifest")

    expected_refs = {(level["page"], level["page_offset"])
                     for texture in manifest["textures"]
                     for level in texture["levels"]}
    if len(expected_refs) != manifest["mip_chunk_count"]:
        raise AssertionError("two datChunkRefs would point at the same chunk")

    walked = set()
    for page in range(count):
        block, units = struct.unpack_from("<HH", data, 0x0C + page * 4)
        start, used = block * PF_BLOCK, units * 0x80
        pos = 0
        while True:
            size = struct.unpack_from("<I", data, start + pos + 0x0C)[0]
            if size == 0:
                break
            if pos % 0x80:
                raise AssertionError(f"page {page}: chunk +0x{pos:X} misaligned")
            if size < 0x100 or pos + size > used:
                raise AssertionError(f"page {page}: invalid size 0x{size:X}")
            callback = struct.unpack_from("<I", data, start + pos + 0x18)[0]
            if callback != 0:
                raise AssertionError(
                    f"page {page}: release callback +0x{pos + 0x18:X} "
                    f"not null ({callback:08X})"
                )
            walked.add((page, pos))
            pos += size
        if pos + 0x10 > used:
            raise AssertionError(f"page {page}: terminator outside the used area")
    if walked != expected_refs:
        raise AssertionError(f"chain/ref mismatch: walked={len(walked)} refs={len(expected_refs)}")

    for texture in manifest["textures"]:
        source = texmod.read_tex(str(source_dir / texture["texture"]))
        expected_palette = bytes(texmod.alpha_to_ps2(source.palette))
        for rec in texture["levels"]:
            level = rec["level"]
            width, height, source_pixels = source.levels[level]
            block, _units = struct.unpack_from("<HH", data, 0x0C + rec["page"] * 4)
            chunk = block * PF_BLOCK + rec["page_offset"]
            pixels_at = chunk + rec["pixels_offset_in_chunk"]
            raw = data[pixels_at:pixels_at + rec["pixel_bytes"]]
            if rec["bpp"] == 8:
                decoded = bytes(texmod.unswizzle8(raw, width, height))
            else:
                decoded = raw
            if decoded != bytes(source_pixels):
                raise AssertionError(f"pixel round trip failed: {texture['texture']} mip {level}")
            if rec["last_mip"]:
                po = pixels_at + rec["pixel_bytes"]
                pal = data[po:po + rec["palette_bytes"]]
                if rec["bpp"] == 8:
                    pal = bytes(texmod.unswizzle_palette(pal, 256))
                if pal != expected_palette:
                    raise AssertionError(f"CLUT round trip failed: {texture['texture']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile MC2 PS2 .tex files into a pf05 PPF for MC3")
    ap.add_argument("--source", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'output', 'mc2_losangeles/texture')))
    ap.add_argument("--mc3-tex", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_tex.py')))
    ap.add_argument("--output", type=Path, default=Path(os.path.join(os.environ.get('MC3_WORK', '.'), 'output', 'mc2_city_ppf/losangeles_midnight_clear.ppf')))
    args = ap.parse_args()
    manifest_tsv = args.output.with_suffix(".refs.tsv")
    manifest_json = args.output.with_suffix(".manifest.json")
    texmod = load_mc3_tex(args.mc3_tex)
    summary = build(texmod, args.source, args.output, manifest_tsv, manifest_json)
    verify(texmod, args.output, args.source, manifest_json)
    print(f"OK: {summary['texture_count']} textures, {summary['mip_chunk_count']} chunks, {summary['page_count']} pages")
    print(f"PPF: {args.output} ({summary['file_size']} bytes)")
    print(f"SHA-256: {summary['sha256']}")
    print(f"refs: {manifest_tsv}")
    print(f"manifest: {manifest_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
