"""Summarize the VIF stream contract for one city mesh inside a PCK."""
from __future__ import annotations

import os
import argparse
import importlib.util
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def packets(pck, mesh):
    group_count = struct.unpack_from("<H", pck.data, mesh + 8)[0]
    group_table = pck.F(struct.unpack_from("<I", pck.data, mesh + 0x10)[0])
    for group in range(group_count):
        desc_va, count, _ = struct.unpack_from("<IHH", pck.data, group_table + 8 * group)
        desc = pck.F(desc_va)
        for index in range(count):
            payload_va, qwords, vertices = struct.unpack_from(
                "<IHH", pck.data, desc + 8 * index
            )
            payload = pck.F(payload_va)
            yield group, index, vertices, bytes(pck.data[payload : payload + 16 * qwords])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pck")
    parser.add_argument("mesh", type=lambda value: int(value, 0), help="file offset")
    args = parser.parse_args()

    audit = load("cityaudit_vif_contract", TOOLKIT / "mc3_cityaudit.py")
    vif = load("vif_contract", TOOLKIT / "mc3_vifpos.py")
    pck, _hf = audit._open(args.pck)
    box = audit._aabb_from_vif(pck, args.mesh)
    print(Path(args.pck).name, f"mesh=file+{args.mesh:06X}", "AABB", box)
    for group, index, vertices, payload in packets(pck, args.mesh):
        commands = list(vif._commands(payload))
        headers = [vif._header(c, payload) for c in commands]
        headers = [item for item in headers if item is not None]
        mscal = [c["imm"] for c in commands if c["cmd"] in (0x14, 0x15, 0x17)]
        unpacks = [
            (c["cmd"], c["num"] or 256, c["imm"] & 0x3FF, c["mode"], c["cl"], c["wl"])
            for c in commands
            if 0x60 <= c["cmd"] <= 0x7F
        ]
        decoded = vif.decode_positions(payload)
        print(
            f"g{group}/p{index} descriptor_vertices={vertices} decoded={len(decoded)} "
            f"headers={headers} mscal={[hex(value) for value in mscal]}"
        )
        print("  UNPACK cmd,num,addr,mode,cl,wl:", unpacks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
