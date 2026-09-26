"""mc3_coverage.py - how much of a city .pck we already know how to read

The question this script answers is "what is STILL missing". It walks the file
from the root, along exactly the paths both hexpats already use, marks every
byte some known structure covers, and at the end lists what is left - the
largest holes first, with the first u32 of each one as a hint.

A city .pck is loaded in place: the file IS the memory image, with
`file_offset = VA - 0x06800000 + 0x80`. So measuring coverage in the file is the
same as measuring it in RAM, only without the noise of what the game allocates
afterwards.

    python mc3_coverage.py <city.pck>
    python mc3_coverage.py <city.pck> --holes 40
    python mc3_coverage.py <city.pck> --dump 0x123456

TWO THINGS THE NUMBER DOES NOT SAY:

  * "covered" means some struct claims the byte, not that the field is
    EXPLAINED. The VIF packets count as covered whole and are opaque inside;
    the same goes for the texture pixels.
  * 0xCD padding and zeros are counted separately. They are not knowledge
    holes, they are alignment, and mixing the two would inflate the "missing".
"""
import argparse
import collections
import hashlib
import struct
import sys

BASE = 0x06800000
HDR = 0x80


class Pck:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        self.n = len(self.d)
        self.marks = bytearray(self.n)          # 0 = not covered
        self.rot = {}                           # id -> name
        self.count = collections.Counter()
        self.bytes_per_label = collections.Counter()
        # CONFLICT = someone claimed a byte another structure already had.
        # Not a detail: in an image loaded in place two structs cannot occupy
        # the same byte, so every conflict is an error in MY model.
        self.conflict = collections.Counter()
        self.conflict_pair = collections.Counter()
        self._prox = 1

    # ---- reading -----------------------------------------------------
    def off(self, va):
        return va - BASE + HDR

    def ok(self, o, size_=4):
        return o is not None and 0 <= o <= self.n - size_

    def u32(self, o):
        return struct.unpack_from('<I', self.d, o)[0] if self.ok(o) else None

    def u16(self, o):
        return struct.unpack_from('<H', self.d, o)[0] if self.ok(o, 2) else None

    def va_off(self, va):
        """offset of a VA, or None if the VA does not fall in the file."""
        if not va or not (BASE <= va < BASE + 0x01000000):
            return None
        o = self.off(va)
        return o if 0 <= o < self.n else None

    # ---- coverage ----------------------------------------------------
    def covers(self, o, size_, label):
        if o is None or size_ is None or size_ <= 0:
            return False
        if o < 0 or o + size_ > self.n:
            return False
        idx = self.rot.setdefault(label, len(self.rot) + 1)
        span = self.marks[o:o + size_]
        new_items = span.count(0)
        foreign = sum(1 for x in span if x and x != idx)
        if foreign:
            self.conflict[label] += foreign
            inv = {v: k for k, v in self.rot.items()}
            for x in set(span):
                if x and x != idx:
                    self.conflict_pair['%s  <-  %s' % (inv.get(x, x), label)] +=                         span.count(x)
        self.marks[o:o + size_] = bytes([idx]) * size_
        self.count[label] += 1
        self.bytes_per_label[label] += new_items
        return True

    # ---- report ------------------------------------------------------
    def holes(self):
        outside = []
        i = 0
        while i < self.n:
            if self.marks[i]:
                i += 1
                continue
            j = i
            while j < self.n and not self.marks[j]:
                j += 1
            outside.append((i, j - i))
            i = j
        return outside


def classify(p, o, size_):
    """name a hole from what is inside it."""
    s = p.d[o:o + size_]
    if not s.strip(b'\xcd'):
        return 'CD padding'
    if not s.strip(b'\x00'):
        return 'zeros'
    if not s.strip(b'\x00\xcd'):
        return 'zeros + CD'
    tex = s.split(b'\0')[0]
    if len(tex) >= 4 and all(32 <= c < 127 for c in tex):
        return 'text ("%s")' % tex[:24].decode('latin1')
    v = struct.unpack_from('<I', s, 0)[0] if len(s) >= 4 else 0
    if 0x00700000 <= v < 0x00800000:
        return 'starts with class token %08X' % v
    if BASE <= v < BASE + 0x01000000:
        return 'starts with pointer %08X' % v
    return 'starts with %08X' % v


