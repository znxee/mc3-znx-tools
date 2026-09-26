"""Port the stock visual model of an MC:LA PSP car into an MC3 PS2 donor.

The PS2 donor keeps the runtime-only contracts (vehicle type, performance,
audio, shaders, customization tables and collision). Geometry is decoded from
MC:LA ``.mesh.psppck`` files, rebuilt as PS2 VIF packets, remapped to donor
materials by shader-template name and attached to donor skeleton IDs.

The first profile is the 2007 Corvette Z06 -> 2003 Corvette Z06 requested for
the toolkit. It replaces every visible stock HLOD piece, supplies source-based
MLOD/LLOD meshes, moves wheel/effect anchors, and emits both main and ``_o``.

Usage:
    python mc3_mcla_vehicle_port.py SOURCE_DIR DONOR_DIR OUT_DIR
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
import pathlib
import shutil
import struct
import sys
import zlib

from mc3_mesh_convert import Mesh, Vtx, load, save, tris_of_strip
from mc3_pck_embed import Pck
from mc3_shadinggroup import extract_shadinggroup
from mc3_vehicle_texture_port import port_native_texture


PROFILE_SOURCE = "vp_chv_corvette_z06_07"
PROFILE_DONOR = "vp_corvettez06_03"

# Target skeleton name -> source skeleton name. Both anchor vectors are rebuilt;
# anchor2 is always world(target)-world(parent) in all 46 donor nodes.
ANCHOR_MAP = {
    "Arm_Front_L": "Suspension_Arm_Front_L",
    "Arm_Front_R": "Suspension_Arm_Front_R",
    "Arm_Rear_L": "Suspension_Arm_Rear_L",
    "Arm_Rear_R": "Suspension_Arm_Rear_R",
    "axl00": "axl_0", "whl00": "whl_0",
    "axl01": "axl_1", "whl01": "whl_1",
    "axl02": "axl_2", "whl02": "whl_2",
    "axl03": "axl_3", "whl03": "whl_3",
    "bmpf": "bumf_stk_corvettez06_07",
    "bmpr": "bumr_stk_corvettez06_07",
    "hd": "hd",
    "trunk": "tk",
    "ss": "ss_stk_corvettez06_07",
    # The C6 lamp mesh is authored in root coordinates. Keep this attachment at
    # zero and disable the C5 donor's 40-degree pop-up animation separately.
    "headlight_R_bne": "tl_stk_corvettez06_07",
    "ext0": "exst0", "ext1": "exst1",
    "hl_lo_0": "hl_low_0", "hl_lo_1": "hl_low_1",
    "hl_hi_0": "hl_hi_0", "hl_hi_1": "hl_hi_1",
    "sst0": "ss_0", "sst1": "ss_1",
    "engsmoke0": "engine_smk_0", "engsmoke1": "engine_smk_1",
    "rev_00": "rev_0", "rev_01": "rev_1",
    "brake_00": "brake_0", "brake_01": "tail_0",
    "brake_02": "tail_1", "brake_03": "brake_1",
    "turn_00": "amber_0", "turn_01": "amber_1",
    "headlight_cone": "hl_cone",
}


def clone_vertex(v: Vtx) -> Vtx:
    return Vtx(tuple(v.pos), tuple(v.uv), tuple(v.nrm), v.skip,
               copy.deepcopy(v.extra))


def clone_mesh(mesh: Mesh) -> Mesh:
    out = Mesh()
    out.piece_id = mesh.piece_id
    out.scale = mesh.scale
    out.flavor = mesh.flavor
    out.virtual_addr = mesh.virtual_addr
    out.source = mesh.source
    for group in mesh.groups:
        out.groups.append({
            "material": group["material"],
            "strips": [[clone_vertex(v) for v in strip]
                       for strip in group["strips"]],
        })
    return out


def read_embedded_psp(pck: Pck, mesh_va: int) -> Mesh:
    """Decode one PSP mesh embedded in a main psppck container."""
    d = pck.data
    o = pck.F(mesh_va)
    if not pck.is_psp_mesh(o):
        raise ValueError("slot does not point to an embedded PSP mesh")
    mesh_list = pck.F(pck.U(o + 0x04))
    materials = pck.F(pck.U(o + 0x08))
    vertices = pck.F(pck.U(o + 0x10))
    out = Mesh()
    out.piece_id = d[o + 0x19]
    out.scale = 1.0 / 4096.0
    out.flavor = "mcla"
    out.source = "embedded psppck:mcla"
    for group_index in range(d[o + 0x1B]):
        entry = mesh_list + 0x0C * group_index
        sub_table = pck.F(pck.U(entry))
        sub_count = pck.U16(entry + 4)
        ranges = [(pck.U16(sub_table + 4*i), pck.U16(sub_table + 4*i + 2))
                  for i in range(sub_count)]
        if not ranges:
            continue
        first = min(start for start, _count in ranges)
        end = max(start + count for start, count in ranges)
        strip = []
        for index in range(first, end):
            q = vertices + 14 * index
            ru, rv = struct.unpack_from("<hh", d, q)
            nrm = tuple((x - 256 if x >= 128 else x) / 127.0
                        for x in d[q+4:q+7])
            pos = struct.unpack_from("<hhh", d, q + 8)
            strip.append(Vtx(tuple(pos),
                             ((ru + 32768) / 32767.0,
                              (rv + 32768) / 32767.0),
                             nrm, False))
        out.groups.append({"material": pck.U16(materials + 2*group_index),
                           "strips": [strip]})
    return out


def find_vehicle_dir(root: pathlib.Path, name: str) -> pathlib.Path:
    def owns_main(path):
        return any((path / f"{name}{suffix}").is_file()
                   for suffix in (".pck", ".psppck"))

    candidates = ([root] if root.name.lower() == name.lower() else []) + [
        p for p in root.rglob(name) if p.is_dir()
    ]
    matches = [p for p in candidates if owns_main(p)]
    if len(matches) != 1:
        raise ValueError(f"expected one {name} directory below {root}, got {len(matches)}")
    return matches[0]


def source_files(source_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    files = list(source_dir.glob("*.mesh.psppck"))

    def one(label, predicate):
        matches = [p for p in files if predicate(p.name.lower())]
        if len(matches) != 1:
            raise ValueError(f"source role {label}: expected one mesh, got {len(matches)}")
        return matches[0]

    not_wide = lambda n: "_wide_" not in n
    return {
        "front": one("front", lambda n: "bumpf_bumf_stk" in n),
        "rear": one("rear", lambda n: "bumpr_bumr_stk" in n),
        "hood": one("hood", lambda n: "_hd_hd_stk_" in n),
        "side": one("side", lambda n: "_ss_ss_stk_" in n),
        # The parent node of both lamp meshes contains ``tl_tl_stk``.  Match
        # the final geometry component instead, otherwise the headlight also
        # satisfies the generic tail-light predicate.
        "head": one("head", lambda n: n.endswith(
            "hl_stk_corvettez06_07_geo.mesh.psppck")),
        "tail": one("tail", lambda n: n.endswith(
            "tl_stk_corvettez06_07_geo.mesh.psppck")),
        "body": one("body", lambda n: not_wide(n) and "hlod_hlod_group_body" in n),
        "interior": one("interior", lambda n: not_wide(n) and "hlod_hlod_group_int" in n),
        "window": one("window", lambda n: not_wide(n) and "hlod_hlod_group_window" in n),
        "decals": one("decals", lambda n: not_wide(n) and "hlod_hlod_group_decals_h" in n),
    }


def shader_remap(source_main: pathlib.Path, donor_main: pathlib.Path):
    _sb, source = extract_shadinggroup(str(source_main))
    _db, donor = extract_shadinggroup(str(donor_main))
    if not source or not donor:
        raise ValueError("could not read one of the shading groups")
    by_name = {}
    for index, name in enumerate(donor):
        by_name.setdefault(name.lower(), []).append(index)
    seen = {}
    remap = {}
    for source_index, name in enumerate(source):
        key = name.lower()
        rank = seen.get(key, 0)
        seen[key] = rank + 1
        choices = by_name.get(key)
        if not choices and key == "carpaint_novinyl.shadert":
            choices = by_name.get("carpaint.shadert")
            rank = 1
        if not choices:
            raise ValueError(f"donor has no shader equivalent for source {source_index}: {name}")
        remap[source_index] = choices[min(rank, len(choices) - 1)]
    return source, donor, remap


def retarget(mesh: Mesh, target_id: int, material_map: dict[int, int],
             overrides: dict[int, int] | None = None,
             delta=(0.0, 0.0, 0.0)) -> Mesh:
    out = clone_mesh(mesh)
    out.piece_id = target_id
    raw_delta = tuple(round(x / out.scale) for x in delta)
    for group in out.groups:
        old = group["material"]
        group["material"] = (overrides or {}).get(old, material_map[old])
        if any(raw_delta):
            for strip in group["strips"]:
                for v in strip:
                    v.pos = tuple(max(-32768, min(32767, v.pos[k] + raw_delta[k]))
                                  for k in range(3))
    return out


def split_x(mesh: Mesh) -> tuple[Mesh, Mesh]:
    """Split a symmetric lamp mesh into donor left/right slots by triangle centroid."""
    sides = (Mesh(), Mesh())
    for out in sides:
        out.piece_id = mesh.piece_id
        out.scale = mesh.scale
        out.source = mesh.source + ":split"
    for group in mesh.groups:
        strips = ([], [])
        for strip in group["strips"]:
            for a, b, c in tris_of_strip(strip):
                tri = [strip[a], strip[b], strip[c]]
                side = 0 if sum(v.pos[0] for v in tri) < 0 else 1
                strips[side].append([clone_vertex(v) for v in tri])
        for side, out in enumerate(sides):
            if strips[side]:
                out.groups.append({"material": group["material"],
                                   "strips": strips[side]})
    return sides


def combine(meshes: list[Mesh], piece_id: int, merge_materials=True) -> Mesh:
    out = Mesh()
    out.piece_id = piece_id
    out.scale = meshes[0].scale
    out.source = "combined MC:LA stock pieces"
    if merge_materials:
        groups = {}
        order = []
        for mesh in meshes:
            for group in mesh.groups:
                mat = group["material"]
                if mat not in groups:
                    groups[mat] = []
                    order.append(mat)
                groups[mat].extend([[clone_vertex(v) for v in strip]
                                    for strip in group["strips"]])
        out.groups = [{"material": mat, "strips": groups[mat]} for mat in order]
    else:
        for mesh in meshes:
            for group in mesh.groups:
                out.groups.append({"material": group["material"],
                                   "strips": [[clone_vertex(v) for v in strip]
                                              for strip in group["strips"]]})
    return out


def translate(mesh: Mesh, delta) -> Mesh:
    return retarget(mesh, mesh.piece_id,
                    {g["material"]: g["material"] for g in mesh.groups},
                    delta=delta)


def source_anchor_tables(source: Pck):
    nodes = source.skel_nodes()
    by_name = {name: row for row in nodes for name in (row[1],)}
    by_id = {row[2]: row for row in nodes}
    return nodes, by_name, by_id


def apply_skeleton_anchors(target: Pck, source: Pck):
    source_nodes, source_by_name, _source_by_id = source_anchor_tables(source)
    del source_nodes
    target_nodes = target.skel_nodes()
    desired = {row[0]: tuple(row[6]) for row in target_nodes}
    changes = []
    for row in target_nodes:
        index, name = row[0], row[1]
        source_name = ANCHOR_MAP.get(name)
        if source_name is None:
            continue
        if source_name not in source_by_name:
            raise ValueError(f"source skeleton lacks {source_name} for target {name}")
        desired[index] = tuple(source_by_name[source_name][6])
        changes.append((name, source_name, tuple(row[6]), desired[index]))
    for row in target_nodes:
        index, parent = row[0], row[3]
        world = desired[index]
        local = world if parent is None else tuple(world[k] - desired[parent][k]
                                                   for k in range(3))
        target.set_skel_anchor(index, world, second=False)
        target.set_skel_anchor(index, local, second=True)
    return changes


def clear_lod_slot(target: Pck, lod_name: str, index: int):
    """Make one LOD entry genuinely empty: both resident mesh and page name null.

    ``rmcLodPaged::Touch`` skips this state and still returns success.  Clearing
    only the mesh pointer would instead page the old named file back in.
    """
    lod_va = target.lods()[lod_name]
    rows = target.slots(lod_va)
    if not 0 <= index < len(rows):
        raise ValueError(f"missing {lod_name} slot {index}")
    row = rows[index]
    header = target.F(lod_va)
    names = target.F(target.U(header + 0x0C))
    old = (row[1], row[2], row[3])
    target.set_slot(row[4], 0)
    struct.pack_into("<I", target.data, names + 4*index, 0)
    return old


def adapt_c6_mechanics(target: Pck):
    """Remove two C5-only visual mechanics from the C6 conversion.

    The 2007 C6 has fixed headlights.  The PS2 donor is a C5 and stores a
    40-degree target at rmcCarModelType+0x3640; leaving it live rotates the
    root-authored C6 lamps around the donor pop-up bone.  A zero target keeps
    the headlight customization ID intact while making the update an identity.

    HLOD slot 9 is the donor-only inner ``trunk_backside``.  The PSP stock trunk
    already supplies the complete panel, so retaining slot 9 mixes 2003 local
    geometry with the relocated 2007 trunk pivot.
    """
    model = target.F(target.U(0x88))
    if not target.inside(model + 0x3640):
        raise ValueError("vehicle model does not reach popup-headlight tuning")
    old_angle = struct.unpack_from("<f", target.data, model + 0x3640)[0]
    struct.pack_into("<f", target.data, model + 0x3640, 0.0)
    removed = clear_lod_slot(target, "HLOD", 9)
    return {"popup_angle_old": old_angle, "popup_angle_new": 0.0,
            "cleared_hlod_9": removed}


def main_meshes(source_dir: pathlib.Path, source_main: Pck, material_map,
                donor_bones: dict[str, tuple]):
    paths = source_files(source_dir)
    raw = {role: load(str(path)) for role, path in paths.items()}
    # Stock trunk is one of the six meshes embedded in the PSP main package.
    trunk_slot = next(row for va in source_main.lods().values()
                      for row in source_main.slots(va)
                      if row[1] and "tk_stk" in row[2].lower())
    raw["trunk"] = read_embedded_psp(source_main, trunk_slot[1])

    target_name = {
        "front": "bmpf", "rear": "bmpr", "tail": "bmpr",
        "hood": "hd", "side": "ss", "head": "headlight_R_bne",
        "body": "vroot", "interior": "vroot", "window": "vroot",
        "decals": "vroot", "trunk": "trunk",
    }
    # Source lamps use generic lit_textured; choose the donor's functional lamp
    # classes for those specific geometry groups.
    overrides = {
        "head": {8: 14},       # emissive_headlight
        "tail": {8: 4},        # player_taillight
    }
    converted = {}
    for role, mesh in raw.items():
        target = donor_bones[target_name[role]]
        converted[role] = retarget(mesh, target[2], material_map,
                                   overrides.get(role))
    return raw, converted


def bake_for_root(mesh: Mesh, source_anchor) -> Mesh:
    baked = translate(mesh, source_anchor)
    baked.piece_id = 0
    return baked


def write_mesh(path: pathlib.Path, mesh: Mesh):
    path.parent.mkdir(parents=True, exist_ok=True)
    save(mesh, str(path))
    check = load(str(path))
    if check.stats()[3] == 0:
        raise ValueError(f"writer produced an empty mesh: {path}")
    return check.stats()


def append_path_slots(target: Pck, assignments):
    """Embed one independent mesh body into every ``(LOD, index)`` slot.

    rmcLodPaged fixes every non-null slot while loading.  Sharing one serialized
    mesh pointer between HLOD and MLOD therefore fixes the same object twice:
    all of its internal pointers receive the image delta twice and leave EE
    RAM.  A savestate of the first C6 build proved the exact failure on trunk,
    window and decals.  The file pointers were valid, while RAM contained
    ``serialized + 2 * load_delta``.  Retail tables use distinct objects, so do
    the same even when two slots originate from the same generated .mesh.pck.
    """
    lods = target.lods()
    needed = {"HLOD", "MLOD", "LLOD"}
    if not needed.issubset(lods):
        raise ValueError(f"donor is missing LOD tables: {needed - set(lods)}")

    for lod, index, path in assignments:
        slots = target.slots(lods[lod])
        row = slots[index]
        va, _fixed, _length = target.append_mesh_body(str(path))
        target.set_slot(row[4], va)
        # Keep the table ID and embedded mesh ID in lockstep.
        target.set_id(lods[lod], index, load(str(path)).piece_id)
    return len(assignments)


def append_slots(target: Pck, generated: dict[str, pathlib.Path]):
    assignments = [
        ("HLOD", 8, generated["trunk"]),
        ("HLOD", 14, generated["body"]),
        ("HLOD", 15, generated["window"]),
        ("HLOD", 16, generated["decals"]),
        ("HLOD", 17, generated["interior"]),
        ("MLOD", 0, generated["trunk"]),
        ("MLOD", 1, generated["mlod_body"]),
        ("MLOD", 2, generated["window"]),
        ("MLOD", 3, generated["decals"]),
        ("LLOD", 0, generated["llod"]),
    ]
    return append_path_slots(target, assignments)


def garage_slots(generated: dict[str, pathlib.Path], external: dict[str, pathlib.Path]):
    """All converted visual slots for the self-contained garage package."""
    return [
        ("HLOD", 4, external["front"]),
        ("HLOD", 5, external["rear"]),
        ("HLOD", 6, external["tail"]),
        ("HLOD", 7, external["hood"]),
        ("HLOD", 8, generated["trunk"]),
        ("HLOD", 10, external["side"]),
        ("HLOD", 12, external["head_left"]),
        ("HLOD", 13, external["head_right"]),
        ("HLOD", 14, generated["body"]),
        ("HLOD", 15, generated["window"]),
        ("HLOD", 16, generated["decals"]),
        ("HLOD", 17, generated["interior"]),
        ("MLOD", 0, generated["trunk"]),
        ("MLOD", 1, generated["mlod_body"]),
        ("MLOD", 2, generated["window"]),
        ("MLOD", 3, generated["decals"]),
        ("LLOD", 0, generated["llod"]),
    ]


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def write_dave(path: pathlib.Path, members: dict[str, pathlib.Path]):
    """Write a plain-name, uncompressed DAVE mini archive.

    MC3 accepts both DAVE (plain names) and Dave (six-bit compressed names).
    Vehicle PCKs deliberately stay uncompressed and every payload starts on a
    0x800-byte sector, matching the retail archive contract.
    """
    rows = sorted(((name.replace("\\", "/"), pathlib.Path(source))
                   for name, source in members.items()), key=lambda row: row[0].lower())
    if not rows:
        raise ValueError("cannot write an empty DAVE archive")
    names = bytearray()
    name_offsets = []
    for name, _source in rows:
        encoded = name.encode("ascii") + b"\0"
        name_offsets.append(len(names))
        names += encoded
    entry_size = align_up(len(rows) * 0x10, 0x800)
    names_size = align_up(len(names), 0x800)
    data = bytearray(0x800 + entry_size + names_size)
    data[:4] = b"DAVE"
    struct.pack_into("<III", data, 4, len(rows), entry_size, names_size)
    names_at = 0x800 + entry_size
    data[names_at:names_at + len(names)] = names
    for index, ((name, source), name_offset) in enumerate(zip(rows, name_offsets)):
        del name
        payload = source.read_bytes()
        file_offset = align_up(len(data), 0x800)
        if file_offset > len(data):
            data += b"\0" * (file_offset - len(data))
        data += payload
        struct.pack_into("<IIII", data, 0x800 + index * 0x10,
                         name_offset, file_offset, len(payload), len(payload))
    final_size = align_up(len(data), 0x800)
    data += b"\0" * (final_size - len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(rows), len(data)


_DAVE_CHARS = "\x00 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~\x7f"


def read_dave_member(path: pathlib.Path, wanted: str) -> bytes:
    """Read one member from a DAVE or six-bit-name Dave archive."""
    data = path.read_bytes()
    magic = data[:4]
    if magic not in (b"DAVE", b"Dave"):
        raise ValueError(f"legacy archive is not DAVE/Dave: {path}")
    count, entry_size, _names_size = struct.unpack_from("<III", data, 4)
    names_at = 0x800 + entry_size
    wanted = wanted.replace("\\", "/").lower()
    previous = ""
    for index in range(count):
        name_rel, file_off, full_size, stored_size = struct.unpack_from(
            "<IIII", data, 0x800 + 0x10 * index)
        pos = names_at + name_rel
        if magic == b"DAVE":
            end = data.find(b"\0", pos)
            if end < 0:
                raise ValueError(f"unterminated DAVE member name at index {index}")
            name = data[pos:end].decode("ascii")
        else:
            cursor = pos
            codes = []
            while len(codes) < 2:
                value = int.from_bytes(data[cursor:cursor + 3], "little")
                cursor += 3
                codes.extend((value >> (6 * bit)) & 0x3F for bit in range(4))
            prefix = ""
            if codes[0] >= 0x38:
                keep = (codes[1] - 0x20) * 8 + codes[0] - 0x38
                prefix = previous[:keep]
                codes = codes[2:]
            chars = []
            while True:
                if not codes:
                    value = int.from_bytes(data[cursor:cursor + 3], "little")
                    cursor += 3
                    codes.extend((value >> (6 * bit)) & 0x3F for bit in range(4))
                code = codes.pop(0)
                if code == 0:
                    break
                chars.append(_DAVE_CHARS[code])
            name = prefix + "".join(chars)
        previous = name
        name = name.replace("\\", "/")
        if name.lower() != wanted:
            continue
        payload = bytes(data[file_off:file_off + stored_size])
        if full_size != stored_size:
            payload = zlib.decompress(payload, -15)
        if len(payload) != full_size:
            raise ValueError(f"bad legacy DAVE payload size: {name}")
        return payload
    raise ValueError(f"DAVE member not found: {wanted}")


def _audio_module():
    candidates = (
        pathlib.Path(__file__).resolve().parent / "bnk_tool" / "pck.py",
        pathlib.Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'bnk_tool/pck.py')),
    )
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ImportError("cannot find bnk_tool/pck.py for the vehicle-audio import")
    spec = importlib.util.spec_from_file_location("_mc3_vehicle_audio_port", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_legacy_audio(legacy_root: pathlib.Path, build_dir: pathlib.Path):
    """Read the old C6 mod's PSP-audio graph and its two external bank assets."""
    archive_candidates = sorted(set(legacy_root.rglob(f"{PROFILE_DONOR}.dat")))
    if not archive_candidates:
        raise ValueError(f"legacy {PROFILE_DONOR}.dat not found below {legacy_root}")
    member = f"resources/vehicle/{PROFILE_DONOR}/{PROFILE_DONOR}.pck"
    audio = _audio_module()
    donor_path = build_dir / "legacy_psp_audio_donor.pck"
    donor_document = None
    archive = None
    wanted_banks = {"AS_z06mod_eng", "AS_z06mod_ex"}
    for candidate in archive_candidates:
        pck_bytes = read_dave_member(candidate, member)
        donor_path.write_bytes(pck_bytes)
        parsed = audio.parse_pck(donor_path)
        if wanted_banks <= set(parsed.bank_names()):
            archive = candidate
            donor_document = parsed
            break
    if archive is None or donor_document is None:
        raise ValueError(
            f"none of the {len(archive_candidates)} legacy archive(s) contains "
            "AS_z06mod_eng and AS_z06mod_ex"
        )

    bank_candidates = list(legacy_root.rglob("BANKS.DAT"))
    banks = bank_candidates[0] if bank_candidates else None
    tds = []
    for name in ("as_z06mod_eng.td", "as_z06mod_ex.td"):
        hits = list(legacy_root.rglob(name))
        if hits:
            tds.append(hits[0])
    if banks is None or len(tds) != 2:
        raise ValueError("legacy BANKS.DAT or as_z06mod_*.td is missing")
    return audio, donor_document, banks, tds


