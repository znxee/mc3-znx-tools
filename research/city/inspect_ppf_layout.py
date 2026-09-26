from __future__ import annotations

import os
import struct
from pathlib import Path


PPF = Path(os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.ppf'))
ANCHOR = bytes.fromhex("cdcdcdcd0e00ffff00000000cdcdcdcd")


def hx(data: bytes, start: int, size: int) -> str:
    out = []
    for off in range(start, start + size, 16):
        row = data[off:off + 16]
        out.append(f"{off:08X}  " + " ".join(f"{b:02X}" for b in row))
    return "\n".join(out)


def main() -> None:
    data = PPF.read_bytes()
    count, stride = struct.unpack_from("<II", data, 4)
    print(f"count={count} stride=0x{stride:X} file=0x{len(data):X}")
    for i in range(12):
        block, units = struct.unpack_from("<HH", data, 0x0C + i * 4)
        start = block * 0x800
        size = units * 0x80
        end = start + size
        descs = []
        pos = start
        while True:
            a = data.find(ANCHOR, pos, end)
            if a < 0:
                break
            descs.append(a - 0x10)
            pos = a + 1
        print(f"page {i}: block=0x{block:04X} start=0x{start:X} units=0x{units:X} size=0x{size:X} descs={[hex(x-start) for x in descs]}")
    print("\npage 1 start:")
    print(hx(data, 0x21000, 0x180))
    print("\npage 1 first descriptor @ 0x23100:")
    print(hx(data, 0x23100, 0xA0))


if __name__ == "__main__":
    main()
