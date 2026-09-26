#!/usr/bin/env python3
"""Find MIPS instructions whose signed immediate matches selected values."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32
from elftools.elf.elffile import ELFFile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("immediate", nargs="+", type=lambda value: int(value, 0))
    parser.add_argument("--context", type=int, default=12)
    args = parser.parse_args()
    wanted = {value & 0xFFFF for value in args.immediate}
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)

    with args.elf.open("rb") as handle:
        elf = ELFFile(handle)
        for section in elf.iter_sections():
            if not (section["sh_flags"] & 0x4) or not section["sh_size"]:
                continue
            data = section.data()
            base = section["sh_addr"]
            hits = []
            for offset in range(0, len(data) - 3, 4):
                word = struct.unpack_from("<I", data, offset)[0]
                if (word & 0xFFFF) in wanted:
                    hits.append(offset)
            for offset in hits:
                start = max(0, offset - args.context * 4)
                end = min(len(data), offset + (args.context + 1) * 4)
                print(f"\n[{section.name}] hit 0x{base + offset:08X}")
                for insn in md.disasm(data[start:end], base + start):
                    marker = ">" if insn.address == base + offset else " "
                    print(f"{marker} {insn.address:08X}: {insn.mnemonic:8s} {insn.op_str}")


if __name__ == "__main__":
    main()