def import_legacy_audio(target: Pck, audio, donor_document):
    target_document = audio.parse_pck_bytes(bytes(target.data), target.path)
    editor = audio.PckEditor(target_document)
    report = editor.import_from_document(donor_document, mode="experimental")
    target.data = editor.buf
    return report


def find_garage_donor(args, donor_root: pathlib.Path, donor_dir: pathlib.Path):
    if args.garage_donor:
        path = pathlib.Path(args.garage_donor).resolve()
        if not path.is_file():
            raise ValueError(f"garage donor does not exist: {path}")
        return path
    direct = donor_dir / f"{PROFILE_DONOR}_g.pck"
    if direct.is_file():
        return direct
    candidates = list(donor_root.rglob(f"{PROFILE_DONOR}_g.pck"))
    if len(candidates) == 1:
        return candidates[0]
    return None


def validate_main(path: pathlib.Path, donor_tag_count: int, shader_count: int,
                  expected_embedded=None):
    pck = Pck(str(path))
    if pck.U(4) != 0x0038E030 or pck.U(8) != 1:
        raise ValueError(f"bad PS2 vehicle header: {path}")
    if len(pck.data) % 16 or pck.body != len(pck.data) - 0x80:
        raise ValueError(f"bad size/alignment: {path}")
    if len(pck.bone_tags()) != donor_tag_count:
        raise ValueError(f"bone tag table changed unexpectedly: {path}")
    for lod, va in pck.lods().items():
        for index, mesh_va, name, slot_id, _off in pck.slots(va):
            if not mesh_va:
                continue
            footprint = pck.mesh_footprint(mesh_va)
            if footprint is None:
                raise ValueError(f"{lod}[{index}] {name}: invalid embedded mesh")
            mesh_off = pck.F(mesh_va)
            groups = pck.U(mesh_off + 8)
            mat_off = pck.F(pck.U(mesh_off + 0x0C))
            mats = [pck.U16(mat_off + 2*i) for i in range(groups)]
            if any(mat >= shader_count for mat in mats):
                raise ValueError(f"{lod}[{index}] uses material outside donor table: {mats}")
            check_id = ((lod, index) in expected_embedded if expected_embedded is not None
                        else index in ({8, 14, 15, 16, 17} if lod == "HLOD" else
                                       {0, 1, 2, 3} if lod == "MLOD" else {0}))
            if check_id:
                internal_id = pck.U16(mesh_off + 6)
                if internal_id != (slot_id & 0xFFFF):
                    raise ValueError(f"{lod}[{index}] mesh ID {internal_id} != slot {slot_id}")
    return pck


