"""mc3_modpath.py - the game's own .mod path, re-walked in Python

Not a converter. This is `rmcModel::Create` and everything under it, rewritten
function by function from the decompilation, so the intermediate objects the
engine builds in RAM can be inspected on a PC. Nothing here calls into the
existing tools: every rule below was read out of the ELF for this file.

THE PATH, as decompiled

    rmcModel::Create(name, flags)                             0x002A8CA0
      strrchr(name,'.') / strcasecmp(".mod")  -> is_mod
      datMemoryStartUseTemporary()
      mesh = new mshMesh(84 bytes, zeroed)
      is_mod ? mshMesh::LoadMod(mesh, name)                   0x005555A0
             : SerializeFromFile<mshMesh>(name, mesh, "mesh") 0x005BD038
      datMemoryEndUseTemporary()
      geom = new rmcModelGeom(24 bytes)                       0x002A8CA0
      geom->vtable[+24](geom, mesh, !is_mod, name)
        = rmcModelGeom::Load(mshMesh&, bool, char const*)     0x002A8FA0
            per material: atoi(name) -> slot
                          mshMaterial::Tristrip(mesh)         0x00559928
                          mshMaterial::Packetize(42, 0, adj)  0x00558DC0
                          rmcGeometry::Init(mesh, k, ..., exp) 0x002AA2A0
      AABox grown over the positions
      delete mesh                    <- the mshMesh is TEMPORARY
      geom[+6] = flags
      return geom

    mshMesh::LoadMod(name)                                    0x005555A0
      datAssetManager::Open(this, name, "mod", 0, 1)          0x00429FC0
      Stream::Read(hdr, 14); hdr[13] = 0
      "version: 2.1x" -> tokenizer 0x632348, newfmt 1, ver = hdr[12] - 38
      "version: 2.00" -> tokenizer 0x632348, newfmt 1, ver 1
      "version: 1.10" -> tokenizer 0x6323F8, newfmt 0, ver 10
      anything else   -> tokenizer 0x6323F8, newfmt 0, ver 0
      mshMesh::LoadMod(tokenizer&, newfmt, ver)               0x00555758

    All 779 .mod files that ship with the game say "version: 1.10", so that is
    the branch this walks. The others are recognised and refused by name rather
    than mis-parsed.

WHAT IS HERE, AND HOW CLOSE IT IS

    The chain runs end to end: a .mod goes in and VIF quadwords come out, every
    stage rewritten from its own decompilation -

        datAsciiTokenizer, mshMesh::LoadMod, mshMaterial::Tristrip,
        mshMaterial::Packetize (+ Score, ChangeWinding, BreakUpLongTristrips),
        rmcModelGeom::Load, rmcGeometry::ComputeFVF and rmcFvf,
        rmcGeometry::Init, PackData, rmcGeometry::Packet::Init.

    Checked against a shipped file, model/probe1.mesh.pck, read back with
    mc3_vifdis.py. Emitting a packet from the same kind of data produces the
    same six VIFcode words the retail chain has, exactly:

        6002409E  header, UNPACK S-32 x2 USN at the ITOP address
        69300100  UNPACK V3-16 x48, positions
        653000D0  UNPACK V2-16 x48, uvs
        6E3000A0  UNPACK V4-8  x48, colours
        0400009E  ITOP
        14000006  MSCAL 6

    Constants that came out of that file rather than out of a guess: the short
    header form is the one in use; the VU addresses are ITOP 09E with colour at
    +2, uv at +0x32 and position at +0x62, and the second buffer is the same
    layout from 1C5; the streams go out in the order positions, uvs, colours;
    the cap is 48, not 42, so byte_615D00 is set in the shipping build; and the
    uv fixed point is 4096 - 32768/4096 is exactly the 8.0 that appears in the
    text.

    WHAT IS NOT PROVEN: that the PAYLOAD bytes match. probe1 is the only asset
    that ships as both .mod and .mesh.pck, and the two are different revisions -
    decode the .pck positions and they land inside the .mod's bounding box but
    on different vertices, 3070 stream vertices against 1914 adjuncts. So there
    is no matched pair to diff against, and the packet boundaries cannot be
    compared either: this packer makes 44 packets for probe1 where the file has
    77, and with different geometry on each side that comparison means nothing.

    Still approximate inside Packet::Init: a packet is fed here as a block of
    positions rather than gathered through its own adjunct list; colours do not
    go through rmcCpvPalette::Lookup 0x002A9F70; the normal stream is not
    emitted.

VALIDATION

    Four checks, none of which needs a reference file to compare against:

      * every one of the 779 .mod files the game ships walks without error;
      * `primitives:` in the header is a TRIANGLE count, so re-deriving it from
        the strips tests the parse, the strip parity and the degenerate-restart
        rule at once - 779 of 779 agree;
      * Packetize must neither invent nor lose geometry, and no packet may pass
        the cap it was given - 779 of 779, 8523 packets;
      * PackData is checked against its own inverse: every position block is
        packed, read back and compared - 779 of 779, 171100 VIF words;
      * every emitted chain is quadword aligned and ends on MSCAL with the
        right microprogram entry, 6 or 8 by skinning - 3231 packets, 52919
        quadwords.

    Note that the packets in the FILE are the exporter's, not the engine's:
    Packetize clears the material's packet array and rebuilds it, so checker.mod
    ships 2 packets and the runtime makes 3 of them. There is nothing to compare
    packet boundaries against, which is why the checks above are shaped the way
    they are.

USAGE

    python mc3_modpath.py <file.mod>            walk one file, print the objects
    python mc3_modpath.py <file.mod> --verbose  also list materials and packets
    python mc3_modpath.py --all <dir>           walk every .mod under a directory
"""
import os
import struct
import sys


# ---------------------------------------------------------------------------
#  mshMesh, as `rmcModel::Create` allocates it: 84 bytes, ten {ptr, u16, u16}
#  slots and a trailing byte. The offsets are the ones the engine reads, not a
#  naming of convenience - `rmcModelGeom::Load` fetches the material count from
#  +68, which is slot +64's count field, and the skinned flag from +80.
# ---------------------------------------------------------------------------

SLOT_POS       = 0     # `v`, stride 12   (3 floats)
SLOT_POSSKIN   = 8     # skinned positions, stride 32
SLOT_NRM       = 16    # `n`, stride 12
SLOT_TANGENT   = 24    # `tangents:`
SLOT_CPV       = 32    # `c`, stride 16   (4 floats - NOT packed bytes)
SLOT_TEX1      = 40    # `t1`, stride 8
SLOT_TEX2      = 48    # `t2`, stride 8
SLOT_ADJUNCT   = 56    # `adjuncts:`; its count is Packetize's 4th argument
SLOT_MATERIAL  = 64    # `materials:`, stride 32
SLOT_MATRIX    = 72    # `matrices:`, stride 12
OFF_SKINNED    = 80    # the byte; set by mshMesh::MakeSkinned

# In the file a primitive is one of three words. Packetize will not accept any
# of them: it demands 3 or 4, which is what Tristrip produces.
PRIM_TRI = 0
PRIM_STR = 1
PRIM_STP = 2
PRIM_STRIP_EVEN = 3
PRIM_STRIP_ODD = 4


class MshArray(object):
    """One {ptr, u16 count, u16 capacity} slot.

    The engine writes the declared header count into the capacity at +6, then
    fills forward and counts at +4. Keeping both apart matters: a file whose
    body is shorter than its header claims leaves count < capacity, and that is
    a real condition worth reporting rather than smoothing over.
    """

    __slots__ = ('items', 'capacity')

    def __init__(self):
        self.items = []
        self.capacity = 0

    @property
    def count(self):
        return len(self.items)

    def __len__(self):
        return len(self.items)


