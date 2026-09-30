"""
mc3_city_build.py - build a Midnight Club 3 PS2 city .pck from nothing

Every city experiment so far has been a transplant into atlanta, and that makes
results hard to read: when something looks wrong on screen there is no way to
tell whether it is the imported geometry or the host city that produced it. This
builds the file instead, so everything inside it is accounted for.

WHAT IS GENERATED and what is COPIED, stated plainly, because the difference is
the whole point:

  generated   the 0x80 header, the MapRoot, the hood table, the hood record,
              the unique components, the ComponentData, the meshes, the group
              and block tables, the VIF packets, and the two colour palettes.
  copied      one shader object (0xAC bytes + the sub-object it points at) and
              the MapRoot scalars whose meaning is not established yet. Each
              copy is marked `COPIADO` at the call site.

The copies are what they are because a shader carries GS register state we have
not decoded (see mc3-city-materials); a shader has exactly ONE internal pointer,
at +0x08, aimed at the object right behind it, so relocating one is a single
fixup. Nothing else in it points anywhere - the rest is vtables, which are
absolute ELF addresses and stay valid wherever the object lands.

    python mc3_city_build.py --donor atlanta_midnight_clear.pck --out-path novo.pck
    python mc3_city_build.py --donor ... --xmod M.xmod --out-path novo.pck
    python mc3_city_build.py --verify- novo.pck

The default content is a test cube, which is the smallest thing that proves the
whole chain: if a cube appears at a known place with a known size, the header,
the hood walk, the component placement, the mesh tables and the VIF packet are
all correct, and anything that fails afterwards is narrower than that.
"""

import argparse
import importlib.util
import math
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    'mc2_to_mc3', os.path.join(_HERE, 'experimental', 'mc2_to_mc3.py'))
mc2 = importlib.util.module_from_spec(_SPEC)
sys.modules['mc2_to_mc3'] = mc2
_SPEC.loader.exec_module(mc2)

BASE_VA = 0x06800000        # the same for every retail city
CITY_KIND = 67
VERSION = 1

# THE TOKEN SPACE IS PER CITY, and ignoring that was a bug of mine.
#
# The VT_* constants in this file were all measured on ATLANTA. Measured:
# atlanta, detroit and sd share one space (MapRoot+0x00 = 0x007A0138) but
# TOKYO uses the same space shifted by +0x488 (0x007A05C0). When building a city
# hosted on tokyo I wrote MapRoot+0x00 and the 18 group descriptors with ATLANTA
# tokens - the file came out with tokyo structure and atlanta tokens mixed.
#
# The shift is discovered from the donor itself in `donor_token`, and every
# emitted token goes through `tok()`. There is no table of cities: MapRoot+0x00
# of the donor is read and compared with the constant measured here.
TOKEN_SHIFT = 0            # filled in by donor_token()


def tok(v):
    """A class token in the donor's token space."""
    return v + TOKEN_SHIFT


def donor_token(donor):
    """Find the shift of the donor's token space from MapRoot+0x00."""
    with open(donor, 'rb') as f:
        f.seek(0x80)
        v = struct.unpack('<I', f.read(4))[0]
    d = v - VT_MAPROOT
    if d not in (0, 0x488):
        raise SystemExit(
            'unknown token space in the donor: MapRoot+0x00 = 0x%08X, '
            'expected 0x%08X (atlanta/detroit/sd) or 0x%08X (tokyo)'
            % (v, VT_MAPROOT, VT_MAPROOT + 0x488))
    return d


VT_MAPROOT = 0x007A0138
VT_MAPROOT_40 = 0x007A01E8
VT_ARRAY = 0x007A1FB8       # the object [vtable][ptr][count][count][0]
# GENERAL RULE, learned the hard way: an object that I create does not enter the
# list the game walks to translate token -> address. Measured in RAM: 607 retail
# meshes became 0x00626D98 and mine kept 0x007A1E18; 240 ComponentData became
# 0x00625098 and mine kept 0x0079FF88. A generated object has to be BORN with the
# translated address.
#
# This does not apply to objects the Place CONSTRUCTS (MapRoot, group descriptor,
# shader): those get their vtable stamped by their own constructor, so whatever
# is in the file is overwritten.
VT_MESH_TOKEN = 0x007A1E18
VT_MESH = 0x00626D98
VT_SHADER = 0x007A1EF8      # the default class: 1951 of the 4105 objects in atlanta
# ComponentData+0x28 is a real VTABLE, called when the camera gets close. In the
# FILE it is 0x0079FF88 in atlanta's 651 components, but that is a TOKEN:
# measured in RAM, the retail components become **0x00625098** and the only one
# left with the raw token was mine. The translator walks a list the generated
# component is not in, so writing the token makes the virtual call jump into the
# game's UTF-16 string table - the `Jump to unaligned address (PC: 0x0061004d)`
# is literally "Ma" from a car name, read from slot +0x38 of that "vtable".
#
# Writing the ALREADY TRANSLATED address fixes it without relying on the translator.
# WRITE THE TOKEN, NOT THE RAM ADDRESS - and this REVERSES the previous decision,
# with the measurement that overturns it.
#
# The old note said: 'the translator walks a list the generated component is not
# in, so writing the token makes the virtual call jump into the string table'.
# That was measured while the generated component was NOT yet in a hood's unique
# array the way retail's are. Now it is, and retail is unambiguous: tokyo has
# ComponentData+0x28 = 0x007A0410 and Component+0x10 = 0x007A0438 in 650 of 650,
# atlanta 0x0079FF88 and 0x0079FFB0 in 651 of 651 - always a TOKEN, never a RAM
# address.
#
# Writing the already translated address into an object the translator DOES reach
# is worse than writing the token: it translates it again, and the result is
# garbage. The symptom that settled it was the hood1 graft, which hung with
# PC=0x00650073 - UTF-16 text, i.e. a jump into the string table, exactly the
# signature of a virtual call through the wrong vtable.
VT_COMPDATA_TOKEN = 0x0079FF88   # as it appears in the retail file
VT_COMPDATA = 0x00625098         # as it ends up in RAM: this is the one that counts
# The component record's `type_va` (+0x10). In the atlanta file it is 0x0079FFB0
# in all 651; in the RAM of the San Diego savestate it is 0x006250C0 (mcCityModel)
# in 628. Same token/vtable relation as VT_COMPDATA, so the RAM version applies
# here for the same reason.
VT_COMPONENT_TOKEN = 0x0079FFB0
VT_COMPONENT = 0x006250C0
# Instances have three classes of their own, measured in savestate RAM:
#   Instance (the 0x60 record)             mcInstCityModel      0x006251B8
#   ComponentData of the type (+0x28)      mcInstCityModelType  0x00625190
# and the Instance record itself carries the token 0x007A00B8 at +0x10.
VT_INST = 0x006251B8            # mcInstCityModel, goes in Instance+0x10
VT_INST_KIND = 0x00625190      # mcInstCityModelType, goes in ComponentData+0x28
# THE TOKENS OF THESE TWO CLASSES, measured by counting and not by analogy. The
# affine relation that works for COMPDATA/COMPONENT (token = RAM - 0x625098 +
# 0x79FF88) does NOT hold here: it predicted 0x007A0080/0x007A00A8 and those
# values appear ZERO times in the files. The right ones were found by counting: in
# atlanta 0x007A00B8 appears 3608 times (the number of instances) and 0x007A0090
# 231 times (the number of types); in tokyo 0x007A0540 x7688 and 0x007A0518 x317,
# the same +0x488 as the rest of the token space.
VT_INST_TOKEN = 0x007A00B8       # goes in Instance+0x10
VT_INST_KIND_TOKEN = 0x007A0090  # goes in the kind's ComponentData, +0x28
INSTANCE_SIZE = 0x60
# How many slots of the shader group receive a real object. Atlanta's props
# index from 0 to 91; 128 covers that with room to spare. The rest of the array
# stays null, like the 81.5%% of null slots in retail.
SLOTS_PREENCHIDOS = 128
TEST_PADDING = 0            # see the layout control in constroi_place
# Size of the CellIndex object (MapRoot+0x1C). Measured the same in atlanta and sd.
CELLINDEX_SIZE = 0x7130

SHADER_SIZE = 0xAC           # measured: the pointer at +0x08 points to the end of the object
# The MapRoot is 0x290 bytes: atlanta's first palette sits at file offset 0x310,
# i.e. MapRoot+0x290. I used 0x230, and `mcCity+0x268` - the COUNT of the two
# scratch arrays of +0x1C - fell outside it and was read as garbage.
# The MapRoot goes up to 0x2290, not 0x290: BOTH 256-colour palettes live inside
# it, at +0x290 and +0x1290. In the four retail cities the pointers at +0x2C and
# +0x30 are exactly 0x06800290 and 0x06801290 - always inline, never in a
# separate block. Putting the palettes elsewhere kept the pointer right but the
# layout different, and there is no reason to bet nobody reads MapRoot+0x290
# directly when imitating it costs nothing.
MAPROOT_SIZE = 0x2290
OFF_LIVE_PALETTE = 0x290
SRC_PALETTE_OFF = 0x1290
COMPONENT_SIZE = 28
HOOD_REC_SIZE = 176
HOOD_ENTRY_SIZE = 20


class Writer:
    """Assemble the .pck body and resolve the pointers at the end.

    A .pck is a memory image: what is stored are VIRTUAL ADDRESSES, and
    `file_offset = VA - base + 0x80`. Writing that front to back requires
    knowing the address of something before it exists, so pointers enter here as
    pending entries (`aponta`) and are filled in by `fecha`.
    """

    def __init__(self):
        # The MapRoot is NOT 0x80 bytes. `mcCity::mcCity` touches +0x190, +0x194,
        # +0x198, +0x19C, +0x1CC and +0x228, and a loop of 20 pointers starting at
        # +0x1D8. Reserving only 0x80 left all of those fields landing on whatever
        # was appended next - the simulator caught the constructor reading a
        # shader float (0x3D80000B) as a pointer. Zero in them is safe: each one is
        # guarded by `if (ptr)` before it is walked.
        self.d = bytearray(0x80 + MAPROOT_SIZE)
        self.pend = []                        # [(field_offset, key)]
        self.marks = {}                       # chave -> offset

    # ---- placement
    def align_to(self, n=16):
        while len(self.d) % n:
            self.d += b'\x00'

    def put(self, blob, map_key=None, align=16):
        self.align_to(align)
        at = len(self.d)
        if map_key:
            self.marks[map_key] = at
        self.d += blob
        return at

    def label(self, map_key, off):
        self.marks[map_key] = off

    def point(self, field, map_key):
        """Later, write the VA of `key` into `field`."""
        self.pend.append((field, map_key))

    def u32(self, off, v):
        struct.pack_into('<I', self.d, off, v)

    def va(self, off):
        return off - 0x80 + BASE_VA

    def close(self, path):
        for field, map_key in self.pend:
            if map_key not in self.marks:
                raise KeyError('pointer without a target: %s' % map_key)
            self.u32(field, self.va(self.marks[map_key]))
        self.align_to(16)                       # all 6718 retail .pck are aligned
        struct.pack_into('<I', self.d, 0x00, BASE_VA)
        struct.pack_into('<I', self.d, 0x04, CITY_KIND)
        struct.pack_into('<I', self.d, 0x08, VERSION)
        struct.pack_into('<I', self.d, 0x0C, len(self.d) - 0x80)
        with open(path, 'wb') as f:
            f.write(bytes(self.d))
        return len(self.d)


def cube(side=40.0):
    """A cube centred on the origin, as the converter delivers it: strip blocks.

    Face by face, each one its own 4-vertex strip, and the degenerate stitching
    joins everything into a single block. The winding follows the game's rule -
    triangle i is (i-2,i-1,i) with i even - and the faces point outwards.
    """
    h = side / 2.0
    v = [(-h, -h, -h), (h, -h, -h), (h, -h, h), (-h, -h, h),
         (-h, h, -h), (h, h, -h), (h, h, h), (-h, h, h)]
    faces = [(7, 6, 4, 5), (0, 1, 3, 2), (4, 5, 0, 1),
             (5, 6, 1, 2), (6, 7, 2, 3), (7, 4, 3, 0)]
    uv = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    strip_list = []
    for f in faces:
        pos = [v[i] for i in f]
        nr = [(0.0, 1.0, 0.0)] * 4
        strip_list.append((pos, list(uv), nr, False))
    # same stitching as the converter: a bridge of degenerates between strips
    cp, cu, cn = [], [], []
    for pos, u, nr, _odd in strip_list:
        if not cp:
            cp, cu, cn = list(pos), list(u), list(nr)
            continue
        bridge = 2 if len(cp) % 2 == 0 else 3
        cp += [cp[-1]] + [pos[0]] * (bridge - 1)
        cu += [cu[-1]] + [u[0]] * (bridge - 1)
        cn += [cn[-1]] + [nr[0]] * (bridge - 1)
        cp += pos
        cu += u
        cn += nr
    return [(cp, cu, cn, False)]


def read_model(file_path, scale, keep_origin=False):
    """Blocks from an MC2 .xmod, through the already validated path.

    `strips_to_blocks` anchors the mesh on its own box for the component path.
    Instances, however, already carry in place.tsv the matrix written for the
    ORIGINAL pivot of the .mod. Recentring the mesh and reapplying that matrix
    without compensation shifts the world: on the LAX emissive cards the measured
    error reaches 1825.77 units and puts huge planes near the camera. For
    instances, restore the origin before quantisation; that is exactly the
    geometry the original matrix was computed for.
    """
    v, t1, adj, strips = mc2.read_xmod(file_path)
    bl, anchor = mc2.strips_to_blocks(v, t1, adj, strips)
    if keep_origin:
        bl = [([tuple(q[i] + anchor[i] for i in range(3)) for q in pos],
               u, nr, o) for pos, u, nr, o in bl]
    if scale != 1.0:
        bl = [([tuple(c * scale for c in q) for q in pos], u, nr, o)
              for pos, u, nr, o in bl]
    return bl


def shader_slots(donor):
    """[slot] of the donor that has a typical shader, by the SAME test as shader_block.

    The first version of this inherited the model from `modelo_shader`, which
    Fork City MODS itself documents as wrong - sub-object at +0xAC, the 3-in-1900
    case - and so it listed zero slots in a donor that has hundreds. The test is
    by STRUCTURE (type 0, sub at +0x10, byte +0x04 of the sub in 0..3), which is
    also what lets tokyo pass: it has the same class with the token space shifted
    by +0x488, and comparing against the vtable rejected it.

    AND THERE IS A SECOND TEST, which was missing and cost me a round. Accepting
    byte +0x04 of the sub in 0..3 says the LOADER dispatches (0x2BA718:
    0 rmcTexturePS2, 1 skip, 2 rmcTextureReference, 3 rmcDictionaryReference,
    anything else = infinite loop). It does not say the SHADER SURVIVES MY COPY.
    `shader_block` takes 0xB0 bytes and `clean_pointers` zeroes every pointer except
    +0x08; in a type-0 sub that is harmless, because rmcTexturePS2 is
    self-contained, but in a type-2 one it zeroes the fallback at sub+0x0C -
    exactly the pointer the texture fork documented as mandatory - and the game
    jumps into CDCDCDCD.

    Measured on atlanta: of the 92 slots the old test approved, 88 are type 0 and
    4 are type 2, and those 4 lose a pointer of the sub. 84 safe ones remain.
    Slot 65, which was the only candidate of the paired experiment, is one of the
    four - the test I ran with it does not count as a negative result, it counts
    as a voided test.
    """
    p = mc2.Pck(donor)
    d = p.data
    a18 = p.F(struct.unpack_from('<I', d, 0x80 + 0x0C)[0])
    g0 = p.F(struct.unpack_from('<I', d, a18 + 4)[0])
    n = struct.unpack_from('<H', d, a18 + 8)[0]
    out = []
    for k in range(n):
        v = struct.unpack_from('<I', d, g0 + 4 * k)[0]
        if not (v and p.inside(p.F(v))):
            continue
        o = p.F(v)
        if struct.unpack_from('<I', d, o + 4)[0] & 0x7F:
            continue
        if struct.unpack_from('<I', d, o + 8)[0] != p.V(o + 0x10):
            continue
        if d[o + 0x14] not in (0, 1, 2, 3):
            continue
        # The WHOLE block has to survive `limpa`, not just the sub-object.
        # My first version of this test looked from +0x10 onwards and so it
        # approved slot 30 - which was the default, the "first that fits", and
        # therefore the shader of EVERY city I had generated so far.
        #
        # The field it missed is +0x0C. Census on atlanta, 110 type-0 shaders:
        # 86 hold 0xCDCDCDCD (the fill pattern), 14 hold a class token, 2 are
        # null and EIGHT point to a real object inside the pck. Slot 30 is one
        # of the eight, and `limpa` zeroed that pointer - the copied block ended
        # up with a shader that promises a sub-object and delivers null.
        #
        # Who paid for it: Fork City MODS 2's shader guard refused those calls
        # (80,907 in 1472 frames) and the city loaded but stayed INVISIBLE; with
        # the guard off, the same call hangs on frame 1. The two symptoms I
        # chased for days were the same defect seen from two sides.
        #
        # 0xCDCDCDCD and tokens are not touched by `limpa` (they are not in the
        # donor's VA range), so a slot whose +0x0C is one of those is copied
        # byte for byte. There are 78 such slots in atlanta, against the 84 the
        # old test approved; the first safe one is 39, not 30.
        raw_ = bytes(d[o:o + 0xB0])
        clean = clean_(raw_, donor, keep=(0x08,))[0]
        if any(raw_[i:i + 4] != clean[i:i + 4]
               for i in range(0, 0xB0, 4) if i != 0x08):
            continue
        out.append(k)
    return out


def shader_model(donor, slot=None):
    """COPIED: a donor shader object, ready to be relocated.

    Returns (shader_bytes, sub_object_bytes, slot). The shader is 0xAC bytes with
    a single pointer, at +0x08, pointing to the object right behind it;
    everything else is an ELF vtable (absolute, still valid anywhere) or inline
    GS register data we have not decoded yet.

    Without `slot`, takes the FIRST one that fits - which in atlanta is 30, and
    which does NOT read the ITOP+2 stream. With `slot`, takes that one and fails
    loudly if it does not fit, because a silently swapped donor is worse than an
    error.
    """
    p = mc2.Pck(donor)
    d = p.data
    a18 = p.F(struct.unpack_from('<I', d, 0x80 + 0x0C)[0])
    g0 = p.F(struct.unpack_from('<I', d, a18 + 4)[0])
    if slot is not None:
        good = shader_slots(donor)
        if slot not in good:
            near_check = [k for k in good if abs(k - slot) <= 8]
            raise SystemExit(
                'slot %d has no shader of the default class in %s.%s'
                % (slot, os.path.basename(donor),
                   ('  Near: %s' % near_check) if near_check else
                   '  Use --shader-list to see the %d that fit.' % len(good)))
        range_ = range(slot, slot + 1)
    else:
        range_ = range(1329)
    for k in range_:
        v = struct.unpack_from('<I', d, g0 + 4 * k)[0]
        if not (v and p.inside(p.F(v))):
            continue
        o = p.F(v)
        if struct.unpack_from('<I', d, o)[0] != VT_SHADER:
            continue
        sub = struct.unpack_from('<I', d, o + 8)[0]
        if not p.inside(p.F(sub)) or p.F(sub) != o + SHADER_SIZE:
            continue
        so = p.F(sub)
        return bytes(d[o:o + SHADER_SIZE]), bytes(d[so:so + 0xA0]), k
    raise SystemExit('no shader of the default class with the expected layout in %s'
                     % os.path.basename(donor))


# Textures from .tex.pck that THE GAME built. Name -> pck body.
GAME_TEXTURES = {}
TEXTURE_ORDER = []          # name at the position = shader slot
TEXTURE_POS = {}            # nome -> slot
GAME_INDICES = None
FLIP_WINDING = False        # reverse the vertex order of each UNPACK (winding)


