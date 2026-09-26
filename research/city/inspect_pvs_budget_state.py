#!/usr/bin/env python3
"""Read the city PVS rate/budget fields from one or more PCSX2 savestates."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load_inject():
    path = Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py"
    spec = importlib.util.spec_from_file_location("mc3_inject_pvs_budget", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        pvs = u32(ram, city + 0x1C)
        dt = f32(ram, 0x618E20)
        print(state)
        print(f"  dt={dt:.9f} fps={(1.0 / dt) if dt > 0 else 0:.3f}")
        print(f"  city=0x{city:08X} pvs=0x{pvs:08X}")
        print("  pvs head:")
        for off in range(0x34, 0x64, 4):
            word = u32(ram, pvs + off)
            print(f"    +0x{off:04X}: 0x{word:08X}  f32={f32(ram, pvs + off): .9g}")
        print("  pvs adaptive tail:")
        for off in range(0x7100, 0x7124, 4):
            word = u32(ram, pvs + off)
            print(f"    +0x{off:04X}: 0x{word:08X}  f32={f32(ram, pvs + off): .9g}")
        print("  payload telemetry:")
        names = ("magic", "groups", "writes", "changed", "flushes", "passes")
        for index, name in enumerate(names):
            print(f"    {name:12s} 0x{u32(ram, 0x61C9C0 + index * 4):08X}")
        print(f"    dt           {f32(ram, 0x61C9D8):.9f}")
        print(f"    rate_smooth  {f32(ram, 0x61C9DC):.6f}")
        print(f"    rate_applied {u32(ram, 0x61C9E0)}")
        print()


if __name__ == "__main__":
    main()
