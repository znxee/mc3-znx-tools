"""
mc3_traffic_mlod.py - put one vp_* vehicle's LOD in a traffic slot.

The vp container stores an LOD as several bone-attached pieces. A traffic car
has one body slot plus separate generic wheels and shadow. This tool:

1. reads every embedded piece of the requested vp LOD;
2. optionally adds the stock front/rear bumpers, side skirts, hood, tail
   lights and spoiler referenced by the HLOD table;
3. bakes each piece's skeleton anchor into its vertices;
4. merges the pieces into one body mesh;
5. re-encodes the packets in retail traffic style (98000260);
6. by default remaps the body to the target traffic car's existing body and
   emissive shaders, without importing player-car customization resources;
7. appends the body and repoints only the target vehicle's vroot slot.

The target's physics, tuning, audio, wheels, shadow, directory entry and name
remain untouched.  The default ``traffic-stock`` profile omits the separate
decal mesh and imports zero donor shaders/textures.  ``--shader-profile full``
retains the old full transplant for analysis.  ``--material N`` remains the
diagnostic mode that forces every group onto one existing traffic shader.
``--uv-profile civic-share`` collapses each donor material onto a safe semantic
swatch of the target Civic's single 128x64 ``va_civic_share`` atlas.

Example:
  python mc3_traffic_mlod.py atlanta_traffic.pck vp_350z_04.pck \
      --vehicle va_civic_sh --lod MLOD --stock --out test.pck
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import struct
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name,
                                                  os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


embed = _sibling("mc3_pck_embed")
convert = _sibling("mc3_mesh_convert")
traffic = _sibling("mc3_traffic")
shader_transplant = _sibling("mc3_shader_transplant")


# Stock customization slots live in HLOD even when the shell comes from MLOD.
# Their names are consistent across the vehicle packages; the prefix before
# `_stk_` names the customization category.
STOCK_PATTERNS = (
    ("front_bumper", re.compile(r"(?:bumpf|bumf)_stk_", re.I)),
    ("rear_bumper", re.compile(r"bumr_stk_", re.I)),
    ("side_skirts", re.compile(r"(?:^|_)ss_stk_", re.I)),
    ("hood", re.compile(r"(?:^|_)hd_stk_", re.I)),
    ("tail_lights", re.compile(r"(?:^|_)tl_stk_", re.I)),
    ("spoiler", re.compile(r"(?:^|_)splr_stk_", re.I)),
)


# The retail traffic Civic uses one 128x64 texture for the whole body.  Player
# cars do not: paint is colored by shaders, while glass, rubber, chrome and
# lights have separate material paths.  Reusing those UVs with ambient_body
# therefore samples unrelated pieces of the Civic atlas.  These coordinates
# are deliberately in the interior of representative retail pixels, never on
# an island edge.  They make a stable one-texture traffic approximation: the
# silhouette and material separation survive, while decals/vinyl stay absent.
CIVIC_SHARE_SIZE = (128, 64)
CIVIC_SHARE_DEFAULT = (16, 17)       # silver body paint
CIVIC_SHARE_SWATCHES = {
    "black_matte.shadert": (8, 50),
    "masked_chrome.shadert": (118, 20),
    "car_decal.shadert": (16, 17),
    "carpaint.shadert": (16, 17),
    # Neutral near-black survives the traffic color randomizer.  The former
    # blue pixel (36,34) could be rotated into a brown/orange window at runtime.
    "colored_glass.shadert": (113, 2),
    "car_window.shadert": (113, 2),
    "rubber.shadert": (8, 50),
    "aa_trim.shadert": (8, 50),
    "emissive_taillight.shadert": (85, 34),
    "lit_textured.shadert": (110, 50),
    "default_shiny.shadert": (110, 34),
    "emissive_reverselight.shadert": (118, 20),
    "licenseplate.shadert": (8, 12),
    "emissive_brakelight.shadert": (85, 34),
    "chrome.shadert": (118, 20),
    "emissive_headlight.shadert": (118, 20),
}


def _civic_share_uv(shader_name):
    x, y = CIVIC_SHARE_SWATCHES.get(shader_name, CIVIC_SHARE_DEFAULT)
    width, height = CIVIC_SHARE_SIZE
    return ((x + 0.5) / width, (y + 0.5) / height)


def remap_uvs(mesh, donor_group, profile):
    """Apply one target-atlas preset while donor material indices still exist."""
    if profile == "preserve":
        return []
    if profile != "civic-share":
        raise ValueError("unknown UV profile %r" % profile)

    report = {}
    for group in mesh.groups:
        donor_index = group["material"]
        entry = donor_group.entries[donor_index]
        if entry is None:
            raise ValueError("donor shader %d is unavailable for UV remap" %
                             donor_index)
        uv = _civic_share_uv(entry.name)
        item = report.setdefault(
            donor_index,
            dict(donor_index=donor_index, name=entry.name, uv=uv,
                 groups=0, vertices=0))
        item["groups"] += 1
        for strip in group["strips"]:
            item["vertices"] += len(strip)
            for vertex in strip:
                vertex.uv = uv
    return [report[index] for index in sorted(report)]


def _standalone_piece(pck, mesh_va):
    """Make a temporary standalone mesh image from an embedded vp piece.

    The synthetic file starts at the piece's own VA, so every pointer inside
    the measured footprint remains valid.  Its material table is copied and
    repointed explicitly: two retail 350Z MLOD pieces keep that table in a
    shared container region outside the mesh footprint, and falling back to a
    packet scan would collapse two real material groups into one fake group 0.
    """
    fp = pck.mesh_footprint(mesh_va)
    if not fp:
        raise ValueError("slot points to something that is not a PS2 mesh")
    lo, hi = fp
    if lo != pck.F(mesh_va):
        raise ValueError("mesh footprint does not start at its root")
    group_count = pck.U(lo + 8)
    material_off = pck.F(pck.U(lo + 0x0C))
    main_off = pck.F(pck.U(lo + 0x10))
    if not (1 <= group_count <= 4096 and
            0 <= material_off <= len(pck.data) - 2 * group_count and
            lo <= main_off <= hi - 8 * group_count):
        raise ValueError("embedded mesh has an invalid group/material table")

    head = bytearray(pck.data[:0x80])
    struct.pack_into("<I", head, 0x00, mesh_va)
    struct.pack_into("<I", head, 0x04, 22)
    struct.pack_into("<I", head, 0x08, 1)
    blob = head + bytes(pck.data[lo:hi])
    while len(blob) % 16:
        blob += b"\xCD"
    new_material_off = len(blob)
    blob += bytes(pck.data[material_off:material_off + 2 * group_count])
    while len(blob) % 16:
        blob += b"\xCD"
    synthetic_base = mesh_va - 0x80
    struct.pack_into("<I", blob, 0x80 + 0x0C,
                     synthetic_base + new_material_off)
    struct.pack_into("<I", blob, 0x0C, len(blob) - 0x80)
    return bytes(blob)


def _read_piece(blob):
    fd, name = tempfile.mkstemp(prefix="mc3_mlod_piece_", suffix=".mesh.pck")
    os.close(fd)
    try:
        with open(name, "wb") as f:
            f.write(blob)
        return convert.read_pck(name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _stock_pieces(p, vp_path, tables, anchors):
    """Pieces selected by the stock entries of the HLOD table.

    Most are zero-pointer entries whose `<name>.pck` sits next to the main vp
    package. A vehicle is also allowed to embed one directly. Returns tuples in
    the same shape used by build_lod plus category/origin labels.
    """
    if "HLOD" not in tables:
        return []
    slots = p.slots(tables["HLOD"])
    folder = os.path.dirname(os.path.abspath(vp_path))
    out = []
    for category, pattern in STOCK_PATTERNS:
        matches = [slot for slot in slots if pattern.search(slot[2] or "")]
        if not matches:
            continue
        # There should be one stock item per category. Prefer an existing
        # external file, then an embedded pointer, to avoid choosing a stale
        # name in an unusual package.
        chosen = None
        chosen_path = None
        for slot in matches:
            candidate = os.path.join(folder, slot[2] + ".pck")
            if os.path.isfile(candidate):
                chosen, chosen_path = slot, candidate
                break
            if slot[1] and chosen is None:
                chosen = slot
        if chosen is None:
            continue
        index, mesh_va, name, slot_id, _slot_off = chosen
        if chosen_path:
            mesh = convert.read_pck(chosen_path)
            origin = os.path.basename(chosen_path)
        else:
            mesh = _read_piece(_standalone_piece(p, mesh_va))
            origin = "embedded HLOD"
        anchor = anchors.get(mesh.piece_id, (0.0, 0.0, 0.0))
        out.append((mesh, anchor, index, mesh_va, name, slot_id,
                    category, origin))
    return out


def build_lod(vp_path, lod="MLOD", material=None, stock=False,
              include_decals=True):
    """Return (merged_mesh, report) for one embedded PS2 vehicle LOD."""
    p = embed.Pck(vp_path)
    tables = p.lods()
    lod = lod.upper()
    if lod not in tables:
        raise ValueError("%s has no %s table (found: %s)" %
                         (os.path.basename(vp_path), lod,
                          ", ".join(tables) or "none"))

    anchors = {node[2]: node[6] for node in p.skel_nodes()}
    pieces = []
    report = []
    for index, mesh_va, name, slot_id, _slot in p.slots(tables[lod]):
        if not mesh_va:
            continue
        if not include_decals and "decal" in (name or "").casefold():
            continue
        mesh = _read_piece(_standalone_piece(p, mesh_va))
        anchor = anchors.get(mesh.piece_id, (0.0, 0.0, 0.0))
        pieces.append((mesh, anchor, index, mesh_va, name, slot_id,
                       lod.lower(), "embedded %s" % lod))

    if stock:
        pieces.extend(_stock_pieces(p, vp_path, tables, anchors))

    if not pieces:
        raise ValueError("%s contains no embedded mesh in %s" %
                         (os.path.basename(vp_path), lod))

    # Use the coarsest source scale: choosing a finer one can overflow s16 on
    # long cars. Every vertex is converted through world units, so mixed scales
    # remain in exactly the same place.
    out = convert.Mesh()
    out.source = "vp-%s" % lod.lower()
    out.piece_id = 0
    out.scale = max(mesh.scale for mesh, *_rest in pieces)

    for (mesh, anchor, index, mesh_va, name, slot_id,
        category, origin) in pieces:
        vertex_count = 0
        for group in mesh.groups:
            if material is not None:
                group["material"] = material
            for strip in group["strips"]:
                vertex_count += len(strip)
                for vertex in strip:
                    vertex.pos = tuple(round((vertex.pos[k] * mesh.scale + anchor[k])
                                             / out.scale) for k in range(3))
                    if any(v < -32768 or v > 32767 for v in vertex.pos):
                        raise ValueError("%s[%d] exceeds s16 after its bone anchor"
                                         % (lod, index))
            out.groups.append(group)
        report.append(dict(index=index, name=name, mesh_va=mesh_va,
                           piece_id=mesh.piece_id, slot_id=slot_id,
                           anchor=anchor, groups=len(mesh.groups),
                           vertices=vertex_count, category=category,
                           origin=origin))

    return out, report


def body_piece(tp, vehicle):
    hits = [pc for pc in tp.pieces()
            if pc["vehicle"].casefold() == vehicle.casefold()
            and pc["piece_id"] == 0]
    if len(hits) != 1:
        known = sorted({pc["vehicle"] for pc in tp.pieces()
                        if not pc["vehicle"].startswith("(")})
        raise ValueError("expected one vroot/body for %r, found %d; vehicles: %s"
                         % (vehicle, len(hits), ", ".join(known)))
    return hits[0]


def insert(traffic_path, vp_path, vehicle, out_path, lod="MLOD", material=None,
           stock=False, shader_profile="traffic-stock", uv_profile="preserve"):
    shader_profile = shader_profile.casefold()
    uv_profile = uv_profile.casefold()
    if shader_profile not in ("traffic-stock", "full"):
        raise ValueError("unknown shader profile %r" % shader_profile)
    if uv_profile not in ("preserve", "civic-share"):
        raise ValueError("unknown UV profile %r" % uv_profile)
    lean = material is None and shader_profile == "traffic-stock"
    if uv_profile != "preserve" and not lean:
        raise ValueError("UV presets require the traffic-stock shader profile")
    if uv_profile == "civic-share" and vehicle.casefold() != "va_civic_sh":
        raise ValueError("civic-share is only valid for target va_civic_sh")
    merged, donor = build_lod(
        vp_path, lod=lod, material=material, stock=stock,
        include_decals=not lean)
    tp = traffic.TrafficPck(traffic_path)
    target = body_piece(tp, vehicle)
    shader_result = None
    material_map = {}
    material_names = {}
    target_group_off = None
    target_shader_count = None
    uv_report = []

    if material is None:
        used = sorted({group["material"] for group in merged.groups})
        roots = [root for name, root in tp.roots()
                 if name.casefold() == vehicle.casefold()]
        if len(roots) != 1:
            raise ValueError("expected one root for %r, found %d" %
                             (vehicle, len(roots)))
        if shader_profile == "traffic-stock":
            target_view = shader_transplant.Pck(tp.pck)
            target_group_off = target_view.pointer(roots[0] + 0x08)
            if target_group_off is None:
                raise ValueError("target vehicle has no shader group")
            target_group = shader_transplant.read_shader_group(
                target_view, target_group_off)
            target_shader_count = target_group.size
            target_by_name = {entry.name: entry.index
                              for entry in target_group.entries if entry}
            body_shader = target_by_name.get("ambient_body.shadert")
            emissive_shader = target_by_name.get(
                "ambient_emissive_brakelight.shadert")
            if body_shader is None or emissive_shader is None:
                raise ValueError(
                    "traffic-stock requires ambient_body and "
                    "ambient_emissive_brakelight in the target group")

            donor_group = shader_transplant.find_shader_group(
                shader_transplant.Pck(vp_path))
            uv_report = remap_uvs(merged, donor_group, uv_profile)
            for donor_index in used:
                entry = donor_group.entries[donor_index]
                if entry is None:
                    raise ValueError("donor shader %d is unavailable" %
                                     donor_index)
                material_names[donor_index] = entry.name
                material_map[donor_index] = (
                    emissive_shader
                    if entry.name.startswith("emissive_") else body_shader)
        else:
            siblings = [piece for piece in tp.pieces()
                        if piece["vehicle"].casefold() == vehicle.casefold()
                        and piece["index"] != target["index"]]
            preserve = sorted({shader for piece in siblings
                               for shader in tp.materials(piece["off"])})
            shader_result = shader_transplant.transplant(
                tp.pck, vp_path, roots[0] + 0x08, used, preserve)
            material_map = shader_result.shader_map
            material_names = shader_result.shader_names
        for group in merged.groups:
            group["material"] = material_map[group["material"]]
    else:
        count = tp.shader_count()
        if count and not 0 <= material < count:
            raise ValueError("forced material %d outside traffic shader count %d" %
                             (material, count))

    fd, mesh_path = tempfile.mkstemp(prefix="mc3_mlod_merged_",
                                     suffix=".mesh.pck")
    os.close(fd)
    try:
        convert.write_pck(merged, mesh_path, block_style="traffic")
        if uv_report:
            # The PS2 packet stores UV in 1/4096 units.  Reopen the standalone
            # body before embedding it and prove every vertex landed on one of
            # the requested semantic swatches after that quantization.
            uv_check = convert.read_pck(mesh_path)
            source_stats = merged.stats()
            roundtrip_stats = uv_check.stats()
            if (roundtrip_stats[0] != source_stats[0] or
                    roundtrip_stats[3] != source_stats[3]):
                raise ValueError(
                    "self-check failed: UV repack changed group/triangle "
                    "topology (%r -> %r)" % (source_stats, roundtrip_stats))
            expected_uvs = {
                (round(item["uv"][0] * 4096) / 4096.0,
                 round(item["uv"][1] * 4096) / 4096.0)
                for item in uv_report
            }
            bad_uvs = [
                vertex.uv
                for group in uv_check.groups
                for strip in group["strips"]
                for vertex in strip
                if vertex.uv not in expected_uvs
            ]
            if bad_uvs:
                raise ValueError(
                    "self-check failed: %d UVs missed semantic swatches" %
                    len(bad_uvs))
        new_va, fixed, body_len = tp.replace(
            target["index"], mesh_path, material=material,
            translated_vtable=True)
    finally:
        try:
            os.unlink(mesh_path)
        except OSError:
            pass

    tp.save(out_path)

    # Reopen through the public reader. This catches body-size, pointer, vtable
    # and footprint mistakes before the file is handed to the console.
    check = traffic.TrafficPck(out_path)
    new_target = body_piece(check, vehicle)
    if new_target["off"] != check.pck.F(new_va):
        raise ValueError("self-check failed: target slot does not point at appended body")
    if struct.unpack_from("<I", check.data, new_target["off"])[0] != \
            traffic.RUNTIME_MESH_VTABLE:
        raise ValueError("self-check failed: appended mesh vtable is not translated")
    if len(check.data) % 16 or struct.unpack_from("<I", check.data, 0x0C)[0] != \
            len(check.data) - 0x80:
        raise ValueError("self-check failed: file size/header alignment")
    expected_materials = [group["material"] for group in merged.groups]
    actual_materials = check.materials(new_target["off"])
    if actual_materials != expected_materials:
        raise ValueError("self-check failed: per-group material table changed")
    if shader_result is not None:
        roots = [root for name, root in check.roots()
                 if name.casefold() == vehicle.casefold()]
        view = shader_transplant.Pck(check.pck)
        new_group_off = view.pointer(roots[0] + 0x08) if len(roots) == 1 else None
        if new_group_off is None:
            raise ValueError("self-check failed: vehicle lost its shader group")
        new_group = shader_transplant.read_shader_group(
            view, new_group_off, shader_result.shader_count)
        if max(actual_materials, default=-1) >= new_group.size:
            raise ValueError("self-check failed: material exceeds shader count")
        for old_index, new_index in shader_result.shader_map.items():
            entry = new_group.entries[new_index]
            if entry is None or entry.name != shader_result.shader_names[old_index]:
                raise ValueError("self-check failed: shader map does not round-trip")
        if shader_result.clone.external_texture_fields:
            fallback_va = view.virtual(
                shader_result.clone.fallback_texture_off)
            for field in shader_result.clone.external_texture_fields:
                if view.u32(field) != fallback_va:
                    raise ValueError(
                        "self-check failed: raw external texture reference at "
                        "%#x" % field)
    elif lean:
        roots = [root for name, root in check.roots()
                 if name.casefold() == vehicle.casefold()]
        view = shader_transplant.Pck(check.pck)
        new_group_off = view.pointer(roots[0] + 0x08) if len(roots) == 1 else None
        if new_group_off != target_group_off:
            raise ValueError(
                "self-check failed: traffic-stock changed target shader group")
        if max(actual_materials, default=-1) >= target_shader_count:
            raise ValueError(
                "self-check failed: traffic-stock material exceeds target group")

    return dict(target=target, donor=donor, merged_stats=merged.stats(),
                scale=merged.scale, new_va=new_va, pointers_fixed=fixed,
                appended_bytes=body_len, output_bytes=len(check.data),
                new_piece=new_target, stock=stock,
                shader_transplant=shader_result,
                shader_profile=("forced" if material is not None
                                else shader_profile),
                uv_profile=uv_profile, uv_report=uv_report,
                material_map=material_map, material_names=material_names,
                materials=actual_materials)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("traffic", help="retail/copy *_traffic.pck")
    ap.add_argument("vp", help="main vp_*.pck (not *_g or *_o)")
    ap.add_argument("--vehicle", required=True,
                    help="directory name, e.g. va_civic_sh")
    ap.add_argument("--lod", default="MLOD", choices=("HLOD", "MLOD", "LLOD"))
    ap.add_argument("--stock", action="store_true",
                    help="add stock bumpers, skirts, hood, lights and spoiler")
    ap.add_argument("--shader-profile", default="traffic-stock",
                    choices=("traffic-stock", "full"),
                    help="traffic-stock reuses only target body/emissive "
                         "shaders (default); full imports donor materials")
    ap.add_argument("--material", type=lambda s: int(s, 0), default=None,
                    help="diagnostic: force one existing traffic shader; "
                         "overrides --shader-profile")
    ap.add_argument("--uv-profile", default="preserve",
                    choices=("preserve", "civic-share"),
                    help="preserve keeps donor UVs; civic-share maps material "
                         "roles to safe swatches in va_civic_share.tex")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)

    result = insert(ns.traffic, ns.vp, ns.vehicle, ns.out,
                    lod=ns.lod, material=ns.material, stock=ns.stock,
                    shader_profile=ns.shader_profile,
                    uv_profile=ns.uv_profile)
    print("%s <- %s %s" % (ns.vehicle, os.path.basename(ns.vp), ns.lod))
    for item in result["donor"]:
        print("  [%d] id=%d %-14s %d group(s), %d vertices  %s  (%s)" %
              (item["index"], item["piece_id"], item["category"],
               item["groups"], item["vertices"], item["name"],
               item["origin"]))
    ng, ns_, nv, nt = result["merged_stats"]
    print("merged: %d groups, %d strips, %d vertices, %d triangles, scale %.10g" %
          (ng, ns_, nv, nt, result["scale"]))
    print("appended: %d bytes at VA %#x; %d internal pointers rebased" %
          (result["appended_bytes"], result["new_va"],
           result["pointers_fixed"]))
    shaders = result["shader_transplant"]
    if result["shader_profile"] == "traffic-stock":
        print("shader profile: traffic-stock; 0 donor shaders/textures; "
              "target retail group unchanged")
        for old_index, new_index in sorted(result["material_map"].items()):
            print("  vp[%d] %-34s -> traffic[%d]" %
                  (old_index, result["material_names"][old_index], new_index))
    elif shaders is not None:
        print("shader transplant: %d selected, %d preserved, %d segments, "
              "%d bytes, %d pointers rebased, %d inline texture(s) / %d bytes" %
              (len(shaders.shader_map), len(shaders.preserved_indices),
               shaders.clone.segment_count, shaders.clone.appended_bytes,
               shaders.clone.pointers_rebased, shaders.clone.texture_count,
               shaders.clone.texture_bytes))
        if shaders.clone.external_texture_fields:
            print("  external texture fallbacks: %d reference(s), %d name(s) "
                  "-> target neutral texture @%#x" %
                  (len(shaders.clone.external_texture_fields),
                   len(shaders.clone.external_texture_names),
                   shaders.clone.fallback_texture_off))
            print("    " + ", ".join(shaders.clone.external_texture_names))
        for old_index, new_index in sorted(shaders.shader_map.items()):
            print("  vp[%d] -> traffic[%d]  %s" %
                  (old_index, new_index, shaders.shader_names[old_index]))
    if result["uv_report"]:
        print("UV profile: %s; %d semantic swatches" %
              (result["uv_profile"], len(result["uv_report"])))
        for item in result["uv_report"]:
            print("  vp[%d] %-34s -> UV %.6f,%.6f  "
                  "(%d group(s), %d vertices)" %
                  (item["donor_index"], item["name"],
                   item["uv"][0], item["uv"][1], item["groups"],
                   item["vertices"]))
    print("saved: %s (%d bytes), self-check passed" %
          (ns.out, result["output_bytes"]))


if __name__ == "__main__":
    main()
