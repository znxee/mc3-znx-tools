#!/usr/bin/env python3
"""Inventory generated-city shader slots and their virtual draw methods in RAM."""

from __future__ import annotations

import os
import argparse
import importlib.util
import json
import struct
from collections import Counter
from pathlib import Path


def load(path: Path):
    spec = importlib.util.spec_from_file_location("mc3_inject_shader_inventory", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state", type=Path)
    ap.add_argument("--toolkit", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    inject = load(Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
    mem = inject.ee_from_savestate(str(args.state))

    def u16(a):
        return struct.unpack_from("<H", mem, a)[0]

    def u32(a):
        return struct.unpack_from("<I", mem, a)[0]

    def sane(a):
        return 0x00100000 <= a < min(len(mem), 0x02000000) and a % 4 == 0

    city = u32(0x00615B40)
    groups = u16(city + 8) if sane(city) else 0
    table = u32(city + 0x0C) if sane(city) else 0
    rows = []
    for gi in range(groups):
        group = table + 16 * gi
        slots = u32(group + 4) if sane(group) else 0
        count = u16(group + 8) if sane(group) else 0
        if not sane(slots) or count > 4096:
            rows.append({"group": gi, "address": group, "error": "invalid slot table"})
            continue
        methods = Counter()
        kinds = Counter()
        shaders = []
        for si in range(count):
            shader = u32(slots + 4 * si)
            if not shader:
                continue
            if not sane(shader):
                shaders.append({"slot": si, "shader": shader, "error": "invalid shader"})
                continue
            vtable = u32(shader)
            flags = u32(shader + 4)
            draw = u32(vtable + 0x28) if sane(vtable) else 0
            draw_cpv = u32(vtable + 0x2C) if sane(vtable) else 0
            kind = flags & 0x7F
            bucket = (flags >> 7) & 0x1F
            methods[(vtable, draw, draw_cpv)] += 1
            kinds[(kind, bucket)] += 1
            shaders.append({"slot": si, "shader": shader, "vtable": vtable,
                            "flags": flags, "kind": kind, "bucket": bucket,
                            "draw": draw, "draw_cpv": draw_cpv})
        rows.append({"group": gi, "address": group, "slot_count": count,
                     "method_sets": [
                         {"vtable": k[0], "draw": k[1], "draw_cpv": k[2], "count": v}
                         for k, v in methods.items()],
                     "kind_buckets": [
                         {"kind": k[0], "bucket": k[1], "count": v}
                         for k, v in kinds.items()],
                     "shaders": shaders})
    model_vtable = 0x00626D98
    result = {"state": str(args.state), "city": city, "group_count": groups,
              "group_table": table,
              "model_vtable": model_vtable,
              "model_vtable_methods": [u32(model_vtable + 4 * i) for i in range(12)],
              "groups": rows}
    print(json.dumps(result, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
