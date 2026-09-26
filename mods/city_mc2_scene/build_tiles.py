#!/usr/bin/env python3
"""Build overlapping MC2 Los Angeles bundles for the in-game scene mod.

Grid cells are 500 m apart. Each 400 m half-width scene overlaps neighbours,
so moving the freecam across a cell boundary does not expose a gap in source
coverage. The MC2 generator chooses textures per tile to stay within PS2 VRAM.
"""

import os
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from pack_scene_queue import convert


X_START, Z_START, STEP, RADIUS = -2000, -1800, 500, 400
NX, NZ = 8, 7
MAX_BUNDLE = 8 * 1024 * 1024


def intersects(obj, x, z):
    return (obj.emin[0] <= x + RADIUS and obj.emax[0] >= x - RADIUS
            and obj.emin[2] <= z + RADIUS and obj.emax[2] >= z - RADIUS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rsc", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--toolkit", type=Path,
                    default=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')))
    ap.add_argument("--tile", nargs=2, type=int, metavar=("X", "Z"),
                    help="build one cell by index (0..7, 0..6)")
    args = ap.parse_args()

    sys.path.insert(0, str(args.toolkit))
    import mc2_rsc as rsc_tool

    comps, insts = rsc_tool.read_hoods(rsc_tool.city_folder(args.rsc))
    if not comps or not insts:
        raise SystemExit("MC2 hood components/instances not found")
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for gx in range(NX):
        for gz in range(NZ):
            if args.tile and (gx, gz) != tuple(args.tile):
                continue
            x = X_START + gx * STEP + STEP // 2
            z = Z_START + gz * STEP + STEP // 2
            if not any(intersects(obj, x, z) for obj in comps + insts):
                continue
            name = f"mc2t_{gx:02d}{gz:02d}.vcl"
            with tempfile.TemporaryDirectory(prefix="mc2_tile_") as tmp:
                scene = Path(tmp) / "scene.bin"
                cmd = [sys.executable, str(args.toolkit / "mc2_scene_blob.py"),
                       str(args.rsc), "--pieces", "4096", "--radius", str(RADIUS),
                       "--centre", str(x), str(z), "--view", "--bin", str(scene)]
                proc = subprocess.run(cmd, text=True, capture_output=True,
                                      check=True)
                match = re.search(r"(\d+) pieces chosen of (\d+) candidates, "
                                  r"(\d+) textures", proc.stdout)
                if not match:
                    raise RuntimeError(f"cannot parse tile {gx},{gz}: {proc.stdout}")
                chosen, candidates, textures = map(int, match.groups())
                if chosen == 0:
                    continue
                bundle, nstatic, npieces = convert(scene.read_bytes())
                if npieces != chosen or len(bundle) > MAX_BUNDLE:
                    raise RuntimeError(f"tile {gx},{gz}: count/heap limit exceeded")
                (args.out / name).write_bytes(bundle)
            row = dict(x=gx, z=gz, center_x=x, center_z=z, radius=RADIUS,
                       file=name, pieces=chosen, candidates=candidates,
                       textures=textures, static_packets=nstatic,
                       bytes=len(bundle))
            rows.append(row)
            print(f"{name}: {chosen}/{candidates} pieces, {textures} textures, "
                  f"{len(bundle)//1024} KiB", flush=True)
    manifest = dict(grid=dict(x_start=X_START, z_start=Z_START, step=STEP,
                              radius=RADIUS, nx=NX, nz=NZ), tiles=rows)
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                            encoding="utf-8")
    print(f"{len(rows)} tiles; {sum(r['pieces'] for r in rows)} piece placements "
          f"across tiles; {sum(r['bytes'] for r in rows)//1048576} MiB total",
          flush=True)


if __name__ == "__main__":
    main()
