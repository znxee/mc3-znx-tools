"""
mc3_pointers.py - reverse pointer lookup for Midnight Club 3 load-in-place
containers (PS2 .pck, PSP .psppck, Xbox .xbck).

These files are a dump of serialized C++ objects: the game loads the whole block
and only fixes up the pointers using the base from the header. Since EVERYTHING
that matters is reachable from the root through pointers, from any address you
can walk up the chain to the root by asking "who points here".

Method (the user's own, from their pointer-rollback / reverse-search notes): search for
the address as a u32; if nobody points exactly at it, step back 0x4 (the real
target usually starts after some padding) and repeat; when MORE THAN ONE place
points at the same target, those are the several owners (e.g. several materials
-> the same texture). This works the same way in all three formats.

Address convention: `va = off + base`, `off = va - base`. On PS2/PSP the first
u32 of the file is the VA of offset 0x80, so `base = u32[0] - 0x80`.
"""
from __future__ import annotations
import struct
from collections import defaultdict

# VA where the PS2 first pointer lives (tag 20947A00). CAREFUL: it is a VA, not
# a file offset -- in the file it sits at F(0x06800128) = 0x1A8 (0x128 + 0x80).
# Mixing the two up shifts root detection by 0x80.
ROOT_PTR_VA = 0x06800128


