"""Audit MC3 pf05 chunk headers referenced by a texture manifest."""

from __future__ import annotations

import argparse
import collections
import json
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ppf", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--require-safe-runtime", action="store_true")
    args = parser.parse_args()

    data = args.ppf.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = [level for texture in manifest["textures"] for level in texture["levels"]]
    fields = collections.Counter()
    unsafe = []
    for level in records:
        page = int(level["page"])
        page_offset = int(level["page_offset"])
        block, _units = struct.unpack_from("<HH", data, 0x0C + page * 4)
        chunk = block * 0x800 + page_offset
        unknown10 = struct.unpack_from("<I", data, chunk + 0x10)[0]
        type_tag = struct.unpack_from("<I", data, chunk + 0x14)[0]
        callback, flags = struct.unpack_from("<II", data, chunk + 0x18)
        fields[(unknown10, type_tag, callback, flags)] += 1
        if callback != 0:
            unsafe.append((level["texture"], int(level["level"]), page,
                           page_offset, unknown10, type_tag, callback, flags))

    print(f"chunks={len(records)} header_variants={len(fields)}")
    for values, count in fields.most_common(16):
        print(f"  unknown10={values[0]:08X} type_tag={values[1]:08X} "
              f"callback={values[2]:08X} flags={values[3]:08X}: {count}")
    print(f"unsafe_release_callbacks={len(unsafe)}")
    for row in unsafe[:20]:
        print("  %s mip=%d page=%d +%05X unknown10=%08X type_tag=%08X "
              "callback=%08X flags=%08X" % row)
    if args.require_safe_runtime and unsafe:
        raise SystemExit("unsafe runtime chunk headers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
