#!/usr/bin/env python3
"""Read-only audit of MC3 city texture streaming state in a PCSX2 savestate."""

from __future__ import annotations

import os
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def u16(data: bytes | bytearray, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def u32(data: bytes | bytearray, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def ptr_ok(ram: bytes | bytearray, ptr: int, size: int = 4) -> bool:
    return 0 < ptr <= len(ram) - size


def audit(state: Path, pck_path: Path) -> dict:
    inject = load_module("mc3_inject_stream_audit", Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
    proxy_builder = load_module("build_la_texture_proxy_stream_audit",
                                Path(__file__).with_name("build_la_texture_proxy.py"))
    ram = inject.ee_from_savestate(state)
    city_file = proxy_builder.CityPck(pck_path)
    city = u32(ram, 0x615B40)
    delta = city - city_file.base
    textures = city_file.basic_textures_by_slot()

    file_ids = Counter()
    pages = Counter()
    resident_by_level = Counter()
    request_states = Counter()
    refs = []
    page_words = []
    for slot, (texture_va, texture_off) in sorted(textures.items()):
        runtime_texture = texture_va + delta
        mip_count = city_file.data[texture_off + 0x86]
        for level in range(mip_count):
            ref_file = texture_off + 0x48 + level * 12
            ref_runtime = runtime_texture + 0x48 + level * 12
            resident = u32(ram, ref_runtime)
            page_offset = u32(ram, ref_runtime + 4)
            page_word = u32(ram, ref_runtime + 8)
            file_id = page_word >> 23
            page = page_word & 0x7FFFFF
            file_ids[file_id] += 1
            pages[page] += 1
            page_words.append((file_id, page))
            if resident:
                resident_by_level[level] += 1
            refs.append({
                "slot": slot,
                "level": level,
                "resident": resident,
                "page_offset": page_offset,
                "page_word": page_word,
                "file_id": file_id,
                "page": page,
                "matches_file": ram[ref_runtime:ref_runtime + 12]
                                == city_file.data[ref_file:ref_file + 12],
            })

    pagefiles = {}
    for file_id in sorted(file_ids):
        table_slot = 0x714CC0 + file_id * 4
        pagefile = u32(ram, table_slot)
        record = {"pointer": pagefile}
        if ptr_ok(ram, pagefile, 24):
            count = u32(ram, pagefile)
            record.update({
                "page_count": count,
                "field04": u32(ram, pagefile + 4),
                "epoch08": u32(ram, pagefile + 8),
                "runtime_page_table": u32(ram, pagefile + 12),
                "ppf_index_table": u32(ram, pagefile + 16),
                "file_handle_or_name": u32(ram, pagefile + 20),
            })
            runtime_table = record["runtime_page_table"]
            index_table = record["ppf_index_table"]
            referenced_pages = sorted({page for fid, page in page_words if fid == file_id})
            runtime_values = []
            index_values = []
            for page in referenced_pages:
                if ptr_ok(ram, runtime_table + page * 4):
                    value = u32(ram, runtime_table + page * 4)
                    runtime_values.append(value)
                    if value == 0:
                        request_states["zero_unrequested"] += 1
                    elif value & 1:
                        request_states["odd_requested_or_pending"] += 1
                    else:
                        request_states["resident_page_object"] += 1
                if ptr_ok(ram, index_table + page * 4):
                    index_values.append(u32(ram, index_table + page * 4))
            record.update({
                "referenced_page_count": len(referenced_pages),
                "runtime_state_counts": dict(Counter(
                    "zero" if value == 0 else "odd" if value & 1 else "even_ptr"
                    for value in runtime_values
                )),
                "runtime_state_examples": [f"0x{value:08X}" for value in runtime_values[:16]],
                "ppf_index_nonzero": sum(value != 0 for value in index_values),
                "ppf_index_examples": [f"0x{value:08X}" for value in index_values[:16]],
            })
        pagefiles[str(file_id)] = record

    # The page manager owns pending lists in slots 11..18.  Locate its exported
    # pointer and report nearby globals separately; this remains descriptive and
    # avoids pretending that an unverified field name is known.
    globals_snapshot = {
        f"0x{address:08X}": u32(ram, address)
        for address in range(0x70E560, 0x70E590, 4)
    }

    return {
        "state": str(state),
        "pck": str(pck_path),
        "city": f"0x{city:08X}",
        "file_base": f"0x{city_file.base:08X}",
        "relocation_delta": delta,
        "texture_count": len(textures),
        "ref_count": len(refs),
        "file_id_counts": dict(file_ids),
        "unique_pages": len(pages),
        "resident_refs_by_level": dict(resident_by_level),
        "refs_matching_file": sum(ref["matches_file"] for ref in refs),
        "request_state_counts": dict(request_states),
        "pagefiles": pagefiles,
        "globals_0x70E560_0x70E58C": globals_snapshot,
        "nonresident_ref_examples": [ref for ref in refs if not ref["resident"]][:12],
        "resident_ref_examples": [ref for ref in refs if ref["resident"]][:12],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", type=Path)
    parser.add_argument("pck", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = [audit(args.state, args.pck)]
    if args.compare:
        result.append(audit(args.compare, args.pck))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