class PointerGraph:
    """Pointer index (forward and reverse) over the bytes of a container.

    - forward: pointer offset -> VA it points to
    - reverse: target offset -> [offsets of the pointers pointing at it]

    A "pointer" is any u32 aligned to `word` whose value falls inside the valid
    VA window and whose TARGET is aligned to `align`. `skip(off)` lets you ignore
    regions (e.g. vertex data, which forms u32 inside the window all the time).
    """

    def __init__(self, data, base, *, valid_lo=None, valid_hi=None,
                 align=4, word=4, skip=None):
        self.data = data
        self.base = base
        self.word = word
        self.align = align
        self.skip = skip
        self.lo = valid_lo if valid_lo is not None else base + 0x80
        # window inclusive at the top: a pointer may point at the last useful byte
        self.hi = valid_hi if valid_hi is not None else base + len(data)
        self._fwd = None
        self._rev = None
        self._sorted_targets = None

    # ---- construction / mapping ------------------------------------------
    @classmethod
    def from_bytes(cls, data, **kw):
        """Read the base from the header (u32[0] - 0x80), like PS2/PSP."""
        va0 = struct.unpack_from("<I", data, 0)[0]
        return cls(data, va0 - 0x80, **kw)

    @classmethod
    def from_pck(cls, pck, **kw):
        """Reuse a Pck object (from mc3_tex or mc3_pck_embed): takes .data and
        the base already computed, plus the packet data region as `skip` if
        present."""
        data = pck.data
        base = getattr(pck, "base", None)
        if base is None or base > 0x1000:      # mc3_tex.Pck.base = raw VA (u32[0])
            base = struct.unpack_from("<I", data, 0)[0] - 0x80
        kw.setdefault("valid_hi", base + getattr(pck, "body", len(data) - 0x80) + 0x80)
        if "skip" not in kw and hasattr(pck, "in_data"):
            kw["skip"] = pck.in_data
        return cls(data, base, **kw)

    def V(self, off):
        return off + self.base

    def F(self, va):
        return va - self.base

    def _build(self):
        if self._rev is not None:
            return
        d, base, lo, hi = self.data, self.base, self.lo, self.hi
        align, skip = self.align, self.skip
        fwd, rev = {}, defaultdict(list)
        n = len(d) - 3
        for o in range(0, n, self.word):
            v = struct.unpack_from("<I", d, o)[0]
            if lo <= v <= hi:
                t = v - base
                if 0 <= t < len(d) and (align <= 1 or t % align == 0):
                    if skip and skip(o):
                        continue
                    fwd[o] = v
                    rev[t].append(o)
        self._fwd = fwd
        self._rev = dict(rev)

    # ---- queries ----------------------------------------------------------
    def all_pointers(self):
        """[(offset, va)] of every pointer. Sorted by offset."""
        self._build()
        return sorted(self._fwd.items())

    def targets(self):
        """Target offsets that somebody points at, sorted."""
        self._build()
        return sorted(self._rev)

    def sources_of(self, target_off):
        """Offsets of the pointers that point EXACTLY at target_off."""
        self._build()
        return list(self._rev.get(target_off, ()))

    def refs_in_range(self, lo_off, hi_off):
        """[(target_off, src_off)] of pointers whose target falls inside
        [lo_off, hi_off]. Same as the "by range" search the user does over the
        padding."""
        self._build()
        step = self.align or 1
        start = lo_off - (lo_off % step) if lo_off > 0 else 0   # align the start
        out = []
        for t in range(start, hi_off + 1, step):
            for s in self._rev.get(t, ()):
                out.append((t, s))
        return out

    def next_target(self, off):
        """Smallest target offset strictly > off. The start of the next
        pointed-to structure marks the end of the current block -- useful for
        sizing without any padding heuristic. O(log n) using the sorted target
        list."""
        import bisect
        self._build()
        if self._sorted_targets is None:
            self._sorted_targets = sorted(self._rev)
        i = bisect.bisect_right(self._sorted_targets, off)
        return self._sorted_targets[i] if i < len(self._sorted_targets) else None

    def block_bounds(self, off, *, strip_padding=True, pad_byte=0xCD):
        """(start, useful_end, next_struct_start) of the block starting at
        `off`. The upper bound comes from the graph (next pointed-to address),
        not from a heuristic; then only the TRAILING padding, the one touching
        the next struct, is stripped.

        This is robust where "scan up to the first 0xCD" breaks: in the dark
        16x16 textures of the vp_d_*_o files the pixels themselves contain 0xCD,
        so scanning forward stops in the middle of the image. Scanning backwards
        only ever cuts real padding.
        """
        nxt = self.next_target(off)
        if nxt is None:
            return (off, None, None)
        e = nxt
        if strip_padding:
            while e > off and self.data[e - 1] == pad_byte:
                e -= 1
        return (off, e, nxt)

    def rollback(self, target_off, window=0x400):
        """Step back by `align` from target_off until somebody points there.
        Returns (found_off, distance, [src_offs]) or (None, None, [])."""
        self._build()
        step = self.align or 4
        k = 0
        while k <= window:
            t = target_off - k
            if t < 0:
                break
            src = self._rev.get(t)
            if src:
                return t, k, list(src)
            k += step
        return None, None, []

    def trace_up(self, leaf_off, *, window=0x400, max_steps=128,
                 root_va=ROOT_PTR_VA):
        """Walk up the owner chain to the root, like the user's manual method.

        A target can have several owners (several materials -> the same texture),
        and the graph has convergences (the mutual descriptor<->info pair, shared
        vtables/lists), so this is a BFS over the reverse graph with a GLOBAL
        `visited`: every node is expanded exactly once -- it finishes in O(V+E),
        without the exponential path blow-up of a DFS with backtracking.

        The root is reached when we follow a pointer whose SOURCE lives in the
        header/root object, i.e. with VA <= `root_va` (on PS2 the root pointer,
        tag 20947A00, sits at VA 0x06800128). Comparing in VA space avoids the
        mistake of treating 0x128 as a file offset. Returns the shortest path to
        the root (a list of steps {target, dist, sources, chosen}, the last one
        with 'root': True); if no path gets there, returns the longest one tried.
        """
        self._build()
        from collections import deque
        best = []
        q = deque([(leaf_off, [])])
        visited = {leaf_off}
        while q:
            cur, path = q.popleft()
            if len(path) > len(best):
                best = path
            if len(path) >= max_steps:
                continue
            # who points inside [cur-window, cur], nearest first
            refs = self.refs_in_range(cur - window, cur)
            refs.sort(key=lambda ts: cur - ts[0])
            for t, s in refs:
                at_root = self.V(s) <= root_va
                step = {"target": t, "dist": cur - t,
                        "sources": self.sources_of(t), "chosen": s,
                        "root": at_root}
                if at_root:                       # source in the header/root object
                    return path + [step]
                if s in visited:
                    continue
                visited.add(s)
                q.append((s, path + [step]))
        return best

    def reaches_root(self, leaf_off, **kw):
        steps = self.trace_up(leaf_off, **kw)
        return bool(steps) and steps[-1].get("root", False)


def graph_for(path_or_pck, **kw):
    """Shortcut: accepts a path, bytes/bytearray, or a Pck object."""
    if isinstance(path_or_pck, (bytes, bytearray)):
        return PointerGraph.from_bytes(path_or_pck, **kw)
    if isinstance(path_or_pck, str):
        return PointerGraph.from_bytes(bytearray(open(path_or_pck, "rb").read()), **kw)
    return PointerGraph.from_pck(path_or_pck, **kw)


__all__ = ["PointerGraph", "graph_for", "ROOT_PTR_VA"]
