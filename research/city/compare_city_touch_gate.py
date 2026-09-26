#!/usr/bin/env python3
"""Compare city/PVS/CTT9 gate state across PCSX2 savestates."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
CTT_MAGIC = b"CTT9"
CTT_FIELDS = (
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
    "first_oob_root",
)


def load_inject():
    path = Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py"
    spec = importlib.util.spec_from_file_location("mc3_inject_gate_compare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def f32(data: bytes, at: int) -> float:
    return struct.unpack_from("<f", data, at)[0]


def main() -> None:
    inject = load_inject()
    for arg in sys.argv[1:]:
        state = Path(arg)
        ram = inject.ee_from_savestate(state)
        city = u32(ram, 0x615B40)
        pvs = u32(ram, city + 0x1C) if 0 < city < len(ram) - 0x24 else 0
        patch = u32(ram, 0x1A70F0)
        immediate = patch & 0xFFFF
        if immediate & 0x8000:
            immediate -= 0x10000
        print(state)
        print(f"  frame_dt={f32(ram, 0x618E20):.9f} approx_fps="
              f"{(1.0 / f32(ram, 0x618E20)) if f32(ram, 0x618E20) > 0 else 0:.3f}")
        print(f"  budget_instruction=0x{patch:08X} immediate={immediate}")
        print(f"  config_magic=0x{u32(ram, 0x61C9F0):08X} "
              f"flags=0x{u32(ram, 0x61C9F4):08X}")
        print(f"  city=0x{city:08X} token=0x{u32(ram, city):08X} "
              f"groups_u16={u16(ram, city + 8)} groups_u32={u32(ram, city + 8)} "
              f"descs=0x{u32(ram, city + 12):08X} pvs=0x{pvs:08X}")
        if pvs:
            print(f"  pvs_limits={f32(ram, pvs + 0x710C):.6f}/"
                  f"{f32(ram, pvs + 0x7110):.6f} "
                  f"pvs_tail=" + " ".join(
                      f"{u32(ram, pvs + off):08X}" for off in range(0x7100, 0x7124, 4)
                  ))
        at = ram.find(CTT_MAGIC)
        if at < 0:
            print("  CTT9=not found")
        else:
            values = [u32(ram, at + 4 * index) for index in range(len(CTT_FIELDS))]
            print(f"  CTT9=0x{at:08X}")
            for name, value in zip(CTT_FIELDS, values):
                print(f"    {name:24s} {value:10d} 0x{value:08X}")
        print()


if __name__ == "__main__":
    main()