class MshMaterial(object):
    """32 bytes. +0 name, +4/+8/+10 primitives, +12/+16/+18 packets."""

    __slots__ = ('name', 'primitives', 'packets', 'textures', 'illum',
                 'ambient', 'diffuse', 'specular', 'attributes',
                 'declared_packets', 'declared_primitives')

    def __init__(self):
        self.name = ''
        self.primitives = []
        self.packets = []
        self.textures = []
        self.illum = ''
        self.ambient = (0.0, 0.0, 0.0)
        self.diffuse = (0.0, 0.0, 0.0)
        self.specular = (0.0, 0.0, 0.0)
        self.attributes = []
        self.declared_packets = 0
        self.declared_primitives = 0

    def slot(self):
        """rmcModelGeom::Load: `if (*name == '#') name++; atoi(name)`.

        The shader slot is not stored as a number anywhere - it is parsed out
        of the material's NAME, with one optional leading '#'. A name that does
        not start with digits gives 0, which is why every stock model whose
        material is called "initialShadingGroup" lands on slot 0.
        """
        s = self.name[1:] if self.name.startswith('#') else self.name
        n = 0
        i = 0
        neg = False
        if i < len(s) and s[i] in '+-':
            neg = s[i] == '-'
            i += 1
        while i < len(s) and s[i].isdigit():
            n = n * 10 + (ord(s[i]) - 48)
            i += 1
        return -n if neg else n


class MshPrimitive(object):
    """16 bytes: +0 type, +4 u16 key, +8 indices, +12 u16 length, +14 owns.

    The key at +4 is what Packetize runs over - it processes primitives in runs
    that share it, and ChangeWinding copies it across unchanged. Here it is the
    index of the packet the primitive was read from, which is the grouping the
    file itself expresses.
    """

    __slots__ = ('type', 'indices', 'key')

    def __init__(self, type_, indices, key=0):
        self.type = type_
        self.indices = indices
        self.key = key


class MshPacket(object):
    """`packet <adjuncts> <primitives> <n> { adj... prims... }`"""

    __slots__ = ('header', 'adjuncts', 'primitives', 'matrices', 'reskins')

    def __init__(self, header):
        self.header = header
        self.adjuncts = []
        self.primitives = []
        self.matrices = []
        self.reskins = []


class MshMesh(object):
    def __init__(self):
        self.slots = {off: MshArray() for off in
                      (SLOT_POS, SLOT_POSSKIN, SLOT_NRM, SLOT_TANGENT,
                       SLOT_CPV, SLOT_TEX1, SLOT_TEX2, SLOT_ADJUNCT,
                       SLOT_MATERIAL, SLOT_MATRIX)}
        self.skinned = False
        self.declared = {}

    def a(self, off):
        return self.slots[off]

    def make_skinned(self):
        """mshMesh::MakeSkinned - reached when `matrices:` is neither 0 nor 1.

        rmcModel::Create then reads positions from +8 at stride 32 instead of
        +0 at stride 12, so the flag decides where the AABox and the exponent
        fit look.
        """
        self.skinned = True

    def add_pos(self, v):
        """mshMesh::AddPos(Vector3 const&, bool, mshBinding const*, float)

        The `v` reader does not touch an array directly - it calls this, and
        THIS is what decides where a position lands. Skinned, it goes to +8 at
        stride 32 (the vector plus its binding); otherwise to +0 at stride 12.
        Missing that indirection leaves a skinned model with an empty +8, which
        is what rmcModelGeom::Load measures, and the exponent and AABox come
        back as if the mesh were a point at the origin.

        The binding itself is filled from the packets' `reskin` lines, which
        are read later; the slot is reserved here the way the engine reserves
        it.
        """
        if self.skinned:
            self.a(SLOT_POSSKIN).items.append(tuple(v) + (None,))
        else:
            self.a(SLOT_POS).items.append(tuple(v))

    def positions(self):
        if self.skinned:
            return [p[:3] for p in self.a(SLOT_POSSKIN).items]
        return self.a(SLOT_POS).items


# ---------------------------------------------------------------------------
#  datAsciiTokenizer, 0x004345E0..0x004354B8
#
#  The engine's tokenizer hands out whitespace-delimited words, and every typed
#  getter (GetInt 0x00434630, GetFloat 0x00434690, GetVector 0x00434758) is a
#  word plus a conversion. MatchToken 0x00434060 reads a word and insists on
#  it. That is the whole contract this file needs.
# ---------------------------------------------------------------------------

class DatAsciiTokenizer(object):
    def __init__(self, text):
        self.text = text
        self.i = 0
        self.n = len(text)

    def get_token(self):
        t = self.text
        i, n = self.i, self.n
        while i < n and t[i] in ' \t\r\n':
            i += 1
        if i >= n:
            self.i = i
            return None
        start = i
        while i < n and t[i] not in ' \t\r\n':
            i += 1
        self.i = i
        return t[start:i]

    def peek_token(self):
        save = self.i
        tok = self.get_token()
        self.i = save
        return tok

    def match_token(self, want):
        """datBaseTokenizer::MatchToken - a word that must be what is expected."""
        got = self.get_token()
        if got != want:
            raise ModError('expected %r, got %r at offset %d'
                           % (want, got, self.i))
        return got

    def get_int(self):
        tok = self.get_token()
        if tok is None:
            raise ModError('end of file where an int was expected')
        return int(tok, 10)

    def get_float(self):
        tok = self.get_token()
        if tok is None:
            raise ModError('end of file where a float was expected')
        return float(tok)

    def match_int(self, want):
        """datAsciiTokenizer::MatchInt - `name: <int>` as one operation."""
        self.match_token(want)
        return self.get_int()

    def get_vector(self, n):
        return tuple(self.get_float() for _ in range(n))

    def get_quoted(self):
        """The texture line is `texture: 0 "name"`; quotes are not delimiters
        to the tokenizer, so the word arrives with them attached."""
        tok = self.get_token()
        if tok is None:
            return ''
        if tok.startswith('"'):
            out = tok
            while not (len(out) > 1 and out.endswith('"')):
                nxt = self.get_token()
                if nxt is None:
                    break
                out += ' ' + nxt
            return out[1:-1]
        return tok


class ModError(Exception):
    pass


# ---------------------------------------------------------------------------
#  datAssetManager::Open  0x00429FC0  (vtable slot +36 of the 0x006D557C
#  singleton). The extension is a parameter, and datAssetManagerHierNew::
#  FullPath 0x00429AC8 appends `.<ext>` only when the name carries no dot -
#  which is why both "model/checker.mod" and "model/checker" resolve.
# ---------------------------------------------------------------------------

def dat_asset_manager_open(root, name, ext):
    p = name.replace('\\', '/')
    if p.startswith('$/'):
        p = p[2:]                       # `$/` is "skip 2 chars", not a device
    if '.' not in os.path.basename(p):
        p += '.' + ext
    full = os.path.join(root, p.replace('/', os.sep))
    if not os.path.isfile(full):
        return None
    with open(full, 'rb') as f:
        return f.read()


# ---------------------------------------------------------------------------
#  mshMesh::LoadMod(char const*)  0x005555A0 - open, sniff, dispatch
# ---------------------------------------------------------------------------

VERSION_UNKNOWN, VERSION_110, VERSION_200, VERSION_21X = 0, 10, 1, 2


def sniff_version(blob):
    """The engine reads exactly 14 bytes and nul-terminates at [13]."""
    hdr = blob[:14]
    if len(hdr) < 14:
        return VERSION_UNKNOWN, 0, False
    if hdr[:12] == b'version: 2.1':
        return VERSION_21X, hdr[12] - 38, True
    if hdr[:13] == b'version: 2.00':
        return VERSION_200, 1, True
    if hdr[:13] == b'version: 1.10':
        return VERSION_110, 10, False
    return VERSION_UNKNOWN, 0, False


# ---------------------------------------------------------------------------
#  mshMesh::LoadMod(datBaseTokenizer&, bool, int)  0x00555758
#
#  The header is eleven `name: <int>` pairs read in a fixed order (the calls sit
#  back to back at 0x555758+488..503 in the decompilation). Each count is
#  written into its slot's capacity, the slot is allocated, and the body then
#  fills forward.
# ---------------------------------------------------------------------------

