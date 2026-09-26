"""
mc3_modloader.py - mod overlay for Midnight Club 3 PS2, using the engine's own
                   ZIP fallback

The trick is that the engine already has an override mechanism, it just shipped
switched off. When a loose file fails to open, the resource opener replaces the
last `/` of the path with `.zip` and looks for the basename inside:

    ASSETS/resources/city/atlanta_bnd.pck   missing
    -> ASSETS/resources/city.zip, entry "atlanta_bnd.pck"

It walks local file headers and reads uncompressed-size bytes RAW, so entries
must be STORED - there is no inflate in the game. The whole thing is gated on one
byte at VA 0x618D00 (file offset 0x478D80) that nothing in the game ever writes.
Confirmed working in game.

So the arrangement is the inverse of the obvious one: **vanilla goes into the
zips, mods stay loose on top.** The loose file wins, the zip is only consulted on
failure, so installing a mod is copying a file and uninstalling is deleting it -
vanilla comes back by itself. No hardlinks, no junctions, no composed tree, and
7.9 GB of loose files collapses into a handful of archives.

    python mc3_modloader.py enable   --elf SLUS.ELF        turn the flag on
    python mc3_modloader.py pack     ASSETS/resources/city  vanilla -> city.zip
    python mc3_modloader.py install  mods/my_building       copy over the tree
    python mc3_modloader.py remove   mods/my_building       delete what it added
    python mc3_modloader.py check    ASSETS/resources/city  validate .pck headers

A mod folder mirrors the asset tree, e.g. mods/my_building/resources/city/*.pck.
"""

import argparse
import os
import shutil
import struct
import zipfile

FLAG_VA = 0x618D00

# The type id a .pck must carry, by how its path looks. A wrong id makes the
# loader return 0 in silence and eleven of the thirteen consumers then spin on a
# `while(1);` left over from a stripped assert - a black screen with no message.
# That is the single failure this tool exists to catch before the game boots.
KINDS = [
    ('_bnd', 9), ('_fog', 4), ('_traffic', 60), ('_peds', 33),
    ('hudmap', 44), ('/prop/', 64), ('_midnight_', 67),
]


def flag_offset(elf):
    d = open(elf, 'rb').read()
    phoff, = struct.unpack_from('<I', d, 28)
    _t, p_off, p_va, _pa, p_filesz = struct.unpack_from('<5I', d, phoff)
    off = FLAG_VA - p_va + p_off
    if not (p_off <= off < p_off + p_filesz):
        raise SystemExit('%s: 0x%X is outside the loaded segment' % (elf, FLAG_VA))
    return d, off


def enable(elf, out):
    d, off = flag_offset(elf)
    print('%s: flag byte at file 0x%06X = %02X' % (os.path.basename(elf), off, d[off]))
    if d[off] == 1:
        print('  already on')
        return
    b = bytearray(d)
    b[off] = 1
    open(out, 'wb').write(bytes(b))
    print('  wrote %s with the ZIP fallback on (one byte)' % out)


def pck_info(path):
    """(type id, ok, why) for a .pck, or (None, True, '') for anything else."""
    if not path.lower().endswith('.pck'):
        return None, True, ''
    with open(path, 'rb') as f:
        h = f.read(16)
    if len(h) < 16:
        return None, False, 'shorter than a header'
    _base, kind, ver, size = struct.unpack('<4I', h)
    real = os.path.getsize(path) - 128
    if ver != 1:
        return kind, False, 'version %d, the loader only accepts 1' % ver
    if size != real:
        return kind, False, 'body size %d but the file holds %d' % (size, real)
    p = path.replace('\\', '/').lower()
    for marks, expected in KINDS:
        if marks in p and kind != expected:
            return kind, False, ('type id %d, but a path like this is loaded as %d'
                                 % (kind, expected))
    return kind, True, ''


def check(root):
    mau = tot = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            kind, ok, why = pck_info(p)
            if kind is None:
                continue
            tot += 1
            if not ok:
                mau += 1
                print('  BAD  %s: %s' % (os.path.relpath(p, root), why))
    print('%d .pck checked, %d rejected' % (tot, mau))
    return mau


