#!/usr/bin/env python3
"""Find MIPS instructions whose immediate matches selected values."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("immediate", nargs="+", type=lambda value: int(value, 0))
    parser.add_argument("--context", type=int, default=12)
    args = parser.parse_args()
    wanted = {value & 0xFFFF for value in args.immediate}
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)

    blob = args.elf.read_bytes()
    if blob[:4] != b"\x7fELF" or blob[4] != 1 or blob[5] != 1:
        raise ValueError("expected a little-endian ELF32")
    phoff = struct.unpack_from("<I", blob, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", blob, 0x2A)
    for segment_index in range(phnum):
        ph = phoff + segment_index * phentsize
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags = struct.unpack_from(
            "<IIIIIII", blob, ph
        )
        if p_type != 1 or not (p_flags & 1) or not p_filesz:
            continue
        data = blob[p_offset:p_offset + p_filesz]
        base = p_vaddr
        hits = []
        for offset in range(0, len(data) - 3, 4):
            word = struct.unpack_from("<I", data, offset)[0]
            if (word & 0xFFFF) in wanted:
                hits.append(offset)
        for offset in hits:
            start = max(0, offset - args.context * 4)
            end = min(len(data), offset + (args.context + 1) * 4)
            print(f"\n[segment {segment_index}] hit 0x{base + offset:08X}")
            for insn in md.disasm(data[start:end], base + start):
                marker = ">" if insn.address == base + offset else " "
                print(f"{marker} {insn.address:08X}: {insn.mnemonic:8s} {insn.op_str}")


if __name__ == "__main__":
    main()
