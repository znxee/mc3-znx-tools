# -*- coding: utf-8 -*-
"""Convert a Midnight Club 2 (PC) model into an MC3 PS2 city.

TEST. Replaces the geometry of ONE city model and keeps everything else in the
original file -- texture, shader, collision, instances. It is there to confirm
in the game that writing the city packet is right, before investing in
converting a whole city.

Nothing already in the file moves: the new geometry and the new block table are
APPENDED at the end and only the target model's pointers are rewritten. It is
the same discipline as the Traffic Replacer.

    python mc2_to_mc3.py                      (uses the defaults below)
    python mc2_to_mc3.py --target NAME --xmod PATH --out-path FILE.pck --scale F

WHAT THIS TEST DOES **NOT** SOLVE
  * texture: the geometry keeps the atlanta atlas, so the UV points to the
    wrong place -- the building shows up, but wrongly textured;
  * collision: the _bnd.pck was not touched, the car goes through;
  * it was never loaded in the game by whoever wrote this.
"""
from __future__ import annotations

import argparse
import io
import os
import struct

MC2_DIR = os.path.join(os.environ.get('MC2_PC_ASSETS', 'mc2_pc/assets_p'),
                       'city', 'losangeles', 'models')
XMOD = os.path.join(MC2_DIR, 'l_downtown_blk_wfargo_x#geom_0_main.xmod')
SRC = os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.pck')
TARGET = 'a_inst_low_ghto_06x'

MODEL_VTABLE = 0x7A0090
VTABLE_AT = 0x28          # the vtable sits 0x28 after the start of the model object
MESH_LOD0 = 0x14          # from the start of the vtable
MESH_BLOCKS = 0x10        # mesh -> group table
MESH_PERBLOCK = 0x14      # mesh -> PER-BLOCK chain (see emit_mesh_region)
MESH_NSLOT = 0x0A         # how many slots that chain has
SPHERE_AT = 0x04          # centre (3 floats) and radius at +0x10
INST_MODEL = 0x0C
INST_RADIUS = 0x18

# (S-32 tag at ITOP+0, position at ITOP+98, UV at ITOP+50, normal at ITOP+2)
EVEN = (b'\x9e\x40\x02\x60', b'\x00\x01', b'\xd0\x00', b'\xa0\x00')
ODD = (b'\xc5\x41\x02\x60', b'\x27\x02', b'\xf7\x01', b'\xc7\x01')
FIXED_UV = None            # constant (u, v): flat surface, for debugging the shape
NORMALS = False           # only 7.3% of city blocks have the ITOP+2 stream
LIGHT = 148                 # per-vertex palette index; 148 is the retail median
MAX_VERTS = 48            # retail limit (measured on the 1353 atlanta meshes)
MAX_BLOCKS = 88           # likewise: no city mesh goes above this


# ------------------------------------------------------------------ MC2
def read_xmod(path):
    """(positions, uvs, strips) of an MC2 .xmod.

    Self-describing text: v/n/c/t1 arrays, `adj` tuples that index into them,
    and `stp`/`str` strips (opposite parities) that index into the tuples.

    THE CATCH: the strip indices are RELATIVE TO THE PACKET, not to the whole
    adjunct array. The file declares this in a `packet <num_adjs> <num_prims>`
    record -- which is easy to confuse with the `packets:` field inside the
    `mtl` block. Reading everything as one global index, half the adjuncts go
    unused and giant triangles appear across the model: in wfargo 620 of 1189
    adjuncts were referenced and there were 21 spikes.

    I had solved it by heuristic (detecting when the index restarts), which gave
    the right result but by luck. jtxredline's `mc2-map-toolkit` shows the
    explicit `packet` record -- that is the correct reading. The same toolkit
    confirms that `stp` starts clockwise and `str` counter-clockwise,
    alternating, which is the parity model used here.
    """
    v, t1, adj, strips = [], [], [], []
    base = 0
    for line in io.open(path, encoding='latin1'):
        f = line.split()
        if not f:
            continue
        if f[0] == 'v' and len(f) >= 4:
            v.append((float(f[1]), float(f[2]), float(f[3])))
        elif f[0] == 't1' and len(f) >= 3:
            t1.append((float(f[1]), float(f[2])))
        elif f[0] == 'packet' and len(f) >= 3:
            base = len(adj)              # the packet restarts the numbering
        elif f[0] == 'adj' and len(f) >= 5:
            # the MC2 tuple is `adj <vertex> <normal> <colour> <uv>`. Field 3 is
            # the per-vertex PALETTE INDEX - the same design MC3 uses in the
            # fourth byte of the ITOP+2 stream. It used to be ignored here; now
            # it goes along, and a vertex without a colour sends zero.
            adj.append((int(f[1]), int(f[4]), int(f[3])))
        elif f[0] in ('stp', 'str') and len(f) >= 3:
            n = int(f[1])
            strips.append((f[0] == 'str', [base + int(x) for x in f[2:2 + n]]))

    return v, t1, adj, strips


