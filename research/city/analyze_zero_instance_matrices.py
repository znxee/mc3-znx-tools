import os
import importlib.util
import struct
import sys


STATE = os.path.join(os.environ.get('MC3_PCSX2_DATA', 'PCSX2'), 'sstates/SLUS-21355 (B3FD5361).01.p2s')
PCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'output', 'losangeles_la_v9_cadeia_local.pck')
TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inject = load("mc3_inject_local", os.path.join(os.environ.get("MC3BOOT", "mc3boot"), "mc3_inject.py"))
mc2 = load("mc2_to_mc3", TOOLS + r"\experimental\mc2_to_mc3.py")

ram = inject.ee_from_savestate(STATE)
pck = mc2.Pck(PCK)


def ru32(addr):
    return struct.unpack_from("<I", ram, addr & 0x1FFFFFF)[0]


def rf12(addr):
    return struct.unpack_from("<12f", ram, addr & 0x1FFFFFF)


city = ru32(0x00615B40)
nh = ru32(city + 0x14)
rhoods = ru32(city + 0x18)
fnh = pck.U(0x80 + 0x14)
fhoods = pck.F(pck.U(0x80 + 0x18))

print(f"city={city:08X} hoods={nh} runtime_hoods={rhoods:08X} file_hoods={fhoods:08X}")
print(f"file_hood_count={fnh}")

total = 0
zeros = []
for h in range(min(nh, fnh)):
    re = rhoods + 20 * h
    fe = fhoods + 20 * h
    rn = ru32(re + 0x0C)
    ra = ru32(re + 0x10)
    fn = pck.U(fe + 0x0C)
    fva = pck.U(fe + 0x10)
    fa = pck.F(fva)
    print(f"hood {h:02d}: count runtime/file={rn}/{fn} arrays={ra:08X}/{fva:08X} delta={ra-fva:+#x}")
    for i in range(min(rn, fn)):
        rr = ra + 0x60 * i
        fr = fa + 0x60 * i
        rm = rf12(rr + 0x20)
        fm = struct.unpack_from("<12f", pck.data, fr + 0x20)
        if all(x == 0.0 for x in rm):
            zeros.append((h, i, rr, ru32(rr + 0x0C), fr, pck.U(fr + 0x0C), fm))
        total += 1

print(f"instances={total} runtime_all_zero={len(zeros)}")
for h, i, rr, rt, fr, ft, fm in zeros:
    print(
        f"ZERO h={h:02d} i={i:04d} runtime={rr:08X} type={rt:08X} "
        f"file_off={fr:08X} file_type={ft:08X} "
        f"file_T=({fm[9]:.4f},{fm[10]:.4f},{fm[11]:.4f}) "
        f"file_basis=({fm[0]:.3f},{fm[1]:.3f},{fm[2]:.3f};"
        f"{fm[3]:.3f},{fm[4]:.3f},{fm[5]:.3f};"
        f"{fm[6]:.3f},{fm[7]:.3f},{fm[8]:.3f})"
    )