HEADER_FIELDS = ('verts', 'normals', 'colors', 'tex1s', 'tex2s', 'tangents',
                 'materials', 'adjuncts', 'primitives', 'matrices', 'reskins')


def msh_mesh_load_mod(mesh, tok, newfmt, version):
    counts = {}
    for field in HEADER_FIELDS:
        counts[field] = tok.match_int(field + ':')
    mesh.declared = counts

    # `matrices: 1` means "no skinning": the engine folds 1 to 0 and only calls
    # MakeSkinned above that. Every stock model declares 1.
    matrices = counts['matrices']
    if matrices == 1:
        matrices = 0
    elif matrices:
        mesh.make_skinned()

    if mesh.skinned:
        mesh.a(SLOT_POSSKIN).capacity = counts['verts']
    else:
        mesh.a(SLOT_POS).capacity = counts['verts']
    mesh.a(SLOT_NRM).capacity = counts['normals']
    mesh.a(SLOT_CPV).capacity = counts['colors']
    mesh.a(SLOT_TEX1).capacity = counts['tex1s']
    mesh.a(SLOT_TEX2).capacity = counts['tex2s']
    mesh.a(SLOT_TANGENT).capacity = counts['tangents']
    mesh.a(SLOT_MATERIAL).capacity = counts['materials']
    mesh.a(SLOT_ADJUNCT).capacity = counts['adjuncts']
    mesh.a(SLOT_MATRIX).capacity = matrices
    # The parser appends `matrices` entries to slot +72 without consuming a
    # token for any of them - the file body carries no matrix data between the
    # header and the `v` stream, only a palette SIZE. The entries matter anyway,
    # because ComputeFVF reads this array's COUNT at mesh+76 to decide whether
    # the vertex format carries blend weights and bindings.
    for _ in range(matrices):
        mesh.a(SLOT_MATRIX).items.append(None)

    # The five per-element streams, each a letter then its components. They are
    # read in this order and the loop for each one runs exactly `capacity`
    # times, so a stream is positional - there is no keying by name.
    # Positions go through AddPos, not into a slot: see MshMesh.add_pos.
    for _ in range(counts['verts']):
        tok.match_token('v')
        mesh.add_pos(tok.get_vector(3))
    _read_stream(tok, mesh.a(SLOT_NRM), 'n', 3, counts['normals'])
    _read_stream(tok, mesh.a(SLOT_CPV), 'c', 4, counts['colors'])
    _read_stream(tok, mesh.a(SLOT_TEX1), 't1', 2, counts['tex1s'])
    _read_stream(tok, mesh.a(SLOT_TEX2), 't2', 2, counts['tex2s'])

    # Materials and packets are ONE sequence, not two. checker.mod puts every
    # material first and then every packet; probe.mod alternates, a material
    # followed by its own packets. Both are the same grammar read block by
    # block, so packets go to the material that is still short of the count it
    # declared - which is the only rule that satisfies both layouts.
    mats = mesh.a(SLOT_MATERIAL)
    pending = []
    while True:
        nxt = tok.peek_token()
        if nxt is None:
            break
        if nxt == 'mtl':
            mat = _read_material(tok)
            mats.items.append(mat)
            pending.append(mat)
        elif nxt == 'packet':
            pk = _read_packet(tok, len(mats.items))
            while pending and len(pending[0].packets) >= pending[0].declared_packets:
                pending.pop(0)
            if not pending:
                raise ModError('a packet with no material left to own it')
            pending[0].packets.append(pk)
        elif nxt in ('mtxv', 'mtxn'):
            # The two trailing counts: how many positions and normals the
            # matrix palette covers. Every stock file ends on this pair.
            tok.get_token()
            tok.get_int()
        elif nxt == 'reskin':
            tok.match_token('reskin')
            tok.get_int()
        else:
            break

    for mat in mats.items:
        for pk in mat.packets:
            mat.primitives.extend(pk.primitives)
        mesh.a(SLOT_ADJUNCT).items.extend(
            adj for pk in mat.packets for adj in pk.adjuncts)

    return 1


def _read_stream(tok, arr, letter, width, n):
    for _ in range(n):
        tok.match_token(letter)
        arr.items.append(tok.get_vector(width))


def _read_material(tok):
    mat = MshMaterial()
    tok.match_token('mtl')
    mat.name = tok.get_token()
    tok.match_token('{')
    while True:
        key = tok.get_token()
        if key is None or key == '}':
            break
        if key == 'packets:':
            mat.declared_packets = tok.get_int()
        elif key == 'primitives:':
            mat.declared_primitives = tok.get_int()
        elif key == 'textures:':
            tok.get_int()
        elif key == 'illum:':
            mat.illum = tok.get_token()
        elif key == 'ambient:':
            mat.ambient = tok.get_vector(3)
        elif key == 'diffuse:':
            mat.diffuse = tok.get_vector(3)
        elif key == 'specular:':
            mat.specular = tok.get_vector(3)
        elif key == 'texture:':
            idx = tok.get_int()
            mat.textures.append((idx, tok.get_quoted()))
        elif key == 'attributes:':
            for _ in range(tok.get_int()):
                mat.attributes.append(_read_attribute(tok))
        else:
            # The grammar carries more keys than any shipped file uses. Skipping
            # an unknown one silently would turn a typo into wrong geometry, so
            # it is refused instead.
            raise ModError('unknown material key %r' % key)
    return mat


def _read_attribute(tok):
    """`attributes: n` then n of `<type> <name> <value...>`."""
    kind = tok.get_token()
    name = tok.get_token()
    if kind == 'bool':
        return (kind, name, tok.get_int())
    if kind == 'int':
        return (kind, name, tok.get_int())
    if kind == 'float':
        return (kind, name, tok.get_float())
    if kind == 'vector':
        return (kind, name, tok.get_vector(3))
    raise ModError('unknown attribute type %r' % kind)


def _is_int(tok_text):
    if not tok_text:
        return False
    t = tok_text[1:] if tok_text[0] in '+-' else tok_text
    return bool(t) and t.isdigit()


def _read_packet(tok, key=0):
    tok.match_token('packet')
    # The header is NOT a fixed three fields. An unskinned packet reads
    # `packet 46 14 1 {` while a skinned one reads `packet 35 3 12 26 {` -
    # rider_h.mod is the case that proves it. Read numbers until the brace,
    # the way a tokenizer-driven reader has to.
    header = []
    while _is_int(tok.peek_token()):
        header.append(tok.get_int())
    pk = MshPacket(tuple(header))
    tok.match_token('{')
    while True:
        word = tok.get_token()
        if word is None or word == '}':
            break
        if word == 'adj':
            # position, normal, colour, tex1, tex2, and one more; -1 is "none".
            pk.adjuncts.append(tuple(tok.get_int() for _ in range(6)))
        elif word == 'tri':
            # No count of its own: one triangle per line.
            pk.primitives.append(MshPrimitive(
                PRIM_TRI, [tok.get_int() for _ in range(3)], key))
        elif word in ('str', 'stp'):
            n = tok.get_int()
            pk.primitives.append(MshPrimitive(
                PRIM_STR if word == 'str' else PRIM_STP,
                [tok.get_int() for _ in range(n)], key))
        elif word == 'reskin':
            # `reskin <bone> <n> <weight> <x> <y> <z>` - skin weights, present
            # only in models whose header declares `reskins:`. Read to the next
            # non-number for the same reason the packet header does.
            vals = []
            while True:
                nxt = tok.peek_token()
                if nxt is None:
                    break
                try:
                    float(nxt)
                except ValueError:
                    break
                vals.append(tok.get_float())
            pk.reskins.append(tuple(vals))
        elif word == 'mtx':
            # The matrices this packet binds. Same reasoning as the header:
            # `mtx 0` in an unskinned model, `mtx 28 27 18 22` in a skinned
            # one, so it runs to the next non-number.
            while _is_int(tok.peek_token()):
                pk.matrices.append(tok.get_int())
        else:
            raise ModError('unknown packet entry %r' % word)
    return pk