def tris_of(pos, odd=False):
    """Triangles of a strip, with the game's winding rule.

    The rule is FIXED: the triangle at index `i` comes out `(i-2, i-1, i)` when
    `i` is even and `(i-1, i-2, i)` when it is odd. No field in the block flips
    it -- the VU1 program is the same in both halves of the double buffer.

    This once had a mandatory `odd`, coming from the field now known to be the
    VU1 address. It was a wrong premise: half the triangles came out reversed
    and vanished in back-face culling. The parameter only still exists for
    whoever wants to decode a loose strip with the parity swapped.
    """
    out = []
    for i in range(2, len(pos)):
        a, b, c = (i - 2, i - 1, i) if (i % 2 == 0) != odd else (i - 1, i - 2, i)
        out.append((a, b, c))
    return out


def vertex_normals(v, adj, strips):
    """MC2 per-vertex normal, summing the faces that touch it.

    The MC3 city HAS a normal stream -- `UNPACK V3-8` at ITOP+2, unit vectors in
    s8 (median |n|/127 = 0.992 measured on atlanta). I had noted the opposite,
    but that came from a VIF walker that lost sync on the masked unpack and
    never reached the fourth stream.

    The winding follows the game's -- triangle i is (i-2,i-1,i) with even i and
    (i-1,i-2,i) with odd i -- so the normal points to the same side as the face
    that will be drawn.
    """
    acc = {}
    for _e_str, idxs in strips:
        for k in range(2, len(idxs)):
            x, y, z = idxs[k - 2], idxs[k - 1], idxs[k]
            if k % 2:
                x, y = y, x
            pa, pb, pc = v[adj[x][0]], v[adj[y][0]], v[adj[z][0]]
            u = [pb[i] - pa[i] for i in range(3)]
            w = [pc[i] - pa[i] for i in range(3)]
            n = (u[1] * w[2] - u[2] * w[1],
                 u[2] * w[0] - u[0] * w[2],
                 u[0] * w[1] - u[1] * w[0])
            for idx in (x, y, z):
                t = acc.setdefault(adj[idx][0], [0.0, 0.0, 0.0])
                for i in range(3):
                    t[i] += n[i]
    out = {}
    for k, t in acc.items():
        m = (t[0] ** 2 + t[1] ** 2 + t[2] ** 2) ** 0.5
        out[k] = tuple(c / m for c in t) if m > 1e-9 else (0.0, 1.0, 0.0)
    return out


def strips_to_blocks(v, t1, adj, strips):
    """MC2 strips -> [(positions, uvs, odd)], centred on the origin.

    STITCHING. MC2 hands over hundreds of tiny strips (the wfargo tower has 287,
    almost all of 4 vertices) and retail never goes above **88 blocks per mesh**
    nor **48 vertices per block** -- measured on the 1353 atlanta city meshes.
    Emitting one strip per block gave 287 blocks and the game stayed on a black
    screen. So the strips are concatenated into one, with a bridge of
    degenerates (repeating the last vertex of the previous one and the first of
    the next), respecting both limits.
    """
    # ANCHOR. The vertex centroid on all three axes was the obvious choice and it
    # is wrong on all three. The game puts the instance on the GROUND (instance y
    # = 0.15 in atlanta), so the model has to have its BASE at the origin, not
    # its middle: anchoring by the centroid, the wfargo building got a local AABB
    # in y of -30.90..164.10 and sank 31 units into the asphalt, with 80% of its
    # vertices under the street. And in x/z the centroid is not the box centre (in
    # a building with a balcony, a wide base or any asymmetric mass it pulls
    # towards the denser vertices): it gave an 11.2-unit shift in the footprint.
    #
    # The rest of the script already assumed base-at-origin without my noticing
    # -- the bound sphere is written at (0, dy/2, 0), which only makes sense with
    # the minimum y at zero. Now both ends agree.
    cx = (max(p[0] for p in v) + min(p[0] for p in v)) / 2.0
    cy = min(p[1] for p in v)
    cz = (max(p[2] for p in v) + min(p[2] for p in v)) / 2.0

    # The MC2 convention is the REVERSE of MC3's: measuring the sign of the
    # normals against the centroid, `stp` matches the block's ODD index (58.7% of
    # the faces outward, against 41.3% for the direct mapping). On the MC3 side
    # the measurement is much sharper -- 64.8% outward over atlanta's 156
    # models, a median of 68% per model -- and confirms that the even index is
    # the front face.
    norm = vertex_normals(v, adj, strips)
    slices = []
    for e_str, idxs in strips:
        pos = [tuple(a - b for a, b in zip(v[adj[i][0]], (cx, cy, cz))) for i in idxs]
        uv = [t1[adj[i][1]] if 0 <= adj[i][1] < len(t1) else (0.0, 0.0) for i in idxs]
        # THE COLOUR TRAVELS INSIDE THE NORMAL. The block is a 4-tuple and there
        # are twenty places that unpack it that way; adding a fifth field would
        # mean touching all twenty. Since the only consumer of the normal is
        # make_block, where it becomes four bytes (xyz + palette index), the
        # index fits as a fourth component without touching anything else.
        nr = [norm.get(adj[i][0], (0.0, 1.0, 0.0))
              + ((adj[i][2],) if len(adj[i]) > 2 else ()) for i in idxs]
        if len(pos) >= 3:
            slices.append((pos, uv, nr, not e_str))

    # WINDING. The game winds the triangle at index i as (i-2,i-1,i) if i is
    # even and (i-1,i-2,i) if it is odd, always -- no field flips it.
    # So what decides the face side is where the strip LANDS inside the block:
    #
    #   `stp` strip (clockwise)          -> first vertex at an EVEN index
    #   `str` strip (counter-clockwise)  -> first vertex at an ODD index
    #
    # (its 1st triangle is born at s+2, which has the same parity as s).
    #
    # The old version measured that parity RELATIVE to the block's `odd`, which
    # is now known to be the VU1 double-buffer address. Since the buffer
    # alternates with the block's position in the list, the winding became a
    # coin toss: half the faces came out reversed and vanished in back-face
    # culling -- that is what gave the MC2 building its loose-slabs look the
    # first time it drew in the game.
    def open_(pos, uv, nr, is_odd):
        """Start a block. A `str` strip has to land on an odd index, so it
        gets a repeated vertex in front (degenerate, as retail does)."""
        if is_odd:
            return [pos[0]] + list(pos), [uv[0]] + list(uv), [nr[0]] + list(nr)
        return list(pos), list(uv), list(nr)

    out = []
    cur_p, cur_u, cur_n = [], [], []
    for pos, uv, nr, is_odd in slices:
        if not cur_p:
            cur_p, cur_u, cur_n = open_(pos, uv, nr, is_odd)
            continue
        # the degenerate bridge has 2 or 3 vertices; pick the one that makes the
        # strip's first vertex land on the right parity
        target = 1 if is_odd else 0
        bridge = 2 if (len(cur_p) + 2) % 2 == target else 3
        if len(cur_p) + bridge + len(pos) > MAX_VERTS:
            out.append((cur_p, cur_u, cur_n, False))
            cur_p, cur_u, cur_n = open_(pos, uv, nr, is_odd)
            continue
        cur_p += [cur_p[-1]] + [pos[0]] * (bridge - 1)
        cur_u += [cur_u[-1]] + [uv[0]] * (bridge - 1)
        cur_n += [cur_n[-1]] + [nr[0]] * (bridge - 1)
        cur_p += pos
        cur_u += uv
        cur_n += nr
    if cur_p:
        out.append((cur_p, cur_u, cur_n, False))
    return out, (cx, cy, cz)


