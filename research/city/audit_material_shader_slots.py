#!/usr/bin/env python3
"""Cross-check PCK material indices against populated city shader slots in RAM."""

from __future__ import annotations

import os
import argparse
import importlib.util
import json
import struct
from collections import Counter
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pck", type=Path)
    ap.add_argument("state", type=Path)
    ap.add_argument("--toolkit", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    counts = load("city_count_material_audit", Path(__file__).with_name("audit_city_counts_runtime.py"))
    inject = load("mc3_inject_material_audit", Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
    audit = counts.Audit(args.pck)
    meshes = audit.collect()
    material_counts = Counter()
    mesh_rows = []
    for mesh, info in sorted(meshes.items()):
        ng = audit.h(mesh + 8)
        materials = audit.off(audit.u(mesh + 12), ng * 2)
        values = [audit.h(materials + 2 * i) for i in range(ng)]
        material_counts.update(values)
        mesh_rows.append({"mesh_file_offset": mesh, "kind": info["kind"],
                          "groups": ng, "materials": values})

    mem = inject.ee_from_savestate(str(args.state))
    u16 = lambda a: struct.unpack_from("<H", mem, a)[0]
    u32 = lambda a: struct.unpack_from("<I", mem, a)[0]
    city = u32(0x00615B40)
    group_count = u16(city + 8)
    group_table = u32(city + 0x0C)
    runtime = []
    for gi in range(group_count):
        group = group_table + 16 * gi
        slot_table = u32(group + 4)
        slot_count = u16(group + 8)
        populated = [i for i in range(slot_count) if u32(slot_table + 4 * i)]
        used_missing = sorted(set(material_counts) - set(populated))
        runtime.append({"group": gi, "slot_count": slot_count,
                        "populated_slots": populated,
                        "used_missing_slots": used_missing})

    result = {
        "pck_distinct_materials": sorted(material_counts),
        "pck_material_counts": dict(sorted(material_counts.items())),
        "pck_max_material": max(material_counts, default=None),
        "meshes": mesh_rows,
        "runtime_groups": runtime,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "meshes"}, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
