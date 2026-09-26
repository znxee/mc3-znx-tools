"""Port new VIF packets into a modern city without rebuilding its structure.

The current file keeps the PPF, textures, proxies, props, CellIndex and the
fixes made after v7. Only the VIF payloads of LA components are overwritten,
and only when centre, group count, packet count, QW and NV match exactly.
"""

import argparse
import importlib.util
import json
import os
import struct
import sys
from collections import defaultdict


DEFAULT_TOOLS = os.path.dirname(os.path.abspath(__file__))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def packet_entries_city(pck, mesh):
    groups = []
    group_count = struct.unpack_from("<H", pck.data, mesh + 8)[0]
    group_table = pck.F(struct.unpack_from("<I", pck.data, mesh + 0x10)[0])
    if not pck.inside(group_table):
        return []
    for group_index in range(group_count):
        list_va, packet_count = struct.unpack_from(
            "<IH", pck.data, group_table + 8 * group_index)
        packet_table = pck.F(list_va)
        group = []
        for packet_index in range(packet_count):
            payload_va, qw, nv = struct.unpack_from(
                "<IHH", pck.data, packet_table + 8 * packet_index)
            payload = pck.F(payload_va)
            if not pck.inside(payload) or payload + 16 * qw > len(pck.data):
                raise ValueError("city payload outside the file")
            group.append((payload, qw, nv))
        groups.append(group)
    return groups


def packet_entries_mesh(path):
    data = open(path, "rb").read()
    base = struct.unpack_from("<I", data, 0)[0]
    file_offset = lambda va: va - base + 0x80
    group_count = struct.unpack_from("<H", data, 0x88)[0]
    group_table = file_offset(struct.unpack_from("<I", data, 0x90)[0])
    groups = []
    for group_index in range(group_count):
        list_va, packet_count = struct.unpack_from(
            "<IH", data, group_table + 8 * group_index)
        packet_table = file_offset(list_va)
        group = []
        for packet_index in range(packet_count):
            payload_va, qw, nv = struct.unpack_from(
                "<IHH", data, packet_table + 8 * packet_index)
            payload = file_offset(payload_va)
            end = payload + 16 * qw
            if payload < 0x80 or end > len(data):
                raise ValueError("forged payload outside the file: %s" % path)
            group.append((data[payload:end], qw, nv))
        groups.append(group)
    return groups


def shape_city(groups):
    return [[(qw, nv) for _payload, qw, nv in group] for group in groups]


def shape_mesh(groups):
    return [[(qw, nv) for _payload, qw, nv in group] for group in groups]


def centre_key(values):
    return tuple(round(float(value), 3) for value in values)


