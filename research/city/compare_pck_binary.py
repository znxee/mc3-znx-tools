#!/usr/bin/env python3
"""Summarize byte and aligned-word differences between same-layout PCK files."""

import argparse
import collections
import hashlib
import struct
from pathlib import Path


def runs(indices):
    if not indices:
        return []
    out = []
    first = previous = indices[0]
    for value in indices[1:]:
        if value != previous + 1:
            out.append((first, previous + 1))
            first = value
        previous = value
    out.append((first, previous + 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--limit", type=int, default=160)
    args = ap.parse_args()
    a = args.old.read_bytes()
    b = args.new.read_bytes()
    if len(a) != len(b):
        print(f"sizes: {len(a)} -> {len(b)}")
    n = min(len(a), len(b))
    changed = [i for i in range(n) if a[i] != b[i]]
    spans = runs(changed)
    print("old", hashlib.sha256(a).hexdigest(), len(a))
    print("new", hashlib.sha256(b).hexdigest(), len(b))
    print(f"changed bytes={len(changed)} runs={len(spans)} first={changed[0] if changed else None:#x} last={changed[-1] if changed else None:#x}")
    lengths = collections.Counter(end - start for start, end in spans)
    print("run lengths", lengths.most_common(20))
    for start, end in spans[:args.limit]:
        lo = start & ~3
        hi = (end + 3) & ~3
        old_words = [f"{struct.unpack_from('<I', a, p)[0]:08X}" for p in range(lo, min(hi, n), 4)]
        new_words = [f"{struct.unpack_from('<I', b, p)[0]:08X}" for p in range(lo, min(hi, n), 4)]
        print(f"{start:08X}..{end:08X} ({end-start:3d})  " +
              " ".join(old_words) + " -> " + " ".join(new_words))


if __name__ == "__main__":
    main()
