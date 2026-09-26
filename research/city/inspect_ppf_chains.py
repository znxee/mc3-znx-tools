from __future__ import annotations

import os
import struct
from pathlib import Path


PPF = Path(os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.ppf'))


def main() -> None:
    data = PPF.read_bytes()
    count, stride = struct.unpack_from("<II", data, 4)
    print(f"count={count} stride=0x{stride:X}")
    for i in range(12):
        block, units = struct.unpack_from("<HH", data, 0x0C + i * 4)
        start, size = block * 0x800, units * 0x80
        end, pos = start + size, start
        chunks = []
        while pos + 0x20 <= end:
            chunk_size = struct.unpack_from("<I", data, pos + 0x0C)[0]
            code = struct.unpack_from("<H", data, pos + 0x14)[0]
            chunks.append((pos - start, chunk_size, code))
            if not chunk_size:
                break
            pos += chunk_size
        print(f"page {i:2d} start=0x{start:X} used=0x{size:X} end_chain=0x{pos-start:X}")
        print("  " + ", ".join(f"+{o:05X}:size={s:05X}/code={c:02X}" for o, s, c in chunks))


if __name__ == "__main__":
    main()