# ------------------------------------------------------- city packet
TAIL_HEAD = bytes.fromhex('00000020c0c0c0c0')
TAIL_FOOT = bytes.fromhex('06000014')


def make_block(pos, uv, nrm, scale, odd):
    """A city block, with the VIF TAIL that closes the packet.

    The tail is 16 constant bytes right after the UVs:
        00 00 00 20 | c0 c0 c0 c0 | 9e 00 00 04 | 06 00 00 14
    with `c5 01 00 04` in place of `9e 00 00 04` when the strip is odd. After it
    come zeros up to a multiple of 16. Forgetting this leaves the VIF reading
    past the packet -- it was one of the two causes of the first test hanging.

    `odd` IS NOT TRIANGLE PARITY -- it is which half of the VU1 DOUBLE BUFFER the
    block uses. The four fields it picks are all VU1 addresses, and the distance
    between the two halves is always 295 qwords:

        field            buffer A          buffer B
        +00 tag          imm 158           imm 453      (UNPACK S-32, num 2)
        +0C sp           imm 256           imm 551      (UNPACK V3-16 = position)
        su (after XYZ)   imm 208           imm 503      (UNPACK V2-16 = UV)
        tail             MSCAL 158         MSCAL 453

    Measured on atlanta: **785 of the 786 groups alternate A,B,A,B... from block
    0** (the only exception is a degenerate group with imm 0). So the value
    comes from the BLOCK'S POSITION IN THE GROUP'S LIST, and resets per group:

        odd = (index_in_list % 2 == 1)

    Copying the `odd` of the source block breaks the alternation when the list
    changes size, and then the DMA overwrites the half the VU is still running
    -- and the game hangs. That is why every test with a block count different
    from the original hung, while the faithful rebuild passed.
    """
    tag, sp, su, sn = ODD if odd else EVEN
    n = len(pos)
    o = bytearray(tag)
    o += struct.pack('<f', scale)
    o += struct.pack('<I', n)
    o += sp + bytes([n, 0x69])
    for x, y, z in pos:
        o += struct.pack('<hhh', *(max(-32768, min(32767, int(round(c / scale))))
                                   for c in (x, y, z)))
    while len(o) % 4:                       # the inner padding is alignment to 4
        o += b'\x00'
    # UV. With `FIXED_UV` every vertex gets the SAME coordinate, so the whole
    # surface samples a single texel and comes out a flat colour. It is not
    # pretty, and that is the point: ported geometry uses the host city's atlas,
    # the UV points to the wrong place and the result is too unreadable to judge
    # whether the SHAPE is right. Flat, you can see silhouette, position and scale.
    o += su + bytes([n, 0x65])
    for u, w in (uv if FIXED_UV is None else [FIXED_UV] * len(uv)):
        o += struct.pack('<hh',
                         max(-32768, min(32767, int(round(u * 4096)))),
                         max(-32768, min(32767, int(round(w * 4096)))))
    # NORMAL, the fourth stream: `UNPACK V4-8` (0x6E) at ITOP+2, without USN.
    # Without it the block draws, but the lighting comes from whatever the
    # previous block left in VU memory.
    #
    # V4 and not V3 on purpose. The microcode does `LQ VF13.xyzw, 2(VI07)` --
    # it reads the whole qword, `w` included -- and retail writes all four bytes:
    # of atlanta's 1073 ITOP+2 streams, 740 are V4-8 and 333 are MASKED V3-8
    # (with `w` coming from ROW). Plain V3-8 shows up 16 times. Emitting plain
    # V3-8, `w` would keep whatever the previous block left in VU memory.
    #
    # The first three bytes are the normal in s8: measured on atlanta's 8177 V4-8
    # vertices, |xyz|/127 has a median of 0.997 and **100%** fall in
    # [0.95, 1.05]. The fourth is the PALETTE INDEX. The microcode does
    # `MTIR VI10, VF13[fsf]` with fsf = bits 22..21 (not 24..23 -- mixing the
    # two up is what made me think the index came from the x byte), reads it
    # signed, and fetches `LQ 896(VI10)`: the table takes 768..1023, the top 256
    # qwords of VU1 memory. The result is multiplied by a global and goes to the
    # GIF packet as a colour.
    #
    # So the MC3 city **has no angle-based lighting**: it is baked per vertex.
    # Writing a single value leaves the building uniform -- which is what
    # happened with 255, retail's mode but also the extreme.
    #
    # It really is an offline bake, it cannot be rebuilt: measured on atlanta's
    # 8177 vertices, the correlation of the index with the normal is
    # +0.045/-0.001/+0.059 and with the vertex height +0.051. The same normal
    # shows up with several indices in 39.5% of cases. So an adjustable value
    # goes here, with the retail median (148) as the default instead of the
    # extreme.
    #
    # Bit 0 of the x byte is the ADC, which suppresses the triangle ending at
    # that vertex (45.1% of retail vertices have it). Here it stays ALWAYS zero
    # on purpose: the strip breaks are already handled by the degenerate bridges
    # built above, and marking ADC on top would suppress good triangles.
    #
    # OFF BY DEFAULT, and that was measured after getting it wrong: **only 7.3%
    # of the city blocks have an ITOP+2 stream** (759 of 10338 in atlanta), and
    # the 22 original blocks of `a_inst_low_ghto_06x` have none -- only position
    # and UV. Writing it into a target whose shader does not read it only dirties
    # the ITOP+2 region, which is shared between blocks, and the building came
    # out reddish in the test. Turn it on only when the replaced mesh already has
    # the stream.
    #
    # CORRECTION: the sentence 'what decides whether the stream is read is the
    # MATERIAL's flags word, not the block' was here and it is WRONG. FORK C++
    # MODS AND PATCHES decompiled mcCityModelClass::SetRenderStates (0x0024B300):
    # it calls rmcSetCpvMode(1, 0) and
    # rmcCpvPalette::Download(*(u32*)(mcCity+0x2C)) once PER PASS. Colour per
    # vertex is turned on by the cullable's CLASS for the whole city -- the sky,
    # at the same vtable position, calls rmcSetCpvMode(0, 0). The material plays
    # no part in that decision. What stays true in the warning is the measured
    # part: the ITOP+2 region is shared, only 7.3% of blocks write it, and the
    # others inherit -- so filling one isolated mesh with the stream in a file
    # that did not have it changes what the NEIGHBOURS inherit. In a fully
    # generated city, where nobody writes it, the problem is the opposite: they
    # all inherit garbage.
    if NORMALS:
        o += sn + bytes([n, 0x6E])
        for n in nrm:
            nx, ny, nz = n[0], n[1], n[2]
            # the fourth component, when present, is the palette index that came
            # from MC2's CPV; without it the usual constant LIGHT is used
            light = int(n[3]) & 0xFF if len(n) > 3 else LIGHT
            q = [max(-127, min(127, int(round(c * 127.0)))) for c in (nx, ny, nz)]
            q[0] &= ~1
            o += struct.pack('<3bB', q[0], q[1], q[2], light)
        while len(o) % 4:
            o += b'\x00'
    # THE TAIL, AND STMASK EXCLUDES THE STREAM.
    #
    # TAIL_HEAD is `STMASK 0xC0C0C0C0`, which masks the `w` field -- it is the
    # partner of the `MASKED UNPACK V3-8` retail uses as an ALTERNATIVE to V4-8:
    # either the block sends the four bytes at once, or it sends three and lets
    # `w` come from ROW under the mask. MEASURED on atlanta, crossing the two over
    # 525 packets:
    #
    #     with stream AND with mask  :   0      <- never, not once
    #     with stream, without mask  :  65
    #     without stream, with mask  : 410
    #     without stream, no mask    :  50
    #
    # Emitting both is a combination that DOES NOT EXIST in retail, and that is
    # what hung on the first frame every time NORMALS was turned on -- not the
    # material's flags word (that explanation of mine already fell, see the CPV
    # note above) and not the shader slot: both attempts, with the slot 30 shader
    # and with the slot 65 one, died on frame 1 for the same reason.
    if not NORMALS:
        o += TAIL_HEAD
    o += (b'\xc5\x01\x00\x04' if odd else b'\x9e\x00\x00\x04') + TAIL_FOOT
    while len(o) % 16:
        o += b'\x00'
    return bytes(o)


