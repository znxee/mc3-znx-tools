#!/usr/bin/env python3
"""Read and rank the BTP1 black-texture probe embedded in a PCSX2 state."""

from __future__ import annotations

import argparse
import json
import struct
import zipfile
from pathlib import Path

MAGIC = b"BTP1"
HEADER_FIELDS = (
    "magic", "version", "bytes", "capacity", "capture_frames",
    "start_frame", "last_frame", "active", "current_city",
    "model_scans", "texture_checks", "invalid_models", "invalid_groups",
    "invalid_materials", "nonbasic_shaders", "no_basic_scans",
    "unique_textures", "table_full",
    "frame_full", "frame_proxy", "frame_black",
    "frame_pending", "frame_unrequested",
    "total_full", "total_proxy", "total_black",
    "total_pending", "total_unrequested", "recovered",
    "last_texture", "last_model", "last_status", "last_lod",
    "last_page_word", "proxy_table", "proxy_count", "first_no_basic_model",
)
ENTRY_FIELDS = (
    "texture", "proxy_id", "first_model", "last_model",
    "first_frame", "last_frame", "first_page_word", "last_page_word",
    "full_checks", "proxy_checks", "black_checks", "recovered_frame",
    "last_status", "last_page_state",
)
HEADER_FORMAT = "<" + "I" * len(HEADER_FIELDS)
ENTRY_FORMAT = "<" + "I" * len(ENTRY_FIELDS)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)
STATUS = {0: "none", 1: "full", 2: "proxy", 3: "black"}


def load_memory(path: Path) -> tuple[bytes, str]:
    if path.is_dir():
        path = path / "eeMemory.bin"
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            candidates = [
                name for name in archive.namelist()
                if name.replace("\\", "/").lower().endswith("eememory.bin")
            ]
            if not candidates:
                raise SystemExit(f"eeMemory.bin not found inside {path}")
            return archive.read(candidates[0]), f"{path}!{candidates[0]}"
    if not path.is_file():
        raise SystemExit(f"state or eeMemory.bin not found: {path}")
    return path.read_bytes(), str(path)


def load_names(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(item["texture_index"]) + 1: str(item["texture"])
        for item in data.get("textures", [])
        if "texture_index" in item and "texture" in item
    }


def page_state_name(value: int) -> str:
    sentinels = {
        0xFFFFFFFF: "resident-ref",
        0xFFFFFFFE: "other-file",
        0xFFFFFFFD: "no-pagefile",
        0xFFFFFFFC: "bad-page",
    }
    if value in sentinels:
        return sentinels[value]
    if value == 0:
        return "unrequested"
    if value & 1:
        return f"pending(0x{value:X})"
    return f"page-resident/no-ref(0x{value:X})"


def find_reports(memory: bytes) -> list[tuple[int, dict, list[dict]]]:
    found = []
    start = 0
    while True:
        offset = memory.find(MAGIC, start)
        if offset < 0:
            break
        start = offset + 1
        if offset + HEADER_SIZE > len(memory):
            continue
        values = struct.unpack_from(HEADER_FORMAT, memory, offset)
        header = dict(zip(HEADER_FIELDS, values))
        capacity = header["capacity"]
        expected = HEADER_SIZE + capacity * ENTRY_SIZE
        if (header["version"] != 11 or capacity < 1 or capacity > 4096
                or header["bytes"] != expected
                or offset + expected > len(memory)):
            continue
        entries = []
        base = offset + HEADER_SIZE
        for index in range(capacity):
            row = struct.unpack_from(ENTRY_FORMAT, memory, base + index * ENTRY_SIZE)
            entry = dict(zip(ENTRY_FIELDS, row))
            if entry["texture"]:
                entries.append(entry)
        found.append((offset, header, entries))
    return found


def report_dict(offset: int, header: dict, entries: list[dict],
                names: dict[int, str]) -> dict:
    ranked = sorted(
        entries,
        key=lambda item: (
            item["black_checks"],
            item["proxy_checks"],
            -item["full_checks"],
        ),
        reverse=True,
    )
    for entry in ranked:
        entry["status_name"] = STATUS.get(entry["last_status"], "invalid")
        entry["page_state_name"] = page_state_name(entry["last_page_state"])
        entry["texture_name"] = names.get(entry["proxy_id"], "")
        entry["texture_hex"] = f"0x{entry['texture']:08X}"
        entry["first_model_hex"] = f"0x{entry['first_model']:08X}"
        entry["last_model_hex"] = f"0x{entry['last_model']:08X}"
        entry["page_word_hex"] = f"0x{entry['last_page_word']:08X}"
    clean_header = dict(header)
    for key in (
        "magic", "current_city", "last_texture", "last_model",
        "last_page_word", "proxy_table", "first_no_basic_model",
    ):
        clean_header[key] = f"0x{header[key]:08X}"
    return {
        "file_offset": f"0x{offset:08X}",
        "header": clean_header,
        "entries": ranked,
    }


def print_report(result: dict, limit: int) -> None:
    h = result["header"]
    print(f"BTP1 at {result['file_offset']}: active={h['active']} "
          f"frames={h['start_frame']}..{h['last_frame']} "
          f"textures={h['unique_textures']}/{h['capacity']}")
    print("bind totals: "
          f"full={h['total_full']} proxy={h['total_proxy']} "
          f"BLACK={h['total_black']} recovered={h['recovered']}")
    print("page misses: "
          f"pending={h['total_pending']} unrequested={h['total_unrequested']} "
          f"proxy_table={h['proxy_table']} proxy_count={h['proxy_count']}")
    print("parser: "
          f"model_scans={h['model_scans']} checks={h['texture_checks']} "
          f"invalid_model={h['invalid_models']} invalid_group={h['invalid_groups']} "
          f"invalid_material={h['invalid_materials']} no_basic={h['no_basic_scans']} "
          f"table_full={h['table_full']}")
    print("ranked textures (black, proxy, full):")
    for entry in result["entries"][:limit]:
        name = entry["texture_name"] or f"proxy#{entry['proxy_id']}"
        print(
            f"  {entry['black_checks']:6d} {entry['proxy_checks']:6d} "
            f"{entry['full_checks']:6d}  {entry['status_name']:5s} "
            f"{entry['texture_hex']} {name} model={entry['last_model_hex']} "
            f"lodpage={entry['page_word_hex']} {entry['page_state_name']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    memory, source = load_memory(args.state)
    reports = find_reports(memory)
    if not reports:
        raise SystemExit(f"BTP1 report not found in {source}")
    names = load_names(args.manifest)
    results = [report_dict(*item, names) for item in reports]
    print(f"source={source}")
    for number, result in enumerate(results, 1):
        if len(results) > 1:
            print(f"report {number}/{len(results)}")
        print_report(result, args.limit)
    if args.json:
        args.json.write_text(
            json.dumps({"source": source, "reports": results}, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

