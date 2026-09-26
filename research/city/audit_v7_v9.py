"""Compare the Los Angeles city v7/v9 PCKs semantically.

The goal is to separate the active-list fix from any accidental change in
shader, material, mesh or matrix.  The script is read-only.
"""

from __future__ import annotations

import os
import hashlib
import struct
import sys
from collections import Counter
from pathlib import Path


TOOLS = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, str(TOOLS))

import mc3_city_build as city  # noqa: E402


V7 = TOOLS / "output" / "modcity_la_v7_vif_local.pck"
V9 = TOOLS / "output" / "modcity_la_v9_cadeia_local.pck"


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def instances(p: city.mc2.Pck) -> list[int]:
    data = p.data
    count = u32(data, 0x80 + 0x14)
    hood_array = p.F(u32(data, 0x80 + 0x18))
    out: list[int] = []
    for hood in range(count):
        desc = hood_array + 20 * hood
        n = u32(data, desc + 0x0C)
        base = p.F(u32(data, desc + 0x10))
        out.extend(base + city.INSTANCE_SIZE * i for i in range(n))
    return out


def meshes(p: city.mc2.Pck) -> list[int]:
    data = p.data
    count = u32(data, 0x80 + 0x14)
    out: list[int] = []
    for hood in range(count):
        for _name, lods, _component, _compdata in city.hf.hood_uniques(p, hood):
            out.extend(lods)
    _slots, types = city.hf.instance_kinds(p)
    for _va, (_name, _compdata, lods) in types.items():
        out.extend(lods)
    return sorted(set(out))


def mesh_ranges(p: city.mc2.Pck, mesh_offsets: list[int]):
    data = p.data
    vif: list[tuple[int, int]] = []
    materials: list[tuple[int, int]] = []
    tables: list[tuple[int, int]] = []
    for mesh in mesh_offsets:
        ng = u16(data, mesh + 8)
        mt = p.F(u32(data, mesh + 0x0C))
        gt = p.F(u32(data, mesh + 0x10))
        materials.append((mt, mt + 2 * ng))
        tables.append((mesh, mesh + 0x1C))
        tables.append((gt, gt + 8 * ng))
        for group in range(ng):
            entry = gt + 8 * group
            nblocks = u16(data, entry + 4)
            block_table = p.F(u32(data, entry))
            tables.append((block_table, block_table + 8 * nblocks))
            for block in range(nblocks):
                item = block_table + 8 * block
                payload = p.F(u32(data, item))
                qwords = u16(data, item + 4)
                vif.append((payload, payload + 16 * qwords))
    return vif, materials, tables


def shader_ranges(p: city.mc2.Pck):
    data = p.data
    ranges: list[tuple[int, int]] = []
    count = u32(data, 0x88)
    descs = p.F(u32(data, 0x8C))
    for group in range(count):
        desc = descs + 16 * group
        nslots = u16(data, desc + 8)
        slots = p.F(u32(data, desc + 4))
        ranges.append((desc, desc + 16))
        ranges.append((slots, slots + 4 * nslots))
        for slot in range(nslots):
            shader_va = u32(data, slots + 4 * slot)
            if not shader_va:
                continue
            shader = p.F(shader_va)
            ranges.append((shader, shader + 12))
            if u32(data, shader + 4) & 0x7F:
                continue
            texture = p.F(u32(data, shader + 8))
            ranges.append((texture, texture + 0xA0))
    return ranges


def identical_ranges(a: bytes, b: bytes, ranges: list[tuple[int, int]]) -> bool:
    return all(a[start:end] == b[start:end] for start, end in ranges)


def inside(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def main() -> None:
    pa, pb = city.mc2.Pck(str(V7)), city.mc2.Pck(str(V9))
    a, b = bytes(pa.data), bytes(pb.data)
    ia, ib = instances(pa), instances(pb)
    ma, mb = meshes(pa), meshes(pb)
    va, kill, taba = mesh_ranges(pa, ma)
    vb, matb, tabb = mesh_ranges(pb, mb)
    sha = shader_ranges(pa)
    shb = shader_ranges(pb)

    diffs = [i for i, pair in enumerate(zip(a, b)) if pair[0] != pair[1]]
    link_bytes = {off + byte for off in ia for byte in range(8)}
    outside_links = [pos for pos in diffs if pos not in link_bytes]
    outside_vif = [pos for pos in outside_links if not inside(pos, va)]
    local = Counter()
    for off in ia:
        for byte in range(city.INSTANCE_SIZE):
            if a[off + byte] != b[off + byte]:
                local[byte] += 1

    print("v7", len(a), hashlib.sha256(a).hexdigest())
    print("v9", len(b), hashlib.sha256(b).hexdigest())
    print("instances", len(ia), len(ib), "same_offsets", ia == ib)
    print("meshes", len(ma), len(mb), "same_offsets", ma == mb)
    print("different_bytes", len(diffs))
    print("instance_local_offsets", sorted(local.items()))
    print("all_instance_bytes_08_5f_identical",
          all(a[x + 8:x + city.INSTANCE_SIZE] == b[x + 8:x + city.INSTANCE_SIZE]
              for x in ia))
    print("outside_instance_links", len(outside_links))
    print("outside_links_but_inside_vif", len(outside_links) - len(outside_vif))
    print("outside_links_and_outside_vif", len(outside_vif), outside_vif[:20])
    print("shader_descriptors_slots_objects_textures_identical",
          sha == shb and identical_ranges(a, b, sha))
    print("material_indices_identical",
          kill == matb and identical_ranges(a, b, kill))
    print("mesh_headers_and_tables_identical",
          taba == tabb and identical_ranges(a, b, taba))
    print("vif_range_layout_identical", va == vb)


if __name__ == "__main__":
    main()
