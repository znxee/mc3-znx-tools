#!/usr/bin/env python3
"""Fix the clipper GIFtag templates (VU1 data[36]/[37]) in already built .vcl files.

The rv1_code clipper (+0x608) emits each clipped polygon as a triangle FAN on a
copy of data[36] or data[37]. Tiles built before 2026-09-25 carried copies of
the strip template data[35] there, so every clipped polygon lost a triangle
and drew a wrong one instead: the dark triangular cuts along screen edges.
mc2_vu_blob.data_block now writes fan templates; this patches existing
bundles in place without regenerating them. Idempotent.

    python patch_clip_template.py $MC3_HOSTFS/mc2t_*.vcl $MC3_HOSTFS/mc2_rsc_vu_bootstrap.vcl
"""
import glob
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_vu_blob as B

DATA_BLOCK = struct.pack("<I", 0x6C400000)  # UNPACK V4-32, 64 qwords, VU address 0


def patch(data: bytearray) -> int:
    """Rewrite data[36]/[37] of every VU data block; returns the slots changed."""
    # VIF codes are word aligned; the same four bytes also occur by chance at odd
    # offsets inside texture/vertex payloads (mc2t_0200 has one at 0x337D76).
    blocks = []
    at = data.find(DATA_BLOCK)
    while at >= 0:
        if at % 4 == 0:
            blocks.append(at)
        at = data.find(DATA_BLOCK, at + 1)
    if not blocks:
        raise ValueError("no VU data-block UNPACK")
    changed = 0
    for at in blocks:
        base = at + 4
        strip = list(struct.unpack_from("<4I", data, base + 35 * 16))
        if strip[0] & 0x8000 == 0 or strip[1] >> 28 != 3 or strip[2] != 0x512:
            raise ValueError("data[35] at 0x%X is not the expected GIFtag" % base)
        fan = B.clip_template(strip)
        for slot in (36, 37):
            off = base + slot * 16
            if list(struct.unpack_from("<4I", data, off)) != fan:
                struct.pack_into("<4I", data, off, *fan)
                changed += 1
    return changed


def main():
    paths = [p for a in sys.argv[1:] for p in (glob.glob(a) or [a])]
    if not paths:
        raise SystemExit(__doc__)
    total = 0
    for p in paths:
        data = bytearray(Path(p).read_bytes())
        n = patch(data)
        if n:
            Path(p).write_bytes(data)
        total += n
        print("%s: %d slot(s) changed" % (p, n))
    print("total %d slot(s) in %d file(s)" % (total, len(paths)))


if __name__ == "__main__":
    main()