# =====================================================================
# the walk
# =====================================================================

def discover_tokens(p):
    """The class tokens CHANGE from city to city.

    0x007A1E18 cannot be pinned as "rmcModelGeom": in tokyo the same class is
    0x007A22A0, because the token is serialised from the module address. So the
    token is DISCOVERED: take the most common among the lod[] targets of the
    ComponentData (mesh) and among the shader+parameter targets (texture).
    """
    u32, u16, vo = p.u32, p.u16, p.va_off
    R = HDR
    mesh = collections.Counter()
    nh, vh = u32(R + 0x14), vo(u32(R + 0x18))
    if vh is not None:
        for h in range(nh or 0):
            e = vh + 0x14 * h
            nu, au = u32(e + 4), vo(u32(e + 8))
            if au is None or not nu:
                continue
            for k in range(min(nu, 400)):
                cd = vo(u32(au + 0x1C * k + 0x0C))
                if cd is None:
                    continue
                for l in range(10):
                    target = vo(u32(cd + 0x3C + 4 * l))
                    if target is not None:
                        mesh[u32(target)] += 1
    tok_mesh = mesh.most_common(1)[0][0] if mesh else 0
    # the root texture gives the rmcTexturePS2 token, and the hood desc+4 confirms it
    tok_tex = u32(vo(u32(R + 0x1D4)) or 0) or 0
    return tok_mesh, tok_tex


