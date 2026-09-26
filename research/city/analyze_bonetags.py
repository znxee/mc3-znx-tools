import os
from collections import Counter
from pathlib import Path
import importlib.util

MOD = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_pck_embed.py'))
spec = importlib.util.spec_from_file_location("mc3_pck_embed", MOD)
mc3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc3)

ROOT = Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/vehicle'))
FILES = sorted(p for p in ROOT.glob("vp_*/*.pck")
               if p.stem.lower() == p.parent.name.lower())


def target(p, off):
    va = p.U(off)
    if not va:
        return None
    dst = p.F(va)
    return dst if p.inside(dst) else None


tables = 0
entries_total = 0
counts = Counter()
chain_lengths = Counter()
arm_l0 = []
errors = []

for path in FILES:
    p = mc3.Pck(str(path))
    model_type = target(p, 0x88)
    shader_model = target(p, model_type + 0xD8) if model_type is not None else None
    bony = target(p, shader_model + 0x80) if shader_model is not None else None
    table = target(p, bony) if bony is not None else None
    if table is None:
        counts["no bone-tag table"] += 1
        continue

    bucket_count = p.U(table + 8)
    stored_count = p.U(table + 0x0C)
    buckets = target(p, table + 0x10)
    if not bucket_count or bucket_count > 4096 or buckets is None:
        errors.append((path.parent.name, "bad table", table, bucket_count, buckets))
        continue

    tables += 1
    seen = set()
    found = []
    for bucket in range(bucket_count):
        entry = target(p, buckets + 4 * bucket)
        chain = 0
        while entry is not None:
            if entry in seen:
                errors.append((path.parent.name, "cycle/duplicate", entry))
                break
            seen.add(entry)
            chain += 1
            name_off = target(p, entry)
            name = p.cstr(name_off) if name_off is not None else "<bad-name>"
            value = p.U(entry + 4)
            found.append((name, value, entry, bucket))
            entry = target(p, entry + 8)
        chain_lengths[chain] += 1

    counts[(bucket_count, stored_count)] += 1
    entries_total += len(found)
    if len(found) != stored_count:
        errors.append((path.parent.name, "count mismatch", stored_count, len(found)))
    for name, value, entry, bucket in found:
        if name.lower() == "arm_l0":
            arm_l0.append((path.parent.name, value, value - 1, entry, bucket))

print("packages", len(FILES), "tables", tables, "entries", entries_total)
print("table shapes", counts.most_common(30))
print("chain lengths", sorted(chain_lengths.items()))
print("arm_l0", arm_l0)
print("errors", errors)
