"""Temporary census for rmcCarModelType+0x33C..+0x1710."""

from __future__ import annotations

import collections
import importlib.util
import os
import struct
import sys


TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_shader_transplant.py')
ROOT = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/vehicle')
START = 0x33C
END = 0x1714  # include the full dword beginning at the user's last byte

spec = importlib.util.spec_from_file_location("mc3_shader_transplant", TOOL)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def runs(values):
    out = []
    begin = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[begin]:
            out.append((START + begin * 4, START + i * 4 - 1,
                        values[begin], i - begin))
            begin = i
    return out


def main():
    cars = []
    per_offset = [collections.Counter() for _ in range((END - START) // 4)]
    patterns = collections.Counter()
    failures = []
    for entry in sorted(os.scandir(ROOT), key=lambda x: x.name.lower()):
        if not entry.is_dir() or not entry.name.startswith("vp_"):
            continue
        path = os.path.join(entry.path, entry.name + ".pck")
        if not os.path.isfile(path):
            continue
        try:
            p = module.Pck(path)
            model = p.pointer(0x88)
            values = [p.u32(model + off) for off in range(START, END, 4)]
            for counter, value in zip(per_offset, values):
                counter[value] += 1
            signature = tuple((a - START, b - START, value)
                              for a, b, value, _count in runs(values)
                              if value != 0xCDCDCDCD)
            patterns[signature] += 1
            stats = collections.Counter(
                "CD" if v == 0xCDCDCDCD else
                "FFFF" if v == 0xFFFFFFFF else
                "ZERO" if v == 0 else "OTHER"
                for v in values)
            others = [(START + 4 * i, v) for i, v in enumerate(values)
                      if v not in (0xCDCDCDCD, 0xFFFFFFFF, 0)]
            cars.append((entry.name, stats, others, signature))
        except Exception as exc:
            failures.append((entry.name, str(exc)))

    print("cars", len(cars), "failures", failures)
    target = next(item for item in cars if item[0] == "vp_350z_04")
    print("350Z runs")
    p350 = module.Pck(os.path.join(ROOT, "vp_350z_04", "vp_350z_04.pck"))
    m350 = p350.pointer(0x88)
    for row in runs([p350.u32(m350 + off) for off in range(START, END, 4)]):
        print(" +%04X..+%04X value=%08X dwords=%d" % row)

    print("pattern count", len(patterns))
    for signature, count in patterns.most_common(20):
        print(" pattern cars", count)
        for a, b, value in signature[:30]:
            print("   +%04X..+%04X %08X" % (a, b, value))
        if len(signature) > 30:
            print("   ...", len(signature), "runs")

    print("cars with OTHER values")
    for name, stats, others, _sig in cars:
        if others:
            print(name, dict(stats), [("+%04X" % o, "%08X" % v)
                                      for o, v in others[:40]],
                  "..." if len(others) > 40 else "")

    print("offsets not constant CD")
    for i, counter in enumerate(per_offset):
        if len(counter) > 1 or next(iter(counter)) != 0xCDCDCDCD:
            common = " ".join("%08X:%d" % item for item in counter.most_common(8))
            print("+%04X %s" % (START + i * 4, common))

    print("special matrix/id sets")
    for name, _stats, _others, _sig in cars:
        path = os.path.join(ROOT, name, name + ".pck")
        p = module.Pck(path)
        model = p.pointer(0x88)
        special_count = p.u32(model + 0x32C)
        if special_count == 0:
            continue
        skeleton = p.pointer(model + 0xDC)
        node_count = p.u16(skeleton)
        node_array = p.pointer(skeleton + 8)
        by_id = {}
        for i in range(node_count):
            node = node_array + 0x44 * i
            node_id = p.u16(node + 0x38)
            name_off = p.pointer(node + 0x0C)
            node_name = p.cstr(name_off) if name_off is not None else None
            by_id[node_id] = (i, node_name)
        ids = [p.u32(model + 0x1590 + 4 * i) for i in range(special_count)]
        matrices = [struct.unpack_from("<12f", p.data,
                    model + 0x390 + 0x30 * i) for i in range(special_count)]
        print(name, "count", special_count, "ids/nodes",
              [(value, by_id.get(value)) for value in ids],
              "translations", [tuple(round(v, 5) for v in m[9:12])
                               for m in matrices],
              "+1710", p.u32(model + 0x1710))


if __name__ == "__main__":
    main()
