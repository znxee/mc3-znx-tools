import os
from collections import Counter
from pathlib import Path
import importlib.util
import math
import struct

MOD = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_pck_embed.py'))
spec = importlib.util.spec_from_file_location("mc3_pck_embed", MOD)
mc3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc3)

ROOT = Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/vehicle'))
FILES = sorted(p for p in ROOT.glob("vp_*/*.pck") if p.stem.lower() == p.parent.name.lower())
IDENTITY = struct.pack("<12f", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)


def load(path):
    p = mc3.Pck(str(path))
    obj = p.F(p.U(0x88))
    nodes = p.skel_nodes()
    names = {n[0]: n[1] for n in nodes}
    return path, p, obj, nodes, names


def target(p, word_off):
    va = p.U(word_off)
    if not va:
        return None
    off = p.F(va)
    return off if p.inside(off) else None


rows = [load(path) for path in FILES]
print("files", len(rows))

print("\nBLOWER MATRICES +31C0[24]")
matrix_total = identity_total = active_identity = 0
nonidentity = []
for path, p, obj, nodes, names in rows:
    blower_count = p.U(obj + 0x3188)
    for i in range(24):
        raw = bytes(p.data[obj + 0x31C0 + 48*i:obj + 0x31C0 + 48*(i+1)])
        matrix_total += 1
        if raw == IDENTITY:
            identity_total += 1
            if i < blower_count:
                active_identity += 1
        elif len(nonidentity) < 20:
            nonidentity.append((path.parent.name, i, struct.unpack("<12f", raw)))
print("identity", identity_total, "/", matrix_total,
      "active identity", active_identity, "nonidentity", nonidentity)

print("\nPOPUP HEADLIGHT +3640")
angle_dist = Counter()
angle_cars = []
for path, p, obj, nodes, names in rows:
    raw = p.U(obj + 0x3640)
    angle_dist[raw] += 1
    if raw != 0xCDCDCDCD:
        angle_cars.append((path.parent.name, struct.unpack_from("<f", p.data, obj + 0x3640)[0]))
print("raw", [("%08X" % k, v) for k, v in angle_dist.most_common()])
print("cars", angle_cars)

print("\nPOPUP SPOILER +36DC targets, +3720 matrices")
popup = []
for path, p, obj, nodes, names in rows:
    root_id = p.U(obj + 0x2DC0)
    if root_id == 0xFFFFFFFF:
        continue
    root_name = names.get(root_id, "")
    targets = [struct.unpack_from("<3f", p.data, obj + 0x36DC + 12*i) for i in range(5)]
    matrices = [struct.unpack_from("<12f", p.data, obj + 0x3720 + 48*i) for i in range(5)]
    popup.append((path.parent.name, root_id, root_name, targets,
                  [(m[9], m[10], m[11]) for m in matrices]))
for item in popup:
    print(item)
print("popup cars", len(popup))

