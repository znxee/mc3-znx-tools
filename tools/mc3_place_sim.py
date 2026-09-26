"""
mc3_place_sim.py - run the city .pck loader's Place walk on the PC, before booting

Every mistake so far cost a boot of the console to discover, and the failure mode
is the worst possible one: an infinite loop with no message. This walks the same
graph the game walks, in the same order, and reports what the game would hit.

WHERE THE RULES COME FROM (all read out of the retail ELF, addresses noted so
they can be re-checked):

  mcCity::mcCity(datResource&)  0x259160   the actual reader; `mcCity::Place`
                                           only calls it. `*(datResource+4)` is
                                           the relocation delta.
  sub_2B3600                    0x2B3600   places one 16-byte group descriptor:
                                             +0x00 vtable  +0x04 data pointer
                                             +0x08 u16 COUNT (the one used)
                                             +0x0C linked-list next
  vtable(dword_615E48)[5]       0x2AF9E8   walks the pointer array. For each
                                           slot: relocate it, then dispatch on
                                           `*(obj+4) & 0x7F`:
                                             0 -> sub_2AFF20   1 -> sub_2B2A08
                                             2 -> sub_2B5B58
                                             anything else -> loc_2AFA88, which
                                             is `nop x5; b loc_2AFA88` - an
                                             INFINITE LOOP (a stripped assert).
  sub_2AFF20                    0x2AFF20   type 0: stamps the vtable, then runs
                                           0x2BA718 over `obj+0x08`, count 1.
  0x2BA718                      0x2BA718   dispatches on the BYTE at `+0x04` of
                                           that sub-object, values 0..3; the
                                           else branch is loc_2BA7E0, the same
                                           `nop x5; b` infinite loop.

WHAT THIS CHECKS, and why each one is a bug that actually happened here:

  * a tag outside the handled set        -> the game spins forever
  * the SAME object in more than one slot -> the walk has no "already placed"
    guard, so its handler runs once per slot and re-adds the delta to its
    internal pointers every time. This is what broke the generated file: 1329
    slots pointing at one shader.
  * a null slot                          -> not skipped; the code reads `*(0+4)`
  * a pointer outside the body           -> reads whatever is next in RAM

    python mc3_place_sim.py CITY.pck
    python mc3_place_sim.py CITY.pck --detail

ALWAYS run it on an untouched retail city first. A checker that fails the
shipped file is measuring itself, not the file - that already happened once here
with the AABB check.
"""

import argparse
import collections
import importlib.util
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SV = importlib.util.spec_from_file_location('mc3_view', os.path.join(_HERE, 'mc3_view.py'))
_view = importlib.util.module_from_spec(_SV)
sys.modules['mc3_view'] = _view
_SV.loader.exec_module(_view)

OK_KINDS = (0, 1, 2)          # 0x2AF9E8: everything else falls into the infinite loop
OK_SUBKINDS = (0, 1, 2, 3)   # 0x2BA718: likewise


class Body:
    """The .pck body, addressed by VA as the game does."""

    def __init__(self, path):
        self.d = open(path, 'rb').read()
        self.base_va, self.kind, self.version, self.body = struct.unpack_from('<4I', self.d, 0)
        self.base = self.base_va - 0x80

    def off(self, va):
        return va - self.base

    def inside(self, va):
        o = self.off(va)
        return 0x80 <= o <= len(self.d) - 4

    def u32(self, va):
        return struct.unpack_from('<I', self.d, self.off(va))[0]

    def u16(self, va):
        return struct.unpack_from('<H', self.d, self.off(va))[0]

    def u8(self, va):
        return self.d[self.off(va)]


