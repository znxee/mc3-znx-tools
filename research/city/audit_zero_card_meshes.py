import csv
import importlib.util
import os
import sys

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
PLACE = TOOLS + r"\output\la729_place.tsv"
SRC = os.path.join(os.environ.get('MC2_PC_ASSETS', 'mc2_pc/assets_p'), 'city/losangeles/models')
ORIG = TOOLS + r"\output\la729_pck"
LOCAL = TOOLS + r"\output\city_v7_vif_relocation_20260906\la729_local_components_20260906"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


conv = load("mc2_to_mc3", TOOLS + r"\experimental\mc2_to_mc3.py")
vif = load("mc3_vifpos_zero_cards", TOOLS + r"\mc3_vifpos.py")
build = load("mc3_city_build_zero_cards", TOOLS + r"\mc3_city_build.py")


def box(points):
    return tuple(min(p[a] for p in points) for a in range(3)) + tuple(max(p[a] for p in points) for a in range(3))


def pck_box(path):
    pts = []
    for group in build.read_pck_groups(path):
        for payload, _qw, _nv in group:
            pts.extend(vif.decode_positions(payload))
    return box(pts)


with open(PLACE, encoding="utf-8") as f:
    lines = [line for line in f if not line.startswith("#")]
rows = list(csv.DictReader(lines, delimiter="\t"))
zeros = [r for r in rows if r["kind"] == "inst" and all(float(r[k]) == 0.0 for k in ("tx", "ty", "tz"))]

missing = []
bad = []
max_source_orig = 0.0
max_orig_local = 0.0
for row in zeros:
    stem = f'{row["name"]}_{row["lod"]}_{row["part"]}'
    paths = [os.path.join(SRC, stem + ".xmod"), os.path.join(ORIG, stem + ".mesh.pck"), os.path.join(LOCAL, stem + ".mesh.pck")]
    if not all(os.path.isfile(p) for p in paths):
        missing.append((stem, [p for p in paths if not os.path.isfile(p)]))
        continue
    pos, _uv, _adj, _strip = conv.read_xmod(paths[0])
    sb = box(pos)
    ob = pck_box(paths[1])
    lb = pck_box(paths[2])
    dso = max(abs(a - b) for a, b in zip(sb, ob))
    dol = max(abs(a - b) for a, b in zip(ob, lb))
    max_source_orig = max(max_source_orig, dso)
    max_orig_local = max(max_orig_local, dol)
    if dso > 0.1 or dol > 0.0:
        bad.append((stem, dso, dol, sb, ob, lb))

print(f"zero_rows={len(zeros)} checked={len(zeros)-len(missing)} missing={len(missing)} bad={len(bad)}")
print(f"max_source_vs_original={max_source_orig:.9f}")
print(f"max_original_vs_local={max_orig_local:.9f}")
for item in missing:
    print("MISSING", item)
for item in bad:
    print("BAD", item)
