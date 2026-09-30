#!/usr/bin/env python3
"""Read CPAS pass/callsite coverage from a PCSX2 savestate."""

from __future__ import annotations

import os
import argparse
import hashlib
import importlib.util
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
MAGIC = b"CPAS"
ENTRY_COUNT = 256
LABELS = (
    "unique:p2:g1:cpv",
    "unique:p8:g0:cpv",
    "unique:p4/p8:g2/g3:draw",
    "unique:p64:g4:cpv",
    "instance:p2:g1:cpv",
    "instance:p8:g0:cpv",
    "instance:p4/p8:g2/g3:draw",
    "instance:p64:g4:cpv",
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def embedded_fingerprint(audit, mesh):
    h = hashlib.sha256()
    ng = audit.h(mesh + 8)
    table = audit.off(audit.u(mesh + 16), ng * 8)
    for group in range(ng):
        listing = audit.off(audit.u(table + 8 * group),
                            audit.h(table + 8 * group + 4) * 8)
        count = audit.h(table + 8 * group + 4)
        h.update(struct.pack("<II", group, count))
        for block in range(count):
            entry = listing + 8 * block
            qw, vertices = audit.h(entry + 4), audit.h(entry + 6)
            payload = audit.off(audit.u(entry), qw * 16)
            h.update(struct.pack("<II", qw, vertices))
            h.update(audit.d[payload:payload + qw * 16])
    return h.digest()


def standalone_fingerprints(builder, mesh_dir):
    result = defaultdict(list)
    if not mesh_dir:
        return result
    for path in sorted(mesh_dir.glob("*.mesh.pck")):
        h = hashlib.sha256()
        try:
            groups = builder.read_pck_groups(str(path))
            for group, packets in enumerate(groups):
                h.update(struct.pack("<II", group, len(packets)))
                for payload, qw, vertices in packets:
                    h.update(struct.pack("<II", qw, vertices))
                    h.update(payload)
        except Exception:
            continue
        result[h.digest()].append(path.name.removesuffix(".mesh.pck"))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("state", type=Path)
    ap.add_argument("--pck", type=Path,
                    default=Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/losangeles_midnight_clear.pck')))
    ap.add_argument("--mesh-dir", type=Path,
                    default=TOOLKIT / "output" / "city_v7_vif_relocation_20260906" /
                    "la729_local_components_20260906")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    inject = load("mc3_inject_cpas", Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
    counts = load("city_counts_cpas", Path(__file__).with_name("audit_city_counts_runtime.py"))
    builder = load("city_build_cpas", TOOLKIT / "mc3_city_build.py")
    ram = inject.ee_from_savestate(str(args.state))

    candidates = []
    start = 0
    while True:
        at = ram.find(MAGIC, start)
        if at < 0:
            break
        if at + 52 + ENTRY_COUNT * 8 <= len(ram):
            version, size = struct.unpack_from("<II", ram, at + 4)
            if version == 1 and size == 52 + ENTRY_COUNT * 8:
                candidates.append(at)
        start = at + 1
    if len(candidates) != 1:
        raise SystemExit(f"expected one CPAS block, found {len(candidates)}: {candidates}")
    at = candidates[0]
    unique, overflow = struct.unpack_from("<II", ram, at + 12)
    calls = struct.unpack_from("<8I", ram, at + 20)

    audit = counts.Audit(args.pck, str(args.state))
    meshes = audit.collect()
    by_offset = {offset: info for offset, info in meshes.items()}
    source = standalone_fingerprints(builder, args.mesh_dir)
    rows = []
    for slot in range(ENTRY_COUNT):
        model, mask = struct.unpack_from("<II", ram, at + 52 + slot * 8)
        if not model:
            continue
        serialized = model - audit.delta
        file_offset = serialized - audit.base
        info = by_offset.get(file_offset)
        names = source.get(embedded_fingerprint(audit, file_offset), []) if info else []
        rows.append({
            "model": f"{model:08X}",
            "serialized": f"{serialized:08X}",
            "file_offset": f"{file_offset:08X}",
            "kind": info["kind"] if info else None,
            "source_names": names,
            "mask": mask,
            "callsites": [LABELS[i] for i in range(8) if mask & (1 << i)],
        })
    rows.sort(key=lambda row: (row["kind"] or "", row["source_names"], row["model"]))
    result = {
        "state": str(args.state),
        "cpas_address": f"{at:08X}",
        "unique_declared": unique,
        "unique_read": len(rows),
        "overflow": overflow,
        "calls": dict(zip(LABELS, calls)),
        "models": rows,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")


if __name__ == "__main__":
    main()
