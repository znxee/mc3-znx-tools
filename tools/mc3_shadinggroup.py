"""
mc3_shadinggroup.py  -  extracts the index->shader table (shadinggroup) from an
MC3 vehicle container (PS2 .pck or PSP .psppck).

Usage:
    python mc3_shadinggroup.py "path/vp_corvettez06_03.pck"

Use it to find out which index to put in material_index_override in
mesh_psptops2_v1.py (the shader order changes from car to car).

How it works: the container owns an rmcShaderGroup with the stable layout
``[token][slots*][u16 count][u16 capacity][local_head*]``.  Each slot points to
a shader resource carrying a ".shadert" name.  The ORDER of that slots array is
the index used by mesh material tables.  Recognising the group is important:
``local_head`` may sit immediately before the slots array in the file, and a
plain "longest run of shader-looking pointers" would silently prepend that
non-shader node and shift every material by one.
"""
import struct
import re
import sys


def extract_shadinggroup(path):
    d = open(path, "rb").read()
    N = len(d)
    base = struct.unpack_from("<I", d, 0)[0] - 0x80  # VIRT -> FILE: file = virt - base

    def U(o):
        return struct.unpack_from("<I", d, o)[0] if 0 <= o <= N - 4 else 0

    def toff(v):
        return v - base

    def read_cstr(o):
        e = o
        while e < N and d[e] not in (0, 0xCD):
            e += 1
        return d[o:e].decode("latin1", "replace")

    names = {m.start(): m.group().decode() for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_.]{2,}\.shadert", d)}
    if not names:
        return base, []
    SLO, SHI = min(names) - 0x400, max(names) + 0x40

    _name_cache = {}

    def name_of(res_file):
        # scan the resource for a pointer that lands on a .shadert name.
        # On PS2 that pointer sits anywhere in the first 0x140 bytes; on PSP
        # (both MC3 and MC:LA) it is at +0x24. Scanning covers both.
        if res_file in _name_cache:
            return _name_cache[res_file]
        out = None
        for k in range(0, 0x140, 4):
            v = U(res_file + k)
            if base <= v <= base + N:
                fo = toff(v)
                if 0 < fo < N - 4:
                    s = read_cstr(fo)
                    if s.endswith(".shadert"):
                        out = s
                        break
        _name_cache[res_file] = out
        return out

    def is_shader_ptr(o):
        # A shader pointer is one that lands on a resource carrying a .shadert
        # name. The PS2 tag check (??7A00) used to stand in for this, but the
        # first u32 of a PSP resource is a VTABLE POINTER that changes per build
        # (0x09174904 on MC3 PSP, 0x00597ECC on MC:LA), so it can never be
        # matched as a constant -- the name is the portable signal.
        v = U(o)
        if not (base <= v <= base + N):
            return False
        fo = toff(v)
        if not (SLO <= fo < SHI):
            return False
        return name_of(fo) is not None

    # Prefer the actual rmcShaderGroup structure.  This keeps the slots array
    # separate from local_head at +0x0C -- the MC:LA Corvette PSP package puts
    # local_head immediately before slots in file order, which fooled the old
    # longest-run heuristic and shifted all 18 material indices by one.
    groups = []
    for o in range(0x80, N - 0x10, 4):
        slots = toff(U(o + 4))
        count, capacity = struct.unpack_from("<HH", d, o + 8)
        if not (1 <= count <= capacity <= 4096):
            continue
        if not (0x80 <= slots <= N - 4 * count):
            continue
        entries = []
        named = 0
        for i in range(count):
            value = U(slots + 4 * i)
            resource = toff(value)
            shader_name = (name_of(resource)
                           if 0x80 <= resource < N - 4 else None)
            entries.append(shader_name or "<unnamed>")
            named += shader_name is not None
        # Real groups may have a final basic/anonymous slot, but the majority
        # of a vehicle group is named.  Requiring both a useful count and a
        # strong ratio rejects unrelated count/capacity vectors.
        if named >= 2 and named * 2 >= count:
            groups.append((named, count, entries))
    if groups:
        _named, _count, entries = max(groups, key=lambda row: (row[0], row[1]))
        return base, entries

    # Legacy fallback for containers where the group header is absent or has a
    # platform-specific wrapper that the structural scan above cannot see.
    # This preserves the old tool's coverage without using it on known groups.
    best = []
    o = 0x80
    while o <= N - 4:
        if is_shader_ptr(o):
            ent = []
            q = o
            while q <= N - 4 and is_shader_ptr(q):
                nm = name_of(toff(U(q)))
                if nm is None:
                    break
                ent.append(nm)
                q += 4
            if len(ent) > len(best):
                best = ent
            o = max(q, o + 4)
        else:
            o += 4
    return base, best


def main():
    if len(sys.argv) < 2:
        print("usage: python mc3_shadinggroup.py <file.pck|.psppck>")
        return
    path = sys.argv[1]
    base, shaders = extract_shadinggroup(path)
    print(f"file: {path}")
    print(f"virtual base: {base + 0x80:#010x}")
    if not shaders:
        print("no shadinggroup found (a PSP container may use a different layout).")
        return
    print(f"shadinggroup ({len(shaders)} shaders):")
    for i, s in enumerate(shaders):
        print(f"  {i:2} = {s}")


if __name__ == "__main__":
    main()