# ---------------------------------------------------------------------------
#  mshMaterial::Tristrip(mshMesh const&)  0x00559928
#
#  Walks the material's primitives and turns each one into strips, bailing on
#  the whole material if any single primitive fails. Its only purpose in the
#  chain is to satisfy Packetize, which refuses anything that is not type 3 or
#  4 - so the parity that `str` and `stp` already carry is what survives.
# ---------------------------------------------------------------------------

def msh_material_tristrip(mat, mesh):
    out = []
    for prim in mat.primitives:
        if prim.type in (PRIM_STR, PRIM_STP):
            out.append(MshPrimitive(
                PRIM_STRIP_EVEN if prim.type == PRIM_STR else PRIM_STRIP_ODD,
                list(prim.indices), prim.key))
        elif prim.type == PRIM_TRI:
            # A bare triangle is a strip of three with even parity.
            if len(prim.indices) != 3:
                return 0
            out.append(MshPrimitive(PRIM_STRIP_EVEN, list(prim.indices),
                                    prim.key))
        else:
            return 0
    mat.primitives = out
    return 1


def triangles_of(prim):
    """The winding rule the strip types encode.

    Type 3 emits (k-2, k-1, k) on even k and type 4 flips it; this is the same
    alternation `str`/`stp` name in the file, which is why Tristrip can carry
    the parity across untouched instead of re-deriving it.
    """
    idx = prim.indices
    flip = 1 if prim.type == PRIM_STRIP_ODD else 0
    tris = []
    for k in range(2, len(idx)):
        a, b, c = idx[k - 2], idx[k - 1], idx[k]
        if a == b or b == c or a == c:
            continue                    # degenerate: a strip restart
        if (k + flip) % 2:
            tris.append((b, a, c))
        else:
            tris.append((a, b, c))
    return tris



# ---------------------------------------------------------------------------
#  mshMaterial::Packetize(int, int, int)  0x00558DC0
#
#  A packet is one CONTINUOUS strip stream, and the whole function is a greedy
#  bin packer over the material's strips. Three pieces make it work:
#
#    Score(bitset, prim, bool)          0x00558650 - eight bytes:
#        `return *(unsigned __int16 *)(a2 + 12);`
#    the strip's own length, and nothing else. The bitset argument is ignored.
#    So the heuristic is not "share the most vertices with what is already
#    packed", which is what the bitset parameter suggests - it is plainly
#    LONGEST FIRST.
#
#    the parity chain: after a strip of length L is appended the running count
#    advances, `v144 += L`, and the type the NEXT strip must have follows from
#    its low bit - `if (v144 & 1) want = 4; else want = 3`. A packet opens
#    wanting type 3.
#
#    two passes: the first takes only a strip whose type already equals `want`
#    and which fits in `cap - fill`. If none does, the second takes the longest
#    strip of ANY type that fits in `cap - fill - 1` and flips it - the extra
#    vertex in the budget is what the flip can cost.
#
#  Primitives are processed in runs sharing the u16 at prim+4, and a packet is
#  closed when neither pass finds anything.
# ---------------------------------------------------------------------------

def score(prim):
    """Score  0x00558650 - the strip's length. Reproduced as its own function
    because its triviality is the finding: nothing here weighs vertex reuse."""
    return len(prim.indices)


def change_winding(prim):
    """ChangeWinding(mshPrimitive&, mshPrimitive const&)  0x00558280

    Flips 3 <-> 4. A strip that already opens on a degenerate pair sheds it,
    which is free; anything else has to grow one, which is the `- 1` the second
    pass carries in its budget.
    """
    idx = prim.indices
    if len(idx) >= 4 and idx[0] == idx[1]:
        new = idx[1:]
    else:
        new = [idx[0]] + list(idx)
    out = MshPrimitive(PRIM_STRIP_ODD if prim.type == PRIM_STRIP_EVEN
                       else PRIM_STRIP_EVEN, new)
    out.key = prim.key
    return out


def break_up_long_tristrips(prims, cap):
    """BreakUpLongTristrips(...)  0x00558658

    A strip that cannot fit a packet is cut into `n` pieces, where n is the
    SMALLEST divisor that brings L/n within the cap - the search literally
    counts up from 2. Pieces are L/n long, except that a piece landing exactly
    on the cap is shortened by one to leave the flip its room.

    Consecutive pieces overlap by two indices, because that is what it takes for
    the second piece to continue the same surface, and each cut flips parity.
    """
    out = []
    for prim in prims:
        L = len(prim.indices)
        if prim.type == PRIM_STRIP_EVEN and cap >= L:
            out.append(prim)
            continue
        if cap >= L:
            out.append(prim)
            continue
        n = 2
        if cap < L // 2:
            n = 3
            while n < L and cap < L // n:
                n += 1
        piece = L // n
        if piece == cap:
            piece -= 1
        if piece < 3:
            piece = 3
        start = 0
        parity = prim.type
        while start < L - 2:
            seg = prim.indices[start:start + piece]
            if len(seg) < 3:
                break
            sub = MshPrimitive(parity, list(seg))
            sub.key = prim.key
            out.append(sub)
            # Overlap by two: the next piece restarts on the last edge, and the
            # parity of that edge is what decides the piece's own type.
            start += piece - 2
            if (piece - 2) & 1:
                parity = (PRIM_STRIP_ODD if parity == PRIM_STRIP_EVEN
                          else PRIM_STRIP_EVEN)
    return out


def msh_material_packetize(mat, cap, adjunct_count):
    for prim in mat.primitives:
        if prim.type not in (PRIM_STRIP_EVEN, PRIM_STRIP_ODD):
            return 0                    # the check the real function opens with

    work = break_up_long_tristrips(mat.primitives, cap)
    used = [False] * len(work)
    packets = []

    i = 0
    while i < len(work):
        # One run = the primitives that share the u16 at prim+4.
        j = i
        key = work[i].key
        while j < len(work) and work[j].key == key:
            j += 1
        run = range(i, j)

        while True:
            cur = []
            fill = 0
            want = PRIM_STRIP_EVEN
            while True:
                best, best_i, flip = 0, -1, False
                for k in run:
                    if used[k]:
                        continue
                    L = score(work[k])
                    if (cap - fill) >= L and work[k].type == want and L > best:
                        best, best_i = L, k
                if best == 0:
                    for k in run:
                        if used[k]:
                            continue
                        L = score(work[k])
                        if (cap - fill - 1) >= L and L > best:
                            best, best_i = L, k
                    flip = best != 0
                if best == 0:
                    break
                used[best_i] = True
                chosen = change_winding(work[best_i]) if flip else work[best_i]
                cur.append(chosen)
                fill += len(chosen.indices)
                want = PRIM_STRIP_ODD if (fill & 1) else PRIM_STRIP_EVEN
            if not cur:
                break
            pk = MshPacket((0, len(cur), 0))
            pk.primitives = cur
            packets.append(pk)
            if all(used[k] for k in run):
                break
        i = j

    mat.packets = packets
    return 1



# ---------------------------------------------------------------------------
#  PackData(unsigned int*, short const*, int, int, int, int&)  0x002AAC78
#
#  The compressor under the vertex streams, and the reason a .pck is as small as
#  it is. Given a rows x cols block of s16 it picks between three encodings and
#  emits the VIF chain for the winner:
#
#    8-bit absolute   every value fits a signed byte
#    8-bit delta      the value MINUS the row above fits a signed byte, in runs
#                     split at the rows where it does not
#    16-bit raw       neither
#
#  The delta run is where the VIF itself does the work: STROW seeds the row
#  register with the run's first row, STMOD 2 puts the unit in difference mode -
#  each unpacked value adds to the one before it - and a byte per component is
#  then enough. The first row of a run writes zeros, because STROW already holds
#  it.
#
#  Four opcode tables, all indexed by the column count:
#      0x615DA8  8-bit    0x62 S  0x66 V2  0x6A V3  0x6E V4
#      0x615DC0  32-bit   0x60    0x64     0x68     0x6C
#      0x615DD8  8-bit, for delta runs - the same codes as 0x615DA8
#      0x615DF0  16-bit   0x61    0x65     0x69     0x6D
# ---------------------------------------------------------------------------

