"""Read-only diagnostics for the mip-fallback PCSX2 crash savestate.

The script deliberately does not patch the state or game assets.  It can also
compare serialized TexturePS2 tail fields between PCKs, which catches runtime
cache fields that a descriptor-only audit misses.
"""

from __future__ import annotations

import collections
import re
import struct
import sys
import zipfile
from pathlib import Path

import zstandard


TEXTURE_TOKEN = 0x007A2320
TEXTURE_VTABLE = 0x00627280
CD = 0xCDCDCDCD


def read_member(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(name)
        archive.fp.seek(info.header_offset)
        header = archive.fp.read(30)
        filename_size, extra_size = struct.unpack_from("<HH", header, 26)
        archive.fp.seek(info.header_offset + 30 + filename_size + extra_size)
        compressed = archive.fp.read(info.compress_size)
        if info.compress_type == zipfile.ZIP_STORED:
            return compressed
        return zstandard.ZstdDecompressor().decompress(
            compressed, max_output_size=info.file_size
        )


def words(blob: bytes, start: int, count: int) -> str:
    result = []
    for offset in range(start, min(len(blob), start + count * 4), 4):
        result.append(f"{struct.unpack_from('<I', blob, offset)[0]:08X}")
    return " ".join(result)


def texture_offsets(blob: bytes, marker: int) -> list[int]:
    needle = struct.pack("<I", marker)
    found = []
    at = 0
    while True:
        at = blob.find(needle, at)
        if at < 0:
            return found
        if at % 4 == 0 and at + 0xA0 <= len(blob):
            mip_count = blob[at + 0x86]
            texture_type = blob[at + 4]
            if 1 <= mip_count <= 4 and texture_type <= 8:
                found.append(at)
        at += 4


def texture_report(label: str, blob: bytes, marker: int) -> None:
    offsets = texture_offsets(blob, marker)
    print(f"\n[{label}] TexturePS2 candidates: {len(offsets)}")
    print("mips:", dict(sorted(collections.Counter(blob[x + 0x86] for x in offsets).items())))
    for tail in (0x87, 0x88, 0x90, 0x98):
        size = 1 if tail == 0x87 else 8
        values = collections.Counter(blob[x + tail:x + tail + size].hex() for x in offsets)
        print(f"+{tail:02X} unique={len(values)} top={values.most_common(8)}")
    cd_near = []
    cd_bytes = struct.pack("<I", CD)
    for offset in offsets:
        relative = blob[offset:offset + 0xA0].find(cd_bytes)
        if relative >= 0:
            cd_near.append((offset, relative))
    print(f"objects containing CDCDCDCD: {len(cd_near)}")
    for offset, relative in cd_near[:12]:
        print(f"  object={offset:08X} field=+{relative:02X}")


def target_city_report(ee: bytes, file_id: int = 123) -> None:
    offsets = texture_offsets(ee, TEXTURE_VTABLE)
    selected = []
    for offset in offsets:
        refs = [struct.unpack_from("<III", ee, offset + 0x48 + 12 * level)
                for level in range(4)]
        name_pointer = struct.unpack_from("<I", ee, offset + 0x78)[0] & 0x1FFFFFF
        raw_name = ee[name_pointer:name_pointer + 32].split(b"\0", 1)[0]
        name_match = re.fullmatch(rb"t\d{3}", raw_name) is not None
        page_match = any((page_word >> 23) == file_id for _, _, page_word in refs)
        if name_match or page_match:
            selected.append((offset, raw_name, refs))

    print(f"\n[target city: tNNN or file_id={file_id}] objects={len(selected)}")
    print("mips:", dict(sorted(collections.Counter(
        ee[offset + 0x86] for offset, _, _ in selected).items())))
    print("uploaded_first +87:", collections.Counter(
        ee[offset + 0x87] for offset, _, _ in selected))
    pointer_kinds = collections.Counter()
    callbacks = collections.Counter()
    bad = []
    for offset, name, refs in selected:
        mip_count = ee[offset + 0x86]
        for level, (pointer, page_offset, page_word) in enumerate(refs):
            active = level < mip_count
            if pointer == 0:
                kind = "zero"
            elif pointer == CD:
                kind = "CDCDCDCD"
            elif (pointer & 0x1FFFFFF) + 0x20 <= len(ee):
                kind = "RAM"
                chunk = pointer & 0x1FFFFFF
                callback = struct.unpack_from("<I", ee, chunk + 0x18)[0]
                callbacks[callback] += 1
                if callback == CD:
                    bad.append((offset, name, level, pointer, page_offset, page_word))
            else:
                kind = "other"
            pointer_kinds[("active" if active else "inactive", kind)] += 1
    print("ref pointers:", pointer_kinds)
    print("chunk release callbacks:", callbacks.most_common(16))
    print(f"active chunks with CD release callback: {len(bad)}")
    for item in bad[:24]:
        offset, name, level, pointer, page_offset, page_word = item
        print(f"  tex={offset:08X} name={name!r} mip={level} ptr={pointer:08X} "
              f"page_off={page_offset:08X} page_word={page_word:08X}")


def state_report(state: Path) -> None:
    with zipfile.ZipFile(state) as archive:
        print("savestate members:")
        for info in archive.infolist():
            print(f"  {info.filename}: {info.file_size} bytes, method={info.compress_type}")

    internal = read_member(state, "PCSX2 Internal Structures.dat")
    cd_bytes = struct.pack("<I", CD)
    cd_offsets = [x for x in range(0, len(internal) - 3, 4)
                  if internal[x:x + 4] == cd_bytes]
    print(f"\ninternal structures: {len(internal)} bytes, aligned CD words={len(cd_offsets)}")
    for offset in cd_offsets[:40]:
        start = max(0, offset - 32) & ~3
        print(f"  CD at {offset:08X}; context {start:08X}: {words(internal, start, 24)}")

    ee = read_member(state, "eeMemory.bin")
    print(f"\nEE RAM: {len(ee)} bytes; aligned CD words="
          f"{sum(ee[x:x + 4] == cd_bytes for x in range(0, len(ee) - 3, 4))}")
    texture_report("savestate EE RAM", ee, TEXTURE_VTABLE)
    target_city_report(ee)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: analyze_mip_crash.py STATE.p2s [PCK ...]", file=sys.stderr)
        return 2
    state_report(Path(argv[1]))
    for item in argv[2:]:
        path = Path(item)
        texture_report(str(path), path.read_bytes(), TEXTURE_TOKEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
