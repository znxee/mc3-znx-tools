#!/usr/bin/env python3
"""Disassemble one virtual-address range from an ELF32 MIPS image."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("start", type=lambda value: int(value, 0))
    parser.add_argument("end", type=lambda value: int(value, 0))
    args = parser.parse_args()
    blob = args.elf.read_bytes()
    phoff = struct.unpack_from("<I", blob, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", blob, 0x2A)
    for segment_index in range(phnum):
        ph = phoff + segment_index * phentsize
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(
            "<IIIII", blob, ph
        )
        if p_type != 1 or not (p_vaddr <= args.start < p_vaddr + p_filesz):
            continue
        begin = p_offset + args.start - p_vaddr
        finish = min(p_offset + args.end - p_vaddr, p_offset + p_filesz)
        md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
        md.skipdata = True
        for insn in md.disasm(blob[begin:finish], args.start):
            print(f"{insn.address:08X}: {insn.mnemonic:8s} {insn.op_str}")
        return
    raise ValueError("start outside the loaded segments")


if __name__ == "__main__":
    main()