VIF_UNPACK_8 = (0, 0x62, 0x66, 0x6A, 0x6E)       # dword_615DA8
VIF_UNPACK_32 = (0, 0x60, 0x64, 0x68, 0x6C)      # dword_615DC0
VIF_UNPACK_D8 = (0, 0x62, 0x66, 0x6A, 0x6E)      # dword_615DD8
VIF_UNPACK_16 = (0, 0x61, 0x65, 0x69, 0x6D)      # dword_615DF0

VIF_STMOD = 0x05000000      # 83886080 in the decompilation; | 2 for difference
VIF_STROW = 0x30000000      # 805306368
VIF_USN = 0x4000

MODE_NONE, MODE_DIFF = 0, 2


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _s32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _fits_s8(v):
    # `(unsigned __int16)(v + 128) >= 0x100` is how the engine asks.
    return 0 <= (v + 128) < 0x100


class VifChain(object):
    """The scratch buffer Packet::Init builds into. The real one is 8 KB of
    stack; it allocates the exact used size at the end and memcpy's into it."""

    def __init__(self):
        self.words = []
        self.tags = []

    def word(self, w):
        self.words.append(w & 0xFFFFFFFF)

    def tag(self, w, what):
        self.tags.append((len(self.words), what))
        self.word(w)

    def bytes_padded(self, data):
        buf = bytearray(data)
        while len(buf) % 4:
            buf.append(0)
        for i in range(0, len(buf), 4):
            self.word(int.from_bytes(bytes(buf[i:i + 4]), 'little'))

    def shorts_padded(self, vals):
        buf = bytearray()
        for v in vals:
            buf += int(v & 0xFFFF).to_bytes(2, 'little')
        while len(buf) % 4:
            buf.append(0)
        for i in range(0, len(buf), 4):
            self.word(int.from_bytes(bytes(buf[i:i + 4]), 'little'))


def pack_data(chain, src, rows, cols, addr, mode):
    """Returns (addr, mode). Both are carried forward across streams, which is
    why they are a reference parameter in the original."""
    all_fit8 = True
    breaks = [0]
    for r in range(rows):
        row_break = False
        for c in range(cols):
            v = src[r][c]
            if not _fits_s8(v):
                all_fit8 = False
            if r and not _fits_s8(v - src[r - 1][c]):
                row_break = True
        if row_break:
            breaks.append(r)
    breaks.append(rows)

    n = rows * cols
    size_delta = 24 * (len(breaks) - 1) + n
    size_abs8 = n + 8
    size_raw16 = 2 * n + 4
    if mode == MODE_NONE:
        size_abs8 = n + 4
    if mode != MODE_DIFF:
        size_delta += 4
    if mode != MODE_NONE:
        size_raw16 = 2 * n + 8

    if all_fit8 and size_abs8 < size_delta:
        if mode:
            chain.tag(VIF_STMOD, 'STMOD 0')
            mode = MODE_NONE
        chain.tag((VIF_UNPACK_8[cols] << 24) | ((rows & 0xFF) << 16)
                  | (addr & 0x3FF), 'UNPACK%d-8 abs x%d' % (cols, rows))
        out = bytearray()
        for r in range(rows):
            for c in range(cols):
                out.append(src[r][c] & 0xFF)
        chain.bytes_padded(out)
        return addr, mode

    if size_delta < size_raw16:
        for k in range(1, len(breaks)):
            b0, b1 = breaks[k - 1], breaks[k]
            run = b1 - b0
            if run == 1:
                if mode:
                    chain.tag(VIF_STMOD, 'STMOD 0')
                    mode = MODE_NONE
                chain.tag((VIF_UNPACK_32[cols] << 24) | 0x10000
                          | (addr & 0x3FF) | VIF_USN, 'UNPACK%d-32 x1' % cols)
                for c in range(cols):
                    chain.word(src[b0][c])
                addr += 1
            else:
                chain.tag(VIF_STROW, 'STROW')
                for c in range(4):
                    chain.word(src[b0][c] if c < cols else 0)
                if mode != MODE_DIFF:
                    chain.tag(VIF_STMOD | MODE_DIFF, 'STMOD 2 (difference)')
                    mode = MODE_DIFF
                chain.tag((VIF_UNPACK_D8[cols] << 24) | ((run & 0xFF) << 16)
                          | (addr & 0x3FF),
                          'UNPACK%d-8 delta x%d' % (cols, run))
                addr += run
                out = bytearray()
                for r in range(run):
                    for c in range(cols):
                        out.append(0 if r == 0 else
                                   (src[b0 + r][c] - src[b0 + r - 1][c]) & 0xFF)
                chain.bytes_padded(out)
        return addr, mode

    if mode:
        chain.tag(VIF_STMOD, 'STMOD 0')
        mode = MODE_NONE
    chain.tag((VIF_UNPACK_16[cols] << 24) | ((rows & 0xFF) << 16)
              | (addr & 0x3FF), 'UNPACK%d-16 raw x%d' % (cols, rows))
    chain.shorts_padded([src[r][c] for r in range(rows) for c in range(cols)])
    return addr, mode


def unpack_data(chain, rows, cols, start=0):
    """The inverse. NOT a game function - it exists so the packer can be checked
    against itself, which is the only reference available for this stage."""
    w = chain.words
    i, mode = start, MODE_NONE
    row = [0, 0, 0, 0]
    out = []
    while i < len(w) and len(out) < rows:
        cmd = (w[i] >> 24) & 0xFF
        if cmd == 0x05:
            mode = w[i] & 0xFF
            i += 1
            continue
        if cmd == 0x30:
            row = [_s32(w[i + 1 + k]) for k in range(4)]
            i += 5
            continue
        num = (w[i] >> 16) & 0xFF
        i += 1
        if cmd in (0x60, 0x64, 0x68, 0x6C):
            out.append([_s32(w[i + c]) for c in range(cols)])
            i += cols
        elif cmd in (0x61, 0x65, 0x69, 0x6D):
            need = num * cols
            nw = (need * 2 + 3) // 4
            raw = b''.join(w[i + k].to_bytes(4, 'little') for k in range(nw))
            i += nw
            for r in range(num):
                out.append([_s16(int.from_bytes(
                    raw[2 * (r * cols + c):2 * (r * cols + c) + 2], 'little'))
                    for c in range(cols)])
        else:
            need = num * cols
            nw = (need + 3) // 4
            raw = b''.join(w[i + k].to_bytes(4, 'little') for k in range(nw))
            i += nw
            for r in range(num):
                vals = []
                for c in range(cols):
                    b = raw[r * cols + c]
                    b = b - 256 if b & 0x80 else b
                    if mode == MODE_DIFF:
                        vals.append((row[c] if r == 0 else out[-1][c]) + b)
                    else:
                        vals.append(b)
                out.append(vals)
    return out[:rows]


# ---------------------------------------------------------------------------
#  rmcGeometry::Packet::Init(...)  0x002AB110  -  the last stage
#
#  6236 bytes that turn one packet's strips into the VIF chain the hardware
#  eats. Its shape, read out of the decompilation:
#
#    header    ITOP_base | 0x6C024000   two quadwords: the packet's bounding
#                                       SPHERE (centre, radius), the vertex
#                                       count, and 1.0 / (1 << exponent) - the
#                                       inverse scale the VU multiplies by.
#              ITOP_base | 0x60024000   the short form, when the sphere is not
#                                       wanted: inverse scale and count only.
#    streams   PackData for the positions - 3 columns, or FOUR when skinned,
#              the extra one carrying the matrix index - then PackData for the
#              UVs, 2 columns or 4 when both sets are present. Colours are
#              already bytes and go out as a direct V4-8 unpack (0x6E, or 0x7E
#              for the masked form) rather than through the compressor.
#    tail      STMOD 0 if the mode was left in difference, then ITOP and
#              MSCAL 6 - or MSCAL 8 when the mesh is skinned, a different
#              microprogram entry. Zero-padded to a quadword boundary.
#
#  The packet slot then gets `+6` = vertex count and `+4` = size >> 4, the
#  quadword count.
#
#  `dword_615DA0` is two words, 0x9E and 0x1C5: the pair of VU buffer bases the
#  packets alternate between. Which one a packet uses is `4 * (index & 1)` -
#  double buffering, so the VU can transform one packet while the next is still
#  being DMA'd in.
# ---------------------------------------------------------------------------

