"""
mc2_rsc.py - read the PS2 build of Midnight Club 2's city resources

The PC build of MC2 keeps the city in text (`.xmod`), which `mc2_port.py`
already handles. The PS2 build does not: `resource/<city>/<city>.rsc` is a
container of finished PS2 display lists, and this file reads them.

    python mc2_rsc.py list   <file.rsc>
    python mc2_rsc.py names  <file.rnt|file.rsc>   [--filter SUBSTR]
    python mc2_rsc.py models <file.rsc>            [--filter SUBSTR]
    python mc2_rsc.py dump   <file.rsc> <index>    # VIF/DMA disassembly
    python mc2_rsc.py check  <file.rsc>            # the structural self-tests
    python mc2_rsc.py hood   <file.rsc>            # the .hood layout beside it
    python mc2_rsc.py cpv    <file.rsc> [--hour T] [--filter S]  # the colours
    python mc2_rsc.py errors <file.rsc>            # score the placement mistakes
    python mc2_rsc.py obj    <file.rsc> [--out X.obj] [--filter S] [--lod N]
    python mc2_rsc.py map    <file.rsc> [--out X.png] [--lod N] [--colour HOUR]

Everything below was measured out of the data. No instruction of the game was
read to get here, and `check` re-runs every assertion the decoder rests on.

CONTAINER (`RSC0`)
    +0x00 'RSC0'
    +0x04 u32 count
    +0x08 count x [u32 offset][char4 fourcc]
    A block's size is the difference to the next entry's offset; the last one
    runs to end of file. Data begins after the table.

NAME TABLE (`RNT0`, the sibling `.rnt`)
    +0x00 'RNT0'  +0x04 u32 count  +0x08 16 reserved bytes
    +0x18 count x [u32 string offset][char4 fourcc][u32 handle]
    NUL-terminated strings follow the table.

HANDLE - the currency of the whole format
    (handle & 0x3FFF) - 1 is a ZERO-BASED index into a container's entry table.
    Bit 14 (0x4000) picks WHICH container: set = this level's `.rsc`, clear =
    the shared texture bank `textures_*.rsc`. Handle 0 means none.

CMI0 - 64 bytes, one per named city model
    +0x00 float centre x, y, z
    +0x0C float radius            (cull sphere; it matches the geometry - see
                                   the last test in `check`)
    +0x10 4 slots, each [u32 handle][u32 handle] -> PMD0
          slots 0,1 are the near LOD and slots 2,3 the far one
    +0x30 u32 flags (only 0, 6, 7 and 8 occur)
    +0x34 12 zero bytes

PMD0 - a DMA source chain feeding VIF1: a finished display list. Every tag is
a `call` except the last, which is a `ret`. Tag transfer is on, so the upper
64 bits of each tag are two VIF codes that run before the tag's own data.

    tag[0]      tagvif  nop; flush
                payload stcycl cl=3 wl=1
                        unpack V3-32 @40  quantisation bias (float)
                                          - or S-32 @40 = 0 on float meshes
                        unpack V4-32 @41  centre.xyz + w
                        mscal 2           (VU setup entry)
                        ... then one or more vertex batches
    tag[k>0]    tagvif  itop <vu base>; mscal 8   (kicks a batch already sent)
                payload further vertex batches

    A batch, at VU base B (B is what `itop` carries):
        stmask 0x50505050 / strow 2048.0.. / stmod 1 / unpack V2-16u @B+2
        stmask 0x40404040 / strow 131072.0 / stmod 1 / unpack V3-16u @B+4
            or, on float meshes,            stmod 0 / unpack V3-32  @B+4 mask
        stmask 0x3f3f3f3f                            / unpack S-8   @B+4 mask
    Vertex stride is 3 quadwords (stcycl cl=3 wl=1). Slot B+3 of a vertex is
    never written here: that is where the per-vertex colour from
    `<time>_cpvs.rsc` lands.

    position  world = (131072.0 + raw/64.0) - bias      (raw is u16), or the
              float straight from the stream on a float mesh
    uv        u = raw/4096.0
    The W of the position quadword carries an 8-bit flag per vertex:
    0x80 = do not draw, 0x00 = draw. Element 0 of that stream is NOT a flag,
    it is the vertex count of the batch. The vertices are one triangle strip
    and the flag is the GS ADC bit.

    The `addr` of a `call` tag is a texture handle, not an address. Because the
    DMAC transfers a tag's own data before it jumps, the texture a batch draws
    with is the handle on the tag BEFORE the one whose `mscal` kicks it.

PLACEMENT - and there are two kinds of it
    Every CMI0 plays exactly one of two roles, and the two partition the set:

    A `unique_component` has its geometry ALREADY in world coordinates. The
    placement went into the bias at VU address 40 and there is no matrix to
    look for. Run `map` and the street plan comes out.

    An instance `type` is a template, placed by `instance_component` records
    that carry a real 3x4 transform, row-vector convention:
    world = p * rot + pos.

    Both kinds are listed in the plain-text `.hood` files under
    `assets/city/<name>/` - the sibling of `assets/resource/`, which holds only
    the compiled form. `check` proves the two halves against each other.

        city         CMI0   components  types   instances   worst spill
        losangeles    723      294        429      8500        0.024
        paris         699      270        429      8195        0.017
        tokyo         865      455        410      7963        0.015

    Do NOT identify components by the `#geom` suffix. That works in Los Angeles
    and nowhere else: Paris and Tokyo split large blocks into `#geom1`,
    `#geom2`, `#geom3`. The reliable test is the partition above.

    The matrices SCALE, and not always uniformly - 1734 of the 8500 Los Angeles
    instances have a scale other than 1 and 1487 of those are non-uniform, up
    to a factor of 11.3. 24 of them have a NEGATIVE determinant, so their
    triangle winding is mirrored; the PC build of the same city has none,
    because there the mirrored pieces are separate `_flip_` meshes instead.

BUGS OF PLACEMENT - `errors` scores them, `map --simulate` draws them
    A port that puts the pieces in roughly the right district but breaks the
    joins between them has one of a small number of bugs, and the `.hood`
    settles which: every instance carries its own emin/emax, so placing the
    template the wrong way and measuring the spill out of that box tells you
    how much damage each mistake does. Los Angeles, 8500 instances:

        hypothesis        break   median    p90      max
        correct            0        0.00    0.01     0.02
        transposed      4404        0.67   15.00   216.47
        scale dropped    533        0.00    0.01    11.49
        pivot re-centred 7728        5.00   15.25  1825.77
        translation only 5204        2.93   23.71   216.47

    The cheap discriminator is the 1864 instances whose rotation is the
    IDENTITY matrix. Transposing does nothing to them, and neither does
    dropping the scale. So if the identity-rotation instances are ALSO out of
    place in a build, the bug is not the matrix convention - look at the pivot,
    which is the one failure that moves almost everything.

COLOUR - `<time of day>_cpvs.rsc`, and it is the real draw chain
    The geometry chain leaves one quadword slot per vertex empty. The colour
    container fills it: `CPP0` is a 256-entry palette and each `PCP0` is a DMA
    chain that `ref`s the geometry straight out of a PMD0 and supplies one
    palette index per vertex. So a PCP0 is not an annotation on the model - it
    IS the draw, and `read_pcp` gives back position, uv, ADC and colour
    together. See the block comment above `TIMES` for the layout and for how
    the names bind a PCP0 to a `.hood` instance.

    The nine files are NOT day, dusk and night. They differ completely byte for
    byte, palettes included, but the colour that comes out is nearly the same:
    mean RGB distance 3.5 between midnight and dawn, 4.7 between midnight and
    dusk, over 29244 vertices compared. The per-vertex colour is baked LOCAL
    light - lamp pools, corner shading - and whatever makes dawn look like dawn
    lives somewhere else. `map --colour midnight_clear` renders it.
"""