def pack(folder, erase):
    """Move the loose files of ONE directory into `<directory>.zip`.

    Not recursive on purpose: the game derives the archive name from the last
    separator of the path it wanted, so a file in `resources/city/` is only ever
    looked up in `resources/city.zip`. Subdirectories need their own archive.
    """
    folder = folder.rstrip('/\\')
    target = folder + '.zip'
    files = [f for f in sorted(os.listdir(folder))
             if os.path.isfile(os.path.join(folder, f))]
    if not files:
        raise SystemExit('%s has no files directly in it' % folder)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(os.path.join(folder, f), f)
    # read it back before touching the originals
    with zipfile.ZipFile(target) as z:
        inside = {i.filename: i for i in z.infolist()}
        for f in files:
            i = inside.get(f)
            if i is None or i.compress_type != zipfile.ZIP_STORED:
                raise SystemExit('%s did not round-trip as stored - originals kept' % f)
            if i.file_size != os.path.getsize(os.path.join(folder, f)):
                raise SystemExit('%s changed size - originals kept' % f)
    print('%s: %d files -> %s (%d bytes, stored)'
          % (folder, len(files), os.path.basename(target), os.path.getsize(target)))
    if erase:
        for f in files:
            os.remove(os.path.join(folder, f))
        print('  removed the loose originals; the game now reads them from the zip')
    else:
        print('  loose originals kept - they still WIN over the zip, so nothing '
              'changes until you pass --erase')


def pack_all(root, erase, minimum):
    """Pack EVERY directory of the tree, one archive each.

    There is no single `assets.zip`: the game derives the archive name from the
    path by replacing its LAST separator with `.zip`, so
    `ASSETS/resources/city/x.pck` is only ever looked for in
    `ASSETS/resources/city.zip`. One global archive would only serve files
    sitting directly under ASSETS. So a tree of 22513 files becomes a few dozen
    archives, not one.

    Directories with fewer than `minimo` files are left loose - an archive that
    holds two files buys nothing and only adds a place for the two to disagree.
    """
    made = skipped_ = 0
    for dirpath, _dirs, files in os.walk(root):
        if dirpath == root:
            continue                      # ASSETS/<file> would need assets.zip
        real_ones = [f for f in files if os.path.isfile(os.path.join(dirpath, f))]
        if len(real_ones) < minimum:
            skipped_ += 1
            continue
        pack(dirpath, erase)
        made += 1
    print()
    print('%d directories packed, %d skipped for having fewer than %d files'
          % (made, skipped_, minimum))


def _pairs(mod, root):
    for dirpath, _dirs, files in os.walk(mod):
        for f in files:
            src = os.path.join(dirpath, f)
            yield src, os.path.join(root, os.path.relpath(src, mod))


def install(mod, root, force):
    bad = [(s, pck_info(s)) for s, _d in _pairs(mod, root)]
    bad = [(s, w) for s, (_t, ok, w) in bad if not ok]
    if bad and not force:
        for s, w in bad:
            print('  BAD  %s: %s' % (os.path.relpath(s, mod), w))
        raise SystemExit('refusing to install; a bad header is a silent black '
                         'screen, not an error message. --force to override')
    n = 0
    for src, dst in _pairs(mod, root):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print('installed %d files from %s' % (n, mod))
    print('  they are loose, so they win over the zips; removing them restores vanilla')


def remove(mod, root):
    n = 0
    for _src, dst in _pairs(mod, root):
        if os.path.isfile(dst):
            os.remove(dst)
            n += 1
    print('removed %d files; vanilla comes back from the zips' % n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=('enable', 'pack', 'pack-all', 'install',
                                    'remove', 'check'))
    ap.add_argument('target', nargs='?')
    ap.add_argument('--elf', help='the executable, for `enable`')
    ap.add_argument('--out', help='where `enable` writes the patched executable')
    ap.add_argument('--root', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS'),
                    help='the loose ASSETS tree the game reads')
    ap.add_argument('--erase', action='store_true',
                    help='`pack` deletes the loose originals after verifying the zip')
    ap.add_argument('--minimum', type=int, default=4,
                    help='pack-all skips directories with fewer files than this')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    if a.action == 'enable':
        if not a.elf:
            raise SystemExit('enable needs --elf')
        enable(a.elf, a.out or os.path.splitext(a.elf)[0] + '_ZIP.ELF')
    elif a.action == 'pack':
        pack(a.target, a.erase)
    elif a.action == 'pack-all':
        pack_all(a.target or a.root, a.erase, a.minimum)
    elif a.action == 'check':
        raise SystemExit(1 if check(a.target or a.root) else 0)
    elif a.action == 'install':
        install(a.target, a.root, a.force)
    else:
        remove(a.target, a.root)


if __name__ == '__main__':
    main()
