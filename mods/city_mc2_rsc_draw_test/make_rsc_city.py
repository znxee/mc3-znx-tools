#!/usr/bin/env python3
"""Build the all-city MC2 PS2 RSC manifests for the direct MC3 renderer.

RSCM v2 contains each selected static component/template PMD once, in RSC
directory order. RINS v1 maps CMI0 models to those PMDs and carries one small
matrix VCLQ packet for each `.hood` instance. Geometry remains shared; the
runtime queues a matrix packet followed by the template PMD packet(s).

RCTX maps the referenced handles to deduplicated original MC2 textures using
the same GS packet generator as the tiled CPVS renderer.
"""

import argparse
import math
import struct
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R
from make_rsc_full_texture_bank import build_for_pmds

RSCM = 0x4D435352  # RSCM
RINS = 0x534E4952  # RINS
VCLQ = 0x514C4356
PMD_RECORD = struct.Struct("<III4f")
MODEL_RECORD = struct.Struct("<8I")
INSTANCE_RECORD = struct.Struct("<I4f")
INSTANCE_PACKET_BYTES = 96
MAX_PIECES = 1536
MAX_TEXTURES = 2048
MAX_INSTANCES = 9000
MAX_MODEL_PMDS = 4


def determinant(m) -> float:
    return (m[0] * (m[4] * m[8] - m[5] * m[7])
            - m[1] * (m[3] * m[8] - m[5] * m[6])
            + m[2] * (m[3] * m[7] - m[4] * m[6]))


def _matrix_packet(instance) -> bytes:
    """Make a unique VU address-12 matrix packet for one placed template."""
    r = instance.rot
    t = instance.pos
    matrix = (
        r[0][0], r[0][1], r[0][2], 0.0,
        r[1][0], r[1][1], r[1][2], 0.0,
        r[2][0], r[2][1], r[2][2], 0.0,
        t[0], t[1], t[2], 1.0,
    )
    body = struct.pack("<2I16f", 0x11000000, 0x6C04000C, *matrix) + b"\0" * 8
    if len(body) != 80:
        raise AssertionError("matrix VCLQ body must be 80 bytes")
    return struct.pack("<4I", 5, VCLQ, 0, len(body)) + body


def _texture_bank(rsc_path: Path, pmds: list[int]) -> tuple[bytes, int]:
    bank, stats = build_for_pmds(R.Rsc(str(rsc_path)), pmds)
    return bank, stats["handles"]


