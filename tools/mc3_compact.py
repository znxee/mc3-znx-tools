"""
mc3_compact.py - reclaim the dead geometry a hood fill leaves behind

`mc3_hood_fill.swap()` writes the replacement geometry at the END of the file
and repoints the mesh at it. Nothing moves, which is what makes the swap safe -
and the geometry that was there before stays in the file as dead weight. For one
neighbourhood that costs a few hundred kilobytes. For a whole city it nearly
doubles the file, and a city `.pck` has a size ceiling: measured in game,
12.08 MB loads and 13.88 MB hangs before the file ever reaches RAM.

So the dead bytes have to go. This does it in the narrowest way that is safe:

  * only the PACKETS of the meshes that were actually replaced are dropped, and
    their ranges are recorded from the old block list BEFORE the swap, so
    nothing is guessed and nothing unknown is discarded;
  * everything else keeps its bytes, in order;
  * every pointer is translated through a shift map.

The pointer pass is the part that has to be careful. Vertex data constantly
forms u32 that fall inside the VA window, so a blind scan would corrupt
geometry. The scan therefore SKIPS the packet regions that survive - the same
rule the rest of the toolkit uses.

    import mc3_compact
    dead = []
    for mesh in ...:
        dead += mc3_compact.mesh_packets(p, mesh)   # BEFORE swapping
    ...
    mc3_compact.compact(p, dead)
"""

import struct


def _block_list(p, mesh):
    """[(packet offset, size)] this mesh references now.

    `mesh+0x10` points to the group table: `ngrp` 8-byte entries, each one
    `[pointer to the list][u16 count][u16]`. Each list has `count` 8-byte
    entries `[packet VA][u16 qwc][u16 nv]`, and qwc gives the size: the packet
    has `qwc * 16` bytes.
    """
    outside = []
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    if not (1 <= ngrp <= 64):
        return outside
    gt = p.F(p.U(mesh + 0x10))
    if not p.inside(gt):
        return outside
    for g in range(ngrp):
        e = gt + 8 * g
        if not p.inside(e + 8):
            break
        items = p.F(p.U(e))
        n = struct.unpack_from('<H', p.data, e + 4)[0]
        if not p.inside(items) or n > 4096:
            continue
        for k in range(n):
            o = items + 8 * k
            if not p.inside(o + 8):
                break
            va, qwc, _nv = struct.unpack_from('<IHH', p.data, o)
            po = p.F(va)
            if p.inside(po) and 0 < qwc <= 4096:
                outside.append((po, qwc * 16))
    return outside


def mesh_packets(p, mesh):
    """The packet regions that will become ORPHANS when this mesh is swapped.

    Call it BEFORE the swap: afterwards, `mesh+0x10` already points to the new
    geometry and nothing names the old one any more.
    """
    return _block_list(p, mesh)


def _mesh_payloads(p, mesh):
    """The per-block PAYLOAD regions of `mesh+0x14`.

    They are one byte per vertex, high entropy, and form u32 values inside the
    VA window all the time - the same trap as the packets. Without skipping
    these, the pointer scan "translates" data and corrupts the file: in tokyo it
    changed the shader group count from 1720 to 55112.

        mesh+0x14 -> [nslot pointers]
          each slot -> ngrp 8-byte entries: [ptrA][ptrB]
             ptrA -> [u32 count][u32 verts per block] + payloads right after
             ptrB -> array of count pointers
    """
    outside = []
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    nslot = struct.unpack_from('<H', p.data, mesh + 0x0A)[0]
    if not (1 <= ngrp <= 64) or not (1 <= nslot <= 64):
        return outside
    t14 = p.F(p.U(mesh + 0x14))
    if not p.inside(t14):
        return outside
    for s in range(nslot):
        base = p.F(p.U(t14 + 4 * s))
        if not p.inside(base):
            continue
        for g in range(ngrp):
            e = base + 8 * g
            if not p.inside(e + 8):
                break
            for field in (0, 4):
                a = p.F(p.U(e + field))
                if not p.inside(a):
                    continue
                n = struct.unpack_from('<I', p.data, a)[0] if field == 0 else 0
                if field == 0 and 0 < n <= 4096:
                    verts = struct.unpack_from('<%dI' % n, p.data, a + 4)
                    size_ = 4 + 4 * n + sum((v + 15) & ~15 for v in verts)
                    outside.append((a, size_))
                elif field == 4:
                    outside.append((a, 4 * max(1, ngrp)))
    return outside


