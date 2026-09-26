from __future__ import annotations

import argparse
import struct
import zipfile
from pathlib import Path


MAGIC = struct.pack("<I", 0x4D433346)


def read_ee(path: Path) -> bytes:
    if path.suffix.casefold() == ".p2s":
        with zipfile.ZipFile(path) as archive:
            return archive.read("eeMemory.bin")
    return path.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("savestate", type=Path)
    args = parser.parse_args()
    memory = read_ee(args.savestate)

    found = 0
    cursor = 0
    while True:
        offset = memory.find(MAGIC, cursor)
        if offset < 0:
            break
        cursor = offset + 4
        if offset + 216 > len(memory):
            continue
        version, size, status, active = struct.unpack_from("<4I", memory, offset + 4)
        if version != 1 or size != 216 or status not in (1, 2):
            continue
        fov = struct.unpack_from("<f", memory, offset + 20)[0]
        camera, frame = struct.unpack_from("<2I", memory, offset + 24)
        matrix = struct.unpack_from("<12f", memory, offset + 64)
        live = struct.unpack_from("<12f", memory, offset + 112)
        print(
            f"EE+0x{offset:08X} active={active} camera=0x{camera:08X} "
            f"frame={frame} fov={fov:.3f}"
        )
        print(
            "  matrix pos=(%.3f, %.3f, %.3f) back=(%.5f, %.5f, %.5f)"
            % (matrix[9], matrix[10], matrix[11], matrix[6], matrix[7], matrix[8])
        )
        print(
            "  live   pos=(%.3f, %.3f, %.3f) back=(%.5f, %.5f, %.5f)"
            % (live[9], live[10], live[11], live[6], live[7], live[8])
        )
        found += 1
    if not found:
        raise SystemExit("freecam state v1 not found")


if __name__ == "__main__":
    main()