def transplant(args):
    tools = os.path.abspath(args.tools)
    mc2 = load_module(
        "mc2_to_mc3", os.path.join(tools, "experimental", "mc2_to_mc3.py"))
    hf = load_module("mc3_hood_fill", os.path.join(tools, "mc3_hood_fill.py"))
    pck = mc2.Pck(args.base)
    original = bytes(pck.data)

    report = json.load(open(args.report, encoding="utf-8"))
    by_centre = defaultdict(list)
    for item in report["component_results"]:
        by_centre[centre_key(item["centre"])].append(item["name"])

    forged_files = {
        name.lower(): os.path.join(args.forged, name)
        for name in os.listdir(args.forged)
        if name.lower().endswith(".pck")
    }
    forged_cache = {}
    candidates = []
    ignored = []

    for hood_index, hood in enumerate(hf.hoods(pck)):
        for unique_index, (_label, meshes, _component, cd) in enumerate(
                hf.hood_uniques(pck, hood_index)):
            centre = centre_key(struct.unpack_from("<3f", pck.data, cd + 0x2C))
            names = by_centre.get(centre, [])
            if len(names) != 1:
                ignored.append({
                    "hood": hood[0], "unique": unique_index,
                    "centre": centre, "reason": "centre_not_unique",
                    "source_names": names,
                })
                continue
            filename = names[0].lower() + ".mesh.pck"
            forged_path = forged_files.get(filename)
            if not forged_path:
                ignored.append({
                    "hood": hood[0], "unique": unique_index,
                    "centre": centre, "reason": "no_forged_file",
                    "source_names": names,
                })
                continue
            replacement = forged_cache.setdefault(
                forged_path, packet_entries_mesh(forged_path))
            replacement_shape = shape_mesh(replacement)
            compatible = []
            for mesh in meshes:
                current = packet_entries_city(pck, mesh)
                if shape_city(current) == replacement_shape:
                    compatible.append((mesh, current))
            if len(compatible) != 1:
                ignored.append({
                    "hood": hood[0], "unique": unique_index,
                    "centre": centre, "reason": "mesh_shape_not_unique",
                    "source_names": names, "mesh_count": len(meshes),
                    "compatible_meshes": len(compatible),
                    "replacement_shape": replacement_shape,
                })
                continue
            mesh, current = compatible[0]
            candidates.append((hood[0], unique_index, centre, filename,
                               mesh, current, replacement))

    expected = {
        item["name"].lower() + ".mesh.pck"
        for item in report["component_results"]
    }
    selected = {item[3] for item in candidates}
    duplicate_names = sorted(name for name in selected if sum(
        1 for item in candidates if item[3] == name) != 1)
    missing = sorted((expected & set(forged_files)) - selected)

    changed_ranges = []
    changed_bytes = 0
    for _hood, _unique, _centre, _filename, _mesh, current, replacement in candidates:
        for current_group, replacement_group in zip(current, replacement):
            for (offset, qw, nv), (payload, replacement_qw, replacement_nv) in zip(
                    current_group, replacement_group):
                if (qw, nv) != (replacement_qw, replacement_nv):
                    raise AssertionError("shape changed after validation")
                old = bytes(pck.data[offset:offset + 16 * qw])
                if old != payload:
                    changed_bytes += sum(a != b for a, b in zip(old, payload))
                    changed_ranges.append((offset, offset + 16 * qw))
                    pck.data[offset:offset + 16 * qw] = payload

    result = {
        "base": os.path.abspath(args.base),
        "forged": os.path.abspath(args.forged),
        "report": os.path.abspath(args.report),
        "city_bytes": len(pck.data),
        "components_selected": len(candidates),
        "component_names_selected": len(selected),
        "changed_packet_ranges": len(changed_ranges),
        "changed_bytes": changed_bytes,
        "missing_forged_components": missing,
        "duplicate_selected_names": duplicate_names,
        "ignored_components": ignored,
    }
    print("components selected: %d (%d names)" %
          (len(candidates), len(selected)))
    print("payloads changed: %d; differing bytes: %d" %
          (len(changed_ranges), changed_bytes))
    print("forged components without a safe target: %d" % len(missing))
    if missing:
        for name in missing[:30]:
            print("  -", name)
    if duplicate_names:
        print("ERROR: name selected more than once")
        for name in duplicate_names:
            print("  -", name)

    # Guarantees: the file does not change size and no structural byte is touched.
    if len(pck.data) != len(original):
        raise AssertionError("the transplant changed the PCK size")
    allowed = bytearray(len(original))
    for start, end in changed_ranges:
        allowed[start:end] = b"\x01" * (end - start)
    illegal = [i for i, (a, b) in enumerate(zip(original, pck.data))
               if a != b and not allowed[i]]
    if illegal:
        raise AssertionError("a byte outside the payloads was changed: 0x%X" % illegal[0])
    if duplicate_names:
        raise SystemExit(2)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    if args.output:
        if missing and not args.allow_partial:
            raise SystemExit("refused: use --allow-partial to write a partial batch")
        with open(args.output, "wb") as stream:
            stream.write(pck.data)
        print("written:", os.path.abspath(args.output))
    else:
        print("analysis only; pass --output to write")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--forged", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--tools", default=DEFAULT_TOOLS)
    parser.add_argument("--output")
    parser.add_argument("--json")
    parser.add_argument("--allow-partial", action="store_true")
    return transplant(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