def flip_winding_payload(payload):
    """Flip the winding by reversing the vertex order of each VIF batch.

    Works PER BATCH (header 0x60/num2 -> MSCAL). Reverses, in lockstep, every
    per-vertex UNPACK (position, uv, colour, normal), which flips the orientation
    of every triangle. Handles both of LA's position formats:

      - ABSOLUTE (0x69 V3-16 / 0x68 V3-32): reverses the raw records.
      - DELTA (0x6A V3-8, STMOD mode 2): decodes with the batch's STROW, reverses
        the absolute positions, and RE-ENCODES the deltas pointing the STROW at
        the new first vertex (every LA delta batch has its own STROW, so there is
        no carry between batches). |delta| is preserved and fits in int8.

    See FORKS: the packets came with MC2's winding, opposite to the VU culler's.
    """
    import mc3_vifpos as _vp
    d = bytearray(payload)
    cmds = list(_vp._commands(bytes(d)))
    # group into batches: each header starts one; MSCAL/next header closes it
    batches = []
    cur = None
    for c in cmds:
        h = _vp._header(c, bytes(d))
        if h is not None:
            if cur:
                batches.append(cur)
            cur = {'base': h[0], 'scale': h[1], 'count': h[2],
                   'strow_off': None, 'pos': None, 'attrs': []}
            continue
        if cur is None:
            continue
        if c['cmd'] == 0x30:
            cur['strow_off'] = c['payload']
        elif 0x60 <= c['cmd'] <= 0x7F and (c['num'] or 256) == cur['count']:
            if _vp._is_position(c, cur['base'], cur['count']):
                cur['pos'] = c
            else:
                cur['attrs'].append(c)
        elif c['cmd'] in (0x14, 0x15, 0x17):
            batches.append(cur)
            cur = None
    if cur:
        batches.append(cur)

    def rev_records(off, cnt, st):
        recs = [bytes(d[off + k * st:off + (k + 1) * st]) for k in range(cnt)]
        recs.reverse()
        d[off:off + cnt * st] = b''.join(recs)

    for bt in batches:
        pos = bt['pos']
        cnt = bt['count']
        # ONLY reverse a batch with ABSOLUTE position in a single UNPACK (num==count).
        # Delta (0x6A mode 2) and position split over several UNPACKs stay intact:
        # reversing raw delta bytes would corrupt them, and reversing only part of
        # a batch misaligns the streams. Those keep MC2's winding (the game culls
        # them), which is better than the whole city inverted.
        if pos is None or pos['mode'] == 2 or (pos['cmd'] & 0x6F) not in (0x68, 0x69):
            flip_winding_payload.skipped += 1
            continue
        cmd = pos['cmd']
        vn = (cmd >> 2) & 3
        vl = cmd & 3
        st = (vn + 1) * ((32 >> vl if vl else 32) // 8)
        for a in bt['attrs']:
            avn = (a['cmd'] >> 2) & 3
            avl = a['cmd'] & 3
            ast = (avn + 1) * ((32 >> avl if avl else 32) // 8)
            rev_records(a['payload'], cnt, ast)
        rev_records(pos['payload'], cnt, st)
    return bytes(d)


def _s8_local(b):
    return b - 256 if b >= 128 else b


def _s32_local(v):
    return v - (1 << 32) if v >= (1 << 31) else v


flip_winding_payload.skipped = 0

RUNTIME_BINDING = False


TEXTURE_LEVELS = {}     # name -> levels from the .ppf manifest


def load_ppf_manifest(file_path):
    """page/page_offset/page_word per level, for the streaming datChunkRef."""
    import json
    m = json.load(open(file_path))
    for t in m['textures']:
        name = t['texture']
        if name.endswith('.tex'):
            name = name[:-4]
        TEXTURE_LEVELS[name.lower()] = t['levels']
    return len(TEXTURE_LEVELS)


def load_textures(folder, identity_map=None):
    """Read the dumps BY IDENTITY, not by file name.

    447 of the 510 files in `la_tex_pck` have the wrong EXTERNAL name - the real
    name is in the pointer at TexturePS2+0x78 (`tNNN`). I "verified" that mapping
    by comparing each object with the `.tex` of the SAME FILE NAME, which is
    circular, and the samples matched by chance (almost everything is 64x64). The
    texture fork measured it and delivered `native_texture_map.tsv` with the
    correct association; use that.
    """
    global GAME_TEXTURES, TEXTURE_ORDER
    GAME_TEXTURES, TEXTURE_ORDER = {}, []
    if identity_map and os.path.isfile(identity_map):
        for ln in open(identity_map, encoding='utf-8', errors='replace'):
            c = ln.rstrip().split(chr(9))
            if len(c) < 3 or not c[0].endswith('.tex'):
                continue
            name = c[0][:-4].lower()          # REAL identity of the texture
            file_path = c[2]                    # file that holds this object
            if not os.path.isfile(file_path):
                continue
            GAME_TEXTURES[name] = open(file_path, 'rb').read()[0x80:]
            TEXTURE_ORDER.append(name)
            TEXTURE_POS[name] = len(TEXTURE_ORDER) - 1
        return len(TEXTURE_ORDER)
    for f in sorted(os.listdir(folder)):
        if not f.endswith('.tex.pck'):
            continue
        name = f[:-len('.tex.pck')]
        d = open(os.path.join(folder, f), 'rb').read()
        GAME_TEXTURES[name.lower()] = d[0x80:]
        TEXTURE_ORDER.append(name.lower())
        TEXTURE_POS[name.lower()] = len(TEXTURE_ORDER) - 1
    return len(TEXTURE_ORDER)


def put_textured_shader(e, cab16, tex_body, levels=None):
    """A shader whose texture is the one the game assembled.

    The retail shader block is 0xB0 bytes = a 16-byte header + a 160-byte texture
    object embedded at +0x10, and the shader's `+0x08` points to that +0x10. The
    rmcTexturePS2 is exactly 160 bytes, so [header][.tex.pck body] already has
    the right shape - and since the body stays CONTIGUOUS, a single delta is
    enough for all of its internal pointers.

    Only the pointers OF THE OBJECT are translated (the four datChunkRef at
    +0x48). Pixel blocks carry no pointers, and rewriting words inside them would
    repeat the mistake that already produced 19 meshes with absurd qw.
    """
    block = bytearray(cab16) + bytearray(tex_body[:160] if levels is not None
                                                 else tex_body)
    # TOKEN, NOT VTABLE, at +0x00 of the object. The .tex.pck was emitted with
    # --token 0, so it holds the runtime vtable (0x00627280); but inside a resource
    # .pck +0x00 has to be the serialised TOKEN (0x007A2320) - the resource ctor
    # rmcTexturePS2(datResource&) swaps token for vtable on load, and finding the
    # vtable already there it reads every following field (resolution, bpp,
    # datChunkRef) shifted and hangs (the 90 cpuTlbMiss / infinite loading).
    struct.pack_into('<I', block, 0x10, VT_TEXTURE_TOKEN)
    off = e.put(bytes(block), align=16)
    va_obj = e.va(off + 0x10)
    delta = va_obj - 0x06800000
    # THE OBJECT'S POINTER FIELDS, measured and not assumed: scanning 60 textures,
    # only three offsets point inside the body - +0x48 and +0x54 (two of the four
    # datChunkRef) and **+0x78**, which is ZERO in the donor and which I therefore
    # did not expect. Leaving +0x78 untranslated is what produced
    # `cpuTlbMiss pc:2ba874 addr:1f00c978` - the PC is right after
    # rmcTexturePS2::rmcTexturePS2(datResource&) 0x002BA830, i.e. the game reached
    # my texture and followed a pointer of the dump.
    if levels is not None:
        # STREAMING FORM. The object the RUNTIME constructor builds is RESIDENT
        # (datChunkRef with a pointer, pixels on the heap) and the resource ctor
        # 0x002BA830 **only relocates and expects streaming** - feeding it the
        # resident form gave 90 cpuTlbMiss at 0x2BA760..0x2BA874. So I keep the GS
        # descriptors the game computed and swap only the refs.
        #
        # The paged ref, decoded on atlanta (8793 of them) and confirmed on sd
        # (8048): `[ptr=0][offset in the page][page_word]`, with page_word = 0x3D80
        # in the high u16 and the PAGE in the low one. The high half is constant in
        # both cities. The `page_word` my .ppf compiler already wrote in the
        # manifest IS that whole field.
        for k in range(4):
            q = off + 0x10 + 0x48 + 12 * k
            if k < len(levels):
                n = levels[k]
                struct.pack_into('<III', e.d, q, 0, n['page_offset'], n['page_word'])
            else:
                struct.pack_into('<III', e.d, q, 0, 0, 0)
        # +0x78 was a runtime heap pointer; in the donor it is ZERO.
        struct.pack_into('<I', e.d, off + 0x10 + 0x78, 0)
    else:
        fields = [0x48 + 12 * k for k in range(4)] + [0x78]
        for c in fields:
            q = off + 0x10 + c
            v = struct.unpack_from('<I', e.d, q)[0]
            if 0x06800000 <= v < 0x06800000 + len(tex_body):
                struct.pack_into('<I', e.d, q, v + delta)
    struct.pack_into('<I', e.d, off + 0x08, va_obj)
    return off


def emit_shader_groups(e, donor_shader, n_groups, n_slots, slot=None,
                        material_index=0):
    """The city's shader groups, in the retail format.

    Atlanta has 18 groups of 1329 slots and the city's objects index GROUP 0 to
    17 and SLOT 0 to 1328. More importantly: the city is not the only one that
    indexes the slots - the props (`atlanta_midnight_clear_props.pck` and its 22
    siblings) carry a `material_index` from 0 to 91 and that index goes into the
    group's slot array. Emitting 1 group of 64 slots, index 80 of
    `a_prop_stlt_02x` read past the end of the array: null vtable and `jalr v0` to
    an invalid address.

    Each filled slot gets ITS OWN object: the Place loop (0x2AF9E8) runs once per
    slot and would add the delta N times to a shared object. Slots above
    SLOTS_PREENCHIDOS stay null, like the 81.5%% of null slots in retail.

    Returns (key of the descriptor array, donor slot of the shader).
    """
    # The shader donor HAS to be in the same token space as the main donor,
    # otherwise the file comes out with one city's structure and another's shader.
    # That is what happened with tokyo + atlanta's shader.
    # WARNING, NOT ERROR: the user tested it and the tokens are INERT - different
    # spaces resolve to the same vtable at run time. Keeping both in the same
    # space is still hygiene (retail never mixes them), but blocking it would
    # impose a theory his measurement already overturned.
    if donor_shader and donor_token(donor_shader) != TOKEN_SHIFT:
        print('  warning: --donor-shader is in another token space '
              '(0x%X vs 0x%X); harmless, but retail does not mix them'
              % (donor_token(donor_shader), TOKEN_SHIFT))
    # A DIFFERENT DONOR PER FILLED SLOT. A page chunk has a SINGLE OWNER.
    #
    # The shader's texture carries no pixels: it carries four 12-byte datChunkRef
    # at +0x48, and the pixels live in the sibling .ppf. datPageFile::PageIn
    # writes a back-pointer into the paged data pointing to THE datChunkRef that
    # resolved it, and evicting the page clears exactly ONE. Two refs to the same
    # (page, offset) means a dangling pointer as soon as the page is recycled.
    #
    # I emitted 128 copies of the SAME shader per group, i.e. 2304 texture objects
    # pointing to ONE chunk (offset 0x2E00, page 3). Measured on retail: atlanta
    # has 4105 shaders and 4105 DISTINCT textures, tokyo 5970 and 5970 - ZERO
    # sharing, neither of chunk nor of object. Their exporter does not deduplicate,
    # on purpose.
    #
    # So each filled slot takes a shader from a DIFFERENT donor slot, which brings
    # its own chunk. When donors run out, the slot stays NULL instead of repeating
    # - null is the majority form in retail (81.5% of slots) and creates no
    # duplicate owner. As a bonus the city stops having a single texture.
    safe = shader_slots(donor_shader)
    if slot is not None:
        safe = [slot] + [k for k in safe if k != slot]
    if not safe:
        raise SystemExit('no usable shader in %s'
                         % os.path.basename(donor_shader))
    n_full = min(n_slots, SLOTS_PREENCHIDOS, len(safe) // max(1, n_groups))
    n_full = max(n_full, 1)
    came_from = safe[0]
    next_ = 0
    shaders_tex = {}
    for g in range(n_groups):
        vas = []
        for k in range(n_slots):
            if TEXTURE_ORDER:
                # A SINGLE GROUP, and a DISTINCT object per slot. I shared the
                # same Basic pointer across the 18 groups to save RAM, and THAT was
                # the cause of the infinite loading: the serialised ctor 2AFF20 runs
                # once PER VISIT and RELOCATES shader+0x08 again each time (measured
                # by the texture fork: 068022A0 -> 010022A0 -> FB8022A0). My builds
                # had 9180 slots for 510 objects = 8670 extra visits; the city that
                # loads has ZERO.
                # Emitting 510 x 18 distinct objects does not work because each
                # chunk needs an exclusive owner, so the way out is fewer groups:
                # group 0 takes the slots, the others stay null.
                if g == 0 and k < len(TEXTURE_ORDER):
                    if True:   # one object per (group, slot); never reuse
                        bl, _came = shader_block(donor_shader,
                                                 safe[k % len(safe)])
                        hdr = bl
                        name_t = TEXTURE_ORDER[k]
                        if RUNTIME_BINDING:
                            # ATLANTA SLOT INTACT, for the mod to overwrite
                            # later. Zeroing the datChunkRef made the page-in hang
                            # on LOADING; keeping them (valid, pointing to atlanta's
                            # .ppf) makes the slot load exactly like the downtown
                            # atlanta that already loads. The chunk duplication
                            # (when k repeats a donor) only bites when the page is
                            # RECYCLED, and tex_bind overwrites shader+0x08 with the
                            # LA texture before that. Only the token is guaranteed
                            # (atlanta's block already has it).
                            o = e.put(bytes(bl))
                            shaders_tex[k] = e.va(o)
                        else:
                            shaders_tex[k] = e.va(put_textured_shader(
                                e, hdr[:16], GAME_TEXTURES[name_t],
                                TEXTURE_LEVELS.get(name_t)))
                    vas.append(shaders_tex[k])
                else:
                    vas.append(0)
            elif k < n_full and next_ < len(safe):
                block, _ = shader_block(donor_shader, safe[next_])
                next_ += 1
                o = e.put(block)
                # each shader with its texture inline at +0x10, like retail
                struct.pack_into('<I', e.d, o + 0x08, e.va(o + 0x10))
                vas.append(e.va(o))
            else:
                vas.append(0)
        if RUNTIME_BINDING and g == 0:
            # MARKER. The mod has to find the slots in RAM and has no way to know
            # the address; scanning for a signature is cheaper than discovering
            # the MapRoot at run time. It sits right BEFORE group 0's array, with
            # the count, so finding the signature means finding the slots.
            e.align_to(16)
            e.put(b'MC3TEXSLOTS' + bytes(1) + struct.pack('<I', len(TEXTURE_ORDER)))
        e.put(b''.join(struct.pack('<I', v) for v in vas), 'group%d_data' % g)
    print('  shaders: %d slot(s) per group, %d distinct donors used '
          '(one chunk per texture)' % (n_full, next_))
    # The geometry's material index HAS to land in a filled slot. With distinct
    # donors per slot, how many slots fill now depends on how many safe shaders
    # the donor has - and a thin donor leaves the index pointing to NULL without
    # warning. This test is the safety net.
    if TEXTURE_ORDER:
        n_full = len(TEXTURE_ORDER)
    if not TEXTURE_ORDER and material_index >= n_full:
        raise SystemExit(
            'material %d but only %d slot(s) per group get filled (the donor has %d '
            'safe shaders for %d groups). Use a --material smaller than %d, '
            'fewer groups, or a donor with more shaders.'
            % (material_index, n_full, len(safe), n_groups, n_full))

    # the descriptors HAVE to be contiguous: MapRoot+0x0C points to the array
    descs = e.put(bytes(0x10 * n_groups), 'groups')
    for g in range(n_groups):
        off = descs + 0x10 * g
        struct.pack_into('<IIHHI', e.d, off, tok(VT_ARRAY), 0, n_slots, n_slots, 0)
        e.point(off + 4, 'group%d_data' % g)
    return 'groups', came_from


def texture_with_pixels(e, donor):
    """COPY the donor's root texture WITH its pixel block, relocating the
    internal pointers. Returns the texture's key.

    This is the missing piece that hung the city built from scratch: the copied
    shader pointed to a texture without pixels (level[].data null, stamp
    0x80000000 = not placed in GS), and most of the city's textures take their
    pixels from the .aib stream, which does not exist in a generated city. In
    Bind, the game tried to place a texture with no pixel source and hung on the
    first frame - the same hang the cube only showed when the camera turned.

    The MapRoot's root (+0x1D4) is one of the 18 atlanta textures with INLINE
    pixels: 256x256 PSMT8, a MipBuffer of 0x10500 bytes (0x100 header + 0x10000
    pixels + 0x400 CLUT). Copying it with the pixels, Bind places the texture
    from the file itself.
    """
    p = mc2.Pck(donor)
    d = p.data
    tva = struct.unpack_from('<I', d, 0x80 + 0x1D4)[0]
    t = p.F(tva)
    mbva = struct.unpack_from('<I', d, t + 0x48)[0]   # level[0].data
    mb = p.F(mbva)
    size_ = struct.unpack_from('<I', d, mb + 0x0C)[0]    # 0x10500

    # 1) the pixel block (MipBuffer + pixels + CLUT), cleared of pointers
    buf = bytearray(d[mb:mb + size_])
    struct.pack_into('<I', buf, 0x00, 0)               # zero_00
    struct.pack_into('<I', buf, 0x04, 0)               # shared_node (runtime)
    struct.pack_into('<I', buf, 0x08, 0)               # owner: repointed below
    struct.pack_into('<I', buf, 0x10, 0)               # unk_10 (era CDCD)
    off_buf = e.put(bytes(buf), 'tex_pixels')

    # 2) the 0x90 texture object, with its pointers zeroed except level[0]
    obj = bytearray(d[t:t + 0x90])
    for k in range(4):
        struct.pack_into('<I', obj, 0x48 + 12 * k, 0)  # level[k].data
    struct.pack_into('<I', obj, 0x78, 0)               # name
    off_tex = e.put(bytes(obj), 'tex_inline')
    # level[0].data -> the pixel block; and the block's owner -> level[0]
    e.point(off_tex + 0x48, 'tex_pixels')
    struct.pack_into('<I', e.d, off_buf + 0x08, e.va(off_tex + 0x48))
    return 'tex_inline'


def shader_block(donor, slot=None):
    """COPIED: a TYPICAL shader, with the sub-object at +0x10.

    I got this wrong twice in a row and the second time was worse than the first.
    Measured over atlanta's ~1900 type-0 shaders, the distance between the shader
    and the object its `+0x08` points to is:

        +0x10 -> 1889 times     +0xC -> 11     +0xAC -> 3

    I had PICKED the 3-in-1900 case, because that is where I tripped first, and
    built the model "shader = 0xB0 block with the sub-object at the end" on top of
    it. The normal case is the sub-object right after the 16-byte header. With the
    pointer at +0xAC the sub-object's `+0x04` byte falls outside the copied block,
    and `0x2BA718` dispatches on garbage -> loc_2BA7E0, infinite loop.

    The stride between consecutive shaders is 0xB0, so the block is 0xB0 bytes
    with the sub-object embedded at +0x10.
    """
    p = mc2.Pck(donor)
    d = p.data
    a18 = p.F(struct.unpack_from('<I', d, 0x80 + 0x0C)[0])
    g0 = p.F(struct.unpack_from('<I', d, a18 + 4)[0])
    n = struct.unpack_from('<H', d, a18 + 8)[0]
    # With `slot`, only that one; without it, the first that fits. The choice
    # matters: what decides whether the ITOP+2 stream is read is the MATERIAL's
    # flags word, not the block, so enabling normals with a shader that does not
    # read them corrupts instead of lighting - that is how a city hung on frame 1.
    range_ = range(slot, slot + 1) if slot is not None else range(n)
    for k in range_:
        if k >= n:
            raise SystemExit('slot %d does not exist: the donor has %d' % (k, n))
        v = struct.unpack_from('<I', d, g0 + 4 * k)[0]
        if not (v and p.inside(p.F(v))):
            continue
        o = p.F(v)
        # FIND IT BY STRUCTURE, NOT BY THE VTABLE VALUE. Comparing against
        # VT_SHADER made tokyo be rejected, and I recorded that in the log as
        # "tokyo has no shader of this type" - that was wrong. The texture fork
        # measured it and I confirmed: tokyo has the SAME class, with the whole
        # token space shifted by +0x488 (007A1EF8 -> 007A2380, 007B2318 ->
        # 007B27A0, 007A1FC8 -> 007A2450). Atlanta, sd and detroit use the lower
        # tokens. Testing the shape covers all four without a table of values.
        if struct.unpack_from('<I', d, o + 4)[0] & 0x7F:      # has to be type 0
            continue
        if struct.unpack_from('<I', d, o + 8)[0] != p.V(o + 0x10):
            continue
        if d[o + 0x14] not in (0, 1, 2, 3):                   # byte +0x04 do sub
            continue
        # The SAME strict test as slots_de_shader: the block has to survive
        # `limpa` whole. Without this the default went back to slot 30, which
        # loses +0x0C. See the note there.
        raw_ = bytes(d[o:o + 0xB0])
        clean = clean_(raw_, donor, keep=(0x08,))[0]
        lost_n = [i for i in range(0, 0xB0, 4)
                    if i != 0x08 and raw_[i:i + 4] != clean[i:i + 4]]
        if lost_n:
            if slot is not None:
                raise SystemExit(
                    'slot %d loses %d field(s) in `limpa` (+%s) - use '
                    '--shader-list'
                    % (slot, len(lost_n), ', +'.join('0x%02X' % i for i in lost_n)))
            continue
        return clean, k
    raise SystemExit(
        'slot %d of %s has no typical shader (sub at +0x10); use '
        '--shader-list' % (slot, os.path.basename(donor)) if slot is not None
        else 'no typical shader (sub at +0x10) in %s'
        % os.path.basename(donor))


def empty_array(e, map_key):
    """An array object with count ZERO, in place of a null pointer.

    Found in the savestate of the first test: `Place` fixes the pointers in the
    order the class declares them, and the MapRoot stopped right after the shader
    group - the next field, `+0x10`, was the null I had left. On the PS2 reading
    address 0 does not fault: it returns the start of RAM, and `Place` walks off
    into a graph of garbage. Infinite loading, no error.

    A zero-count array is the legitimate EMPTY form: the pointer is valid, the
    loop runs zero times.
    """
    data = e.put(bytes(16), map_key + '_data')
    obj = e.put(struct.pack('<IIHHI', tok(VT_ARRAY), 0, 0, 0, 0), map_key)
    e.point(obj + 4, map_key + '_data')
    return obj


def empty_hood(e):
    """A zeroed block for HoodEntry+0x08/+0x10 of a hood with no content.

    Measured: NO retail hood has a zero count - the four cities fill six or seven
    - so there is no example of an "empty hood" to copy. What exists is the
    invariant: `unique_array` and `instance_array` are never null, in the 25 hood
    entries of the four cities. Since the accompanying count is zero, the loop
    reads nothing; the pointer only has to be a legitimate address. The lesson of
    `empty_array` applies: on the PS2 reading address 0 does not fault, it
    returns the start of RAM and Place walks off into garbage.
    """
    if 'empty_hood' not in e.marks:
        e.put(bytes(0x60), 'empty_hood')
    return 'empty_hood'


def clean_(blob, donor, keep=()):
    """Zero EVERY pointer inside a copied blob, except the ones I re-point.

    Copying a donor structure brings its pointers along, and they point into the
    8 MB source space. In a 26 KB file that is a dangling pointer, and I had been
    handling only one or two per structure: `+0x194` has 23 and I handled 2; the
    `+0x1D4` chains have 1 to 9 and I handled none; the shader block has TWO
    (`+0x08` and `+0x0C`) and I only fixed the first - copied 64 times, that made
    64 identical dangling pointers.

    Zeroing is safe where the consumer tests before using, which is the case for
    these fields; keeping the donor's value never is.
    """
    p = mc2.Pck(donor)
    b = bytearray(blob)
    n = 0
    for o in range(0, len(b) - 3, 4):
        if o in keep:
            continue
        v = struct.unpack_from('<I', b, o)[0]
        if BASE_VA <= v < BASE_VA + 0x1000000 and p.inside(p.F(v)):
            struct.pack_into('<I', b, o, 0)
            n += 1
    return bytes(b), n


def maproot_pointer_fields():
    """Every MapRoot offset that holds a POINTER, according to `mcCity::mcCity`.

    Copying the donor's MapRoot brings its pointers along, which point inside the
    source file and are not valid here. All of them are guarded by `if (ptr)`
    before being walked, so zeroing is safe; keeping the donor's value is not.
    """
    return ([0x0C, 0x10, 0x18, 0x1C, 0x2C, 0x30, 0x190, 0x194, 0x198,
             0x19C, 0x1CC, 0x228, 0x1D4] + [0x1D8 + 4 * k for k in range(20)])


def maproot_base(donor):
    """COPIED: the donor's whole MapRoot (0x2290), with the pointers zeroed.

    What remains are the city's SCALARS - extents, LOD distances, fog,
    saturation - which I had been zeroing without knowing. That is ~0x250 bytes
    of parameters. The donor's two palettes come along and are overwritten by
    `inline_palettes`; only the offsets from `maproot_pointer_fields()` are
    zeroed, and they are all below 0x290, so the palette area is not touched here.
    """
    d = mc2.Pck(donor).data
    m = bytearray(d[0x80:0x80 + MAPROOT_SIZE])
    for off in maproot_pointer_fields():
        struct.pack_into('<I', m, off, 0)
    struct.pack_into('<I', m, 0x04, 0)       # "already placed" flag
    return m


def block_1c(e, donor, map_key, components=(), instances=(), scratch=64,
             cells_per_unique=None, cells_per_inst=None):
    """The city's culling index, at `MapRoot+0x1C`.

        +0x00 component count             +0x04 instance count
        +0x08 -> UniqueComponent array    (unique_array)
        +0x0C -> Instance array           (instance_array)
        +0x10 -> CellRecord[cell_count]   THE CELL GRID
        +0x14 -> one pointer per cell     (the `extra` list, see mc3_coverage)
        +0x18.. scalars (copied)

    CORRECTION (what hung the city built from scratch): `+0x10` is NOT scratch.
    It is the source grid the game READS - each 12-byte CellRecord is
    `{s16 unique_count, s16 instance_count, u32 unique_list, u32 instance_list}`,
    and `unique_list` points to a `u16[unique_count]` of INDICES into
    unique_array. In atlanta cell 51 is `{1, 0, ->[237], 0}` and
    unique_array[237] is the component that lives in that cell.

    Leaving the whole grid zeroed, the cube entered unique_array but no cell
    listed it; turning the camera to the cube's cell the game went into a loop
    writing to that cell (counter going 0x123 -> 0x242 between two frames) and
    froze. With the grid filled in it reads valid data.

    `cells_per_unique` maps unique-index -> list of cell indices its tile box covers.
    Every listed cell gets a CellRecord pointing to that unique.
    """
    # THE OBJECT IS 0x7130 BYTES, NOT 0x60. Measured on atlanta and sd: from the
    # start of the CellIndex to the unique_array cookie there are 28976 bytes, and
    # `+0x64`/`+0x84` - the visible lists the runtime fills - come ZEROED in the
    # file. Copying only 0x60 let `+0x64` land in the next allocation, and the game
    # read a POINTER where it expects an index limited to 0x190. That is what Fork
    # City MODS 2's packet guard caught as `bad packet lists` in my file.
    p = mc2.Pck(donor)
    d = p.data
    o = p.F(struct.unpack_from('<I', d, 0x80 + 0x1C)[0])
    ua = p.F(struct.unpack_from('<I', d, o + 8)[0])
    # The unique_array starts IMMEDIATELY after the CellIndex; there is no 0x10
    # cookie between the two. Subtracting 0x10 truncated the 0x7130 object to
    # 0x7120 and made mcPVS::Update read the first four unique_array pointers as
    # the PVS's own final fields. In particular, +0x7120 is the adaptive distance
    # increment (10.0 in retail).
    obj_size = ua - o if ua > o else CELLINDEX_SIZE
    if not (0x60 <= obj_size <= 0x20000):
        obj_size = CELLINDEX_SIZE
    blob = bytearray(clean_(bytes(d[o:o + 0x60]), donor)[0])
    blob += bytes(obj_size - 0x60)          # runtime lists are born zeroed
    # The 0x7104..0x7124 footer is not scratch. It holds, respectively, the two
    # current distances, time limits, counters and adaptive steps. All four
    # retail cities use 800/800, 34/40, 0/0, 40/10, 0. Keeping the donor's values
    # preserves the contract and still lets SetIntendedFrameRate replace only the
    # two limits, as the original game does.
    if obj_size >= 0x7128:
        blob[0x7104:0x7128] = d[o + 0x7104:o + 0x7128]
    struct.pack_into('<II', blob, 0, len(components), len(instances))
    obj = e.put(bytes(blob), map_key)
    e.put(b''.join(struct.pack('<I', v) for v in components) or bytes(4),
          map_key + '_comps')
    e.put(b''.join(struct.pack('<I', v) for v in instances) or bytes(4),
          map_key + '_insts')

    # invert the maps: cell -> [indices]. A CellRecord has BOTH sides,
    # unique_list and instance_list, each a u16[] of indices into its array.
    def per_cell_of(mapping):
        outside = {}
        for idx, cell_list in (mapping or {}).items():
            for c in cell_list:
                if 0 <= c < scratch:
                    outside.setdefault(c, []).append(idx)
        return outside

    pc_u = per_cell_of(cells_per_unique)
    pc_i = per_cell_of(cells_per_inst)

    # the u16 lists, deduplicated by content (many cells repeat the list)
    lists = {}

    def list_of(idxs, marks):
        ch = tuple(sorted(idxs))
        if ch not in lists:
            name = '%s_%s_%d' % (map_key, marks, len(lists))
            e.put(b''.join(struct.pack('<H', u) for u in ch), name)
            lists[ch] = name
        return lists[ch]

    cells = bytearray(12 * scratch)
    obj_cells = e.put(bytes(cells), map_key + '_s1')
    for c in set(pc_u) | set(pc_i):
        unis = pc_u.get(c, [])
        inss = pc_i.get(c, [])
        struct.pack_into('<hh', e.d, obj_cells + 12 * c, len(unis), len(inss))
        if unis:
            e.point(obj_cells + 12 * c + 4, list_of(unis, 'ul'))
        if inss:
            e.point(obj_cells + 12 * c + 8, list_of(inss, 'il'))

    e.put(bytes(4 * scratch), map_key + '_s2')
    for k, target in enumerate(('_comps', '_insts', '_s1', '_s2')):
        e.point(obj + 8 + 4 * k, map_key + target)
    return obj


def hood_names_(donor):
    """The donor's hood names, in order (`bh, dt, fwy, lf, mt, ug, we`)."""
    p = mc2.Pck(donor)
    d = p.data
    n = struct.unpack_from('<I', d, 0x80 + 0x14)[0]
    hb = p.F(struct.unpack_from('<I', d, 0x80 + 0x18)[0])
    out = []
    for h in range(n):
        rec = p.F(struct.unpack_from('<I', d, hb + 20 * h)[0])
        out.append(bytes(d[rec:rec + 4]).split(bytes(1))[0].decode('latin1'))
    return out


def hood_record(e, donor, name, map_key, which=0, last=False):
    """COPIED and relinked: a hood's 0xB0-byte record.

    Atlanta's 7 records are contiguous with a 0xB0 stride and each one is
    `[name 4][vtable chain 0x7A2320, 0xA0][head 12]`; the head (`+0xA4`) has
    `+0x08` pointing to the chain of the NEXT record, which is why they are glued
    together. Here the record comes whole from the donor, the name is rewritten,
    and the head pointer is aimed at a chain copy of OUR OWN.

    **THE LAST RECORD IS THE TERMINATOR.** In atlanta `we` (index 6) has
    `+0xA4`, `+0xA8` and `+0xAC` all ZERO, and the other six have a vtable at
    +0xA4 and a pointer at +0xAC. Recycling the donor's records for a city with
    more hoods, the terminator lands in the middle and the last one keeps a valid
    pointer - the chain never ends. `last=True` zeroes the whole head, whatever
    that costs the donor it came from.
    """
    p = mc2.Pck(donor)
    d = p.data
    base = p.F(struct.unpack_from('<I', d, 0x80 + 0x18)[0])
    rec = p.F(struct.unpack_from('<I', d, base + 20 * which)[0])
    blob = bytearray(clean_(bytes(d[rec:rec + 0xB0]), donor, keep=(0xAC,))[0])
    nb = name.encode('latin1')[:3]
    blob[0:4] = nb.ljust(4, bytes(1))
    chain = bytes(d[rec + 0xB0 + 4:rec + 0xB0 + 4 + 0xA0])
    if last:
        struct.pack_into('<3I', blob, 0xA4, 0, 0, 0)
    off = e.put(bytes(blob), map_key)
    e.put(chain, map_key + '_chain')
    if not last:
        e.point(off + 0xAC, map_key + '_chain')
    return off


def hood_donors(donor, n):
    """Which donor record to use for each of the `n` hoods.

    The donor's terminator (the only one with `+0xA4` zeroed) is reserved for the
    LAST one; the rest cycle over the non-terminators, so no terminator lands in
    the middle of the chain.
    """
    p = mc2.Pck(donor)
    d = p.data
    nd = struct.unpack_from('<I', d, 0x80 + 0x14)[0]
    base = p.F(struct.unpack_from('<I', d, 0x80 + 0x18)[0])
    ends = []
    regular = []
    for h in range(nd):
        rec = p.F(struct.unpack_from('<I', d, base + 20 * h)[0])
        if struct.unpack_from('<I', d, rec + 0xA4)[0]:
            regular.append(h)
        else:
            ends.append(h)
    if not regular:
        regular = list(range(nd))
    # DO NOT force a terminator on the last one: measured, atlanta has a record
    # with a zeroed head at the end but tokyo and detroit have NONE and they load.
    # What matters is not letting atlanta's odd record land in the MIDDLE.
    return [(regular[i % len(regular)], False) for i in range(n)]


# The three structures at MapRoot+0x190/+0x194/+0x198 are CULLABLE CLASSES, and
# the one at +0x228 is rmcTextureProxyPS2 (thumbnails), NOT visibility. Field +0x00
# of +0x194/+0x198 is NOT a count: it is the class's pass mask (0x4E in retail =
# 2|4|8|64). Zeroing it removes both classes from the scheduler and leaves the
# city invisible even with the cell list filled in. +0x1C counts objects: fill it
# below with comp/inst+1. TextureProxy is only used by Bind when TEX1 has a
# non-zero ID in bits 21..31. The current LA dumps have ID zero: do not fill
# +0x228 with the donor's tables.
#
# Each tuple is (offset in the MapRoot, who walks it, COUNT offsets to zero,
# POINTER offsets to re-point).
# (offset, who walks it, counts to zero, REAL SIZE)
# The size matters: +0x194 sits at 0xFC6B0 and +0x198 at 0xFC6F0, **0x40 apart**.
# Copying 0x100 dragged the whole neighbouring structure into mine - which is why
# +0x194 showed up with 23 pointers when the object only has 2.
EXTRA_STRUCTURES = [
    (0x190, 'sub_25C998', (0x30,),      0x100),
    (0x194, 'sub_24C528', (0x1C,),      0x40),
    (0x198, 'sub_2571B0', (0x1C,),      0x40),
    (0x228, 'sub_2BA6B0', (0x00,),      0x100),
]


def extra_structures(e, donor, head_order3=None, head_order2=None,
                     n_comp=0, n_inst=0):
    """Create the four structures I used to leave NULL, with a valid initial state.

    MIND what "empty" means in each field - there is not a single rule:

    * `MapRoot+0x10` is dereferenced WITHOUT a guard, so there the legitimate
      empty value is an array object with count zero. Null makes the PS2 read
      address 0.
    * `+0x04`/`+0x08` of these classes are the HEAD OF A LINKED LIST:
      `CullableTypeFixup` (0x24AE58) does `while (v3) { ctor(v3); v3 =
      *(v3+12); }` - it walks to NULL, with no count. Pointing it at a block of
      zeros makes the constructor run ONCE over zeros; **here the correct empty
      value is NULL**.

    That confusion is what produced the `Jump to unaligned address (PC:
    0xcdcdcdcd)`: Place completed 11 of 11 pointers and execution ended up in
    padding, coming out of a constructor run over an empty block.

    In other words: **reading the consumer's code is what tells you whether empty
    is NULL or an object** - generalising either form breaks the other.
    """
    p = mc2.Pck(donor)
    d = p.data
    made = []
    for off, who, counts, size_ in EXTRA_STRUCTURES:
        va = struct.unpack_from('<I', d, 0x80 + off)[0]
        if not va or not p.inside(p.F(va)):
            continue
        o = p.F(va)
        blob = bytearray(clean_(bytes(d[o:o + size_]), donor)[0])
        for c in counts:
            struct.pack_into('<I', blob, c, 0)
        # +0x1C OF +0x194/+0x198 IS NOT "a runtime count that starts at zero",
        # as I had noted: it is the COUNT OF OBJECTS the cullable class manages,
        # and zeroing it makes the scheduler see zero objects and DRAW NOTHING -
        # with all the geometry in the file, in the right place. Measured on
        # three retail cities:
        #     atlanta 651 comp / 3608 inst -> +194=4260  +198=3609
        #     sd                5008 inst -> +198=5009
        #     detroit           5156 inst -> +198=5157
        # i.e. **+0x198 = inst + 1** and **+0x194 = comp + inst + 1** (the
        # difference between the two is exactly the number of components: 651).
        if off == 0x194 and (n_comp or n_inst):
            struct.pack_into('<I', blob, 0x1C, n_comp + n_inst + 1)
        elif off == 0x198 and n_inst:
            struct.pack_into('<I', blob, 0x1C, n_inst + 1)
        map_key = 'extra_%X' % off
        base = e.put(bytes(blob), map_key)
        # THE ORDER-3 CLASS receives our list of types.
        #
        # Measured: the linked list of `MapRoot+0x194` has **651 nodes, and the
        # 651 are the objects the component points to at +0x0C**. So what I called
        # "ComponentData" is a `mcCityModelType`, and `CullableTypeFixup(3, head,
        # res)` (0x24AE58) walks the list calling `mcCityModelType::mcCityModelType`
        # on each one - which stamps the vtable at +0x28 and runs
        # `mcCityModelInfo::mcCityModelInfo` over +0x2C (position, radius, LOD
        # slots).
        #
        # Writing the vtable by hand, as I did, only imitated the constructor's
        # LAST effect. Without being in the list, the rest of it never runs.
        order = struct.unpack_from('<I', blob, 0x20)[0]
        # +0x194 is order 3 (comps), +0x198 is order 2 (instances). Each one gets
        # the head of its own chain of types.
        head = head_order3 if order == 3 else (
            head_order2 if order == 2 else None)
        if head:
            e.point(base + 0x04, head)
            e.point(base + 0x08, head)
        e.point(0x80 + off, map_key)
        made.append(off)
    return made


def chains_1d4(e, donor, tex_donor=None):
    """COPIED: the 21 slots of MapRoot+0x1D4/+0x1D8 that retail fills.

    Each one points to an object of the `0x7A2320` chain - the same class as the
    shader's sub-object - and is processed by `0x2BA718` with count 1,
    dispatching on byte `+0x04` (0..3). In atlanta only 5 of the 21 are filled,
    with bytes 0 and 2.

    They are guarded by `if (ptr)`, so null does NOT hang Place. I copy them
    because the from-scratch file dies after Place and every field retail has and
    mine does not is one variable less.
    """
    p = mc2.Pck(donor)
    d = p.data
    made_n = 0
    for k in range(21):
        off = 0x1D4 + 4 * k
        va = struct.unpack_from('<I', d, 0x80 + off)[0]
        if not va or not p.inside(p.F(va)):
            continue
        o = p.F(va)
        if d[o + 4] not in (0, 1, 2, 3):      # what 0x2BA718 knows how to handle
            continue
        # SLOT +0x1D4 GETS THE TEXTURE WITH PIXELS, the others go in clean.
        #
        # Measured: in atlanta the object at +0x1D4 has level[0] pointing to a
        # MipBuffer of 0x10500 bytes (256x256 PSMT8: 0x100 header, 0x10000 pixels,
        # 0x400 CLUT). In my file that same field came out NULL, because `limpa`
        # zeroes every pointer of the copied block - and the generated city loaded
        # without A SINGLE texture.
        #
        # texture_with_pixels() was written exactly for this and nobody ever called
        # it. Now it is, and only for this slot: the other 20 are objects of the
        # same class but without inline pixels in retail, so copying them clean is
        # still right.
        if off == 0x1D4:
            # Measured: this slot's MipBuffer varies A LOT per city - atlanta has
            # 0x10500 (256x256 PSMT8) and tokyo has 0x160. Since the city hosted on
            # tokyo wants its 7688 instance slots but a real texture, the texture
            # donor is separable.
            e.point(0x80 + off, texture_with_pixels(e, tex_donor or donor))
            made_n += 1
            continue
        map_key = 'cadeia_%X' % off
        e.put(clean_(bytes(d[o:o + 0xA0]), donor)[0], map_key)
        e.point(0x80 + off, map_key)
        made_n += 1
    return made_n


def maproot_scalars(donor):
    """COPIED: the MapRoot fields whose meaning is not settled yet.

    These are +0x20..+0x2B (flags and a float) and the whole +0x40..+0x7F block,
    which starts with the vtable 0x7A01E8 and continues with floats that look
    like extents/fog. Copying is more honest than guessing zero: zero in a scale
    field blanks the whole city without an error.
    """
    d = mc2.Pck(donor).data
    return bytes(d[0x80 + 0x20:0x80 + 0x2C]), bytes(d[0x80 + 0x40:0x80 + 0x80])


# The black->white ramp was great for READING the index on screen, and terrible
# as a default: I do not generate the one-byte-per-vertex vector (rmcModelGeom+0x14
# comes out NULL in my 543 meshes, against 74%% filled in retail), so the
# effective index is 0 - the first step of the ramp, BLACK. The city drew (531
# and 580 draw calls in the captures) and nobody saw anything: black geometry, at
# midnight, behind fog. With the white palette the geometry shows even without
# the per-vertex vector. Set PALETTE_RAMP = True to go back to the ramp when
# diagnosing the index.
PALETTE_RAMP = False

# The cardinality of `rmcModelGeom+0x14` is not the number of LODs. The component
# path calls DrawCpv with index zero; the instance path passes `Instance+0x1C`
# directly, which this builder numbers 0 to N-1 inside each type's chain. In the
# retail PCKs, a type's main CPV mesh declares exactly N slots. So components need
# one slot and instance types need as many slots as there are records of that type.
CPV_SLOTS_PER_COMPONENT = 1


# MC2's palette, when there is one. Filled in by palette_from_mc2().
MC2_PALETTE = None


def palette_from_mc2(cpvs_path):
    """Read the CPP0 of an MC2 PS2 <hour>_cpvs.rsc and return 256 float RGB.

    MC2 and MC3 use THE SAME DESIGN for per-vertex colour - a 1-byte index per
    vertex into a 256-entry palette - so the palette ports one to one, without
    flattening to RGB. A finding of Fork MC2 PS2.

    PS2 colour convention: alpha is 0x80 in all 256 entries and the components
    are 0..127, i.e. 128 is 1.0. Measured on dusk_clear and midnight_clear: alpha
    128 in 256 of 256, maximum luminance 126.2. So the conversion to the float MC3
    expects is dividing by 128, not by 255 - dividing by 255 would leave the city
    at half brightness.
    """
    import importlib.util as _il
    sp = _il.spec_from_file_location('mc2_rsc', os.path.join(_HERE, 'mc2_rsc.py'))
    m = _il.module_from_spec(sp)
    sys.modules['mc2_rsc'] = m
    sp.loader.exec_module(m)
    r = m.Rsc(cpvs_path)
    for off, fcc, size in r.entries:
        if fcc == 'CPP0':
            buf = r.data[off:off + size]
            return [tuple(c / 128.0 for c in
                          struct.unpack_from('<3B', buf, k * 4))
                    for k in range(256)]
    raise SystemExit('%s has no CPP0' % os.path.basename(cpvs_path))


# The palette ALPHA is not decoration. In the four retail cities (atlanta,
# detroit, sd, tokyo; every condition checked) entries 0..7 have alpha 0,
# entries 8..255 alpha 1.0, and entry 255 is always opaque white (1,1,1,1) -
# the entry rmcCpvPalette::Lookup 0x2ACE68 reserves. The city leaves its palette
# resident on VU1 (mcCityModelClass::SetRenderStates downloads MCCITY+0x2C), and
# whoever draws later with rmcSetCpvMode(1, 0) without downloading its own
# inherits it: the checkpoint smoke column (mcCheckpoint::Draw, cp_flare*
# shaders, texcombine modulate + blendset normal) gets vertex alpha 0 and
# vanishes. With alpha 0 in all 256 entries that is exactly what the MC2 maps did.
PALETTE_ZERO_ALPHA = 8      # entries 0..7: alpha 0, as retail


def grey_palette():
    """256 Vector4 entries - the table the per-vertex byte indexes."""
    out = bytearray()
    for i in range(256):
        if MC2_PALETTE:
            r, g, b = MC2_PALETTE[i]
        else:
            c = (i / 255.0) if PALETTE_RAMP else 1.0
            r = g = b = c
        if i == 255:
            r = g = b = 1.0
        out += struct.pack('<4f', r, g, b, 0.0 if i < PALETTE_ZERO_ALPHA else 1.0)
    return bytes(out)


def emit_per_vertex_chain(e, groups, map_key, slot_count=1, value=0):
    """Emit an independent chain per ``rmcModelGeom+0x14`` index.

    The format is per slot and per group: each group carries the number of
    blocks, the vertex count of each block and a separate one-byte-per-vertex
    payload. No substructure is shared between slots, because Place walks and
    fixes each root independently, as in the retail PCKs.
    """
    if not groups:
        raise ValueError('per-vertex chain without a group')
    if not 1 <= slot_count <= 0xFFFF:
        raise ValueError('CPV slot count outside u16: %d' % slot_count)

    off_slots = e.put(b'\x00' * (4 * slot_count), map_key, align=16)
    for slot in range(slot_count):
        tab_key = '%s_slot%d' % (map_key, slot)
        off_tab = e.put(b'\x00' * (8 * len(groups)), tab_key, align=16)
        e.point(off_slots + 4 * slot, tab_key)

        for gi, group in enumerate(groups):
            if not group:
                raise ValueError('per-vertex chain with an empty group')
            vertices = [len(geometry_block[0]) for geometry_block in group]
            hdr = struct.pack('<I', len(group)) + b''.join(
                struct.pack('<I', n) for n in vertices)
            key_a = '%s_s%d_g%d_a' % (map_key, slot, gi)
            e.put(hdr, key_a, align=16)
            e.point(off_tab + 8 * gi, key_a)

            payloads = []
            for bi, n in enumerate(vertices):
                payload_key = '%s_s%d_g%d_b%d_payload' % (
                    map_key, slot, gi, bi)
                e.put(bytes([value & 0xFF]) * n, payload_key, align=16)
                payloads.append(payload_key)

            key_b = '%s_s%d_g%d_b' % (map_key, slot, gi)
            off_b = e.put(b'\x00' * (4 * len(payloads)), key_b, align=16)
            e.point(off_tab + 8 * gi + 4, key_b)
            for bi, payload_key in enumerate(payloads):
                e.point(off_b + 4 * bi, payload_key)
    return map_key


def append_per_vertex_chain(p, groups, slot_count=1, value=0):
    """Immediate version, also without sharing substructures between slots."""
    if not 1 <= slot_count <= 0xFFFF:
        raise ValueError('CPV slot count outside u16: %d' % slot_count)
    off_slots = p.append(b'\x00' * (4 * slot_count), 16)
    for slot in range(slot_count):
        off_tab = p.append(b'\x00' * (8 * len(groups)), 16)
        struct.pack_into('<I', p.data, off_slots + 4 * slot, p.V(off_tab))
        for gi, group in enumerate(groups):
            vertices = [len(geometry_block[0]) for geometry_block in group]
            hdr = struct.pack('<I', len(group)) + b''.join(
                struct.pack('<I', n) for n in vertices)
            off_a = p.append(hdr, 16)
            payloads = [p.append(bytes([value & 0xFF]) * n, 16)
                        for n in vertices]
            off_b = p.append(b''.join(struct.pack('<I', p.V(o))
                                      for o in payloads), 16)
            struct.pack_into('<II', p.data, off_tab + 8 * gi,
                             p.V(off_a), p.V(off_b))
    return off_slots


def donor_grid(donor):
    """(grid_w, grid_h, origin_x, origin_z) of the donor, in CELLS."""
    d = mc2.Pck(donor).data
    return struct.unpack_from('<4i', d, 0x80 + 0x26C)


def donor_instances(donor):
    """The donor CellIndex's `instance_count` - the instance budget.

    Measured on retail: atlanta 3608, sd 5008, detroit 5156, tokyo 7688. MC2's LA
    brings 8511, more than the LARGEST retail city, and the host's support files
    (props, traffic, peds) are sized for the city they accompany. With 8511 on
    atlanta, the requested prop index reached 4701 in an array whose valid
    entries end around 3971, and Fork City MODS 2's guard logged one failure per
    frame.
    """
    p = mc2.Pck(donor)
    d = p.data
    ci = p.F(struct.unpack_from('<I', d, 0x80 + 0x1C)[0])
    return struct.unpack_from('<I', d, ci + 4)[0]


def grid_from_extents(mn, mx):
    """(grid_w, grid_h, origin_x, origin_z) from the extents, in CELLS.

    The retail INVARIANT, checked on atlanta/sd/tokyo/detroit:
        origin_x = floor(min_x / 40)   grid_w = floor(max_x/40) - origin_x + 1
        origin_z = floor(min_z / 40)   grid_h = floor(max_z/40) - origin_z + 1
        cell_count = grid_w * grid_h
    Copying the donor's grid with different extents shifts the culling: LA asks
    for 91x82 and origin (-50,-43), atlanta gives 64x62 and origin (-50,-43) as
    well - the 62 is atlanta's, which is why the swap goes unnoticed. Measured on
    the file: losangeles_la_v5 comes out (91,82,-50,-43), which is exactly the sum
    from the extents of MC2's losangeles.lvl (-2000..1600 / -1720..1520).
    """
    ox = int(math.floor(mn[0] / 40.0)); oz = int(math.floor(mn[2] / 40.0))
    gw = int(math.floor(mx[0] / 40.0)) - ox + 1
    gh = int(math.floor(mx[2] / 40.0)) - oz + 1
    return gw, gh, ox, oz


def cell_box(grid, cx, cz, half_x, half_z):
    """The tile box at +0x14, RELATIVE to the grid origin.

    The box is from the AABB, not the sphere. I tested both against atlanta: with
    the sphere radius only 91 of the 651 components match, and the ones that miss
    miss towards MORE (read 20..26 against 18..28 computed) - so retail uses the
    real, tighter box.

    `grade` = (gw, gh, ox, oz). I used to read the DONOR's grid here, which
    shifted everything when the new city's extents were different.
    """
    gw, gh, ox, oz = grid
    def range_(c, half, origin, limit):
        a = int(math.floor((c - half) / 40.0)) - origin
        b = int(math.floor((c + half) / 40.0)) - origin
        a = max(0, min(limit - 1, a))
        b = max(0, min(limit - 1, b))
        return min(a, b), max(a, b)
    x0, x1 = range_(cx, half_x, ox, gw)
    z0, z1 = range_(cz, half_z, oz, gh)
    return x0, x1, z0, z1


def inline_palettes(e, r):
    """Both palettes written INSIDE the MapRoot, as in retail."""
    pal = grey_palette()
    e.d[r + OFF_LIVE_PALETTE:r + OFF_LIVE_PALETTE + len(pal)] = pal
    e.d[r + SRC_PALETTE_OFF:r + SRC_PALETTE_OFF + len(pal)] = pal
    e.label('paletteA', r + OFF_LIVE_PALETTE)
    e.label('paletteB', r + SRC_PALETTE_OFF)


def build(out_path, donor, blocks, hood_name='zz', position=(0.0, 0.0, 0.0),
             n_slots=1329, n_groups=18, material_index=1, probe=False,
             hoods=None, slot_shader=None, texture_donor=None):
    e = Writer()

    # ---------------------------------------------------------------- shader
    # ONE COPY PER SLOT. The 0x2AF9E8 loop has no "already placed" guard and runs
    # the handler once per slot; the same object in N slots gets N additions of
    # the delta to its internal pointers. In retail each object appears in exactly
    # one slot. See mc3_place_sim.py.
    _g, came_from = emit_shader_groups(e, donor, n_groups, n_slots,
                                      slot_shader, material_index)

    # ---------------------------------------------------------------- palettes


    # ---------------------------------------------------------------- geometry
    # The scale does not go into the .pck: quantisation travels INSIDE the VIF
    # packet (the float that opens the block). Here it serves make_block and the
    # report, so it is always computed, even with packets coming from the game.
    scale = mc2.pick_scale(blocks)
    if GAME_PACKETS:
        # Ready VIF packets, from a mesh.pck assembled by THE GAME ITSELF. Swaps
        # my stripping, parity, ADC and block splitting for its own.
        items = bytearray()
        for payload, qw, nv in GAME_PACKETS:
            items += struct.pack('<IHH', e.va(e.put(flip_winding_payload(payload) if FLIP_WINDING else payload, align=16)), qw, nv)
        n_blocks = len(GAME_PACKETS)
    else:
        packets = [mc2.make_block(pos, uv, nr, scale, i % 2 == 1)
                   for i, (pos, uv, nr, _o) in enumerate(blocks)]
        offs = [e.put(pk, align=16) for pk in packets]
        items = bytearray()
        for off, (pos, _u, _n, _o) in zip(offs, blocks):
            qw = (len(packets[offs.index(off)]) + 15) // 16
            items += struct.pack('<IHH', e.va(off), qw, len(pos))
        n_blocks = len(blocks)
    list_offset = e.put(bytes(items), 'block_list')

    group_table = struct.pack('<IHH', 0, n_blocks, 0)
    off_gt = e.put(group_table, 'group_table')
    e.point(off_gt, 'block_list')

    # the material table sits RIGHT BEFORE the mesh object, as in retail
    e.align_to(16)
    off_mt = len(e.d)
    e.d += struct.pack('<H', material_index) + b'\x00\x00'
    e.label('material_table', off_mt)

    mesh = struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0, 1,
                       CPV_SLOTS_PER_COMPONENT, 0, 0, 0, 1)
    off_mesh = e.put(mesh, 'mesh', align=4)
    e.point(off_mesh + 0x0C, 'material_table')
    e.point(off_mesh + 0x10, 'group_table')
    # The per-vertex chain has to match the mesh PACKET BY PACKET.
    cpv_group = ([([0] * nv,) for _p, _q, nv in GAME_PACKETS]
                 if GAME_PACKETS else blocks)
    emit_per_vertex_chain(e, [cpv_group], 'mesh_t14')
    e.point(off_mesh + 0x14, 'mesh_t14')

    # ---------------------------------------------------------------- componente
    pts = [c for pos, _u, _n, _o in blocks for c in pos]
    radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)

    off_cd = e.put(compdata(radius, position), 'compdata')
    e.point(off_cd + 0x3C, 'mesh')            # LOD slot 0
    # The three fields retail NEVER leaves null and that the cube path did (the
    # --place path already did this):
    #   +0x04  the owner, which is the MapRoot+0x194 structure itself
    #   +0x08  this object's name, which lives inside it at +0x70
    #   +0x18  the component entry that points here
    e.label('compdata_name', off_cd + 0x70)
    e.point(off_cd + 0x04, 'extra_194')
    e.point(off_cd + 0x08, 'compdata_name')
    e.point(off_cd + 0x18, 'components')

    comp = bytearray(COMPONENT_SIZE)
    struct.pack_into('<f', comp, 0x18, radius)
    struct.pack_into('<I', comp, 0x10, tok(VT_COMPONENT_TOKEN))
    # +0x0B pass_mask: NEVER zero in retail (303 of atlanta's 651 are 8) and zero
    # means "enters no pass", i.e. does not draw. Among the components whose mesh
    # uses the same shader class as this one (007A1EF8), 8 is by far the most
    # common value: 230 against 50 for the runner-up.
    comp[0x0B] = 8
    half = max(abs(q[i]) for pos, _u, _n, _o in blocks for q in pos for i in (0, 2))
    # the cube inherits the donor's extents, so the donor's grid is the right one here
    grid = donor_grid(donor)
    x0, x1, z0, z1 = cell_box(grid, position[0], position[2], half, half)
    comp[0x14], comp[0x15], comp[0x16], comp[0x17] = x0, x1, z0, z1
    off_comp = e.put(bytes(comp), 'components')
    e.point(off_comp + 0x0C, 'compdata')
    comp_vas = [e.va(off_comp)]                 # goes into the MapRoot+0x1C index
    # the cells the cube's tile box covers, to register unique 0 in them
    gw = grid[0]
    cells_per_unique = {0: [z * gw + x for z in range(z0, z1 + 1)
                         for x in range(x0, x1 + 1)]}

    # ---------------------------------------------------------------- hoods
    # THE SAME NAMES AND THE SAME NUMBER as the donor.
    #
    # Evidence: every file that loaded has atlanta's 7 hoods with their original
    # names; the two that hung after a complete Place (`bx2_hood` and `cubo_v4`)
    # had ONE hood called `zz`. Hood names appear in `.loc`, race data and in the
    # `_bnd`, so inventing one probably breaks whoever looks them up by name. Here
    # every donor hood exists; only the first gets the geometry, the others keep
    # a zero count.
    # `hoods` overrides the donor's list. THE FIELD IS FOUR BYTES - three
    # characters and a terminator - which is why the retail names are `bh`,
    # `dt`, `fwy` and not the full neighbourhood names. Midnight Club 2 spells
    # its own hoods `l_beverlyhills`, `l_downtown` and so on, so the CODE
    # prefix carries over but the rest has to be abbreviated on the way in.
    #
    # When more hoods are asked for than the donor has, the records cycle: the
    # record is a 0xB0-byte blob copied and relinked, and reusing one is only a
    # matter of the name written over it.
    from_donor = hood_names_(donor)
    hood_names = list(hoods) if hoods else from_donor
    entries = bytearray()
    for i, nm in enumerate(hood_names):
        hood_record(e, donor, nm, 'hood_rec%d' % i, i % len(from_donor))
        ent = bytearray(HOOD_ENTRY_SIZE)
        if i == 0:
            struct.pack_into('<I', ent, 4, 1)   # only the first one gets the model
        entries += ent
    off_ent = e.put(bytes(entries), 'hoods')
    empty = empty_hood(e)
    for i in range(len(hood_names)):
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 0, 'hood_rec%d' % i)
        # the two arrays are NEVER null in retail, even with a zero count
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 8,
                 'components' if i == 0 else empty)
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 0x10, empty)

    # ---------------------------------------------------------------- MapRoot
    # COPIED WHOLE from the donor (0x290 bytes) with the pointers zeroed, and only
    # then the fields I control. I used to copy two small pieces (+0x20..0x2B and
    # +0x40..0x7F) and zero the rest - ~0x250 bytes of city scalars (extents, LOD
    # distances, fog, saturation) went to zero without my knowing they existed.
    r = 0x80
    e.d[r:r + MAPROOT_SIZE] = maproot_base(donor)
    inline_palettes(e, r)
    e.u32(r + 0x00, tok(VT_MAPROOT))
    e.u32(r + 0x08, 0 if probe else n_groups)   # shader groups
    e.point(r + 0x0C, 'groups')
    empty_array(e, 'field10')                   # never NULL: see empty_array()
    e.point(r + 0x10, 'field10')
    e.u32(r + 0x14, 0 if probe else len(hood_names))
    e.point(r + 0x18, 'hoods')
    # the flat index, now with the real component inside
    # THE SIZE OF THE TWO PER-CELL ARRAYS IS `grid_w * grid_h`, NOT ANY NUMBER.
    # I wrote 64 at +0x268 and left +0x26C/+0x270 as they came from the donor
    # (64 x 62 = 3968 in atlanta), breaking the invariant
    # `cell_count == grid_w * grid_h` that holds in the three measured cities. The
    # culling loop indexes by `rel_z * grid_w + rel_x`, so with the donor's grid
    # and a 64-entry array it read up to 3968 records from a 64-entry buffer -
    # 47 KB of reads outside the array.
    scratch = abs(struct.unpack_from('<i', e.d, r + 0x26C)[0]
                  * struct.unpack_from('<i', e.d, r + 0x270)[0]) or 64
    e.u32(r + 0x268, scratch)                   # == grid_w * grid_h
    block_1c(e, donor, 'field1c', comp_vas, (), scratch, cells_per_unique)
    e.point(r + 0x1C, 'field1c')
    e.point(r + 0x2C, 'paletteA')
    e.point(r + 0x30, 'paletteB')
    made = extra_structures(e, donor, 'compdata')
    n_chains = chains_1d4(e, donor, texture_donor)

    n = e.close(out_path)
    print('written %s (%d bytes)' % (out_path, n))
    print('  shader copied from donor slot %d; %d slots in group 0' % (came_from, n_slots))
    print('  extra structures created: %s; %d chain(s) of +0x1D4/+0x1D8'
          % (', '.join('+0x%X' % x for x in made), n_chains))
    print('  %d block(s), %d vertices, radius %.1f, scale 1/%.0f'
          % (len(blocks), sum(len(p) for p, _u, _n, _o in blocks), radius, 1.0 / scale))
    return n