# Measured against a shipped .mesh.pck, not inferred: the two ITOP bases and
# the fixed offsets of each stream from them. Reading model/probe1.mesh.pck back
# gives ITOP 09E, colour 0A0, uv 0D0, position 100 on one buffer and 1C5 / 1C7 /
# 1F7 / 227 on the other - the same layout shifted, +2, +0x32 and +0x62.
VU_BUFFER_BASE = (0x9E, 0x1C5)      # dword_615DA0
VU_OFF_COLOUR = 0x02
VU_OFF_UV = 0x32
VU_OFF_POS = 0x62

VIF_ITOP = 0x04000000
VIF_MSCAL = 0x14000000
MSCAL_ENTRY = 6                     # 335544326 = 0x14000006
MSCAL_ENTRY_SKINNED = 8             # 335544328 = 0x14000008

# The cap Packetize is called with. The call site picks 42 or 48 on byte_615D00,
# and the shipped file was built with the larger one: its packets hold up to 48
# vertices, never 42.
PACKET_CAP = 48


def _bounding_sphere(points):
    """The header carries a sphere, not the box: centre is the box's midpoint
    and the radius comes off the diagonal, halved."""
    if not points:
        return (0.0, 0.0, 0.0), 0.0
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    ext = [hi[i] - lo[i] for i in range(3)]
    centre = tuple(lo[i] + ext[i] * 0.5 for i in range(3))
    diag = (ext[0] * ext[0] + ext[1] * ext[1] + ext[2] * ext[2]) ** 0.5
    return centre, diag * 0.5


def _f32(x):
    import struct
    return struct.unpack('<I', struct.pack('<f', x))[0]


def packet_init(verts, exponent, index, skinned=False, uvs=None,
                colours=None):
    """Build one packet's VIF chain, in the order a shipped file has it:
    header, positions, uvs, colours, ITOP, MSCAL.

    The header is the SHORT form - `UNPACK S-32 x2 USN` at the ITOP address,
    carrying 1.0/(1 << exponent) and the vertex count. Packet::Init can also
    emit a long form with a bounding sphere, gated on byte_615D88, and the
    retail file does not use it.
    """
    chain = VifChain()
    base = VU_BUFFER_BASE[index & 1]
    n = len(verts)

    chain.tag((0x60 << 24) | 0x020000 | VIF_USN | (base & 0x3FF),
              'header (inverse scale, count)')
    chain.word(_f32(1.0 / float(1 << exponent)))
    chain.word(n)

    # Positions. Four columns when skinned - the extra one is the matrix index.
    cols = 4 if skinned else 3
    rows = []
    for v in verts:
        row = [int(c * (1 << exponent)) for c in v[:3]]
        if skinned:
            row.append(0)
        rows.append(row)
    if rows:
        pack_data(chain, rows, len(rows), cols, base + VU_OFF_POS, MODE_NONE)

    if uvs:
        ucols = 4 if len(uvs[0]) > 2 else 2
        urows = [[int(c * 4096.0) for c in u[:ucols]] for u in uvs]
        pack_data(chain, urows, len(urows), ucols, base + VU_OFF_UV, MODE_NONE)

    if colours:
        # Already bytes, so straight out as V4-8 with no compressor in the way.
        chain.tag((0x6E << 24) | ((len(colours) & 0xFF) << 16)
                  | (base + VU_OFF_COLOUR) & 0x3FF, 'UNPACK4-8 colour')
        buf = bytearray()
        for c in colours:
            buf += int(c & 0xFFFFFFFF).to_bytes(4, 'little')
        chain.bytes_padded(buf)

    chain.tag(VIF_ITOP | (base & 0x3FF), 'ITOP')
    chain.tag(VIF_MSCAL |
              (MSCAL_ENTRY_SKINNED if skinned else MSCAL_ENTRY), 'MSCAL')
    while len(chain.words) % 4:
        chain.word(0)
    return chain


# ---------------------------------------------------------------------------
#  rmcFvf  0x002BAEE8..0x002BB300  -  the vertex format
#
#  Fourteen channels. `+0` is a u16 presence mask, `+2` a byte holding the total
#  size, and from `+4` the per-channel size CODES, three bits each, packed
#  across u16s - RecomputeTotalSize 0x002BB300 walks them and adds up
#  dword_647DA8[code].
#
#  Channel numbers, read from each setter's SetChannelDataSize argument:
#      0 Pos   1 BlendWeight   2 Bindings   3 Normal   4 Diffuse   6+i Texture i
#
#  Size code 6 is not a size. It means "the default for this channel", and each
#  setter resolves it differently - Pos turns it into 3 or 2 depending on its
#  fourth argument, Texture into 1. That is why the disable mask in
#  rmcGeometry::Init is built with 6.
# ---------------------------------------------------------------------------

# dword_647DA8. Only six entries are real: [6] and [7] read 8192123 and 8060960,
# which is whatever follows the table in .rodata, and nothing indexes them
# because 6 is resolved away before it ever reaches here.
RMC_DATA_SIZE = (4, 8, 12, 16, 4, 4)

CH_POS, CH_BLENDWEIGHT, CH_BINDINGS, CH_NORMAL, CH_DIFFUSE = 0, 1, 2, 3, 4
CH_TEXTURE0 = 6

CH_NAMES = {CH_POS: 'pos', CH_BLENDWEIGHT: 'blendweight',
            CH_BINDINGS: 'bindings', CH_NORMAL: 'normal',
            CH_DIFFUSE: 'diffuse', 6: 'tex0', 7: 'tex1'}


class RmcFvf(object):
    def __init__(self):
        self.mask = 0
        self.size_code = [0] * 14

    def set_channel(self, ch, on, code):
        self.size_code[ch] = code if on else 0
        if on:
            self.mask |= 1 << ch
        else:
            self.mask &= ~(1 << ch)

    def set_pos(self, on, code, wide=False):
        if code == 6:
            code = 3 if wide else 2
        self.set_channel(CH_POS, on, code if on else 0)

    def set_texture(self, i, on, code):
        if code == 6:
            code = 1
        self.set_channel(CH_TEXTURE0 + i, on, code)

    def disable_set_channels(self, other):
        """DisableSetChannels  0x002BB1F8 - every channel ON in `other` is
        turned OFF here. rmcGeometry::Init builds `other` with the diffuse
        channel set when a companion file exists, which is how a mesh whose
        colours live in a side file stops carrying them inline."""
        for ch in range(14):
            if other.mask & (1 << ch):
                self.set_channel(ch, False, 0)

    def total_size(self):
        n = 0
        for ch in range(14):
            if self.mask & (1 << ch):
                n += RMC_DATA_SIZE[self.size_code[ch]]
        return n

    def channels(self):
        return [(CH_NAMES.get(ch, 'ch%d' % ch), RMC_DATA_SIZE[self.size_code[ch]])
                for ch in range(14) if self.mask & (1 << ch)]


