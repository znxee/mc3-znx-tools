r"""Rebuild the heap block the `mesh_dump` mod dumped to the EE console.

The mod creates a private heap (mcHeap::CreateMemTopHeap), asks the game to read
a text `.mod` into it and dumps the block in hexadecimal between a
`BLOB <base> <bytes>` line and an `ENDB <bytes>` one. This script pulls that out
of the PCSX2 log and returns the binary.

WHY GO THROUGH THE CONSOLE. A module has no file writing - the game exposes none
to us - and a savestate would make the user act on every iteration. The SIO at
0x1000F180 is the only channel, and it is fast: 148 KB took 0.024 s of emulated
time.

WHAT COMES OUT HERE IS THE BODY OF A `.pck`, with one caveat: the pointers are
ABSOLUTE at the base where the heap landed (the `base` of the BLOB line). A
`.pck` keeps the same pointers relative to a base declared in the header and
`datResource` adds the delta at load - so converting is subtracting the base
from every word that is a pointer, not rewriting structure.

    python mc3_heap_dump.py --log path\to\pcsx2_log.txt --out-path model.bin
"""
import argparse
import os
import re
import struct

LINE = re.compile(r'^([0-9A-F]{8}):([0-9A-F]+)\s*$')


def extract(path):
    """Return (base, bytes) of the last complete BLOB in the log."""
    base = size_ = None
    parts = {}
    for raw in open(path, encoding='utf-8', errors='replace'):
        l = raw.strip()
        # PCSX2 prefixes each line with [    33,8216]
        cut = l.find(']')
        if cut >= 0 and l.startswith('['):
            l = l[cut + 1:].strip()
        if l.startswith('BLOB '):
            t = l.split()
            base, size_ = int(t[1], 16), int(t[2], 16)
            parts = {}
            continue
        if l.startswith('ENDB '):
            continue
        m = LINE.match(l)
        if m and base is not None:
            off = int(m.group(1), 16)
            d = bytes.fromhex(m.group(2))
            parts[off] = d
    if base is None:
        raise SystemExit('no BLOB line in the log')
    out_path = bytearray(size_)
    seen = 0
    for off, d in parts.items():
        if off + len(d) <= size_:
            out_path[off:off + len(d)] = d
            seen += len(d)
    return base, bytes(out_path), seen


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log', default='pcsx2_cli_log.txt')
    ap.add_argument('--out-path', default='output/heap_dump.bin')
    ap.add_argument('--model', type=lambda s: int(s, 16), metavar='VA',
                    help='address of the rmcModel (the PRB1 of the log), to report')
    a = ap.parse_args()

    base, d, seen = extract(a.log)
    print('BLOB base %08X, %d bytes (%d received, %d missing)'
          % (base, len(d), seen, len(d) - seen))
    os.makedirs(os.path.dirname(a.out_path) or '.', exist_ok=True)
    open(a.out_path, 'wb').write(d)
    print('  -> %s' % a.out_path)

    # how many words look like a pointer into the block itself: that is what
    # makes the block relocatable as a pck body
    n = inside = 0
    for i in range(0, len(d) - 3, 4):
        v = struct.unpack_from('<I', d, i)[0]
        if base <= v < base + len(d):
            inside += 1
        n += 1
    print('  words: %d, pointing inside the block: %d (%.1f%%)'
          % (n, inside, 100.0 * inside / max(1, n)))

    if a.model:
        o = a.model - base
        if 0 <= o < len(d) - 32:
            c = struct.unpack_from('<8I', d, o)
            print('  rmcModel at +%06X: vtable %08X, groups %d, table %08X, geoms %08X'
                  % (o, c[0], c[2], c[3], c[4]))


if __name__ == '__main__':
    main()
