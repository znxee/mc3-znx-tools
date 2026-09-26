"""
mc3_city_strip.py - empty a Midnight Club 3 PS2 city, keeping only what you ask for

For testing an imported model in isolation. Everything the city draws is a mesh
hanging off the hood graph, and every mesh carries a group count as a u16 at
`mesh+0x08`; setting it to zero makes the draw loop and the per-block chain both
iterate nothing, so the mesh stops existing visually without a single pointer
moving. Two bytes per mesh, and the file stays structurally identical.

The collision is NOT touched: `<city>_bnd.pck` is a separate file, so the car
still drives on the real ground even where nothing is drawn. That is usually
what you want for a test - keep a radius of scenery for orientation and let the
invisible collision hold the car up everywhere else.

Which meshes to keep is decided by position rather than by name. Names do encode
the hood (`a_dt_` downtown, `a_fwy_` freeway, `a_bh_rd_` roads and so on) but a
radius around the thing under test picks up its ground and its neighbours
without having to know the naming.

The spawn point is not in this file. It is `ASSETS/tune/city/<city>.reset`, a
51-byte text file the executable really does read (`tune/city` appears 21 times
in the ELF):

    2
    308.5 0.2 173.6 358.6 Garage
    0 0 0 0 Origin

which is `count`, then `x y z heading name` per line. Add a line there to start
next to whatever you are testing.

    python mc3_city_strip.py atlanta_midnight_clear.pck --near 580,406,300 --out out.pck
    python mc3_city_strip.py atlanta_midnight_clear.pck --keep "ghto_06|_rd_" --out out.pck
"""

import argparse
import collections
import os
import re
import struct

NGRP_AT = 0x08            # u16: how many groups the mesh has
GT_AT = 0x10              # pointer to the group table


class City:
    """A city .pck, far enough parsed to find every mesh and who owns it."""

    def __init__(self, path):
        self.data = bytearray(open(path, 'rb').read())
        self.path = path
        base, kind, version, size = struct.unpack_from('<4I', self.data, 0)
        if kind not in (67, 4):
            raise SystemExit('%s: container type %d, not a city (67) or fog (4)'
                             % (os.path.basename(path), kind))
        if size != len(self.data) - 128:
            raise SystemExit('%s: body size %d does not match the file' % (path, size))
        self.base = base - 0x80
        self.tag = self._mesh_tag()
        self.owners = self._owners()

    def u32(self, o):
        return struct.unpack_from('<I', self.data, o)[0]

    def u16(self, o):
        return struct.unpack_from('<H', self.data, o)[0]

    def off(self, v):
        return v - self.base

    def ok(self, v):
        return self.base + 0x80 <= v < self.base + len(self.data)

    def _mesh_tag(self):
        """The mesh tag is build residue and differs per city - atlanta, detroit
        and san diego use 0x007A1E18 while tokyo uses 0x007A22A0 - so it is
        voted for, not hardcoded. Voting on the value alone is not enough: raw
        vertex data hits the range often enough to win. The candidate has to be
        followed by a plausible group count and a group table inside the file.

        The range covers BOTH forms: a retail file stores a TOKEN (0x007A....),
        a file we generate stores the address that token translates to at load
        (0x0062....), because an object we create never enters the list the game
        translates. Accepting only the token range made this blind to our own
        output."""
        votes = collections.Counter()
        for o in range(0x80, len(self.data) - 0x20, 4):
            v = self.u32(o)
            if 0x600000 <= v < 0x7C0000:
                if 1 <= self.u16(o + NGRP_AT) <= 64 and self.ok(self.u32(o + GT_AT)):
                    votes[v] += 1
        if not votes:
            raise SystemExit('no mesh tag found in %s' % os.path.basename(self.path))
        return votes.most_common(1)[0][0]

    def _grid(self):
        """(cell_x, origin_x, cell_z, origin_z), fitted by least squares.

        Every placed instance offers both a real position and the culling tile
        box at +0x14, so the grid can be recovered from the file instead of
        assumed. It comes out at 40 units per cell in all four cities with a
        per-city origin. That is what gives a position to the meshes hanging off
        UNIQUE components, which carry a tile box but no transform - the roads
        are all like that, and without this they get dropped and the test car
        drives over nothing.
        """
        pts = []
        R = 0x80
        nh, hp = self.u32(R + 0x14), self.u32(R + 0x18)
        if not self.ok(hp) or nh >= 64:
            return None
        hb = self.off(hp)
        for h in range(nh):
            e = hb + 20 * h
            cnt, arr = self.u32(e + 0x0C), self.u32(e + 0x10)
            if not self.ok(arr) or cnt > 20000:
                continue
            ab = self.off(arr)
            for k in range(cnt):
                c = ab + 96 * k
                q = struct.unpack_from('<3f', self.data, c + 0x44)
                if not all(-5000 < x < 5000 for x in q):
                    continue
                pts.append((q[0], q[2],
                            (self.data[c + 0x14] + self.data[c + 0x15] + 1) / 2.0,
                            (self.data[c + 0x16] + self.data[c + 0x17] + 1) / 2.0))
        if len(pts) < 32:
            return None

        def fit(ti, vi):
            n = len(pts)
            st = sum(p[ti] for p in pts)
            sv = sum(p[vi] for p in pts)
            stt = sum(p[ti] * p[ti] for p in pts)
            stv = sum(p[ti] * p[vi] for p in pts)
            den = n * stt - st * st
            if not den:
                return None
            a = (n * stv - st * sv) / den
            return a, (sv - a * st) / n

        fx, fz = fit(2, 0), fit(3, 1)
        if not fx or not fz or not (20.0 < fx[0] < 80.0 and 20.0 < fz[0] < 80.0):
            return None
        return fx[0], fx[1], fz[0], fz[1]

    def _owners(self):
        """{mesh offset: (name, [(x, y, z), ...])} through hood -> component -> data.

        ALL positions, not just the first. A mesh reused by several instances
        gets one entry per instance, because keeping it by proximity has to
        consider every place it is used: `a_inst_low_ghto_06x` is placed three
        times in atlanta and its first instance is 1400 units from its third, so
        recording only the first drops the very model under test.

        The translation is the fourth row of the instance's 3x4 at +0x20. All
        ten slots of `buffers[2][5]` at +0x3C are read, not just the first - the
        others hold the LOD variants of the same model.
        """
        out = {}
        grid = self._grid()
        R = 0x80
        nh, hp = self.u32(R + 0x14), self.u32(R + 0x18)
        if not self.ok(hp) or nh >= 64:
            return out
        hb = self.off(hp)
        for h in range(nh):
            e = hb + 20 * h
            for cnt, arr, stride, placed in ((self.u32(e + 0x04), self.u32(e + 0x08), 28, False),
                                             (self.u32(e + 0x0C), self.u32(e + 0x10), 96, True)):
                if not self.ok(arr) or cnt > 20000:
                    continue
                ab = self.off(arr)
                for k in range(cnt):
                    comp = ab + stride * k
                    dp = self.u32(comp + 0x0C)
                    if not self.ok(dp):
                        continue
                    cd = self.off(dp)
                    nm = bytes(self.data[cd + 0x70:cd + 0xB0]).split(b'\0')[0]
                    nm = nm.decode('latin1', 'ignore')
                    pos = None
                    if placed:
                        q = struct.unpack_from('<3f', self.data, comp + 0x44)
                        if all(-1e5 < c < 1e5 for c in q):
                            pos = q
                    elif grid:
                        cx, ox, cz, oz = grid
                        pos = (cx * ((self.data[comp + 0x14] + self.data[comp + 0x15] + 1) / 2.0) + ox,
                               0.0,
                               cz * ((self.data[comp + 0x16] + self.data[comp + 0x17] + 1) / 2.0) + oz)
                    for slot in range(10):
                        mp = self.u32(cd + 0x3C + 4 * slot)
                        if not self.ok(mp):
                            continue
                        name, items = out.setdefault(self.off(mp), (nm, []))
                        if pos is not None:
                            items.append(pos)
        return out

    def meshes(self):
        return [o for o in range(0x80, len(self.data) - 0x20, 4)
                if self.u32(o) == self.tag
                and 1 <= self.u16(o + NGRP_AT) <= 64
                and self.ok(self.u32(o + GT_AT))]


