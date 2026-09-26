"""Extend the per-cell arrays of props and peds to fit the city's cell_count.

The city .pck cell_count is a CONTRACT BETWEEN FILES, decompiled by FORK C++
MODS AND PATCHES. Three resources carry an array of ONE POINTER PER CELL and the
loop that relocates each one is bounded by mcCity+0x268, read from the city
ALREADY LOADED:

    a) the city's own CellIndex  - always consistent, same file
    b) <city>_props.pck   mcPropClass::mcPropClass(datResource&) 0x0038F3F8
    c) <city>_peds.pck    mcCreatureCullableClass::...           0x004667E8

The last two have the same body:

    if (ptr) ptr += delta;
    for (i = 0; i < cellcount; i++) if (arr[i]) arr[i] += delta;

If the city declares MORE cells than the props/peds was built for, the loop
walks past the end of the array and ADDS THE RESOURCE DELTA to every non-null
word it finds after it. It corrupts another object in place, silently, during
the load - and the game dies on the first frame with nothing pointing at the
grid.

The fix is surgical and moves nothing: append the new array at the END of the
file, repoint, and add to header[3]. No other pointer changes because nothing
is reallocated.

    python mc3_cellcount.py --cells 7462 <files...>

With --preserve the old entries are copied to the start of the new array.
Without it the array comes out ZEROED, which is what serves for the first
diagnostic boot: no props and no peds, but it proves the cause in a single run.
The old entries were indexed with the DONOR city's W, so copying them leaves
props and peds in the wrong cell of the new grid - safe in memory, wrong on
screen.
"""
import argparse
import os
import struct

# (label, object offset, pointer offset, identity test)
FORMATS = (
    ('props', 0x9B0, 0x9D8, lambda d: struct.unpack_from('<I', d, 0x9B0)[0] == 102
     and struct.unpack_from('<I', d, 0x9D0)[0] == 4),
    ('peds', 0x280, 0x2A4, lambda d: len(d) > 0x2B0),
)


def current_entries(d, base, ptr, next_):
    """How many entries the array has today: from its start to the next object."""
    start_ = struct.unpack_from('<I', d, ptr)[0] - base + 0x80
    return start_, max(0, (next_ - start_) // 4)


def extend(path, cells, preserve, dry_run):
    d = bytearray(open(path, 'rb').read())
    base, kind, ver, body = struct.unpack_from('<IIII', d, 0)
    if body != len(d) - 0x80:
        return '%s: header[3]=%d does not match the file (%d)' % (
            os.path.basename(path), body, len(d) - 0x80)
    for rot, obj, ptr, ok in FORMATS:
        if len(d) > obj + 0x30 and ok(d):
            break
    else:
        return '%s: not recognised as props or peds' % os.path.basename(path)
    va = struct.unpack_from('<I', d, ptr)[0]
    start_ = va - base + 0x80
    if not (0 < start_ < len(d)):
        return '%s: array pointer outside the file (0x%08X)' % (
            os.path.basename(path), va)
    old = bytes(d[start_:start_ + 4 * cells])          # only what exists
    new_off = (len(d) + 15) & ~15
    d += bytes(new_off - len(d))
    new_body = bytearray(4 * cells)
    if preserve:
        new_body[:len(old)] = old
    d += new_body
    struct.pack_into('<I', d, ptr, base + new_off - 0x80)
    struct.pack_into('<I', d, 0x0C, len(d) - 0x80)
    if not dry_run:
        open(path, 'wb').write(bytes(d))
    return '%-44s %-6s array 0x%X -> 0x%X, %d entries, %s (+%d bytes)' % (
        os.path.basename(path), rot, start_, new_off, cells,
        'copied' if preserve else 'zeroed', len(d) - body - 0x80)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--cells', type=int, required=True,
                    help='the city cell_count (MapRoot+0x268)')
    ap.add_argument('--preserve', action='store_true',
                    help='copy the old entries instead of zeroing everything')
    ap.add_argument('--dry-run', action='store_true', help='do not write')
    ap.add_argument('files', nargs='+')
    a = ap.parse_args()
    for f in a.files:
        print('  ' + extend(f, a.cells, a.preserve, a.dry_run))


if __name__ == '__main__':
    main()
