#!/usr/bin/env python3
"""Compare one source .mod with one mesh embedded in a city PCK."""

from __future__ import annotations

import os
import argparse
import importlib.util
import json
import struct
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("pck", type=Path)
    ap.add_argument("mesh", type=lambda x: int(x, 0), help="mesh file offset")
    ap.add_argument("--toolkit", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')))
    args = ap.parse_args()
    top = load("topology_shared", Path(__file__).with_name("audit_city_topology.py"))
    conv = load("mesh_convert_embedded", args.toolkit / "mc3_mesh_convert.py")
    city = load("cityaudit_embedded", args.toolkit / "mc3_cityaudit.py")
    vif = load("vif_embedded", args.toolkit / "mc3_vifpos.py")
    pck, _ = city._open(str(args.pck))

    groups = []
    ng = struct.unpack_from("<H", pck.data, args.mesh + 8)[0]
    table = pck.F(pck.U(args.mesh + 0x10))
    for gi in range(ng):
        desc = pck.F(pck.U(table + 8 * gi))
        count = struct.unpack_from("<H", pck.data, table + 8 * gi + 4)[0]
        packets = []
        for bi in range(count):
            ptr, qw, nv = struct.unpack_from("<IHH", pck.data, desc + 8 * bi)
            off = pck.F(ptr)
            packets.append((bytes(pck.data[off:off + 16 * qw]), qw, nv))
        groups.append(packets)

    class Embedded:
        @staticmethod
        def read_pck_groups(_path):
            return groups

    source, stats = top.source_triangles(conv, args.source)
    encoded, packet_stats = top.pck_triangles(Embedded, vif, args.pck)
    result = {"source": str(args.source), "pck": str(args.pck),
              "mesh": args.mesh, "groups": ng, "source_stats": stats,
              **packet_stats, **top.compare(source, encoded, 0.02)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