def check_block(c, entry):
    """None if the block is consistent, or a description of what does not add up.

    The block entry is `[u32 address][u16 qwc][u16 nv]` and `gfxGeometry::Draw`
    (0x1ED930) builds a DMAtag from it: `qwc | 0x30000000 | (address << 32)`.
    The `qwc` is NOT a hint: it is the transfer counter. If it does not match the
    packet's real size, the VIF gets a piece too many (reads garbage as a command)
    or too few (the packet loses the tail that closes it).

    And `nv` has to match how many vertices the POSITION stream unpacks,
    otherwise the mesh draws a different number of triangles than it declared.
    """
    va, qwc, nv = struct.unpack_from('<IHH', c.d, c.off(entry))
    if not va:
        return None
    if not c.inside(va):
        return 'packet outside the body (%08X)' % va
    if not qwc:
        return 'qwc zero'
    end = va + 16 * qwc
    if not c.inside(end - 4):
        return 'a %d-qw packet runs past the end of the body' % qwc
    # DO NOT limit nv to 256: that was a heuristic filter of the AABB reader, and
    # atlanta itself has blocks of 308, 404 and up to 525 vertices. Retail rules.
    if nv < 3:
        return 'nv = %d (less than one triangle)' % nv
    batches_ = _view._vif_walk(c.d, c.off(va), 16 * qwc)
    if not batches_:
        return 'the VIF stream loses sync within the %d declared qw' % qwc
    found_it = 0
    for batch in batches_:
        for _rel, cmd, _off, num in batch:
            if (cmd & 0x0F) == 0x04 and (cmd & 0x60) == 0x60:   # V3-16/V3-32 = position
                found_it += num
    if found_it and found_it != nv:
        return 'nv declares %d but the position stream carries %d' % (nv, found_it)
    return None


# Every MapRoot field `mcCity::mcCity` (0x259160) touches, in code order.
# `modelled` says whether THIS simulator walks the structure or only checks that
# the pointer is valid. What is not modelled is not being verified - knowing
# that matters more than the list of passes.
MAPROOT_FIELDS = [
    (0x1D4, 'object via vtable[9]',       'sub_?  (a1+468)',        False),
    (0x1D8, '20 pointers via vtable[9]',  'loop of 20 (a1+472..)',  False),
    (0x0C,  'shader descriptors',         'sub_2B3600 x n',         True),
    (0x10,  'one more shader array',      'sub_2B3600',             True),
    (0x18,  'hood table',                 'sub_25D4F0 x n',         True),
    (0x1C,  'flat city index',            'sub_25B9D8',             False),
    (0x2C,  'palette A',                  'relocated only',         True),
    (0x30,  'palette B',                  'relocated only',         True),
    (0x190, 'structure at 0x190',         'sub_25C998',             False),
    (0x194, 'structure at 0x194',         'sub_24C528',             False),
    (0x198, 'structure at 0x198',         'sub_2571B0',             False),
    (0x19C, 'pointer at 0x19C',           'relocated only',         False),
    (0x1CC, 'pointer at 0x1CC',           'relocated only',         False),
    (0x228, 'structure at 0x228',         'sub_2BA6B0',             False),
]


def coverage(path, reference):
    """What the file has, what the reference has, and what this simulator covers.

    Answers the question that matters before booting: **which parts of the city
    did I leave empty**. A null field does not hang `Place` - all of them are
    guarded by `if (ptr)` - but nothing guarantees that whoever consumes the city
    afterwards tolerates the absence. This table is the list of suspects, in order.
    """
    c, r = Body(path), Body(reference)
    print('%s   x   %s (reference)' % (os.path.basename(path), os.path.basename(reference)))
    print()
    print('  %-6s %-28s %-24s %-9s %-9s %s'
          % ('field', 'what it is', 'who walks it', 'ref', 'this', 'simulated'))
    missing_n = []
    for off, what, who, mod in MAPROOT_FIELDS:
        vr = r.u32(r.base_va + off)
        vc = c.u32(c.base_va + off)
        status = 'has' if vc else 'EMPTY'
        if vr and not vc:
            missing_n.append((off, what, who, mod))
        print('  +%-5X %-28s %-24s %-9s %-9s %s'
              % (off, what[:28], who[:24], 'has' if vr else '-', status,
                 'yes' if mod else 'NO'))
    print()
    n_mod = sum(1 for _o, _q, _w, m in MAPROOT_FIELDS if m)
    print('  this simulator walks %d of %d MapRoot structures' % (n_mod, len(MAPROOT_FIELDS)))
    if missing_n:
        print('  %d field(s) the reference has and this file does NOT:' % len(missing_n))
        for off, what, who, mod in missing_n:
            print('     +0x%-4X %-28s (%s)%s' % (off, what, who,
                  '' if mod else '   <- and it is not even checked here'))
    else:
        print('  no field present in the reference is empty here')
    return missing_n