def build(rsc_path: Path) -> tuple[bytes, bytes, bytes, dict[str, int]]:
    rsc = R.Rsc(str(rsc_path))
    models = R.read_models(rsc)
    by_name = {model.name.lower(): model for model in models}
    city_dir = R.city_folder(str(rsc_path))
    components, instances = R.read_hoods(city_dir)
    component_names = {item.name.lower() for item in components}
    instance_types = {item.type.lower() for item in instances}
    required_names = component_names | instance_types
    missing = sorted(required_names - set(by_name))
    if missing:
        raise ValueError(f"{len(missing)} `.hood` names do not resolve to CMI0: {missing[:8]}")
    if len(components) != 294 or len(instances) != 8500 or len(instance_types) != 429:
        raise ValueError("unexpected PS2 Los Angeles .hood counts; refusing to emit a partial city")

    # The runtime uses dense ids for the 723 CMI0s. Keep a separate map from
    # each model's RSC directory entry to that dense id for instance lookups.
    # The static and instanced sets partition the 723 models for this city.
    model_ids = {model.index: i for i, model in enumerate(models)}
    model_rows: dict[int, tuple[int, list[int]]] = {}
    pmd_owner: dict[int, tuple[int, int]] = {}
    handles: set[int] = set()
    for model in models:
        name = model.name.lower()
        if name not in required_names:
            continue
        flags = 0 if name in component_names else 1
        selected = R.model_blocks(model, None)
        pmd_indices: list[int] = []
        for handle in selected:
            resolved = rsc.resolve(handle)
            if resolved is None or resolved[0] is not rsc:
                raise ValueError(f"external/missing PMD handle {handle:#x}: {model.name}")
            pmd = resolved[1]
            if rsc.entries[pmd][1] != "PMD0":
                raise ValueError(f"non-PMD0 handle {handle:#x}: {model.name}")
            if pmd in pmd_owner:
                raise ValueError(f"PMD {pmd} is referenced by more than one CMI0")
            pmd_owner[pmd] = (model_ids[model.index], flags)
            pmd_indices.append(pmd)
            tags, consumed = R.dma_chain(rsc.block(pmd))
            if consumed != rsc.entries[pmd][2]:
                raise ValueError(f"PMD {pmd} chain does not consume its block")
            handles.update(tag.addr for tag in tags if tag.id == "call")
        if not pmd_indices or len(pmd_indices) > MAX_MODEL_PMDS:
            raise ValueError(f"{model.name}: expected 1..{MAX_MODEL_PMDS} near/fallback PMDs")
        model_rows[model_ids[model.index]] = (flags, pmd_indices)

    if len(model_rows) != len(models) or len(model_rows) != 723:
        raise ValueError("the `.hood` model set must cover every Los Angeles CMI0")
    if len(instances) > MAX_INSTANCES:
        raise ValueError(f"instance count exceeds runtime limit {MAX_INSTANCES}")
    pmds = sorted(pmd_owner)
    if len(pmds) > MAX_PIECES:
        raise ValueError(f"PMD count exceeds runtime limit {MAX_PIECES}: {len(pmds)}")
    pmd_record_index = {pmd: index for index, pmd in enumerate(pmds)}

    pmd_records = bytearray()
    for pmd in pmds:
        model_id, flags = pmd_owner[pmd]
        model = models[model_id]
        pmd_records += PMD_RECORD.pack(pmd, model_id, flags,
                                       *model.centre, model.radius)
    manifest = struct.pack("<4I", RSCM, 2,
                           16 + len(pmd_records), len(pmds)) + pmd_records

    # Complete CMI-indexed draw map. Each group owns at most four PMD packets.
    model_records = bytearray()
    for model_id, model in enumerate(models):
        flags, model_pmds = model_rows[model_id]
        references = [pmd_record_index[pmd] for pmd in model_pmds]
        references += [0xFFFFFFFF] * (MAX_MODEL_PMDS - len(references))
        model_records += MODEL_RECORD.pack(model_id, flags, len(model_pmds),
                                           *references, 0)

    instance_records = bytearray()
    matrix_packets = bytearray()
    negative_determinants = 0
    for instance in instances:
        model = by_name[instance.type.lower()]
        model_id = model_ids[model.index]
        group = model_rows[model_id]
        if group[0] != 1:
            raise ValueError(f"instance {instance.type} resolved to a static model")
        cx = (instance.emin[0] + instance.emax[0]) * 0.5
        cy = (instance.emin[1] + instance.emax[1]) * 0.5
        cz = (instance.emin[2] + instance.emax[2]) * 0.5
        radius = math.sqrt(sum((instance.emax[k] - instance.emin[k]) ** 2
                               for k in range(3))) * 0.5 + 0.03
        if not all(map(math.isfinite, (cx, cy, cz, radius))):
            raise ValueError("non-finite instance bound")
        instance_records += INSTANCE_RECORD.pack(model_id, cx, cy, cz, radius)
        matrix_packets += _matrix_packet(instance)
        det = determinant([x for row in instance.rot for x in row])
        negative_determinants += det < 0.0

    header_bytes = 32
    models_offset = header_bytes
    instances_offset = models_offset + len(model_records)
    packets_offset = (instances_offset + len(instance_records) + 15) & ~15
    instance_bank = bytearray(packets_offset)
    instance_bank[:32] = struct.pack("<8I", RINS, 1,
                                     packets_offset + len(matrix_packets),
                                     len(model_rows), len(instances),
                                     models_offset, instances_offset,
                                     packets_offset)
    instance_bank[models_offset:instances_offset] = model_records
    instance_bank[instances_offset:instances_offset + len(instance_records)] = instance_records
    instance_bank += matrix_packets

    texture_bank, handle_count = _texture_bank(rsc_path, pmds)
    counts = {
        "components": len(component_names),
        "types": len(instance_types),
        "instances": len(instances),
        "pmds": len(pmds),
        "handles": handle_count,
        "mirrored": negative_determinants,
        "manifest_bytes": len(manifest),
        "instance_bytes": len(instance_bank),
        "texture_bytes": len(texture_bank),
    }
    return manifest, bytes(instance_bank), texture_bank, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rsc", type=Path)
    ap.add_argument("output_mods", type=Path)
    ap.add_argument("hostfs", type=Path)
    args = ap.parse_args()
    manifest, instances, textures, counts = build(args.rsc)
    for root in (args.output_mods, args.hostfs):
        root.mkdir(parents=True, exist_ok=True)
        (root / "mc2_rsc_manifest.bin").write_bytes(manifest)
        (root / "mc2_rsc_instances.bin").write_bytes(instances)
        (root / "mc2_rsc_texture_bank.bin").write_bytes(textures)
        print(f"{root}: RSCM {len(manifest)}; RINS {len(instances)}; "
              f"RCTX {len(textures)} bytes")
    print("LA PS2 full city: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