def strip(city, keep):
    """Zero the group count of every mesh `keep` rejects. Returns (kept, emptied)."""
    kept = emptied = 0
    for mo in city.meshes():
        nm, pos = city.owners.get(mo, ('', []))
        if keep(mo, nm, pos):
            kept += 1
            continue
        struct.pack_into('<H', city.data, mo + NGRP_AT, 0)
        emptied += 1
    return kept, emptied


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pck')
    ap.add_argument('--near', metavar='X,Z,RADIUS',
                    help='keep every mesh whose instance sits within RADIUS of (X, Z)')
    ap.add_argument('--keep', metavar='REGEX', help='keep meshes whose name matches')
    ap.add_argument('--ownerless', action='store_true',
                    help='keep meshes with no owner too (they have no position, '
                         'so --near cannot judge them)')
    ap.add_argument('--out', help='where to write the stripped city')
    a = ap.parse_args()

    city = City(a.pck)
    all_ = city.meshes()
    print('%s: tag %08X, %d meshes, %d with an owner'
          % (os.path.basename(a.pck), city.tag, len(all_), len(city.owners)))

    rx = re.compile(a.keep, re.I) if a.keep else None
    cx = cz = rad = None
    if a.near:
        cx, cz, rad = (float(x) for x in a.near.split(','))

    def keep(mo, nm, positions):
        if rx and nm and rx.search(nm):
            return True
        if not positions:
            return bool(a.ownerless)
        if rad is None:
            return False
        # any instance inside the radius is enough
        return any((p[0] - cx) ** 2 + (p[2] - cz) ** 2 <= rad * rad for p in positions)

    if rx is None and rad is None:
        raise SystemExit('nothing would be kept; pass --near and/or --keep')

    stay = [(mo, city.owners.get(mo, ('', []))) for mo in all_
             if keep(mo, *city.owners.get(mo, ('', [])))]
    kept, emptied = strip(city, keep)
    print('  kept %d, emptied %d' % (kept, emptied))
    names = collections.Counter(nm for _mo, (nm, _p) in stay if nm)
    if names:
        print('  kept by name: %s%s'
              % (', '.join('%s x%d' % kv for kv in names.most_common(10)),
                 ' ...' if len(names) > 10 else ''))
    if not kept:
        print('  WARNING: nothing kept - the city will be completely empty')

    out = a.out or (os.path.splitext(a.pck)[0] + '_strip.pck')
    open(out, 'wb').write(bytes(city.data))
    print('wrote %s (%d bytes, %d changed)'
          % (out, len(city.data), 2 * emptied))
    if a.near:
        print()
        print('to start next to it, add a line to ASSETS/tune/city/<city>.reset')
        print('and bump the count on its first line:')
        print('    %.1f 0.2 %.1f 0.0 Test' % (cx, cz))


if __name__ == '__main__':
    main()
