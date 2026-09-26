import os
import struct
import sys
from pathlib import Path

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, os.environ.get("MC3BOOT", "mc3boot"))

import mc3_inject
import mc3_mkmod


HOOKS = (
    0x0024C42C,
    0x0024C47C,
    0x0024C508,
    0x002570B4,
    0x00257104,
    0x00257190,
)


def word(ram, address):
    return struct.unpack_from("<I", ram, address & 0x1FFFFFF)[0]


def jal_target(pc, instruction):
    if instruction >> 26 != 3:
        return None
    return (((pc + 4) & 0xF0000000) | ((instruction & 0x03FFFFFF) << 2))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_no_cpv_state.py STATE.p2s")
    ram = mc3_inject.ee_from_savestate(sys.argv[1])
    targets = []
    for address in HOOKS:
        instruction = word(ram, address)
        target = jal_target(address, instruction)
        targets.append(target)
        print(f"{address:08X}: {instruction:08X} -> " +
              (f"{target:08X}" if target is not None else "not jal"))

    unique = {target for target in targets if target is not None}
    if len(unique) != 1:
        raise SystemExit(f"FAIL: expected one handler target, got {unique}")
    base = unique.pop()

    mod_path = TOOLKIT / "output" / "mods" / "city_no_cpv_test.mod"
    mod_bytes = mod_path.read_bytes()
    expected, entry = mc3_mkmod.relocar(mod_bytes, base)
    actual = ram[base & 0x1FFFFFF:(base & 0x1FFFFFF) + len(expected)]
    print(f"handler/base: {base:08X}; entry +{entry:X}")
    print(f"relocated code bytes: {len(expected)}")
    print("code match:", "YES" if actual == expected else "NO")
    if actual != expected:
        for i, (a, b) in enumerate(zip(actual, expected)):
            if a != b:
                print(f"first mismatch +{i:X}: RAM {a:02X}, expected {b:02X}")
                break
        raise SystemExit(1)


if __name__ == "__main__":
    main()