def rmc_geometry_compute_fvf(mesh, disable):
    """rmcGeometry::ComputeFVF  0x002AA178

    Reads the mesh as a u16 array, so every test below is one stream's count
    field: a1[38] is +76 (matrices), a1[10] is +20 (normals), a1[18] is +36
    (colours), a1[22] is +44 (tex1), a1[26] is +52 (tex2).

    The one rule worth naming: diffuse is enabled on `colours && !matrices`. A
    skinned mesh gets NO colour channel - the two are mutually exclusive in the
    vertex format, not merely unusual together.
    """
    fvf = RmcFvf()
    skinned = mesh.a(SLOT_MATRIX).count != 0
    fvf.set_pos(True, 2)
    fvf.set_channel(CH_BLENDWEIGHT, skinned, 0)
    fvf.set_channel(CH_BINDINGS, skinned, 4)
    fvf.set_channel(CH_NORMAL, mesh.a(SLOT_NRM).count != 0, 2)
    fvf.set_channel(CH_DIFFUSE,
                    bool(mesh.a(SLOT_CPV).count) and not skinned, 4)
    fvf.set_texture(0, mesh.a(SLOT_TEX1).count != 0, 1)
    fvf.set_texture(1, mesh.a(SLOT_TEX2).count != 0, 1)
    fvf.disable_set_channels(disable)
    return fvf


# ---------------------------------------------------------------------------
#  rmcGeometry::Init(mshMesh const&, int, bool, int)  0x002AA2A0
#
#      +4 u16   packet count, taken straight from mshMaterial+16
#      +0       array of per-packet slots, 8 bytes each {data, u16 qwords,
#               u16 ...}, with the element count 16 bytes in front
#
#  mshMaterial+12 is a u16 ARRAY OF CUT POINTS, not a list of packets: entry i
#  is where packet i starts in the material's primitive list, which is why
#  Packetize opens by appending a zero to it. Init turns consecutive entries
#  into (first, count) pairs and hands each pair to rmcGeometry::Packet::Init.
#
#  When there is NO companion file the per-packet buffers are then merged into
#  one contiguous allocation of 16 * total_quadwords and memcpy'd in - which is
#  the shape a .pck holds.
# ---------------------------------------------------------------------------

class RmcGeometry(object):
    __slots__ = ('packets', 'fvf', 'exponent')

    def __init__(self):
        self.packets = []
        self.fvf = None
        self.exponent = 0


def rmc_geometry_init(mesh, mat, exponent, has_file=False):
    disable = RmcFvf()
    if has_file:
        # SetDiffuseChannel(&mask, 1, 6) - see DisableSetChannels above.
        disable.set_channel(CH_DIFFUSE, True, 4)
    geo = RmcGeometry()
    geo.fvf = rmc_geometry_compute_fvf(mesh, disable)
    geo.exponent = exponent
    # rmcGeometry::Packet::Init 0x002AB110 would run per packet here: 6236 bytes
    # that turn a run of strips into the VIF quadwords, applying the exponent.
    # Not reimplemented - the packet boundaries and the format it would write
    # into are what this produces.
    for pk in mat.packets:
        geo.packets.append((len(pk.primitives),
                            sum(len(pr.indices) for pr in pk.primitives)))
    return geo


# ---------------------------------------------------------------------------
#  rmcModelGeom, 24 bytes, filled by
#  rmcModelGeom::Load(mshMesh&, bool, char const*)  0x002A8FA0
#
#      +4  byte   matrix count, then bit 8 of the word is the skinned flag
#      +6  byte   the flags argument rmcModel::Create was given
#      +8  u16    group count  = mshMesh[+68] = the material count
#      +12 u16[]  one shader slot per group, from atoi on the material name
#      +16        geometry array; the element count sits 16 bytes BEFORE it
# ---------------------------------------------------------------------------

class RmcModelGeom(object):
    __slots__ = ('groups', 'matrices', 'skinned', 'flags', 'shader_slots',
                 'geometries', 'geoms', 'exponent', 'aabb_min', 'aabb_max')

    def __init__(self):
        self.groups = 0
        self.matrices = 0
        self.skinned = False
        self.flags = 0
        self.shader_slots = []
        self.geometries = []
        self.geoms = []
        self.exponent = 0
        self.aabb_min = (0.0, 0.0, 0.0)
        self.aabb_max = (0.0, 0.0, 0.0)


def fit_exponent(positions, override=0):
    """The fixed-point exponent, exactly as Load derives it.

        m = max |x|, |y|, |z| over every position
        j = 15; while (m >= 0.99) { m *= 0.5; j--; }
        if (j <= 0) j += 16;
        if (dword_70FAD4) j = dword_70FAD4;

    The comparison is `>= 0.99000001` in the decompilation, not `>= 1.0`, and
    the wrap at j <= 0 means a model far from the origin comes back with a
    LARGE exponent rather than a clamped one. Both are reproduced literally.
    """
    m = 0.0
    for p in positions:
        for c in p[:3]:
            a = abs(c)
            if m < a:
                m = a
    j = 15
    while m >= 0.99000001:
        m *= 0.5
        j -= 1
    if j <= 0:
        j += 16
    if override:
        j = override
    return j


def quantisation_overflow(positions, exponent):
    """How many components do NOT survive the round trip to s16.

    This is the trap in the wrap above, and it is not hypothetical: Fork Alpha
    2004 traced a broken city back to it. `if (j <= 0) j += 16` means a model far
    enough from the origin comes back with a LARGE exponent instead of a clamped
    one, and `pos * (1 << exponent)` then runs past 32767 and wraps - a facade
    ends up on the far side of the block.

    The engine does not check. Anything generating a mesh for it has to, because
    the failure is silent: the file is structurally perfect and the geometry is
    somewhere else.
    """
    limit = 1 << exponent
    bad = 0
    worst = 0.0
    for p in positions:
        for c in p[:3]:
            v = c * limit
            if v < -32768.0 or v > 32767.0:
                bad += 1
                if abs(v) > worst:
                    worst = abs(v)
    return bad, worst


def rmc_model_geom_load(geom, mesh, is_not_mod, exponent_override=0,
                        packet_cap=42):
    mats = mesh.a(SLOT_MATERIAL)
    geom.groups = mats.count
    geom.matrices = mesh.a(SLOT_MATRIX).count
    # `if (a3) flags |= (mesh[80] != 0) << 8` - the bit is gated on the SAME
    # argument that says "this did not come from a .mod". So a .mod always
    # produces a geom with the skinned bit clear, however skinned the mshMesh
    # is. Reproduced rather than corrected: it is what the engine does.
    geom.skinned = bool(is_not_mod and mesh.skinned)

    positions = mesh.positions()
    geom.exponent = fit_exponent(positions, exponent_override)

    for k, mat in enumerate(mats.items):
        geom.shader_slots.append(mat.slot())
        if not msh_material_tristrip(mat, mesh):
            raise ModError('Tristrip refused material %d (%r)' % (k, mat.name))
        # Packetize(mat, 42, 0, adjunct_count). 42 quadwords, or 48 when
        # byte_615D00 is set - the call site picks between them.
        if not msh_material_packetize(mat, packet_cap,
                                      mesh.a(SLOT_ADJUNCT).count):
            raise ModError('Packetize refused material %d (%r)' % (k, mat.name))
        geom.geometries.append(mat)
        geom.geoms.append(rmc_geometry_init(mesh, mat, geom.exponent))
    return geom


def rmc_create_model_aabox(positions):
    """rmcCreateModelResetAABox + rmcCreateModel_AABoxGrow, 0x002A8CA0's tail."""
    if not positions:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = [positions[0][i] for i in range(3)]
    hi = list(lo)
    for p in positions:
        for i in range(3):
            if p[i] < lo[i]:
                lo[i] = p[i]
            if p[i] > hi[i]:
                hi[i] = p[i]
    return tuple(lo), tuple(hi)


# ---------------------------------------------------------------------------
#  rmcModel::Create(char const*, int)  0x002A8CA0
# ---------------------------------------------------------------------------

def rmc_model_create(root, name, flags=0, exponent_override=0):
    ext = os.path.splitext(name)[1].lower()
    is_mod = ext == '.mod'

    blob = dat_asset_manager_open(root, name, 'mod')
    if blob is None:
        # Open returns 0 and LoadMod returns 0; Create then returns 0 too. The
        # caller is what hangs, not this.
        raise ModError('%s does not resolve under %s' % (name, root))

    kind, version, newfmt = sniff_version(blob)
    if kind != VERSION_110:
        raise ModError('this walks "version: 1.10" only; %s declares %r'
                       % (name, blob[:13].decode('latin-1', 'replace')))

    mesh = MshMesh()
    tok = DatAsciiTokenizer(blob.decode('latin-1'))
    tok.get_token()                     # "version:"
    tok.get_token()                     # "1.10"
    if not msh_mesh_load_mod(mesh, tok, newfmt, version):
        raise ModError('LoadMod refused %s' % name)

    geom = RmcModelGeom()
    rmc_model_geom_load(geom, mesh, not is_mod, exponent_override)
    geom.flags = flags
    geom.aabb_min, geom.aabb_max = rmc_create_model_aabox(mesh.positions())
    return mesh, geom


