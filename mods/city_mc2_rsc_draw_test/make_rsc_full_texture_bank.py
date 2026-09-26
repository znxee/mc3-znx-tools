#!/usr/bin/env python3
"""Build the all-city RCTX v2 bank from the existing RSCM and MC2 GS textures.

Identical decoded TEX0/PAL0 images share one entry. v2 carries NO VRAM
addresses: in game, 0x1500.. is the bottom row of MC3's render target
(0x800..0x1600) and its Z16 buffer (0x1600..0x1D00), which is where the
coloured strip at the bottom of the screen came from. The runtime now takes
each upload from MC3's own per-frame texture ring (gfxTexture current/top at
0x70FBEC/0x70FBE8, base 0x70FBF0, generation 0x700005C0), so the bank holds
the parts that never change as ready VCLQ packets and the parts that carry an
address as templates with the address fields zero:

    header   RCTX, 2, total bytes, handle count, image count,
             handles offset, images offset, 0
    handle   handle, image index                              (8 bytes)
    image    texel header, texel data, CLUT header, CLUT data,
             texel blocks, CLUT blocks, TEX0 low, TEX0 high    (32 bytes)

A header template is the 80-byte GIF A+D packet of one transfer (tag with
EOP clear, BITBLTBUF with DBP 0, TRXPOS, TRXREG, TRXDIR); the matching data
packet is `NOP, DIRECT n` + the IMAGE tag and texels, so the GIF packet
continues across the two VCL calls exactly as in the single v1 upload.
"""

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R
import mc2_tex_gs as TG

RSCM = 0x4D435352
RCTX = 0x58544352
VCLQ = 0x514C4356
RING_MIN = 0x1000      # MC3's ring is base 0x1D01..top 0x3580; one image must fit easily
MAX_HANDLES = 2048


def pmd_indices(manifest: bytes) -> list[int]:
    if len(manifest) < 16:
        raise ValueError("short RSCM")
    magic, version, size, count = struct.unpack_from("<4I", manifest)
    if (magic, version, size) != (RSCM, 2, len(manifest)) or not 0 < count <= 1536:
        raise ValueError("expected complete RSCM v2")
    if len(manifest) != 16 + count * 28:
        raise ValueError("RSCM record count/length mismatch")
    pmds = [struct.unpack_from("<I", manifest, 16 + i * 28)[0]
            for i in range(count)]
    if pmds != sorted(set(pmds)):
        raise ValueError("RSCM PMDs must be unique and sorted")
    return pmds


