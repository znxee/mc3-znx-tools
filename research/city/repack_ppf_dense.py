"""Repack an audited MC3 pf05 PPF densely without re-encoding payloads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from pathlib import Path

PF_HEADER = 0x10000
PF_STRIDE = 0x11000
PF_BLOCK = 0x800
PF_ALIGN = 0x80


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def page_start(data: bytes, page: int) -> int:
    entry = struct.unpack_from("<I", data, 12 + 4 * page)[0]
    return (entry & 0x7FFFF) * PF_BLOCK


def repack(source_ppf: Path, source_manifest: Path, output: Path) -> dict:
    old = source_ppf.read_bytes()
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    old_sha = hashlib.sha256(old).hexdigest()
    if manifest.get("sha256", "").lower() != old_sha:
        raise ValueError("source PPF SHA-256 does not match manifest")
    if old[:4] != b"pf05" or struct.unpack_from("<I", old, 8)[0] != PF_STRIDE:
        raise ValueError("unsupported PPF layout")

    pages: list[bytearray] = []
    cursors: list[int] = []
    checks: list[tuple[bytes, int, int, int]] = []

    def new_page() -> int:
        pages.append(bytearray(b"\xCD" * PF_STRIDE))
        cursors.append(0)
        return len(pages) - 1

    page = new_page()
    file_id = int(manifest["file_id_for_current_modcity"])
    for texture in manifest["textures"]:
        for level in texture["levels"]:
            old_at = page_start(old, int(level["page"])) + int(level["page_offset"])
            old_size = struct.unpack_from("<I", old, old_at + 12)[0]
            if old_size != int(level["chunk_size"]):
                raise ValueError("source chunk size does not match manifest")
            new_size = align_up(old_size, PF_ALIGN)
            if new_size + 0x20 > PF_STRIDE:
                raise ValueError("chunk does not fit in one page")
            chunk = bytearray(b"\xCD" * new_size)
            chunk[:old_size] = old[old_at:old_at + old_size]
            struct.pack_into("<I", chunk, 12, new_size)
            cursor = cursors[page]
            if cursor + new_size + 0x20 > PF_STRIDE:
                pages[page][cursor:cursor + 0x20] = bytes(0x20)
                page = new_page()
                cursor = 0
            pages[page][cursor:cursor + new_size] = chunk
            cursors[page] = cursor + new_size
            payload_size = int(level["pixel_bytes"])
            if level["last_mip"]:
                payload_size += int(level["palette_bytes"])
            checks.append((old[old_at + 0x90:old_at + 0x90 + payload_size],
                           page, cursor, payload_size))
            level.update({"page": page, "page_offset": cursor,
                          "page_word": (file_id << 23) | page,
                          "chunk_size": new_size})
    pages[page][cursors[page]:cursors[page] + 0x20] = bytes(0x20)

    header = bytearray(PF_HEADER)
    struct.pack_into("<4sII", header, 0, b"pf05", len(pages), PF_STRIDE)
    for index in range(len(pages)):
        file_offset = PF_HEADER + index * PF_STRIDE
        struct.pack_into("<HH", header, 12 + 4 * index,
                         file_offset // PF_BLOCK, PF_STRIDE // PF_ALIGN)
    data = bytes(header) + b"".join(bytes(page_data) for page_data in pages)
    for expected, page_index, offset, size in checks:
        at = PF_HEADER + page_index * PF_STRIDE + offset + 0x90
        if data[at:at + size] != expected:
            raise AssertionError("payload changed while repacking")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.write_bytes(data)
    new_sha = hashlib.sha256(data).hexdigest()
    manifest.update({"output": str(output), "sha256": new_sha,
                     "page_count": len(pages), "file_size": len(data),
                     "page_packing": "dense_sequential_0x80",
                     "supersedes_sha256": old_sha,
                     "status": "Dense PPF repacked from audited native payloads."})
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    columns = ["texture_index", "texture", "source_format", "mip_count", "level",
               "width", "height", "bpp", "psm", "page", "page_offset", "file_id",
               "page_word", "chunk_size", "pixels_offset_in_chunk", "pixel_bytes",
               "pixel_blocks", "palette_bytes", "clut_blocks", "blocks_through_end",
               "last_mip"]
    with output.with_suffix(".refs.tsv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for texture in manifest["textures"]:
            for level in texture["levels"]:
                writer.writerow({key: level[key] for key in columns})
    return {"source_sha256": old_sha, "output_sha256": new_sha,
            "pages_before": int(struct.unpack_from("<I", old, 4)[0]),
            "pages_after": len(pages), "payloads_preserved": len(checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ppf", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repack(args.source_ppf, args.source_manifest, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
