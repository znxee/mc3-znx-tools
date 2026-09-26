"""Put loose files into the root of a PS2 ISO without rebuilding it.

It is there to take the modloader to real hardware. In PCSX2 the files sit
loose in HostFS; on a real PS2 there is no HostFS, and mc3boot looks for each
file in host0: -> cdrom0: -> mass0:. With everything inside the ISO, cdrom0:
answers, and the same binary runs in both places.

WHY NOT REBUILD THE ISO. The MC3 image is 7.95 GB and there is no rebuilder in
the toolkit. But it has room where it matters, and this was MEASURED on this
image:

    root directory   LBA 261, 732 bytes used of one sector -> 1316 free
    free gap         LBA 9702, 445062 sectors (869 MB) zeroed
    descriptors      only the primary one, no Joliet - a single place to update

So the files can be written into a gap, the directory records added in the
space left in the root sector, and the root size fixed in the two places where
it appears. Nothing moves and the file does not change size.

WARNING: the MC3 image has TWO concatenated ISO volumes - the second starts at
sector 2084960, with its own CD001. This script only touches the first and never
writes past the volume declared in the PVD.

    python mc3_iso.py <iso> --add mc3boot.elf --add mc3mod.bin --apply

Without --apply it only reports what it would do, and even with --apply it
checks that the target sectors are zeroed before writing a single byte.
"""
import argparse
import os
import struct

SECTOR_SIZE = 2048
PVD_LBA = 16


def iso_name(base):
    """core.mod -> CORE.MOD;1. ISO9660 level 1: 8.3, upper case."""
    root, ext = os.path.splitext(os.path.basename(base))
    def clean_(s):
        return ''.join(c if (c.isalnum() or c == '_') else '_' for c in s.upper())
    return '%s.%s;1' % (clean_(root)[:8], clean_(ext[1:])[:3])


def read_records(d, size_):
    out = []
    i = 0
    while i < size_ and i < len(d) and d[i]:
        out.append(bytes(d[i:i + d[i]]))
        i += d[i]
    return out


def make_record(name, lba, size):
    n = name.encode('ascii')
    L = 33 + len(n)
    if L % 2:
        L += 1                                 # the record has an EVEN length
    r = bytearray(L)
    r[0] = L
    struct.pack_into('<I', r, 2, lba)
    struct.pack_into('>I', r, 6, lba)          # the big-endian field too
    struct.pack_into('<I', r, 10, size)
    struct.pack_into('>I', r, 14, size)
    r[25] = 0                                  # flags: regular file
    struct.pack_into('<H', r, 28, 1)           # volume number
    struct.pack_into('>H', r, 30, 1)
    r[32] = len(n)
    r[33:33 + len(n)] = n
    return bytes(r)


def key(reg):
    """The order ISO9660 requires: by name, without the version."""
    return reg[33:33 + reg[32]].split(b';')[0]