def _live(p):
    """Every packet region still referenced, for the scan to skip."""
    hb = p.F(p.U(0x80 + 0x18))
    live = []
    seen = set()

    def do_cd(cd):
        for lod in range(10):
            mp = p.U(cd + 0x3C + 4 * lod)
            m = p.F(mp)
            if p.inside(m) and m not in seen:
                seen.add(m)
                live.extend(_block_list(p, m))
                try:
                    live.extend(_mesh_payloads(p, m))
                except Exception:
                    pass

    for h in range(p.U(0x80 + 0x14)):
        e = hb + 20 * h
        nu, au = p.U(e + 4), p.F(p.U(e + 8))
        ni, ai = p.U(e + 0x0C), p.F(p.U(e + 0x10))
        if p.inside(au):
            for k in range(nu):
                cd = p.F(p.U(au + 28 * k + 0x0C))
                if p.inside(cd):
                    do_cd(cd)
        if p.inside(ai):
            for k in range(ni):
                cd = p.F(p.U(ai + 96 * k + 0x0C))
                if p.inside(cd):
                    do_cd(cd)
    return live


def _merge(ranges):
    """Sort and join ranges that touch."""
    if not ranges:
        return []
    r = sorted((a, a + n) for a, n in ranges)
    out_path = [list(r[0])]
    for a, b in r[1:]:
        if a <= out_path[-1][1]:
            out_path[-1][1] = max(out_path[-1][1], b)
        else:
            out_path.append([a, b])
    return [(a, b) for a, b in out_path]


def compact(p, dead, base_va=0x06800000):
    """Remove the dead ranges and fix every pointer. Returns (before, after)."""
    before = len(p.data)
    dead = [(a, n) for a, n in dead if a >= 0x80 and n > 0]
    range_list = _merge(dead)
    if not range_list:
        return before, before

    # do not remove anything that is still referenced
    live = set()
    for a, n in _live(p):
        live.add(a)
    range_list = [(a, b) for a, b in range_list if a not in live]
    if not range_list:
        return before, before

    # shift map: how many bytes disappear before each offset
    starts = [a for a, _b in range_list]
    accum = []
    t = 0
    for a, b in range_list:
        accum.append(t)
        t += b - a

    import bisect

    def new(off):
        i = bisect.bisect_right(starts, off) - 1
        if i < 0:
            return off
        a, b = range_list[i]
        if off < b:                      # inside a dead range
            return None
        return off - (accum[i] + (b - a))

    # the pointer scan skips the live packets: vertex data forms u32 values
    # that fall in the VA window and a blind scan would corrupt the geometry
    skip = _merge(_live(p))
    skip_starts = [a for a, _b in skip]

    def in_packet(off):
        i = bisect.bisect_right(skip_starts, off) - 1
        return i >= 0 and off < skip[i][1]

    limit_va = base_va + before
    d = p.data
    fixed_n = lost_n = 0
    for off in range(0x80, before - 4, 4):
        if in_packet(off):
            continue
        v = struct.unpack_from('<I', d, off)[0]
        if not (base_va <= v < limit_va):
            continue
        target = v - base_va + 0x80
        n = new(target)
        if n is None:
            lost_n += 1
            continue
        struct.pack_into('<I', d, off, n - 0x80 + base_va)
        fixed_n += 1

    # remove back to front so the offsets stay valid
    for a, b in reversed(range_list):
        del d[a:b]

    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    return before, len(d), fixed_n, lost_n


def mesh_region(p, mesh):
    """ALL the space this mesh occupies today, in merged ranges.

    `emit_mesh_region` writes the replacement as ONE contiguous region - tables,
    lists, packets and payloads together - and asks for that size at once.
    Freeing only the packets does not help, because each one alone is smaller
    than the new region: the pool had 5.25 MB and gave back 0.6 MB of file.
    Merging everything the mesh occupies, the free range gets the right order of
    magnitude.

    Included: the group table, the block lists, the packets, the `+0x14`
    pointer array, the per-group entries and the per-block payloads.
    """
    outside = list(_block_list(p, mesh))
    try:
        outside += _mesh_payloads(p, mesh)
    except Exception:
        pass
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    nslot = struct.unpack_from('<H', p.data, mesh + 0x0A)[0]
    if 1 <= ngrp <= 64:
        gt = p.F(p.U(mesh + 0x10))
        if p.inside(gt):
            outside.append((gt, 8 * ngrp))
            for g in range(ngrp):
                e = gt + 8 * g
                if not p.inside(e + 8):
                    break
                items = p.F(p.U(e))
                n = struct.unpack_from('<H', p.data, e + 4)[0]
                if p.inside(items) and n <= 4096:
                    outside.append((items, 8 * n))
        if 1 <= nslot <= 64:
            t14 = p.F(p.U(mesh + 0x14))
            if p.inside(t14):
                outside.append((t14, 4 * nslot))
                for s in range(nslot):
                    base = p.F(p.U(t14 + 4 * s))
                    if p.inside(base):
                        outside.append((base, 8 * ngrp))
    return [(a, b - a) for a, b in _merge(outside)]
