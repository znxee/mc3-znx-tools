"""Repack an audited MC3 pf05 PPF by texture-chain co-occurrence.

No texture payload is decoded or re-encoded.  Levels of the same texture remain
together whenever the complete chain fits a page.  Complete chains are ordered
from the placed models/materials that use them together, preserving request locality.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import struct
from pathlib import Path

PF_HEADER = 0x10000
PF_STRIDE = 0x11000
PF_BLOCK = 0x800
PF_ALIGN = 0x80
TERMINATOR_SIZE = 0x20


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def page_start(data: bytes, page: int) -> int:
    entry = struct.unpack_from("<I", data, 12 + 4 * page)[0]
    return (entry & 0x7FFFF) * PF_BLOCK


def texture_key(name: str) -> str:
    value = name.lower()
    return value[:-4] if value.endswith(".tex") else value


def locality_ranks(model_map_path: Path, place_path: Path):
    raw_model_map = json.loads(model_map_path.read_text(encoding="utf-8"))
    # MC2 .hood names are case-inconsistent while Windows asset lookup is not.
    # Match the proven porter's case-insensitive behaviour here as well.
    model_map = {name.lower(): textures for name, textures in raw_model_map.items()}
    positions: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    uses: collections.Counter[str] = collections.Counter()
    cooccurrence: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter)
    rows = mapped_rows = 0
    with place_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader((line for line in stream if not line.startswith("#")),
                                delimiter="\t")
        for row in reader:
            rows += 1
            names = model_map.get(
                f"{row['name']}_{row['lod']}_{row['part']}".lower())
            if names is None:
                continue
            mapped_rows += 1
            if row["kind"] == "comp":
                x = (float(row["eminx"]) + float(row["emaxx"])) * 0.5
                z = (float(row["eminz"]) + float(row["emaxz"])) * 0.5
            else:
                x, z = float(row["tx"]), float(row["tz"])
            normalized = sorted({texture_key(name) for name in names if name})
            for index, name in enumerate(normalized):
                for other in normalized[index + 1:]:
                    cooccurrence[name][other] += 1
                    cooccurrence[other][name] += 1
            for name in normalized:
                if not name:
                    continue
                positions[name].append((x, z))
                uses[name] += 1

    ranks = {}
    for name, values in positions.items():
        x = sum(value[0] for value in values) / len(values)
        z = sum(value[1] for value in values) / len(values)
        # A 200-unit cell is close to a city streaming neighbourhood.  Frequency
        # keeps broadly shared materials adjacent inside the same spatial cell.
        ranks[name] = (0, math.floor(z / 200.0), math.floor(x / 200.0),
                       -uses[name], name)
    return ranks, {
        "placement_rows": rows,
        "mapped_placement_rows": mapped_rows,
        "unmapped_placement_rows": rows - mapped_rows,
        "ranked_textures": len(ranks),
        "cooccurrence_edges": sum(len(values) for values in cooccurrence.values()) // 2,
    }, uses, cooccurrence


def repack(source_ppf: Path, source_manifest: Path, model_map: Path,
           place: Path, output: Path) -> dict:
    old = source_ppf.read_bytes()
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    old_sha = hashlib.sha256(old).hexdigest()
    if manifest.get("sha256", "").lower() != old_sha:
        raise ValueError("source PPF SHA-256 does not match manifest")
    if old[:4] != b"pf05" or struct.unpack_from("<I", old, 8)[0] != PF_STRIDE:
        raise ValueError("unsupported PPF layout")

    ranks, locality, uses, cooccurrence = locality_ranks(model_map, place)
    items = []
    for texture_index, texture in enumerate(manifest["textures"]):
        rank = ranks.get(texture_key(texture["texture"]),
                         (1, 0, 0, 0, texture["texture"].lower()))
        for level in texture["levels"]:
            old_at = page_start(old, int(level["page"])) + int(level["page_offset"])
            old_size = struct.unpack_from("<I", old, old_at + 12)[0]
            if old_size != int(level["chunk_size"]):
                raise ValueError("source chunk size does not match manifest")
            new_size = align_up(old_size, PF_ALIGN)
            if new_size + TERMINATOR_SIZE > PF_STRIDE:
                raise ValueError("chunk does not fit in one page")
            items.append({"texture_index": texture_index, "texture": texture,
                          "level": level, "rank": rank, "old_at": old_at,
                          "old_size": old_size, "new_size": new_size})

    # The failed mip-tier experiment proved that the runtime requests a texture
    # chain as a unit: isolating L1/L2 made 11/12 coarse pages pending while L0
    # occupied the cache.  Keep each chain indivisible when it fits.  Fill pages
    # with chains that actually co-occur in placed models; only then use size as
    # a fallback.  This is both dense and tied to measured runtime material use.
    chains: dict[int, list[dict]] = collections.defaultdict(list)
    for item in items:
        chains[item["texture_index"]].append(item)
    units: list[list[dict]] = []
    single_page_chains = split_chains = 0
    for chain in chains.values():
        chain.sort(key=lambda item: int(item["level"]["level"]))
        if sum(item["new_size"] for item in chain) + TERMINATOR_SIZE <= PF_STRIDE:
            units.append(chain)
            single_page_chains += 1
        else:
            split_chains += 1
            units.extend([[item] for item in chain])
    pages: list[list[dict]] = []
    used: list[int] = []
    remaining = set(range(len(units)))
    while remaining:
        seed = max(remaining, key=lambda index: (
            uses[texture_key(units[index][0]["texture"]["texture"])],
            sum(item["new_size"] for item in units[index])))
        selected = [seed]
        remaining.remove(seed)
        cursor = sum(item["new_size"] for item in units[seed])
        while True:
            fitting = [index for index in remaining
                       if cursor + sum(item["new_size"] for item in units[index])
                       + TERMINATOR_SIZE <= PF_STRIDE]
            if not fitting:
                break

            def candidate_score(index: int):
                name = texture_key(units[index][0]["texture"]["texture"])
                score = sum(cooccurrence[name][texture_key(
                    units[other][0]["texture"]["texture"])] for other in selected)
                size = sum(item["new_size"] for item in units[index])
                return score, uses[name] if score else 0, size

            chosen = max(fitting, key=candidate_score)
            selected.append(chosen)
            remaining.remove(chosen)
            cursor += sum(item["new_size"] for item in units[chosen])
        page_items = []
        cursor = 0
        for index in selected:
            for item in units[index]:
                item["offset"] = cursor
                page_items.append(item)
                cursor += item["new_size"]
        pages.append(page_items)
        used.append(cursor)
    packing_stats = {"page_count": len(pages),
                     "single_page_chains": single_page_chains,
                     "split_chains": split_chains}

    file_id = int(manifest["file_id_for_current_modcity"])
    page_data = [bytearray(b"\xCD" * PF_STRIDE) for _ in pages]
    checks = []
    level_bytes = collections.Counter()
    for page_index, page_items in enumerate(pages):
        for item in page_items:
            old_at, old_size = item["old_at"], item["old_size"]
            chunk = bytearray(b"\xCD" * item["new_size"])
            chunk[:old_size] = old[old_at:old_at + old_size]
            struct.pack_into("<I", chunk, 12, item["new_size"])
            offset = item["offset"]
            page_data[page_index][offset:offset + item["new_size"]] = chunk
            level = item["level"]
            payload_size = int(level["pixel_bytes"])
            if level["last_mip"]:
                payload_size += int(level["palette_bytes"])
            checks.append((old[old_at + 0x90:old_at + 0x90 + payload_size],
                           page_index, offset, payload_size))
            level_bytes[int(level["level"])] += item["new_size"]
            level.update({"page": page_index, "page_offset": offset,
                          "page_word": (file_id << 23) | page_index,
                          "chunk_size": item["new_size"]})
        page_data[page_index][used[page_index]:used[page_index] + TERMINATOR_SIZE] = \
            bytes(TERMINATOR_SIZE)

    header = bytearray(PF_HEADER)
    struct.pack_into("<4sII", header, 0, b"pf05", len(pages), PF_STRIDE)
    for index in range(len(pages)):
        file_offset = PF_HEADER + index * PF_STRIDE
        struct.pack_into("<HH", header, 12 + 4 * index,
                         file_offset // PF_BLOCK, PF_STRIDE // PF_ALIGN)
    data = bytes(header) + b"".join(bytes(value) for value in page_data)
    for expected, page_index, offset, size in checks:
        at = PF_HEADER + page_index * PF_STRIDE + offset + 0x90
        if data[at:at + size] != expected:
            raise AssertionError("payload changed while repacking")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.write_bytes(data)
    new_sha = hashlib.sha256(data).hexdigest()
    manifest.update({
        "output": str(output), "sha256": new_sha, "page_count": len(pages),
        "file_size": len(data), "page_packing": "texture_chain_cooccurrence_0x80",
        "supersedes_sha256": old_sha,
        "packing_stats": packing_stats,
        "locality_inputs": {"model_map": str(model_map), "place": str(place), **locality},
        "status": "Texture-chain/co-occurrence PPF repacked from audited native payloads.",
    })
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
    report = {
        "source_sha256": old_sha, "output_sha256": new_sha,
        "pages_before": struct.unpack_from("<I", old, 4)[0],
        "pages_after": len(pages), "payloads_preserved": len(checks),
        "packing_stats": packing_stats, "aligned_chunk_bytes_by_level": dict(level_bytes),
        "locality": locality,
    }
    output.with_suffix(".packing.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ppf", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--model-map", type=Path, required=True)
    parser.add_argument("--place", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repack(args.source_ppf, args.source_manifest, args.model_map,
                            args.place, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
