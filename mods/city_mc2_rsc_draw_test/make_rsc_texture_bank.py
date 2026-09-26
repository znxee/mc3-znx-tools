#!/usr/bin/env python3
"""Pack textures referenced by the selected original MC2 PMD0s into RCTX.

The mod still reads the geometry from the original .rsc at game runtime.
RCTX contains VCLQ uploads and switches only; CALL handles select records.
"""

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R
import mc2_tex_gs as TG

MAGIC = 0x58544352  # RCTX
VCLQ = 0x514C4356
FLUSH = 0x11000000
TBP_FIRST = 0x1500
CBP_FIRST = 0x3800


def vcl_packet(words: list[int], handle: int) -> bytes:
    if len(words) % 4:
        raise ValueError("GS DIRECT data is not quadword-aligned")
    body = bytearray(struct.pack("<2I", FLUSH, 0x50000000 | (len(words) // 4)))
    body += struct.pack(f"<{len(words)}I", *words)
    body += bytes((-len(body)) & 15)
    if len(body) // 16 > 0x3FFF:
        raise ValueError("VCLQ packet too large")
    return struct.pack("<4I", len(body) // 16, VCLQ, handle, len(body)) + body


def convert(path: Path, pmd_indices: list[int]) -> tuple[bytes, list[tuple[int, str]]]:
    rsc = R.Rsc(str(path))
    handles = []
    for pmd_index in pmd_indices:
        if rsc.entries[pmd_index][1] != "PMD0":
            raise ValueError(f"entry {pmd_index} is not PMD0")
        tags, used = R.dma_chain(rsc.block(pmd_index))
        if used != rsc.entries[pmd_index][2]:
            raise ValueError("PMD0 chain does not consume its block")
        for tag in tags:
            if tag.id == "call" and tag.addr not in handles:
                handles.append(tag.addr)
    if not handles or len(handles) > 64:
        raise ValueError(f"unsupported texture-handle count: {len(handles)}")
    out = bytearray(16 + len(handles) * 20)
    out += bytes((-len(out)) & 15)
    names = []
    tbp, cbp = TBP_FIRST, CBP_FIRST
    for i, handle in enumerate(handles):
        tex = TG.load(rsc, handle) if handle else TG.white()
        # The same 256-byte-block allocation scheme as mc2_scene_blob.py.
        tex_blocks = (tex.w * tex.h * tex.bpp // 8 + 255) // 256
        tex_blocks = (tex_blocks + 3) & ~3
        if tbp + tex_blocks > 0x3800 or cbp + 4 > 0x4000:
            raise ValueError("texture bank exceeds reserved GS VRAM")
        upload = vcl_packet(TG.upload_words(tex, tbp, cbp), handle)
        switch = vcl_packet(TG.switch_words(tex, tbp, cbp), handle)
        up_off = len(out)
        out += upload
        sw_off = len(out)
        out += switch
        struct.pack_into("<5I", out, 16 + i * 20, handle,
                         up_off, len(upload), sw_off, len(switch))
        names.append((handle, tex.name))
        tbp += tex_blocks
        cbp += 4
    struct.pack_into("<4I", out, 0, MAGIC, 1, len(out), len(handles))
    return bytes(out), names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rsc", type=Path)
    ap.add_argument("outputs", nargs="+", type=Path)
    ap.add_argument("--pmd", type=int, action="append",
                    help="PMD0 index; repeat for every block (default: 3426, 3990, 3991)")
    args = ap.parse_args()
    bank, names = convert(args.rsc, args.pmd or [3426, 3990, 3991])
    for out in args.outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bank)
        print(f"{out}: {len(bank)} bytes; {len(names)} textures:",
              ", ".join(f"{h:#x}={n}" for h, n in names))


if __name__ == "__main__":
    main()
