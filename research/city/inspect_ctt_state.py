#!/usr/bin/env python3
"""Read-only scan for CTT9 and its hook words in PCSX2 savestates."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load_inject():
    path = Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py"
    spec = importlib.util.spec_from_file_location("mc3_inject_ctt_scan", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def main() -> None:
    inject = load_inject()
    for value in sys.argv[1:]:
        path = Path(value)
        ram = inject.ee_from_savestate(path)
        hits = []
        magic = b"CTT9"
        start = 0
        while True:
            at = ram.find(magic, start)
            if at < 0:
                break
            hits.append(at)
            start = at + 1
        hooks = {
            "city_frame": 0x00257BAC,
            "city_dispatch": 0x0024BFC4,
            "inst_dispatch": 0x00256F78,
            "city_draw_cpv_0": 0x0024C42C,
            "city_draw_0": 0x0024C4C0,
        }
        print(path)
        print("  CTT9 hits:", " ".join(f"0x{at:08X}" for at in hits) or "none")
        for name, address in hooks.items():
            word = u32(ram, address)
            target = ((address + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
            print(f"  {name:16s} {address:08X}: {word:08X} -> {target:08X}")
        for at in hits:
            words = [u32(ram, at + offset) for offset in range(0, 64, 4)]
            print("  report", f"0x{at:08X}", " ".join(f"{word:08X}" for word in words))


if __name__ == "__main__":
    main()
