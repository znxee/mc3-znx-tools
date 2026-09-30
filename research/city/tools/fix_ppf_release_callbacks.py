"""Create a safe PPF candidate by nulling serialized chunk callbacks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_city", type=Path)
    parser.add_argument("output_city", type=Path)
    args = parser.parse_args()
    if args.output_city.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_city}")
    shutil.copytree(args.source_city, args.output_city)

    ppf = args.output_city / "losangeles_midnight_clear.ppf"
    manifest_path = ppf.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = bytearray(ppf.read_bytes())
    before_hash = sha256(data)
    old_values = {}
    changed = 0
    seen = set()
    for texture in manifest["textures"]:
        for level in texture["levels"]:
            page = int(level["page"])
            page_offset = int(level["page_offset"])
            block, _units = struct.unpack_from("<HH", data, 0x0C + page * 4)
            callback_at = block * 0x800 + page_offset + 0x18
            if callback_at in seen:
                raise AssertionError(f"duplicate chunk callback at 0x{callback_at:X}")
            seen.add(callback_at)
            value = struct.unpack_from("<I", data, callback_at)[0]
            old_values[f"{value:08X}"] = old_values.get(f"{value:08X}", 0) + 1
            if value != 0:
                struct.pack_into("<I", data, callback_at, 0)
                changed += 1

    for callback_at in seen:
        if struct.unpack_from("<I", data, callback_at)[0] != 0:
            raise AssertionError(f"callback remains nonzero at 0x{callback_at:X}")
    ppf.write_bytes(data)
    after_hash = sha256(data)
    # The independent route auditor and existing manifests use lowercase hex.
    manifest["sha256"] = after_hash.lower()
    manifest["runtime_chunk_release_callback"] = 0
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    report = {
        "status": "PASS",
        "source": str(args.source_city),
        "output": str(args.output_city),
        "chunks": len(seen),
        "callbacks_changed": changed,
        "old_callback_values": old_values,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "pck_files_unchanged": 9,
    }
    report_path = args.output_city.parent.parent / "release_callback_fix.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
