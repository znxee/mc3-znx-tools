"""Slice a text .mod into INTERNALLY CONSISTENT pieces.

WHY THIS EXISTS, worth recording because it cost me several boots: the first
version of this cut `mtl` blocks and rewrote `adjuncts:` in the header, but left
the `v`/`t1` arrays whole in the file. The header said 1914 and there were 2926
array lines. If the parser sizes the buffer by `adjuncts:` and reads the arrays
until it finds `mtl`, it writes 2926 entries into a 1914-entry buffer - and what
comes out of that is the game allocator's "Heap %s overrun" (0x0065D8BC) and a
crash at a random address. I went on to blame those crashes on a material
ceiling that DOES NOT EXIST: 10-block slices starting at 0, at 11 and at 17 all
load.

So cutting requires renumbering. This script collects the indices really used
by the chosen blocks, emits only those array entries, and rewrites each `adj`
to the new indices. The array lines are COPIED, never reformatted, so no float
drift is introduced.

Line terminator: LF. The game's own .mod files use LF (heli.mod does not have a
single CR); my generator had been writing CRLF. That alone does not crash - it
was measured - but there is no reason to differ from what the game ships.

    python mc3_mod_split.py input.mod out_%d.mod --per 10
    python mc3_mod_split.py input.mod slice.mod --slice 11 21
"""
import argparse
import io
import os

FIELDS = ('v', 'n', 'c', 't1')          # the order of the first 4 adj fields


def le(path):
    txt = io.open(path, 'rb').read().decode('latin1').replace('\r\n', '\n')
    lines = txt.split('\n')
    hdr, arrays, i = [], {k: [] for k in FIELDS}, 0
    while i < len(lines) and not lines[i].startswith('mtl'):
        t = lines[i].split()
        if t and t[0] in arrays:
            arrays[t[0]].append(lines[i])
        elif ':' in lines[i]:
            hdr.append(lines[i])
        i += 1
    blocks, current = [], None
    while i < len(lines):
        if lines[i].startswith('mtl'):
            if current:
                blocks.append(current)
            current = [lines[i]]
        elif current is not None:
            current.append(lines[i])
        i += 1
    if current:
        blocks.append(current)
    return hdr, arrays, blocks


def write(out_path, hdr, arrays, blocks):
    """Emit only the referenced array entries, renumbered."""
    used_set = {k: set() for k in FIELDS}
    for b in blocks:
        for l in b:
            t = l.split()
            if t and t[0] == 'adj':
                for f, k in enumerate(FIELDS):
                    used_set[k].add(int(t[1 + f]))

    mapping, body = {}, []
    for k in FIELDS:
        order = sorted(used_set[k])
        mapping[k] = {old: new for new, old in enumerate(order)}
        for old in order:
            body.append(arrays[k][old])

    blocks_out, nadj, nprim = [], 0, 0
    for b in blocks:
        new = []
        for l in b:
            t = l.split()
            if t and t[0] == 'adj':
                ids = [mapping[k][int(t[1 + f])] for f, k in enumerate(FIELDS)]
                new.append('\tadj\t%d\t%d\t%d\t%d\t%s\t%s'
                            % tuple(ids + [t[5], t[6]]))
                nadj += 1
            else:
                new.append(l)
                if t and t[0] in ('str', 'stp'):
                    nprim += int(t[1]) - 2
                elif t and t[0] == 'tri':
                    nprim += 1
        blocks_out.append(new)

    count = {'verts': len(used_set['v']), 'normals': len(used_set['n']),
             'colors': len(used_set['c']), 'tex1s': len(used_set['t1']),
             'materials': len(blocks), 'adjuncts': nadj, 'primitives': nprim}
    new_head = []
    for l in hdr:
        k = l.split(':')[0].strip()
        new_head.append('%s: %d' % (k, count[k]) if k in count else l)

    lines = new_head + [''] + body + ['']
    for b in blocks_out:
        lines += b
    io.open(out_path, 'wb').write(('\n'.join(lines)).encode('latin1'))
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('entry')
    ap.add_argument('out_path', help='with --per, use %%d in the name')
    ap.add_argument('--per', dest='per_leaf', type=int, metavar='N', help='N mtl blocks per file')
    ap.add_argument('--slice', dest='slice_', type=int, nargs=2, metavar=('START', 'END'))
    a = ap.parse_args()

    hdr, arrays, blocks = le(a.entry)
    print('%s: %d mtl blocks, arrays v=%d n=%d c=%d t1=%d'
          % (os.path.basename(a.entry), len(blocks),
             *[len(arrays[k]) for k in FIELDS]))

    if a.slice_:
        chunks = [(a.slice_[0], a.slice_[1])]
    elif a.per_leaf:
        chunks = [(i, min(i + a.per_leaf, len(blocks)))
                   for i in range(0, len(blocks), a.per_leaf)]
    else:
        chunks = [(0, len(blocks))]

    for n, (i, f) in enumerate(chunks):
        name = a.out_path % n if '%d' in a.out_path else a.out_path
        c = write(name, hdr, arrays, blocks[i:f])
        print('  %-16s [%2d,%2d)  mtl=%-3d verts=%-5d colors=%-4d adj=%-5d prim=%d'
              % (os.path.basename(name), i, f, c['materials'], c['verts'],
                 c['colors'], c['adjuncts'], c['primitives']))


if __name__ == '__main__':
    main()
