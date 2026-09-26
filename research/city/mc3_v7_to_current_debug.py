import hashlib
import json
import os
import struct

import mc3_v7_to_current as t

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
BASE = os.path.join(os.environ.get('MC3_WORK', '.'), "backups", "Fork City MODS 2",
                    "2026-09-20_forge_bisect", "before", "modcity_midnight_clear.pck")
FORGED = os.path.join(os.environ.get('MC3_WORK', '.'), 'output', 'la_forge3_local')
REFERENCE = os.path.join(os.environ.get('MC3_WORK', '.'), "backups", "LA_before_forge_2026-09-20",
                         "la729_local_components_20260906")
REPORT = os.path.join(TOOLS, "output", "city_v7_vif_relocation_20260906",
                      "la729_local_components_report_20260906.json")


def digest_city(pck, mesh):
    h = hashlib.sha256()
    for group in t.packet_entries_city(pck, mesh):
        for offset, qw, nv in group:
            h.update(struct.pack("<HH", qw, nv))
            h.update(pck.data[offset:offset + 16 * qw])
    return h.hexdigest()


def digest_mesh(path):
    h = hashlib.sha256()
    for group in t.packet_entries_mesh(path):
        for payload, qw, nv in group:
            h.update(struct.pack("<HH", qw, nv))
            h.update(payload)
    return h.hexdigest()


def main():
    mc2 = t.load_module("mc2_to_mc3", os.path.join(TOOLS, "experimental", "mc2_to_mc3.py"))
    hf = t.load_module("mc3_hood_fill", os.path.join(TOOLS, "mc3_hood_fill.py"))
    pck = mc2.Pck(BASE)
    active = []
    for hi, hood in enumerate(hf.hoods(pck)):
        for ui, (_label, meshes, _component, cd) in enumerate(hf.hood_uniques(pck, hi)):
            active.append({
                "hood": hood[0], "unique": ui, "cd": cd, "meshes": meshes,
                "centre": tuple(struct.unpack_from("<3f", pck.data, cd + 0x2C)),
            })

    report = json.load(open(REPORT, encoding="utf-8"))["component_results"]
    counts = {}
    problems = []
    for item in report:
        name = item["name"].lower() + ".mesh.pck"
        forged_path = os.path.join(FORGED, name)
        reference_path = os.path.join(REFERENCE, name)
        if not os.path.isfile(forged_path):
            continue
        replacement_shape = t.shape_mesh(t.packet_entries_mesh(forged_path))
        ref_digest = digest_mesh(reference_path) if os.path.isfile(reference_path) else None
        compatible = []
        for obj in active:
            for mesh in obj["meshes"]:
                groups = t.packet_entries_city(pck, mesh)
                if t.shape_city(groups) != replacement_shape:
                    continue
                d = sum((obj["centre"][i] - item["centre"][i]) ** 2 for i in range(3)) ** .5
                compatible.append((d, digest_city(pck, mesh) == ref_digest,
                                   obj["hood"], obj["unique"], mesh, obj["centre"]))
        compatible.sort()
        exact = [x for x in compatible if x[0] < .002]
        exact_ref = [x for x in exact if x[1]]
        key = (len(exact), len(exact_ref))
        counts[key] = counts.get(key, 0) + 1
        if len(exact) != 1 or len(exact_ref) != 1:
            problems.append({
                "name": name, "centre": item["centre"], "exact": exact[:8],
                "nearest": compatible[:5],
            })
    print("distribution (exact centre+shape, also old digest):")
    for key in sorted(counts):
        print(" ", key, counts[key])
    print("problems:", len(problems))
    with open("mc3_v7_to_current_debug.json", "w", encoding="utf-8") as stream:
        json.dump(problems, stream, indent=2)
        stream.write("\n")
    for item in problems[:25]:
        print(item["name"], "exact", len(item["exact"]), "nearest", item["nearest"][:2])


if __name__ == "__main__":
    main()
