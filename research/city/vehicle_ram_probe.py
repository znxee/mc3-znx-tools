#!/usr/bin/env python3
"""Compare a serialized MC3 vehicle PCK with its load-in-place EE image.

This is intentionally read-only.  The address passed with --base is the EE
address corresponding to file offset 0x80 (the first byte of the PCK body).
"""

import argparse
import importlib.util
import json
import os
import struct
import sys


ROOT_PTR_VA = 0x06800128
LOD_NAMES = ("HLOD", "MLOD", "LLOD")
VEH_SLOTS = (
    (0x8C, "Package bound"), (0x90, "Material"),
    (0x94, "VehStuck"), (0x98, "VehInput"), (0x9C, "VehDriver"),
    (0xA0, "VehDamage"), (0xA4, "VehModel"),
    (0xA8, "Audio root"), (0xAC, "mcCarCustom runtime"),
    (0xB0, "CamShake"), (0xB4, "VehSim Base"),
    (0xB8, "VehSim Mods"), (0xBC, "VehGyro Base"),
    (0xC0, "VehGyro Mods"), (0xC4, "VehZone"), (0xC8, "VehMods"),
)

RETAIL_VTABLES = {
    0x00626D08: "rmcLodPaged",
    0x00626D98: "rmcModelGeom",
    0x006276C0: "mcCarCustom",
    0x00627770: "vehicle audio root",
    0x006277F0: "mcCarDamageTune",
    0x00627820: "mcGyroTune",
    0x006278C8: "mcCarSimTune",
    0x00627978: "mcCarTuneZone",
    0x006279A8: "mcCarCamShakeTune",
    0x00627AC0: "mcDriverTune",
    0x00627F40: "mcCarModelTune",
    0x00627FD0: "mcCullableType",
    0x006281B0: "mcCarModsTune",
    0x006281E0: "mcCarStuckTune",
    0x0062DF00: "phMaterial",
    0x0062E4C0: "drwShaderModel",
    0x00633010: "phBoundGeometry",
    0x00634A50: "mcPlayerInputTune",
}

# Fields compared by rmcCarModelType::ComputeMatrixRemapTable (0x3024C8).
# Labels with a question mark are deliberately not claimed as final: the code
# proves the offset pairing, while the exact category name still needs a second
# customized-car capture.
CUSTOM_SELECTIONS = (
    (0x1184, "front bumper"),
    (0x118C, "rear bumper"),
    (0x1194, "side skirts"),
    (0x119C, "brush guard / top ?"),
    (0x11A0, "grille / louvers"),
    (0x11A8, "wing / spoiler"),
    (0x11E8, "headlights / special ?"),
    (0x11F0, "hidden 24-slot set ?"),
    (0x11F8, "hood"),
    (0x1200, "scoop / blower"),
    (0x12BC, "taillights"),
)


def u16(data, off):
    if not (0 <= off <= len(data) - 2):
        return None
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    if not (0 <= off <= len(data) - 4):
        return None
    return struct.unpack_from("<I", data, off)[0]


def hx(value):
    return "-" if value is None else f"0x{value:08X}"


def vt_name(value):
    return RETAIL_VTABLES.get(value, "")