def simulate(path, detail=False):
    c = Body(path)
    root = c.base_va                      # the MapRoot sits at file 0x80 = base VA
    problems = []
    def error(cat, msg):
        problems.append((cat, msg))

    print('%s' % os.path.basename(path))
    print('  header: base %08X, kind %d, version %d, body %d (file %d)'
          % (c.base_va, c.kind, c.version, c.body, len(c.d)))
    if c.kind != 67:
        error('header', 'kind %d, not 67 - the loader bails out silently' % c.kind)
    if c.body != len(c.d) - 0x80:
        error('header', 'declared body %d, file has %d' % (c.body, len(c.d) - 0x80))

    n_groups = c.u32(root + 0x08)
    p_groups = c.u32(root + 0x0C)
    p_field10 = c.u32(root + 0x10)
    n_hoods = c.u32(root + 0x14)
    p_hoods = c.u32(root + 0x18)
    print('  MapRoot: %d shader group(s), %d hood(s)' % (n_groups, n_hoods))

    # mcCity::mcCity walks the group descriptors and then +0x10 through the
    # SAME function - +0x10 is another shader array, not a separate structure
    descriptors = []
    if p_groups:
        if not c.inside(p_groups):
            error('maproot', '+0x0C points outside the body (%08X)' % p_groups)
        else:
            descriptors += [p_groups + 16 * i for i in range(n_groups)]
    elif n_groups:
        error('maproot', '+0x0C NULL with count %d' % n_groups)
    if p_field10:
        if c.inside(p_field10):
            descriptors.append(p_field10)
        else:
            error('maproot', '+0x10 points outside the body (%08X)' % p_field10)
    else:
        error('maproot', '+0x10 NULL - the PS2 reads address 0 instead of faulting, and '
                        'Place wanders off into garbage')
    for name, field in (('+0x18 hoods', p_hoods), ('+0x2C paletteA', c.u32(root + 0x2C)),
                        ('+0x30 paletteB', c.u32(root + 0x30)),
                        ('+0x1C', c.u32(root + 0x1C))):
        if not field:
            error('maproot', '%s NULL' % name)
        elif not c.inside(field):
            error('maproot', '%s outside the body (%08X)' % (name, field))

    # ---- the 0x2AF9E8 loop, once per descriptor
    owners = collections.defaultdict(list)     # object -> [(group, slot)]
    tags = collections.Counter()
    nulls = 0
    for gi, desc in enumerate(descriptors):
        if not c.inside(desc):
            continue
        data = c.u32(desc + 4)
        n = c.u16(desc + 8)                    # `lhu a3, 4(a3)` with a3 = desc+4
        if not n:
            continue
        if not c.inside(data):
            error('group %d' % gi, 'data pointer outside the body (%08X)' % data)
            continue
        for k in range(n):
            va = data + 4 * k
            if not c.inside(va):
                error('group %d' % gi, 'array runs past the end of the body at slot %d' % k)
                break
            obj = c.u32(va)
            if not obj:
                nulls += 1
                continue
            if not c.inside(obj):
                error('group %d' % gi, 'slot %d points outside the body (%08X)' % (k, obj))
                continue
            owners[obj].append((gi, k))
            tags[c.u32(obj + 4) & 0x7F] += 1

    largest_array = max([c.u16(dd + 8) for dd in descriptors if c.inside(dd)] or [0])

    # ---- the three tests that came from the code
    bad_tags = sorted(t for t in tags if t not in OK_KINDS)
    if bad_tags:
        # not a verdict: an object placed BEFORE by its owner has its +0x04
        # rewritten, and then the kind read here is not what the game will see
        print('  kinds seen: %s' % ', '.join('%d(%d)' % (t, tags[t]) for t in sorted(tags)))
        print('    outside {0,1,2}: %s  -- they only hang if NOT placed earlier by their owner'
              % ', '.join(str(t) for t in bad_tags))
    else:
        print('  kinds seen: %s (all handled)'
              % ', '.join('%d(%d)' % (t, tags[t]) for t in sorted(tags)))

    repeated = {o: v for o, v in owners.items() if len(v) > 1}
    if repeated:
        worst_ = max(repeated.values(), key=len)
        error('repetition',
             '%d object(s) appear in more than one slot; the worst appears %d times. '
             'The loop has NO "already placed" guard: the handler runs once per '
             'slot and re-adds the delta to the internal pointers on every pass'
             % (len(repeated), len(worst_)))
        if detail:
            for o, v in sorted(repeated.items(), key=lambda x: -len(x[1]))[:5]:
                print('     %08X appears %d times: %s%s'
                      % (o, len(v), v[:4], ' ...' if len(v) > 4 else ''))

    print('  %d distinct objects in %d filled slot(s), %d null(s)'
          % (len(owners), sum(len(v) for v in owners.values()), nulls))
    if nulls:
        print('    (a null slot is NOT skipped: the code reads *(0+4). It works by '
              'accident on the PS2, but it is a reason to fill them all)')

    # ---- the MapRoot goes up to +0x228, not up to +0x80
    #
    # `mcCity::mcCity` touches +0x190, +0x194, +0x198, +0x19C, +0x1CC and +0x228,
    # and a loop of 20 pointers from +0x1D8. A 0x80-byte MapRoot leaves all of
    # that landing on whatever comes next in the file - that is what the
    # from-scratch builder did, and the loader read geometry as pointers.
    MAPROOT_END = 0x22C
    if len(c.d) - c.off(root) < MAPROOT_END:
        error('maproot', 'the MapRoot has only %d bytes to the end of the body; mcCity uses '
                        'up to +0x228 (%d)' % (len(c.d) - c.off(root), MAPROOT_END))
    else:
        for off, who in ((0x190, 'sub_25C998'), (0x194, 'sub_24C528'),
                          (0x198, 'sub_2571B0'), (0x228, 'sub_2BA6B0')):
            v = c.u32(root + off)
            if v and not c.inside(v):
                error('maproot', '+0x%X outside the body (%08X) and non-null, so %s '
                                'will walk it' % (off, v, who))

    # ---- the hoods: sub_25D4F0 (stride 20), sub_24C598 (28), sub_257220 (96)
    #
    # The hood Place is SHALLOW: `+0x00` (the record) is only relocated, never
    # walked, and the ComponentData at `+0x0C` likewise. Everything is guarded
    # by `if`, so a null pointer with a zero count is legitimate here.
    comps = []
    if p_hoods and c.inside(p_hoods):
        for h in range(n_hoods):
            e = p_hoods + 20 * h
            if not c.inside(e + 16):
                error('hood %d' % h, 'entry runs past the end of the body')
                break
            rec, nu, au, ni, ai = struct.unpack_from('<5I', c.d, c.off(e))
            for name, va in (('record', rec), ('unique array', au), ('instance array', ai)):
                if va and not c.inside(va):
                    error('hood %d' % h, '%s outside the body (%08X)' % (name, va))
            if au and c.inside(au):
                for k in range(nu):
                    comp = au + 28 * k
                    if not c.inside(comp + 24):
                        error('hood %d' % h, 'unique array runs past the end at item %d' % k)
                        break
                    comps.append(comp)
            elif nu:
                error('hood %d' % h, '%d uniques but the array is NULL' % nu)
            if ai and c.inside(ai):
                if not c.inside(ai + 96 * ni - 4) and ni:
                    error('hood %d' % h, 'instance array runs past the end of the body')
            elif ni:
                error('hood %d' % h, '%d instances but the array is NULL' % ni)

    # ---- a dangling pointer in a COPIED blob
    #
    # A value in the VA range pointing PAST the end of the body. On its own that
    # proves nothing - atlanta itself has thousands, because a vertex coordinate
    # sometimes looks like an address. What gives it away is EXACT REPETITION: a
    # blob copied from the donor and replicated N times carries the same dangling
    # pointer N times, and coordinates do not do that. That is how the second
    # pointer of the shader block (`+0x0C`) showed up, repeated 64 times.
    body_end = c.base_va + c.body
    where = collections.defaultdict(list)
    for o in range(0x80, len(c.d) - 4, 4):
        v = struct.unpack_from('<I', c.d, o)[0]
        if c.base_va <= v < c.base_va + 0x1000000 and v >= body_end:
            where[v].append(o)
    # the discriminant is the CONSTANT STRIDE: a replicated blob always puts the
    # same pointer at the same inner offset, so the occurrences are equidistant.
    # A vertex coordinate that happens to look like a VA does not do that - it
    # is what failed atlanta in the first version of this test (65 values, one of
    # them 434 times), and a checker that fails the factory file is measuring
    # itself.
    suspects = []
    for v, offs in where.items():
        if len(offs) < 8:
            continue
        steps = {offs[i + 1] - offs[i] for i in range(len(offs) - 1)}
        if len(steps) == 1 and 8 <= steps.pop() <= 0x400:
            suspects.append((v, len(offs), offs[1] - offs[0]))
    # A WARNING, not a failure: not even the constant stride can safely tell a
    # pointer from data - atlanta has a value repeated 14 times every 0xC bytes
    # that is a legitimate array. Raising the threshold until retail passes would
    # be tuning the test to the answer. The real guarantee lives in the builder,
    # in `limpa()`, which zeroes every pointer of a copied blob by construction;
    # this only draws attention to what DESERVES a look.
    if suspects:
        v, n, step = max(suspects, key=lambda x: x[1])
        print('  warning: %d pointer(s) outside the body repeated with a CONSTANT '
              'STRIDE; the worst (%08X) appears %d times every 0x%X bytes'
              % (len(suspects), v, n, step))
        print('         (atlanta has 2 like this, so this alone does not fail; '
              'look when the stride is the size of a blob you copy)')
        if detail:
            for v, n, step in sorted(suspects, key=lambda x: -x[1])[:6]:
                print('     %08X x%d stride 0x%X' % (v, n, step))

    # ---- MapRoot+0x1C: the flat city index
    #
    # Measured on atlanta: array1 is the 651 unique components (651 of 651 match)
    # and array2 the 3608 instances (3608 of 3608). `sub_25B9D8` relocates every
    # pointer of both, and the two scratch arrays at +0x10/+0x14 are sized by
    # `mcCity+0x268` - which sits INSIDE the MapRoot, so the MapRoot needs at
    # least 0x26C bytes.
    p1c = c.u32(root + 0x1C)
    if p1c and c.inside(p1c + 0x18):
        n1, n2 = c.u32(p1c), c.u32(p1c + 4)
        a1, a2v = c.u32(p1c + 8), c.u32(p1c + 12)
        if n1 != len(comps):
            error('+0x1C', 'declares %d components but the hoods have %d' % (n1, len(comps)))
        elif n1 and c.inside(a1 + 4 * n1):
            in_index = {c.u32(a1 + 4 * k) for k in range(n1)}
            missing_n = {c.base_va + (x - c.base) for x in ()}   # placeholder
            missing = len(set(comps) - in_index)
            if missing:
                error('+0x1C', '%d hood component(s) missing from the index' % missing)
        for name, va, n in (('array1', a1, n1), ('array2', a2v, n2)):
            if n and (not va or not c.inside(va + 4 * n - 4)):
                error('+0x1C', '%s invalid for %d entries' % (name, n))
        scale = c.u32(root + 0x268) if len(c.d) - c.off(root) >= 0x26C else None
        if scale is None:
            error('+0x1C', 'MapRoot smaller than 0x26C: mcCity+0x268 (the size of the '
                          '+0x10/+0x14 scratch arrays) falls outside it')
        else:
            for off, width in ((0x10, 12), (0x14, 4)):
                va = c.u32(p1c + off)
                if scale and (not va or not c.inside(va + width * scale - 4)):
                    error('+0x1C', 'the +0x%X scratch does not fit %d records of %d bytes'
                                  % (off, scale, width))

    # ---- after Place: whoever consumes the hood reads the geometry
    n_mesh = n_bad_slot = n_bad_block = n_bad_cd = 0
    for comp in comps:
        cd = c.u32(comp + 0x0C)
        if not cd:
            continue
        if not c.inside(cd + 0x60):
            error('component', 'ComponentData outside the body (%08X)' % cd)
            continue
        # ComponentData: two fields are CONSTANT across atlanta's 651, and zeroing
        # the one at +0x28 (a vtable token) produced `Jump to unaligned address`
        # when the camera got close to the model - a virtual call through a
        # pointer read from garbage.
        if c.u32(cd + 0x00) != 1:
            n_bad_cd += 1
        # +0x28 is a vtable. Retail writes the TOKEN 0x0079FF88 and the game
        # translates it to 0x00625098 at load; a generated object is not in the
        # translator's list, so for it the right thing is to write the translated
        # address already. Both are accepted.
        if c.u32(cd + 0x28) not in (0x0079FF88, 0x00625098):
            n_bad_cd += 1
            if detail and n_bad_cd <= 4:
                print('     ComponentData %08X: +0x28 = %08X, expected 0079FF88 '
                      '(token) or 00625098 (translated)' % (cd, c.u32(cd + 0x28)))
        for slot in range(10):
            mp = c.u32(cd + 0x3C + 4 * slot)
            if not mp:
                continue
            if not c.inside(mp + 0x14):
                error('component', 'mesh of slot %d outside the body (%08X)' % (slot, mp))
                continue
            n_mesh += 1
            # same story as the ComponentData: retail writes the token 0x007A1E18
            # and the game translates it to 0x00626D98; a generated object is not
            # in the translator's list, so for it the translated value is right
            if c.u32(mp) not in (0x007A1E18, 0x00626D98):
                n_bad_cd += 1
                if detail:
                    print('     mesh %08X: vtable %08X, expected 007A1E18 or 00626D98'
                          % (mp, c.u32(mp)))
            ngrp = c.u16(mp + 8)
            mt, gt = c.u32(mp + 0x0C), c.u32(mp + 0x10)
            if ngrp and (not mt or not c.inside(mt + 2 * ngrp)):
                error('mesh', '%08X: invalid material table with %d group(s)' % (mp, ngrp))
                continue
            if ngrp and (not gt or not c.inside(gt + 8 * ngrp)):
                error('mesh', '%08X: invalid group table with %d group(s)' % (mp, ngrp))
                continue
            for g in range(ngrp):
                idx = c.u16(mt + 2 * g)
                if idx >= largest_array:
                    n_bad_slot += 1
                    if detail and n_bad_slot <= 5:
                        print('     mesh %08X group %d: material %d >= %d slots'
                              % (mp, g, idx, largest_array))
                lp, cnt, _z = struct.unpack_from('<IHH', c.d, c.off(gt + 8 * g))
                if not lp or not c.inside(lp + 8 * cnt):
                    error('mesh', '%08X group %d: invalid block list' % (mp, g))
                    continue
                for b_ in range(cnt):
                    status = check_block(c, lp + 8 * b_)
                    if status:
                        n_bad_block += 1
                        if detail and n_bad_block <= 6:
                            print('     mesh %08X group %d block %d: %s' % (mp, g, b_, status))
    if n_bad_cd:
        error('componentdata', '%d wrong constant field(s) in ComponentData '
                              '(+0x00 must be 1, +0x28 must be 0079FF88)' % n_bad_cd)
    if n_bad_block:
        error('block', '%d block(s) whose qwc does not match the packet, or whose nv does '
                      'not match the position stream' % n_bad_block)
    if n_bad_slot:
        error('material', '%d group(s) with a material index past the end of the '
                         'shader array (%d slots)' % (n_bad_slot, largest_array))
    print('  %d unique component(s), %d reachable mesh(es)' % (len(comps), n_mesh))

    # ---- the +0x08 sub-object of kind-0 objects (0x2BA718)
    bad_sub = 0
    for obj, refs in owners.items():
        if (c.u32(obj + 4) & 0x7F) != 0:
            continue
        sub = c.u32(obj + 8)
        if not sub or not c.inside(sub):
            error('kind 0', 'object %08X has an invalid +0x08 (%08X)' % (obj, sub))
            continue
        b = c.u8(sub + 4)
        if b not in OK_SUBKINDS:
            bad_sub += 1
            if detail and bad_sub <= 5:
                print('     sub-object of %08X: byte +0x04 = %d (outside 0..3)' % (obj, b))
    if bad_sub:
        error('kind 0', '%d sub-object(s) with byte +0x04 outside 0..3 -> loc_2BA7E0' % bad_sub)

    print()
    if problems:
        print('  %d PROBLEM(S):' % len(problems))
        for cat, m in problems:
            print('    [%s] %s' % (cat, m))
    else:
        print('  nothing the code that was read would fail')
    return not problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pck', nargs='+')
    ap.add_argument('--detail', action='store_true')
    ap.add_argument('--coverage', metavar='REFERENCE',
                    help='compare field by field with a .pck that works and say '
                         'what this simulator does NOT check')
    a = ap.parse_args()
    if a.coverage:
        for p in a.pck:
            coverage(p, a.coverage)
            print()
        raise SystemExit(0)
    ok = True
    for p in a.pck:
        ok = simulate(p, a.detail) and ok
        print()
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
