"""mc3_occlude.py - the city culling that is NOT in the .pck

I spent weeks editing `resources/city/<city>_<cond>.pck` thinking the whole city
lived there. It does not. What decides what shows on screen lives in
`ASSETS/city/<city>/`, and none of it is touched when the geometry is swapped:

    <city>.occlude        TEXT. Occluder boxes in world coordinates. Atlanta
                          has 340. Loaded by
                          `mcOccluderSystem::LoadDynamicGeomDataset` (its error
                          message is at 0x0065C8D0 in the retail ELF).
    <city>_city.aib       BINARY. Magic `TSV1`, tagged blocks
                          [u8 tag]['A'][u32 size][body]. Atlanta has 4396
                          28-byte records and 4713 of 20, plus the name
                          "atlanta" written inside. The game builds the name as
                          `city/%s/%s_city.aib`.
    <city>.graph          TEXT. Street graph. Its `MinExtents` matches EXACTLY
                          the field at MapRoot+0x250 of the .pck.
    <city>_pvs.aib        Same format as _city.aib, but the PS2 ELF has no
                          `pvs` string that builds this name - only `$cpvs`
                          (a .mod token) and `nopvs`. Probably not read in this
                          build.

An occluder box hides whatever is BEHIND it. Atlanta's 340 describe atlanta's
buildings; imported geometry that falls behind one of them vanishes, and there
is nothing on screen that explains why. Since the file is text, its
contribution can be tested in a minute: sink every box far below the world and
see how much comes back.

    python mc3_occlude.py                        # what exists today
    python mc3_occlude.py --neutralize           # sink the boxes (makes a backup)
    python mc3_occlude.py --restore              # bring the backup back

It is not the final version of anything: it is a switch to MEASURE how much of
"everything vanished" is occlusion and how much is something else.
"""
import argparse
import os
import re
import shutil

ROOT = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/city')
BACKGROUND = 100000.0          # how far to sink, in world units


def path(city):
    return os.path.join(ROOT, city, '%s.occlude' % city)


def le(city):
    with open(path(city), 'r', encoding='latin1') as f:
        return f.read()


def summary(text):
    """(version, num_geoms, num_primitives, [name of each occluder])."""
    def field(name):
        m = re.search(r'^%s:\s*(\d+)' % name, text, re.M)
        return int(m.group(1)) if m else -1
    names = re.findall(r'^;\|[^\r\n]*\|([^\t\r\n]+)\t\[(\d+)\]', text, re.M)
    return field('version'), field('num_geoms'), field('num_primitives'), names


def sink(text, dy=BACKGROUND):
    """Move every vertex and every sphere centre down by `dy`.

    Touching Y and not the count is on purpose. Zeroing `num_geoms` would change
    the size of everything after it and I do not know whether the reader accepts
    zero; this way the file keeps the SAME shape, the same number of boxes, the
    same order, and only their place changes. If the game hangs like this, the
    problem was not occlusion.
    """
    out_path = []
    for line in text.splitlines(True):
        s = line.rstrip('\r\n')
        end = line[len(s):]
        if s.startswith('sphere:'):
            p = s.split()
            if len(p) == 5:
                p[2] = '%.7f' % (float(p[2]) - dy)
                out_path.append(' '.join(p) + end)
                continue
        else:
            p = s.split('\t')
            if len(p) == 3:
                try:
                    v = [float(x) for x in p]
                except ValueError:
                    out_path.append(line)
                    continue
                v[1] -= dy
                out_path.append('\t'.join('%.7f' % x for x in v) + end)
                continue
        out_path.append(line)
    return ''.join(out_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--city', default='atlanta')
    ap.add_argument('--neutralize', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--root', default=ROOT)
    a = ap.parse_args()

    globals()['ROOT'] = a.root
    target = path(a.city)
    bak = target + '.orig'

    if a.restore:
        if not os.path.isfile(bak):
            raise SystemExit('no backup at %s' % bak)
        shutil.copyfile(bak, target)
        print('restored %s' % target)
        return

    text = le(a.city)
    ver, ng, npr, names = summary(text)
    print('%s: version %d, num_geoms %d, num_primitives %d, %d named box(es)'
          % (os.path.basename(target), ver, ng, npr, len(names)))
    for name, idx in names[:5]:
        print('   [%s] %s' % (idx, name))
    if len(names) > 5:
        print('   ... %d more' % (len(names) - 5))

    if not a.neutralize:
        return

    if not os.path.isfile(bak):
        shutil.copyfile(target, bak)
        print('backup at %s' % bak)
    new = sink(text)
    with open(target, 'w', encoding='latin1', newline='') as f:
        f.write(new)
    print('sunk %.0f units; %d bytes (was %d)'
          % (BACKGROUND, len(new), len(text)))
    print('to undo: python mc3_occlude.py --restore')


if __name__ == '__main__':
    main()
