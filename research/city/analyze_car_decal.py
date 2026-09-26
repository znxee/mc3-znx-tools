"""Census of car_decal.shadert resources in canonical PS2 vp_*.pck files."""

from __future__ import annotations

import os
import re
import struct
from collections import Counter
from pathlib import Path


ROOT = Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/vehicle'))


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0] if 0 <= off <= len(data) - 4 else 0


def cstr(data: bytes, off: int) -> str:
    end = off
    while end < len(data) and data[end] not in (0, 0xCD):
        end += 1
    return data[off:end].decode("latin1", "replace")


def shader_program(data: bytes, off: int) -> bytes:
    """Read the compact rmcShaderComplex state stream through its 0xFF terminator."""
    out = bytearray()
    while 0 <= off < len(data) and len(out) < 0x100:
        opcode = data[off]
        out.append(opcode)
        off += 1
        if opcode == 0xFF:
            return bytes(out)
        width = 1 if opcode & 0xC0 == 0 else 4
        out.extend(data[off : off + width])
        off += width
    return bytes(out)


def canonical_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.glob("vp_*/*.pck")
        if path.stem == path.parent.name
    )


def shader_slots(data: bytes) -> list[tuple[int, str]]:
    base = u32(data, 0) - 0x80
    names = {
        match.start(): match.group().decode("ascii")
        for match in re.finditer(rb"[A-Za-z_][A-Za-z0-9_.]{2,}\.shadert", data)
    }
    if not names:
        return []

    cache: dict[int, str | None] = {}

    def name_of(resource: int) -> str | None:
        if resource in cache:
            return cache[resource]
        found = None
        for delta in range(0, 0x140, 4):
            target = u32(data, resource + delta) - base
            if target in names:
                found = names[target]
                break
        cache[resource] = found
        return found

    best: list[tuple[int, str]] = []
    off = 0x80
    while off <= len(data) - 4:
        target = u32(data, off) - base
        name = name_of(target) if 0 <= target < len(data) else None
        if name is None:
            off += 4
            continue
        run: list[tuple[int, str]] = []
        while off <= len(data) - 4:
            target = u32(data, off) - base
            name = name_of(target) if 0 <= target < len(data) else None
            if name is None:
                break
            run.append((target, name))
            off += 4
        if len(run) > len(best):
            best = run
    return best


def main() -> None:
    rows = []
    chrome_rows = []
    errors = []
    for path in canonical_files():
        data = path.read_bytes()
        base = u32(data, 0) - 0x80
        slots = shader_slots(data)
        hits = [(index, resource) for index, (resource, name) in enumerate(slots) if name == "car_decal.shadert"]
        if not hits:
            errors.append((path.parent.name, f"car_decal hits=0 slots={len(slots)}"))
            continue
        for index, resource in hits:
            shader = u32(data, resource + 0x48) - base
            if not (0 <= shader <= len(data) - 0x40):
                errors.append((path.parent.name, f"invalid shader ptr from resource+48: {shader:#x}"))
                continue
            name_off = u32(data, shader + 0x28) - base
            program_off = u32(data, shader + 0x08) - base
            rows.append(
                {
                    "car": path.parent.name,
                    "slot": index,
                    "resource": resource,
                    "shader": shader,
                    "vtable": u32(data, shader),
                    "flags": u32(data, shader + 0x04),
                    "program": shader_program(data, program_off).hex(" ") if 0 <= program_off < len(data) else "",
                    "name": cstr(data, name_off) if 0 <= name_off < len(data) else "",
                    "state_mask": u32(data, shader + 0x2C),
                    "color": struct.unpack_from("<4f", data, shader + 0x30),
                }
            )
            chrome = u32(data, resource + 0x4C) - base
            if 0 <= chrome <= len(data) - 0x40:
                chrome_name_off = u32(data, chrome + 0x28) - base
                chrome_name = cstr(data, chrome_name_off) if 0 <= chrome_name_off < len(data) else ""
                if chrome_name == "car_decal_chrome.shadert":
                    first_program = u32(data, chrome + 0x08) - base
                    second_pass = u32(data, chrome + 0x24) - base
                    second_program = u32(data, second_pass) - base if 0 <= second_pass <= len(data) - 0x20 else -1
                    chrome_rows.append(
                        {
                            "car": path.parent.name,
                            "slot": index,
                            "vtable": u32(data, chrome),
                            "flags": u32(data, chrome + 0x04),
                            "first_program": shader_program(data, first_program).hex(" "),
                            "second_program": shader_program(data, second_program).hex(" ") if second_program >= 0 else "",
                            "state_mask": u32(data, chrome + 0x2C),
                            "color": struct.unpack_from("<4f", data, chrome + 0x30),
                            "passes": 2 if second_pass >= 0 else 1,
                        }
                    )

    print(f"canonical={len(canonical_files())} decoded={len(rows)} errors={len(errors)}")
    for field in ("slot", "vtable", "flags", "program", "name", "state_mask", "color"):
        values = Counter(row[field] for row in rows)
        print(f"{field}: {len(values)} distinct")
        for value, count in values.most_common(12):
            if isinstance(value, int):
                print(f"  {count:3} 0x{value:08X}")
            else:
                print(f"  {count:3} {value}")
    if errors:
        print("errors:")
        for car, error in errors:
            print(f"  {car}: {error}")

    print(f"chrome_decoded={len(chrome_rows)}")
    for field in ("slot", "vtable", "flags", "first_program", "second_program", "state_mask", "color", "passes"):
        values = Counter(row[field] for row in chrome_rows)
        print(f"chrome {field}: {len(values)} distinct")
        for value, count in values.most_common(12):
            if isinstance(value, int):
                print(f"  {count:3} 0x{value:08X}")
            else:
                print(f"  {count:3} {value}")


if __name__ == "__main__":
    main()
