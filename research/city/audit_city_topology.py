#!/usr/bin/env python3
"""Compare source .mod faces with the VIF packets emitted by the game.

This deliberately follows the real packet tables and the short-packet VU
addresses.  It treats every VIF batch as a separate strip and honours the ADC
bit in the normal stream at ITOP+2.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import statistics
from pathlib import Path


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def source_triangles(conv, path: Path):
    mesh = conv.read_mod_text(str(path))
    out = []
    for group in mesh.groups:
        for strip in group["strips"]:
            for a, b, c in conv.tris_of_strip(strip):
                out.append(tuple(tuple(float(x) * mesh.scale for x in strip[i].pos)
                                 for i in (a, b, c)))
    return out, mesh.stats()


def packet_adc(vif, payload: bytes):
    """Return one ADC boolean list for each short-packet batch."""
    out = []
    current = None
    for cmd in vif._commands(payload):
        header = vif._header(cmd, payload)
        if header is not None:
            if current is not None:
                out.append(current["adc"])
            base, _scale, count = header
            current = {"base": base, "count": count, "adc": None}
            continue
        if current is None or not (0x60 <= cmd["cmd"] <= 0x7F):
            continue
        base_cmd = cmd["cmd"] & ~0x10
        cols = ((base_cmd >> 2) & 3) + 1
        addr = cmd["imm"] & 0x3FF
        count = cmd["num"] or 256
        if (addr == ((current["base"] + 2) & 0x3FF)
                and count == current["count"] and cols in (3, 4)
                and (base_cmd & 3) == 2):
            stride = cols
            p = cmd["payload"]
            current["adc"] = [(payload[p + stride * i] & 1) != 0
                              for i in range(count)]
    if current is not None:
        out.append(current["adc"])
    return out


def pck_triangles(builder, vif, path: Path):
    out = []
    packets = batches = adc_batches = missing_adc = 0
    vertices_declared = 0
    for group in builder.read_pck_groups(str(path)):
        for payload, _qw, declared in group:
            packets += 1
            vertices_declared += declared
            pos_batches = vif.decode_batches(payload)
            flags = packet_adc(vif, payload)
            if sum(map(len, pos_batches)) != declared:
                raise ValueError(f"{path.name}: VIF vertices do not match descriptor")
            if len(flags) != len(pos_batches):
                raise ValueError(f"{path.name}: ADC batch count does not match position batches")
            for pos, adc in zip(pos_batches, flags):
                batches += 1
                if adc is None:
                    missing_adc += 1
                    adc = [False] * len(pos)
                else:
                    adc_batches += 1
                for i in range(2, len(pos)):
                    if adc[i]:
                        continue
                    a, b = ((i - 2, i - 1) if i % 2 == 0
                            else (i - 1, i - 2))
                    tri = (tuple(pos[a]), tuple(pos[b]), tuple(pos[i]))
                    if len(set(tri)) == 3:
                        out.append(tri)
    return out, {
        "packets": packets,
        "batches": batches,
        "vertices_declared": vertices_declared,
        "adc_batches": adc_batches,
        "missing_adc_batches": missing_adc,
    }


def max_edge(tris):
    value = 0.0
    for tri in tris:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            value = max(value, math.dist(tri[a], tri[b]))
    return value


def shape_key(tri, quantum):
    lens = sorted(math.dist(tri[a], tri[b])
                  for a, b in ((0, 1), (1, 2), (2, 0)))
    return tuple(round(x / quantum) for x in lens)


def fuzzy_unmatched(source, encoded, tolerance):
    """Greedy multiset match of triangle side triples within a real tolerance."""
    import itertools
    a = [tuple(sorted(math.dist(t[x], t[y])
                      for x, y in ((0, 1), (1, 2), (2, 0)))) for t in source]
    b = [tuple(sorted(math.dist(t[x], t[y])
                      for x, y in ((0, 1), (1, 2), (2, 0)))) for t in encoded]
    buckets = collections.defaultdict(set)
    for i, sig in enumerate(b):
        buckets[tuple(math.floor(v / tolerance) for v in sig)].add(i)
    left = set(range(len(b)))
    missing = 0
    for sig in sorted(a):
        base = tuple(math.floor(v / tolerance) for v in sig)
        candidates = []
        for delta in itertools.product((-1, 0, 1), repeat=3):
            candidates.extend(buckets.get(tuple(base[k] + delta[k] for k in range(3)), ()))
        candidates = [i for i in candidates if i in left]
        if candidates:
            best = min(candidates, key=lambda i: max(abs(sig[k] - b[i][k]) for k in range(3)))
            distance = max(abs(sig[k] - b[best][k]) for k in range(3))
            if distance <= tolerance:
                left.remove(best)
                buckets[tuple(math.floor(v / tolerance) for v in b[best])].discard(best)
                continue
        missing += 1
    return missing, len(left)


def compare(source, encoded, quantum):
    a = collections.Counter(shape_key(t, quantum) for t in source)
    b = collections.Counter(shape_key(t, quantum) for t in encoded)
    sa = sorted(tuple(sorted(math.dist(t[x], t[y])
                             for x, y in ((0, 1), (1, 2), (2, 0))))
                for t in source)
    sb = sorted(tuple(sorted(math.dist(t[x], t[y])
                             for x, y in ((0, 1), (1, 2), (2, 0))))
                for t in encoded)
    deltas = [max(abs(x - y) for x, y in zip(ta, tb))
              for ta, tb in zip(sa, sb)]
    fuzzy_0125 = fuzzy_unmatched(source, encoded, 0.125)
    fuzzy_05 = fuzzy_unmatched(source, encoded, 0.5)
    return {
        "source_triangles": len(source),
        "pck_triangles": len(encoded),
        "missing_shape_triangles": sum((a - b).values()),
        "extra_shape_triangles": sum((b - a).values()),
        "source_max_edge": max_edge(source),
        "pck_max_edge": max_edge(encoded),
        "sorted_shape_max_delta": max(deltas, default=0.0),
        "sorted_shape_p95_delta": (statistics.quantiles(deltas, n=20)[18]
                                   if len(deltas) >= 20 else max(deltas, default=0.0)),
        "fuzzy_missing_0_125": fuzzy_0125[0],
        "fuzzy_extra_0_125": fuzzy_0125[1],
        "fuzzy_missing_0_5": fuzzy_05[0],
        "fuzzy_extra_0_5": fuzzy_05[1],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolkit", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--mesh-dir", type=Path, required=True)
    ap.add_argument("--filter", action="append", default=[])
    ap.add_argument("--quantum", type=float, default=0.02)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    conv = load("mc3_mesh_convert_topology", args.toolkit / "mc3_mesh_convert.py")
    builder = load("mc3_city_build_topology", args.toolkit / "mc3_city_build.py")
    vif = load("mc3_vifpos_topology", args.toolkit / "mc3_vifpos.py")

    rows = []
    for src in sorted(args.models.glob("*.mod")):
        name = src.stem
        if args.filter and not any(token.lower() in name.lower() for token in args.filter):
            continue
        pck = args.mesh_dir / (name + ".mesh.pck")
        if not pck.exists():
            continue
        try:
            s, source_stats = source_triangles(conv, src)
            p, packet_stats = pck_triangles(builder, vif, pck)
            row = {"model": name, **compare(s, p, args.quantum),
                   "source_stats": source_stats, **packet_stats}
            row["ok"] = (row["source_triangles"] == row["pck_triangles"]
                         and row["missing_shape_triangles"] == 0
                         and row["extra_shape_triangles"] == 0)
        except Exception as exc:
            row = {"model": name, "ok": False, "error": str(exc)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))

    summary = {
        "models": len(rows),
        "ok": sum(bool(x.get("ok")) for x in rows),
        "errors": sum("error" in x for x in rows),
        "triangle_count_mismatch": sum(
            x.get("source_triangles") != x.get("pck_triangles")
            for x in rows if "source_triangles" in x),
        "shape_mismatch": sum(
            bool(x.get("missing_shape_triangles") or x.get("extra_shape_triangles"))
            for x in rows),
        "quantum": args.quantum,
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "models": rows},
                                        indent=2, ensure_ascii=False),
                             encoding="utf-8")


if __name__ == "__main__":
    main()