def build(args):
    source_root = pathlib.Path(args.source).resolve()
    donor_root = pathlib.Path(args.donor).resolve()
    out_root = pathlib.Path(args.out).resolve()
    source_dir = find_vehicle_dir(source_root, PROFILE_SOURCE)
    donor_dir = find_vehicle_dir(donor_root, PROFILE_DONOR)
    source_main_path = source_dir / f"{PROFILE_SOURCE}.psppck"
    donor_main_path = donor_dir / f"{PROFILE_DONOR}.pck"
    if not source_main_path.is_file() or not donor_main_path.is_file():
        raise ValueError("main source or donor package is missing")

    final_dir = out_root / "resources" / "vehicle" / PROFILE_DONOR
    build_dir = out_root / "_build"
    final_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    legacy_audio = None
    if args.legacy_mod:
        legacy_audio = prepare_legacy_audio(pathlib.Path(args.legacy_mod).resolve(),
                                            build_dir)

    source_main = Pck(str(source_main_path))
    donor_probe = Pck(str(donor_main_path))
    source_nodes, source_by_name, source_by_id = source_anchor_tables(source_main)
    del source_nodes, source_by_name
    donor_bones = {row[1]: row for row in donor_probe.skel_nodes()}
    source_shaders, donor_shaders, material_map = shader_remap(
        source_main_path, donor_main_path)
    raw, converted = main_meshes(
        source_dir, source_main, material_map, donor_bones)

    # External stock slots: names come from the donor table, so no main-package
    # string surgery is necessary.
    hslots = donor_probe.slots(donor_probe.lods()["HLOD"])
    external_roles = {4: "front", 5: "rear", 6: "tail", 7: "hood", 10: "side"}
    external_stats = {}
    external_paths = {}
    for slot_index, role in external_roles.items():
        name = hslots[slot_index][2] + ".pck"
        path = final_dir / name
        external_stats[name] = write_mesh(path, converted[role])
        external_paths[role] = path
    left, right = split_x(converted["head"])
    for slot_index, role, mesh in ((12, "head_left", left),
                                   (13, "head_right", right)):
        name = hslots[slot_index][2] + ".pck"
        path = final_dir / name
        external_stats[name] = write_mesh(path, mesh)
        external_paths[role] = path

    generated = {}
    for role in ("trunk", "body", "window", "decals", "interior"):
        path = build_dir / f"{role}.mesh.pck"
        write_mesh(path, converted[role])
        generated[role] = path
    mlod_body = combine([converted["body"], converted["interior"]], 0)
    generated["mlod_body"] = build_dir / "mlod_body.mesh.pck"
    write_mesh(generated["mlod_body"], mlod_body)

    # LLOD is a complete root-attached stock car. Merge equal materials to keep
    # group count low even though MC:LA supplied no lower-detail geometry.
    low_roles = ("body", "interior", "window", "decals", "front", "rear",
                 "hood", "side", "head", "tail", "trunk")
    low_meshes = [bake_for_root(converted[role],
                                source_by_id[raw[role].piece_id][6])
                  for role in low_roles]
    llod = combine(low_meshes, 0, merge_materials=True)
    generated["llod"] = build_dir / "llod_complete.mesh.pck"
    write_mesh(generated["llod"], llod)

    donor_tag_count = len(donor_probe.bone_tags())
    built_main = []
    texture_reports = []
    mechanics_reports = []
    runtime_paths = []
    for suffix in ("", "_o"):
        donor_path = donor_dir / f"{PROFILE_DONOR}{suffix}.pck"
        if not donor_path.is_file():
            if suffix:
                continue
            raise ValueError(f"missing donor {donor_path}")
        target = Pck(str(donor_path))
        changes = apply_skeleton_anchors(target, source_main)
        appended = append_slots(target, generated)
        mechanics_reports.append((donor_path.name, adapt_c6_mechanics(target)))
        if args.texture_mode == "psp-native":
            texture_reports.append((donor_path.name,
                                    port_native_texture(target, source_main_path)))
        audio_report = None
        if legacy_audio is not None:
            audio_report = import_legacy_audio(target, legacy_audio[0], legacy_audio[1])
        output_path = final_dir / donor_path.name
        target.save(str(output_path))
        validate_main(output_path, donor_tag_count, len(donor_shaders))
        built_main.append((output_path.name, appended, len(changes), audio_report))
        runtime_paths.append(output_path)

    # The garage/front-end has a distinct retail package. The old manual port
    # copied the race PCK byte-for-byte to _g, losing the garage-specific graph.
    # Build from the real _g donor and keep it self-contained: the global
    # ASSETS archive then needs only this one PCK for the garage.
    garage_donor = find_garage_donor(args, donor_root, donor_dir)
    garage_output = None
    if garage_donor is not None:
        garage_probe = Pck(str(garage_donor))
        target = Pck(str(garage_donor))
        changes = apply_skeleton_anchors(target, source_main)
        assignments = garage_slots(generated, external_paths)
        appended = append_path_slots(target, assignments)
        mechanics_reports.append((f"{PROFILE_DONOR}_g.pck",
                                    adapt_c6_mechanics(target)))
        if args.texture_mode == "psp-native":
            texture_reports.append((f"{PROFILE_DONOR}_g.pck",
                                    port_native_texture(target, source_main_path)))
        audio_report = None
        if legacy_audio is not None:
            audio_report = import_legacy_audio(target, legacy_audio[0], legacy_audio[1])
        garage_output = final_dir / f"{PROFILE_DONOR}_g.pck"
        target.save(str(garage_output))
        validate_main(garage_output, len(garage_probe.bone_tags()),
                      len(donor_shaders),
                      {(lod, index) for lod, index, _path in assignments})
        built_main.append((garage_output.name, appended, len(changes), audio_report))

    # Race/cruise mounts a per-vehicle mini archive. Keep root-attached shell
    # meshes embedded in the main files and carry the seven stock-piece
    # external meshes beside them. ID 0 would also page correctly; it means
    # skeleton node 0/vroot, not "external loading disabled".
    runtime_paths.extend(external_paths.values())
    prefix = f"resources/vehicle/{PROFILE_DONOR}/"
    archive_members = {prefix + path.name: path for path in runtime_paths}
    dat_path = out_root / f"{PROFILE_DONOR}.dat"
    dat_members, dat_size = write_dave(dat_path, archive_members)

    assets_root = out_root / "ASSETS"
    assets_root.mkdir(parents=True, exist_ok=True)
    assets_dat_path = assets_root / dat_path.name
    shutil.copy2(dat_path, assets_dat_path)
    if garage_output is not None:
        garage_install = (assets_root / "resources" / "vehicle" /
                          PROFILE_DONOR / garage_output.name)
        garage_install.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(garage_output, garage_install)

    audio_assets = None
    if legacy_audio is not None:
        _audio, donor_audio_doc, legacy_banks, legacy_tds = legacy_audio
        shutil.copy2(legacy_banks, out_root / "BANKS.DAT")
        td_install = assets_root / "audio" / "banks"
        td_install.mkdir(parents=True, exist_ok=True)
        for source_td in legacy_tds:
            shutil.copy2(source_td, td_install / source_td.name)
        audio_assets = (donor_audio_doc, legacy_banks, legacy_tds)

    # Validate external material domains after all role-specific overrides.
    for path in final_dir.glob("*.mesh.pck"):
        mesh = load(str(path))
        if any(g["material"] >= len(donor_shaders) for g in mesh.groups):
            raise ValueError(f"external mesh {path.name} has an invalid material")

    lines = [
        f"Source: {source_main_path}",
        f"Donor:  {donor_main_path}",
        f"Output: {final_dir}",
        "",
        "Material remap by shader template:",
    ]
    for index, name in enumerate(source_shaders):
        lines.append(f"  {index:2d} {name:30s} -> {material_map[index]:2d} "
                     f"{donor_shaders[material_map[index]]}")
    lines += [
        "  head material 8 -> 14 emissive_headlight.shadert",
        "  tail material 8 -> 4 player_taillight.shadert",
        "",
        "C6 geometry:",
        "  stock faces preserved; embedding rebases only typed mesh pointers.",
        "",
        "Main packages:",
    ]
    lines += [f"  {name}: {count} appended mesh bodies, {anchors} anchor mappings"
              + (f", PSP audio import {sum(v for k, v in audio_report.items() if k != 'warnings' and isinstance(v, int))} edits"
                 if audio_report is not None else "")
              for name, count, anchors, audio_report in built_main]
    lines += ["", "C6 mechanical adaptation:"]
    lines += [f"  {name}: popup headlights {row['popup_angle_old']:.6f} -> 0 rad; "
              f"cleared HLOD slot 9 {row['cleared_hlod_9'][1]}"
              for name, row in mechanics_reports]
    lines += [
        "",
        "Retail-style packaging:",
        f"  {dat_path.name}: DAVE, {dat_members} uncompressed members, {dat_size} bytes",
        f"  install archive: ASSETS/{dat_path.name}",
        (f"  garage: ASSETS/resources/vehicle/{PROFILE_DONOR}/{garage_output.name} "
         "(self-contained)" if garage_output is not None else
         "  garage: skipped; provide --garage-donor to build the retail _g package"),
        ("  audio: legacy C6 PSP port imported as AS_z06mod_eng/ex; BANKS.DAT and two .td files included"
         if audio_assets is not None else
         "  audio: inherited from the clean PS2 donor"),
    ]
    if texture_reports:
        lines += ["", "Native PSP texture port:"]
        for name, row in texture_reports:
            old = row["old_dimensions"]
            new = row["new_dimensions"]
            lines.append(
                f"  {name}: {row['source_name']} {new[0]}x{new[1]} -> "
                f"{row['target_name']} (was {old[0]}x{old[1]}); "
                f"{row['padding_reclaimed']} unused bytes filled with CD"
            )
    lines += ["", "External meshes:"]
    lines += [f"  {name}: groups={stats[0]} strips={stats[1]} "
              f"verts={stats[2]} tris={stats[3]}"
              for name, stats in sorted(external_stats.items())]
    lines += [
        "",
        "Known first-test limitations:",
        ("  - PS2 performance/collision remain from vp_corvettez06_03; audio uses the legacy PSP port."
         if audio_assets is not None else
         "  - PS2 performance/audio/collision remain from vp_corvettez06_03."),
        ("  - The PSP trim atlas is encoded at its native 128x128; donor shader "
         "objects and the two 8x8 fallback textures remain." if texture_reports else
         "  - Donor textures remain; use --texture-mode psp-native for the source atlas."),
        "  - MC:LA has no MLOD/LLOD here, so those reuse/merge HLOD geometry.",
        "  - Custom PSP bumpers/hoods/skirts/lights are not registered yet; stock is complete.",
        ("  - The full legacy BANKS.DAT is still required because its two PSP-derived .bnk payloads live there."
         if audio_assets is not None else
         "  - Optional --legacy-mod imports the old C6 PSP audio graph and bank assets."),
    ]
    (out_root / "PORT_REPORT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    install_lines = [
        "MC:LA Corvette Z06 2007 -> MC3 PS2 (replaces vp_corvettez06_03)",
        "",
        "Retail/HostFS install:",
        "  Merge this build's ASSETS folder into the game's ASSETS folder.",
        f"  ASSETS/{PROFILE_DONOR}.dat serves race/cruise.",
        (f"  ASSETS/resources/vehicle/{PROFILE_DONOR}/{PROFILE_DONOR}_g.pck "
         "serves garage/front-end."),
        "",
        ("Copy BANKS.DAT to the game root. It contains the PSP-derived"
         if audio_assets is not None else
         "No BANKS.DAT replacement is included; audio stays with the PS2 donor."),
        ("as_z06mod_eng/ex banks referenced by this car's VehicleAudioRoot."
         if audio_assets is not None else ""),
        "",
        "The top-level resources folder is the unpacked/debug form of the mini archive.",
        "It is retained for inspection and loose-file experiments.",
    ]
    (out_root / "INSTALL.txt").write_text("\n".join(install_lines) + "\n",
                                          encoding="utf-8")
    print("\n".join(lines))
    return final_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="folder containing vp_chv_corvette_z06_07")
    parser.add_argument("donor", help="folder containing vp_corvettez06_03")
    parser.add_argument("out", help="output root")
    parser.add_argument("--garage-donor", help="clean vp_corvettez06_03_g.pck used by the garage/front-end")
    parser.add_argument("--texture-mode", choices=("psp-native", "donor"),
                        default="psp-native",
                        help="port the native 128x128 PSP trim atlas (default), or keep donor textures")
    parser.add_argument("--legacy-mod",
                        help="old C6 mod folder: imports AS_z06mod PSP audio and packages BANKS.DAT + two .td files")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
