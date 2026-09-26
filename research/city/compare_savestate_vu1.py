"""Print stable hashes for VU1 micro-memory stored in PCSX2 savestates."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import zipfile

import zstandard


def read_member(path: Path, name: str) -> bytes:
    archive = zipfile.ZipFile(path)
    info = next(item for item in archive.infolist() if item.filename == name)
    assert archive.fp is not None
    archive.fp.seek(info.header_offset)
    name_size, extra_size = struct.unpack_from("<HH", archive.fp.read(30), 26)
    archive.fp.seek(info.header_offset + 30 + name_size + extra_size)
    return zstandard.ZstdDecompressor().decompress(
        archive.fp.read(info.compress_size), max_output_size=info.file_size
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("states", nargs="+")
    args = parser.parse_args()
    for value in args.states:
        path = Path(value)
        data = read_member(path, "vu1MicroMem.bin")
        print(
            path.name,
            len(data),
            hashlib.sha256(data).hexdigest(),
            "nonzero=%d" % sum(byte != 0 for byte in data),
            "at_mscal6=" + data[6 * 8 : 6 * 8 + 64].hex(),
        )
        print(
            "  chunks1k:",
            " ".join(
                hashlib.sha256(data[offset : offset + 1024]).hexdigest()[:12]
                for offset in range(0, len(data), 1024)
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
