#!/usr/bin/env python3
"""Convert an MCS2 viewer chain into immutable AddVCLCall packets.

The first packets contain VU code, GS state and textures. Each near-LOD piece
gets its own packet and world-space box, so the game can cull it each frame.
DMA tags are flattened; their VIF words and payloads are retained in order.
"""

import argparse
import struct
from pathlib import Path


SOURCE_MAGIC = 0x3253434D  # MCS2
BUNDLE_MAGIC = 0x3251434D  # MCQ2
PACKET_MAGIC = 0x514C4356  # VCLQ
MAX_QWC = 0x3FFF
HEADER_BYTES = 64
PIECE_BYTES = 32


def tags(chain: bytes, first_word: int, last_word: int):
    """Yield complete CNT tags through the tag beginning at last_word."""
    pos = first_word * 4
    last = last_word * 4
    if pos > last or last + 16 > len(chain):
        raise ValueError(f"invalid tag range {first_word:#x}..{last_word:#x}")
    while pos <= last:
        word0, _addr, vif0, vif1 = struct.unpack_from("<4I", chain, pos)
        qwc, kind = word0 & 0xFFFF, (word0 >> 28) & 7
        end = pos + 16 + qwc * 16
        if kind != 1 or end > len(chain):
            raise ValueError(f"unexpected DMA tag {kind} at {pos:#x}")
        yield struct.pack("<2I", vif0, vif1) + chain[pos + 16:end]
        pos = end
    if pos <= last:
        raise ValueError("tag walk did not reach last tag")


def packet(body: bytes):
    if len(body) & 15:
        body += b"\0" * 8  # one VIF NOP pair after an odd number of DMA tags
    qwc = len(body) // 16
    if not 1 <= qwc <= MAX_QWC:
        raise ValueError(f"packet has {qwc} QWC")
    return struct.pack("<4I", qwc, PACKET_MAGIC, 0, len(body)) + body


def parse(data: bytes):
    if len(data) < 128:
        raise ValueError("short MCS2 header")
    hdr = struct.unpack_from("<32I", data)
    if hdr[0] != SOURCE_MAGIC:
        raise ValueError("not an MCS2 viewer scene")
    first_piece, chain_words, static_last = hdr[1:4]
    table_off, count, end_word = hdr[21:24]
    if not (0 < static_last < first_piece < end_word + 4 <= chain_words):
        raise ValueError("invalid chain boundaries")
    if count == 0 or count > 16000 or table_off != 128 + chain_words * 4:
        raise ValueError("invalid piece table")
    if table_off + count * 64 != len(data):
        raise ValueError("piece table does not end at EOF")
    chain = data[128:table_off]
    pieces = [struct.unpack_from("<2I6f2I2I4I", data, table_off + i * 64)
              for i in range(count)]
    return chain, first_piece, static_last, pieces


def convert(data: bytes):
    chain, first_piece, static_last, pieces = parse(data)
    out = bytearray(HEADER_BYTES)
    static_recs = []
    piece_recs = []

    def append(body: bytes):
        offset = len(out)
        encoded = packet(body)
        out.extend(encoded)
        return offset, len(encoded)

    # The first static tag begins at word 0. Its VU block holds the old
    # view-projection matrix; record its 16-float address for runtime updates.
    static_body = bytearray()
    matrix_abs = None
    pos = 0
    while pos <= static_last:
        word0 = struct.unpack_from("<I", chain, pos * 4)[0]
        end = pos + 4 + 4 * (word0 & 0xFFFF)
        vif = next(tags(chain, pos, pos))
        if len(static_body) + len(vif) + 8 > MAX_QWC * 16:
            static_recs.append(append(bytes(static_body)))
            static_body.clear()
        if not static_body and matrix_abs is None:
            marker = vif.find(struct.pack("<I", 0x6C400000))
            if marker >= 0:
                matrix_abs = len(out) + 16 + marker + 4 + 4 * 16
        static_body += vif
        pos = end
    if pos != first_piece or matrix_abs is None:
        raise ValueError("static region or VU view-projection marker invalid")
    if static_body:
        static_recs.append(append(bytes(static_body)))

    for i, entry in enumerate(pieces):
        first, last = entry[:2]
        if first < first_piece or last >= len(chain) // 4 or first > last:
            raise ValueError(f"invalid piece {i} tag range")
        body = b"".join(tags(chain, first, last))
        off, size = append(body)
        piece_recs.append((off, size, *entry[2:8]))

    static_table = len(out)
    for rec in static_recs:
        out += struct.pack("<2I", *rec)
    piece_table = len(out)
    for rec in piece_recs:
        out += struct.pack("<2I6f", *rec)
    struct.pack_into("<8I", out, 0, BUNDLE_MAGIC, 1, len(out),
                     len(static_recs), len(piece_recs), static_table,
                     piece_table, matrix_abs)
    return bytes(out), len(static_recs), len(piece_recs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="MCS2 scene generated with --view")
    ap.add_argument("output", type=Path, help="MCQ2 packet bundle")
    args = ap.parse_args()
    result, nstatic, npieces = convert(args.input.read_bytes())
    args.output.write_bytes(result)
    print(f"{args.output}: {len(result)} bytes; {nstatic} static packets; "
          f"{npieces} independently culled pieces")


if __name__ == "__main__":
    main()
