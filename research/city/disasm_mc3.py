#!/usr/bin/env python3
"""Disassemble a virtual-address range from the local MC3 analysis ELF."""
import argparse
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('elf')
    parser.add_argument('address', type=lambda value: int(value, 0))
    parser.add_argument('size', type=lambda value: int(value, 0))
    args = parser.parse_args()
    data = open(args.elf, 'rb').read()
    phoff = struct.unpack_from('<I', data, 28)[0]
    phentsize, phnum = struct.unpack_from('<HH', data, 42)
    file_offset = None
    for index in range(phnum):
        ptype, poff, pvaddr, _ppaddr, pfilesz, _pmemsz, _flags, _align = \
            struct.unpack_from('<8I', data, phoff + index * phentsize)
        if ptype == 1 and pvaddr <= args.address < pvaddr + pfilesz:
            file_offset = poff + args.address - pvaddr
            break
    if file_offset is None:
        raise SystemExit('address is not in a file-backed LOAD segment')
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    # Capstone does not know every R5900/COP2 opcode; keep walking across
    # unknown four-byte words so the surrounding integer code remains useful.
    md.skipdata = True
    for insn in md.disasm(data[file_offset:file_offset + args.size], args.address):
        print('%08X  %-8s %s' % (insn.address, insn.mnemonic, insn.op_str))


if __name__ == '__main__':
    main()
