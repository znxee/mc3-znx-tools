"""Report the byte budget of mc3boot modules enabled with '= 1'."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=11384)
    args = parser.parse_args()
    ini = (args.root / "mc3boot.ini").read_text(encoding="utf-8")
    names = re.findall(r"^([a-z_0-9]+\.mod)\s*=\s*1\s*$", ini,
                       flags=re.MULTILINE | re.IGNORECASE)
    total = 0
    missing = []
    for name in names:
        path = args.root / name
        if not path.is_file():
            missing.append(name)
            print(f"MISSING {name}")
            continue
        size = path.stat().st_size
        total += size
        print(f"{size:6d}  {name}")
    print(f"total={total} limit={args.limit} remaining={args.limit - total}")
    if missing or total > args.limit:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
