"""Summarize frontend city boot progress and all TLB-miss spellings."""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path


def main() -> int:
    for item in sys.argv[1:]:
        path = Path(item)
        text = path.read_text(encoding="utf-8", errors="replace")
        stamps = [float(a) + float(b) / 10000.0
                  for a, b in re.findall(r"^\[\s*(\d+),(\d+)\]", text, re.MULTILINE)]
        sessions = collections.Counter(re.findall(r"SES:\s+([0-9A-Fa-f]{8})", text))
        misses = re.findall(
            r"TLB Miss, pc=0x([0-9A-Fa-f]+) addr=0x([0-9A-Fa-f]+) \[(\w+)\]", text
        )
        by_pc = collections.Counter(pc.lower() for pc, _, _ in misses)
        by_addr = collections.Counter(addr.lower() for _, addr, _ in misses)
        exceptions = [line for line in text.splitlines()
                      if "Exception" in line or "cpuTlbMiss" in line]
        print(path)
        print(f"  span={max(stamps) if stamps else 0:.4f}s sessions={dict(sessions)}")
        print(f"  TLB Miss={len(misses)} by_pc={by_pc.most_common(16)}")
        print(f"  TLB addr={by_addr.most_common(16)}")
        print(f"  Exception/cpuTlbMiss={len(exceptions)}")
        for line in exceptions[:20]:
            print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