import argparse
import collections
import math
import os
import re
import struct
import sys

import mc3_output


def _out_path(item_name):
    """Output path, with its directory created. Keeps this tool inside the
    output/<assunto>/ convention the forks agreed on."""
    path = mc3_output.path(item_name)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    return path


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

class Rsc(object):
    """One `.rsc` container plus, when it exists, its `.rnt` name table."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as fh:
            self.data = fh.read()
        if self.data[:4] != b'RSC0':
            raise ValueError('%s: not RSC0 (magic %r)' % (path, self.data[:4]))
        count = struct.unpack_from('<I', self.data, 4)[0]
        raw = [struct.unpack_from('<I4s', self.data, 8 + 8 * i)
               for i in range(count)]
        self.entries = []
        for i, (off, fcc) in enumerate(raw):
            end = raw[i + 1][0] if i + 1 < count else len(self.data)
            self.entries.append((off, fcc.decode('ascii', 'replace'), end - off))
        self.names = {}
        self._byindex = None
        self._read_names()
        self._shared = None
        self._cache = {}

    def _read_names(self):
        rnt = os.path.splitext(self.path)[0] + '.rnt'
        if not os.path.isfile(rnt):
            return
        with open(rnt, 'rb') as fh:
            d = fh.read()
        if d[:4] != b'RNT0':
            return
        for i in range(struct.unpack_from('<I', d, 4)[0]):
            soff, fcc, handle = struct.unpack_from('<I4sI', d, 0x18 + 12 * i)
            end = d.index(b'\0', soff)
            self.names[handle] = (fcc.decode('ascii', 'replace'),
                                  d[soff:end].decode('ascii', 'replace'))

    def name(self, index):
        """Name of the entry at `index`, whatever bit picks this container.

        The handle's high bits name the CONTAINER, and they differ per file:
        the city `.rsc` uses bit 14, `ambients_*` and `*_cpvs` use bit 15, the
        texture bank uses none. Asking for one fixed bit silently returned None
        for all of the others.
        """
        if self._byindex is None:
            top = collections.Counter(h >> 14 for h in self.names)
            main = top.most_common(1)[0][0] if top else 1
            self._byindex = {(h & 0x3FFF) - 1: nm for h, (fcc, nm) in self.names.items()
                             if h >> 14 == main}
        return self._byindex.get(index)

    def shared(self):
        """The `textures_*.rsc` bank that handles without bit 14 index."""
        if self._shared is None:
            p = os.path.join(os.path.dirname(self.path), 'textures_original.rsc')
            self._shared = Rsc(p) if os.path.isfile(p) else False
        return self._shared or None

    def resolve(self, handle):
        """(container, index) for a handle, or None."""
        if not handle:
            return None
        index = (handle & 0x3FFF) - 1
        box = self if (handle & 0x4000) else self.shared()
        if box is None or not (0 <= index < len(box.entries)):
            return None
        return box, index

    def block(self, index):
        off, fcc, size = self.entries[index]
        return self.data[off:off + size]

    def indices(self, fourcc):
        return [i for i, e in enumerate(self.entries) if e[1] == fourcc]

    def texture_name(self, handle):
        got = self.resolve(handle)
        if got is None:
            return None
        box, index = got
        if box.entries[index][1] != 'TEX0':
            return None
        off = box.entries[index][0]
        return box.data[off + 0xA0:off + 0xE0].split(b'\0')[0].decode(
            'ascii', 'replace')


# ---------------------------------------------------------------------------
# VIF / DMA
# ---------------------------------------------------------------------------

DMA_ID = {0: 'refe', 1: 'cnt', 2: 'next', 3: 'ref',
          4: 'refs', 5: 'call', 6: 'ret', 7: 'end'}

VIF_SIMPLE = {0x00: 'nop', 0x01: 'stcycl', 0x02: 'offset', 0x03: 'base',
              0x04: 'itop', 0x05: 'stmod', 0x06: 'mskpath3', 0x07: 'mark',
              0x10: 'flushe', 0x11: 'flush', 0x13: 'flusha', 0x14: 'mscal',
              0x15: 'mscalf', 0x17: 'mscnt'}

VN = ('S', 'V2', 'V3', 'V4')
VL = ('32', '16', '8', '5')


class Vif(object):
    """One decoded VIF code and the inline data it consumed."""

    __slots__ = ('off', 'cmd', 'num', 'imm', 'data')

    def __init__(self, off, cmd, num, imm):
        self.off, self.cmd, self.num, self.imm = off, cmd, num, imm
        self.data = b''

    @property
    def name(self):
        if self.cmd in VIF_SIMPLE:
            return VIF_SIMPLE[self.cmd]
        if 0x60 <= self.cmd <= 0x7F:
            return 'unpack'
        return {0x20: 'stmask', 0x30: 'strow', 0x31: 'stcol', 0x4A: 'mpg',
                0x50: 'direct', 0x51: 'directhl'}.get(self.cmd,
                                                      '?%02x' % self.cmd)

    vn = property(lambda self: (self.cmd >> 2) & 3)
    vl = property(lambda self: self.cmd & 3)
    masked = property(lambda self: bool((self.cmd >> 4) & 1))
    addr = property(lambda self: self.imm & 0x3FF)
    usn = property(lambda self: bool((self.imm >> 14) & 1))
    count = property(lambda self: self.num or 256)

    def __str__(self):
        n = self.name
        if n == 'stcycl':
            return 'stcycl cl=%d wl=%d' % (self.imm & 0xFF, self.imm >> 8)
        if n in ('itop', 'mscal', 'mscalf', 'stmod', 'offset', 'base', 'mark',
                 'mskpath3'):
            return '%s %#x' % (n, self.imm)
        if n == 'stmask':
            return 'stmask %#010x' % struct.unpack_from('<I', self.data, 0)[0]
        if n in ('strow', 'stcol'):
            return '%s %s' % (n, ' '.join('%#010x' % v for v in
                                          struct.unpack_from('<4I', self.data, 0)))
        if n == 'unpack':
            return 'unpack %s-%s%s num=%d addr=%d%s' % (
                VN[self.vn], VL[self.vl], 'u' if self.usn else 's',
                self.count, self.addr, ' mask' if self.masked else '')
        return n


def unpack_size(vif):
    """Bytes of inline data an unpack consumes, rounded up to a word."""
    if vif.vl == 3:                            # V4-5: one halfword per element
        size = vif.count * 2
    else:
        size = vif.count * (vif.vn + 1) * (32 >> vif.vl) // 8
    return (size + 3) & ~3


def vif_codes(buf):
    """Decode a word stream of VIF codes with their inline data."""
    out = []
    i, n = 0, len(buf)
    while i + 4 <= n:
        code = struct.unpack_from('<I', buf, i)[0]
        v = Vif(i, (code >> 24) & 0xFF, (code >> 16) & 0xFF, code & 0xFFFF)
        i += 4
        if v.cmd == 0x20:
            v.data, i = buf[i:i + 4], i + 4
        elif v.cmd in (0x30, 0x31):
            v.data, i = buf[i:i + 16], i + 16
        elif v.cmd == 0x4A:
            take = (v.num or 256) * 8
            v.data, i = buf[i:i + take], i + take
        elif v.cmd in (0x50, 0x51):
            take = (v.imm or 65536) * 16
            v.data, i = buf[i:i + take], i + take
        elif 0x60 <= v.cmd <= 0x7F:
            take = unpack_size(v)
            v.data, i = buf[i:i + take], i + take
        elif v.cmd not in VIF_SIMPLE:
            out.append(v)
            break
        out.append(v)
    return out


class Tag(object):
    __slots__ = ('off', 'id', 'qwc', 'addr', 'irq', 'tagvif', 'payload')


def dma_chain(buf):
    """Walk a DMA source chain that lives entirely inside `buf`.

    Returns (tags, bytes consumed). The caller compares the second value with
    the block size: that is the structural test that the walk was right.
    """
    tags = []
    p = 0
    while p + 16 <= len(buf):
        lo, addr = struct.unpack_from('<II', buf, p)
        t = Tag()
        t.off = p
        t.id = DMA_ID[(lo >> 28) & 7]
        t.qwc = lo & 0xFFFF
        t.addr = addr
        t.irq = (lo >> 31) & 1
        t.tagvif = buf[p + 8:p + 16]
        t.payload = buf[p + 16:p + 16 + t.qwc * 16]
        tags.append(t)
        p += 16 + t.qwc * 16
        if t.id in ('refe', 'end', 'ret'):
            break
    return tags, p


# ---------------------------------------------------------------------------
# PMD0 -> geometry
# ---------------------------------------------------------------------------

class Batch(object):
    """One VU1 kick: a single triangle strip drawn with one texture."""

    __slots__ = ('base', 'pos', 'uv', 'adc', 'cpv', 'texture', 'entry')

    def __init__(self, base):
        self.base = base
        self.pos = []
        self.uv = []
        self.adc = []
        self.cpv = []           # palette indices, only filled by read_pcp
        self.texture = 0
        self.entry = 8

    def triangles(self):
        """Indices into `pos`, honouring the ADC flag and strip winding."""
        out = []
        for k in range(2, len(self.pos)):
            if k < len(self.adc) and (self.adc[k] & 0x80):
                continue
            out.append((k - 2, k - 1, k) if k % 2 == 0 else (k - 1, k - 2, k))
        return out


def read_pmd(rsc, index):
    """Decode one PMD0 block into the batches it draws, in draw order."""
    if index in rsc._cache:
        return rsc._cache[index]
    off, fcc, size = rsc.entries[index]
    if fcc != 'PMD0':
        raise ValueError('entry %d is %s, not PMD0' % (index, fcc))
    tags, used = dma_chain(rsc.data[off:off + size])
    if used != size:
        raise ValueError('entry %d: chain used %d of %d bytes'
                         % (index, used, size))

    bias = (0.0, 0.0, 0.0)
    params = None
    slots = {}          # VU base -> the Batch most recently uploaded there
    drawn = []
    texture = 0         # handle set by the last `call` the DMAC passed
    itop = 0

    def slot(base, field):
        """The batch being filled at `base`; a fresh one if that field is
        already taken, which is how a re-upload to the same base is caught."""
        b = slots.get(base)
        if b is None or getattr(b, field):
            b = Batch(base)
            slots[base] = b
        return b

    for tag in tags:
        for v in vif_codes(tag.tagvif):
            if v.name == 'itop':
                itop = v.imm
            elif v.name == 'mscal' and v.imm != 2:
                b = slots.get(itop)
                if b is not None:
                    b.texture = texture
                    b.entry = v.imm
                    drawn.append(b)
        for v in vif_codes(tag.payload):
            if v.name != 'unpack':
                continue
            if v.addr == 40:
                bias = (struct.unpack_from('<3f', v.data, 0)
                        if (v.vn, v.vl) == (2, 0) else (0.0, 0.0, 0.0))
            elif v.addr == 41:
                params = struct.unpack_from('<4f', v.data, 0)
            elif (v.vn, v.vl) == (1, 1):                    # V2-16 -> uv
                b = slot(v.addr - 2, 'uv')
                b.uv = [tuple(x / 4096.0 for x in
                              struct.unpack_from('<2H', v.data, 4 * k))
                        for k in range(v.count)]
            elif (v.vn, v.vl) == (2, 1):                    # V3-16 -> position
                b = slot(v.addr - 4, 'pos')
                b.pos = [tuple((131072.0 + raw / 64.0) - bias[c]
                               for c, raw in enumerate(
                                   struct.unpack_from('<3H', v.data, 6 * k)))
                         for k in range(v.count)]
            elif (v.vn, v.vl) == (2, 0):                    # V3-32 -> position
                b = slot(v.addr - 4, 'pos')
                b.pos = [struct.unpack_from('<3f', v.data, 12 * k)
                         for k in range(v.count)]
            elif (v.vn, v.vl) == (0, 2):                    # S-8 -> adc flags
                b = slot(v.addr - 4, 'adc')
                b.adc = list(v.data[:v.count])
        if tag.id == 'call':
            texture = tag.addr

    rsc._cache[index] = (drawn, bias, params, tags)
    return rsc._cache[index]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Model(object):
    __slots__ = ('index', 'name', 'centre', 'radius', 'lods', 'flags')


def read_models(rsc):
    out = []
    for i in rsc.indices('CMI0'):
        off = rsc.entries[i][0]
        m = Model()
        m.index = i
        m.name = rsc.name(i) or '<%d>' % i
        cx, cy, cz, r = struct.unpack_from('<4f', rsc.data, off)
        m.centre, m.radius = (cx, cy, cz), r
        m.lods = [struct.unpack_from('<2I', rsc.data, off + 0x10 + 8 * j)
                  for j in range(4)]
        m.flags = struct.unpack_from('<I', rsc.data, off + 0x30)[0]
        out.append(m)
    return out


def models_by_name(rsc):
    """Lowercased name -> Model. The `.hood` files disagree with the `.rnt`
    on capitalisation (`l_inst_SoCalGasprt01_x`), so never match case."""
    return {m.name.lower(): m for m in read_models(rsc)}


def model_points(rsc, model, lod=-1):
    """Every decoded world-space vertex of a model."""
    pts = []
    for h in model_blocks(model, lod):
        got = rsc.resolve(h)
        if got is not None:
            for b in read_pmd(rsc, got[1])[0]:
                pts.extend(b.pos)
    return pts


def model_blocks(model, lod=None):
    """PMD0 handles of a model.

    Slots 0 and 1 hold the near LOD, slots 2 and 3 the far one, so `lod=0`
    takes the first pair, `lod=1` the second. `lod=None` prefers the near LOD
    and falls back to the far one; `lod=-1` takes everything.
    """
    flat = [[h for h in pair if h] for pair in model.lods]
    if lod is None:
        near = flat[0] + flat[1]
        return near if near else flat[2] + flat[3]
    if lod < 0:
        return [h for group in flat for h in group]
    return flat[2 * lod] + flat[2 * lod + 1]


# ---------------------------------------------------------------------------
# The `.hood` layer - where the city is laid out
# ---------------------------------------------------------------------------
#
# `assets/resource/<city>/` holds the compiled geometry; `assets/city/<city>/`
# holds the plain-text layout that produced it. The two halves name the same
# things, so they can be checked against each other - `check` does exactly that.
#
#   <city>.lvl        extents, grid dimensions, the list of hoods
#   <hood>.hood       what stands where:
#       unique_component N { name: <CMI0 name>  emin x y z  emax x y z }
#           A model whose geometry was baked in world coordinates. There is no
#           matrix because it does not need one.
#       instance_component N { type: <CMI0 name>  owner: ...  extension: N
#           <3 rows of the rotation>          <- row-vector convention
#           <1 row of the translation>
#           emin x y z  emax x y z }
#           A model drawn from a shared template, with a real transform.
#
# Los Angeles: 294 unique_component (= the 294 CMI0 named `*#geom`) and 8500
# instance_component over 429 types (= the 429 CMI0 that are not `*#geom`).

# ---------------------------------------------------------------------------
# The colour layer - `<time of day>_cpvs.rsc`
# ---------------------------------------------------------------------------
#
# The per-vertex colour is NOT in the geometry. It lives in its own container,
# one per time of day, holding `PCP0` blocks plus a single `CPP0`.
#
#   CPP0   256 x [R G B A], the palette. A is always 0x80 - the PS2 convention
#          where 0x80 is fully opaque.
#   PCP0   a DMA chain that draws ONE model with colour. It does not repeat the
#          geometry: it `ref`s it out of a PMD0 and only supplies the colours.
#
# A PCP0 mirrors its source PMD0 tag for tag. Its `ref` list is exactly the
# source's payload tags, in order, minus the final one-quadword padding tag,
# which the PCP0 re-emits itself. The ref address is not an address:
#
#       ref addr = ((source entry index + 1) << 20) | byte offset
#
# and the low nibble of that (which the DMAC ignores) carries a flag worth 4 or
# 5. It is constant inside a block and correlates with neither the pass, the
# LOD, nor the source, so what it selects is unknown - masking the address with
# 0xFFFF0 reads all 13756 blocks correctly.
#
# The colours arrive as `unpack S-8u` at VU address B+3 - the one slot per
# vertex the geometry chain leaves empty. Unlike the ADC stream, element 0 IS a
# colour, not a count.
#
# NAMES bind the layer together, from the sibling `.rnt`:
#       <component>_<lod>_<main|refl>
#       <owner>_<type><extension>_<lod>_<main|refl>
# where <extension> is the `extension:` field of the `.hood` instance. That
# field is unique within a type, so (type, extension) names an instance
# exactly - verified on 8432 of 8432 with the owner as the check.
#
# WHAT THE COLOURS ACTUALLY ARE: baked LOCAL light, not the time of day. All
# nine files differ completely byte for byte - different palettes AND different
# indices - yet the colour that comes out is nearly identical: mean RGB
# distance 3.5 between midnight and dawn, 4.7 between midnight and dusk. So the
# nine sets are nine re-quantisations of almost the same data, and whatever
# makes dawn look like dawn is somewhere else.

TIMES = ('dawn_clear', 'dawn_cloudy', 'dawn_rainy',
         'dusk_clear', 'dusk_cloudy', 'dusk_rainy',
         'midnight_clear', 'midnight_cloudy', 'midnight_rainy')

_PAYLOAD = {}


def payload_map(rsc):
    """PMD0 index -> {(byte offset, qwc)} its own chain would transfer.

    The ref address carries only 12 bits of the source index, so above 4095 it
    is ambiguous; matching the (offset, qwc) pair picks the right candidate.
    """
    key = id(rsc)
    if key not in _PAYLOAD:
        m = {}
        for i, e in enumerate(rsc.entries):
            if e[1] != 'PMD0':
                continue
            off, fcc, size = e
            tags, used = dma_chain(rsc.data[off:off + size])
            m[i] = {(t.off + 16, t.qwc) for t in tags if t.qwc}
        _PAYLOAD[key] = m
    return _PAYLOAD[key]


def open_cpvs(rsc, time_of_day='midnight_clear'):
    """The `<time>_cpvs.rsc` beside a city container, or None."""
    p = os.path.join(os.path.dirname(rsc.path), time_of_day + '_cpvs.rsc')
    return Rsc(p) if os.path.isfile(p) else None


def palette(cpvs):
    """CPP0 -> [(R, G, B, A)]."""
    got = cpvs.indices('CPP0')
    if not got:
        return []
    off, fcc, size = cpvs.entries[got[0]]
    return [tuple(cpvs.data[off + 4 * k:off + 4 * k + 4]) for k in range(size // 4)]


_INLINE = ('cnt', 'call', 'ret', 'end', 'next')


def pcp_chain(cpvs, index):
    """Walk a PCP0 chain.

    A `ref` takes its data from elsewhere, so the next tag follows immediately;
    every other id carries its data inline. A tag's own two VIF codes and the
    data after it are ONE stream - an unpack in the tag reads the payload that
    follows it - so they are returned joined.
    """
    off, fcc, size = cpvs.entries[index]
    raw = cpvs.data[off:off + size]
    tags, p = [], 0
    while p + 16 <= size:
        lo, addr = struct.unpack_from('<II', raw, p)
        qwc = lo & 0xFFFF
        did = DMA_ID[(lo >> 28) & 7]
        inline = did in _INLINE
        tags.append((p, did, qwc, addr,
                     raw[p + 8:p + 16 + (qwc * 16 if inline else 0)]))
        p += 16 + (qwc * 16 if inline else 0)
        if did in ('refe', 'end', 'ret'):
            break
    return tags, p


def pcp_stream(rsc, cpvs, index):
    """(the bytes VIF1 really sees, [(byte position, texture handle)])."""
    pmap = payload_map(rsc)
    out = bytearray()
    marks = []
    for (p, did, qwc, addr, s) in pcp_chain(cpvs, index)[0]:
        out += s[:8]
        if did == 'ref':
            src = ((addr >> 20) & 0xFFF) - 1
            want = addr & 0xFFFF0
            for cand in (src, src + 4096, src + 8192):
                if (want, qwc) in pmap.get(cand, ()):
                    o = rsc.entries[cand][0]
                    out += rsc.data[o + want:o + want + qwc * 16]
                    break
            else:
                return None, None
        else:
            out += s[8:]
        if did == 'call':
            marks.append((len(out), addr))
    return bytes(out), marks


def read_pcp(rsc, cpvs, index):
    """Batches with geometry AND colour, in draw order."""
    buf, marks = pcp_stream(rsc, cpvs, index)
    if buf is None:
        return []
    bias = (0.0, 0.0, 0.0)
    slots, drawn = {}, []
    itop = ti = texture = 0
    for v in vif_codes(buf):
        while ti < len(marks) and marks[ti][0] <= v.off:
            texture = marks[ti][1]
            ti += 1
        if v.name == 'itop':
            itop = v.imm
        elif v.name == 'mscal':
            if v.imm != 2 and itop in slots:
                b = slots[itop]
                b.texture = texture
                b.entry = v.imm
                drawn.append(b)
        elif v.name == 'unpack':
            if len(v.data) < unpack_size(v):
                continue
            if v.addr in (40, 41):
                if v.addr == 40:
                    bias = (struct.unpack_from('<3f', v.data, 0)
                            if (v.vn, v.vl) == (2, 0) else (0.0, 0.0, 0.0))
                continue
            kind = (v.vn, v.vl)
            if kind == (1, 1):
                base, field = v.addr - 2, 'uv'
            elif kind in ((2, 1), (2, 0)):
                base, field = v.addr - 4, 'pos'
            elif kind == (0, 2):
                # the ADC byte rides in the W of the position quadword at B+4;
                # a colour index lands in its own slot at B+3
                prev = slots.get(v.addr - 4)
                if prev is not None and prev.pos and not prev.adc:
                    base, field = v.addr - 4, 'adc'
                else:
                    base, field = v.addr - 3, 'cpv'
            else:
                continue
            b = slots.get(base)
            if b is None or getattr(b, field):
                b = Batch(base)
                slots[base] = b
            if field == 'uv':
                b.uv = [tuple(x / 4096.0 for x in
                              struct.unpack_from('<2H', v.data, 4 * k))
                        for k in range(v.count)]
            elif field == 'pos':
                if kind == (2, 1):
                    b.pos = [tuple((131072.0 + raw / 64.0) - bias[c]
                                   for c, raw in enumerate(
                                       struct.unpack_from('<3H', v.data, 6 * k)))
                             for k in range(v.count)]
                else:
                    b.pos = [struct.unpack_from('<3f', v.data, 12 * k)
                             for k in range(v.count)]
            elif field == 'adc':
                b.adc = list(v.data[:v.count])
            else:
                b.cpv = list(v.data[:v.count])
    return drawn


_PCP_INST = re.compile(r'^(.*?)_(l_inst_.*?)(\d+)_(\d+)_(main|refl)$', re.I)
_PCP_COMP = re.compile(r'^(.*?)_(\d+)_(main|refl)$')


def cpv_entries(cpvs):
    """[(index, kind, key, lod, pass)] for every PCP0.

    kind 'inst' -> key is (type, extension); kind 'comp' -> key is the model
    name. Both are lowercased.
    """
    out = []
    for handle, (fcc, name) in cpvs.names.items():
        if fcc != 'PCP0':
            continue
        index = (handle & 0x3FFF) - 1
        m = _PCP_INST.match(name)
        if m:
            out.append((index, 'inst', (m.group(2).lower(), int(m.group(3))),
                        int(m.group(4)), m.group(5)))
            continue
        m = _PCP_COMP.match(name)
        if m:
            out.append((index, 'comp', m.group(1).lower(),
                        int(m.group(2)), m.group(3)))
    return out


NUMBER = r'(-?\d+\.\d+)'
_EMIN = re.compile(r'emin ' + ' '.join([NUMBER] * 3))
_EMAX = re.compile(r'emax ' + ' '.join([NUMBER] * 3))
_ROW = re.compile(r'^\t' + r'\t'.join([NUMBER] * 3) + r'\s*$', re.M)


class Component(object):
    """A `unique_component`: geometry already in world coordinates."""

    __slots__ = ('hood', 'name', 'emin', 'emax')


class Instance(object):
    """An `instance_component`: a template plus the transform to place it."""

    __slots__ = ('hood', 'type', 'owner', 'extension', 'rot', 'pos',
                 'emin', 'emax')

    def apply(self, point):
        """Row-vector convention: world = point * rot + pos. Verified against
        the stored emin/emax on all 8500 Los Angeles instances."""
        r, t = self.rot, self.pos
        return (point[0] * r[0][0] + point[1] * r[1][0] + point[2] * r[2][0] + t[0],
                point[0] * r[0][1] + point[1] * r[1][1] + point[2] * r[2][1] + t[1],
                point[0] * r[0][2] + point[1] * r[1][2] + point[2] * r[2][2] + t[2])

    def apply_wrong(self, point, how, centre=(0.0, 0.0, 0.0)):
        """Place the point the WAY A PORT GETS IT WRONG, to reproduce a
        symptom on purpose. `outside()` against the instance's own emin/emax
        then measures the damage - see BUGS OF PLACEMENT in the module doc.
        """
        r, t = self.rot, self.pos
        if how == 'transposed':                     # R * p + T
            return (r[0][0] * point[0] + r[0][1] * point[1] + r[0][2] * point[2] + t[0],
                    r[1][0] * point[0] + r[1][1] * point[1] + r[1][2] * point[2] + t[1],
                    r[2][0] * point[0] + r[2][1] * point[1] + r[2][2] * point[2] + t[2])
        if how == 'no-scale':                     # rows normalised to length 1
            n = [math.dist((0, 0, 0), q) or 1.0 for q in r]
            r = [[r[a][b] / n[a] for b in range(3)] for a in range(3)]
            self_r, self.rot = self.rot, r
            try:
                return self.apply(point)
            finally:
                self.rot = self_r
        if how == 'aabb-pivot':                      # template re-centred first
            return self.apply([point[k] - centre[k] for k in range(3)])
        if how == 'translation-only':                  # matrix ignored
            return (point[0] + t[0], point[1] + t[1], point[2] + t[2])
        raise ValueError('unknown mode: %s' % how)


ERRORS = ('transposed', 'no-scale', 'aabb-pivot', 'translation-only')


def city_folder(rsc_path):
    """`assets/city/<name>/` for a `assets/resource/<name>/<name>.rsc`."""
    folder = os.path.dirname(os.path.abspath(rsc_path))
    name = os.path.basename(folder)
    guess = os.path.join(os.path.dirname(os.path.dirname(folder)), 'city', name)
    return guess if os.path.isdir(guess) else None


def read_hoods(folder):
    """(components, instances) from every `.hood` in a city folder."""
    comps, insts = [], []
    if not folder:
        return comps, insts
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.hood'):
            continue
        with open(os.path.join(folder, fn)) as fh:
            text = fh.read()
        for blk in re.finditer(r'unique_component \d+ \{(.*?)\n\}', text, re.S):
            body = blk.group(1)
            c = Component()
            c.hood = fn
            c.name = re.search(r'name:\s*(\S+)', body).group(1)
            c.emin = [float(v) for v in _EMIN.search(body).groups()]
            c.emax = [float(v) for v in _EMAX.search(body).groups()]
            comps.append(c)
        for blk in re.finditer(r'instance_component \d+ \{(.*?)\n\}', text, re.S):
            body = blk.group(1)
            rows = [[float(v) for v in r] for r in _ROW.findall(body)]
            if len(rows) != 4:
                continue
            i = Instance()
            i.hood = fn
            i.type = re.search(r'type:\s*(\S+)', body).group(1)
            i.owner = re.search(r'owner:\s*(\S+)', body).group(1)
            i.extension = int(re.search(r'extension:\s*(\d+)', body).group(1))
            i.rot, i.pos = rows[:3], rows[3]
            i.emin = [float(v) for v in _EMIN.search(body).groups()]
            i.emax = [float(v) for v in _EMAX.search(body).groups()]
            insts.append(i)
    return comps, insts


def outside(points, emin, emax):
    """How far `points` spill out of the box, 0.0 when they all fit."""
    lo = [min(p[k] for p in points) for k in range(3)]
    hi = [max(p[k] for p in points) for k in range(3)]
    return max(max(emin[k] - lo[k], 0.0) + max(hi[k] - emax[k], 0.0)
               for k in range(3))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    rsc = Rsc(args.file_)
    kinds = collections.Counter(e[1] for e in rsc.entries)
    total = collections.Counter()
    for off, fcc, size in rsc.entries:
        total[fcc] += size
    named = collections.Counter(v[0] for v in rsc.names.values())
    print('%s: %d entries, %d bytes' % (os.path.basename(rsc.path),
                                        len(rsc.entries), len(rsc.data)))
    print('%-6s %7s %12s %10s' % ('type', 'count', 'bytes', 'named'))
    for fcc, n in kinds.most_common():
        print('%-6s %7d %12d %10d' % (fcc, n, total[fcc], named.get(fcc, 0)))
    return 0


def cmd_names(args):
    path = args.file_
    if path.lower().endswith('.rsc'):
        path = os.path.splitext(path)[0] + '.rnt'
    with open(path, 'rb') as fh:
        d = fh.read()
    if d[:4] != b'RNT0':
        sys.exit('%s: not RNT0' % path)
    count = struct.unpack_from('<I', d, 4)[0]
    print('%s: %d names' % (os.path.basename(path), count))
    for i in range(count):
        soff, fcc, handle = struct.unpack_from('<I4sI', d, 0x18 + 12 * i)
        name = d[soff:d.index(b'\0', soff)].decode('ascii', 'replace')
        if args.filter_ and args.filter_ not in name:
            continue
        print('  %-5s %#07x  idx %5d  %s' % (fcc.decode('ascii', 'replace'),
                                             handle, (handle & 0x3FFF) - 1, name))
    return 0


def cmd_models(args):
    rsc = Rsc(args.file_)
    print('%-44s %9s %9s %9s %8s %5s %s' %
          ('name', 'x', 'y', 'z', 'radius', 'flags', 'slots'))
    shown = 0
    for m in read_models(rsc):
        if args.filter_ and args.filter_ not in m.name:
            continue
        shown += 1
        slots = ' '.join('-' if not any(p) else
                         ','.join('%d' % ((h & 0x3FFF) - 1) for h in p if h)
                         for p in m.lods)
        print('%-44s %9.1f %9.1f %9.1f %8.1f %5d  %s' %
              (m.name[:44], m.centre[0], m.centre[1], m.centre[2],
               m.radius, m.flags, slots))
    print('%d models' % shown)
    return 0


def cmd_dump(args):
    rsc = Rsc(args.file_)
    index = int(args.index, 0)
    off, fcc, size = rsc.entries[index]
    print('entry %d  %s  offset %#x  size %d  name %s'
          % (index, fcc, off, size, rsc.name(index)))
    if fcc != 'PMD0':
        raw = rsc.block(index)
        for o in range(0, min(len(raw), 512), 16):
            row = raw[o:o + 16]
            print('%06X  %-47s %s' % (o, ' '.join('%02X' % b for b in row),
                                      ''.join(chr(b) if 32 <= b < 127 else '.'
                                              for b in row)))
        return 0
    tags, used = dma_chain(rsc.data[off:off + size])
    print('%d tags, chain uses %d of %d bytes' % (len(tags), used, size))
    for k, t in enumerate(tags):
        tex = rsc.texture_name(t.addr) if t.id == 'call' else None
        print('[%2d] +%#06x %-4s qwc=%-4d addr=%#07x%s   %s'
              % (k, t.off, t.id, t.qwc, t.addr,
                 ('  tex=%s' % tex) if tex else '',
                 '; '.join(str(v) for v in vif_codes(t.tagvif))))
        for v in vif_codes(t.payload):
            extra = '  [%d B]' % len(v.data) if v.name == 'unpack' else ''
            print('        +%#06x  %s%s' % (v.off, v, extra))
    batches = read_pmd(rsc, index)[0]
    print('-> %d batches, %d vertices, %d triangles'
          % (len(batches), sum(len(b.pos) for b in batches),
             sum(len(b.triangles()) for b in batches)))
    return 0


def cmd_check(args):
    rsc = Rsc(args.file_)
    fails = []

    def test(label, ok, detail=''):
        print('  %-52s %s%s' % (label, 'PASS' if ok else 'FAIL',
                                ('   ' + detail) if detail else ''))
        if not ok:
            fails.append(label)

    pmd = rsc.indices('PMD0')
    print('%s: %d PMD0, %d CMI0, %d TEX0'
          % (os.path.basename(rsc.path), len(pmd), len(rsc.indices('CMI0')),
             len(rsc.indices('TEX0'))))

    residue, ids, badtex, ntex = [], collections.Counter(), 0, 0
    for i in pmd:
        off, fcc, size = rsc.entries[i]
        tags, used = dma_chain(rsc.data[off:off + size])
        if used != size:
            residue.append(i)
        for t in tags:
            ids[t.id] += 1
            if t.id == 'call' and t.addr:
                ntex += 1
                got = rsc.resolve(t.addr)
                if got is None or got[0].entries[got[1]][1] != 'TEX0':
                    badtex += 1
    test('the DMA chain consumes the whole block', not residue,
         '%d with leftovers' % len(residue))
    test('only call tags, ending in a single ret',
         set(ids) == {'call', 'ret'} and ids['ret'] == len(pmd), str(dict(ids)))
    test('every call addr resolves to a TEX0', badtex == 0,
         '%d of %d failed' % (badtex, ntex))

    good = bad = 0
    other = collections.Counter()
    nostream = 0
    for i in pmd:
        for b in read_pmd(rsc, i)[0]:
            if not (b.pos and b.adc and b.uv):
                nostream += 1
                continue
            if b.adc[0] == len(b.pos):
                good += 1
            else:
                bad += 1
            for f in b.adc[1:]:
                other[f] += 1
    test('flag[0] of a batch is the vertex count', bad == 0,
         '%d ok, %d not, %d incomplete' % (good, bad, nostream))
    test('the other flags are only 0x00 and 0x80', set(other) <= {0x00, 0x80},
         str(sorted(other)))

    seen, kinds = collections.Counter(), collections.Counter()
    for m in read_models(rsc):
        for h in model_blocks(m, -1):
            got = rsc.resolve(h)
            if got is None:
                kinds['?'] += 1
                continue
            seen[got[1]] += 1
            kinds[got[0].entries[got[1]][1]] += 1
    test('CMI0 slots point only to PMD0', set(kinds) == {'PMD0'},
         str(dict(kinds)))
    test('every PMD0 is referenced exactly once',
         all(seen.get(i, 0) == 1 for i in pmd),
         'ownerless %d, repeated %d'
         % (sum(1 for i in pmd if not seen.get(i)),
            sum(1 for i in pmd if seen.get(i, 0) > 1)))

    # One-sided on purpose. Every decoded vertex must sit INSIDE the stored
    # sphere; the sphere being looser than the geometry is not an error, and
    # 162 of the 723 Los Angeles models are loose by up to 53 units.
    slack = []
    for m in read_models(rsc):
        pts = []
        for h in model_blocks(m, -1):
            got = rsc.resolve(h)
            if got is not None:
                for b in read_pmd(rsc, got[1])[0]:
                    pts.extend(b.pos)
        if pts:
            slack.append(max(math.dist(m.centre, p) for p in pts) - m.radius)
    slack.sort()
    tight = sum(1 for s in slack if s > -0.5)
    test('decoded geometry fits in the CMI0 sphere',
         slack and slack[-1] < 0.5,
         'max overflow %+.4f; %d of %d tight spheres'
         % (slack[-1] if slack else 0.0, tight, len(slack)))

    # The plain-text layout under assets/city/<name>/ names the same models the
    # container does, so the two halves can check each other.
    comps, insts = read_hoods(city_folder(rsc.path))
    if not comps and not insts:
        print('  (no assets/city/<name>/ alongside: 4 .hood tests skipped)')
    else:
        by = models_by_name(rsc)
        cnames = {c.name.lower() for c in comps}
        tnames = {i.type.lower() for i in insts}
        missing = (cnames | tnames) - set(by)
        test('every .hood name exists as a CMI0', not missing,
             '%d components + %d instances, %d without CMI0'
             % (len(comps), len(insts), len(missing)))
        # The two roles partition the CMI0 set exactly: a model is either baked
        # in world coordinates or it is a template, never both and never
        # neither. Do NOT test this by the `#geom` suffix - that only holds in
        # Los Angeles; Paris and Tokyo split big blocks into `#geom1`, `#geom2`.
        test('CMI0 = components + kinds, no leftover or overlap',
             not (cnames & tnames) and not (set(by) - cnames - tnames),
             '%d + %d = %d of %d CMI0, %d in common'
             % (len(cnames), len(tnames), len(cnames) + len(tnames), len(by),
                len(cnames & tnames)))

        spill = []
        for c in comps:
            m = by.get(c.name.lower())
            pts = model_points(rsc, m) if m else []
            if pts:
                spill.append(outside(pts, c.emin, c.emax))
        test('baked geometry fits in the .hood emin/emax',
             spill and max(spill) < 0.5,
             'max overflow %.4f in %d components'
             % (max(spill) if spill else 0.0, len(spill)))

        spill = []
        for i in insts:
            m = by.get(i.type.lower())
            pts = model_points(rsc, m) if m else []
            if pts:
                spill.append(outside([i.apply(p) for p in pts], i.emin, i.emax))
        test('instance times the matrix falls in its emin/emax',
             spill and max(spill) < 0.5,
             'max overflow %.4f in %d instances'
             % (max(spill) if spill else 0.0, len(spill)))

    # The colour layer, when a `<time>_cpvs.rsc` is beside the container.
    cpvs = open_cpvs(rsc)
    if cpvs is None:
        print('  (no midnight_clear_cpvs.rsc alongside: 3 colour tests skipped)')
    else:
        pmap = payload_map(rsc)
        okref = badref = 0
        tail = collections.Counter()
        for i in cpvs.indices('PCP0'):
            tags, used = pcp_chain(cpvs, i)
            groups = collections.OrderedDict()
            for (p, did, qwc, addr, s) in tags:
                if did == 'ref':
                    groups.setdefault(((addr >> 20) & 0xFFF) - 1, []).append(
                        (addr & 0xFFFF0, qwc))
            for src, lst in groups.items():
                for cand in (src, src + 4096, src + 8192):
                    want = pmap.get(cand)
                    if want and all(r in want for r in lst):
                        okref += 1
                        tail[len(want) - len(lst)] += 1
                        break
                else:
                    badref += 1
        test('every PCP0 ref finds its tags in the source PMD0', badref == 0,
             '%d groups ok, %d without a source, tags left at the end %s'
             % (okref, badref, dict(tail)))

        nb = shortcol = nocol = 0
        idx0 = 0
        for i in cpvs.indices('PCP0'):
            for b in read_pcp(rsc, cpvs, i):
                nb += 1
                if not b.cpv:
                    nocol += 1
                elif len(b.cpv) != len(b.pos):
                    shortcol += 1
                elif b.cpv[0] == len(b.pos):
                    idx0 += 1
        test('one colour index per vertex, in every batch',
             shortcol == 0 and nocol == 0,
             '%d batches, %d without colour, %d with a wrong count' % (nb, nocol, shortcol))
        # Element 0 of the ADC stream is a count; element 0 of the colour
        # stream is NOT - it is a colour like any other.
        test('colour index 0 is NOT a count (unlike the ADC)',
             nb and idx0 < nb * 0.05,
             '%d of %d batches coincide with the count' % (idx0, nb))

    print('\n%d tests failed' % len(fails))
    return 1 if fails else 0


def _gather(rsc, filter_=None, lod=None, instances=True):
    """[(label, [(Batch, Instance or None)])] ready to draw.

    A baked `*#geom` model comes out untransformed. A template comes out once
    per `.hood` instance, carrying the matrix that places it.
    """
    def batches_of(model):
        got = []
        for h in model_blocks(model, lod):
            r = rsc.resolve(h)
            if r is not None:
                got.extend(read_pmd(rsc, r[1])[0])
        return got

    out = []
    comps, insts = read_hoods(city_folder(rsc.path)) if instances else ([], [])
    placed = {i.type.lower() for i in insts}

    for m in read_models(rsc):
        if m.name.lower() in placed:
            continue                    # drawn below, once per instance
        if filter_ and filter_ not in m.name:
            continue
        got = batches_of(m)
        if got:
            out.append((m.name, [(b, None) for b in got]))

    by = models_by_name(rsc)
    cache = {}
    for i in insts:
        if filter_ and filter_ not in i.type:
            continue
        m = by.get(i.type.lower())
        if m is None:
            continue
        if m.name not in cache:
            cache[m.name] = batches_of(m)
        if cache[m.name]:
            out.append((i.type, [(b, i) for b in cache[m.name]]))
    return out


def cmd_hood(args):
    rsc = Rsc(args.file_)
    folder = city_folder(rsc.path)
    if not folder:
        sys.exit('no assets/city/<name>/ next to %s' % rsc.path)
    comps, insts = read_hoods(folder)
    print('%s: %d unique_component, %d instance_component'
          % (folder, len(comps), len(insts)))
    per = collections.Counter(i.type for i in insts)
    for c in comps:
        if args.filter_ and args.filter_ not in c.name:
            continue
        print('  baked    %-46s %s -> %s' % (
            c.name[:46], ' '.join('%.1f' % v for v in c.emin),
            ' '.join('%.1f' % v for v in c.emax)))
    for t, n in per.most_common():
        if args.filter_ and args.filter_ not in t:
            continue
        print('  template %-46s %d instances' % (t[:46], n))
    return 0


def _gather_cpv(rsc, cpvs, filter_=None, lod=0):
    """[(label, [(Batch, Instance or None)])] taken from the colour layer.

    Every batch here carries `cpv`, because the PCP0 chain is the one that
    actually draws: it refs the geometry out of the PMD0 and adds the colour.
    """
    comps, insts = read_hoods(city_folder(rsc.path))
    place = {(i.type.lower(), i.extension): i for i in insts}
    out = []
    for index, kind, key, level, which in cpv_entries(cpvs):
        if level != lod:
            continue
        label = key[0] if kind == 'inst' else key
        if filter_ and filter_ not in label:
            continue
        batches = read_pcp(rsc, cpvs, index)
        if not batches:
            continue
        inst = place.get(key) if kind == 'inst' else None
        out.append((label, [(b, inst) for b in batches]))
    return out


def _place(batch, inst, simulate=None):
    """World points of one batch, optionally placed the wrong way on purpose."""
    if inst is None:
        return list(batch.pos)
    if not simulate:
        return [inst.apply(p) for p in batch.pos]
    lo = [min(p[k] for p in batch.pos) for k in range(3)]
    hi = [max(p[k] for p in batch.pos) for k in range(3)]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    return [inst.apply_wrong(p, simulate, c) for p in batch.pos]


def cmd_errors(args):
    """Score each way a port can get placement wrong, against the .hood."""
    rsc = Rsc(args.file_)
    comps, insts = read_hoods(city_folder(rsc.path))
    if not insts:
        sys.exit('no assets/city/<name>/ next to %s' % rsc.path)
    by = models_by_name(rsc)
    cache = {}
    rows = collections.defaultdict(list)
    ident = 0
    for i in insts:
        key = i.type.lower()
        if key not in cache:
            m = by.get(key)
            pts = model_points(rsc, m) if m else []
            if pts:
                lo = [min(p[k] for p in pts) for k in range(3)]
                hi = [max(p[k] for p in pts) for k in range(3)]
                cache[key] = (pts, [(lo[k] + hi[k]) / 2 for k in range(3)])
            else:
                cache[key] = ([], (0.0, 0.0, 0.0))
        pts, centre = cache[key]
        if not pts:
            continue
        r = i.rot
        if all(abs(r[a][b] - (1.0 if a == b else 0.0)) < 1e-6
               for a in range(3) for b in range(3)):
            ident += 1
        rows['correct'].append(outside([i.apply(p) for p in pts], i.emin, i.emax))
        for how in ERRORS:
            rows[how].append(outside([i.apply_wrong(p, how, centre) for p in pts],
                                     i.emin, i.emax))
    n = len(rows['correct'])
    print('%s: %d instances with geometry, %d of them with an IDENTITY rotation'
          % (os.path.basename(rsc.path), n, ident))
    print('%-16s %12s %9s %9s %9s' % ('hypothesis', 'break', 'median', 'p90', 'max'))
    for how in ('correct',) + ERRORS:
        v = sorted(rows[how])
        bad = sum(1 for x in v if x > 0.5)
        print('%-16s %6d/%-5d %9.2f %9.2f %9.2f'
              % (how, bad, n, v[n // 2], v[int(0.9 * n)], v[-1]))
    print('\nThe cheap discriminator: an instance with an IDENTITY rotation does not')
    print('move under "transposed" or "no-scale". If the identity ones are also out')
    print('of place in your build, the bug is NOT the matrix convention.')
    return 0


def cmd_cpv(args):
    rsc = Rsc(args.file_)
    tod = args.hour or 'midnight_clear'
    cpvs = open_cpvs(rsc, tod)
    if cpvs is None:
        sys.exit('no %s_cpvs.rsc next to %s' % (tod, rsc.path))
    pal = palette(cpvs)
    ents = cpv_entries(cpvs)
    print('%s: %d PCP0, palette of %d colours (alpha %s)'
          % (tod, len(cpvs.indices('PCP0')), len(pal),
             ', '.join('%#x' % a for a in sorted({c[3] for c in pal}))))
    per = collections.Counter((k, l, p) for _, k, _, l, p in ents)
    for key in sorted(per):
        print('   %-5s lod %d %-5s %6d' % (key[0], key[1], key[2], per[key]))
    if args.filter_:
        for handle, (fcc, name) in sorted(cpvs.names.items(), key=lambda t: t[1][1]):
            if fcc == 'PCP0' and args.filter_ in name:
                bs = read_pcp(rsc, cpvs, (handle & 0x3FFF) - 1)
                print('   %-64s %2d batches, %5d vertices'
                      % (name[:64], len(bs), sum(len(b.pos) for b in bs)))
    return 0


def cmd_obj(args):
    rsc = Rsc(args.file_)
    lod = None if args.lod is None else int(args.lod)
    got = _gather(rsc, args.filter_, lod, not args.without_instances)
    out = _out_path(args.out or
                 os.path.splitext(os.path.basename(rsc.path))[0] + '.obj')
    nv = nt = 0
    with open(out, 'w') as fh:
        fh.write('# %s - exported by mc2_rsc.py\n' % os.path.basename(rsc.path))
        for label, batches in got:
            fh.write('o %s\n' % label)
            for b, place in batches:
                base = nv + 1
                for p in b.pos:
                    fh.write('v %.4f %.4f %.4f\n' % (place.apply(p) if place else p))
                for uv in b.uv:
                    fh.write('vt %.6f %.6f\n' % uv)
                nv += len(b.pos)
                tex = rsc.texture_name(b.texture)
                if tex:
                    fh.write('usemtl %s\n' % tex.replace('\\', '/'))
                paired = len(b.uv) == len(b.pos)
                for tri in b.triangles():
                    if paired:
                        fh.write('f %d/%d %d/%d %d/%d\n'
                                 % (base + tri[0], base + tri[0],
                                    base + tri[1], base + tri[1],
                                    base + tri[2], base + tri[2]))
                    else:
                        fh.write('f %d %d %d\n'
                                 % (base + tri[0], base + tri[1], base + tri[2]))
                    nt += 1
    print('%s\n  %d models, %d vertices, %d triangles' % (out, len(got), nv, nt))
    return 0


def cmd_map(args):
    from PIL import Image, ImageDraw
    rsc = Rsc(args.file_)
    lod = None if args.lod is None else int(args.lod)
    pal = None
    if args.colour:
        cpvs = open_cpvs(rsc, args.colour)
        if cpvs is None:
            sys.exit('no %s_cpvs.rsc next to %s' % (args.colour, rsc.path))
        pal = palette(cpvs)
        got = _gather_cpv(rsc, cpvs, args.filter_, 0 if lod is None else lod)
    else:
        got = _gather(rsc, args.filter_, lod, not args.without_instances)
    world = [_place(b, place, args.simulate) for label, bs in got for b, place in bs]
    xs = [p[0] for chunk in world for p in chunk]
    zs = [p[2] for chunk in world for p in chunk]
    if not xs:
        sys.exit('nothing to draw')
    size = int(args.size)
    mnx, mxx, mnz, mxz = min(xs), max(xs), min(zs), max(zs)
    span = max(mxx - mnx, mxz - mnz)
    img = Image.new('RGB', (size, size), (10, 10, 14))
    dr = ImageDraw.Draw(img)

    def to_px(p):
        return ((p[0] - mnx) / span * (size - 1),
                (size - 1) - (p[2] - mnz) / span * (size - 1))

    if pal:
        # Real colours: paint low ground first so a road does not cover the
        # kerb that sits on it. One triangle at a time, averaged over its
        # three vertex colours.
        faces = []
        k = 0
        for label, batches in got:
            for b, place in batches:
                pts = world[k]
                k += 1
                for tri in b.triangles():
                    c = [pal[b.cpv[j]] for j in tri] if b.cpv else [(128,) * 4] * 3
                    faces.append((sum(pts[j][1] for j in tri) / 3.0,
                                  [pts[j] for j in tri],
                                  tuple(min(255, sum(x[ch] for x in c) * 2 // 3)
                                        for ch in range(3))))
        faces.sort(key=lambda f: f[0])
        for _, pts, col in faces:
            dr.polygon([to_px(p) for p in pts], fill=col)
    else:
        # Draw the baked city first, then the instances on top of it, so a tree
        # does not disappear under the block it stands on.
        for order in (0, 1):
            k = 0
            for label, batches in got:
                for b, place in batches:
                    pts = world[k]
                    k += 1
                    if (place is not None) != bool(order):
                        continue
                    h = (hash(rsc.texture_name(b.texture) or '') & 0x7FFFFFFF)
                    col = (70 + h % 150, 70 + (h >> 9) % 150, 90 + (h >> 18) % 140)
                    px = [to_px(p) for p in pts]
                    for tri in b.triangles():
                        dr.polygon([px[tri[0]], px[tri[1]], px[tri[2]]], fill=col)
    out = _out_path(args.out or
                 os.path.splitext(os.path.basename(rsc.path))[0] + '_map.png')
    img.save(out)
    print('%s\n  x %.1f..%.1f  z %.1f..%.1f  %d draws'
          % (out, mnx, mxx, mnz, mxz, len(got)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest='cmd')
    for name in ('list', 'names', 'models', 'check', 'hood', 'errors'):
        p = sub.add_parser(name)
        p.add_argument('file_')
        p.add_argument('--filter', dest='filter_')
    p = sub.add_parser('cpv')
    p.add_argument('file_')
    p.add_argument('--filter', dest='filter_')
    p.add_argument('--hour', help='dawn_clear, midnight_rainy, ... (see TIMES)')
    p = sub.add_parser('dump')
    p.add_argument('file_')
    p.add_argument('index')
    for name in ('obj', 'map'):
        p = sub.add_parser(name)
        p.add_argument('file_')
        p.add_argument('--out')
        p.add_argument('--filter', dest='filter_')
        p.add_argument('--lod')
        p.add_argument('--size', default=2048)
        p.add_argument('--without-instances', action='store_true',
                       dest='without_instances',
                       help='only the baked geometry, without the .hood')
        p.add_argument('--colour', metavar='HOUR',
                       help='paint with the per-vertex colour of <hour>_cpvs.rsc')
        p.add_argument('--simulate', choices=ERRORS, metavar='ERROR',
                       help='place wrongly on purpose: ' + ', '.join(ERRORS))
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    return {'list': cmd_list, 'names': cmd_names, 'models': cmd_models,
            'dump': cmd_dump, 'check': cmd_check, 'hood': cmd_hood,
            'cpv': cmd_cpv, 'errors': cmd_errors,
            'obj': cmd_obj, 'map': cmd_map}[args.cmd](args) or 0


if __name__ == '__main__':
    sys.exit(main())
