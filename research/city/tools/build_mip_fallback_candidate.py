"""Build and audit a retail-shaped mip fallback candidate for Mod City.

The active HostFS files are read-only inputs.  Outputs are written below the
requested candidate directory and must be installed separately after review.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import shutil
import struct
import sys
from pathlib import Path


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def tex0_shape(tex0: int) -> tuple[int, int, int]:
    return 1 << ((tex0 >> 26) & 0xF), 1 << ((tex0 >> 30) & 0xF), (tex0 >> 20) & 0x3F


def desired_mips(source_count: int) -> int:
    # The 56 one-level LA textures are intentional small/utility textures.
    # The structural mismatch is the 453 two-level chains: retail city
    # textures of those exact shapes use three levels.
    return 3 if source_count >= 2 else 1


def downsample_indices(indices: bytes, width: int, height: int,
                       palette: bytes) -> tuple[int, int, bytearray]:
    new_width, new_height = max(1, width // 2), max(1, height // 2)
    colors = len(palette) // 4
    entries = [tuple(palette[index * 4:index * 4 + 4]) for index in range(colors)]
    cache: dict[tuple[int, int, int, int], int] = {}
    output = bytearray(new_width * new_height)
    for y in range(new_height):
        for x in range(new_width):
            samples = []
            for dy in range(2):
                sy = min(height - 1, y * 2 + dy)
                for dx in range(2):
                    sx = min(width - 1, x * 2 + dx)
                    samples.append(entries[indices[sy * width + sx]])
            target = tuple(round(sum(sample[channel] for sample in samples) / len(samples))
                           for channel in range(4))
            match = cache.get(target)
            if match is None:
                tr, tg, tb, ta = target
                best_distance = None
                match = 0
                for index, (red, green, blue, alpha) in enumerate(entries):
                    distance = ((tr - red) ** 2 + (tg - green) ** 2
                                + (tb - blue) ** 2 + 4 * (ta - alpha) ** 2)
                    if best_distance is None or distance < best_distance:
                        best_distance, match = distance, index
                cache[target] = match
            output[y * new_width + x] = match
    return new_width, new_height, output


def make_sources(texmod, source_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    output_dir.mkdir(parents=True)
    counts_before = collections.Counter()
    counts_after = collections.Counter()
    generated = 0
    for path in sorted(source_dir.glob("*.tex")):
        texture = texmod.read_tex(str(path))
        counts_before[texture.mipmaps] += 1
        target = desired_mips(texture.mipmaps)
        levels = [(width, height, bytearray(payload))
                  for width, height, payload in texture.levels[:target]]
        while len(levels) < target:
            width, height, payload = levels[-1]
            indices = (texmod.unpack_4bpp(payload, width * height)
                       if texture.bpp == 4 else bytearray(payload))
            new_width, new_height, new_indices = downsample_indices(
                indices, width, height, texture.palette)
            packed = (texmod.pack_4bpp(new_indices, new_width * new_height)
                      if texture.bpp == 4 else new_indices)
            levels.append((new_width, new_height, bytearray(packed)))
            generated += 1
        rebuilt = texmod.TexFile(texture.width, texture.height, texture.fmt,
                                 target, texture.f8, texture.f10, texture.f12,
                                 bytearray(texture.palette), levels)
        texmod.write_tex(str(output_dir / path.name), rebuilt)
        reread = texmod.read_tex(str(output_dir / path.name))
        if reread.mipmaps != target or len(reread.levels) != target:
            raise AssertionError(f"source round-trip failed: {path.name}")
        counts_after[target] += 1
    if sum(counts_after.values()) != 510:
        raise AssertionError(f"expected 510 textures, got {sum(counts_after.values())}")
    return {"counts_before": dict(sorted(counts_before.items())),
            "counts_after": dict(sorted(counts_after.items())),
            "generated_levels": generated}


def pck_offset(data: bytes, pointer: int, need: int = 4) -> int:
    offset = pointer - u32(data, 0) + 0x80
    if not 0x80 <= offset <= len(data) - need:
        raise ValueError(f"pointer outside PCK: {pointer:08X}")
    return offset


def donor_objects(path: Path):
    data = path.read_bytes()
    seen: set[int] = set()
    group_count = u32(data, 0x88)
    group_array = u32(data, 0x8C)
    for group in range(group_count):
        descriptor = pck_offset(data, group_array + 16 * group, 16)
        slot_count = u16(data, descriptor + 8)
        slots = pck_offset(data, u32(data, descriptor + 4), slot_count * 4) if slot_count else 0
        for slot in range(slot_count):
            shader_pointer = u32(data, slots + 4 * slot)
            if not shader_pointer:
                continue
            shader = pck_offset(data, shader_pointer, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_pointer = u32(data, shader + 8)
            if not texture_pointer or texture_pointer in seen:
                continue
            seen.add(texture_pointer)
            texture = pck_offset(data, texture_pointer, 0xA0)
            if data[texture + 4] != 0:
                continue
            body = bytes(data[texture:texture + 0xA0])
            yield (*tex0_shape(u64(body, 0x10)), body[0x86]), body, texture


def inventory_donors(paths: list[Path]):
    donors = {}
    counts = collections.Counter()
    provenance = {}
    for path in paths:
        for key, body, offset in donor_objects(path):
            counts[key] += 1
            if key not in donors:
                donors[key] = body
                provenance[key] = {"pck": str(path), "offset": offset}
    return donors, counts, provenance


def descriptor_audit(descriptor: bytes, level: dict) -> None:
    if len(descriptor) != 16:
        raise AssertionError("bad descriptor length")
    blocks, through = struct.unpack_from("<HH", descriptor, 4)
    if blocks != int(level["pixel_blocks"]):
        raise AssertionError(f"pixel blocks differ for {level['texture']} mip {level['level']}")
    if through != int(level["blocks_through_end"]):
        raise AssertionError(f"running blocks differ for {level['texture']} mip {level['level']}")
    if tex0_shape(u64(descriptor, 8)) != (int(level["width"]), int(level["height"]),
                                                  int(level["psm"])):
        raise AssertionError(f"TEX0 shape differs for {level['texture']} mip {level['level']}")


def attach_donors(manifest_path: Path, old_manifest_path: Path,
                  donors, donor_counts, provenance) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    coverage = collections.Counter()
    preserved = expanded = 0
    for texture, old_texture in zip(manifest["textures"], old_manifest["textures"]):
        target = desired_mips(int(texture["mip_count"]))
        old_count = int(old_texture["mip_count"])
        key = (int(texture["width"]), int(texture["height"]),
               int(texture["levels"][0]["psm"]), target)
        if old_count == target:
            if len(old_texture["levels"]) != target:
                raise AssertionError(f"bad old mip count for {texture['texture']}")
            for level, old_level in zip(texture["levels"], old_texture["levels"]):
                descriptor = bytes.fromhex(old_level["native_mip_descriptor_hex"])
                descriptor_audit(descriptor, level)
                level["native_mip_descriptor_hex"] = descriptor.hex()
            texture["retail_donor"] = {"preserved_active_template": True}
            preserved += 1
            continue
        body = donors.get(key)
        if body is None:
            raise AssertionError(f"no exact retail donor for {texture['texture']}: {key}")
        if len(texture["levels"]) != target:
            raise AssertionError(f"unexpected mip count for {texture['texture']}")
        for level in texture["levels"]:
            index = int(level["level"])
            descriptor = body[0x08 + 16 * index:0x18 + 16 * index]
            descriptor_audit(descriptor, level)
            level["native_mip_descriptor_hex"] = descriptor.hex()
        texture["native_clut_offset_qwords"] = u16(body, 0x80)
        texture["native_clut256"] = body[0x84]
        texture["retail_donor"] = {**provenance[key], "shape": list(key),
                                     "available": donor_counts[key]}
        coverage[key] += 1
        expanded += 1
    manifest["status"] = "Mip fallback candidate with exact retail TexturePS2 descriptors."
    manifest["mip_policy"] = "Preserve mip1; expand every mip2/mip3 chain to exactly mip3."
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return {"textures": len(manifest["textures"]), "expanded_with_exact_retail_donor": expanded,
            "preserved_existing_templates": preserved, "shapes_used": len(coverage)}


def encode_native_payloads(ppf_path: Path, manifest_path: Path,
                           source_dir: Path, codec) -> str:
    """Replace generic encoder output with the audited retail ELF codec."""
    data = bytearray(ppf_path.read_bytes())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for texture in manifest["textures"]:
        source = (source_dir / texture["texture"]).read_bytes()
        width, height, source_format, mip_count, *_ = struct.unpack_from("<7H", source)
        bpp = 8 if source_format in (1, 14) else 4
        color_count = 256 if bpp == 8 else 16
        native_palette = codec.palette(source[14:14 + 4 * color_count], bpp)
        source_at = 14 + 4 * color_count
        if mip_count != len(texture["levels"]):
            raise AssertionError(f"native source count differs: {texture['texture']}")
        for level in texture["levels"]:
            index = int(level["level"])
            mip_width, mip_height = max(1, width >> index), max(1, height >> index)
            size = mip_width * mip_height * bpp // 8
            payload, upload = codec.pixels(
                source[source_at:source_at + size], mip_width, mip_height, bpp)
            source_at += size
            page_entry = u32(data, 12 + 4 * int(level["page"]))
            chunk = (page_entry & 0x7FFFF) * 0x800 + int(level["page_offset"])
            target = chunk + int(level["pixels_offset_in_chunk"])
            if len(payload) != int(level["pixel_bytes"]):
                raise AssertionError(f"native payload size differs: {texture['texture']} mip {index}")
            data[target:target + len(payload)] = payload
            level["upload_width"], level["upload_height"], level["upload_psm"] = upload
            if level["last_mip"]:
                palette_at = target + len(payload)
                if len(native_palette) != int(level["palette_bytes"]):
                    raise AssertionError(f"native palette size differs: {texture['texture']}")
                data[palette_at:palette_at + len(native_palette)] = native_palette
        if source_at != len(source):
            raise AssertionError(f"native source extent differs: {texture['texture']}")
    digest = sha256(data)
    ppf_path.write_bytes(data)
    manifest["sha256"] = digest.lower()
    manifest["encoder"] = "native_texture_codec.py; derived from retail ELF and corpus-verified"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return digest


def current_texture_offsets(data: bytes, old_manifest: dict) -> list[int]:
    # Slot order is deliberately free to vary between the nine PCKs.  Resolve
    # identity from the already-audited unique streamed chunk owned by each
    # TexturePS2, exactly as the independent route auditor does.
    ref_to_index = {}
    for index, texture in enumerate(old_manifest["textures"]):
        for level in texture["levels"]:
            key = (int(level["page_offset"]), int(level["page_word"]))
            if key in ref_to_index:
                raise AssertionError(f"duplicate old manifest chunk: {key}")
            ref_to_index[key] = index
    descriptors = []
    group_array = u32(data, 0x8C)
    for group in range(u32(data, 0x88)):
        descriptors.append(pck_offset(data, group_array + 16 * group, 16))
    if u32(data, 0x90):
        descriptors.append(pck_offset(data, u32(data, 0x90), 16))
    found = {}
    seen = set()
    for descriptor in descriptors:
        slot_count = u16(data, descriptor + 8)
        slots = pck_offset(data, u32(data, descriptor + 4), slot_count * 4) if slot_count else 0
        for slot in range(slot_count):
            shader_pointer = u32(data, slots + 4 * slot)
            if not shader_pointer:
                continue
            shader = pck_offset(data, shader_pointer, 12)
            if u32(data, shader + 4) & 0x7F:
                continue
            texture_pointer = u32(data, shader + 8)
            if not texture_pointer or texture_pointer in seen:
                continue
            seen.add(texture_pointer)
            texture = pck_offset(data, texture_pointer, 0xA0)
            if data[texture + 4] != 0:
                continue
            identities = set()
            for level in range(data[texture + 0x86]):
                pointer, page_offset, page_word = struct.unpack_from(
                    "<III", data, texture + 0x48 + 12 * level)
                if pointer == 0 and page_word >> 23:
                    identity = ref_to_index.get((page_offset, page_word))
                    if identity is not None:
                        identities.add(identity)
            if len(identities) == 1:
                identity = identities.pop()
                if identity in found:
                    raise AssertionError(f"texture {identity} has multiple PCK owners")
                found[identity] = texture
            elif identities:
                raise AssertionError(f"one TexturePS2 maps to multiple identities: {identities}")
    expected = len(old_manifest["textures"])
    missing = sorted(set(range(expected)) - set(found))
    if missing:
        raise AssertionError(f"missing {len(missing)} texture identities; first={missing[:10]}")
    return [found[index] for index in range(expected)]


def verify_old_route(data: bytes, offsets: list[int], old_manifest: dict) -> None:
    for texture, offset in zip(old_manifest["textures"], offsets):
        levels = texture["levels"]
        if data[offset + 0x86] != len(levels):
            raise AssertionError(f"old mip count differs: {texture['texture']}")
        for level in levels:
            index = int(level["level"])
            descriptor = data[offset + 0x08 + 16 * index:offset + 0x18 + 16 * index]
            if descriptor.hex() != level["native_mip_descriptor_hex"]:
                raise AssertionError(f"old descriptor differs: {texture['texture']} mip {index}")
            ref = struct.unpack_from("<III", data, offset + 0x48 + 12 * index)
            if ref != (0, int(level["page_offset"]), int(level["page_word"])):
                raise AssertionError(f"old datChunkRef differs: {texture['texture']} mip {index}")


def patch_pck(source: Path, output: Path, old_manifest: dict, new_manifest: dict,
              donors) -> dict:
    original = source.read_bytes()
    data = bytearray(original)
    offsets = current_texture_offsets(data, old_manifest)
    verify_old_route(data, offsets, old_manifest)
    allowed = set()
    for texture, old_texture, offset in zip(new_manifest["textures"],
                                             old_manifest["textures"], offsets):
        target = len(texture["levels"])
        if int(old_texture["mip_count"]) < target:
            key = (int(texture["width"]), int(texture["height"]),
                   int(texture["levels"][0]["psm"]), target)
            body = donors[key]
            data[offset + 0x08:offset + 0x48] = body[0x08:0x48]
            allowed.update(range(offset + 0x08, offset + 0x48))
        for index in range(4):
            at = offset + 0x48 + 12 * index
            if index < target:
                level = texture["levels"][index]
                struct.pack_into("<III", data, at, 0, int(level["page_offset"]),
                                 int(level["page_word"]))
            else:
                struct.pack_into("<III", data, at, 0, 0, 0)
            allowed.update(range(at, at + 12))
        # Size/CLUT allocator fields come from the exact retail shape.  TEX1
        # cannot be copied wholesale: MC3 stores its texture-proxy ID in bits
        # that are unused by the GS.  A donor-city ID sends Bind through an
        # unrelated or null proxy.  Preserve every source TEX1 bit and change
        # only GS MXL (bits 2..4) from old_count-1 to target-1.
        # Preserve +0x85 (the source clamp mode) and +0x7C (proxy/flags identity).
        if int(old_texture["mip_count"]) < target:
            data[offset + 0x80:offset + 0x85] = body[0x80:0x85]
            data[offset + 0x86] = target
            source_tex1 = u64(original, offset + 0x88)
            target_tex1 = (source_tex1 & ~0x1C) | ((target - 1) << 2)
            struct.pack_into("<Q", data, offset + 0x88, target_tex1)
            allowed.update(range(offset + 0x80, offset + 0x85))
            allowed.add(offset + 0x86)
            allowed.update(range(offset + 0x88, offset + 0x90))

    changed = [index for index, (before, after) in enumerate(zip(original, data))
               if before != after]
    forbidden = [index for index in changed if index not in allowed]
    if forbidden:
        raise AssertionError(f"{source.name}: {len(forbidden)} forbidden byte changes")
    # Re-open the completed route and require descriptor/ref identity.
    for texture, offset in zip(new_manifest["textures"], offsets):
        if data[offset + 0x86] != len(texture["levels"]):
            raise AssertionError(f"new mip count differs: {texture['texture']}")
        for level in texture["levels"]:
            index = int(level["level"])
            descriptor = data[offset + 0x08 + 16 * index:offset + 0x18 + 16 * index]
            if descriptor.hex() != level["native_mip_descriptor_hex"]:
                raise AssertionError(f"new descriptor differs: {texture['texture']} mip {index}")
            ref = struct.unpack_from("<III", data, offset + 0x48 + 12 * index)
            if ref != (0, int(level["page_offset"]), int(level["page_word"])):
                raise AssertionError(f"new datChunkRef differs: {texture['texture']} mip {index}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.write_bytes(data)
    return {"file": source.name, "size": len(data), "changed_bytes": len(changed),
            "before_sha256": sha256(original), "after_sha256": sha256(data),
            "only_texture_fields_changed": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-textures", type=Path, required=True)
    parser.add_argument("--mc3-tex", type=Path, required=True)
    parser.add_argument("--ppf-builder", type=Path, required=True)
    parser.add_argument("--ppf-repacker", type=Path, required=True)
    parser.add_argument("--native-codec", type=Path, required=True)
    parser.add_argument("--model-map", type=Path, required=True)
    parser.add_argument("--place", type=Path, required=True)
    parser.add_argument("--old-manifest", type=Path, required=True)
    parser.add_argument("--active-pck-dir", type=Path, required=True)
    parser.add_argument("--donor-pck", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    texmod = load_module("mc3_tex_mip_fallback", args.mc3_tex)
    builder = load_module("mc3_ppf_builder_mip_fallback", args.ppf_builder)
    repacker = load_module("mc3_ppf_repacker_mip_fallback", args.ppf_repacker)
    codec = load_module("mc3_native_codec_mip_fallback", args.native_codec)
    source_report = make_sources(texmod, args.source_textures,
                                 args.output_dir / "source_textures")
    donors, donor_counts, provenance = inventory_donors(args.donor_pck)

    dense_ppf = args.output_dir / "dense" / "losangeles_midnight_clear.ppf"
    dense_ppf.parent.mkdir()
    dense_manifest = dense_ppf.with_suffix(".manifest.json")
    dense_refs = dense_ppf.with_suffix(".refs.tsv")
    summary = builder.build(texmod, args.output_dir / "source_textures", dense_ppf,
                            dense_refs, dense_manifest)
    summary["sha256"] = encode_native_payloads(
        dense_ppf, dense_manifest, args.output_dir / "source_textures", codec)
    donor_report = attach_donors(dense_manifest, args.old_manifest,
                                  donors, donor_counts, provenance)

    final_ppf = args.output_dir / "candidate" / "city" / "losangeles_midnight_clear.ppf"
    final_ppf.parent.mkdir(parents=True)
    packing_report = repacker.repack(dense_ppf, dense_manifest, args.model_map,
                                     args.place, final_ppf)
    final_manifest_path = final_ppf.with_suffix(".manifest.json")
    final_manifest = json.loads(final_manifest_path.read_text(encoding="utf-8"))
    old_manifest = json.loads(args.old_manifest.read_text(encoding="utf-8"))

    pck_reports = []
    names = [f"losangeles_{time}_{weather}.pck" for time in
             ("dawn", "dusk", "midnight") for weather in
             ("clear", "cloudy", "rainy")]
    for name in names:
        pck_reports.append(patch_pck(args.active_pck_dir / name,
                                     final_ppf.parent / name, old_manifest,
                                     final_manifest, donors))

    report = {"status": "PASS", "source": source_report,
              "donors": donor_report,
              "dense": {"pages": summary["page_count"],
                        "chunks": summary["mip_chunk_count"],
                        "sha256": summary["sha256"]},
              "packing": packing_report,
              "final_ppf": {"path": str(final_ppf),
                            "size": final_ppf.stat().st_size,
                            "sha256": sha256(final_ppf.read_bytes())},
              "pcks": pck_reports}
    (args.output_dir / "audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "source": source_report,
                      "donors": donor_report, "dense": report["dense"],
                      "packing": packing_report,
                      "final_ppf": report["final_ppf"],
                      "pcks": len(pck_reports)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
