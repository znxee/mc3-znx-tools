"""Put mc3boot.ini in a cave-safe configuration for city texture testing."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ini", type=Path)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    text = args.ini.read_text(encoding="utf-8")
    old = "peds.mod = 1"
    new = "peds.mod = 0"
    if old not in text:
        raise SystemExit(f"expected line not found: {old}")
    if text.count(old) != 1:
        raise SystemExit(f"expected one line, found {text.count(old)}")
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    if args.backup.exists():
        raise FileExistsError(f"refusing to overwrite {args.backup}")
    shutil.copy2(args.ini, args.backup)
    args.ini.write_text(text.replace(old, new), encoding="utf-8")
    check = args.ini.read_text(encoding="utf-8")
    if old in check or new not in check:
        raise AssertionError("mc3boot.ini verification failed")
    print(f"backup={args.backup}")
    print(f"changed: {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
