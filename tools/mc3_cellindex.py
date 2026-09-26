"""mc3_cellindex.py - rebuild the per-cell index at MapRoot+0x1C

THIS IS THE CITY CULLING. Not the sphere, not the tile box: the tile box is only
the INPUT. What the game walks is a per-cell table, and it comes READY in the
`.pck`. Swapping the geometry without redoing the table leaves Los Angeles out
of every list - it loads, it runs, and almost nothing shows.

Measured on `atl_la_shader.pck`, the build that went into the game showing only
the asphalt: the index kept 10087 uniques and 9794 instances (retail atlanta's
numbers, untouched) while the real tile boxes asked for 18026 and 7347.

STRUCTURE, with the object pointed to by `MapRoot+0x1C`:

    +0x00 u32  number of unique components   (651 in atlanta)
    +0x04 u32  number of instances           (3608)
    +0x08 ptr  u32[+0x00]  pointer to each UniqueComponent
    +0x0C ptr  u32[+0x04]  pointer to each Instance
    +0x10 ptr  record[cell_count] of 12 bytes:
                 +0 s16 how many uniques cover this cell
                 +2 s16 how many instances cover it
                 +4 ptr u16[n0]  indices into the +0x08 array
                 +8 ptr u16[n1]  indices into the +0x0C array
    +0x14 ptr  u32[cell_count]   an extra list per cell (see below)

`cell_count` is `MapRoot+0x268`, and the cell index is `rel_z * W + rel_x` with
W at `+0x26C` - the same address `mcCity::CellIndexFromPos` (0x257BE8)
computes. THAT IS WHY THE GRID IS NOT A FREE PARAMETER: the fixup at 0x25BAE8
walks `cell_count` 12-byte records and 0x25BB40 walks `cell_count` pointers,
both in arrays that came from the file. Raising `+0x268` without redoing the
arrays makes the loop run past the end - that is how 91x82 hung the loading with
a file whose arrays had 3968 entries.

THE RULE WAS VALIDATED AGAINST RETAIL, not deduced: rebuilding atlanta's index
from its own tile boxes, the 3968 cells come out IDENTICAL, and the sums match
in the three cities measured (atlanta 10087/9794, tokyo 15913/16485, detroit
14484/11308). The lists come in DESCENDING index order - 2316 of 2316 lists
with more than one entry.

ABOUT `+0x14`: it is another per-cell list, with 14-bit indices plus two flag
bits, and it is not deciphered (it looks like a PVS). Here it is re-emitted with
ALL entries null, a state retail already exercises in 1810 of atlanta's 3968
cells. Null draws more, not less - which is the right side to err on while the
city is being ported.
"""
import struct
import collections


def _object(p):
    """(object offset, [unique offsets], [instance offsets])."""
    r = 0x80
    o = p.F(p.U(r + 0x1C))
    if not p.inside(o):
        return None
    nu, ni = struct.unpack_from('<2I', p.data, o)
    if not (0 < nu <= 20000 and 0 < ni <= 60000):
        return None
    a08, a0c = p.F(p.U(o + 8)), p.F(p.U(o + 0x0C))
    if not (p.inside(a08) and p.inside(a0c)):
        return None
    us = [p.F(p.U(a08 + 4 * k)) for k in range(nu)]
    ins = [p.F(p.U(a0c + 4 * k)) for k in range(ni)]
    return o, us, ins


def _per_cell(p, objs, w, h):
    """{cell index: [object indices, descending]}."""
    m = collections.defaultdict(list)
    for i, off in enumerate(objs):
        if not p.inside(off + 0x18):
            continue
        x0, x1, z0, z1 = p.data[off + 0x14:off + 0x18]
        for z in range(max(0, z0), min(h - 1, z1) + 1):
            base = z * w
            for x in range(max(0, x0), min(w - 1, x1) + 1):
                m[base + x].append(i)
    for k in m:
        m[k].reverse()          # retail keeps them descending
    return m


def check(p):
    """(equal cells, total) between the stored index and the tile boxes.

    It works both ways: it validates the rule on a retail file and exposes a
    stale index in a ported file.
    """
    r = 0x80
    cc, w, h = struct.unpack_from('<3i', p.data, r + 0x268)
    got = _object(p)
    if not got:
        return 0, 0
    o, us, ins = got
    a10 = p.F(p.U(o + 0x10))
    if not p.inside(a10):
        return 0, cc
    mu, mi = _per_cell(p, us, w, h), _per_cell(p, ins, w, h)
    equal_ones = 0
    for k in range(cc):
        c0, c1 = struct.unpack_from('<2h', p.data, a10 + 12 * k)
        pa, pb = struct.unpack_from('<2I', p.data, a10 + 12 * k + 4)
        read_n = []
        for cnt, ptr in ((c0, pa), (c1, pb)):
            if cnt > 0 and ptr and p.inside(p.F(ptr)):
                read_n.append(list(struct.unpack_from('<%dH' % cnt, p.data, p.F(ptr))))
            else:
                read_n.append([])
        if read_n == [mu.get(k, []), mi.get(k, [])]:
            equal_ones += 1
    return equal_ones, cc


def rebuild(p):
    """Rewrite +0x10 and +0x14 of the MapRoot+0x1C object. Returns a summary."""
    r = 0x80
    cc, w, h = struct.unpack_from('<3i', p.data, r + 0x268)
    got = _object(p)
    if not got or cc <= 0 or cc != w * h:
        return None
    o, us, ins = got
    mu, mi = _per_cell(p, us, w, h), _per_cell(p, ins, w, h)

    # The lists sit right after the records, packed and aligned to 2 bytes,
    # which is how retail keeps them (cell 51 at ...CE0 and 52 at ...CE2).
    records_ = bytearray(12 * cc)
    lists = bytearray()
    n_u = n_i = 0
    for k in range(cc):
        lu, li = mu.get(k, ()), mi.get(k, ())
        struct.pack_into('<2h', records_, 12 * k, len(lu), len(li))
        for col, seq in ((4, lu), (8, li)):
            if not seq:
                continue
            struct.pack_into('<I', records_, 12 * k + col, len(lists))   # provisional
            lists += struct.pack('<%dH' % len(seq), *seq)
        n_u += len(lu)
        n_i += len(li)

    ptrs = bytearray(4 * cc)                 # +0x14, all null on purpose
    block = bytes(records_) + bytes(lists)
    if len(block) % 4:
        block += b'\0' * (4 - len(block) % 4)
    shift_ptrs = len(block)
    block += bytes(ptrs)

    off = p.append(block)
    lists_base = off + 12 * cc
    # now that it is known where it landed, the provisional offsets become VAs
    for k in range(cc):
        c0, c1 = struct.unpack_from('<2h', p.data, off + 12 * k)
        for col, cnt in ((4, c0), (8, c1)):
            if cnt <= 0:
                continue
            rel = struct.unpack_from('<I', p.data, off + 12 * k + col)[0]
            struct.pack_into('<I', p.data, off + 12 * k + col,
                             p.V(lists_base + rel))

    struct.pack_into('<I', p.data, o + 0x10, p.V(off))
    struct.pack_into('<I', p.data, o + 0x14, p.V(off + shift_ptrs))
    return {'cells': cc, 'grid': (w, h), 'uniques': n_u, 'instances': n_i,
            'bytes': len(block), 'occupied': sum(1 for k in range(cc)
                                                 if mu.get(k) or mi.get(k))}
