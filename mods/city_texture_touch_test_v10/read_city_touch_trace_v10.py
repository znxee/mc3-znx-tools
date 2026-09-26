#!/usr/bin/env python3
"""Read the CTT10 report from an extracted PCSX2 eeMemory.bin or directory."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


FIELDS = (
    "magic", "version", "current_city", "current_descs", "active_checks",
    "dispatch_calls", "selector_calls", "selector_nonzero", "draw_calls",
    "draw_cpv_calls", "touch_calls", "touch_ready", "frame_calls",
    "frame_epoch", "touch_requests", "touch_skipped", "cache_collisions",
    "cache_full", "last_dispatch_kind", "last_dispatch_object",
    "last_dispatch_owner", "last_dispatch_pass", "last_dispatch_arg",
    "last_selector_container", "last_selector_index", "last_selector_lod",
    "last_selector_result", "last_cpv_model", "last_cpv_group",
    "last_cpv_arg5", "last_cpv_index", "last_cpv_slot_count",
    "last_cpv_roots", "last_cpv_root", "max_cpv_index", "oob_calls",
    "clamp_calls", "first_oob_model", "first_oob_group", "first_oob_arg5",
    "first_oob_index", "first_oob_slot_count", "first_oob_roots",
    "first_oob_root", "current_texture_lod", "last_touch_lod",
    "last_touch_distance_bits", "lod0_frame", "lod1_frame", "lod2_frame",
    "lod3plus_frame", "lod0_total", "lod1_total", "lod2_total",
    "lod3plus_total", "touch_lod_upgrades", "pagefile", "page_count",
    "page_stride", "page_state_table", "pages_resident", "pages_pending",
    "pages_unrequested", "page_requests", "vram_top_blocks",
    "vram_current_blocks", "vram_free_blocks", "overlay_calls",
    "overlay_draws", "overlay_font", "overlay_font_attempts",
)
FORMAT = "<" + "I" * len(FIELDS)
MAGIC = b"CT10"
HEX_FIELDS = {
    "magic", "current_city", "current_descs", "last_dispatch_object",
    "last_dispatch_owner", "last_dispatch_arg", "last_selector_container",
    "last_selector_result", "last_cpv_model", "last_cpv_group",
    "last_cpv_roots", "last_cpv_root", "first_oob_model",
    "first_oob_group", "first_oob_roots", "first_oob_root",
    "last_touch_distance_bits", "pagefile", "page_stride",
    "page_state_table", "overlay_font",
}


def memory_path(value: Path) -> Path:
    if value.is_dir():
        value = value / "eeMemory.bin"
    if not value.is_file():
        raise SystemExit(f"eeMemory.bin not found: {value}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path,
                        help="extracted state directory or eeMemory.bin")
    args = parser.parse_args()
    path = memory_path(args.state)
    data = path.read_bytes()
    reports = []
    start = 0
    while True:
        offset = data.find(MAGIC, start)
        if offset < 0:
            break
        if offset + struct.calcsize(FORMAT) <= len(data):
            values = struct.unpack_from(FORMAT, data, offset)
            if values[1] == 10:
                reports.append((offset, values))
        start = offset + 1
    if not reports:
        raise SystemExit(f"CTT10 report not found in {path}")
    for number, (offset, values) in enumerate(reports, 1):
        if len(reports) > 1:
            print(f"report {number}/{len(reports)}")
        print(f"file_offset=0x{offset:08X} ee_address=0x{offset:08X}")
        for name, value in zip(FIELDS, values):
            if name in HEX_FIELDS:
                print(f"{name}=0x{value:08X}")
            else:
                print(f"{name}={value}")


if __name__ == "__main__":
    main()