def walk(p):
    d, u32, u16, vo = p.d, p.u32, p.u16, p.va_off
    TOK_MESH, TOK_TEX = discover_tokens(p)
    print('tokens of this city: rmcModelGeom=%08X  rmcTexturePS2=%08X'
          % (TOK_MESH, TOK_TEX))

    p.covers(0, HDR, 'pck header')
    R = HDR
    p.covers(R, 0x2290, 'MapRoot (with the two 256-entry palettes)')

    # ---- textures: reused by several owners, hence a function -----
    views = set()

    def texture(o, label='rmcTexturePS2'):
        # dispatch on the TOKEN, not on "looks like a pointer". Accepting any
        # target inflated the count with pieces of VIF data and produced conflicts.
        if o is None or o in views:
            return
        tok = u32(o)
        if tok == TOK_TEX - 0x240:           # rmcTextureReference, 0x10 bytes
            views.add(o)
            p.covers(o, 0x10, 'rmcTextureReference')
            name = vo(u32(o + 8))
            if name is not None:
                end = d.find(bytes(1), name)
                if 0 <= end < name + 128:
                    p.covers(name, end - name + 1, 'texture name')
            texture(vo(u32(o + 0x0C)))
            return
        if tok != TOK_TEX:                    # not an rmcTexturePS2
            return
        views.add(o)
        p.covers(o, 0x90, label)
        name = vo(u32(o + 0x78))
        if name is not None:
            end = d.find(b'\0', name)
            if 0 <= end < name + 128:
                p.covers(name, end - name + 1, 'texture name')
        for k in range(4):
            data = vo(u32(o + 0x48 + 12 * k))
            if data is None:
                continue
            p.covers(data, 0x90, 'MipBuffer')
            # the size comes from the level's TEX0, NOT from the MipBuffer +0x0C
            # field: that field covers the whole allocation and swallowed the
            # neighbours, inflating the coverage. Here it is only what the level
            # really occupies.
            tex0 = struct.unpack_from('<Q', d, o + 8 + 16 * k + 8)[0]
            w = 1 << ((tex0 >> 26) & 0xF)
            h = 1 << ((tex0 >> 30) & 0xF)
            psm = (tex0 >> 20) & 0x1F
            npix = w * h if psm == 0x13 else (w * h // 2 if psm == 0x14 else 0)
            if npix and npix < 0x400000:
                p.covers(data + 0x90, npix, 'pixels')
                mips = d[o + 0x86]
                if mips == k + 1:
                    p.covers(data + 0x90 + npix,
                            1024 if psm == 0x13 else 64, 'CLUT')

    # ---- meshes ----------------------------------------------------
    def mesh(o):
        # Check the TOKEN before walking. Without it a garbage lod[] led to
        # "meshes" whose tables fell inside a neighbouring ComponentData.
        if o is None or o in views or u32(o) != TOK_MESH:
            return
        views.add(o)
        p.covers(o, 0x20, 'rmcModelGeom (fields)')
        gc = u16(o + 8)
        if not gc or gc > 4096:
            return
        mt = vo(u32(o + 0x0C))
        if mt is not None:
            p.covers(mt, 2 * gc, 'mesh material table')
        gt = vo(u32(o + 0x10))
        if gt is not None:
            p.covers(gt - 0x10, 0x10, 'array count + padding')
            p.covers(gt, 8 * gc, 'GroupEntry')
            for g in range(gc):
                e = gt + 8 * g
                lst, nb = vo(u32(e)), u16(e + 4)
                if lst is None or not nb or nb > 4096:
                    continue
                p.covers(lst - 0x10, 0x10, 'array count + padding')
                p.covers(lst, 8 * nb, 'BlockEntry')
                for k in range(nb):
                    pk, qwc = vo(u32(lst + 8 * k)), u16(lst + 8 * k + 4)
                    if pk is not None and qwc:
                        p.covers(pk, 16 * qwc, 'VIF packet')
        # ------------------------------------------------------------
        # +0x14: TWO LEVELS of indirection down to one byte per vertex
        #
        #   +0x14 -> u32 slot[slot_count]              (cookie = slot_count)
        #     slot[k] -> {A, B}[group_count]           (cookie = group_count)
        #        A -> { u32 nblocks, u32 vertices[nblocks] }
        #        B -> u32 vector[nblocks]
        #              vector[j] -> u8 data[vertices[j]], aligned to 16
        #
        # Checked on sd_dusk_clear: 1327/1327 level-1 cookies match slot_count,
        # 5411/5411 level-2 ones match group_count, 24396/24396 of the A records
        # reproduce exactly the group's BlockEntry vertex_count values, and
        # 12627/12627 consecutive vectors sit at a distance of "vertices rounded
        # up to 16".
        # ------------------------------------------------------------
        pb = vo(u32(o + 0x14))
        sc = u16(o + 0x0A)
        if pb is not None and sc and sc <= 4096:
            p.covers(pb - 0x10, 0x10, 'array count + padding')
            # only 4*sc: the rest of the quadword is NOT padding, the allocator
            # keeps small records there (an A of {2,48,4} fits in 12 bytes)
            p.covers(pb, 4 * sc, 'slot table (+0x14)')
            for k in range(sc):
                level2 = vo(u32(pb + 4 * k))
                if level2 is None:
                    continue
                p.covers(level2 - 0x10, 0x10, 'array count + padding')
                p.covers(level2, 8 * gc, '{counts, vectors} entries per group')
                for g in range(gc):
                    ea, eb = vo(u32(level2 + 8 * g)), vo(u32(level2 + 8 * g + 4))
                    if ea is None or eb is None:
                        continue
                    n = u32(ea)
                    if not n or n > 4096:
                        continue
                    p.covers(ea, 4 + 4 * n, 'vertex counts per block')
                    p.covers(eb, 4 * n, 'pointer vector per block')
                    for j in range(n):
                        target = vo(u32(eb + 4 * j))
                        nv = u32(ea + 4 + 4 * j)
                        if target is not None and nv and nv < 0x10000:
                            p.covers(target, nv, 'one byte per vertex')

        for off_ in (0x18, 0x1C):
            q = vo(u32(o + off_))
            if q is not None and u32(q - 8) == 1:
                ln = u32(q - 4)
                if ln and ln < 0x10000:
                    p.covers(q - 8, 8 + ln, '+18/+1C pair (unexplained)')

    # ---- shaders ---------------------------------------------------
    def shader(o):
        # The size comes from the BITFIELD at +0x04, not from a per-token table:
        #   bits 22..24 = number of parameters
        #   bits 25..31 = mask, bit i set = parameter i is a TEXTURE
        # so the object takes 0x08 + 4*n and only the parameters that really
        # are textures need following.
        if o is None or o in views:
            return
        views.add(o)
        tok = u32(o)
        flags = u32(o + 4) or 0
        n = (flags >> 22) & 7
        mask = (flags >> 25) & 0x7F
        # SLOT 0 ALWAYS EXISTS, even with a zero count. city_facade (007A1EF8
        # here) has flags == 0 - index 0, no parameter - and still carries a
        # texture at +0x08, and packed objects of that kind sit 12 bytes apart.
        # Using 8+4*n gave 8 and left the texture and its pixels out: that was
        # 338 KB.
        nslots = max(n, 1)
        p.covers(o, 8 + 4 * nslots, 'shader %08X' % tok)
        for i in range(nslots):
            if i == 0 or (mask & (1 << i)):
                texture(vo(u32(o + 8 + 4 * i)))

    # ---- shader groups ---------------------------------------------
    ngrp, vgrp = u32(R + 0x08), vo(u32(R + 0x0C))
    if vgrp is not None:
        p.covers(vgrp, 0x10 * ngrp, 'ShaderGroupDesc')
        for g in range(ngrp):
            e = vgrp + 0x10 * g
            arr, n = vo(u32(e + 4)), u16(e + 8)
            if arr is None or not n:
                continue
            p.covers(arr, 4 * n, 'group slot array')
            for k in range(n):
                shader(vo(u32(arr + 4 * k)))
    sky = vo(u32(R + 0x10))
    if sky is not None:
        p.covers(sky, 0x10, 'sky ShaderGroupDesc')
        arr, n = vo(u32(sky + 4)), u16(sky + 8)
        if arr is not None and n:
            p.covers(arr, 4 * n, 'group slot array')
            for k in range(n):
                shader(vo(u32(arr + 4 * k)))

    # ---- cullable classes ------------------------------------------
    for off_ in (0x190, 0x194, 0x198):
        c = vo(u32(R + off_))
        if c is not None:
            p.covers(c, 0x40, 'CullableClass')

    # ---- root texture and proxy ------------------------------------
    texture(vo(u32(R + 0x1D4)), 'rmcTexturePS2 (root)')
    for k in range(20):
        texture(vo(u32(R + 0x1D8 + 4 * k)))
    px = vo(u32(R + 0x228))
    if px is not None:
        p.covers(px, 0x18, 'TextureProxy')
        n = u16(px)
        for field, mult, rot in ((0x04, 128, 'proxy: indices'),
                                 (0x08, 64, 'proxy: palettes'),
                                 (0x0C, 4, 'proxy: table')):
            a = vo(u32(px + field))
            if a is not None:
                p.covers(a, n * mult, rot)

    # ---- cell index -----------------------------------------------
    ci = vo(u32(R + 0x1C))
    cell_count = u32(R + 0x268)
    if ci is not None:
        # The CellIndex is not 0x18 bytes: the six pointers are only the head.
        # After them come ~29 KB ZEROED in the file, which are the visible
        # lists the runtime fills (+0x64 and +0x84, see the hexpat note). It
        # was the largest block left, and it is not content: it is a buffer.
        ua = vo(u32(ci + 0x08))
        body = (ua - 0x10 - ci) if (ua and ua > ci) else 0x18
        p.covers(ci, 0x18, 'CellIndex (head)')
        if 0x18 < body < 0x100000:
            p.covers(ci + 0x18, body - 0x18,
                    'CellIndex body (zeroed in the file, visible lists)')
        nu, ni = u32(ci), u32(ci + 4)
        for field, cnt, mult, rot in ((0x08, nu, 4, 'unique list'),
                                      (0x0C, ni, 4, 'instance list'),
                                      (0x10, cell_count, 0x0C, 'CellRecord'),
                                      (0x14, cell_count, 4, 'per-cell extra')):
            a = vo(u32(ci + field))
            if a is not None and cnt and cnt < 0x100000:
                p.covers(a, cnt * mult, rot)
        # +0x14 is not "a u32 per cell, undeciphered": it is a vector of
        # POINTERS per cell. Each target is
        #   { float; u16 na (bit15 = flag); u16 nb; u16 list[na+nb] }
        # aligned to 4. The values are an INSTANCE index in 14 bits plus a
        # 2-bit field on top. Checked: 2390 of 2391 records end exactly at the
        # start of the next one, zero outside.
        extra = vo(u32(ci + 0x14))
        if extra is not None and cell_count and cell_count < 0x100000:
            for i in range(cell_count):
                a = vo(u32(extra + 4 * i))
                if a is None:
                    continue
                na = (p.u16(a + 4) or 0) & 0x7FFF
                nb = p.u16(a + 6) or 0
                if na + nb < 0x8000:
                    p.covers(a, (8 + 2 * (na + nb) + 3) & ~3,
                            'per-cell instance list (+0x14)')

        cells = vo(u32(ci + 0x10))
        if cells is not None and cell_count and cell_count < 0x100000:
            for c in range(cell_count):
                e = cells + 0x0C * c
                for field, cnt in ((4, p.u16(e) or 0), (8, p.u16(e + 2) or 0)):
                    a = vo(u32(e + field))
                    if a is not None and cnt:
                        p.covers(a, 2 * cnt, 'cell list (u16)')

    # ---- hoods, components and instances --------------------------
    def component(o):
        if o is None or o in views:
            return
        views.add(o)
        p.covers(o, 0x90, 'ComponentData')
        for k in range(10):
            mesh(vo(u32(o + 0x3C + 4 * k)))

    nh, vh = u32(R + 0x14), vo(u32(R + 0x18))
    if vh is not None:
        p.covers(vh, 0x14 * nh, 'HoodEntry')
        for h in range(nh):
            e = vh + 0x14 * h
            desc = vo(u32(e))
            if desc is not None:
                p.covers(desc, 4, 'hood name')
                texture(desc + 4, 'rmcTexturePS2 (hood)')
            nu, au = u32(e + 4), vo(u32(e + 8))
            ni, ai = u32(e + 0x0C), vo(u32(e + 0x10))
            if au is not None and nu and nu < 0x100000:
                p.covers(au, 0x1C * nu, 'UniqueComponent')
                for k in range(nu):
                    component(vo(u32(au + 0x1C * k + 0x0C)))
            if ai is not None and ni and ni < 0x100000:
                p.covers(ai, 0x60 * ni, 'Instance')
                for k in range(ni):
                    component(vo(u32(ai + 0x60 * k + 0x0C)))


def orphan_meshes(p, tok_mesh):
    """Meshes that exist in the file and that no pointer reaches.

    sd has 3 and detroit 2; atlanta and tokyo have none. All five are an EXACT
    DUPLICATE of a mesh in use - the same geometry byte for byte and the same
    material table - only ownerless. It is a leftover of the process that built
    the pck, not a structure still to be understood.

    The function returns (orphans, groups of repeated geometry). Note that
    repeated geometry WITH an owner on both sides is something else and is
    legitimate: the st_*_01x / st_*_01ax pairs are the same road with different
    materials.
    """
    d, u32, u16 = p.d, p.u32, p.u16
    target = struct.pack('<I', tok_mesh)
    pos = []
    i = 0
    while True:
        i = d.find(target, i)
        if i < 0 or i > p.n - 0x30:
            break
        if i % 16 == 0:
            pos.append(i)
        i += 4
    pointed = set()
    for a in range(0, p.n - 4, 4):
        v = u32(a)
        if v and BASE <= v < BASE + p.n:
            pointed.add(v)

    def digital(a):
        gc, gt = u16(a + 8), p.va_off(u32(a + 0x10))
        mt = p.va_off(u32(a + 0x0C))
        if gt is None or mt is None:
            return None
        h = hashlib.md5()
        for g in range(min(gc, 4096)):
            lst, nb = p.va_off(u32(gt + 8 * g)), u16(gt + 8 * g + 4)
            if lst is None:
                return None
            for k in range(min(nb or 0, 4096)):
                pk = p.va_off(u32(lst + 8 * k))
                n = 16 * (u16(lst + 8 * k + 4) or 0)
                if pk is None:
                    return None
                h.update(d[pk:pk + n])
        return h.hexdigest()

    per_geo = {}
    for a in pos:
        g = digital(a)
        if g:
            per_geo.setdefault(g, []).append(a)
    orphans_ = [a for a in pos if (a + BASE - HDR) not in pointed]
    repeated_ = [v for v in per_geo.values() if len(v) > 1]
    return pos, orphans_, repeated_


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pck')
    ap.add_argument('--holes', type=int, default=25,
                    help='how many holes to list (default 25)')
    ap.add_argument('--dump', help='show 128 bytes from this offset (hex)')
    a = ap.parse_args()

    p = Pck(a.pck)
    if a.dump:
        o = int(a.dump, 16)
        for k in range(0, 128, 16):
            s = p.d[o + k:o + k + 16]
            print('%08X  %-47s  %s' % (o + k, ' '.join('%02X' % x for x in s),
                  ''.join(chr(x) if 32 <= x < 127 else '.' for x in s)))
        return 0

    walk(p)
    covered = p.n - p.marks.count(0)
    print('%s  %d bytes' % (a.pck, p.n))
    print('covered by known structures: %d  (%.1f%%)'
          % (covered, 100.0 * covered / p.n))
    print()
    print('%-42s %8s %12s' % ('structure', 'count', 'bytes'))
    for rot, nb in p.bytes_per_label.most_common():
        print('%-42s %8d %12d' % (rot, p.count[rot], nb))

    if p.conflict:
        print()
        print('CONFLICTS (byte claimed by two structs - a model error)')
        for rot, nb in p.conflict_pair.most_common(12):
            print('%-64s %10d' % (rot, nb))

    outside = p.holes()
    by_kind = collections.Counter()
    kind_bytes = collections.Counter()
    for o, size_ in outside:
        t = classify(p, o, size_)
        if t.startswith('starts with') or t.startswith('text'):
            t = t.split(' (')[0].split(' %08X')[0]
            t = 'unmapped data' if 'starts' in t else 'loose text'
        by_kind[t] += 1
        kind_bytes[t] += size_
    print()
    print('LEFT OVER: %d spans, %d bytes (%.1f%%)'
          % (len(outside), p.n - covered, 100.0 * (p.n - covered) / p.n))
    print('%-24s %8s %12s' % ('nature', 'spans', 'bytes'))
    for t, nb in kind_bytes.most_common():
        print('%-24s %8d %12d' % (t, by_kind[t], nb))

    # Whose is the neighbour BEHIND each hole: it is the most useful hint for
    # where to continue - the hole is almost always the continuation of a
    # structure I marked too short, or its sibling.
    inv = {v: k for k, v in p.rot.items()}
    before = collections.Counter()
    bytes_before = collections.Counter()
    for o, size_ in outside:
        rot = inv.get(p.marks[o - 1], '(start of file)') if o else '(start)'
        before[rot] += 1
        bytes_before[rot] += size_
    print()
    print('HOLE BY NEIGHBOUR BEHIND')
    print('%-42s %8s %12s' % ('comes right after', 'spans', 'bytes'))
    for rot, nb in bytes_before.most_common(12):
        print('%-42s %8d %12d' % (rot, before[rot], nb))

    tok_mesh, _ = discover_tokens(p)
    if tok_mesh:
        all_items, orphans_, repeated_ = orphan_meshes(p, tok_mesh)
        print()
        print('MESHES: %d in the file, %d OWNERLESS, %d groups of repeated geometry'
              % (len(all_items), len(orphans_), len(repeated_)))
        for o in orphans_:
            print('   orphan at %08X' % o)

    print()
    print('THE %d LARGEST HOLES' % a.holes)
    print('%-10s %10s  %s' % ('offset', 'bytes', 'what it looks like'))
    for o, size_ in sorted(outside, key=lambda x: -x[1])[:a.holes]:
        print('%08X %10d  %s' % (o, size_, classify(p, o, size_)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