# --------------------------------------------------------------------------
# the real city: feeding the builder with a place.tsv
# --------------------------------------------------------------------------
#
# `build()` assembles ONE component from a test cube. This is where the table
# written by `mc2_port.py city` comes in: one line per thing to draw, with the
# mesh name, the matrix and the AABB.
#
# A deliberate first slice: ONLY the `comp` lines. They are already in world
# coordinates. The `.hood` AABB can include OTHER parts of the same object, so
# it is not a pivot for the chosen part. They do not depend on the instance
# record, which had not been decoded yet. They are the streets and buildings;
# the `inst` lines are repeated walls, palm trees and posts.
#
# The `.pck` hood name fits in FOUR BYTES (three characters and the
# terminator), and this abbreviation is the one the C++ fork fixed on both sides.
HOOD_ABBREV = {
    'l_beverlyhills': 'bh',  'l_downtown': 'dt',   'l_ghetto': 'gh',
    'l_hills': 'hl',         'l_hollywood': 'hw',  'l_i05': 'i05',
    'l_i10': 'i10',          'l_i101': '101',      'l_i105': '105',
    'l_industrial': 'ind',   'l_lax': 'lax',       'l_santamonica': 'sm',
}


def read_place(file_path):
    """(lines, extents) of a `<city>_place.tsv`."""
    extents = {}
    hdr = None
    lines = []
    with open(file_path, 'r', errors='replace') as f:
        for l in f.read().splitlines():
            if l.startswith('#'):
                p = [x.strip() for x in l[1:].split('\t')]
                if p and p[0] in ('extents_min', 'extents_max'):
                    extents[p[0]] = [float(x) for x in p[1:] if x]
                continue
            if not l.strip():
                continue
            if hdr is None:
                hdr = l.split('\t')
                continue
            c = l.split('\t')
            if len(c) >= len(hdr):
                lines.append(dict(zip(hdr, c)))
    return lines, extents


