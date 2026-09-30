from __future__ import annotations

import os
import argparse
import importlib.util
import math
import sys
from pathlib import Path


BASE = Path(__file__).with_name("build_mc2_city_ppf.py")


def load_base():
    spec = importlib.util.spec_from_file_location("mc2_ppf_base_v3", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {BASE}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile MC2 PS2 .tex files into a pf05 PPF")
    ap.add_argument("--source", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'output', 'mc2_losangeles/texture')))
    ap.add_argument("--mc3-tex", type=Path, default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_tex.py')))
    ap.add_argument("--output", type=Path, default=Path(os.path.join(os.environ.get('MC3_WORK', '.'), 'output', 'mc2_city_ppf_v3/losangeles_midnight_clear.ppf')))
    ap.add_argument("--mips", type=int, default=0,
                    help="limit the level count (1 = level 0 only). The "
                         "engine only has rmcTexturePS2 donors with 1 or 3 "
                         "levels: measuring atlanta+sd+detroit, the LA shapes "
                         "with mips=2 have ZERO donors (11.2% coverage), and "
                         "with mips=1 or 3 coverage goes to 100%.")
    args = ap.parse_args()
    base = load_base()

    if args.mips:
        base.MIPS_MAX = args.mips
        print("  levels limited to %d" % args.mips)
    original_make_chunk = base.make_chunk

    def make_chunk_retail_header(texmod, tex, level_index):
        chunk, meta = original_make_chunk(texmod, tex, level_index)
        fixed = bytearray(chunk)
        fixed[0x18:0x1C] = bytes(4)
        if not meta["last_mip"]:
            blocks = meta["pixel_blocks"]
            if blocks <= 0 or blocks & (blocks - 1):
                raise AssertionError(f"non-power-of-two blocks: {blocks}")
            # Matches retail's ordinary no-CLUT chunks:
            # 256 blocks -> 0x24, 64 -> 0x20, 16 -> 0x1C, 4 -> 0x18.
            size_code = 0x14 + 2 * int(math.log2(blocks))
            fixed[0x14:0x16] = size_code.to_bytes(2, "little")
        return bytes(fixed), meta

    base.make_chunk = make_chunk_retail_header
    refs = args.output.with_suffix(".refs.tsv")
    manifest = args.output.with_suffix(".manifest.json")
    texmod = base.load_mc3_tex(args.mc3_tex)
    summary = base.build(texmod, args.source, args.output, refs, manifest)
    base.verify(texmod, args.output, args.source, manifest)
    print(f"OK: {summary['texture_count']} textures, {summary['mip_chunk_count']} chunks, {summary['page_count']} pages")
    print(f"PPF: {args.output} ({summary['file_size']} bytes)")
    print(f"SHA-256: {summary['sha256']}")
    print(f"refs: {refs}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
