#!/usr/bin/env python3
"""Extract the tested MC3-compatible VU/data setup from mc2piece.bin.

The first CNT contains VU microcode and its data block; geometry starts at the
next DMA tag. Emit that setup plus the measured GS baseline as a VCLQ packet,
with word 2 recording the live view-projection matrix offset for runtime.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_tex_gs as TG
import mc2_vu_blob as B

VCLQ = 0x514C4356
DATA_BLOCK = 0x6C400000  # UNPACK V4-32, 64 qwords, VU address 0


def convert(source: bytes) -> bytes:
    if len(source) < 32:
        raise ValueError("short input DMA chain")
    lo, _addr, vif0, vif1 = struct.unpack_from("<4I", source)
    qwc, kind = lo & 0xFFFF, (lo >> 28) & 7
    if kind != 1:
        raise ValueError("first DMA tag is not CNT")
    end = 16 + qwc * 16
    if end > len(source):
        raise ValueError("first CNT exceeds the input chain")
    payload = bytearray(source[16:end])
    marker = struct.pack("<I", DATA_BLOCK)
    at = payload.find(marker)
    if at < 0 or payload.find(marker, at + 4) >= 0:
        raise ValueError("expected exactly one VU data-block UNPACK")
    # mc2piece.bin was an isolated geometry test: VU viewport q[8].z was 0,
    # so every surface got the same GS depth and the last drawn wall won.
    # Match native MC3 VU1 q[8].z/q[9].z, measured from a freeroam savestate:
    # near = larger Z with GEQUAL. The old -2000/2048 pair kept MC2 faces
    # internally ordered but biased them against MC3 traffic's Z values.
    z_scale = at + 4 + 8 * 16 + 8
    z_offset = at + 4 + 9 * 16 + 8
    if struct.unpack_from("<f", payload, z_scale)[0] != 0.0 or \
            struct.unpack_from("<f", payload, z_offset)[0] != 1000.0:
        raise ValueError("unexpected source viewport depth values")
    native_z = 2047.96875
    struct.pack_into("<f", payload, z_scale, -native_z)
    struct.pack_into("<f", payload, z_offset, native_z)
    # mc2piece.bin predates the clipper fix: its q[36]/q[37] are copies of the strip
    # template q[35]. The rv1_code clipper emits clipped polygons as fans, so a strip
    # template dropped/misplaced one triangle per clipped quad (the dark cuts).
    for slot in (36, 37):
        tag = at + 4 + slot * 16
        words = list(struct.unpack_from("<4I", payload, tag))
        struct.pack_into("<4I", payload, tag, *B.clip_template(words))
    # data[42] is the colour of the models without CPVS (roads, junctions:
    # program 8 multiplies it into the texture). mc2piece.bin carried an
    # orange test colour (0.95, 0.65, 0.25) that turned the asphalt into a
    # dark brown plane; MC2's own VU1 memory holds a neutral 0.8.
    struct.pack_into("<4f", payload, at + 4 + 42 * 16, 0.8, 0.8, 0.8, 1.0)
    for slot in (35, 36, 37):            # PRIM.ABE (PRIM bit 6 = word bit 21)
        tag = at + 4 + slot * 16 + 4
        struct.pack_into("<I", payload, tag,
                         struct.unpack_from("<I", payload, tag)[0] | (1 << 21))
    # Packet header + two tag-VIF words + marker + command word + q[4..7].
    matrix_offset = 16 + 8 + at + 4 + 4 * 16
    body = struct.pack("<2I", vif0, vif1) + payload
    # Reuse the measured standalone MC2 scene state: linear LOD, repeat wrap,
    # and the depth/alpha test used by the working MC2 scene packets.
    # MC2 blends everything: PRIM has ABE set and ALPHA is (Cs-Cd)*As+Cd, so
    # opaque surfaces carry alpha 0x80 and layers like the road reflection
    # (fx_reflection_01, over the asphalt) are translucent. Without it that
    # layer was drawn opaque and hid the asphalt. TEST as MC2: alpha test
    # NOTEQUAL 0 failing to FB only (cut-outs write no Z), Z GEQUAL.
    # Values read from the GS in an MC2 savestate. TEX1 is set per texture.
    gs = TG.gif_tag(4, 0)
    TG.ad(gs, 0x61, TG.TEX1_1)
    TG.ad(gs, 0, TG.CLAMP_1)
    TG.ad(gs, 0x5100F, 0x47)             # TEST_1
    TG.ad(gs, 0x44, 0x42)                # ALPHA_1
    if len(gs) & 3:
        raise ValueError("GS state packet is not QWC aligned")
    body += struct.pack("<2I", 0x11000000, 0x50000000 | (len(gs) // 4))
    body += struct.pack("<%dI" % len(gs), *gs)
    if len(body) & 15:
        body += b"\0" * 8
    if not body or len(body) // 16 > 0x3FFF:
        raise ValueError("bootstrap packet QWC is out of range")
    return struct.pack("<4I", len(body) // 16, VCLQ, matrix_offset, len(body)) + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="tested mc2piece.bin")
    ap.add_argument("outputs", nargs="+", type=Path,
                    help="output files, typically output/mods and HostFS")
    args = ap.parse_args()
    packet = convert(args.source.read_bytes())
    for out in args.outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(packet)
        print(f"{out}: {len(packet)} bytes; matrix byte offset 0x{struct.unpack_from('<I', packet, 8)[0]:X}")


if __name__ == "__main__":
    main()
