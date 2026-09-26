"""Turn the MC2 placement table into something a PS2 module can read.

Of the three legs of porting a city from raw files - geometry, collision,
placement - the first two have live readers in MC3 and the third does not. The
`.lvl`/`.hood` grammar is in the string pool with zero instructions referencing
it: the readers were compiled out. So placement is the one part that has to be
code, and this is its PC half.

    python mc3_place.py output/mc2_losangeles/losangeles_place.tsv \\
        --out $MC3_HOSTFS/losangeles.place

WHY A BINARY AND NOT THE TSV

The module is freestanding - no libc, no strtod. Parsing 8809 lines of decimal
floats on the EE to produce numbers this script already has is work in the wrong
place. Here it is one pass over a text file; there it would be a float parser
nobody should have to debug through a savestate.

THE LUCKY PART

`gfxState::SetWorld` (0x0052E0D0) reads three floats per row and expands them to
four, so its argument is a Matrix34: twelve floats, row-major, translation last.
That is EXACTLY the column order the TSV already has - m00..m22 then tx ty tz -
so the twelve floats are copied through untouched and the module hands the
pointer straight to the game. No conversion, and nothing to get transposed.

FORMAT

    +0   'MC3L'
    +4   version
    +8   name count          unique asset names
    +12  entry count         comp rows and inst rows together: both are draws
    +16  names offset        NUL-terminated, concatenated
    +20  names size
    +24  entries offset
    +28  reserved
    +32  extents min[3]      from the .lvl header, for whoever wants them
    +44  extents max[3]
    +56  padding to 64

    entry, 68 bytes:
    +0   u32   name index
    +4   float m[12]         Matrix34, ready for SetWorld
    +52  float centre[3]     from the AABB, for culling
    +64  float radius
"""
import argparse
import io
import os
import struct

MAGIC = 0x4D43334C          # 'MC3L'
VERSION = 1
HDR = 64
ENTRY = 68


def read_tsv(path):
    """(names, entries, emin, emax). `comp` and `inst` both become entries.

    The `comp` rows carry an identity matrix and a world-space box - they are
    the fixed geometry, already placed. The `inst` rows are the repeated props
    with a real transform. Both get drawn, so both become entries here; the
    distinction only mattered to the exporter.
    """
    names, index, entries = [], {}, []
    emin = emax = None
    for line in io.open(path, encoding='latin1'):
        line = line.rstrip('\n')
        if line.startswith('# extents_min'):
            emin = [float(x) for x in line.split('\t')[1:4]]
            continue
        if line.startswith('# extents_max'):
            emax = [float(x) for x in line.split('\t')[1:4]]
            continue
        if not line or line.startswith('#') or line.startswith('kind'):
            continue
        c = line.split('\t')
        if c[0] not in ('comp', 'inst'):
            continue
        # The model file is <name>_<lod>_<part>.mod, and gfxGetModel appends the
        # extension itself, so the asset name is everything before it.
        #
        # '#' is replaced as hygiene, NOT as a fix. It was once believed to be
        # why gfxGetModel hung; renaming all 298 files changed nothing and the
        # hang landed on the identical name with an underscore. Keeping the
        # substitution because an odd character in an asset name is worth
        # avoiding anyway - but nobody should read it as a solved problem.
        # The files in ASSETS/model must match whatever this produces.
        # The FULL spelling, directory and extension included. rmcModel::Create
        # dispatches on the extension - no ".mod" means it takes the binary
        # path - and mc2_probe measured that the directory is required too:
        # "model/checker.mod" loads and "checker.mod" returns nothing. Building
        # it here keeps string handling out of a module that has no libc.
        asset = 'model/%s.mod' % (
            ('%s_%s_%s' % (c[1], c[2], c[3])).replace('#', '_'))
        if asset not in index:
            index[asset] = len(names)
            names.append(asset)
        m = [float(x) for x in c[4:16]]          # m00..m22 then tx ty tz
        box = [float(x) for x in c[16:22]]     # emin xyz, emax xyz
        centre = [(box[i] + box[i + 3]) * 0.5 for i in range(3)]
        radius = max((box[i + 3] - box[i]) * 0.5 for i in range(3))
        entries.append((index[asset], m, centre, radius))
    return names, entries, emin or [0, 0, 0], emax or [0, 0, 0]


