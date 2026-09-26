#!/usr/bin/env python3
"""Build one RSCM model manifest and its matching RCTX texture bank.

Only named CMI0 near-LOD PMD0s are selected. Geometry stays in the original
.rsc and is read by the game at runtime; these sidecars hold placement metadata
and texture packets. Keep the two outputs together when changing the config.
"""

import argparse
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R
# RCTX v2 like the all-city bank: the runtime rejects v1 (fixed VRAM addresses).
from make_rsc_full_texture_bank import build_for_pmds

MAGIC = 0x4D435352  # RSCM
MAX_PIECES = 32
RECORD = struct.Struct("<I9f")


def build(rsc_path: Path, config_path: Path) -> tuple[bytes, bytes, list[str]]:
    rsc = R.Rsc(str(rsc_path))
    models = R.models_by_name(rsc)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    records = []
    pmds = []
    labels = []
    for group in config["groups"]:
        origin_name = group["anchor_model"].lower()
        if origin_name not in models:
            raise ValueError(f"unknown preview anchor: {origin_name}")
        origin = models[origin_name].centre
        lateral = float(group["lateral"])
        forward = float(group["forward"])
        if not all(map(math.isfinite, (*origin, lateral, forward))):
            raise ValueError("non-finite placement")
        for name in group["models"]:
            model = models.get(name.lower())
            if model is None:
                raise ValueError(f"unknown CMI0 model: {name}")
            handles = R.model_blocks(model, 0)
            if not handles:
                raise ValueError(f"no near-LOD PMD0: {name}")
            for handle in handles:
                resolved = rsc.resolve(handle)
                if resolved is None or resolved[0] is not rsc:
                    raise ValueError(f"external or missing PMD0 {handle:#x}: {name}")
                pmd = resolved[1]
                if rsc.entries[pmd][1] != "PMD0" or pmd in pmds:
                    raise ValueError(f"duplicate/non-PMD0 {pmd}: {name}")
                values = (*model.centre, model.radius, *origin, lateral, forward)
                if not all(map(math.isfinite, values)):
                    raise ValueError(f"non-finite CMI0: {name}")
                records.append(RECORD.pack(pmd, *values))
                pmds.append(pmd)
                labels.append(f"{pmd}:{model.name}")
    if not 0 < len(records) <= MAX_PIECES:
        raise ValueError(f"PMD count must be 1..{MAX_PIECES}, got {len(records)}")
    # The runtime reads the original RSC sequentially. Keep its PMD requests
    # in directory order so it can stream forward once instead of reopening
    # and rescanning the multi-megabyte file for every model.
    ordered = sorted(zip(pmds, records, labels), key=lambda row: row[0])
    pmds = [row[0] for row in ordered]
    records = [row[1] for row in ordered]
    labels = [row[2] for row in ordered]
    manifest = struct.pack("<4I", MAGIC, 1, 16 + len(records) * RECORD.size,
                           len(records)) + b"".join(records)
    bank, stats = build_for_pmds(R.Rsc(str(rsc_path)), pmds)
    return manifest, bank, labels + [f"{stats['handles']} texture handles, {stats['images']} images"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rsc", type=Path)
    ap.add_argument("config", type=Path)
    ap.add_argument("output_mods", type=Path)
    ap.add_argument("hostfs", type=Path)
    args = ap.parse_args()
    manifest, bank, labels = build(args.rsc, args.config)
    for root in (args.output_mods, args.hostfs):
        root.mkdir(parents=True, exist_ok=True)
        (root / "mc2_rsc_manifest.bin").write_bytes(manifest)
        (root / "mc2_rsc_texture_bank.bin").write_bytes(bank)
        print(f"{root}: manifest {len(manifest)} bytes, bank {len(bank)} bytes")
    print("; ".join(labels))


if __name__ == "__main__":
    main()
