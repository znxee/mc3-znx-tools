"""Preserve Mod City TEX1 identity bits while raising only GS MXL to mip 3."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path


TOKEN = 0x007A2320
FILE_ID = 123
MXL_MASK = 0x1C
NAMES = [f"modcity_{time}_{weather}.pck"
         for time in ("dawn", "dusk", "midnight")
         for weather in ("clear", "cloudy", "rainy")]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def ref(data: bytes, offset: int, level: int) -> tuple[int, int, int]:
    return struct.unpack_from("<III", data, offset + 0x48 + level * 12)


def is_target(old: bytes, current: bytes, offset: int) -> bool:
    if offset + 0xA0 > len(old):
        return False
    if u32(old, offset) != TOKEN or u32(current, offset) != TOKEN:
        return False
    if old[offset + 4] != 0 or current[offset + 4] != 0:
        return False
    if old[offset + 0x86] != 2 or current[offset + 0x86] != 3:
        return False
    old_refs = [ref(old, offset, level) for level in range(4)]
    new_refs = [ref(current, offset, level) for level in range(4)]
    if any(pointer != 0 for pointer, _, _ in old_refs + new_refs):
        return False
    if any((old_refs[level][2] >> 23) != FILE_ID for level in (0, 1)):
        return False
    if any((new_refs[level][2] >> 23) != FILE_ID for level in (0, 1, 2)):
        return False
    return old_refs[2] == (0, 0, 0) and old_refs[3] == (0, 0, 0)


def patch_one(old_path: Path, source_path: Path, output_path: Path) -> dict:
    old = old_path.read_bytes()
    source = source_path.read_bytes()
    if len(old) != len(source):
        raise AssertionError(f"size mismatch: {old_path.name}")
    data = bytearray(source)
    offsets = []
    needle = struct.pack("<I", TOKEN)
    at = 0
    while True:
        at = source.find(needle, at)
        if at < 0:
            break
        if at % 4 == 0 and is_target(old, source, at):
            offsets.append(at)
        at += 4
    if len(offsets) != 453:
        raise AssertionError(f"{source_path.name}: expected 453 expanded textures, got {len(offsets)}")

    changed_bytes = set()
    donor_identity_changes = 0
    for offset in offsets:
        old_tex1 = u64(old, offset + 0x88)
        source_tex1 = u64(source, offset + 0x88)
        target_tex1 = (old_tex1 & ~MXL_MASK) | (2 << 2)
        if ((target_tex1 ^ old_tex1) & ~MXL_MASK) != 0:
            raise AssertionError("identity bits changed while constructing TEX1")
        if ((source_tex1 ^ old_tex1) & ~MXL_MASK) != 0:
            donor_identity_changes += 1
        before = bytes(data[offset + 0x88:offset + 0x90])
        struct.pack_into("<Q", data, offset + 0x88, target_tex1)
        after = bytes(data[offset + 0x88:offset + 0x90])
        changed_bytes.update(offset + 0x88 + index for index, pair in enumerate(zip(before, after))
                             if pair[0] != pair[1])
        if ((u64(data, offset + 0x88) >> 2) & 7) != 2:
            raise AssertionError("MXL did not become 2")

    output_path.write_bytes(data)
    return {
        "file": source_path.name,
        "textures": len(offsets),
        "textures_with_donor_identity_bits": donor_identity_changes,
        "changed_bytes_from_v5": len(changed_bytes),
        "before_sha256": sha256(source),
        "after_sha256": sha256(data),
        "tex1_non_mxl_bits_match_pre_v4": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old_city", type=Path)
    parser.add_argument("source_city", type=Path)
    parser.add_argument("output_city", type=Path)
    args = parser.parse_args()
    if args.output_city.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_city}")
    shutil.copytree(args.source_city, args.output_city)
    reports = [patch_one(args.old_city / name, args.source_city / name,
                         args.output_city / name) for name in NAMES]
    report = {
        "status": "PASS",
        "policy": "copy retail mip descriptors; preserve source TEX1 except MXL=2",
        "pcks": reports,
    }
    report_path = args.output_city.parent.parent / "tex1_identity_fix.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "pcks": len(reports),
        "textures_per_pck": sorted({item["textures"] for item in reports}),
        "donor_identity_per_pck": sorted({item["textures_with_donor_identity_bits"] for item in reports}),
        "changed_bytes_per_pck": sorted({item["changed_bytes_from_v5"] for item in reports}),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