def sort_by_size(names, entries, folder, ceiling):
    """Entries from the smallest mesh to the largest, optionally without the largest.

    The module resolves the first N distinct names it meets walking the entry
    list, so the ORDER OF THE ENTRIES decides which meshes it tries first. Draw
    order does not matter, which makes this free.

    It is here to measure rather than to fix: the loader stopped on the largest
    mesh of the set (548 KB, 1511 verts, 31 materials, 3476 adjuncts) having
    taken the 161 KB one before it without complaint. Smallest-first turns the
    number of models resolved into the size at which it gives up - one boot
    instead of a hand bisection.
    """
    size = {}
    for i, n in enumerate(names):
        base = n.split('/')[-1]
        # The exporter's directory still spells the 298 component meshes with
        # '#'; only the copies under ASSETS/model were renamed. Looking up just
        # the sanitised name silently returned 0 for every one of them, which
        # sorted the BIGGEST meshes to the front - the exact opposite of the
        # point. Try both spellings, and say so when one is genuinely missing.
        size[i] = 0
        for cand in (base, base.replace('_geom_', '#geom_')):
            try:
                size[i] = os.path.getsize(os.path.join(folder, cand))
                break
            except OSError:
                pass

    missing_n = sum(1 for v in size.values() if v == 0)
    if missing_n:
        print('WARNING: %d of %d meshes not found in %s - they go first in the '
              'order, which skews the measurement' % (missing_n, len(names), folder))

    keep = [i for i in range(len(names))
              if not ceiling or size[i] <= ceiling]
    dropped = len(names) - len(keep)
    order = sorted(keep, key=lambda i: size[i])

    new_idx = {}
    new_names = []
    for i in order:
        new_idx[i] = len(new_names)
        new_names.append(names[i])

    new_ones = [(new_idx[idx], m, c, r) for idx, m, c, r in entries
             if idx in new_idx]
    new_ones.sort(key=lambda e: e[0])
    largest = max((size[i] for i in order), default=0)
    return new_names, new_ones, dropped, largest


def write(out_path, names, entries, emin, emax):
    table = b''
    offs = []
    for n in names:
        offs.append(len(table))
        table += n.encode('latin1') + b'\0'
    while len(table) % 4:
        table += b'\0'

    names_off = HDR
    entries_off = names_off + len(table)

    out = bytearray()
    out += struct.pack('<8I', MAGIC, VERSION, len(names), len(entries),
                       names_off, len(table), entries_off, 0)
    out += struct.pack('<6f', *(emin + emax))
    out += bytes(HDR - len(out))
    out += table
    for idx, m, centre, radius in entries:
        # The name index is stored as the OFFSET into the table, so the module
        # does not need a second indirection to find the string.
        out += struct.pack('<I', offs[idx])
        out += struct.pack('<12f', *m)
        out += struct.pack('<3f', *centre)
        out += struct.pack('<f', radius)

    io.open(out_path, 'wb').write(bytes(out))
    return len(out)


def main():
    p = argparse.ArgumentParser(description='placement table -> binary')
    p.add_argument('tsv')
    p.add_argument('--out', required=True)
    p.add_argument('--models',
                   help='folder of the .mod files; with it the entries come out '
                        'sorted from the smallest mesh to the largest')
    p.add_argument('--max-mesh', type=int, default=0,
                   help='drop meshes above N bytes (0 = no limit)')
    a = p.parse_args()

    names, entries, emin, emax = read_tsv(a.tsv)
    if a.models:
        names, entries, dropped, largest = sort_by_size(names, entries, a.models,
                                                        a.max_mesh)
        print('sorted from the smallest mesh to the largest; the largest left has '
              '%d bytes%s' % (largest, (', %d dropped' % dropped)
                              if dropped else ''))
    size_ = write(a.out, names, entries, emin, emax)
    print('%s: %d entries, %d unique names, %d bytes (%.0f KB)'
          % (os.path.basename(a.out), len(entries), len(names), size_, size_ / 1024.0))
    print('   extents  %s .. %s'
          % (' '.join('%.0f' % v for v in emin), ' '.join('%.0f' % v for v in emax)))
    print('   in game RAM: %.0f KB of entries + %d KB of names'
          % (len(entries) * ENTRY / 1024.0, (size_ - HDR - len(entries) * ENTRY) // 1024))


if __name__ == '__main__':
    main()
