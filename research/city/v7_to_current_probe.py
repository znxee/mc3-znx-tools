import hashlib
import importlib.util
import os
import struct
import sys
from collections import defaultdict

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
BASE = os.path.join(os.environ.get('MC3_WORK', '.'), "backups", "Fork City MODS 2",
                    "2026-09-20_forge_bisect", "before", "losangeles_midnight_clear.pck")
REFERENCE = os.path.join(os.environ.get('MC3_WORK', '.'), "backups", "LA_before_forge_2026-09-20",
                         "la729_local_components_20260906")
FORGED = os.path.join(os.environ.get('MC3_WORK', '.'), 'output', 'la_forge3_local')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mc2 = load("mc2_to_mc3", os.path.join(TOOLS, "experimental", "mc2_to_mc3.py"))
hf = load("mc3_hood_fill", os.path.join(TOOLS, "mc3_hood_fill.py"))


def packets_from_city(p, mesh):
    groups = []
    count = struct.unpack_from("<H", p.data, mesh + 8)[0]
    table = p.F(struct.unpack_from("<I", p.data, mesh + 0x10)[0])
    for group_index in range(count):
        list_va, packet_count = struct.unpack_from("<IH", p.data, table + 8 * group_index)
        packet_table = p.F(list_va)
        group = []
        for packet_index in range(packet_count):
            payload_va, qw, nv = struct.unpack_from(
                "<IHH", p.data, packet_table + 8 * packet_index)
            payload = p.F(payload_va)
            group.append((qw, nv, bytes(p.data[payload:payload + 16 * qw])))
        groups.append(group)
    return groups


def packets_from_mesh(path):
    data = open(path, "rb").read()
    base = struct.unpack_from("<I", data, 0)[0]
    f = lambda va: va - base + 0x80
    count = struct.unpack_from("<H", data, 0x88)[0]
    table = f(struct.unpack_from("<I", data, 0x90)[0])
    groups = []
    for group_index in range(count):
        list_va, packet_count = struct.unpack_from("<IH", data, table + 8 * group_index)
        packet_table = f(list_va)
        group = []
        for packet_index in range(packet_count):
            payload_va, qw, nv = struct.unpack_from(
                "<IHH", data, packet_table + 8 * packet_index)
            payload = f(payload_va)
            group.append((qw, nv, data[payload:payload + 16 * qw]))
        groups.append(group)
    return groups


def signature(groups):
    h = hashlib.sha256()
    h.update(struct.pack("<I", len(groups)))
    for group in groups:
        h.update(struct.pack("<I", len(group)))
        for qw, nv, payload in group:
            h.update(struct.pack("<HH", qw, nv))
            h.update(payload)
    return h.hexdigest()


def main():
    refs = defaultdict(list)
    reference_files = [f for f in os.listdir(REFERENCE) if f.lower().endswith(".pck")]
    forged_files = {f.lower(): f for f in os.listdir(FORGED) if f.lower().endswith(".pck")}
    for filename in reference_files:
        refs[signature(packets_from_mesh(os.path.join(REFERENCE, filename)))].append(filename)

    p = mc2.Pck(BASE)
    active = []
    for hood_index, hood in enumerate(hf.hoods(p)):
        for unique_index, (_name, meshes, _component, _cd) in enumerate(
                hf.hood_uniques(p, hood_index)):
            for lod_index, mesh in enumerate(meshes):
                active.append((hood[0], unique_index, lod_index, mesh,
                               signature(packets_from_city(p, mesh))))

    exact = ambiguous = missing = forged = shape_ok = 0
    used = set()
    examples = []
    for hood, unique, lod, mesh, sig in active:
        names = refs.get(sig, [])
        if len(names) == 1:
            exact += 1
            name = names[0]
            used.add(name.lower())
            if name.lower() in forged_files:
                forged += 1
                a = packets_from_city(p, mesh)
                b = packets_from_mesh(os.path.join(FORGED, forged_files[name.lower()]))
                if [[(x[0], x[1]) for x in g] for g in a] == [
                        [(x[0], x[1]) for x in g] for g in b]:
                    shape_ok += 1
                elif len(examples) < 20:
                    examples.append((hood, unique, lod, name, "shape mismatch"))
        elif len(names) > 1:
            ambiguous += 1
            if len(examples) < 20:
                examples.append((hood, unique, lod, names[:4], "ambiguous"))
        else:
            missing += 1
            if len(examples) < 20:
                examples.append((hood, unique, lod, hex(mesh), "no reference"))

    forged_names = set(forged_files)
    print("active meshes:", len(active))
    print("reference files:", len(reference_files), "unique signatures:", len(refs))
    print("exact:", exact, "ambiguous:", ambiguous, "missing:", missing)
    print("exact with forged replacement:", forged, "shape compatible:", shape_ok)
    print("forged files matched:", len(used & forged_names), "/", len(forged_names))
    print("unmatched forged sample:", sorted(forged_names - used)[:20])
    print("examples:")
    for item in examples:
        print(" ", item)


if __name__ == "__main__":
    main()