def mesh_path(folder, line):
    """The `.mod` a place.tsv line names."""
    if line['part'] == '-':
        name = '%s_%s.mod' % (line['name'], line['lod'])
    else:
        name = '%s_%s_%s.mod' % (line['name'], line['lod'], line['part'])
    p = os.path.join(folder, name)
    return p if os.path.isfile(p) else None


def centre_blocks(blocks):
    """Blocks shifted to the BOX MIDPOINT, and the shift applied.

    Measured on atlanta: a component's geometry is LOCAL and centred, and the
    world comes in through `ComponentData+0x2C`. In `a_bh_blk_02x#geom` the local
    box goes from -121.6 to 121.8 in x and from -162.9 to 162.8 in z, with the
    position at (-388.0, 12.4, -1042.2); the stored radius (203.8) is exactly the
    half-diagonal of that box.

    `read_model` already removes the bulk, but it centres x and z on the box and
    rests y on zero, so a difference in y is left that has to go into the position.
    """
    pts = [q for pos, _u, _n, _o in blocks for q in pos]
    mn = [min(q[i] for q in pts) for i in range(3)]
    mx = [max(q[i] for q in pts) for i in range(3)]
    c = [(mn[i] + mx[i]) / 2.0 for i in range(3)]
    if max(abs(v) for v in c) < 1e-4:
        return blocks, c
    outside = [([tuple(q[i] - c[i] for i in range(3)) for q in pos], u, nr, o)
            for pos, u, nr, o in blocks]
    return outside, c


def read_world_component(file_path):
    """Place a component's part without changing its world vertices.

    The chosen .mod may be only main/refl/emissive, but the TSV's emin/emax
    belong to the whole .hood object. Using that box's centre as the position
    after centring this part shifts the geometry (368.26 units on
    l_santamonica_blk_14). Restore the MC2 origin and store exactly the centre
    removed by centre_blocks in ComponentData+0x2C. The AABB is for culling, not a pivot.
    """
    blocks = read_model(file_path, 1.0, keep_origin=True)
    return centre_blocks(blocks) if blocks else ([], (0.0, 0.0, 0.0))


def emit_geometry(e, blocks, map_key, position, material_index=1):
    """Packets + list + group table + material + mesh + ComponentData.

    The same sequence `build()` runs for the cube, extracted so it can run
    hundreds of times. Returns (ComponentData key, radius).
    """
    # THREE PATHS, from the best informed to the least. With groups AND texture
    # indices, a mesh with ONE GROUP PER MATERIAL - the only way to give a
    # component more than one texture. Without them, the old path.
    if GAME_GROUPS and GAME_INDICES:
        emit_grouped_mesh(e, GAME_GROUPS, map_key, GAME_INDICES,
                             CPV_SLOTS_PER_COMPONENT)
    else:
        if GAME_PACKETS:
            # Ready packets, from a mesh.pck the game itself assembled.
            items = bytearray()
            for payload, qw, nv in GAME_PACKETS:
                off = e.put(flip_winding_payload(payload) if FLIP_WINDING else payload, align=16)
                items += struct.pack('<IHH', e.va(off), qw, nv)
            n_packets = len(GAME_PACKETS)
        else:
            scale = mc2.pick_scale(blocks)
            packets = [mc2.make_block(pos, uv, nr, scale, i % 2 == 1)
                       for i, (pos, uv, nr, _o) in enumerate(blocks)]
            offs = [e.put(pk, align=16) for pk in packets]
            items = bytearray()
            for off, pk, (pos, _u, _n, _o) in zip(offs, packets, blocks):
                items += struct.pack('<IHH', e.va(off), (len(pk) + 15) // 16, len(pos))
            n_packets = len(blocks)
        e.put(bytes(items), map_key + '_list')

        off_gt = e.put(struct.pack('<IHH', 0, n_packets, 0), map_key + '_gt')
        e.point(off_gt, map_key + '_list')

        # the material table sits RIGHT BEFORE the mesh object, as in retail
        e.align_to(16)
        off_mt = len(e.d)
        e.d += struct.pack('<H', material_index) + b'\x00\x00'
        e.label(map_key + '_mt', off_mt)

        # +0x18 is never zero in retail: 624 atlanta meshes have 1, 408 have a
        # pointer and 190 have CDCDCDCD. 1 is the most common form and the only
        # one that can be written without inventing a target.
        off_mesh = e.put(struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0, 1,
                                     CPV_SLOTS_PER_COMPONENT, 0, 0, 0, 1),
                         map_key + '_mesh', align=4)
        # DO NOT switch to align=16. Retail has 100% of its meshes aligned to 16
        # and mine sit at %16==4, but switching to 16 REGRESSED: the city that ran
        # at 60 FPS started to hang on the FIRST frame (game frames = 1). Aligning
        # shifts every following allocation, and something depends on the layout
        # align=4 produces - probably the adjacency between the material table
        # and the mesh object, which retail keeps glued together. Measured, not assumed.
        e.point(off_mesh + 0x0C, map_key + '_mt')
        e.point(off_mesh + 0x10, map_key + '_gt')
        cpv_group = ([([0] * nv,) for _p, _q, nv in GAME_PACKETS]
                     if GAME_PACKETS else blocks)
        emit_per_vertex_chain(e, [cpv_group], map_key + '_t14')
        e.point(off_mesh + 0x14, map_key + '_t14')

    # the retail radius is the half-diagonal of the local box, not the distance
    # to the vertex farthest from the centroid
    pts = [q for pos, _u, _n, _o in blocks for q in pos]
    radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)

    off_cd = e.put(compdata(radius, position), map_key + '_cd')
    # THE MESH GOES INSIDE, in retail's majority form (431 of 650 in tokyo).
    # The separate mesh object emitted above stays in the file and stays valid -
    # retail has both too - but the embedded one is the one that counts.
    put_embedded_mesh(e, off_cd, map_key + '_mt', map_key + '_gt',
                      blocks, map_key + '_emb')
    e.label(map_key + '_cd_name', off_cd + 0x70)
    return map_key + '_cd', radius, off_cd


REAL_PCK_CENTRE = {}      # kind -> (cx, cz) measured in the game's .pck


