#!/usr/bin/env python3
"""Flatten mc2_vu_blob.py's DMA chain into a lowPsxGfx::AddVCLCall packet."""

import argparse
import struct
from pathlib import Path

MAGIC = 0x514C4356  # VCLQ
HEADER_SIZE = 16


def convert(data: bytes):
    offset = 0
    stream = bytearray()
    tags = 0
    while offset + 16 <= len(data):
        word0, _word1, vif0, vif1 = struct.unpack_from('<4I', data, offset)
        qwc = word0 & 0xFFFF
        tag_id = (word0 >> 28) & 7
        payload_start = offset + 16
        payload_end = payload_start + qwc * 16
        if payload_end > len(data):
            raise ValueError(f'DMA tag at 0x{offset:X} extends beyond input')
        if tag_id not in (1, 7):  # This generated artifact should be CNT ... END.
            raise ValueError(f'unexpected DMA tag id {tag_id} at 0x{offset:X}')
        stream += struct.pack('<2I', vif0, vif1)
        stream += data[payload_start:payload_end]
        offset = payload_end
        tags += 1
        if tag_id == 7:
            break
    if offset != len(data):
        raise ValueError(f'chain consumed {offset} of {len(data)} bytes')
    if len(stream) % 16:
        raise ValueError('flattened VIF stream is not quadword aligned')
    qwc = len(stream) // 16
    if qwc == 0 or qwc > 0xFFFF:
        raise ValueError(f'AddVCLCall QWC out of range: {qwc}')

    # Locate the 64-qword VU data block at address zero emitted by mc2_vu_blob.
    words = struct.unpack(f'<{len(stream) // 4}I', stream)
    candidates = [i * 4 for i, word in enumerate(words) if word == 0x6C400000]
    if len(candidates) != 1:
        raise ValueError(f'expected one V4-32x64 UNPACK to VU0, got {candidates!r}')
    block = candidates[0] + 4
    matrix_translation = block + 15 * 16
    if matrix_translation + 16 > len(stream):
        raise ValueError('matrix translation falls outside the stream')

    header = bytearray(HEADER_SIZE)
    struct.pack_into('<H', header, 0, qwc)
    struct.pack_into('<I', header, 4, MAGIC)
    struct.pack_into('<I', header, 8, matrix_translation)  # payload-relative
    struct.pack_into('<I', header, 12, len(stream))
    return bytes(header + stream), tags, qwc, matrix_translation


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    result, tags, qwc, matrix = convert(args.input.read_bytes())
    args.output.write_bytes(result)
    print(f'{args.output}: {len(result)} bytes; {tags} DMA tags -> {qwc} QWC; '
          f'matrix translation payload+0x{matrix:X}')


if __name__ == '__main__':
    main()