def emit_mesh_region(p, mesh, cut, blobs_per_group):
    """Write, in a single contiguous region, the group table AND the per-block
    chain of `mesh+0x14`. Returns (group table offset, chain offset).

    The chain was the second bug behind the hangs: it has `mesh+0x0A` slots, each
    with one 8-byte entry per group, and that entry keeps a count that has to be
    EQUAL to the group's number of blocks (measured: 6893 of 6893 atlanta
    entries). Reusing the original file's one makes the game index past the end
    as soon as a group gains a block.

        mesh+0x14 -> [nslot pointers]
          each slot -> ngrp 8-byte entries: [ptrA][ptrB]
             ptrA -> [u32 count][u32 vertices of each block], and the payloads
                     right after -- 1 BYTE PER VERTEX, aligned to 16
             ptrB -> array of `count` pointers, one per payload

    The payload is per vertex and I do not know what it means (high entropy,
    values near +-127; it looks like a normal or baked lighting). For geometry
    that did not come from a city mesh there is nothing to copy, so the bytes of
    the model being replaced are recycled cyclically: it is valid data, with the
    right distribution, and the game accepts it.
    """
    ngrp = len(cut)
    nslot = struct.unpack_from('<H', p.data, mesh + MESH_NSLOT)[0]
    counts = [len(g) for g in cut]
    verts = [[len(pos) for pos, _uv, _nr, _o in g] for g in cut]

    # sample payload bytes, from the chain already in the file
    sample = b''
    t14 = p.U(mesh + MESH_PERBLOCK)
    if p.inside(p.F(t14)) and nslot:
        tab = p.F(p.U(p.F(t14)))
        pa, pb = struct.unpack_from('<II', p.data, tab)
        if p.inside(p.F(pb)):
            q = p.F(p.U(p.F(pb)))
            if p.inside(q):
                sample = bytes(p.data[q:q + 64])
    if not any(sample):
        sample = bytes([0x81])

    def fill(n):
        return bytes(sample[i % len(sample)] for i in range(n))

    def a16(n):
        return (n + 15) & ~15

    # --- step 1: only the relative positions, which do not depend on where it lands ---
    rel = 0
    rel += 16                                   # group table header
    r_gtab = rel
    rel += a16(8 * ngrp)
    r_list, r_blocks = [], []
    for gi in range(ngrp):
        rel += 16                               # list header
        r_list.append(rel)
        rel += a16(8 * counts[gi])
        r_blocks.append(rel)
        rel += sum(len(b) for b in blobs_per_group[gi])
        rel = a16(rel)
    r_slots = rel
    rel += a16(4 * nslot)
    r_pa, r_pay, r_pb = [], [], []
    for _s in range(nslot):
        pa_s, pay_s, pb_s = [], [], []
        rel = a16(rel)
        r_tabslot = rel
        rel += a16(8 * ngrp)
        for gi in range(ngrp):
            pa_s.append(rel)
            rel += a16(4 + 4 * counts[gi])
            targets = []
            for nv in verts[gi]:
                targets.append(rel)
                rel += a16(nv)
            pay_s.append(targets)
            pb_s.append(rel)
            rel += a16(4 * counts[gi])
        r_pa.append((r_tabslot, pa_s))
        r_pay.append(pay_s)
        r_pb.append(pb_s)
    total = a16(rel)

    # --- step 2: reserve and write with the real addresses ---
    at = p.append(b'\x00' * total)
    B = lambda r: at + r                        # file offset
    W = lambda r: p.V(at + r)                   # virtual address

    struct.pack_into('<I', p.data, B(0), ngrp)
    p.data[B(4):B(16)] = b'\xCD' * 12
    for gi in range(ngrp):
        struct.pack_into('<IHH', p.data, B(r_gtab + 8 * gi),
                         W(r_list[gi]), counts[gi], 0xCDCD)
        struct.pack_into('<I', p.data, B(r_list[gi] - 16), counts[gi])
        p.data[B(r_list[gi] - 12):B(r_list[gi])] = b'\xCD' * 12
        o = r_blocks[gi]
        for j, blob in enumerate(blobs_per_group[gi]):
            struct.pack_into('<IHH', p.data, B(r_list[gi] + 8 * j),
                             W(o), len(blob) // 16, verts[gi][j])
            p.data[B(o):B(o) + len(blob)] = blob
            o += len(blob)

    for s in range(nslot):
        struct.pack_into('<I', p.data, B(r_slots + 4 * s), W(r_pa[s][0]))
        tabslot, pa_s = r_pa[s]
        for gi in range(ngrp):
            struct.pack_into('<II', p.data, B(tabslot + 8 * gi),
                             W(pa_s[gi]), W(r_pb[s][gi]))
            struct.pack_into('<I', p.data, B(pa_s[gi]), counts[gi])
            for j, nv in enumerate(verts[gi]):
                struct.pack_into('<I', p.data, B(pa_s[gi] + 4 + 4 * j), nv)
                q = r_pay[s][gi][j]
                p.data[B(q):B(q) + nv] = fill(nv)
                struct.pack_into('<I', p.data, B(r_pb[s][gi] + 4 * j), W(q))

    struct.pack_into('<I', p.data, 0x0C, len(p.data) - 0x80)
    return B(r_gtab), B(r_slots)


def tile_grid(p):
    """(cell_x, origin_x, cell_z, origin_z) fitted from the file itself.

    Every component carries a culling tile box at +0x14 (x0, x1, z0, z1 as
    bytes) and the placed instances also carry a real position, so the grid can
    be recovered by least squares instead of being assumed. It comes out at
    exactly 40 units per cell in all four cities, with a per-city origin and a
    mean error of about a quarter of a cell - which is just what quantising to
    tiles costs.
    """
    R = 0x80
    nh, hp = p.U(R + 0x14), p.U(R + 0x18)
    if not p.inside(p.F(hp)):
        return None
    hb = p.F(hp)
    pts = []
    for h in range(nh if nh < 64 else 0):
        e = hb + 20 * h
        cnt, arr = p.U(e + 0x0C), p.U(e + 0x10)
        if not p.inside(p.F(arr)) or cnt > 20000:
            continue
        ab = p.F(arr)
        for k in range(cnt):
            c = ab + 96 * k
            try:
                q = struct.unpack_from('<3f', p.data, c + 0x44)
            except Exception:
                continue
            if not all(-5000 < x < 5000 for x in q):
                continue
            pts.append((q[0], q[2],
                        (p.data[c + 0x14] + p.data[c + 0x15] + 1) / 2.0,
                        (p.data[c + 0x16] + p.data[c + 0x17] + 1) / 2.0))
    if len(pts) < 32:
        return None

    def fit(ti, vi):
        n = len(pts)
        st = sum(q[ti] for q in pts)
        sv = sum(q[vi] for q in pts)
        stt = sum(q[ti] * q[ti] for q in pts)
        stv = sum(q[ti] * q[vi] for q in pts)
        den = n * stt - st * st
        if not den:
            return None
        a = (n * stv - st * sv) / den
        return a, (sv - a * st) / n

    fx, fz = fit(2, 0), fit(3, 1)
    if not fx or not fz or not (20.0 < fx[0] < 80.0 and 20.0 < fz[0] < 80.0):
        return None
    return fx[0], fx[1], fz[0], fz[1]


def set_tile_box(p, comp, radius, grid):
    """Recompute the culling tile box of one instance for a new radius."""
    cx, ox, cz, oz = grid
    x, _y, z = struct.unpack_from('<3f', p.data, comp + 0x44)
    x0 = max(0, min(255, int((x - radius - ox) // cx)))
    x1 = max(0, min(255, int((x + radius - ox) // cx)))
    z0 = max(0, min(255, int((z - radius - oz) // cz)))
    z1 = max(0, min(255, int((z + radius - oz) // cz)))
    old = tuple(p.data[comp + 0x14:comp + 0x18])
    p.data[comp + 0x14:comp + 0x18] = bytes((x0, x1, z0, z1))
    return old, (x0, x1, z0, z1)


def pick_scale(blocks):
    """The retail scale is always a power of two; take the smallest that does not saturate."""
    m = max((abs(c) for p, _u, _nr, _o in blocks for v in p for c in v), default=1.0)
    e = -12
    while m / (2.0 ** e) > 32000 and e < 6:
        e += 1
    return 2.0 ** e


# ------------------------------------------------------------------ .pck
class Pck:
    def __init__(self, path):
        self.data = bytearray(open(path, 'rb').read())
        self.va = struct.unpack_from('<I', self.data, 0)[0]
        self.base = self.va - 0x80
        self.orig = len(self.data)

    def F(self, v):
        return v - self.base

    def V(self, o):
        return o + self.base

    def U(self, o):
        return struct.unpack_from('<I', self.data, o)[0]

    def inside(self, o):
        return 0x80 <= o <= len(self.data) - 4

    def append(self, blob, align=16):
        """Write a new block and return its offset.

        If `self.free` exists, a DEAD range that fits is used instead of growing
        the file. That matters because the city `.pck` has a size ceiling -
        measured in game, 12.08 MB loads and 13.88 MB hangs before reaching RAM -
        and porting a whole city by appending almost doubles the file.

        Reusing dead space is MUCH safer than compacting afterwards: no byte
        moves, so no pointer needs translating. And translation by sweeping is
        unsafe by nature - the two 16-bit counts of tokyo's group descriptor are
        1720 each, packed they give 0x06B806B8, which falls inside the VA window
        and becomes a "pointer" to any blind scan. In atlanta the same counts
        give 0x05310531 and go unnoticed; it was only luck.
        """
        # BEST FIT, not the first that fits: with first-fit the big range is
        # consumed by a small allocation and the pool fragments - measured, 5.39
        # MB of free space gave back only 0.36 MB of file.
        n = len(blob)
        free = getattr(self, 'free', None)
        if free:
            best_ = -1
            best_leftover = None
            for i, (a, size_) in enumerate(free):
                start_ = a + (-a % align)
                leftover = (a + size_) - (start_ + n)
                if leftover >= 0 and (best_leftover is None or leftover < best_leftover):
                    best_, best_leftover = i, leftover
                    if leftover < align:
                        break
            if best_ >= 0:
                a, size_ = free[best_]
                start_ = a + (-a % align)
                self.data[start_:start_ + n] = blob
                if best_leftover >= 64:
                    free[best_] = (start_ + n, best_leftover)
                else:
                    free.pop(best_)
                return start_
        while len(self.data) % align:
            self.data += b'\xCD'
        at = len(self.data)
        self.data += blob
        while len(self.data) % align:
            self.data += b'\xCD'
        struct.pack_into('<I', self.data, 0x0C, len(self.data) - 0x80)
        return at

    def save(self, path):
        with open(path, 'wb') as f:
            f.write(bytes(self.data))


def find_model(p, name):
    """Offset of the START of the model object (its vtable sits at +0x28)."""
    for o in range(0x80, len(p.data) - 0x80, 4):
        if p.U(o) == MODEL_VTABLE:
            raw = bytes(p.data[o + 0x48:o + 0x70]).split(b'\0')[0]
            if raw.decode('latin1', 'replace') == name:
                return o - VTABLE_AT, o
    return None, None


def instances_of(p, model_off):
    m = p.F(p.U(0x9C))
    arr = p.F(p.U(m + 0x0C))
    out = []
    for k in range(p.U(m + 4)):
        r = p.F(p.U(arr + 4 * k))
        if p.F(p.U(r + INST_MODEL)) == model_off:
            out.append(r)
    return out


def append_array(d, body, n, align=16):
    """Append an array WITH THE 16-BYTE HEADER the game expects.

    Found in the mesh constructor (`sub_2A8FA0` in the ELF): it allocates
    `n*8 + 16`, writes the COUNT at the start of the buffer and keeps the
    pointer pointing 16 BYTES LATER. So `[ptr - 16]` is the element count.
    Checked on retail: 6/6 groups and 6/6 block lists of the target. I never
    wrote this -- it stayed 0xCDCDCDCD there.
    """
    pad = 0xCD
    while len(d) % align:
        d.append(pad)
    d.extend(struct.pack("<I", n))
    d.extend(bytes([pad]) * 12)
    body_at = len(d)
    d.extend(body)
    while len(d) % align:
        d.append(pad)
    struct.pack_into("<I", d, 0x0C, len(d) - 0x80)
    return body_at



def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--xmod', default=XMOD)
    ap.add_argument('--src', default=SRC)
    ap.add_argument('--target', default=TARGET)
    ap.add_argument('--out-path', default=None)
    ap.add_argument('--keep-sphere', action='store_true',
                    help='do not touch the bound sphere or the instance radius')
    ap.add_argument('--blocks', type=int, default=0,
                    help='cut at N blocks (to bisect the block count)')
    ap.add_argument('--fixed-uv', metavar='U,V',
                    help='lock the UV to one value; the surface comes out in one flat'
                         ' colour, which keeps the SHAPE readable while the'
                         ' texture still points at the wrong atlas')
    ap.add_argument('--normals', action='store_true',
                    help='emit the normal/colour stream at ITOP+2. Only 7.3%% of'
                         ' city blocks have that stream and the target mesh may'
                         ' not be one of them -- check before turning it on')
    ap.add_argument('--light', type=int, default=LIGHT,
                    help='per-vertex palette index (0-255). The MC3 city does'
                         ' not light by angle: this byte picks a colour'
                         ' baked into a 256-entry table. 148 is the retail'
                         ' median; 255 (the mode) comes out bright and flat')
    ap.add_argument('--scale', type=float, default=1.0,
                    help='multiply the size of the MC2 model (1.0 = real size)')
    a = ap.parse_args()

    globals()['LIGHT'] = max(0, min(255, a.light))
    globals()['NORMALS'] = bool(a.normals)
    if getattr(a, 'fixed_uv', None):
        globals()['FIXED_UV'] = tuple(float(x) for x in a.fixed_uv.split(','))
    v, t1, adj, strips = read_xmod(a.xmod)
    blocks, centre = strips_to_blocks(v, t1, adj, strips)
    if a.scale != 1.0:
        blocks = [([tuple(c * a.scale for c in q) for q in p], u, nr, o)
                  for p, u, nr, o in blocks]
    if a.blocks:
        blocks = blocks[:a.blocks]
    pts = [c for p, _u, _nr, _o in blocks for c in p]
    dx = max(q[0] for q in pts) - min(q[0] for q in pts)
    dy = max(q[1] for q in pts) - min(q[1] for q in pts)
    dz = max(q[2] for q in pts) - min(q[2] for q in pts)
    rad = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
    tris = sum(len(p) - 2 for p, _u, _nr, _o in blocks)
    print('MC2  %s' % os.path.basename(a.xmod))
    print('     %d verts, %d strips -> %d blocks, %d triangles' %
          (len(v), len(strips), len(blocks), tris))
    print('     size %.1f x %.1f x %.1f, radius %.1f' % (dx, dy, dz, rad))

    p = Pck(a.src)
    mo, vt = find_model(p, a.target)
    if mo is None:
        raise SystemExit('model %r not found' % a.target)
    mesh = p.F(p.U(vt + MESH_LOD0))
    old_radius = struct.unpack_from('<f', p.data, vt + 0x10)[0]
    inst = instances_of(p, mo)
    print('\nMC3  target %s' % a.target)
    print('     object @%#x (vtable @%#x), mesh @%#x, original radius %.1f, %d instance(s)'
          % (mo, vt, mesh, old_radius, len(inst)))
    for r in inst:
        print('        instance @%#x at (%8.1f, %6.1f, %8.1f)'
              % (r, *struct.unpack_from('<3f', p.data, r + 0x44)))

    sc = pick_scale(blocks)
    print('\n     packet scale: %g' % sc)

    # 1) new blocks, appended
    #    The VU1 double-buffer half comes from the POSITION in the group's list,
    #    not from the source block -- so the blocks are split among the groups
    #    BEFORE encoding, in contiguous slices (the old `ptrs[i::ngrp]` still
    #    shuffled blocks between groups of different materials).
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    n, r = divmod(len(blocks), ngrp)
    cut, k = [], 0
    for i in range(ngrp):
        m = max(1, n + (1 if i < r else 0))
        cut.append(blocks[k:k + m] or [blocks[0]])
        k += m
    if k < len(blocks):
        cut[-1] += blocks[k:]

    blobs_per_group = [[make_block(pos, uv, nr, sc, j % 2 == 1)
                        for j, (pos, uv, nr, _origin_odd) in enumerate(group)]
                       for group in cut]

    # 2) THE NUMBER OF GROUPS HAS TO STAY THE SAME.
    #    The mesh keeps the count in a u16 at +0x08 (and again at +0x20) and has
    #    a material table with one shader index PER GROUP. The first test merged
    #    everything into one group and left the count at 6: the game read the 5
    #    following entries, which were 0xCDCDCDCD, as pointers -- and hung on
    #    entering the city. So the blocks are spread among the groups that
    #    already exist, and the material table stays valid.
    #
    #    The layout is the same as retail's and `test_vu2.py`'s (which passed in
    #    game): a group table with a 16-byte header, and per group a contiguous
    #    region [header][entries][blocks glued together].
    grp_at, t14_at = emit_mesh_region(p, mesh, cut, blobs_per_group)
    struct.pack_into('<I', p.data, mesh + MESH_BLOCKS, p.V(grp_at))
    struct.pack_into('<I', p.data, mesh + MESH_PERBLOCK, p.V(t14_at))
    print('     %d blocks in %d groups (%s), table @%#x, per-block chain @%#x'
          % (sum(len(x) for x in cut), ngrp,
             '+'.join(str(len(f)) for f in cut), grp_at, t14_at))

    # 3) bound sphere: without it the game culls the building away
    if getattr(a, 'keep_sphere', False):
        print('     bound sphere KEPT (radius %.1f) -- the instance cell bytes'
              % old_radius)
        print('     are precomputed, touching the radius can misalign the culling')
    else:
        struct.pack_into('<fff', p.data, vt + SPHERE_AT, 0.0, dy / 2.0, 0.0)
        struct.pack_into('<f', p.data, vt + 0x10, rad)
        for r in inst:
            struct.pack_into('<f', p.data, r + INST_RADIUS, rad)
        print('     bound sphere: radius %.1f -> %.1f (in the %d instances too)'
              % (old_radius, rad, len(inst)))
        # The sphere alone is not enough: culling also uses the TILE AABB at
        # component+0x14, four precomputed bytes. A building 6x larger with the
        # old box vanishes as the camera moves. The grid comes out fitted from the
        # file itself (40-unit cells, origin per city).
        cell_grid = tile_grid(p)
        if cell_grid is None:
            print('     WARNING: could not fit the tile grid; the culling AABB'
                  ' stayed the original')
        else:
            print('     tile grid: cell %.2f x %.2f, origin (%.0f, %.0f)'
                  % (cell_grid[0], cell_grid[2], cell_grid[1], cell_grid[3]))
            for r in inst:
                old_one, new_ = set_tile_box(p, r, rad, cell_grid)
                print('     tile AABB of instance @%#x: %s -> %s'
                      % (r, list(old_one), list(new_)))

    out = a.out_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  os.path.basename(a.src))
    p.save(out)
    orig = open(a.src, 'rb').read()
    new = open(out, 'rb').read()
    changed = sum(1 for x, y in zip(orig, new) if x != y)
    print('\noutput: %s' % out)
    print('     %d -> %d bytes | %d bytes changed in the original region'
          % (len(orig), len(new), changed))


if __name__ == '__main__':
    main()