print("\nWHEEL RECORDS +3810/+3814")
count_dist = Counter()
layout_dist = Counter()
wheel_name_fields = [Counter(), Counter(), Counter()]
wheel_errors = []
for path, p, obj, nodes, names in rows:
    count = p.U(obj + 0x3814)
    count_dist[count] += 1
    base = target(p, obj + 0x3810)
    if not count:
        if base is not None:
            wheel_errors.append((path.parent.name, "zero count with pointer", base))
        continue
    if base is None or base + 28*count > len(p.data):
        wheel_errors.append((path.parent.name, "bad record pointer", base, count))
        continue
    refs = []
    for array_off in (0x3824, 0x3844):
        side = []
        for i in range(8):
            q = target(p, obj + array_off + 4*i)
            if q is None:
                side.append(None)
            elif (q - base) % 28 == 0 and 0 <= (q - base)//28 < count:
                side.append((q - base)//28)
            else:
                side.append("bad:%X" % q)
        refs.append(tuple(side))
    layout_dist[(count, refs[0], refs[1])] += 1
    for i in range(count):
        rec = base + 28*i
        for k, field in enumerate((4, 8, 12)):
            node = p.U(rec + field)
            label = "<FFFF>" if node == 0xFFFFFFFF else names.get(node, "<bad:%d>" % node)
            wheel_name_fields[k][label] += 1
print("counts", sorted(count_dist.items()))
print("layouts")
for key, n in layout_dist.most_common():
    print(" ", n, key)
print("node fields +04/+08/+0C")
for c in wheel_name_fields:
    print(c.most_common(24))
print("errors", wheel_errors)

print("\nATBITSET +381C/+3820")
bit_meta = Counter()
bit_names = Counter()
set_counts = Counter()
sample_bits = None
for path, p, obj, nodes, names in rows:
    words = p.U16(obj + 0x3820)
    bits = p.U16(obj + 0x3822)
    bit_meta[(words, bits, len(nodes))] += 1
    data = target(p, obj + 0x381C)
    if data is None:
        continue
    active = []
    for bit in range(min(bits, words * 32)):
        if p.U(data + 4*(bit//32)) & (1 << (bit & 31)):
            active.append(bit)
            bit_names[names.get(bit, "<unnamed>")] += 1
    set_counts[len(active)] += 1
    if path.parent.name.lower() == "vp_350z_04":
        sample_bits = [(i, names.get(i, "")) for i in active]
print("metadata", bit_meta)
print("set counts", sorted(set_counts.items()))
print("top names", bit_names.most_common(40))
print("350z", sample_bits)

print("\nTAIL POINTERS +3864..+3888")
for field in (0x3864, 0x3868, 0x386C, 0x3870, 0x3874, 0x3888):
    heads = Counter()
    valid = null = 0
    for path, p, obj, nodes, names in rows:
        q = target(p, obj + field)
        if q is None:
            null += p.U(obj + field) == 0
            continue
        valid += 1
        heads[p.U(q)] += 1
    print(hex(field), "valid", valid, "null", null,
          [("%08X" % k, v) for k, v in heads.most_common(12)])

print("\nLOD/DISTANCE FLOATS +3878[4]")
for i in range(4):
    vals = Counter(round(struct.unpack_from("<f", p.data, obj + 0x3878 + 4*i)[0], 6)
                   for path, p, obj, nodes, names in rows)
    print(i, vals.most_common(20))

print("\nOCCLUDE DATASET +3888")
occ_counts = Counter()
occ_heads = Counter()
for path, p, obj, nodes, names in rows:
    q = target(p, obj + 0x3888)
    if q is None:
        occ_counts["null"] += 1
        continue
    vals = tuple(p.U(q + 4*i) for i in range(4))
    occ_counts[vals[1]] += 1
    occ_heads[vals] += 1
print("counts", occ_counts)
for vals, n in occ_heads.most_common(16):
    print(n, tuple("%08X" % v for v in vals))

target_row = next(r for r in rows if r[0].parent.name.lower() == "vp_350z_04")
path, p, obj, nodes, names = target_row
print("\n350Z POINTER TARGET HEADS")
for field in (0x3810, 0x3818, 0x381C, 0x3824, 0x3844, 0x3864, 0x3868, 0x386C, 0x3870, 0x3874, 0x3888):
    va = p.U(obj + field)
    q = target(p, obj + field)
    raw = bytes(p.data[q:q+48]).hex(" ") if q is not None else ""
    print("+%04X va=%08X file=%s %s" % (field, va, "-" if q is None else "%X" % q, raw))


def cstr(p, off):
    if not p.inside(off):
        return ""
    end = off
    while end < len(p.data) and p.data[end] not in (0, 0xCD):
        end += 1
    return bytes(p.data[off:end]).decode("latin1", "replace")


def shader_name(p, off):
    if off is None:
        return None
    for rel in range(0, 0x180, 4):
        va = p.U(off + rel)
        dst = p.F(va)
        if p.inside(dst):
            name = cstr(p, dst)
            if name.endswith(".shadert"):
                return rel, name
    return None


print("\n350Z DIRECT TAIL SHADERS")
for field in (0x3868, 0x386C, 0x3870, 0x3874):
    q = target(p, obj + field)
    print("+%04X file=%s shader=%r" %
          (field, "-" if q is None else "%X" % q, shader_name(p, q)))

print("\nTAIL SHADER NAMES ACROSS 117 PACKAGES")
for field in (0x3868, 0x386C, 0x3870, 0x3874):
    names_seen = Counter()
    details = []
    for path, pp, oo, nodes, names in rows:
        found = shader_name(pp, target(pp, oo + field))
        names_seen[str(found)] += 1
        if field == 0x3870 and found != (40, 'rider_stealth.shadert'):
            details.append((path.parent.name, found))
    print(hex(field), names_seen.most_common(30))
    if details:
        print(" exceptions", details)

print("\nRESERVED +3644..+36DB NON-PADDING WORDS")
word_patterns = []
for rel in range(0x3644, 0x36DC, 4):
    vals = Counter(pp.U(oo + rel) for path, pp, oo, nodes, names in rows)
    non_pad = sum(n for v, n in vals.items() if v not in (0, 0xCDCDCDCD, 0xFFFFFFFF))
    if non_pad:
        word_patterns.append((rel, non_pad,
                              [("%08X" % v, n) for v, n in vals.most_common(8)]))
print("non-padding offsets", len(word_patterns))
for row in word_patterns:
    print("+%04X" % row[0], row[1], row[2])