def build_for_pmds(rsc: R.Rsc, pmds: list[int]) -> tuple[bytes, dict[str, int]]:
    handles: set[int] = set()
    for pmd in pmds:
        if rsc.entries[pmd][1] != "PMD0":
            raise ValueError(f"RSC entry {pmd} is not PMD0")
        tags, consumed = R.dma_chain(rsc.block(pmd))
        if consumed != rsc.entries[pmd][2]:
            raise ValueError(f"PMD {pmd} has an incomplete DMA chain")
        handles.update(tag.addr for tag in tags if tag.id == "call")
    if not handles or len(handles) > MAX_HANDLES:
        raise ValueError(f"unexpected texture handle count: {len(handles)}")

    # RNT names are not needed here: the original CALL handle resolves TEX0,
    # MIP0 and PAL0 through mc2_rsc, just as in the tiled scene generator.
    unique: list[tuple[TG.Texture, int]] = []
    unique_ids: dict[tuple, int] = {}
    handle_ids: dict[int, int] = {}
    for handle in sorted(handles):
        tex = TG.load(rsc, handle) if handle else TG.white()
        key = (tex.w, tex.h, tex.bpp, bytes(tex.pixels), bytes(tex.clut))
        index = unique_ids.get(key)
        if index is None:
            index = len(unique)
            unique_ids[key] = index
            tex_blocks = (tex.w * tex.h * tex.bpp // 8 + 1023) // 1024 * 4
            unique.append((tex, tex_blocks))
        handle_ids[handle] = index

    for tex, blocks in unique:
        if footprint(tex) > blocks:
            raise ValueError("texture %dx%d/%d touches more GS blocks than reserved"
                             % (tex.w, tex.h, tex.bpp))
    tex_blocks_total = sum(blocks for _tex, blocks in unique)
    clut_blocks_total = sum(clut_blocks(tex) for tex, _blocks in unique)
    largest = max(blocks + clut_blocks(tex) for tex, blocks in unique)
    if largest > RING_MIN:
        raise ValueError("one image needs %d blocks, more than MC3's ring" % largest)

    handles_off = 32
    images_off = handles_off + len(handles) * 8
    out = bytearray(images_off + len(unique) * 32)
    out += bytes((-len(out)) & 15)
    for i, (tex, blocks) in enumerate(unique):
        texel, clut = transfers(tex)
        offs = []
        for header, image in (texel, clut):
            offs.append(len(out))
            out += struct.pack("<%dI" % len(header), *header)
            offs.append(len(out))
            out += data_packet(image)
        t0 = TG.tex0(tex, 0, 0)
        struct.pack_into("<8I", out, images_off + i * 32, *offs,
                         blocks, clut_blocks(tex), t0 & 0xFFFFFFFF, t0 >> 32)
    for i, handle in enumerate(sorted(handles)):
        struct.pack_into("<2I", out, handles_off + i * 8, handle, handle_ids[handle])
    struct.pack_into("<8I", out, 0, RCTX, 2, len(out), len(handles), len(unique),
                     handles_off, images_off, 0)
    return bytes(out), {
        "pmds": len(pmds), "handles": len(handles), "images": len(unique),
        "tex_blocks": tex_blocks_total, "clut_blocks": clut_blocks_total,
        "largest_image_blocks": largest, "bank_bytes": len(out),
    }


def clut_blocks(tex) -> int:
    return 4 if tex.bpp == 8 else 1     # 16x16 or 8x2 PSMCT32 rectangle


# GS block numbering inside a page (PCSX2 GSBlock tables).
_BT8 = [[0, 1, 4, 5, 16, 17, 20, 21], [2, 3, 6, 7, 18, 19, 22, 23],
        [8, 9, 12, 13, 24, 25, 28, 29], [10, 11, 14, 15, 26, 27, 30, 31]]
_BT4 = [[0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
        [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31]]


def footprint(tex) -> int:
    """Blocks from TBP that a PSMT8/PSMT4 texture really touches."""
    bw, top = max(1, tex.w // 64), 0
    if tex.bpp == 8:
        for y in range(0, tex.h, 16):
            for x in range(0, tex.w, 16):
                top = max(top, ((y >> 6) * (bw >> 1) << 5) + ((x >> 7) << 5) +
                          _BT8[(y >> 4) & 3][(x >> 4) & 7])
    else:
        for y in range(0, tex.h, 16):
            for x in range(0, tex.w, 32):
                top = max(top, ((y >> 7) * (bw >> 1) << 5) + ((x >> 7) << 5) +
                          _BT4[(y >> 4) & 7][(x >> 5) & 3])
    return top + 1


def transfers(tex):
    """[(A+D header template, IMAGE words)] for the texels and the CLUT.

    Same GIF content as mc2_tex_gs.upload_words(tex, 0, 0), cut after TRXDIR.
    """
    words = TG.upload_words(tex, 0, 0)
    parts, at = [], 0
    for _ in range(2):
        header = words[at:at + 20]
        if header[0] != 4 or [header[k] for k in (6, 10, 14, 18)] != \
                [TG.BITBLTBUF, TG.TRXPOS, TG.TRXREG, TG.TRXDIR] or header[5] & 0x3FFF:
            raise ValueError("unexpected upload header layout")   # tag EOP clear, DBP 0
        nloop = words[at + 20] & 0x7FFF
        image = words[at + 20:at + 24 + nloop * 4]
        parts.append((header, image))
        at += 24 + nloop * 4
    if at != len(words):
        raise ValueError("upload has more than texel + CLUT transfers")
    return parts


def data_packet(words) -> bytes:
    """VCLQ packet `NOP, DIRECT n` + words. NOP, not FLUSH: the GIF packet opened
    by the header (EOP clear) is still in progress when this arrives."""
    body = bytearray(struct.pack("<2I", 0, 0x50000000 | (len(words) // 4)))
    body += struct.pack("<%dI" % len(words), *words)
    body += bytes((-len(body)) & 15)
    if len(words) % 4 or len(body) // 16 > 0x3FFF:
        raise ValueError("bad DIRECT payload")
    return struct.pack("<4I", len(body) // 16, VCLQ, 0, len(body)) + body


def build(rsc_path: Path, manifest_path: Path) -> tuple[bytes, dict[str, int]]:
    return build_for_pmds(R.Rsc(str(rsc_path)),
                          pmd_indices(manifest_path.read_bytes()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rsc", type=Path)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("outputs", nargs="+", type=Path)
    args = ap.parse_args()
    bank, stats = build(args.rsc, args.manifest)
    for out in args.outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bank)
        print(f"{out}: {len(bank)} bytes")
    print(", ".join(f"{name}={value}" for name, value in stats.items()))


if __name__ == "__main__":
    main()
