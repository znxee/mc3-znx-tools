#!/usr/bin/env python3
"""Read NINS call counters from a PCSX2 savestate."""

from __future__ import annotations

import os
import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
MAGIC = b"NINS"
LABELS = (
    "instance:p2:g1:cpv",
    "instance:p8:g0:cpv",
    "instance:p4/p8:g2/g3:draw",
    "instance:p64:g4:cpv",
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()

    inject = load("mc3_inject_nins", Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
    ram = inject.ee_from_savestate(str(args.state))
    candidates = []
    start = 0
    while True:
        at = ram.find(MAGIC, start)
        if at < 0:
            break
        if at + 28 <= len(ram):
            version, size = struct.unpack_from("<II", ram, at + 4)
            if version == 1 and size == 28:
                candidates.append(at)
        start = at + 1
    if len(candidates) != 1:
        raise SystemExit(
            f"expected one NINS block, found {len(candidates)}: {candidates}"
        )
    at = candidates[0]
    calls = struct.unpack_from("<4I", ram, at + 12)
    print(json.dumps({
        "state": str(args.state),
        "nins_address": f"{at:08X}",
        "calls": dict(zip(LABELS, calls)),
        "active": any(calls),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