def real_pck_centre(file_path):
    """XZ centre of the vertices the GAME assembled, not those of the .mod.

    Each Instance matrix in place.tsv assumes the RECENTRED mesh: measuring the
    8511 instances, 6249 (73%) only match with `final = (tx,tz)`. But of the 431
    kinds that came from the game, 71 keep the original pivot (centre up to 1894
    away from the origin) - their instances fly away, and that is what showed up
    piled far off. Since the geometry comes from the `.pck` and the vertices
    cannot be recentred, I measure the real centre here and COMPENSATE in the
    translation.
    """
    import struct as _s
    d = open(file_path, 'rb').read()
    N = len(d)
    vb = _s.unpack_from('<I', d, 0)[0]
    fo = lambda v: v - vb + 0x80
    w = lambda o: _s.unpack_from('<I', d, o)[0]
    h = lambda o: _s.unpack_from('<H', d, o)[0]
    g, geo = w(0x88) & 0xFFFF, fo(w(0x90))
    xs = []
    zs = []
    for gi in range(g):
        ge = geo + 8 * gi
        lst, n = fo(w(ge)), h(ge + 4)
        for k in range(n):
            e = lst + 8 * k
            dp, qw = fo(w(e)), h(e + 4)
            if not (0x80 <= dp < N) or dp + 16 * qw > N:
                continue
            sc = _s.unpack_from('<f', d, dp + 4)[0]
            nv = _s.unpack_from('<I', d, dp + 8)[0]
            if not (0 < nv <= 64) or not (0 < sc < 100):
                continue
            if d[dp + 15] != 0x69:          # only the POSITION stream
                continue
            for i in range(nv):
                o = dp + 16 + 6 * i
                if o + 6 > N:
                    break
                x, _y, z = _s.unpack_from('<hhh', d, o)
                xs.append(x * sc)
                zs.append(z * sc)
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(zs) / len(zs))


def _row_matrix(l):
    """The Matrix34 of a place.tsv `inst` line: 12 floats in the game's order,
    three rows of 3x3 rotation and then the translation."""
    return [float(l[c]) for c in
            ('m00', 'm01', 'm02', 'm10', 'm11', 'm12',
             'm20', 'm21', 'm22', 'tx', 'ty', 'tz')]


def _aabb_centre(l):
    return tuple((float(l['emin' + a]) + float(l['emax' + a])) / 2.0
                 for a in ('x', 'y', 'z'))


def compdata_inst(radius, centre, name):
    """ComponentData of an instance KIND: the same as the comp one, but with the
    mcInstCityModelType token at +0x28 and the centre in LOCAL coordinates (each
    Instance's matrix is what puts it in the world)."""
    cd = bytearray(0xF0)
    struct.pack_into('<I', cd, 0x00, 1)
    struct.pack_into('<I', cd, 0x28, tok(VT_INST_KIND_TOKEN))
    struct.pack_into('<3f', cd, 0x2C, *centre)
    struct.pack_into('<f', cd, 0x38, radius)
    cd[0x70:0x70 + len(name)] = name
    return bytes(cd)


# VIF packets from a .pck that THE GAME ITSELF generated (see
# mc3_heap_to_pck.py). When this is filled in, _emit_mesh copies these blocks
# instead of calling mc2.make_block - which swaps MY stripping, parity, ADC and
# block splitting for the game's, the four things hand emission got wrong. A
# list of (payload, quadwords, vertices).
GAME_PACKETS = None
VT_TEXTURE_TOKEN = 0x007A2320  # serialised rmcTexturePS2 token (runtime vtable = 0x00627280)
# When --pck-mesh points to a folder: model name -> the game's packets.
PCK_FOLDER = None
PCK_PER_MODEL = {}
GROUPS_PER_MODEL = {}
GAME_GROUPS = None      # groups of the current model
TEXTURES_PER_MODEL = {}   # model name -> texture names, in mtl order
from_game = [0]
DEBUG_FLAG = [0]


def read_pck_groups(file_path):
    """A mesh.pck's packets GROUPED, one item per group.

    The `.pck` the game assembled keeps the split by material (27 groups in a
    27-material model). Flattening that into one group, as I used to, forces the
    whole city onto one material index, and that is why everything looked the
    same. I keep the split so each group can get its own texture.
    """
    import struct as _s
    d = open(file_path, 'rb').read()
    vb = _s.unpack_from('<I', d, 0)[0]
    fo = lambda a: a - vb + 0x80
    groups = _s.unpack_from('<I', d, 0x88)[0] & 0xFFFF
    geo = fo(_s.unpack_from('<I', d, 0x90)[0])
    out = []
    for g in range(groups):
        go = geo + 8 * g
        pk, n = _s.unpack_from('<IH', d, go)
        e0 = fo(pk)
        item = []
        for k in range(n):
            dp, qw, nv = _s.unpack_from('<IHH', d, e0 + 8 * k)
            if nv == 0 or qw == 0:
                continue
            o = fo(dp)
            item.append((d[o:o + 16 * qw], qw, nv))
        out.append(item)
    return out


def read_pck_packets(file_path):
    """Extract the VIF packets of a mesh.pck, in group order."""
    import struct as _s
    d = open(file_path, 'rb').read()
    vb = _s.unpack_from('<I', d, 0)[0]
    fo = lambda a: a - vb + 0x80
    groups = _s.unpack_from('<I', d, 0x88)[0] & 0xFFFF
    geo = fo(_s.unpack_from('<I', d, 0x90)[0])
    out = []
    for g in range(groups):
        go = geo + 8 * g
        pk, n = _s.unpack_from('<IH', d, go)
        e0 = fo(pk)
        for k in range(n):
            dp, qw, nv = _s.unpack_from('<IHH', d, e0 + 8 * k)
            # AN EMPTY PACKET DOES NOT PROPAGATE. The game emits some with nv=0,
            # and they created an asymmetry between the block list and the
            # per-vertex chain (the mesh declared 2 blocks, [74, 0], and the
            # chain 1) - the verifier reported "count 1 != 2" and "payload outside"
            # in 194 meshes. A block with no vertex draws nothing, so it goes.
            if nv == 0 or qw == 0:
                continue
            o = fo(dp)
            out.append((d[o:o + 16 * qw], qw, nv))
    return out


def texture_slots(model, n_groups):
    """Shader slot of each group, by the NAME of the texture the mtl asked for.

    THE LIST HAS TO BE ALIGNED BY MATERIAL, one entry per `mtl` of the .mod,
    including those with `textures: 0`. The old map listed only the materials
    WITH a texture, and since the `.pck` has one group per material, everything
    after a texture-less material was shifted by one: the group got the next
    material's texture and the last one fell into slot 0. Measured: 29 models
    have a texture-less material and in them **379 groups** came out swapped; and
    slot 0 is `fx_rearlight_glow`, a white, diffuse glow texture - those were
    the flat bright planes in the middle of the city. Fork City MODS 2 saw the
    same two models (`industrial_blk_06` and `_04`) in the visible list "with
    only fx_rearlight_glow", which agrees with this measurement.

    A texture-less material INHERITS the previous one instead of falling into
    slot 0: it draws with its own diffuse colour (0.4 0.4 0.4 in the typical
    case), so anything neutral is better than stamping a glow on it.
    """
    names = TEXTURES_PER_MODEL.get(model, [])
    # A texture-less material BEFORE the first textured one has no "previous" to
    # inherit from, and falling into slot 0 is the worst possible choice: slot 0
    # is `fx_rearlight_glow`. MEASURED ON 12/09, with a one-variable bisection: a
    # map aligned by material, which now declares the leading gap of 26 models,
    # sent 205 groups to slot 0 and the game died on the FIRST FRAME with
    # `R5900 Exception: Jump to unaligned address (PC 0x00000633)`. The only
    # delta between the build that runs and the one that dies was 410 bytes, all
    # in the material table. So the LEADING gap inherits FORWARD, from the first
    # textured material, and never goes to slot 0.
    # AND SLOT 0 IS FATAL, not just ugly. Measured on 12/09 in two independent
    # failures: (a) 205 groups sent to slot 0 by the leading gap killed the game
    # on the first frame; (b) the 216 new pieces, which have no entry in the map,
    # ALL fell into slot 0 and the boot died in `datPageFile::PageIn` (pc
    # 0x42cae4/0x42cbec/0x42cc1c/0x42cc40) reading garbage addresses. The reason
    # is on the .ppf side: slot 0 is `fx_rearlight_glow` and its chunk is the
    # ONLY one of 965 whose body is entirely zeroed, so the page-in dereferences
    # an empty header. That is why the final fallback is 1, never 0.
    FALLBACK = 1
    first_ = FALLBACK
    for n in names:
        k = n.lower() if n else ''
        if k and k in TEXTURE_POS:
            first_ = TEXTURE_POS[k]
            break
    outside = []
    last = first_
    for g in range(n_groups):
        n = names[g].lower() if g < len(names) else ''
        if n and n in TEXTURE_POS:
            last = TEXTURE_POS[n]
        outside.append(last)
    return outside


def emit_grouped_mesh(e, groups, map_key, indices, slot_count=1):
    """A mesh with ONE GROUP PER MATERIAL, so each one can get a texture.

    `groups` is the output of read_pck_groups (a list of packet lists) and
    `indices` carries the shader slot of each group. The previous version
    flattened everything into one group and used a single index - which is why
    the whole city looked the same.

    Layout, the same as the three levels of the rest of the file: the COUNT
    comes 16 bytes BEFORE the array, and the array has one 8-byte entry per
    group (`[ptr to the list][u16 packets][u16 0]`).
    """
    for g, packets in enumerate(groups):
        items = bytearray()
        for payload, qw, nv in packets:
            off = e.put(flip_winding_payload(payload) if FLIP_WINDING else payload, align=16)
            items += struct.pack('<IHH', e.va(off), qw, nv)
        e.put(bytes(items), '%s_list%d' % (map_key, g))

    e.align_to(16)
    e.put(struct.pack('<I', len(groups)) + bytes(12))      # the count, at -16
    off_gt = e.put(bytes(8 * len(groups)), map_key + '_gt')
    for g, packets in enumerate(groups):
        e.point(off_gt + 8 * g, '%s_list%d' % (map_key, g))
        struct.pack_into('<H', e.d, off_gt + 8 * g + 4, len(packets))

    e.align_to(16)
    off_mt = len(e.d)
    for g in range(len(groups)):
        e.d += struct.pack('<H', indices[g] if g < len(indices) else 0)
    e.d += bytes((-len(e.d)) % 4)
    e.label(map_key + '_mt', off_mt)

    off_mesh = e.put(struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0,
                                 len(groups), slot_count, 0, 0, 0, 1),
                     map_key + '_mesh', align=4)
    e.point(off_mesh + 0x0C, map_key + '_mt')
    e.point(off_mesh + 0x10, map_key + '_gt')
    cpv_group = [[([0] * nv,) for _p, _q, nv in pac] for pac in groups]
    emit_per_vertex_chain(e, cpv_group, map_key + '_t14', slot_count=slot_count)
    e.point(off_mesh + 0x14, map_key + '_t14')
    return off_mesh


def _emit_mesh(e, blocks, map_key, material_index=1, slot_count=1):
    """Only the mesh (packets + list + groups + material + rmcModelGeom), without
    a ComponentData. Extracted from emit_geometry for the instance kinds."""
    if GAME_PACKETS:
        # The packets come ready from the game: copy them and only redo the list.
        items = bytearray()
        for payload, qw, nv in GAME_PACKETS:
            off = e.put(flip_winding_payload(payload) if FLIP_WINDING else payload, align=16)
            items += struct.pack('<IHH', e.va(off), qw, nv)
        n_packets = len(GAME_PACKETS)
    else:
        scale = mc2.pick_scale(blocks)
        packets = [mc2.make_block(pos, uv, nr, scale, i % 2 == 1)
                   for i, (pos, uv, nr, _o) in enumerate(blocks)]
        offs = [e.put(pk, align=16) for pk in packets]
        items = bytearray()
        for off, pk, (pos, _u, _n, _o) in zip(offs, packets, blocks):
            items += struct.pack('<IHH', e.va(off), (len(pk) + 15) // 16, len(pos))
        n_packets = len(blocks)
    e.put(bytes(items), map_key + '_list')
    # n_packets, NOT len(blocks): with --pck-mesh the list holds the game's
    # packets and the blocks are only the model reading. Declaring len(blocks)
    # made the group table promise more blocks than the list has, and the
    # verifier reported "count 1 != 2" with a phantom block of 0 vertices.
    off_gt = e.put(struct.pack('<IHH', 0, n_packets, 0), map_key + '_gt')
    e.point(off_gt, map_key + '_list')
    e.align_to(16)
    off_mt = len(e.d)
    e.d += struct.pack('<H', material_index) + b'\x00\x00'
    e.label(map_key + '_mt', off_mt)
    off_mesh = e.put(struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0, 1,
                                 slot_count, 0, 0, 0, 1),
                     map_key + '_mesh', align=4)
    # DO NOT switch to align=16. Retail has 100% of its meshes aligned to 16 and
    # mine sit at %16==4, but switching to 16 REGRESSED: the city that ran at
    # 60 FPS started to hang on the FIRST frame (game frames = 1). Aligning
    # shifts every following allocation, and something depends on the layout
    # align=4 produces - probably the adjacency between the material table and
    # the mesh object, which retail keeps glued together. Measured, not assumed.
    e.point(off_mesh + 0x0C, map_key + '_mt')
    e.point(off_mesh + 0x10, map_key + '_gt')
    # The per-vertex chain has to match the mesh PACKET BY PACKET. With
    # --pck-mesh the packets are the game's, so the vertex count comes from them
    # and not from my blocks - otherwise the chain declares 93 blocks for a
    # 27-block mesh and Place walks garbage.
    if GAME_PACKETS:
        cpv_group = [([0] * nv,) for _payload, _qw, nv in GAME_PACKETS]
    else:
        cpv_group = blocks
    emit_per_vertex_chain(e, [cpv_group], map_key + '_t14', slot_count=slot_count)
    e.point(off_mesh + 0x14, map_key + '_t14')


def emit_instances(e, insts, models_dir, grid, material_index=1,
                     hood_map=None):
    """Build the instance KINDS and the 0x60 Instance records.

    Each distinct model becomes ONE kind (mesh in local coordinates + an
    mcInstCityModelType ComponentData), chained at MapRoot+0x198. Each `inst`
    line becomes an Instance record pointing to the kind, with its Matrix34, the
    tile box of the world centre and the kind's local radius. Instances of the
    same kind form a list linked by next/prev.

    Returns (inst_vas, cells_per_inst, by_hood_inst, head_key, n_kinds).
    """
    count_per_kind = {}
    for l in insts:
        key = (l['name'], l['lod'], l['part'])
        count_per_kind[key] = count_per_kind.get(key, 0) + 1

    kinds = {}                      # key -> (key, radius, centre) or None
    kind_order = []
    for l in insts:
        key = (l['name'], l['lod'], l['part'])
        if key in kinds:
            continue
        p = mesh_path(models_dir, l)
        if not p:
            kinds[key] = None
            continue
        try:
            # The place.tsv matrix belongs to this mesh's original pivot.
            blocks = read_model(p, 1.0, keep_origin=True)
        except Exception:
            kinds[key] = None
            continue
        if not blocks:
            kinds[key] = None
            continue
        map_key = 'itipo_%d' % len(kind_order)
        pts = [q for pos, _u, _n, _o in blocks for q in pos]
        centre = tuple((min(p[i] for p in pts) + max(p[i] for p in pts)) / 2.0
                       for i in range(3))
        radius = max(((q[0] - centre[0]) ** 2 + (q[1] - centre[1]) ** 2
                    + (q[2] - centre[2]) ** 2) ** 0.5 for q in pts)
        slot_count = count_per_kind[key]
        # Each instance KIND also has its own .pck from the game. Without this
        # the 431 kinds stayed on hand emission while the 298 components came
        # from the game - half the city made each way.
        global GAME_PACKETS
        globals()['GAME_GROUPS'] = None
        globals()['GAME_INDICES'] = None
        if PCK_FOLDER:
            base_name = os.path.splitext(os.path.basename(p))[0].lower()
            GAME_PACKETS = PCK_PER_MODEL.get(base_name)
            if GAME_PACKETS:
                from_game[0] += 1
            # THE INSTANCE KINDS TOO. I had put one group per material only on
            # the COMPONENT path; the 431 kinds stayed on the old path and their
            # per-vertex chain did not match the group table - 299 of 729 valid
            # meshes in all of LA.
            globals()['GAME_GROUPS'] = GROUPS_PER_MODEL.get(base_name)
            globals()['GAME_INDICES'] = (
                texture_slots(base_name, len(GROUPS_PER_MODEL[base_name]))
                if (base_name in GROUPS_PER_MODEL) else None)
        if GAME_GROUPS and GAME_INDICES:
            emit_grouped_mesh(e, GAME_GROUPS, map_key, GAME_INDICES,
                                 slot_count)
        else:
            _emit_mesh(e, blocks, map_key, material_index, slot_count=slot_count)
        off_cd = e.put(compdata_inst(radius, centre, b'generated_inst'), map_key + '_cd')
        put_embedded_mesh(e, off_cd, map_key + '_mt', map_key + '_gt',
                          blocks, map_key + '_emb', slot_count=slot_count)
        e.label(map_key + '_name', off_cd + 0x70)
        kinds[key] = (map_key, radius, centre)
        kind_order.append(map_key)

    # chain the kinds at +0x198 (same form as the comp chain at +0x194)
    for k, map_key in enumerate(kind_order):
        cd = e.marks[map_key + '_cd']
        e.point(cd + 0x04, 'extra_198')
        e.point(cd + 0x08, map_key + '_name')
        if k:
            e.point(cd + 0x0C, kind_order[k - 1] + '_cd')
            e.point(cd + 0x14, kind_order[k - 1] + '_cd')
        if k + 1 < len(kind_order):
            e.point(cd + 0x10, kind_order[k + 1] + '_cd')

    # the Instance records, contiguous per hood
    gw = grid[0]
    inst_vas = []
    cells_per_inst = {}
    by_hood_inst = {}
    kind_chain = {}
    # group by the TARGET hood (the donor's), not by MC2's hood: a hood's records
    # have to stay contiguous, and the file can only have the hoods the donor has.
    by_hood = {}
    for l in insts:
        dest = hood_map.get(l['hood'], l['hood']) if hood_map else l['hood']
        by_hood.setdefault(dest, []).append(l)

    for hood, entries_ in by_hood.items():
        base_name = None
        n_hood = 0
        for l in entries_:
            key = (l['name'], l['lod'], l['part'])
            t = kinds.get(key)
            if not t:
                continue
            kind_key, radius, _centre = t
            cx, cy, cz = _aabb_centre(l)
            x0, x1, z0, z1 = cell_box(grid, cx, cz, radius, radius)
            rec = bytearray(INSTANCE_SIZE)
            struct.pack_into('<I', rec, 0x10, tok(VT_INST_TOKEN))
            rec[0x0B] = 8
            rec[0x14], rec[0x15], rec[0x16], rec[0x17] = x0, x1, z0, z1
            struct.pack_into('<f', rec, 0x18, radius)
            # DO NOT COMPENSATE THE PIVOT. I tried subtracting the real .pck
            # centre from the translation, thinking the kinds with a preserved
            # pivot were shifting the instances. MEASURED BEFORE INSTALLING: of
            # the 431 kinds, only 74 instances (0.9% of 8511) come from a kind
            # with a large pivot, and of those **70 end up closer to the right
            # place WITHOUT the compensation**. The correction made it worse. The
            # pivot is not the cause of the missing city.
            struct.pack_into('<12f', rec, 0x20, *_row_matrix(l))
            struct.pack_into('<3f', rec, 0x50, cx, cy, cz)
            idx = len(inst_vas)
            key_i = 'inst_%d' % idx
            # DoSetWorld (0x2ADB48) uploads Instance+0x20 with three LQ.
            # LQ silently rounds down to 16 bytes. With align=4 the scalar
            # scratchpad mirror is right but DMA reads the preceding CPV
            # index as matrix[0], losing tz and flattening/stacking objects.
            # 0x60-byte records remain contiguous after aligning the first.
            off = e.put(bytes(rec), key_i, align=16)
            e.point(off + 0x0C, kind_key + '_cd')
            if base_name is None:
                base_name = 'insts_%s' % hood
                e.label(base_name, off)
            inst_vas.append(e.va(off))
            cells_per_inst[idx] = [z * gw + x for z in range(z0, z1 + 1)
                                 for x in range(x0, x1 + 1)]
            kind_chain.setdefault(key, []).append((key_i, off))
            n_hood += 1
        if base_name is not None:
            by_hood_inst[hood] = (base_name, n_hood)

    # the per-kind linked list: next/prev and chain_index; and the KIND's +0x18
    # points back to its FIRST instance (never null in retail).
    for key, chain in kind_chain.items():
        kind_key = kinds[key][0]
        e.point(e.marks[kind_key + '_cd'] + 0x18, chain[0][0])
        for j, (key_i, off) in enumerate(chain):
            struct.pack_into('<I', e.d, off + 0x1C, j)
            # THE DIRECTION OF THE TWO LINKS WAS SWAPPED, and that erased 95
            # percent of the city. What walks the list is
            # `mcCullableType::RenderAllInstances` (retail 0x0024B1A8):
            #
            #     cull = *(kind + 0x18);                  // head of the ACTIVE ones
            #     while (cull) { Render(cull); cull = *(cull + 0x04); }
            #
            # so +0x04 IS THE NEXT. The alpha's `mcCullableType::AddToActive`
            # (retail 0x0024B030) confirms the pair: it zeroes `cull[0]` and puts
            # the old head in `cull[1]`, that is +0x00 = prev and +0x04 = next.
            # Written the other way round, the render walks the PREV direction
            # from the head and stops at the first node. Measured: v7 reached 431
            # of 8511 instances (5.1 percent), exactly one per kind, while the
            # retail sd reaches 5008 of 5008.
            if j + 1 < len(chain):
                e.point(off + 0x04, chain[j + 1][0])
            if j:
                e.point(off + 0x00, chain[j - 1][0])

    head = kind_order[-1] + '_cd' if kind_order else None
    return inst_vas, cells_per_inst, by_hood_inst, head, len(kind_order)


