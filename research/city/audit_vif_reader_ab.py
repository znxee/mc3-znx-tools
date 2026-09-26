#!/usr/bin/env python3
"""Compare the validated city VIF decoder with mc3_view's legacy batch reader."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

from city_vif_recenter import decode_positions, pck_packets


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("mc3_view_ab", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def bounds(points):
    lo = [min(p[a] for p in points) for a in range(3)]
    hi = [max(p[a] for p in points) for a in range(3)]
    return lo, hi, [hi[a] - lo[a] for a in range(3)]


def legacy_positions(view, packet: bytes, nv: int):
    scale = struct.unpack_from("<f", packet, 4)[0] or (1.0 / 256.0)
    out = []
    for off, count, _adc in view._vif_batches(packet, 0, len(packet), nv):
        for i in range(count):
            q = off + 6 * i
            if q + 6 > len(packet):
                raise ValueError("legacy batch exceeds packet")
            out.append(tuple(v * scale for v in struct.unpack_from("<3h", packet, q)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--view", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--strict", action="store_true",
                    help="compare mc3_view's new strict decoder, not its legacy helper")
    args = ap.parse_args()

    view = load_module(args.view.resolve())
    totals = {"files": 0, "packets": 0, "vertices_validated": 0,
              "vertices_legacy": 0, "validated_extent_gt_1000": 0,
              "legacy_extent_gt_1000": 0, "different_bounds": 0,
              "decode_errors": 0}
    examples = []
    for path in sorted(args.source.resolve().glob("*.mesh.pck")):
        data = path.read_bytes()
        totals["files"] += 1
        file_validated = []
        file_legacy = []
        try:
            for group, index, off, size, nv in pck_packets(data):
                packet = data[off:off + size]
                decoded, _chains = decode_positions(packet)
                valid = [tuple(v * scale for v in xyz) for scale, xyz in decoded]
                legacy = (view._vifpos.decode_positions(packet) if args.strict
                          else legacy_positions(view, packet, nv))
                if len(valid) != nv or len(legacy) != nv:
                    raise ValueError("descriptor count mismatch")
                totals["packets"] += 1
                totals["vertices_validated"] += len(valid)
                totals["vertices_legacy"] += len(legacy)
                file_validated.extend(valid)
                file_legacy.extend(legacy)
        except Exception as exc:
            totals["decode_errors"] += 1
            if len(examples) < 20:
                examples.append({"file": path.name, "error": str(exc)})
            continue
        vb = bounds(file_validated)
        lb = bounds(file_legacy)
        vg = max(vb[2]) > 1000.0
        lg = max(lb[2]) > 1000.0
        totals["validated_extent_gt_1000"] += int(vg)
        totals["legacy_extent_gt_1000"] += int(lg)
        delta = max(abs(vb[j][a] - lb[j][a]) for j in (0, 1) for a in range(3))
        totals["different_bounds"] += int(delta > 1e-5)
        if (lg != vg or delta > 1.0) and len(examples) < 20:
            examples.append({"file": path.name, "validated": vb,
                             "legacy": lb, "max_bound_delta": delta})

    report = {"source": str(args.source.resolve()),
              "view": str(args.view.resolve()),
              "compared_decoder": "strict" if args.strict else "legacy",
              "view_sha256": hashlib.sha256(args.view.read_bytes()).hexdigest(),
              "totals": totals, "examples": examples}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
