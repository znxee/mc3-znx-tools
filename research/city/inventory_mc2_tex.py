from __future__ import annotations

import os
import importlib.util
import struct
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'output', 'mc2_losangeles/texture'))
TOOL = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'mc3_tex.py'))


def load_tool():
    spec = importlib.util.spec_from_file_location("mc3_tex", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    texmod = load_tool()
    dims = Counter()
    formats = Counter()
    levels = Counter()
    triples = Counter()
    extras = Counter()
    largest = []
    bad = []
    for path in sorted(ROOT.glob("*.tex")):
        t = texmod.read_tex(str(path))
        dims[t.width, t.height] += 1
        formats[t.fmt] += 1
        levels[t.mipmaps] += 1
        triples[t.width, t.height, t.fmt, t.mipmaps] += 1
        extras[t.f8, t.f10, t.f12] += 1
        raw = path.read_bytes()
        expect = 14 + t.colors * 4 + sum(len(x[2]) for x in t.levels)
        if expect != len(raw):
            bad.append((path.name, expect, len(raw)))
        largest.append((len(raw), path.name, t.width, t.height, t.fmt, t.mipmaps))
    print("files", sum(formats.values()), "bad", bad[:3])
    print("formats", formats)
    print("mipmap counts", levels)
    print("dims", dims)
    print("extras", extras)
    print("triples")
    for key, n in triples.most_common():
        print(n, key)
    print("largest")
    for x in sorted(largest, reverse=True)[:30]:
        print(x)


if __name__ == "__main__":
    main()