def load_pck_class(tool_dir):
    path = os.path.join(tool_dir, "mc3_pck_embed.py")
    spec = importlib.util.spec_from_file_location("mc3_pck_embed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Pck


class Probe:
    def __init__(self, pck, ram, ram_base):
        self.p = pck
        self.ram = ram
        self.ram_base = ram_base
        self.body_end = min(len(pck.data), 0x80 + pck.body)

    def ram_u16(self, addr):
        return u16(self.ram, addr)

    def ram_u32(self, addr):
        return u32(self.ram, addr)

    def ram_cstr(self, addr, limit=256):
        if not (0 <= addr < len(self.ram)):
            return ""
        end = addr
        stop = min(len(self.ram), addr + limit)
        while end < stop and self.ram[end] not in (0, 0xCD):
            end += 1
        return bytes(self.ram[addr:end]).decode("latin1", "replace")

    def ram_for_off(self, off):
        return self.ram_base + off - 0x80

    def ram_for_va(self, va):
        return self.ram_base + va - self.p.va

    def off_for_ram(self, addr):
        return 0x80 + addr - self.ram_base

    def serialized_inside(self, va):
        return self.p.va <= va < self.p.va + self.p.body

    def runtime_inside_image(self, addr):
        return self.ram_base <= addr < self.ram_base + self.p.body

    def root(self):
        slot_off = self.p.F(ROOT_PTR_VA)
        slot_ram = self.ram_for_off(slot_off)
        file_ptr = self.p.U(slot_off)
        ram_ptr = self.ram_u32(slot_ram)
        out = {
            "slot_file_offset": slot_off,
            "slot_runtime_address": slot_ram,
            "serialized_pointer": file_ptr,
            "runtime_pointer": ram_ptr,
            "expected_runtime_pointer": self.ram_for_va(file_ptr),
            "file_root_offset": self.p.F(file_ptr),
            "fields": [],
        }
        file_root = self.p.F(file_ptr)
        # drwShaderModel ends at +0x84.  Bytes after that belong to the next
        # adjacent serialized allocation, even though they are contiguous.
        for rel in range(0, 0x84, 4):
            out["fields"].append({
                "offset": rel,
                "serialized": self.p.U(file_root + rel),
                "runtime": self.ram_u32(ram_ptr + rel),
            })
        bound_va = self.p.U(file_root + 0x78)
        bound_rt = self.ram_u32(ram_ptr + 0x78)
        out["bound"] = {
            "serialized_pointer": bound_va,
            "file_offset": self.p.F(bound_va) if bound_va else None,
            "runtime_pointer": bound_rt,
            "serialized_token": (self.p.U(self.p.F(bound_va))
                                 if bound_va and self.serialized_inside(bound_va) else None),
            "runtime_vtable": self.ram_u32(bound_rt) if bound_rt else None,
        }
        out["bony_data"] = {
            "serialized_pointer": self.p.U(file_root + 0x80),
            "runtime_pointer": self.ram_u32(ram_ptr + 0x80),
        }
        return out

    def container(self):
        ptr_off = 0x88
        file_ptr = self.p.U(ptr_off)
        ram_ptr = self.ram_u32(self.ram_for_off(ptr_off))
        custom_ptr = self.ram_u32(self.ram_for_off(0xAC))
        custom = {
            "runtime_slot_address": self.ram_for_off(0xAC),
            "runtime_pointer": custom_ptr,
            "runtime_vtable": self.ram_u32(custom_ptr) if custom_ptr else None,
            "selections": [],
        }
        if custom_ptr:
            for off, name in CUSTOM_SELECTIONS:
                custom["selections"].append({
                    "offset": off, "name": name,
                    "value": self.ram_u32(custom_ptr + off),
                })
        return {
            "pointer_file_offset": ptr_off,
            "serialized_pointer": file_ptr,
            "file_object_offset": self.p.F(file_ptr),
            "runtime_pointer": ram_ptr,
            "expected_runtime_pointer": self.ram_for_va(file_ptr),
            "serialized_base_token": self.p.U(self.p.F(file_ptr) + 0x28),
            "runtime_base_vtable": self.ram_u32(ram_ptr + 0x28),
            "root_field_offset": 0xD8,
            "skeleton_field_offset": 0xDC,
            "custom": custom,
        }

    def lods(self, root):
        result = []
        file_root = self.p.F(root["serialized_pointer"])
        ram_root = root["runtime_pointer"]
        for i, name in enumerate(LOD_NAMES):
            file_table = self.p.U(file_root + 0x10 + 4 * i)
            ram_table = self.ram_u32(ram_root + 0x10 + 4 * i)
            if not file_table and not ram_table:
                continue
            entry = {
                "name": name,
                "serialized_table": file_table,
                "runtime_table": ram_table,
                "expected_runtime_table": self.ram_for_va(file_table) if file_table else 0,
                "slots": [],
            }
            if not file_table or not ram_table:
                result.append(entry)
                continue
            fh = self.p.F(file_table)
            count_off = self.p.table_count_off(file_table)
            fn = self.p.U16(fh + count_off)
            rn = self.ram_u16(ram_table + count_off)
            entry["count"] = fn
            entry["runtime_count"] = rn
            entry["serialized_token"] = self.p.U(fh + 4)
            entry["runtime_vtable"] = self.ram_u32(ram_table + 4)
            f_arrays = [self.p.U(fh + x) for x in (8, 12, 16)]
            r_arrays = [self.ram_u32(ram_table + x) for x in (8, 12, 16)]
            entry["serialized_arrays"] = f_arrays
            entry["runtime_arrays"] = r_arrays
            if not all(r_arrays) or rn is None:
                result.append(entry)
                continue
            limit = min(fn, rn, 4096)
            for j in range(limit):
                fmp = self.p.U(self.p.F(f_arrays[0]) + 4 * j)
                fnp = self.p.U(self.p.F(f_arrays[1]) + 4 * j)
                fid = self.p.U(self.p.F(f_arrays[2]) + 4 * j)
                rmp = self.ram_u32(r_arrays[0] + 4 * j)
                rnp = self.ram_u32(r_arrays[1] + 4 * j)
                rid = self.ram_u32(r_arrays[2] + 4 * j)
                fname = self.p.cstr(self.p.F(fnp)) if fnp else ""
                rname = self.ram_cstr(rnp) if rnp else ""
                expected = self.ram_for_va(fmp) if fmp and self.serialized_inside(fmp) else None
                if not fmp and rmp:
                    state = "external-loaded"
                elif fmp and rmp == expected:
                    state = "embedded-relocated"
                elif not fmp and not rmp:
                    state = "empty"
                elif fmp == rmp:
                    state = "unchanged-pointer"
                else:
                    state = "rewritten"
                vtable = self.ram_u32(rmp) if rmp else None
                entry["slots"].append({
                    "index": j, "name": fname, "runtime_name": rname,
                    "id": fid, "runtime_id": rid,
                    "serialized_mesh": fmp, "runtime_mesh": rmp,
                    "expected_runtime_mesh": expected,
                    "runtime_vtable": vtable, "state": state,
                })
            result.append(entry)
        return result

    def vehicle_structs(self):
        out = []
        for off, name in VEH_SLOTS:
            file_ptr = self.p.U(off)
            ram_slot = self.ram_for_off(off)
            ram_ptr = self.ram_u32(ram_slot)
            expected = (self.ram_for_va(file_ptr)
                        if file_ptr and self.serialized_inside(file_ptr) else None)
            out.append({
                "name": name, "file_slot": off, "runtime_slot": ram_slot,
                "serialized_pointer": file_ptr, "runtime_pointer": ram_ptr,
                "expected_runtime_pointer": expected,
                "serialized_head": self.p.U(self.p.F(file_ptr)) if file_ptr and self.serialized_inside(file_ptr) else None,
                "runtime_vtable": self.ram_u32(ram_ptr) if ram_ptr else None,
                "runtime_word_04": self.ram_u32(ram_ptr + 4) if ram_ptr else None,
            })
        return out

    def skeleton(self):
        ft = self.p.skel_table()
        if not ft:
            return None
        count, list_off, stride = ft
        header_off = None
        for source_off in (0x1AC, 0x1C4):
            candidate = self.p.U(source_off)
            if candidate and self.p.F(candidate) + 8 < len(self.p.data):
                if self.p.F(self.p.U(self.p.F(candidate) + 8)) == list_off:
                    header_off = self.p.F(candidate)
                    break
        result = {"count": count, "list_file_offset": list_off, "stride": stride}
        if header_off is None:
            return result
        header_va = self.p.V(header_off)
        runtime_header = self.ram_for_va(header_va)
        runtime_list = self.ram_u32(runtime_header + 8)
        result.update({
            "header_file_offset": header_off,
            "header_serialized_address": header_va,
            "header_runtime_address": runtime_header,
            "runtime_count": self.ram_u16(runtime_header),
            "serialized_list": self.p.V(list_off),
            "runtime_list": runtime_list,
            "expected_runtime_list": self.ram_for_off(list_off),
        })
        nodes = self.p.skel_nodes()
        same_names = same_ids = same_anchors = relation_ok = 0
        layout = self.p.SKEL_LAYOUT[stride]
        for node in nodes:
            idx, name, bone_id, _parent, _child, _next, anchor, off = node
            ro = runtime_list + idx * stride
            rname_ptr = self.ram_u32(ro + layout["name"])
            if self.ram_cstr(rname_ptr) == name:
                same_names += 1
            if self.ram_u16(ro + layout["id"]) == bone_id:
                same_ids += 1
            raw_anchor = struct.pack("<fff", *anchor)
            if 0 <= ro <= len(self.ram) - 12 and self.ram[ro:ro+12] == raw_anchor:
                same_anchors += 1
            rels = []
            for key in ("next", "child", "parent"):
                v = self.ram_u32(ro + layout[key])
                rels.append(v == 0 or (runtime_list <= v < runtime_list + count * stride and
                                      (v - runtime_list) % stride == 0))
            if all(rels):
                relation_ok += 1
        result.update({
            "same_names": same_names, "same_ids": same_ids,
            "same_anchors": same_anchors, "valid_runtime_relations": relation_ok,
        })
        return result

    def word_classes(self):
        regions = sorted(self.p.data_regions())
        merged = []
        for a, b in regions:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        counts = {
            "unchanged": 0, "self_relocated": 0, "zero_to_pointer": 0,
            "changed_other": 0, "packet_words_skipped": 0,
        }
        reg_i = 0
        for off in range(0x80, self.body_end - 3, 4):
            while reg_i < len(merged) and merged[reg_i][1] <= off:
                reg_i += 1
            if reg_i < len(merged) and merged[reg_i][0] <= off < merged[reg_i][1]:
                counts["packet_words_skipped"] += 1
                continue
            fv = self.p.U(off)
            rv = self.ram_u32(self.ram_for_off(off))
            if fv == rv:
                counts["unchanged"] += 1
            elif self.serialized_inside(fv) and rv == self.ram_for_va(fv):
                counts["self_relocated"] += 1
            elif fv == 0 and rv and 0x10000 <= rv < len(self.ram):
                counts["zero_to_pointer"] += 1
            else:
                counts["changed_other"] += 1
        counts["data_regions"] = len(merged)
        counts["data_region_bytes"] = sum(b - a for a, b in merged)
        return counts

    def changed_words(self):
        """Interesting non-packet u32 changes, with conservative labels."""
        regions = sorted(self.p.data_regions())

        def in_packet(off):
            # Only used by the verbose pass, so a short linear scan is fine.
            return any(a <= off < b for a, b in regions)

        out = []
        for off in range(0x80, self.body_end - 3, 4):
            if in_packet(off):
                continue
            fv = self.p.U(off)
            ra = self.ram_for_off(off)
            rv = self.ram_u32(ra)
            if fv == rv or (self.serialized_inside(fv) and rv == self.ram_for_va(fv)):
                continue
            if fv == 0 and rv and 0x10000 <= rv < len(self.ram):
                kind = "zero-to-pointer"
            elif 0x00700000 <= fv < 0x00800000 and 0x00100000 <= rv < 0x00700000:
                kind = "token-to-vtable"
            elif 0 < fv < len(self.ram) and 0 < rv < len(self.ram):
                kind = "external-pointer"
            else:
                kind = "state-or-data"
            target_head = self.ram_u32(rv) if 0 < rv <= len(self.ram) - 4 else None
            target_text = self.ram_cstr(rv, 80) if 0 < rv < len(self.ram) else ""
            if not target_text or any(ord(c) < 32 or ord(c) > 126 for c in target_text):
                target_text = ""
            out.append({
                "file_offset": off, "runtime_address": ra,
                "serialized": fv, "runtime": rv, "kind": kind,
                "target_head": target_head, "target_text": target_text,
            })
        return out

    def analyze(self):
        root = self.root()
        return {
            "file": self.p.path,
            "serialized_body_address": self.p.va,
            "type_id": self.p.U(4),
            "version": self.p.U(8),
            "body_size": self.p.body,
            "ram_base": self.ram_base,
            "relocation_delta": self.ram_base - self.p.va,
            "container": self.container(),
            "root": root,
            "lods": self.lods(root),
            "vehicle_structs": self.vehicle_structs(),
            "skeleton": self.skeleton(),
            "word_classes": self.word_classes(),
            "changed_words": self.changed_words(),
        }


def print_report(report):
    print("MC3 loaded vehicle probe")
    print(f"type/version {hx(report['type_id'])} / {report['version']}")
    print(f"PCK body  {hx(report['serialized_body_address'])} + {hx(report['body_size'])}")
    print(f"EE image  {hx(report['ram_base'])}")
    print(f"delta     {report['relocation_delta']:+#x}")
    top = report["container"]
    print("\nRMC CAR MODEL TYPE")
    print(f"file +{top['file_object_offset']:#x} -> EE {hx(top['runtime_pointer'])} "
          f"(ptr stored at file +{top['pointer_file_offset']:#x})")
    print(f"base token {hx(top['serialized_base_token'])} -> "
          f"{hx(top['runtime_base_vtable'])} {vt_name(top['runtime_base_vtable'])}")
    custom = top["custom"]
    print(f"mcCarCustom file +0xac runtime-only -> {hx(custom['runtime_pointer'])} "
          f"vt={hx(custom['runtime_vtable'])} {vt_name(custom['runtime_vtable'])}")
    for sel in custom["selections"]:
        value = sel["value"]
        signed = value - 0x100000000 if value is not None and value & 0x80000000 else value
        shown = "-" if signed is None else str(signed)
        print(f"  custom +{sel['offset']:04X} {shown:>4s}  {sel['name']}")
    root = report["root"]
    print("\nROOT")
    print(f"slot file +{root['slot_file_offset']:#x} -> RAM {hx(root['slot_runtime_address'])}")
    print(f"ptr  {hx(root['serialized_pointer'])} -> {hx(root['runtime_pointer'])} "
          f"expected {hx(root['expected_runtime_pointer'])}")
    for field in root["fields"]:
        rel = field["offset"]
        fv, rv = field["serialized"], field["runtime"]
        mark = "=" if fv == rv else "->"
        print(f"  +{rel:02X} {hx(fv)} {mark:2s} {hx(rv)}")
    bnd = root["bound"]
    bnd_off = "-" if bnd["file_offset"] is None else f"+{bnd['file_offset']:#x}"
    print(f"  bound file {bnd_off} {hx(bnd['serialized_token'])} -> "
          f"EE {hx(bnd['runtime_pointer'])} {hx(bnd['runtime_vtable'])} "
          f"{vt_name(bnd['runtime_vtable'])}")
    print("\nLOD TABLES")
    for lod in report["lods"]:
        slots = lod.get("slots", [])
        states = {}
        for slot in slots:
            states[slot["state"]] = states.get(slot["state"], 0) + 1
        print(f"{lod['name']:4s} {hx(lod['serialized_table'])} -> {hx(lod['runtime_table'])} "
              f"count {lod.get('count', '-')} / {lod.get('runtime_count', '-')} "
              f"vt={hx(lod.get('runtime_vtable'))} {vt_name(lod.get('runtime_vtable'))} {states}")
        for slot in slots:
            if slot["state"] in ("external-loaded", "rewritten"):
                print(f"  [{slot['index']:3d}] id={slot['id']:4d} {slot['state']:15s} "
                      f"{hx(slot['runtime_mesh'])} vt={hx(slot['runtime_vtable'])} {slot['name']}")
    print("\nVEHICLE OBJECTS")
    for item in report["vehicle_structs"]:
        print(f"{item['name']:14s} {hx(item['serialized_pointer'])} -> "
              f"{hx(item['runtime_pointer'])} vt={hx(item['runtime_vtable'])} "
              f"{vt_name(item['runtime_vtable'])} expected={hx(item['expected_runtime_pointer'])}")
    sk = report["skeleton"]
    print("\nSKELETON")
    if not sk:
        print("not found")
    else:
        print(json.dumps(sk, indent=2))
    print("\nALIGNED U32 CLASSES (packet data excluded)")
    for key, value in report["word_classes"].items():
        print(f"{key:24s} {value}")


def print_details(report):
    changes = report["changed_words"]
    print("\nZERO -> POINTER")
    for x in changes:
        if x["kind"] == "zero-to-pointer":
            extra = x["target_text"] or ("head=" + hx(x["target_head"]))
            print(f"file +{x['file_offset']:06X} / EE {hx(x['runtime_address'])}: "
                  f"{hx(x['runtime'])} {extra}")
    pairs = {}
    for x in changes:
        if x["kind"] == "token-to-vtable":
            key = (x["serialized"], x["runtime"])
            pairs[key] = pairs.get(key, 0) + 1
    print("\nSERIALIZED TOKEN -> RUNTIME VTABLE")
    for (source, target), count in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{hx(source)} -> {hx(target)}  x{count}")
    kinds = {}
    for x in changes:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    print("\nVERBOSE CHANGE CLASSES")
    for key, value in sorted(kinds.items()):
        print(f"{key:24s} {value}")


def print_range(probe, spec):
    start_s, end_s = spec.split(":", 1)
    start, end = int(start_s, 0), int(end_s, 0)
    print(f"\nFILE/RAM WORD RANGE {start:#x}:{end:#x}")
    for off in range(start & ~3, min(end, probe.body_end), 4):
        fv = probe.p.U(off)
        ra = probe.ram_for_off(off)
        rv = probe.ram_u32(ra)
        if fv == rv:
            kind = "same"
        elif probe.serialized_inside(fv) and rv == probe.ram_for_va(fv):
            kind = "self"
        elif 0x00700000 <= fv < 0x00800000 and 0x00100000 <= rv < 0x00700000:
            kind = "vtable"
        elif fv == 0 and rv and 0x10000 <= rv < len(probe.ram):
            kind = "new-ptr"
        else:
            kind = "changed"
        print(f"+{off:06X}  EE {ra:08X}  {fv:08X} -> {rv:08X}  {kind}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pck")
    ap.add_argument("ram")
    ap.add_argument("--base", required=True, type=lambda s: int(s, 0),
                    help="EE address corresponding to PCK file offset 0x80")
    own_dir = os.path.dirname(os.path.abspath(__file__))
    default_tools = (own_dir if os.path.isfile(os.path.join(own_dir, "mc3_pck_embed.py"))
                     else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
    ap.add_argument("--tool-dir", default=default_tools)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--details", action="store_true",
                    help="list zero-filled runtime pointers and vtable rewrites")
    ap.add_argument("--range", dest="word_range",
                    help="print aligned words as FILE_START:FILE_END, e.g. 0x80:0x200")
    ns = ap.parse_args(argv)
    Pck = load_pck_class(ns.tool_dir)
    pck = Pck(ns.pck)
    with open(ns.ram, "rb") as f:
        ram = f.read()
    report = Probe(pck, ram, ns.base).analyze()
    if ns.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
        if ns.details:
            print_details(report)
        if ns.word_range:
            print_range(Probe(pck, ram, ns.base), ns.word_range)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
