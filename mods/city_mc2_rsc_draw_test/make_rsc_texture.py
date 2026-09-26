#!/usr/bin/env python3
"""Pack the first PMD0 texture from the original MC2 RSC into one VCLQ packet.

The geometry remains read from mc2_losangeles.rsc at game runtime. This helper
only prepares the PS2 GS upload/switch for its first texture handle.
"""

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R
import mc2_tex_gs as TG

VCLQ = 0x514C4356
FLUSH = 0x11000000
TBP = 0x1500
CBP = 0x3800


def convert(path: Path, pmd_index: int) -> tuple[bytes, str, int]:
    rsc = R.Rsc(str(path))
    if rsc.entries[pmd_index][1] != "PMD0":
        raise ValueError(f"entry {pmd_index} is not PMD0")
    tags, used = R.dma_chain(rsc.block(pmd_index))
    if used != rsc.entries[pmd_index][2]:
        raise ValueError("PMD0 chain does not consume its block")
    handles = {tag.addr for tag in tags if tag.id == "call"}
    if len(handles) != 1:
        raise ValueError(f"prototype requires exactly one texture handle: {handles}")
    handle = handles.pop()
    tex = TG.load(rsc, handle) if handle else TG.white()
    upload = TG.upload_words(tex, TBP, CBP)
    switch = TG.switch_words(tex, TBP, CBP)
    body = bytearray()
    for words in (upload, switch):
        if len(words) % 4:
            raise ValueError("GS packet is not QWC-aligned")
        body += struct.pack("<2I", FLUSH, 0x50000000 | (len(words) // 4))
        body += struct.pack(f"<{len(words)}I", *words)
    if len(body) & 15:
        body += b"\0" * 8
    packet = struct.pack("<4I", len(body) // 16, VCLQ, handle, len(body)) + body
    return packet, tex.name, handle


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rsc", type=Path)
    ap.add_argument("outputs", nargs="+", type=Path)
    ap.add_argument("--pmd", type=int, default=3426)
    args = ap.parse_args()
    packet, name, handle = convert(args.rsc, args.pmd)
    for out in args.outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(packet)
        print(f"{out}: {len(packet)} bytes; handle={handle:#x}; texture={name}")


if __name__ == "__main__":
    main()
