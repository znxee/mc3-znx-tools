#!/usr/bin/env python3
"""Find direct JAL call sites to one MIPS virtual address in an ELF32 image."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("target", type=lambda value: int(value, 0))
    parser.add_argument("--context", type=int, default=16)
    args = parser.parse_args()
    wanted = 0x0C000000 | ((args.target >> 2) & 0x03FFFFFF)
    blob = args.elf.read_bytes()
    phoff = struct.unpack_from("<I", blob, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", blob, 0x2A)
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    md.skipdata = True
    for segment_index in range(phnum):
        ph = phoff + segment_index * phentsize
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(
            "<IIIII", blob, ph
        )
        if p_type != 1 or not p_filesz:
            continue
        data = blob[p_offset:p_offset + p_filesz]
        for offset in range(0, len(data) - 3, 4):
            if struct.unpack_from("<I", data, offset)[0] != wanted:
                continue
            start = max(0, offset - args.context * 4)
            end = min(len(data), offset + (args.context + 1) * 4)
            print(f"\n[segment {segment_index}] call 0x{p_vaddr + offset:08X}")
            for insn in md.disasm(data[start:end], p_vaddr + start):
                marker = ">" if insn.address == p_vaddr + offset else " "
                print(f"{marker} {insn.address:08X}: {insn.mnemonic:8s} {insn.op_str}")


if __name__ == "__main__":
    main()