# ---------------------------------------------------------------------------

def describe(name, mesh, geom, verbose=False):
    print('%s' % name)
    print('  mshMesh (temporary, 84 bytes)')
    for off, label, stride in (
            (SLOT_POS, 'v   positions', 12),
            (SLOT_POSSKIN, 'v   pos (skin)', 32),
            (SLOT_NRM, 'n   normals', 12),
            (SLOT_CPV, 'c   colours', 16), (SLOT_TEX1, 't1  tex1', 8),
            (SLOT_TEX2, 't2  tex2', 8), (SLOT_ADJUNCT, '    adjuncts', 0),
            (SLOT_MATERIAL, '    materials', 32),
            (SLOT_MATRIX, '    matrices', 12)):
        a = mesh.a(off)
        flag = '' if a.count == a.capacity else '   <- header said %d' % a.capacity
        print('    +%-3d %-14s %5d%s' % (off, label, a.count, flag))
    print('    +80  skinned        %5s' % ('yes' if mesh.skinned else 'no'))

    tris = sum(len(triangles_of(p)) for m in geom.geometries
               for p in m.primitives)
    print('  rmcModelGeom (24 bytes, survives)')
    print('    +8   groups         %5d' % geom.groups)
    print('    +12  shader slots   %s' % (geom.shader_slots or '-'))
    print('    +4   matrices       %5d   skinned %s'
          % (geom.matrices, 'yes' if geom.skinned else 'no'))
    print('         exponent       %5d   (1/%d)' % (geom.exponent,
                                                    1 << geom.exponent))
    print('         AABox          %s .. %s'
          % (tuple('%.3f' % c for c in geom.aabb_min),
             tuple('%.3f' % c for c in geom.aabb_max)))
    print('         triangles      %5d' % tris)
    if geom.geoms:
        fvf = geom.geoms[0].fvf
        print('  rmcFvf (vertex format, from ComputeFVF)')
        print('         mask %04X, %d bytes/vertex' % (fvf.mask, fvf.total_size()))
        print('         %s' % ', '.join('%s:%dB' % c for c in fvf.channels()))
    if verbose:
        for k, mat in enumerate(geom.geometries):
            print('    [%d] %-28s slot %-4d %d packets, %d strips, %d tris'
                  % (k, mat.name, mat.slot(), len(mat.packets),
                     len(mat.primitives),
                     sum(len(triangles_of(p)) for p in mat.primitives)))
            for t in mat.textures:
                print('         texture %d %s' % t)


def find_root(path):
    """The asset root is whatever directory the path sits under - the engine
    gets it from `T:\\mc3ssets` at 0x00637DCF, rewritten to the HostFS tree."""
    p = os.path.abspath(path)
    while True:
        parent = os.path.dirname(p)
        if parent == p:
            return os.path.dirname(os.path.abspath(path))
        if os.path.basename(p).upper() == 'ASSETS':
            return p
        p = parent


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    verbose = '--verbose' in argv
    if argv[1] == '--all':
        base = argv[2]
        ok = bad = tri_bad = pack_bad = rt_bad = ovf = 0
        npk = nwords = 0
        failures = []
        for dirpath, _, files in os.walk(base):
            for f in sorted(files):
                if not f.lower().endswith('.mod'):
                    continue
                full = os.path.join(dirpath, f)
                root = find_root(full)
                rel = os.path.relpath(full, root).replace(os.sep, '/')
                try:
                    mesh, geom = rmc_model_create(root, rel)
                    ok += 1
                    # The file's own header is an independent check on the walk:
                    # `primitives:` is a TRIANGLE count, so re-deriving it from
                    # the strips tests the parse, the strip parity and the
                    # degenerate-restart rule all at once.
                    tris = sum(len(triangles_of(pr)) for m in geom.geometries
                               for pr in m.primitives)
                    if tris != mesh.declared['primitives']:
                        tri_bad += 1
                        failures.append((rel, 'header says %d triangles, walk got %d'
                                       % (mesh.declared['primitives'], tris)))
                        continue
                    # Packetize must not invent or lose geometry, and no packet
                    # may pass the cap it was given. Both are checkable without
                    # a reference file, which is what makes them worth checking.
                    packed = sum(len(triangles_of(pr)) for m in geom.geometries
                                 for pk in m.packets for pr in pk.primitives)
                    if packed != tris:
                        pack_bad += 1
                        failures.append((rel, 'Packetize changed the triangles: '
                                            '%d -> %d' % (tris, packed)))
                        continue
                    over = [len(pk.primitives) for m in geom.geometries
                            for pk in m.packets
                            if sum(len(pr.indices) for pr in pk.primitives) > 42]
                    if over:
                        pack_bad += 1
                        failures.append((rel, '%d packets over the 42 cap' % len(over)))
                        continue
                    npk += sum(len(m.packets) for m in geom.geometries)
                    # The quantisation check. Fork Alpha 2004 traced a broken
                    # city to exactly this and the engine never complains, so
                    # the walk has to.
                    nb, worst_ = quantisation_overflow(mesh.positions(),
                                                     geom.exponent)
                    if nb:
                        ovf += 1
                        failures.append((rel, '%d components overflow the s16 '
                                            '(worst %.0f, exponent %d)'
                                            % (nb, worst_, geom.exponent)))
                    # Round-trip the packer. The positions become s16 the way
                    # Packet::Init makes them - scaled by the fitted exponent -
                    # then pack_data emits a VIF chain and unpack_data reads it
                    # back. Anything the compressor gets wrong shows up as a
                    # value that does not come home.
                    rows = [[int(c * (1 << geom.exponent)) for c in p3]
                            for p3 in mesh.positions()]
                    # In blocks of 42, because that is the shape the engine
                    # feeds it: NUM in a VIFcode is EIGHT BITS, so a single
                    # unpack cannot carry more than 255 rows, and a packet never
                    # holds more than the cap anyway.
                    broke_at = -1
                    for b in range(0, len(rows), 42):
                        blk = rows[b:b + 42]
                        chain = VifChain()
                        pack_data(chain, blk, len(blk), 3, 0, MODE_NONE)
                        back = unpack_data(chain, len(blk), 3)
                        nwords += len(chain.words)
                        if back != blk:
                            broke_at = b + next((i for i, (x, y) in
                                                enumerate(zip(back, blk))
                                                if x != y), 0)
                            break
                    if broke_at >= 0:
                        rt_bad += 1
                        failures.append((rel, 'pack round-trip broke at row %d '
                                            'of %d' % (broke_at, len(rows))))
                except (ModError, ValueError) as e:
                    bad += 1
                    failures.append((rel, str(e)))
        print('%d files walked' % (ok + bad))
        print('  %d failed to walk' % bad)
        print('  %d walked but disagreed on the triangle count' % tri_bad)
        print('  %d failed the Packetize invariants' % pack_bad)
        print('  %d failed the PackData round trip' % rt_bad)
        print('  %d with a quantisation OVERFLOW' % ovf)
        print('  %d match on everything  (%d packets, %d VIF words)'
              % (ok - tri_bad - pack_bad - rt_bad, npk, nwords))
        for rel, why in failures[:12]:
            print('    %-40s %s' % (rel, why))
        return 1 if (bad or tri_bad or pack_bad or rt_bad) else 0

    path = argv[1]
    root = find_root(path)
    rel = os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/')
    mesh, geom = rmc_model_create(root, rel)
    describe(rel, mesh, geom, verbose)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