# TURNING ON THE PER-VERTEX LIGHT STREAM.
#
# Measured on atlanta: only 4.5%% of the blocks (471 of 10361) carry the ITOP+2
# stream, and even so the whole city shows - because a block WITHOUT the stream
# INHERITS whatever the previous one left in VU memory. In a city generated from
# scratch no block writes it, so all of them inherit garbage, and at midnight
# that is black: the geometry drew (531 and 580 draw calls measured) and nothing
# was visible.
#
# TRIED AND REVERTED: turning it on in every block hung on the FIRST frame
# (game frames = 1), the same signature as the attempt to align to 16.
#
# I EXPLAINED THAT REVERSAL WRONGLY, and FORK C++ MODS AND PATCHES disproved it
# by decompilation. I had written that 'what decides whether the stream is read
# is the MATERIAL's flags word'. IT IS NOT. mcCityModelClass::SetRenderStates
# (0x0024B300, vt[12] of cullable list 3) calls rmcSetCpvMode(1, 0) and
# rmcCpvPalette::Download(*(u32*)(mcCity + 0x2C)) ONCE PER PASS. CPV - colour
# per vertex - stays on for the whole city, decided by the cullable's CLASS,
# not by material and not by block. The sky, at the same vtable position, calls
# rmcSetCpvMode(0, 0). So: the ITOP+2 data is ALWAYS consumed in the city pass,
# and writing the stream is not forbidden.
#
# So the first-frame hang is a defect of the PACKET I emit, not a rule of the
# engine. What can still differ is the active VU program, which
# mcCullableMgr::Render re-sends through lowPsxGfx::DoMicrocode around certain
# lists - which is why the test stays PAIRED: copy the shader of a slot whose
# blocks provably carry the stream and turn NORMALS on together. Measured on
# atlanta, the blocks with the stream use material indices 19, 72, 65, 24, 293,
# 7, 71 and 26; of these, all but 293 are among the donor's 128 usable slots
# (see --shader-list).
#
# WHY THE WHITE PALETTE DID NOT SAVE IT: checked in the file, MapRoot+0x2C
# points correctly to the inline palette at +0x290, and the format matches
# retail (256 entries of 3 floats + pad). Mine is white in all 256; retail's has
# 233 distinct ones. If the final colour were only palette[index], the
# generated city would be WHITE, not black - the index could not escape the
# table. It is black, so what fails is before the lookup: with no block writing
# ITOP+2, the field that becomes VI10 is another system's garbage, not a small
# byte. This is one more reason for the paired test, not a proof.
#
# Turned on by --normals on the command line; the default stays off.
mc2.NORMALS = False


def build_place(out_path, donor, place, models_dir, n_slots=1329,
                   n_groups=18, material_index=1, limit=None, only_hood=None,
                   donor_shader=None, max_inst=None, slot_shader=None,
                   texture_donor=None, hoods=None):
    """A city from place.tsv, with the real geometry."""
    globals()['TOKEN_SHIFT'] = donor_token(donor)
    lines, extents = read_place(place)
    comps = [l for l in lines if l['kind'] == 'comp']
    insts = [l for l in lines if l['kind'] == 'inst']
    # THE INSTANCE BUDGET IS THE DONOR'S. See donor_instances().
    ceiling = max_inst if max_inst else donor_instances(donor)
    if ceiling and len(insts) > ceiling:
        print('  instances: %d in place.tsv, cut to %d (the donor budget)'
              % (len(insts), ceiling))
        insts = insts[:ceiling]
    if only_hood:
        comps = [l for l in comps if l['hood'] == only_hood]
        insts = [l for l in insts if l['hood'] == only_hood]
    if limit:
        comps = comps[:limit]
    if not comps:
        raise SystemExit('no `comp` line selected in %s' % place)

    # THE GRID COMES FROM THE CITY'S EXTENTS, not from the donor. Without this,
    # LA (which asks for 91x62, origin -50,-43) inherited atlanta's 64x62 grid
    # and the culling came out shifted 17 cells in x.
    if extents.get('extents_min') and extents.get('extents_max'):
        mn = [float(v) for v in extents['extents_min'][:3]]
        mx = [float(v) for v in extents['extents_max'][:3]]
        grid = grid_from_extents(mn, mx)
    else:
        grid = donor_grid(donor)

    # THE HOODS ARE THE DONOR'S, IN NAME AND IN NUMBER.
    #
    # This was already recorded on the cube path and I did not apply it here: a
    # hood name appears in `.loc`, in race data, in the `_bnd` and - measured now
    # - in the path that places the PROPS. MC2's LA has 12 hoods
    # (bh dt gh hl hw i05 i10 101 105 ind lax sm) and atlanta has 7
    # (bh dt fwy lf mt ug we); emitting LA's 12, atlanta's props landed in a hood
    # that does not exist on their side. With the props disabled the same file
    # runs clean (1440 frames, the four counters at zero), which isolates the
    # hood/props interaction.
    #
    # The geometry does not move: only the GROUPING by hood changes. Each MC2
    # hood is mapped to a donor hood, in order, cycling.
    from_donor = hood_names_(donor)
    hoods_mc2 = sorted({l['hood'] for l in comps} | {l['hood'] for l in insts})
    # MATCH BY NAME when --hoods brings the real names. The old map distributed
    # by POSITION in the donor's list, which is arbitrary and became visible when
    # a place with a single hood sent the 34 downtown components to 'bh'
    # (from_donor[0]) instead of 'dt'. The twelve names city_slot6 registers are
    # exactly LA's twelve hoods, so there is a one-to-one match; position is only
    # a fallback.
    ABBREV = {'beverlyhills': 'bh', 'downtown': 'dt', 'ghetto': 'gh',
             'hills': 'hl', 'hollywood': 'hw', 'i05': 'i05', 'i10': 'i10',
             'i101': '101', 'i105': '105', 'industrial': 'ind',
             'lax': 'lax', 'santamonica': 'sm'}
    declared = list(hoods) if hoods else list(from_donor)
    hood_map = {}
    for i, h in enumerate(hoods_mc2):
        short = ABBREV.get(h[2:] if h.startswith('l_') else h)
        hood_map[h] = (short if short in declared
                        else declared[i % len(declared)])

    e = Writer()
    # LAYOUT CONTROL. Dead bytes right after the MapRoot, only to shift
    # everything that follows without changing A SINGLE BYTE of content. It exists
    # because two of my changes hung on the first frame (align=16 on the meshes
    # and the ITOP+2 stream) and what they have in common is shifting the whole
    # layout -- this is the experiment that separates "the content is wrong" from
    # "the file cannot change size". Useless in production.
    if TEST_PADDING:
        e.put(bytes([0xCD]) * TEST_PADDING, 'test_padding')

    # ---------------------------------------------------------------- shader
    # the shader can come from ANOTHER donor: tokyo has no shader of the typical
    # kind (sub-object at +0x10), so it serves as the base for everything else
    # but not for this piece
    _g, came_from = emit_shader_groups(e, donor_shader or donor,
                                      n_groups, n_slots, slot_shader,
                                      material_index)



    # ------------------------------------------------------------- geometry
    by_hood = {}
    chain = []                       # the ComponentData, in file order
    read_n = skipped = 0
    verts = 0
    for i, l in enumerate(comps):
        p = mesh_path(models_dir, l)
        if not p:
            skipped += 1
            continue
        try:
            blocks, world = read_world_component(p)
        except Exception as ex:                       # a mesh the reader refuses
            print('  skipped %s: %s' % (os.path.basename(p), ex))
            skipped += 1
            continue
        if not blocks:
            skipped += 1
            continue
        # world is the centre removed FROM THE PART, not the whole object's box.
        # With --pck-mesh pointing to a FOLDER, each component uses the .pck the
        # game assembled for IT (<model name>.mesh.pck). A component with no file
        # falls back to hand emission, and the report says how many.
        global GAME_PACKETS
        if PCK_FOLDER:
            # CASE-INSENSITIVE: the .hood names have capitals the files do not
            # (l_downtown_blk_staplesCntr_x#geom), and the exact lookup lost that
            # component silently - 33 of 34.
            base_name = os.path.splitext(os.path.basename(p))[0].lower()
            GAME_PACKETS = PCK_PER_MODEL.get(base_name)
            if GAME_PACKETS:
                from_game[0] += 1
            globals()['GAME_GROUPS'] = GROUPS_PER_MODEL.get(base_name)
            globals()['GAME_INDICES'] = (
                texture_slots(base_name, len(GROUPS_PER_MODEL[base_name]))
                if (base_name in GROUPS_PER_MODEL) else None)
        map_key, radius, off_cd = emit_geometry(e, blocks, 'c%d' % i, world,
                                              material_index)
        by_hood.setdefault(l['hood'], []).append((map_key, radius))
        chain.append((map_key, off_cd))
        verts += sum(len(b[0]) for b in blocks)
        read_n += 1

    # THE ComponentData ARE A LINKED LIST, and without it only the first one is placed.
    #
    # Measured on atlanta: `CullableTypeFixup` (0x24AE58) does
    # `while (v3) { ctor(v3); v3 = *(v3+0x0C); }` starting from the head at
    # `MapRoot+0x194`, and that head points to the **LAST** ComponentData of the
    # file; `+0x0C` walks backwards to the first one, whose `+0x0C` is null.
    # That is 651 nodes and atlanta's 651 components.
    #
    # With the head at the first one and `+0x0C` null (what I did), the loop
    # runs ONCE: measured in the savestate of the hang, 1 of the 298
    # ComponentData had its mesh pointer relocated and 297 were untouched.
    #
    #   +0x04  the owner, which is the MapRoot+0x194 structure itself
    #   +0x08  this object's name (itself + 0x70)
    #   +0x0C  the PREVIOUS  (null in the first)  <- how the loop walks
    #   +0x10  the NEXT      (null in the last)
    #   +0x14  the previous again
    for k, (map_key, off_cd) in enumerate(chain):
        e.point(off_cd + 0x04, 'extra_194')
        e.point(off_cd + 0x08, map_key + '_name')
        if k:
            e.point(off_cd + 0x0C, chain[k - 1][0])
            e.point(off_cd + 0x14, chain[k - 1][0])
        if k + 1 < len(chain):
            e.point(off_cd + 0x10, chain[k + 1][0])

    # the component records HAVE to be contiguous per hood: the hood entry
    # keeps [count][pointer to the array]
    comp_vas = []
    cells_per_unique = {}
    # regroup the components by TARGET hood, so they stay contiguous
    # THE HOOD NAMES ARE A CONTRACT with whoever registers the city, which is why
    # the declared list has to be known ALREADY HERE: this is where the
    # components become contiguous blocks per hood, and relocating later no
    # longer touches the blocks already emitted. city_slot6.mod registers losangeles
    # with TWELVE hoods; a .pck declaring the donor's seven left the game in
    # ENDLESS LOADING - measured in a savestate with the game alive at 59.9 Hz and
    # city 5 requested on both sinks. Same class as cell_count.
    hood_names = list(hoods) if hoods else list(from_donor)
    by_dest = {}
    orphans = set()
    for h, entries_ in by_hood.items():
        d = hood_map.get(h, h)
        if d not in hood_names:
            orphans.add(d)
            d = hood_names[0]
        by_dest.setdefault(d, []).extend(entries_)
    by_hood = by_dest
    if orphans:
        print('  hood(s) outside the declared list, moved to %s: %s'
              % (hood_names[0], ', '.join(sorted(orphans))))
    for hood, entries_ in by_hood.items():
        base = None
        for k, (map_key, radius) in enumerate(entries_):
            comp = bytearray(COMPONENT_SIZE)
            struct.pack_into('<f', comp, 0x18, radius)
            struct.pack_into('<I', comp, 0x10, tok(VT_COMPONENT_TOKEN))
            comp[0x0B] = 8                      # see the note in build()
            cd_off = e.marks[map_key]
            cx = struct.unpack_from('<f', e.d, cd_off + 0x2C)[0]
            cz = struct.unpack_from('<f', e.d, cd_off + 0x34)[0]
            x0, x1, z0, z1 = cell_box(grid, cx, cz, radius, radius)
            comp[0x14], comp[0x15], comp[0x16], comp[0x17] = x0, x1, z0, z1
            gw = grid[0]
            cells_per_unique[len(comp_vas)] = [z * gw + x
                                            for z in range(z0, z1 + 1)
                                            for x in range(x0, x1 + 1)]
            off = e.put(bytes(comp), 'comp_%s_%d' % (hood, k), align=4)
            if base is None:
                base = off
                e.label('comps_%s' % hood, off)
            e.point(off + 0x0C, map_key)
            # and the way back: the ComponentData points to the entry that reaches it
            e.point(e.marks[map_key] + 0x18, 'comp_%s_%d' % (hood, k))
            comp_vas.append(e.va(off))

    # ------------------------------------------------------------- instances
    (inst_vas, cells_per_inst, by_hood_inst,
     kind_head, n_kinds) = emit_instances(e, insts, models_dir, grid,
                                              material_index, hood_map)

    # ----------------------------------------------------------------- hoods
    # ALL of the donor's hoods exist, in its order, with its names. A hood
    # without geometry keeps a zero count and an empty array - that is what
    # retail does, and what the cube path already did.
    # THE HOOD NAMES ARE A CONTRACT with whoever registers the city.
    # city_slot6.mod registers losangeles with TWELVE hoods; if the .pck declares
    # the donor's seven, the game asks for a hood that does not exist and stays
    # in ENDLESS LOADING - measured on 03/09/2026 in a savestate showing the game
    # alive at 59.9 Hz, with city 5 requested on both sinks and never getting in.
    # Same class as cell_count: a contract between files, not an internal detail.
    # The indexing order IS the declared list: the game asks for the hood by
    # INDEX, so keying on the donor's list blew up as soon as --hoods brought
    # more names than it has. A component whose hood is not in the list goes to
    # the first one, so it does not vanish silently.
    order = list(hood_names)
    choice = hood_donors(donor, len(hood_names))
    entries = bytearray()
    for i, nm in enumerate(hood_names):
        which, last = choice[i]
        hood_record(e, donor, nm, 'hood_rec%d' % i, which, last)
        h = order[i]
        ent = bytearray(HOOD_ENTRY_SIZE)
        struct.pack_into('<I', ent, 0x04, len(by_hood.get(h, [])))       # unique_count
        struct.pack_into('<I', ent, 0x0C,
                         by_hood_inst.get(h, ('', 0))[1])                # instance_count
        entries += ent
    off_ent = e.put(bytes(entries), 'hoods')
    empty = empty_hood(e)
    for i, h in enumerate(order):
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 0, 'hood_rec%d' % i)
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 8,
                 'comps_%s' % h if h in by_hood else empty)
        e.point(off_ent + HOOD_ENTRY_SIZE * i + 0x10,
                 by_hood_inst[h][0] if h in by_hood_inst else empty)

    # --------------------------------------------------------------- MapRoot
    r = 0x80
    e.d[r:r + MAPROOT_SIZE] = maproot_base(donor)
    inline_palettes(e, r)
    e.u32(r + 0x00, tok(VT_MAPROOT))
    e.u32(r + 0x08, n_groups)
    e.point(r + 0x0C, 'groups')
    empty_array(e, 'field10')
    e.point(r + 0x10, 'field10')
    e.u32(r + 0x14, len(hood_names))
    e.point(r + 0x18, 'hoods')
    gw, gh, ox, oz = grid
    scratch = gw * gh
    struct.pack_into('<iiii', e.d, r + 0x26C, gw, gh, ox, oz)   # grid + origin
    e.u32(r + 0x268, scratch)                                   # == grid_w*grid_h
    block_1c(e, donor, 'field1c', comp_vas, inst_vas, scratch,
             cells_per_unique, cells_per_inst)
    e.point(r + 0x1C, 'field1c')
    e.point(r + 0x2C, 'paletteA')
    e.point(r + 0x30, 'paletteB')
    if extents.get('extents_min') and extents.get('extents_max'):
        for k, field in ((0x238, 'extents_min'), (0x244, 'extents_max')):
            for j, v in enumerate(extents[field][:3]):
                struct.pack_into('<f', e.d, r + k + 4 * j, v)
    # the head is the LAST one of the chain, not the first
    made = extra_structures(e, donor,
                              chain[-1][0] if chain else None,
                              kind_head,
                              n_comp=len(comp_vas), n_inst=len(inst_vas))
    n_chains = chains_1d4(e, donor, texture_donor)

    n = e.close(out_path)
    print('written %s (%d bytes)' % (out_path, n))
    print('  %d components written, %d skipped, %d vertices'
          % (read_n, skipped, verts))
    if PCK_FOLDER:
        print('  with game packets: %d (components + instance kinds)'
              % from_game[0])
    print('  %d hood(s): %s'
          % (len(hood_names),
             ', '.join('%s=%d' % (hood_names[i], len(by_hood.get(h, [])))
                       for i, h in enumerate(order))))
    print('  shader copied from donor slot %d; %d slots in group 0'
          % (came_from, n_slots))
    print('  extra structures: %s; %d chain(s) of +0x1D4/+0x1D8'
          % (', '.join('+0x%X' % x for x in made), n_chains))
    return n


def compdata(radius, position, name=b'generated_00'):
    """A ComponentData with the CONSTANT fields all 651 in atlanta have.

    Leaving this zeroed is what produced the `R5900 Exception: Jump to unaligned
    address` when getting close to the model: **`+0x28` is a vtable TOKEN, the
    same (`0x0079FF88`) in all 651 ComponentData of the file**, and `+0x00` is 1
    in all of them. With zero there, the first virtual call on the component
    jumps through a pointer read from garbage - and the address is not even
    aligned.

    The pointers at `+0x04..+0x18` vary per component (`+0x08` points to the
    name, `+0x10` to another ComponentData) and stay null here: there is at
    least one retail component with nulls at `+0x0C/+0x10/+0x14`, so null is
    tolerated there.
    """
    cd = bytearray(0xF0)
    struct.pack_into('<I', cd, 0x00, 1)
    struct.pack_into('<I', cd, 0x28, tok(VT_COMPDATA_TOKEN))
    struct.pack_into('<3f', cd, 0x2C, *position)
    struct.pack_into('<f', cd, 0x38, radius)
    cd[0x70:0x70 + len(name)] = name
    # +0x1C, +0x20 and +0x24 are 0xCDCDCDCD in 23,814 of 23,814 retail
    # ComponentData, and still stay ZERO here, by the user's decision: 0xCD is
    # the UNINITIALISED MEMORY pattern, and imitating 'uninitialised' in a
    # generated file copies retail's appearance without copying its meaning.
    # Tested with CD: it changed nothing.
    return bytes(cd)


# THE MESH EMBEDDED AT ComponentData+0x90.
#
# Measured: in 584 of tokyo's 650 ComponentData, the word at +0x90 is the MESH
# TOKEN, and the fields +0x98..+0xAC are byte for byte the fields +0x08..+0x1C
# of a mesh - I lined the two up in a real component and the overlap is exact,
# with mesh+0x00 landing on CD+0x90. So the ComponentData CARRIES A MESH INSIDE
# IT, and in 431 of the 650 its own +0x3C points to that inner mesh instead of
# to a separate object.
#
# My builder always allocated the mesh outside and left +0x90 zeroed. Writing
# the embedded mesh fills +0x90, +0x98, +0x9C, +0xA0, +0xA8 and +0xAC at once
# with values whose semantics I know entirely, because it is the same mesh
# header I already emit - and it puts the component in retail's MAJORITY form.
MESH_SIZE = 0x20
OFF_EMBEDDED_MESH = 0x90


def put_embedded_mesh(e, off_cd, key_mt, key_gt, blocks, map_key, slot_count=1):
    """Write the mesh header inside the ComponentData and point +0x3C to it."""
    m = off_cd + OFF_EMBEDDED_MESH
    # THE COUNT HAS TO BE THAT OF THE TABLE THIS MESH REUSES, not a fixed 1. This
    # mesh points `+0x10` to `key_gt`, which on the grouped path has
    # `len(GAME_GROUPS)` entries - and the ctor `rmcModelGeom(datResource&)` uses
    # ONLY this u16 to know how many `rmcGeometry` to relocate. With 1 here and N
    # in the table, the remaining N-1 groups keep the raw file offset in place of
    # the pointer and their geometry draws from the start of RAM. The comment
    # below had already caught the N for the CPV chain; it was missing here.
    n_groups = len(GAME_GROUPS) if GAME_GROUPS else 1
    struct.pack_into('<IIHHIIII', e.d, m,
                     tok(VT_MESH_TOKEN), 0, n_groups, slot_count, 0, 0, 0, 1)
    e.point(m + 0x0C, key_mt)
    e.point(m + 0x10, key_gt)
    # This mesh REUSES the component's group table (key_gt), so its chain has to
    # match THAT table and not my blocks. With --pck-mesh the table holds the
    # game's packets; building the chain from `blocks` produced 101 entries for
    # a list of 26.
    # The chain has to match the group table this mesh REUSES. With GAME_GROUPS
    # the table has N groups, so the chain needs N as well - building it from
    # the flattened blocks gave 82 of 298 invalid meshes.
    if GAME_GROUPS:
        cpv_groups = [[([0] * nv,) for _p, _q, nv in pac] for pac in GAME_GROUPS]
    else:
        cpv_groups = [([0] * nv,) for _p, _q, nv in GAME_PACKETS]             if GAME_PACKETS else blocks
        cpv_groups = [cpv_groups]
    emit_per_vertex_chain(e, cpv_groups, map_key + '_t14', slot_count=slot_count)
    e.point(m + 0x14, map_key + '_t14')
    e.u32(off_cd + 0x3C, e.va(m))


def generated_geometry(p, blocks):
    """Append mesh + ComponentData + component to an existing .pck.

    Returns (off_mesh, off_cd, off_comp, radius). Nothing in the file moves:
    everything is APPENDED and only the new fields point here.
    """
    scale = mc2.pick_scale(blocks)
    pks = [mc2.make_block(pos, uv, nr, scale, i % 2 == 1)
           for i, (pos, uv, nr, _o) in enumerate(blocks)]
    offs = [p.append(pk) for pk in pks]
    items = b''.join(struct.pack('<IHH', p.V(o), (len(pk) + 15) // 16, len(bl[0]))
                     for o, pk, bl in zip(offs, pks, blocks))
    list_va = p.V(p.append(items))
    off_gt = p.append(struct.pack('<IHH', 0, len(blocks), 0))
    struct.pack_into('<I', p.data, off_gt, list_va)
    off_mt = p.append(struct.pack('<H', 1) + bytes([0, 0]))
    off_mesh = p.append(struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0, 1,
                                    CPV_SLOTS_PER_COMPONENT, 0, 0, 0, 1))
    struct.pack_into('<I', p.data, off_mesh + 0x0C, p.V(off_mt))
    struct.pack_into('<I', p.data, off_mesh + 0x10, p.V(off_gt))
    off_t14 = append_per_vertex_chain(p, [blocks])
    struct.pack_into('<I', p.data, off_mesh + 0x14, p.V(off_t14))

    pts = [c for pos, _u, _n, _o in blocks for c in pos]
    radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
    off_cd = p.append(compdata(radius, (-400.0, 5.0, 700.0)))
    struct.pack_into('<I', p.data, off_cd + 0x3C, p.V(off_mesh))

    comp = bytearray(COMPONENT_SIZE)
    # +0x10 is the type_va, and it was MISSING here - the hood1 graft wrote zero
    # and hung with PC=0x00650073 (UTF-16 text), a different failure from the
    # builder's. Without this the graft tested its own bug, not the file's.
    struct.pack_into('<I', comp, 0x10, tok(VT_COMPONENT_TOKEN))
    struct.pack_into('<f', comp, 0x18, radius)
    off_comp = p.append(bytes(comp))
    struct.pack_into('<I', p.data, off_comp + 0x0C, p.V(off_cd))
    return off_mesh, off_cd, off_comp, radius


