"""Build a pure A/B: v7 plus only the prev/next links of v9."""

from __future__ import annotations

import os
import hashlib
import struct
from pathlib import Path

TOOLS = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
V7 = TOOLS / "output" / "losangeles_la_v7_vif_local.pck"
V9 = TOOLS / "output" / "losangeles_la_v9_cadeia_local.pck"
OUT = TOOLS / "output" / "losangeles_la_v9_chain_only_from_v7.pck"


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def main() -> None:
    original = V7.read_bytes()
    source = V9.read_bytes()
    if len(original) != len(source) or u32(original, 0) != u32(source, 0):
        raise SystemExit("v7/v9 do not share size/base")

    out = bytearray(original)
    base = u32(original, 0)
    file_off = lambda va: va - base + 0x80
    hood_count = u32(original, 0x94)
    hood_array = file_off(u32(original, 0x98))
    instances: list[int] = []
    for hood in range(hood_count):
        desc = hood_array + 20 * hood
        count = u32(original, desc + 0x0C)
        first = file_off(u32(original, desc + 0x10))
        instances.extend(first + 0x60 * i for i in range(count))

    for instance in instances:
        out[instance:instance + 8] = source[instance:instance + 8]

    allowed = {instance + byte for instance in instances for byte in range(8)}
    changed = [i for i, (a, b) in enumerate(zip(original, out)) if a != b]
    if any(i not in allowed for i in changed):
        raise SystemExit("a change escaped Instance+0/+4")
    if any(out[i + 8:i + 0x60] != original[i + 8:i + 0x60]
           for i in instances):
        raise SystemExit("instance matrix/centre/type/radius changed")

    OUT.write_bytes(out)
    print("output", OUT)
    print("instances", len(instances))
    print("changed_bytes", len(changed))
    print("sha256", hashlib.sha256(out).hexdigest())


if __name__ == "__main__":
    main()