def gaps(f, total):
    """Free sectors inside the declared volume, by measuring what is in use."""
    used_set = []

    def walk(lba, size_):
        ns = (size_ + SECTOR_SIZE - 1) // SECTOR_SIZE
        used_set.append((lba, lba + ns))
        f.seek(lba * SECTOR_SIZE)
        d = f.read(ns * SECTOR_SIZE)
        i = 0
        while i < len(d) and d[i]:
            nl = d[i + 32]
            elba = struct.unpack_from('<I', d, i + 2)[0]
            esize = struct.unpack_from('<I', d, i + 10)[0]
            if not (nl == 1 and d[i + 33] in (0, 1)):
                if d[i + 25] & 2:
                    walk(elba, esize)
                else:
                    used_set.append((elba, elba + (esize + SECTOR_SIZE - 1) // SECTOR_SIZE))
            i += d[i]

    f.seek(PVD_LBA * SECTOR_SIZE)
    pvd = f.read(SECTOR_SIZE)
    r = pvd[156:190]
    walk(struct.unpack_from('<I', r, 2)[0], struct.unpack_from('<I', r, 10)[0])
    used_set.sort()
    free = []
    end = 32
    for a, b in used_set:
        if a > end:
            free.append((end, a - end))
        end = max(end, b)
    if end < total:
        free.append((end, total - end))
    return free


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('iso')
    ap.add_argument('--add', action='append', default=[], metavar='FILE',
                    help='file to put in the root; the 8.3 name comes from its own name')
    ap.add_argument('--how', action='append', default=[], metavar='NAME.EXT',
                    help='ISO name for the --add at the same position')
    ap.add_argument('--boot', metavar='NAME.ELF',
                    help='rewrite SYSTEM.CNF BOOT2 to this name')
    ap.add_argument('--apply', dest='apply_', action='store_true', help='really write')
    a = ap.parse_args()

    f = open(a.iso, 'r+b' if a.apply_ else 'rb')
    f.seek(PVD_LBA * SECTOR_SIZE)
    pvd = bytearray(f.read(SECTOR_SIZE))
    total = struct.unpack_from('<I', pvd, 80)[0]
    root_lba = struct.unpack_from('<I', pvd, 156 + 2)[0]
    root_size = struct.unpack_from('<I', pvd, 156 + 10)[0]
    print('%s: volume %d sectors, root LBA %d, %d bytes used of %d'
          % (os.path.basename(a.iso), total, root_lba, root_size, SECTOR_SIZE))
    if root_size > SECTOR_SIZE:
        raise SystemExit('the root takes more than one sector; this script does not cover that')

    f.seek(root_lba * SECTOR_SIZE)
    root_sector = bytearray(f.read(SECTOR_SIZE))
    records_ = read_records(root_sector, root_size)
    existing = set(key(r) for r in records_)

    free = [l for l in gaps(f, total) if l[1] > 4]
    free.sort(key=lambda x: -x[1])
    if not free:
        raise SystemExit('no free gap in the volume')
    free_lba, n_free = free[0]
    print('  chosen gap: LBA %d, %d sectors (%.1f MB)'
          % (free_lba, n_free, n_free * SECTOR_SIZE / 2.0 ** 20))

    new_items = []
    cursor = free_lba
    for k, path in enumerate(a.add):
        if not os.path.isfile(path):
            raise SystemExit('not found: %s' % path)
        name = a.how[k] if k < len(a.how) else iso_name(path)
        if ';' not in name:
            name += ';1'
        if name.split(';')[0].encode('ascii') in existing:
            raise SystemExit('%s already exists in the ISO root' % name)
        data = open(path, 'rb').read()
        ns = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
        if cursor + ns > free_lba + n_free:
            raise SystemExit('the gap cannot fit %s' % name)
        new_items.append((name, cursor, data))
        print('     %-22s -> %-14s LBA %-8d %8d bytes (%d sectors)'
              % (os.path.basename(path), name, cursor, len(data), ns))
        cursor += ns

    all_ = records_ + [make_record(n, l, len(d)) for n, l, d in new_items]
    all_.sort(key=key)
    body = b''.join(all_)
    print('  root: %d bytes -> %d bytes (limit %d)' % (root_size, len(body), SECTOR_SIZE))
    if len(body) > SECTOR_SIZE:
        raise SystemExit('the new records do not fit in the root sector')

    cnf = None
    if a.boot:
        for r in records_:
            if key(r) == b'SYSTEM.CNF':
                lba = struct.unpack_from('<I', r, 2)[0]
                size_ = struct.unpack_from('<I', r, 10)[0]
                f.seek(lba * SECTOR_SIZE)
                old = f.read(size_).decode('ascii', 'replace')
                lines = []
                for ln in old.splitlines():
                    if ln.upper().startswith('BOOT2'):
                        ln = 'BOOT2 = cdrom0:\\%s' % a.boot
                    lines.append(ln)
                new = ('\r\n'.join(lines) + '\r\n').encode('ascii')
                cnf = (lba, new)
                print('  SYSTEM.CNF: %d -> %d bytes, BOOT2 = cdrom0:\\%s'
                      % (size_, len(new), a.boot))
                break
        if cnf is None:
            raise SystemExit('SYSTEM.CNF not found in the root')

    if not a.apply_:
        print('')
        print('  DRY RUN - nothing was written. Repeat with --apply.')
        return

    for name, lba, data in new_items:
        ns = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
        f.seek(lba * SECTOR_SIZE)
        if any(f.read(ns * SECTOR_SIZE)):
            raise SystemExit('LBA %d is not empty; aborted without writing anything' % lba)

    for name, lba, data in new_items:
        f.seek(lba * SECTOR_SIZE)
        f.write(data + bytes((-len(data)) % SECTOR_SIZE))
    if cnf:
        lba, new = cnf
        f.seek(lba * SECTOR_SIZE)
        f.write(new + bytes(SECTOR_SIZE - len(new)))
        for i, r in enumerate(all_):
            if key(r) == b'SYSTEM.CNF':
                rb = bytearray(r)
                struct.pack_into('<I', rb, 10, len(new))
                struct.pack_into('>I', rb, 14, len(new))
                all_[i] = bytes(rb)
        body = b''.join(all_)

    new_sector = bytearray(SECTOR_SIZE)
    new_sector[:len(body)] = body
    struct.pack_into('<I', new_sector, 10, len(body))     # the "." entry itself
    struct.pack_into('>I', new_sector, 14, len(body))
    f.seek(root_lba * SECTOR_SIZE)
    f.write(new_sector)
    struct.pack_into('<I', pvd, 156 + 10, len(body))
    struct.pack_into('>I', pvd, 156 + 14, len(body))
    f.seek(PVD_LBA * SECTOR_SIZE)
    f.write(pvd)
    f.close()
    print('')
    print('  written.')


if __name__ == '__main__':
    main()