def graft(base, out_path, piece, side=40.0):
    """Differential bisection: a file that LOADS, with ONE generated piece.

    Building from scratch fails and does not say where. Starting from the real
    file and swapping one piece at a time makes each boot rule out half the
    space instead of testing one field. And the criterion does not even need
    the screen: the savestate says **how many of the MapRoot's six pointers were
    relocated**, and in a file that loads it is all of them.

    Nothing already in the file moves - the new pieces are APPENDED and only the
    MapRoot pointers are rewritten. It is the same discipline as the hood fill.
    """
    # The graft's token space is the BASE's, not atlanta's where the constants
    # were measured. Without this the graft writes atlanta tokens into a tokyo
    # file and stops being a clean test.
    globals()['TOKEN_SHIFT'] = donor_token(base)
    p = mc2.Pck(base)
    r = 0x80

    def put(blob, align=16):
        return p.V(p.append(blob, align))

    if piece == 'palettes':
        struct.pack_into('<I', p.data, r + 0x2C, put(grey_palette()))
        struct.pack_into('<I', p.data, r + 0x30, put(grey_palette()))
        note = 'both palettes replaced by generated ramps'

    elif piece == 'group':
        # THE SHADER GROUP DESCRIPTOR the way a from-scratch file builds it: ONE
        # group of 64 slots instead of retail's 18 of 1329. The shader objects
        # stay the file's own - only the table's shape changes, which is what has
        # not been isolated yet.
        n_groups = struct.unpack_from('<I', p.data, r + 0x08)[0]
        base_desc = p.F(struct.unpack_from('<I', p.data, r + 0x0C)[0])
        data = p.F(struct.unpack_from('<I', p.data, base_desc + 4)[0])
        n0 = struct.unpack_from('<H', p.data, base_desc + 8)[0]
        live = []
        for i in range(n0):
            v = struct.unpack_from('<I', p.data, data + 4 * i)[0]
            if v and p.inside(p.F(v)):
                live.append(v)
            if len(live) == 64:
                break
        arr = put(b''.join(struct.pack('<I', v) for v in live))
        desc = put(struct.pack('<IIHHI', tok(VT_ARRAY), 0, len(live), len(live), 0))
        struct.pack_into('<I', p.data, p.F(desc) + 4, arr)
        struct.pack_into('<I', p.data, r + 0x08, 1)
        struct.pack_into('<I', p.data, r + 0x0C, desc)
        note = ('%d shader groups became 1 group of %d slots'
                % (n_groups, len(live)))

    elif piece == 'extras':
        # THE FOUR STRUCTURES AT +0x190/+0x194/+0x198/+0x228, the way a
        # from-scratch file builds them: COPIED from the file itself, with the
        # COUNTS ZEROED, and the +0x194 head pointing to the same ComponentData
        # chain that is already there.
        #
        # It is the piece no earlier graft tested. Shader passed, palettes
        # passed, and the hood ones loaded and only broke when getting close -
        # so what is left of a generated file, and was never isolated, is this
        # block of zeroed counts.
        made = []
        for off, _who, counts, size_ in EXTRA_STRUCTURES:
            va = struct.unpack_from('<I', p.data, r + off)[0]
            if not va or not p.inside(p.F(va)):
                continue
            o = p.F(va)
            blob = bytearray(p.data[o:o + size_])
            before = [struct.unpack_from('<I', blob, c)[0] for c in counts]
            for c in counts:
                struct.pack_into('<I', blob, c, 0)
            struct.pack_into('<I', p.data, r + off, put(bytes(blob)))
            made.append('+0x%X %s->0' % (off, before))
        note = 'extra structures copied with zeroed counts: %s' % ', '.join(made)

    elif piece == 'shaders':
        # MIRRORS THE RETAIL PATTERN, instead of "filling everything".
        #
        # The loop at 0x2AF9E8 has no "already placed" guard: it runs the handler
        # once per SLOT. In retail each object appears in EXACTLY one slot (4117
        # objects in 4117 slots, measured) and the rest is null. Filling every
        # slot with the same shader - which I did thinking it avoided nulls - puts
        # the same object in thousands of slots, and the handler re-adds the
        # delta to its internal pointers once per slot.
        #
        # So: where retail has an object, put a NEW copy; where it has null,
        # leave null.
        block, k = shader_block(base)
        n_groups = struct.unpack_from('<I', p.data, r + 0x08)[0]
        base_desc = p.F(struct.unpack_from('<I', p.data, r + 0x0C)[0])
        arrays, made_n = [], 0
        for g in range(n_groups):
            data = p.F(struct.unpack_from('<I', p.data, base_desc + 16 * g + 4)[0])
            n = struct.unpack_from('<H', p.data, base_desc + 16 * g + 8)[0]
            slots = []
            for i in range(n):
                v = struct.unpack_from('<I', p.data, data + 4 * i)[0]
                if v and p.inside(p.F(v)):
                    o = p.append(block)
                    struct.pack_into('<I', p.data, o + 8, p.V(o + 0x10))
                    slots.append(p.V(o))
                    made_n += 1
                else:
                    slots.append(0)
            arrays.append(put(b''.join(struct.pack('<I', x) for x in slots)))
        desc = b''.join(struct.pack('<IIHHI', tok(VT_ARRAY), 0,
                                    struct.unpack_from('<H', p.data, base_desc + 16 * g2 + 8)[0],
                                    struct.unpack_from('<H', p.data, base_desc + 16 * g2 + 8)[0], 0)
                        for g2 in range(n_groups))
        off_g = p.append(desc)
        for g in range(n_groups):
            struct.pack_into('<I', p.data, off_g + 16 * g + 4, arrays[g])
        struct.pack_into('<I', p.data, r + 0x0C, p.V(off_g))
        note = ('%d groups, %d new copies (one per slot retail fills)'
                % (n_groups, made_n))

    elif piece == 'hood2':
        # One step BEFORE hood1: keep the ORIGINAL component and only repoint
        # its `+0x0C` to our ComponentData. The trio then is:
        #   cubos_ug  original comp + original CD + our geometry  -> LOADS
        #   hood2     original comp + OUR CD      + our geometry  -> ?
        #   hood1     OUR comp      + OUR CD      + our geometry  -> ?
        # Whichever hangs first points at the exact piece.
        hb = p.F(struct.unpack_from('<I', p.data, r + 0x18)[0])
        nh = struct.unpack_from('<I', p.data, r + 0x14)[0]
        target = 0
        for h in range(nh):
            rec = p.F(struct.unpack_from('<I', p.data, hb + 20 * h)[0])
            if bytes(p.data[rec:rec + 3]).startswith(b'ug'):
                target = h
                break
        au = p.F(struct.unpack_from('<I', p.data, hb + 20 * target + 8)[0])
        _m, off_cd, _c, _r = generated_geometry(p, cube(side))
        struct.pack_into('<I', p.data, au + 0x0C, p.V(off_cd))   # 1st component
        struct.pack_into('<I', p.data, hb + 20 * target + 4, 1)    # only it
        note = 'hood %d: ORIGINAL component pointing to our ComponentData' % target

    elif piece == 'hood1':
        # MINIMUM DELTA from what already works.
        #
        # `cubos_ug` loads: 7 original hoods, ORIGINAL components, only the
        # geometry swapped. `zero6` hangs: 7 hoods with the right names but
        # GENERATED components. The step between the two is this one: keep the
        # 7 hoods and the target hood intact, and swap only its UNIQUES ARRAY for
        # a component + ComponentData generated by us. If it hangs, the problem is
        # in the generated component; if it loads, it is elsewhere in the
        # from-scratch file.
        hb = p.F(struct.unpack_from('<I', p.data, r + 0x18)[0])
        nh = struct.unpack_from('<I', p.data, r + 0x14)[0]
        target = None
        for h in range(nh):
            rec = p.F(struct.unpack_from('<I', p.data, hb + 20 * h)[0])
            if bytes(p.data[rec:rec + 3]).startswith(b'ug'):
                target = h
                break
        if target is None:
            target = 0
        off_mesh, off_cd, off_comp, radius = generated_geometry(p, cube(side))
        struct.pack_into('<I', p.data, hb + 20 * target + 4, 1)          # 1 unique
        struct.pack_into('<I', p.data, hb + 20 * target + 8, p.V(off_comp))
        note = ('hood %d intact, uniques array replaced by 1 generated component'
                % target)

    elif piece == 'hood':
        blocks = cube(side)
        scale = mc2.pick_scale(blocks)
        pks = [mc2.make_block(pos, uv, nr, scale, i % 2 == 1)
               for i, (pos, uv, nr, _o) in enumerate(blocks)]
        offs = [p.append(pk) for pk in pks]
        items = b''.join(struct.pack('<IHH', p.V(o), (len(pk) + 15) // 16, len(bl[0]))
                         for o, pk, bl in zip(offs, pks, blocks))
        list_va = put(items)
        off_gt = p.append(struct.pack('<IHH', 0, len(blocks), 0))
        struct.pack_into('<I', p.data, off_gt, list_va)
        off_mt = p.append(struct.pack('<H', 1) + bytes([0, 0]))
        # This old probe does not emit +0x14: disable CPV explicitly.
        off_mesh = p.append(struct.pack('<IIHHIIII', tok(VT_MESH_TOKEN), 0, 1, 0, 0, 0, 0, 1))
        struct.pack_into('<I', p.data, off_mesh + 0x0C, p.V(off_mt))
        struct.pack_into('<I', p.data, off_mesh + 0x10, p.V(off_gt))

        pts = [c for pos, _u, _n, _o in blocks for c in pos]
        radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
        off_cd = p.append(compdata(radius, (-400.0, 5.0, 700.0)))
        struct.pack_into('<I', p.data, off_cd + 0x3C, p.V(off_mesh))

        comp = bytearray(COMPONENT_SIZE)
        struct.pack_into('<f', comp, 0x18, radius)
        off_comp = p.append(bytes(comp))
        struct.pack_into('<I', p.data, off_comp + 0x0C, p.V(off_cd))

        # record: a copy of the file's own first hood, with the name changed
        hb = p.F(struct.unpack_from('<I', p.data, r + 0x18)[0])
        rec0 = p.F(struct.unpack_from('<I', p.data, hb)[0])
        blob = bytearray(p.data[rec0:rec0 + 0xB0])
        blob[0:4] = b'zz' + bytes(2)
        chain = bytes(p.data[rec0 + 0xB4:rec0 + 0xB4 + 0xA0])
        off_rec = p.append(bytes(blob))
        va_cad = put(chain)
        struct.pack_into('<I', p.data, off_rec + 0xAC, va_cad)

        # the INSTANCE array has to exist even with a zero count: a null pointer
        # here is the same trap as MapRoot+0x10 (on the PS2 address 0 reads, it
        # does not fault, and Place wanders off into garbage)
        va_inst = put(bytes(96))
        ent = bytearray(HOOD_ENTRY_SIZE)
        struct.pack_into('<I', ent, 4, 1)
        struct.pack_into('<I', ent, 16, va_inst)
        off_ent = p.append(bytes(ent))
        struct.pack_into('<I', p.data, off_ent + 0, p.V(off_rec))
        struct.pack_into('<I', p.data, off_ent + 8, p.V(off_comp))
        struct.pack_into('<I', p.data, r + 0x14, 1)
        struct.pack_into('<I', p.data, r + 0x18, p.V(off_ent))
        note = '1 hood generated with 1 cube; the %d original hoods stay in the file but out of the table' % 0
    else:
        raise SystemExit('unknown piece: %s' % piece)

    p.save(out_path)
    print('%-10s -> %s (%d bytes)  %s' % (piece, os.path.basename(out_path), len(p.data), note))


def verify(file_path):
    """Reopen the file with the readers that already exist in the toolkit.

    It does not prove the game accepts it - it proves the structure agrees with
    the reading that was validated on the retail files, which is the only
    automatic test possible before booting.
    """
    spec = importlib.util.spec_from_file_location(
        'hf', os.path.join(_HERE, 'mc3_hood_fill.py'))
    hf = importlib.util.module_from_spec(spec)
    sys.modules['hf'] = hf
    spec.loader.exec_module(hf)

    p = mc2.Pck(file_path)
    base, kind, ver, body = struct.unpack_from('<4I', p.data, 0)
    print('%s' % os.path.basename(file_path))
    print('  header: base %08X, kind %d, version %d, body %d (file %d)'
          % (base, kind, ver, body, len(p.data)))
    ok = True
    if kind != CITY_KIND:
        print('  ERROR: kind %d, expected %d - the loader fails SILENTLY'
              % (kind, CITY_KIND))
        ok = False
    if body != len(p.data) - 0x80:
        print('  ERROR: declared body does not match the file')
        ok = False
    if len(p.data) % 16:
        print('  ERROR: file is not aligned to 16')
        ok = False

    hs = hf.hoods(p)
    print('  %d hood(s): %s' % (len(hs), ', '.join(h[0] for h in hs)))
    for i, h in enumerate(hs):
        u = hf.hood_uniques(p, i)
        print('    [%d] %-6s %d unique, %d instance -> %d readable component(s)'
              % (i, h[0], h[1], h[3], len(u)))
        for nm, meshes, comp, cd in u:
            pos = struct.unpack_from('<3f', p.data, cd + 0x2C)
            radius = struct.unpack_from('<f', p.data, cd + 0x38)[0]
            for m in meshes:
                ab = hf.aabb_local(p, m)
                ng = struct.unpack_from('<H', p.data, m + 8)[0]
                print('       %-12s pos (%.1f %.1f %.1f) radius %.1f, %d group(s), AABB %s'
                      % (nm, pos[0], pos[1], pos[2], radius, ng,
                         ('%.1f x %.1f x %.1f' % tuple(ab[1][k] - ab[0][k] for k in range(3)))
                         if ab else 'UNREADABLE'))
                # An unreadable AABB is NOT an error: atlanta itself in retail
                # has 18 meshes like that (empty group or a layout the reader
                # does not cover), and the game loads. Count, do not fail.
    # MATERIAL INDEX AGAINST THE GROUP SIZE. `rmcModel::FixupShaders`
    # (0x2A8A18) walks the mesh's groups and does
    #
    #     if (*(u16*)(group+8) < material_index[i])  while (1) ;
    #
    # so an index larger than the group's COUNT hangs the game in an empty loop
    # - the exact signature of "endless loading". Nothing is fixed at 1329:
    # atlanta sizes 1329 and uses up to 1328, tokyo sizes 1720 and uses up to
    # 1719. The table is the size of the largest index plus one.
    ng_mr = struct.unpack_from('<I', p.data, 0x80 + 0x08)[0]
    if ng_mr:
        base_g = p.F(struct.unpack_from('<I', p.data, 0x80 + 0x0C)[0])
        smallest = min(struct.unpack_from('<H', p.data, base_g + 16 * g + 8)[0]
                    for g in range(ng_mr))
        largest = 0
        for i in range(len(hs)):
            for _n, meshes, _c, _cd in hf.hood_uniques(p, i):
                for m in meshes:
                    n = struct.unpack_from('<H', p.data, m + 8)[0]
                    mt = p.F(struct.unpack_from('<I', p.data, m + 0x0C)[0])
                    if not p.inside(mt):
                        continue
                    for k in range(n):
                        largest = max(largest, struct.unpack_from('<H', p.data,
                                                              mt + 2 * k)[0])
        print('  material: largest index used %d, smallest group has %d slot(s)'
              % (largest, smallest))
        if largest > smallest:
            print('     index above the count: FixupShaders falls into while(1)')
            ok = False

    # A NULL SLOT IN A USED GROUP = A CALL THROUGH A NULL POINTER.
    # `rmcModel::FixupShaders` dereferences array[index] and calls the virtual at
    # +24 without checking. Retail lives with nulls because each mesh only takes
    # part in the passes where its material exists; imported geometry has no such
    # guarantee. Here it only FAILS an index filled in NO group, which is an
    # error in any case, and reports the minimum as a hint when a shader hangs.
    if ng_mr:
        base_g2 = p.F(struct.unpack_from('<I', p.data, 0x80 + 0x0C)[0])
        n0 = struct.unpack_from('<H', p.data, base_g2 + 8)[0]
        full = [0] * n0
        for g in range(ng_mr):
            dd = p.F(struct.unpack_from('<I', p.data, base_g2 + 16 * g + 4)[0])
            if not p.inside(dd):
                continue
            for i in range(n0):
                if struct.unpack_from('<I', p.data, dd + 4 * i)[0]:
                    full[i] += 1
        used_set = set()
        mesh_targets = []
        for i in range(len(hs)):
            for _n, meshes, _c, _cd in hf.hood_uniques(p, i):
                mesh_targets.append(meshes)
        try:
            _sl, _tp = hf.instance_kinds(p)
            for _va, (_nm, _cd2, ms) in _tp.items():
                mesh_targets.append(ms)
        except Exception:
            pass
        for meshes in mesh_targets:
            if True:
                for m in meshes:
                    nn = struct.unpack_from('<H', p.data, m + 8)[0]
                    mt = p.F(struct.unpack_from('<I', p.data, m + 0x0C)[0])
                    if not p.inside(mt):
                        continue
                    for k in range(nn):
                        used_set.add(struct.unpack_from('<H', p.data, mt + 2 * k)[0])
        if used_set:
            worst_ = min(full[i] for i in used_set if i < n0)
            empties = [i for i in used_set if i < n0 and full[i] == 0]
            print('  shader slots: the rarest index in use exists in %d of '
                  '%d group(s)%s' % (worst_, ng_mr,
                                     '; %d in NONE' % len(empties) if empties else ''))
            if empties:
                print('     index with no shader in any group: call through a null pointer')
                ok = False

    # PER-VERTEX CHAIN OF rmcModelGeom+0x14. The Draw path builds an UNPACK S-8
    # from this structure. In a generated mesh a null pointer does not mean "no
    # attribute": NUM=0 in VIF means 256, and the DMA starts consuming the next
    # geometry packet as data until it loses alignment.
    #
    # Retail has a few special meshes without the chain, so its absence only
    # fails the ComponentData this builder marks as generated_*. Every chain
    # present, retail or generated, is still validated in full.
    required_by_kind = {}
    hb_cpv = p.F(struct.unpack_from('<I', p.data, 0x80 + 0x18)[0])
    for h_cpv in range(len(hs)):
        cpv_entry = hb_cpv + 20 * h_cpv
        n_cpv = struct.unpack_from('<I', p.data, cpv_entry + 0x0C)[0]
        arr_cpv = p.F(struct.unpack_from('<I', p.data, cpv_entry + 0x10)[0])
        if not p.inside(arr_cpv):
            continue
        for k_cpv in range(n_cpv):
            inst_cpv = arr_cpv + INSTANCE_SIZE * k_cpv
            cpv_kind = struct.unpack_from('<I', p.data, inst_cpv + 0x0C)[0]
            cpv_index = struct.unpack_from('<I', p.data, inst_cpv + 0x1C)[0]
            required_by_kind[cpv_kind] = max(
                required_by_kind.get(cpv_kind, 0), cpv_index + 1)

    chain_targets = []
    for i in range(len(hs)):
        for nm, meshes, _c, _cd in hf.hood_uniques(p, i):
            chain_targets += [(m, nm, CPV_SLOTS_PER_COMPONENT) for m in meshes]
    try:
        _sl4, _tp4 = hf.instance_kinds(p)
        for _va4, (nm4, _cd4, ms4) in _tp4.items():
            required4 = required_by_kind.get(_va4, CPV_SLOTS_PER_COMPONENT)
            chain_targets += [(m, nm4, required4) for m in ms4]
    except Exception:
        pass

    # The same mesh can be reached from more than one table; validate it once
    # and keep the largest cardinality if there are aliases.
    chain_uniques = {}
    for m, nm, required in chain_targets:
        previous = chain_uniques.get(m)
        if previous is None or required > previous[1]:
            chain_uniques[m] = (nm, required)

    errors_t14 = []
    n_valid_t14 = n_blocks_t14 = n_slot_groups_t14 = n_retail_nulls = 0
    # THE MESH'S GROUP COUNT AGAINST THE TABLE'S OWN. The constructor
    # `rmcModelGeom::rmcModelGeom(datResource&)` (retail 0x2A9D18) reads the u16
    # at `mesh+0x08` and IT ALONE decides how many `rmcGeometry` to build:
    #
    #     for (i < *(u16*)(this+8))  rmcGeometry(*(this+0x10) + i*8, res)
    #
    # and each `rmcGeometry` relocates the pointer of its own packet list. With a
    # count smaller than the real number of groups the walk stops early: the
    # remaining groups keep the raw FILE OFFSET in place of the pointer, and a
    # file offset read as an EE address lands near the start of RAM - which is
    # exactly the "several objects piled in a narrow strip" symptom. Nothing in
    # the other checks catches this, because they all use `ng` as the TRUTH to
    # size the table and only look at the groups it declares.
    #
    # The table knows its own size: `emit_grouped_mesh` writes the count as a
    # u32 16 bytes BEFORE the array, the three-level convention of the rest of
    # the file. If the two numbers differ, the file contradicts itself.
    #
    # MEASURED BEFORE COMING IN HERE, so as not to create a false positive: with
    # `mesh+0x08 != 0` the rule holds in 6152 retail meshes (atlanta 807, sd
    # 1751, detroit 1705, tokyo 1889) with ZERO mismatches. The case
    # `mesh+0x08 == 0` is left out on purpose - the ctor loop does not run, the
    # pointer is never walked and the prefix means nothing; atlanta alone has
    # 544 such meshes, all legitimate.
    errors_ng = []
    n_ng_ok = n_ng_zero = n_ng_unprefixed = 0

    def _off_va_t14(va, byte_size=1):
        if not va:
            return None
        f = p.F(va)
        if f < 0x80 or f + byte_size > len(p.data):
            return None
        return f

    def _generated_name_t14(nm):
        if isinstance(nm, bytes):
            nm = nm.decode('ascii', 'ignore')
        return str(nm).lower().startswith(('generated_', 'gerado_'))

    for m, (nm, required_slots) in sorted(chain_uniques.items()):
        if m < 0x80 or m + MESH_SIZE > len(p.data):
            errors_t14.append('mesh 0x%X outside the file' % m)
            continue
        ng, ns = struct.unpack_from('<HH', p.data, m + 8)
        gt = _off_va_t14(struct.unpack_from('<I', p.data, m + 0x10)[0], 8 * ng)
        if not ng:
            n_ng_zero += 1
        elif gt is None or gt < 0x90:
            n_ng_unprefixed += 1
        else:
            pref = struct.unpack_from('<I', p.data, gt - 16)[0]
            if not 1 <= pref <= 4096:
                n_ng_unprefixed += 1
            elif pref == ng:
                n_ng_ok += 1
            else:
                errors_ng.append(
                    'mesh 0x%X (%s) declares %d group(s) at +0x08 but the table '
                    'has %d: the ctor only relocates %d, the rest keep a raw '
                    'file offset' % (m, nm, ng, pref, ng))
        va_t14 = struct.unpack_from('<I', p.data, m + 0x14)[0]
        t14 = _off_va_t14(va_t14, 4 * ns)
        mandatory = _generated_name_t14(nm)
        slots_cpv_ok = not mandatory or ns >= required_slots
        if not slots_cpv_ok:
            errors_t14.append(
                'generated mesh 0x%X (%s) declares %d CPV slot(s); '
                'Instance+0x1C requires %d' % (m, nm, ns, required_slots))
        if not va_t14:
            if mandatory:
                errors_t14.append('generated mesh 0x%X (%s) with a null +0x14' % (m, nm))
            else:
                n_retail_nulls += 1
            continue
        if not ng or not ns or gt is None or t14 is None:
            errors_t14.append('mesh 0x%X with an invalid header/+0x14 pointer' % m)
            continue

        mesh_ok = slots_cpv_ok
        for s in range(ns):
            tab = _off_va_t14(struct.unpack_from('<I', p.data, t14 + 4 * s)[0],
                              8 * ng)
            if tab is None:
                errors_t14.append('mesh 0x%X slot %d points outside' % (m, s))
                mesh_ok = False
                continue
            for g in range(ng):
                gdesc = gt + 8 * g
                list_va, nb, _res = struct.unpack_from('<IHH', p.data, gdesc)
                n_slot_groups_t14 += 1
                if not nb:
                    continue
                items = _off_va_t14(list_va, 8 * nb)
                pa_va, pb_va = struct.unpack_from('<II', p.data, tab + 8 * g)
                pa = _off_va_t14(pa_va, 4 + 4 * nb)
                pb = _off_va_t14(pb_va, 4 * nb)
                if items is None or pa is None or pb is None:
                    errors_t14.append('mesh 0x%X slot %d group %d points outside' %
                                      (m, s, g))
                    mesh_ok = False
                    continue
                declared_n = struct.unpack_from('<I', p.data, pa)[0]
                if declared_n != nb:
                    errors_t14.append('mesh 0x%X slot %d group %d: count %d != %d' %
                                      (m, s, g, declared_n, nb))
                    mesh_ok = False
                for b in range(nb):
                    nv = struct.unpack_from('<H', p.data, items + 8 * b + 6)[0]
                    nv_t14 = struct.unpack_from('<I', p.data, pa + 4 + 4 * b)[0]
                    if nv_t14 != nv:
                        errors_t14.append(
                            'mesh 0x%X slot %d group %d block %d: vertices %d != %d'
                            % (m, s, g, b, nv_t14, nv))
                        mesh_ok = False
                    payload_va = struct.unpack_from('<I', p.data, pb + 4 * b)[0]
                    if _off_va_t14(payload_va, max(1, nv)) is None:
                        errors_t14.append(
                            'mesh 0x%X slot %d group %d block %d: payload outside'
                            % (m, s, g, b))
                        mesh_ok = False
                    n_blocks_t14 += 1
        if mesh_ok:
            n_valid_t14 += 1

    print('  mesh+0x14 chain: %d/%d valid mesh(es), %d group-slot(s), '
          '%d block(s)%s' %
          (n_valid_t14, len(chain_uniques), n_slot_groups_t14, n_blocks_t14,
           '; %d retail null(s) tolerated' % n_retail_nulls
           if n_retail_nulls else ''))
    if errors_t14:
        ok = False
        print('     %d error(s) in the per-vertex chain:' % len(errors_t14))
        for error in errors_t14[:8]:
            print('       ' + error)
        if len(errors_t14) > 8:
            print('       ... and %d more' % (len(errors_t14) - 8))

    print('  group count (mesh+0x08 vs the table): %d ok, %d mismatched'
          '%s%s'
          % (n_ng_ok, len(errors_ng),
             ', %d with +0x08=0 (the ctor loop does not run)' % n_ng_zero
             if n_ng_zero else '',
             ', %d without a readable prefix' % n_ng_unprefixed
             if n_ng_unprefixed else ''))
    if errors_ng:
        ok = False
        for error in errors_ng[:6]:
            print('       ' + error)
        if len(errors_ng) > 6:
            print('       ... and %d more' % (len(errors_ng) - 6))

    # THE CULLING GRID ORIGIN has to match the extents.
    # `mcCity::CalcAbsCellXZ` (0x257CC8) does floor(x/40) without an origin, and
    # `CalcRelCellXZ` (0x257C60) subtracts +0x274 and +0x278. In the four retail
    # cities, 8 of 8: +0x274 == floor(extents_min.x/40), +0x278 == floor(min.z/40).
    import math as _m
    _mn = struct.unpack_from('<3f', p.data, 0x80 + 0x238)
    _ox, _oz = struct.unpack_from('<2i', p.data, 0x80 + 0x274)
    _ex = int(_m.floor(_mn[0] / 40.0)); _ez = int(_m.floor(_mn[2] / 40.0))
    print('  grid: origin +0x274/+0x278 = %d, %d (the extents ask for %d, %d)'
          % (_ox, _oz, _ex, _ez))
    if (_ox, _oz) != (_ex, _ez):
        print('     cell origin does not match the extents: culling shifted '
              '%d cells in x and %d in z' % (_ex - _ox, _ez - _oz))
        ok = False

    # THE INSTANCES ARE CHAINED PER KIND. `+0x00` is the next and `+0x04` the
    # previous, and the chain only links records with the SAME `+0x0C`. Measured
    # on atlanta: 3377 links, all to the same kind, none crossed, 231 chains.
    # Repointing `+0x0C` without redoing the chains mixes the kinds and the game
    # stays in endless loading - which is what happened in the first version of
    # the instance port.
    hb3 = p.F(struct.unpack_from('<I', p.data, 0x80 + 0x18)[0])
    ins = []
    for h in range(len(hs)):
        e3 = hb3 + 20 * h
        cnt3 = struct.unpack_from('<I', p.data, e3 + 0x0C)[0]
        arr3 = p.F(struct.unpack_from('<I', p.data, e3 + 0x10)[0])
        if p.inside(arr3):
            ins += [arr3 + 96 * k for k in range(cnt3)]
    if ins:
        misaligned = [o for o in ins if (p.V(o) + 0x20) & 15]
        print('  instance Matrix34: %d/%d aligned to 16 bytes'
              % (len(ins) - len(misaligned), len(ins)))
        if misaligned:
            print('     DoSetWorld/LQ would truncate the matrix at %s'
                  % ', '.join('0x%08X' % (p.V(o) + 0x20)
                              for o in misaligned[:6]))
            ok = False
        by_va = dict((p.V(o), o) for o in ins)
        tp = dict((o, struct.unpack_from('<I', p.data, o + 0x0C)[0]) for o in ins)
        crossed = 0
        for o in ins:
            d = by_va.get(struct.unpack_from('<I', p.data, o)[0])
            if d is not None and tp[d] != tp[o]:
                crossed += 1
        print('  instances: %d in %d kind(s), %d link(s) crossing kinds'
              % (len(ins), len(set(tp.values())), crossed))
        if crossed:
            print('     instance chain mixes kinds')
            ok = False

    # THE PASS MASKS OF THE TWO CITY CLASSES. The global scheduler uses +0x00
    # directly; zero stops even RenderAllTypes from being called. All the retail
    # cities measured use 0x4E (passes 2, 4, 8 and 64).
    for off, name in ((0x194, 'mcCityModelClass'),
                      (0x198, 'mcInstCityModelClass')):
        va = struct.unpack_from('<I', p.data, 0x80 + off)[0]
        mask = None
        if va and p.inside(p.F(va)):
            mask = struct.unpack_from('<I', p.data, p.F(va))[0]
        print('  pass mask %s (+0x%X): %s'
              % (name, off,
                 'INVALID' if mask is None else '0x%08X' % mask))
        if mask != 0x4E:
            print('     expected 0x0000004E; zero removes the class from the scheduler')
            ok = False

    # THE ComponentData CHAIN. `CullableTypeFixup` (0x24AE58) walks
    # `while (v3) { ctor(v3); v3 = *(v3+0x0C); }` from the head at
    # `MapRoot+0x194`, which points to the LAST object of the file. A node
    # outside the chain is never built - and Place stops there, silently.
    #
    # Checked on retail before becoming a test: atlanta walks 651 nodes and has
    # 651 components.
    total = sum(len(hf.hood_uniques(p, i)) for i in range(len(hs)))
    o194 = struct.unpack_from('<I', p.data, 0x80 + 0x194)[0]
    n_chain, outside = 0, False
    if o194 and p.inside(p.F(o194)):
        cur = struct.unpack_from('<I', p.data, p.F(o194) + 4)[0]
        while cur and n_chain < 20000:
            f = p.F(cur)
            if not p.inside(f):
                outside = True
                break
            cur = struct.unpack_from('<I', p.data, f + 0x0C)[0]
            n_chain += 1
    print('  ComponentData chain (MapRoot+0x194): %d node(s) for %d component(s)%s'
          % (n_chain, total, '  POINTER OUTSIDE THE FILE' if outside else ''))
    if outside or (total and n_chain != total):
        print('     the chain does not cover them all: Place only builds as far as it goes')
        ok = False

    # GENERAL SWEEP: no raw RAM address should be left in the file.
    # Retail never writes a resolved vtable - always a token - and I found that
    # out field by field, burning a boot per field (ComponentData+0x28,
    # Component+0x10, Instance+0x10, kind+0x28 and finally mesh+0x00). This sweep
    # exists so the next forgotten field shows up here and not in a savestate.
    #
    # The whole 0x62xxxx-0x64xxxx range is NOT a valid test: retail tokyo has
    # 165 words there and atlanta 191, all coincidental data (vertices, floats).
    # What retail has at ZERO are the RESOLVED vtables, so the test is by exact
    # value.
    d = open(file_path, 'rb').read()
    RAW_VTABLES = {VT_COMPDATA: 'ComponentData+0x28', VT_COMPONENT: 'Component+0x10',
            VT_INST: 'Instance+0x10', VT_INST_KIND: 'instance kind+0x28',
            VT_MESH: 'mesh+0x00'}
    found = {}
    # SKIP THE VIF PAYLOADS. Vertex data produces any 32-bit value, and with
    # 3.9 MB of geometry the raw sweep reported 0x00625190 five times as an
    # "instance kind" in a city that has NO instances at all - all five fell
    # inside payload. Looking for vtables only makes sense where there is
    # structure.
    payload_ranges = []
    for o in range(0x80, len(d) - 32, 4):
        if struct.unpack_from('<I', d, o)[0] >> 20 != 0x007:
            continue
        g = struct.unpack_from('<H', d, o + 8)[0]
        gt = struct.unpack_from('<I', d, o + 0x10)[0]
        fo_gt = gt - base + 0x80
        if not (1 <= g <= 64) or not (0x80 <= fo_gt < len(d) - 8):
            continue
        # ALL the groups, not just the first. With an N-group mesh (which is what
        # texture per material requires) protecting only group 0 leaves the rest
        # of the payload exposed, and the vtable false positive comes back.
        for gi in range(g):
            ge = fo_gt + 8 * gi
            if ge + 8 > len(d):
                break
            items = struct.unpack_from('<I', d, ge)[0] - base + 0x80
            nb = struct.unpack_from('<H', d, ge + 4)[0]
            if not (0x80 <= items < len(d)) or not (1 <= nb <= 4096):
                continue
            for k in range(nb):
                e = items + 8 * k
                if e + 8 > len(d):
                    break
                dp = struct.unpack_from('<I', d, e)[0] - base + 0x80
                qw = struct.unpack_from('<H', d, e + 4)[0]
                if 0x80 <= dp < len(d) and qw:
                    payload_ranges.append((dp, min(len(d), dp + 16 * qw)))
    payload_ranges.sort()

    def in_payload(x, _f=payload_ranges):
        import bisect
        i = bisect.bisect_right(_f, (x, 1 << 30)) - 1
        return i >= 0 and _f[i][0] <= x < _f[i][1]

    for i in range(0x80, len(d) - 4, 4):
        if in_payload(i):
            continue
        w = struct.unpack_from('<I', d, i)[0]
        if w in RAW_VTABLES:
            found[w] = found.get(w, 0) + 1
    if found:
        ok = False
        print('  RESOLVED VTABLE WRITTEN TO THE FILE (retail only writes tokens):')
        for w, c in sorted(found.items(), key=lambda x: -x[1]):
            print('     0x%08X  x%-5d  %s' % (w, c, RAW_VTABLES[w]))
    else:
        print('  tokens: no resolved vtable in the file, as in retail')
    print('  %s' % ('structure is consistent' if ok else 'STRUCTURE HAS ERRORS'))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--textures', metavar='DIR',
                    help='folder of .tex.pck files the GAME built; each becomes '
                         'a shader slot and the mesh gets one group per material')
    ap.add_argument('--runtime-binding', action='store_true',
                    help='empty, marked slots, for a mod to supply the texture '
                         'later; avoids the serialised form entirely')
    ap.add_argument('--identity-map', metavar='TSV',
                    help='native_texture_map.tsv: the REAL identity of each dump '
                         '(447/510 file names are wrong)')
    ap.add_argument('--ppf-manifest', metavar='JSON',
                    help='the .ppf manifest; links the STREAMING datChunkRef '
                         'instead of the resident one')
    ap.add_argument('--texture-map', metavar='JSON',
                    help='model -> texture names in mtl order')
    ap.add_argument('--pck-mesh', metavar='PCK',
                    help='use the VIF packets of a mesh.pck that THE GAME generated '
                         '(mc3_heap_to_pck.py) instead of emitting by hand')
    ap.add_argument('--donor', help='retail city .pck: where the shader comes from')
    ap.add_argument('--out-path')
    ap.add_argument('--xmod', help='MC2 model; without it a test cube is used')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--side', type=float, default=40.0, help='side of the test cube')
    ap.add_argument('--pos', default='0,0,0')
    ap.add_argument('--hoods',
                    help='comma-separated hood names, replacing the '
                         "donor's; AT MOST THREE CHARACTERS each")
    ap.add_argument('--hood', default='zz')
    ap.add_argument('--slots', type=int, default=1329,
                    help='slots per shader group (retail: 1329)')
    ap.add_argument('--groups', type=int, default=18,
                    help='shader groups (retail: 18)')
    ap.add_argument('--max-inst', type=int, default=None,
                    help="instance ceiling (default: the donor's)")
    ap.add_argument('--material', type=int, default=1)
    ap.add_argument('--probe', action='store_true',
                    help='a city with ZERO hoods and ZERO shader groups: if it '
                         'loads, the problem is in the content, not the skeleton')
    ap.add_argument('--shader-slot', type=int, metavar='N',
                    help='which donor slot provides the shader. Without it the '
                         'first usable one is taken (slot 30 in atlanta), which '
                         'does NOT read the ITOP+2 stream. The atlanta blocks '
                         'that carry the stream use indices 19, 72, 65, 24, '
                         '293, 7, 71 and 26')
    ap.add_argument('--shader-list', action='store_true',
                    help='list the donor slots that have a usable shader')
    ap.add_argument('--flip-winding', action='store_true',
                    help='reverse the winding of each packet (MC2 -> MC3)')
    ap.add_argument('--normals', action='store_true',
                    help='emit the per-vertex light stream (ITOP+2, V4-8). '
                         'CPV stays on in the city pass through '
                         'mcCityModelClass::SetRenderStates, so the data is '
                         'always consumed; use TOGETHER with --shader-slot of a '
                         'slot whose blocks carry the stream')
    ap.add_argument('--padding', type=int, default=0, metavar='N',
                    help='CONTROL: N dead bytes right after the MapRoot, to '
                         'shift the layout without changing any content')
    ap.add_argument('--light', type=int, default=None, metavar='N',
                    help='per-vertex palette index for --normals '
                         '(0..255; default 148, the retail median)')
    ap.add_argument('--mc2-palette', metavar='CPVS.RSC',
                    help='use the CPP0 palette of an MC2 PS2 <hour>_cpvs.rsc '
                         'instead of the white palette')
    ap.add_argument('--texture-donor', metavar='PCK',
                    help='where the MapRoot+0x1D4 root texture comes from. The '
                         'MipBuffer varies by city: atlanta 0x10500 '
                         '(256x256), tokyo 0x160')
    ap.add_argument('--donor-shader', metavar='PCK',
                    help='where to take ONLY the shader block from, when the '
                         'main donor has none of the typical kind')
    ap.add_argument('--place', metavar='TSV',
                    help='placement table from mc2_port.py; uses the real '
                         'geometry instead of the cube')
    ap.add_argument('--models-dir', metavar='DIR',
                    help='model/ folder of the exported package (default: next '
                         'to --place)')
    ap.add_argument('--limit', type=int, help='only the first N components')
    ap.add_argument('--only-hood', help='only the lines of this place.tsv hood')
    ap.add_argument('--verify-only', metavar='FILE', help='only reopen and check')
    ap.add_argument('--base', help='real .pck that ALREADY loads, for bisection')
    ap.add_argument('--graft', choices=('palettes', 'shaders', 'extras', 'group', 'hood', 'hood1', 'hood2'),
                    help='replace ONE piece of --base with the generated version')
    a = ap.parse_args()

    if a.textures:
        print('  game textures: %d shader slot(s)'
              % load_textures(a.textures, a.identity_map))
    if a.runtime_binding:
        globals()['RUNTIME_BINDING'] = True
        print('  runtime binding: empty slots, marked with MC3TEXSLOTS')
    if a.ppf_manifest:
        print('  .ppf manifest: %d texture(s)' % load_ppf_manifest(a.ppf_manifest))
    if a.texture_map:
        import json
        global TEXTURES_PER_MODEL
        TEXTURES_PER_MODEL = {k.lower(): v for k, v in
                               json.load(open(a.texture_map)).items()}
        print('  texture map: %d model(s)' % len(TEXTURES_PER_MODEL))
    if a.pck_mesh:
        global GAME_PACKETS, PCK_FOLDER
        if os.path.isdir(a.pck_mesh):
            PCK_FOLDER = a.pck_mesh
            for f in sorted(os.listdir(PCK_FOLDER)):
                if f.endswith('.mesh.pck'):
                    pck_path = os.path.join(PCK_FOLDER, f)
                    key_m = f[:-len('.mesh.pck')].lower()
                    PCK_PER_MODEL[key_m] = read_pck_packets(pck_path)
                    GROUPS_PER_MODEL[key_m] = read_pck_groups(pck_path)
                    c = real_pck_centre(pck_path)
                    if c:
                        REAL_PCK_CENTRE[key_m] = c
            print('  game .pck files in the folder: %d model(s), %d packet(s) in total'
                  % (len(PCK_PER_MODEL),
                     sum(len(v) for v in PCK_PER_MODEL.values())))
        else:
            GAME_PACKETS = read_pck_packets(a.pck_mesh)
            print('  packets from the game: %d, %d quadwords, %d vertices'
                  % (len(GAME_PACKETS), sum(p[1] for p in GAME_PACKETS),
                     sum(p[2] for p in GAME_PACKETS)))

    # Both of these touch mc2_to_mc3 globals and have to take effect before any
    # make_block. See the CPV note further up.
    if a.flip_winding:
        globals()['FLIP_WINDING'] = True
        print('  winding flip: on')
    if a.normals:
        mc2.NORMALS = True
    if a.mc2_palette:
        globals()['MC2_PALETTE'] = palette_from_mc2(a.mc2_palette)
        print('  palette: 256 MC2 colours (%s)'
              % os.path.basename(a.mc2_palette))
    if a.padding:
        globals()['TEST_PADDING'] = a.padding
    if a.light is not None:
        if not 0 <= a.light <= 255:
            ap.error('--light goes from 0 to 255')
        mc2.LIGHT = a.light

    if a.base and a.graft:
        graft(a.base, a.out_path, a.graft, a.side)
        raise SystemExit(0)
    if a.verify_only:
        raise SystemExit(0 if verify(a.verify_only) else 1)
    if a.shader_list:
        target = a.donor_shader or a.donor
        good = shader_slots(target)
        print('%s: %d slot(s) with a usable shader' % (os.path.basename(target),
                                                       len(good)))
        print('   ' + ' '.join(str(k) for k in good))
        # The ones Fork City MODS measured carrying the ITOP+2 stream. Having a
        # usable shader and being the right pair for --normals are different
        # things: CPV is always on in the city pass, but the active VU program
        # is re-sent per list, so it is still worth picking a slot whose blocks
        # provably carry the stream.
        marked = [k for k in good if k in (19, 72, 65, 24, 293, 7, 71, 26)]
        print('   of those the blocks with the stream use: %s'
              % (' '.join(str(k) for k in marked) if marked
                 else 'none in this donor'))
        return

    if not (a.donor and a.out_path):
        ap.error('--donor and --out-path are required (or use --verify-only)')

    if a.place:
        models_dir = a.models_dir or os.path.join(os.path.dirname(a.place), 'model')
        if not os.path.isdir(models_dir):
            ap.error('models folder not found: %s' % models_dir)
        build_place(a.out_path, a.donor, a.place, models_dir, a.slots, a.groups,
                    a.material, a.limit, a.only_hood, a.donor_shader,
                    a.max_inst, a.shader_slot, a.texture_donor,
                    [h.strip() for h in a.hoods.split(',')] if a.hoods else None)
        print()
        raise SystemExit(0 if verify(a.out_path) else 1)

    blocks = read_model(a.xmod, a.scale) if a.xmod else cube(a.side)
    if not blocks:
        raise SystemExit('empty model')
    # The guard applies to HAND emission. With --pck-mesh the packets come
    # ready from the game and the `blocks` only serve for extents and counts, so
    # counting mine here would block a file the game itself assembled.
    if len(blocks) > mc2.MAX_BLOCKS and not GAME_PACKETS:
        raise SystemExit('%d blocks, retail never goes above %d'
                         % (len(blocks), mc2.MAX_BLOCKS))
    if GAME_PACKETS and len(GAME_PACKETS) > mc2.MAX_BLOCKS:
        raise SystemExit('%d packets from the game, retail never goes above %d'
                         % (len(GAME_PACKETS), mc2.MAX_BLOCKS))
    pos = tuple(float(x) for x in a.pos.split(','))
    hoods = [h.strip() for h in a.hoods.split(',')] if a.hoods else None
    if hoods:
        too_long = [h for h in hoods if len(h) > 3]
        if too_long:
            raise SystemExit('a hood name has at most 3 characters '
                             '(the .pck field has 4 bytes): %s'
                             % ', '.join(too_long))
    build(a.out_path, a.donor, blocks, a.hood, pos, a.slots, a.groups, a.material,
          a.probe, hoods, a.shader_slot, a.texture_donor)
    print()
    verify(a.out_path)


if __name__ == '__main__':
    main()
