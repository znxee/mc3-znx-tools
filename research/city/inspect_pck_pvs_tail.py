#!/usr/bin/env python3
"""Inspect the serialized adaptive-distance tail of a city PCK CellIndex/PVS."""

from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import struct
import sys


BUILDER = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_city_build.py'))


def load_builder():
    spec = importlib.util.spec_from_file_location("mc3_city_build_pvs_tail", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError(BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def f32(data: bytes, at: int) -> float:
    return struct.unpack_from("<f", data, at)[0]


def main() -> None:
    builder = load_builder()
    for arg in sys.argv[1:]:
        path = Path(arg)
        pck = builder.mc2.Pck(path)
        root = 0x80
        obj = pck.F(u32(pck.data, root + 0x1C))
        unique = pck.F(u32(pck.data, obj + 8))
        object_size = unique - 0x10 - obj
        print(path)
        print(f"  base=0x{pck.base:08X} object=0x{obj:X} unique=0x{unique:X} "
              f"object_size=0x{object_size:X}")
        for off in range(0x7100, min(0x7130, object_size), 4):
            word = u32(pck.data, obj + off)
            print(f"  +0x{off:04X}: 0x{word:08X}  f32={f32(pck.data, obj + off): .9g}")
        print()


if __name__ == "__main__":
    main()
