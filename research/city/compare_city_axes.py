"""Compare local mesh axes and instance bases between two MC3 city PCKs."""
from __future__ import annotations

import os
import argparse
import importlib.util
from pathlib import Path
import re
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pck")
    parser.add_argument("--match", default="palm|tree")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    audit = load("cityaudit_axes", TOOLKIT / "mc3_cityaudit.py")
    pck, hf = audit._open(args.pck)
    instances, types = audit.collect(pck, hf)
    by_type = {}
    for off, type_va, matrix, center, radius in instances:
        by_type.setdefault(type_va, []).append((off, matrix, center, radius))

    wanted = re.compile(args.match, re.IGNORECASE)
    rows = []
    for type_va, (name, cd, meshes) in types.items():
        if not wanted.search(name):
            continue
        boxes = [audit._aabb_from_vif(pck, mesh) for mesh in meshes]
        box = next((item for item in boxes if item), None)
        dims = tuple(box[1][i] - box[0][i] for i in range(3)) if box else None
        owned = by_type.get(type_va, [])
        sample = owned[0][1] if owned else None
        up_row = sample[3:6] if sample else None
        up_col = (sample[1], sample[4], sample[7]) if sample else None
        rows.append((name, type_va, len(owned), dims, up_row, up_col, cd, meshes))

    print(Path(args.pck).name, "matching types:", len(rows))
    for name, type_va, count, dims, up_row, up_col, cd, meshes in rows[: args.limit]:
        dim_text = "?" if dims is None else "%.3f x %.3f x %.3f" % dims
        row_text = "?" if up_row is None else "(%.3f %.3f %.3f)" % up_row
        col_text = "?" if up_col is None else "(%.3f %.3f %.3f)" % up_col
        print(
            f"{name!r} type={type_va:08X} instances={count} "
            f"dims={dim_text} localY-row={row_text} localY-col={col_text} "
            f"cd=file+{cd:06X} meshes={','.join(f'{m:06X}' for m in meshes)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
