"""
mc3_mesh_convert.py - multi-directional mesh converter for Midnight Club.

Formats (read AND write):
    .psppck   PSP mesh   - MC3 PSP (magic CC991609) and MC:LA Remix (38545A00)
    .pck      PS2 mesh   - single mesh.pck (tag 980F7A00)
    .mesh     Angel Studios text mesh - the source format in ASSETS/vehicle/va_*,
              same one zKiwi's io_AngelStudios_mesh Blender addon reads/writes
    .mod      Angel Studios `version: 1.10` text mesh (READ only) - the dialect
              gfxModelBase::LoadMod reads, shipped by MC3 in ASSETS/model. MC2
              writes `.xmod` in the same dialect, so this is the MC2 -> PS2
              route. Without --scale the fixed-point exponent is fitted to the
              data, the way the game fits it in rmcModelGeom::Load; the fixed
              1/4096 would saturate a city mesh in world coordinates.
    .obj      Wavefront OBJ
    .xbck     Xbox mesh - single .mesh.xbck (read AND write) or a whole
              container through read_xbck_container() (read only); layout
              mapped from TahmidAlam-git's mesh_to_obj.py
              (github.com/TahmidAlam-git/MC3_extraction_tools)

Usage:
    python mc3_mesh_convert.py input output
    python mc3_mesh_convert.py bumper.psppck bumper.pck        # PSP -> PS2
    python mc3_mesh_convert.py bumper.pck bumper.obj           # PS2 -> OBJ
    python mc3_mesh_convert.py bumper.pck bumper.mesh          # PS2 -> Blender
    python mc3_mesh_convert.py wheel.mesh wheel.pck            # Blender -> PS2
    python mc3_mesh_convert.py l_prop_cone.xmod cone.pck --materials=5   # MC2 -> PS2
    python mc3_mesh_convert.py hood.mesh.xbck hood.obj         # Xbox -> OBJ
    python mc3_mesh_convert.py hood.mesh.pck hood.mesh.xbck    # PS2 -> Xbox
    python mc3_mesh_convert.py model.obj out.psppck --mc3      # OBJ -> MC3 PSP
    python mc3_mesh_convert.py mcla.psppck mc3.psppck --mc3    # MC:LA -> MC3 PSP

Options:
    --keep-strips      write the .pck from the SOURCE strips instead of
                       re-stripifying. Only cuts strips longer than a packet,
                       re-seeding the continuation with two ADC vertices. Much
                       closer to what the retail exporter produced: rewriting 40
                       retail vehicle pieces gives 1459 packets / 741,696 bytes
                       against 9067 / 1,404,368 the other way, with the retail
                       files themselves at 694,352. Triangles and winding come
                       out identical either way.
    --mc3 / --mcla     psppck flavor to write (default: keep source, else mcla)
    --id N             piece id (header @0x99 psp / @0x86 ps2)
    --materials a,b,c  override shader index per group
    --scale F          raw<->world scale (default 1/4096)
    -v                 verbose

All formats share the same geometry model: triangle strips where the triangle
closing at index i is (i-2,i-1,i) for even i and (i-1,i-2,i) for odd i, with
degenerate (repeated) vertices used to restart strips.

Per-vertex colour (the .mesh `Cpv` array, the Diffuse channel of the vertex
format) survives .mesh <-> .pck in both directions. It rides the block as the
UNPACK V4-8 stream, command 0x6E, at the address slot right above the position
stream with the USN bit set (0x4118 even / 0x4245 odd against 0x00EE / 0x021B).
Sources that carry no colour (OBJ, xbck, psppck) get DEFAULT_CPV, which is the
constant this writer used to emit for every vertex, so their output is
unchanged. Tex1 and skinning (Skinned/PosSkin/Offset) are still skipped: not
one of the 341 retail .mesh files uses them, so there is nothing to validate a
guess against.
"""
import math
import os
import re
import struct
import sys

DEFAULT_SCALE = 1.0 / 4096.0
MAGIC_MC3 = bytes.fromhex("cc991609")
MAGIC_MCLA = bytes.fromhex("38545a00")
TAG_PS2 = bytes.fromhex("980f7a00")
RUNTIME_PS2_MESH_VTABLES = (0x00626D98,)
MAX_BLOCK_VERTS = 40


# ---------------------------------------------------------------------------
# Intermediate representation
# ---------------------------------------------------------------------------

# Diffuse a vertex gets when the source format carries no colour (OBJ, xbck,
# psppck). It is what write_pck used to hard-code for every vertex, so a
# colourless source still produces the exact same bytes it did before.
DEFAULT_CPV = (128, 128, 128, 0)


class Vtx:
    __slots__ = ("pos", "uv", "nrm", "skip", "extra", "cpv")

    def __init__(self, pos, uv, nrm, skip=False, extra=None, cpv=None):
        self.pos = pos      # raw ints (s16), world = pos * scale
        self.uv = uv        # floats, 1.0 = one texture repeat
        self.nrm = nrm      # floats, unit vector
        self.skip = skip    # ADC flag (PS2): skip the triangle closing here
        self.extra = extra  # raw per-vertex bytes kept verbatim (Xbox: float
                            # position + packed normal + tangent), so an
                            # xbck->xbck round-trip is byte exact
        # Diffuse (the .mesh `Cpv` array) as 4 bytes 0..255. rmcGeometry::
        # ComputeFVF (0x2AA178) turns the Diffuse channel on when the mshMesh
        # has Cpv entries and no skinning, and it lands in the block as the
        # UNPACK V4-8 stream (command 0x6E). Every one of the 341 retail .mesh
        # files carries Cpv, so dropping it loses a channel the game expects.
        self.cpv = DEFAULT_CPV if cpv is None else cpv


class Mesh:
    def __init__(self):
        self.groups = []      # list of dicts: {"material": int, "strips": [[Vtx,...],...]}
        self.piece_id = 0
        self.scale = DEFAULT_SCALE
        self.flavor = None    # "mc3" | "mcla" | None
        self.virtual_addr = None   # keep the source file's VA on round-trips

        self.source = ""

    def stats(self):
        ns = sum(len(g["strips"]) for g in self.groups)
        nv = sum(len(s) for g in self.groups for s in g["strips"])
        nt = sum(len(tris_of_strip(s)) for g in self.groups for s in g["strips"])
        return len(self.groups), ns, nv, nt


def tris_of_strip(strip):
    """Triangles a strip actually draws (engine parity, skipping ADC-flagged and
    degenerate faces)."""
    out = []
    for i in range(2, len(strip)):
        if strip[i].skip:
            continue
        a, b, c = (i-2, i-1, i) if i % 2 == 0 else (i-1, i-2, i)
        pa, pb, pc = strip[a].pos, strip[b].pos, strip[c].pos
        if pa == pb or pb == pc or pa == pc:
            continue
        out.append((a, b, c))
    return out


def _s8(b):
    return b - 256 if b >= 128 else b


def _clampi(v, lo, hi):
    return max(lo, min(hi, int(round(v))))


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_psppck(path):
    d = open(path, "rb").read()
    base = struct.unpack_from("<I", d, 0)[0] - 0x80
    magic = d[0x80:0x84]
    flavor = "mc3" if magic == MAGIC_MC3 else "mcla"
    mesh_list = struct.unpack_from("<I", d, 0x84)[0] - base
    mat_off = struct.unpack_from("<I", d, 0x88)[0] - base
    dmg_ptr = struct.unpack_from("<I", d, 0x8c)[0]
    mesh_data = struct.unpack_from("<I", d, 0x90)[0] - base
    piece_id = d[0x99]
    num = d[0x9b]                      # works for both MC3 and MC:LA

    m = Mesh()
    m.piece_id = piece_id
    m.scale = DEFAULT_SCALE
    m.flavor = flavor
    m.virtual_addr = struct.unpack_from("<I", d, 0)[0]
    m.source = "psppck:" + flavor

    for g in range(num):
        e = mesh_list + g * 0xC
        p = struct.unpack_from("<I", d, e)[0] - base
        cnt = struct.unpack_from("<H", d, e + 4)[0]
        mat = struct.unpack_from("<H", d, mat_off + 2 * g)[0]
        # The PSP subranges are pieces of one strip with transition vertices in
        # between.  Flattening the complete span and rebuilding only the faces it
        # actually draws is required for trim meshes that cross a subrange seam.
        subs = []
        po = p
        for k in range(cnt):
            rp = struct.unpack_from("<H", d, po)[0]
            rl = struct.unpack_from("<H", d, po + 2)[0]
            subs.append((rp, rl))
            po += 4
        if not subs:
            continue
        g0 = min(s[0] for s in subs)
        g1 = max(s[0] + s[1] for s in subs)
        if mesh_data + g1 * 14 > len(d):
            continue
        strip = []
        for i in range(g0, g1):
            o = mesh_data + i * 14
            ru, rv = struct.unpack_from("<hh", d, o)
            uv = ((ru + 32768) / 32767.0, (rv + 32768) / 32767.0)
            nrm = (_s8(d[o+4]) / 127.0, _s8(d[o+5]) / 127.0, _s8(d[o+6]) / 127.0)
            pos = struct.unpack_from("<hhh", d, o + 8)
            strip.append(Vtx(tuple(pos), uv, nrm, False))
        m.groups.append({"material": mat, "strips": [strip]})
    return m


def find_pointers(path_or_bytes):
    """All pointers in a pck/psppck, found by scanning 4-byte-aligned u32 values
    inside [virtual_address, virtual_address + body_size] (header @0x00 and
    @0x0C). Almost no other data lands in that window -- which is likely why
    each file gets its own virtual address. Returns [(file_offset, value)]."""
    d = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
    vbase = struct.unpack_from("<I", d, 0)[0]
    body = struct.unpack_from("<I", d, 0x0C)[0]
    out = []
    for o in range(0, len(d) - 3, 4):
        v = struct.unpack_from("<I", d, o)[0]
        if vbase <= v <= vbase + body:
            out.append((o, v))
    return out


def read_pck(path):
    d = open(path, "rb").read()
    N = len(d)
    vbase = struct.unpack_from("<I", d, 0)[0]

    def toff(v):
        return v - vbase + 0x80

    tag = struct.unpack_from("<I", d, 0x80)[0]
    if d[0x82:0x84] not in (b"\x7a\x00", b"\xbc\x00") and \
            tag not in RUNTIME_PS2_MESH_VTABLES:
        raise ValueError("not a PS2 mesh.pck (unknown mesh token/vtable at 0x80)")
    num = struct.unpack_from("<I", d, 0x88)[0]
    mat_off = toff(struct.unpack_from("<I", d, 0x8c)[0])
    main_off = toff(struct.unpack_from("<I", d, 0x90)[0])
    # A slice cut out of a container keeps the container's pointers, so they can
    # fall outside this file: degrade to a single unnamed group instead.
    if not (1 <= num <= 4096 and 0 <= mat_off <= N-2*num and 0 <= main_off <= N-8*num):
        num, mat_off, main_off = 0, 0, 0
    mats = [struct.unpack_from("<H", d, mat_off + 2*i)[0] for i in range(num)]
    mains = sorted(toff(struct.unpack_from("<I", d, main_off + 8*i)[0]) for i in range(num))

    m = Mesh()
    m.piece_id = struct.unpack_from("<H", d, 0x86)[0]
    m.source = "pck"

    # Block discovery: follow the real pointers (main pointer -> per-group array
    # of block pointers: u32 ptr + u16 qwords + u16 vertex count). Deterministic,
    # unlike scanning for VIF tags which can match inside vertex data. Falls back
    # to a tag scan for files whose header is broken (e.g. slices cut out of a
    # container).
    # Each block pointer covers a REGION holding several concatenated VIF blocks
    # (u32 ptr + u16 size in qwords + u16 vertex count). Use those regions to
    # bound the search and to assign each block to its group.
    regions = []      # (start, end, group)
    for gi in range(num):
        try:
            mp = toff(struct.unpack_from("<I", d, main_off + 8*gi)[0])
            cnt = struct.unpack_from("<H", d, main_off + 8*gi + 4)[0]
            if not (0 <= mp <= N-8) or not (1 <= cnt <= 4096):
                continue
            for k in range(cnt):
                e = mp + 8*k
                bptr = toff(struct.unpack_from("<I", d, e)[0])
                qw = struct.unpack_from("<H", d, e + 4)[0]
                if 0 <= bptr < N and qw:
                    regions.append((bptr, min(N, bptr + qw*16), gi))
        except Exception:
            pass

    def blocks_in(lo, hi):
        # block starts are 16-byte aligned; require a vertex tag right after
        out = []
        for off in range(lo, hi, 16):
            if d[off:off+3] in (b"\x98\x00\x02", b"\xc5\x01\x02") and \
               re.search(rb"(\xee\x00|\x1b\x02).\x69", d[off:off+0x30], re.DOTALL):
                out.append(off)
        return out

    blocks, block_group = [], {}
    for (lo, hi, gi) in regions:
        for bo in blocks_in(lo, hi):
            blocks.append(bo)
            block_group[bo] = gi
    if not blocks:      # broken header (e.g. slice cut out of a container)
        blocks = [mm.start() for mm in re.finditer(rb"(\x98\x00\x02.|\xc5\x01\x02.)", d, re.DOTALL)
                  if re.search(rb"(\xee\x00|\x1b\x02).\x69", d[mm.start():mm.start()+0x30], re.DOTALL)]
    blocks.sort()

    import bisect
    per = {}
    for bi, bo in enumerate(blocks):
        gi = block_group.get(bo)
        if gi is None:
            gi = max(0, bisect.bisect_right(mains, bo) - 1)
        scale = struct.unpack_from("<f", d, bo + 4)[0] or DEFAULT_SCALE
        m.scale = scale
        bend = blocks[bi+1] if bi+1 < len(blocks) else N
        vm = re.search(rb"(\xee\x00|\x1b\x02)(.)\x69", d[bo:bo+0x40], re.DOTALL)
        vc = vm.group(2)[0]
        vd = bo + vm.start() + 4

        def find_tag(pat, want):
            """Locate a stream tag inside this block only, requiring the count
            byte to match the vertex count (avoids matching vertex data)."""
            for mm in re.finditer(pat, d[bo:bend], re.DOTALL):
                if mm.group(2)[0] == want and (bo + mm.start()) % 4 == 0:
                    return mm
            return None

        um = find_tag(rb"(\xc4\x00|\xf1\x01)(.)\x65", vc)
        nm = find_tag(rb"(\x9a\x00|\xc7\x01)(.)\x6a", vc)
        # Diffuse: UNPACK V4-8 with the USN bit set, one address slot (42 qw)
        # above the position stream -- 0x118/0x4245 against 0xEE/0x21B.
        cm = find_tag(rb"(\x18\x41|\x45\x42)(.)\x6e", vc)
        strip = []
        for i in range(vc):
            pos = struct.unpack_from("<hhh", d, vd + 6*i)
            uv = (0.0, 0.0)
            if um:
                uo = bo + um.start() + 4 + 4*i
                u, v = struct.unpack_from("<HH", d, uo)
                uv = (u / 4096.0, v / 4096.0)
            nrm, skip = (0.0, 0.0, 1.0), False
            if nm:
                no = bo + nm.start() + 4 + 3*i
                nrm = (_s8(d[no]) / 127.0, _s8(d[no+1]) / 127.0, _s8(d[no+2]) / 127.0)
                skip = (d[no] & 1) == 1
            cpv = DEFAULT_CPV
            if cm:
                co = bo + cm.start() + 4 + 4*i
                cpv = (d[co], d[co+1], d[co+2], d[co+3])
            strip.append(Vtx(tuple(pos), uv, nrm, skip, cpv=cpv))
        per.setdefault(gi, []).append(strip)
    # Each block is read as its own strip. PS2 blocks that continue a strip
    # re-seed with 2 ADC-flagged vertices, so the seam triangles are already
    # suppressed -- no merging needed (merging would drop them twice).
    for gi in sorted(per):
        m.groups.append({"material": mats[gi] if gi < len(mats) else 0, "strips": per[gi]})
    return m


def read_obj(path, scale=DEFAULT_SCALE):
    V, VT, VN = [], [], []
    faces_by_mat = {}
    cur = "default"
    piece_id = 0
    for line in open(path, "r", encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "#":
            # scale/id written by our own exporter, so OBJ round-trips exactly
            if len(t) >= 3 and t[1] == "mc3_scale":
                try:
                    scale = float(t[2])
                except ValueError:
                    pass
            elif len(t) >= 3 and t[1] == "mc3_id":
                try:
                    piece_id = int(t[2])
                except ValueError:
                    pass
            continue
        if t[0] == "v":
            V.append(tuple(float(x) for x in t[1:4]))
        elif t[0] == "vt":
            VT.append((float(t[1]), float(t[2]) if len(t) > 2 else 0.0))
        elif t[0] == "vn":
            VN.append(tuple(float(x) for x in t[1:4]))
        elif t[0] == "usemtl":
            cur = " ".join(t[1:])
        elif t[0] == "f" and len(t) >= 4:
            idx = []
            for tok in t[1:]:
                p = tok.split("/")
                vi = int(p[0]); vi = vi-1 if vi > 0 else len(V)+vi
                ti = None
                if len(p) > 1 and p[1]:
                    ti = int(p[1]); ti = ti-1 if ti > 0 else len(VT)+ti
                ni = None
                if len(p) > 2 and p[2]:
                    ni = int(p[2]); ni = ni-1 if ni > 0 else len(VN)+ni
                idx.append((vi, ti, ni))
            for k in range(1, len(idx)-1):
                faces_by_mat.setdefault(cur, []).append((idx[0], idx[k], idx[k+1]))

    m = Mesh()
    m.scale = scale
    m.piece_id = piece_id
    m.source = "obj"
    inv = 1.0 / scale
    for name, faces in faces_by_mat.items():
        mat = 0
        mm = re.match(r"\s*([0-9A-Fa-f]{1,4})\b", name)
        if mm:
            try:
                mat = int(mm.group(1), 16)
            except ValueError:
                mat = 0
        # smoothed normals for vertices without vn
        acc = {}
        for f in faces:
            p = [V[i[0]] for i in f]
            n = _face_normal(p[0], p[1], p[2])
            for i in f:
                a = acc.get(i[0], (0.0, 0.0, 0.0))
                acc[i[0]] = (a[0]+n[0], a[1]+n[1], a[2]+n[2])
        sm = {}
        for k, a in acc.items():
            l = math.sqrt(a[0]**2 + a[1]**2 + a[2]**2) or 1.0
            sm[k] = (a[0]/l, a[1]/l, a[2]/l)

        def mkv(ref):
            vi, ti, ni = ref
            p = V[vi]
            pos = (_clampi(p[0]*inv, -32768, 32767), _clampi(p[1]*inv, -32768, 32767),
                   _clampi(p[2]*inv, -32768, 32767))
            uv = VT[ti] if (ti is not None and ti < len(VT)) else (0.0, 0.0)
            nrm = VN[ni] if (ni is not None and ni < len(VN)) else sm.get(vi, (0.0, 0.0, 1.0))
            return Vtx(pos, uv, nrm, False)

        strips = _stripify([tuple(f) for f in faces])
        out = []
        for st in strips:
            out.append([mkv(r) for r in st])
        m.groups.append({"material": mat, "strips": out})
    return m


def read_xbck(path, scale=None, start=None):
    """Xbox .mesh.xbck. Layout mapped by TahmidAlam-git's mesh_to_obj.py
    (github.com/TahmidAlam-git/MC3_extraction_tools).

    Unlike PS2/PSP this is a D3D-style buffer: float positions and UVs in one
    interleaved stream, then a strip index list, so there are no VIF packets and
    no ADC flag.

    Header (from 0x80): +0x00 vtable ptr | +0x07 VERTEX STRIDE (0x1C = 28) |
    +0x08 u16 face_sections | +0x0A u16 ff_sections | +0x10 16 unknown bytes |
    then face_sections u16 | align 16 | u16 set_count + 2 | set_count*24 |
    12 bytes | align 16.

    Then the vertex stream: 3 floats position, (stride-20) unknown bytes
    (packed normal/colour), 2 floats UV. It ends where the face header starts,
    recognised by u32==1 followed by a zero u32 -- the format stores no vertex
    count, which is why the original tool scans for it too.

    Face header: one 12-byte record per section (u32 1 | u16 start | u16 count |
    u32 0), then the u16 index list, read as one triangle strip per section.
    """
    d = path if isinstance(path, (bytes, bytearray)) else open(path, "rb").read()
    if len(d) < 0x100:
        raise ValueError("file too small for a .xbck mesh")

    def u16(o):
        return struct.unpack_from("<H", d, o)[0]

    def u32(o):
        return struct.unpack_from("<I", d, o)[0]

    def align16(o):
        return o + (-o % 16)

    o = 0x80 if start is None else start
    stride = d[o + 7]
    face_sections = u16(o + 8)
    ff_sections = u16(o + 0x0A)
    if not (12 < stride <= 64) or not (1 <= face_sections <= 4096):
        raise ValueError("not a .xbck mesh header (stride=%d sections=%d)"
                         % (stride, face_sections))
    o += 0x10
    o += 16                    # four pointers (material table, sets, tail, indices)
    mat_off = o
    materials = [u16(mat_off + 2*i) for i in range(face_sections)]
    o += face_sections * 2
    o = align16(o)
    set_count = u16(o)
    o += 4
    o += set_count * 24
    o += 12
    o = align16(o)

    # --- vertex stream -----------------------------------------------------
    pos, uv, extra = [], [], []
    uv_off = stride - 8
    limit = len(d) - stride
    while o <= limit:
        if u32(o) == 1 and u32(o + 8) == 0:   # start of the face header
            break
        pos.append(struct.unpack_from("<fff", d, o))
        uv.append(struct.unpack_from("<ff", d, o + uv_off))
        extra.append(bytes(d[o:o + uv_off]))
        o += stride
    if not pos:
        raise ValueError("no vertices found")

    # --- face header -------------------------------------------------------
    ranges = []
    for _ in range(face_sections):
        if o + 12 > len(d):
            break
        ranges.append((u16(o + 4), u16(o + 6)))
        o += 12
    while o < len(d) and d[o] == 0xCD:
        o += 1

    if ff_sections:
        # extra per-vertex arrays used by map models; skip them
        o += 4 + ff_sections * 4
        for _ in range(ff_sections):
            o = align16(o + 12) + 4 * len(pos)
        o = align16(o)

    total = max((a + b) for a, b in ranges) if ranges else 0
    idx = [u16(o + 2*i) for i in range(total) if o + 2*i + 2 <= len(d)]

    if scale is None:
        scale = _fit_scale(pos)
    inv = 1.0 / scale
    m = Mesh()
    m.scale = scale
    m.source = "xbck"
    # The u16 at 0x86 is (stride << 8) | piece_id, so the low byte is the very
    # same piece id the PS2 .mesh.pck stores as a u16 at 0x86. Verified on every
    # mesh present in both builds: 4135 of 4135 match, and no piece id in the
    # PS2 assets exceeds 255, so the byte is always enough to hold it.
    m.piece_id = d[0x86]
    for si, (start, count) in enumerate(ranges):
        strip = []
        for k in range(start, min(start + count, len(idx))):
            vi = idx[k]
            if vi >= len(pos):
                continue
            P = pos[vi]
            ex = extra[vi]
            strip.append(Vtx(tuple(_clampi(c * inv, -32768, 32767) for c in P),
                             uv[vi], unpack_normal3(ex[12:16]) if len(ex) >= 16 else (0.0, 0.0, 1.0),
                             False, ex))
        if len(strip) >= 3:
            mat = materials[si] if si < len(materials) else 0
            m.groups.append({"material": mat, "strips": [strip]})
    if not any(v.extra for g in m.groups for s2 in g["strips"] for v in s2):
        _recompute_normals(m)
    return m


def unpack_normal3(b):
    """Xbox NORMPACKED3: 11/11/10 signed bits in one u32. Confirmed on the 350z
    hood -- decoding +0x0C this way agrees with the geometric normal on 60 of 60
    sampled vertices."""
    v = struct.unpack_from("<I", b, 0)[0]

    def sgn(val, bits):
        return val - (1 << bits) if val >= (1 << (bits - 1)) else val

    n = (sgn(v & 0x7FF, 11) / 1023.0, sgn((v >> 11) & 0x7FF, 11) / 1023.0,
         sgn((v >> 22) & 0x3FF, 10) / 511.0)
    l = math.sqrt(n[0]**2 + n[1]**2 + n[2]**2)
    return (n[0]/l, n[1]/l, n[2]/l) if l else (0.0, 0.0, 1.0)


def pack_normal3(n):
    l = math.sqrt(n[0]**2 + n[1]**2 + n[2]**2) or 1.0
    x = _clampi(n[0]/l * 1023, -1024, 1023) & 0x7FF
    y = _clampi(n[1]/l * 1023, -1024, 1023) & 0x7FF
    z = _clampi(n[2]/l * 511, -512, 511) & 0x3FF
    return struct.pack("<I", x | (y << 11) | (z << 22))


def _recompute_normals(mesh):
    """The Xbox stream keeps its normal packed in bytes this reader does not
    decode, so rebuild smooth normals from the faces -- otherwise everything
    exports flat-shaded with a zero normal."""
    for g in mesh.groups:
        acc = {}
        for strip in g["strips"]:
            for (a, b, c) in tris_of_strip(strip):
                n = _face_normal(strip[a].pos, strip[b].pos, strip[c].pos)
                for i in (a, b, c):
                    k = strip[i].pos
                    p = acc.get(k)
                    acc[k] = n if p is None else (p[0]+n[0], p[1]+n[1], p[2]+n[2])
        for strip in g["strips"]:
            for v in strip:
                n = acc.get(v.pos)
                if not n:
                    continue
                l = math.sqrt(n[0]**2 + n[1]**2 + n[2]**2)
                if l:
                    v.nrm = (n[0]/l, n[1]/l, n[2]/l)


def read_xbck_container(path, scale=None):
    """Every mesh embedded in an Xbox container (vp_*_g.xbck and friends).

    Reuses the LOD slot tables that mc3_pck_embed already reads on .xbck, so the
    meshes come from the real pointers instead of scanning for names.
    Returns one Mesh with every piece merged as separate groups.
    """
    import importlib.util as _il, os as _os
    hp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "mc3_pck_embed.py")
    sp = _il.spec_from_file_location("mc3_pck_embed", hp)
    emb = _il.module_from_spec(sp)
    sys.modules.setdefault("mc3_pck_embed", emb)
    sp.loader.exec_module(emb)

    p = emb.Pck(path)
    d = bytes(p.data)
    out = Mesh()
    out.source = "xbck-container"
    seen = set()
    for lod_va in p.lods().values():
        for (_i, mp, _nm, _id, _off) in p.slots(lod_va):
            if not mp or mp in seen:
                continue
            seen.add(mp)
            try:
                m = read_xbck(d, scale, start=p.F(mp))
            except Exception:
                continue
            if not out.groups:
                out.scale = m.scale
            k = out.scale / m.scale if m.scale else 1.0
            for g in m.groups:
                if k != 1.0:
                    for st in g["strips"]:
                        for v in st:
                            v.pos = tuple(_clampi(c / k, -32768, 32767) for c in v.pos)
                out.groups.append(g)
    return out


XBCK_VTABLE   = 0x005DB670      # class ptr seen on every vehicle .mesh.xbck
XBCK_STRIDE   = 28              # 12 pos + 4 normal + 4 tangent + 8 uv
XBCK_SET_BLOB = bytes.fromhex("c15e8000") + struct.pack("<HH", 0x59, XBCK_STRIDE)
XBCK_SET_TAIL = struct.pack("<H", 0x4a02)
XBCK_DEFAULT_TANGENT = bytes.fromhex("7f7f7f01")


def write_xbck(mesh, path, virtual_base=0x829E9980):
    """Write a single-mesh .xbck. Mapped from the simplest files in the game
    (368 bytes, one section, 4 vertices) -- see read_xbck for the field list.

    The file is load-in-place, so the header pointers are real addresses and are
    emitted as such:
        +0x0C -> material table   +0x10 -> sets
        +0x18 -> tail             +0x1C -> index list
    plus the tail's own pointer to the vertex block, which the game stores with
    the top bit cleared (0x829E9A60 is written 0x029E9A60).

    Per-vertex the normal is repacked as NORMPACKED3. The 4 bytes after it look
    like a tangent (perpendicular to the normal on 80 of 116 hood vertices) but
    are not fully decoded, so a vertex that came from another .xbck keeps its
    original bytes and anything else gets a neutral default.
    """
    # --- flatten to one vertex buffer + one index list ---------------------
    verts, index, sections, mats = [], {}, [], []
    idx = []
    # .xbck has no ADC flag, so a PS2 strip written verbatim would draw the
    # bridge triangles the console hides -- the same trap as the .mesh writer
    # (a 128-triangle hood came out with 146). Rebuild from the drawn triangles,
    # except when the vertices carry their original .xbck bytes, where the
    # strips are already clean and rebuilding would lose the raw passthrough.
    passthrough = any(v.extra for g in mesh.groups
                      for s2 in g["strips"] for v in s2)
    for g in mesh.groups:
        for strip in (g["strips"] if passthrough else clean_strips(g)):
            start = len(idx)
            for v in strip:
                key = (v.pos, v.uv, v.extra, v.nrm)
                j = index.get(key)
                if j is None:
                    j = len(verts)
                    index[key] = j
                    verts.append(v)
                idx.append(j)
            if len(idx) - start >= 3:
                sections.append((start, len(idx) - start))
                mats.append(g["material"] & 0xFFFF)
            else:
                del idx[start:]
    if not sections:
        raise ValueError("nothing to write: no strip with 3+ indices")
    if len(verts) > 0xFFFF:
        raise ValueError("too many vertices for 16-bit indices: %d" % len(verts))

    ns = len(sections)

    def align16(n):
        return n + (-n % 16)

    # --- lay the file out so pointers can be resolved before writing -------
    off_hdr = 0x80
    off_mat = off_hdr + 0x20
    off_setc = align16(off_mat + 2*ns)
    off_sets = off_setc + 4
    off_tail = off_sets + 24*ns
    off_vtx = align16(off_tail + 12)
    off_face = off_vtx + XBCK_STRIDE*len(verts)
    off_idx = align16(off_face + 12*ns)
    total = align16(off_idx + 2*len(idx))

    d = bytearray(b"\xCD" * total)
    d[0:0x80] = bytes(0x80)

    def VA(o):
        return virtual_base + o

    struct.pack_into("<I", d, 0x00, VA(0x80))
    struct.pack_into("<I", d, 0x04, 22)              # resource type, like PS2
    struct.pack_into("<I", d, 0x08, 1)
    struct.pack_into("<I", d, 0x0C, total - 0x80)    # body size

    struct.pack_into("<I", d, off_hdr + 0x00, XBCK_VTABLE)
    d[off_hdr + 0x04:off_hdr + 0x08] = bytes(
        (0, 0, mesh.piece_id & 0xFF, XBCK_STRIDE))
    struct.pack_into("<HH", d, off_hdr + 0x08, ns, 0)
    struct.pack_into("<I", d, off_hdr + 0x0C, VA(off_mat))
    struct.pack_into("<I", d, off_hdr + 0x10, VA(off_sets))
    struct.pack_into("<I", d, off_hdr + 0x14, 0)
    struct.pack_into("<I", d, off_hdr + 0x18, VA(off_tail))
    struct.pack_into("<I", d, off_hdr + 0x1C, VA(off_idx))

    for i, mt in enumerate(mats):
        struct.pack_into("<H", d, off_mat + 2*i, mt)

    struct.pack_into("<I", d, off_setc, ns)
    for i, (start, count) in enumerate(sections):
        o = off_sets + 24*i
        struct.pack_into("<I", d, o, VA(off_face + 12*i + 4))   # -> its face range
        d[o+4:o+12] = XBCK_SET_BLOB
        d[o+12:o+14] = XBCK_SET_TAIL
        struct.pack_into("<H", d, o + 14, len(verts))
        struct.pack_into("<I", d, o + 16, 0)
        struct.pack_into("<H", d, o + 20, 1)

    struct.pack_into("<I", d, off_tail + 0, 1)
    struct.pack_into("<I", d, off_tail + 4, VA(off_vtx) & 0x7FFFFFFF)
    struct.pack_into("<I", d, off_tail + 8, 0)

    for i, v in enumerate(verts):
        o = off_vtx + XBCK_STRIDE*i
        if not (v.extra and len(v.extra) == XBCK_STRIDE - 8):
            struct.pack_into("<fff", d, o, v.pos[0]*mesh.scale,
                             v.pos[1]*mesh.scale, v.pos[2]*mesh.scale)
        if v.extra and len(v.extra) == XBCK_STRIDE - 8:
            # came straight from another .xbck: keep the exact bytes, which also
            # preserves the float positions that the s16 model would quantize
            d[o:o + XBCK_STRIDE - 8] = v.extra
        else:
            d[o+12:o+16] = pack_normal3(v.nrm)
            d[o+16:o+20] = XBCK_DEFAULT_TANGENT
        struct.pack_into("<ff", d, o + 20, v.uv[0], v.uv[1])

    for i, (start, count) in enumerate(sections):
        o = off_face + 12*i
        struct.pack_into("<I", d, o, 1)
        struct.pack_into("<HH", d, o + 4, start, count)
        struct.pack_into("<I", d, o + 8, 0)

    for i, vi in enumerate(idx):
        struct.pack_into("<H", d, off_idx + 2*i, vi)

    with open(path, "wb") as f:
        f.write(bytes(d))
    return len(d)


def _fit_scale(world_positions):
    """Finest power-of-two scale that still fits every coordinate in s16.

    The .mesh format stores world floats and has no field for the fixed-point
    scale, so reading it back at a fixed 1/4096 would re-quantize a mesh that
    came from a 1/16384 .pck and shift vertices in the 4th decimal. Picking the
    scale from the data keeps the round-trip lossless.
    """
    m = 0.0
    for p in world_positions:
        for c in p:
            m = max(m, abs(c))
    if m == 0.0:
        return DEFAULT_SCALE
    # 1/32768 is the finest the game itself uses (seen on small body-kit parts)
    denom = 32768.0
    while denom > 1.0 and m * denom > 32767.0:
        denom /= 2.0
    return 1.0 / denom


def read_mesh_text(path, scale=None):
    """Angel Studios .mesh (the text source shipped in ASSETS/vehicle/va_*).

    Layout: Pos/Nrm/Cpv/Tex0/Tex1 arrays, then Adj (adjuncts) that combine them
    by index (P/N/C0/T0), then Mtl blocks holding TRISTRIP index lists over the
    adjuncts. Material names are "#<n>", where n is the shader index -- the same
    number this converter carries in group["material"].

    Coordinates are kept EXACTLY as the game stores them. The Blender addon
    flips (x,y,z)->(-x,z,y) because Blender is Z-up, but doing that here would
    mirror every .mesh <-> .pck conversion.
    """
    txt = open(path, "r", encoding="utf-8", errors="replace").read()
    tok = txt.replace("{", " { ").replace("}", " } ").split()

    pos, nrm, uv0, cpv, adj = [], [], [], [], []
    groups = []
    i, n = 0, len(tok)

    def nums(count, width):
        """Read `count` records of `width` floats, skipping braces."""
        nonlocal i
        out = []
        while i < n and tok[i] in ("{", "}"):
            i += 1
        for _ in range(count):
            out.append(tuple(float(tok[i + k]) for k in range(width)))
            i += width
        return out

    while i < n:
        t = tok[i]
        if t == "Pos":
            c = int(tok[i + 1]); i += 2
            pos = nums(c, 3)
        elif t == "Nrm":
            c = int(tok[i + 1]); i += 2
            nrm = nums(c, 3)
        elif t == "Tex0":
            c = int(tok[i + 1]); i += 2
            uv0 = nums(c, 2)
        elif t == "Cpv":
            c = int(tok[i + 1]); i += 2
            cpv = nums(c, 4)
        elif t in ("Tex1", "Skinned", "PosSkin", "Offset"):
            # Skipped on purpose, and none of them occurs in retail data: of the
            # 341 .mesh files shipped, every one has Cpv and not one has Tex1,
            # Skinned, PosSkin or Offset. Carrying them would mean inventing
            # VIF streams with nothing to validate against -- and per
            # ComputeFVF, Offset != 0 turns Diffuse OFF and skinning ON, which
            # is a different vertex format altogether.
            c = int(tok[i + 1]); i += 2
            if t == "Tex1":
                nums(c, 2)
        elif t == "Adj":
            c = int(tok[i + 1]); i += 2
            while i < n and tok[i] in ("{", "}"):
                i += 1
            for _ in range(c):
                rec = {}
                for _ in range(4):
                    rec[tok[i]] = int(tok[i + 1])
                    i += 2
                adj.append((rec.get("P", 0), rec.get("N", 0),
                            rec.get("C0", 0), rec.get("T0", 0)))
        elif t == "Name":
            name = tok[i + 1].strip('"')
            mat = int(name[1:]) if name.startswith("#") and name[1:].isdigit() else 0
            groups.append({"material": mat, "strips": []})
            i += 2
        elif t == "Idx":
            c = int(tok[i + 1]); i += 2
            while i < n and tok[i] == "{":
                i += 1
            idx = [int(tok[i + k]) for k in range(c)]
            i += c
            if not groups:
                groups.append({"material": 0, "strips": []})
            groups[-1]["strips"].append(idx)
        else:
            i += 1

    if scale is None:
        scale = _fit_scale(pos)
    inv = 1.0 / scale
    m = Mesh()
    m.scale = scale
    m.source = "mesh-text"
    for g in groups:
        strips = []
        for idx in g["strips"]:
            strip = []
            for a in idx:
                if a >= len(adj):
                    continue
                p, nn, c0, t0 = adj[a]
                P = pos[p] if p < len(pos) else (0.0, 0.0, 0.0)
                N = nrm[nn] if nn < len(nrm) else (0.0, 0.0, 1.0)
                T = uv0[t0] if t0 < len(uv0) else (0.0, 0.0)
                C = DEFAULT_CPV if c0 >= len(cpv) else \
                    tuple(_clampi(x * 255.0, 0, 255) for x in cpv[c0])
                strip.append(Vtx(tuple(_clampi(v * inv, -32768, 32767) for v in P),
                                 T, N, cpv=C))
            if len(strip) >= 3:
                strips.append(strip)
        if strips:
            m.groups.append({"material": g["material"], "strips": strips})
    return m


# Primitive kinds of the .mod/.xmod dialect, and the parity the strip starts on.
# Measured, not guessed: over the 39 retail .mod files, comparing every
# triangle's geometric normal against the normals its adjuncts name, `str`
# agrees on even parity 54294/54817 (99.0%) and `stp` agrees on odd parity
# 54593/55038 (99.2%); the swapped readings score 0.6% and 0.5%. `tri` is a
# plain triangle list.
MOD_PRIM_PARITY = {"str": 0, "stp": 1}


def read_mod_text(path, scale=None):
    """Angel Studios .mod / .xmod -- the `version: 1.10` text dialect that
    gfxModelBase::LoadMod (0x1EBFC0) reads. MC3 ships 39 of them in
    ASSETS/model; MC2 writes every city mesh in the same dialect, so this is
    also the MC2 -> PS2 route.

    Same data model as .mesh in a different spelling: eleven header counts,
    then `v` / `n` / `c` / `t1` / `t2` lines, then `mtl <name> { packets: N ...}`
    blocks that each own the next N `packet <adjuncts> <primitive lines>
    <matrices>` blocks. A packet holds `adj <v> <n> <c> <t1> <t2> <tangent>`
    lines -- the field order of the header -- and `tri`/`str`/`stp` index lists
    addressing those adjuncts LOCALLY, one reference per adjunct.

    Cross-checks that hold on all 39 retail files: `adjuncts:` equals the sum of
    the packets' adjunct counts, and `primitives:` equals the triangle total
    (n-2 per strip, n/3 per `tri`) -- not the number of primitive lines.

    What the file does NOT carry is the shader slot. A .mesh names its material
    "#19", which IS the index; a .mod names it `mtl lambert2SG` with
    `texture: 0 "name"`. Groups therefore come out with material 0 and want
    --materials= to place them.
    """
    V, N, C, T1 = [], [], [], []
    # Materials queue up and each claims `packets:` of the packet blocks that
    # follow, in order. The two orderings in the wild both fall out of this:
    # MC3 interleaves (mtl, its packets, mtl, its packets) while the MC2 city
    # meshes declare every mtl first and then every packet.
    mats = []            # [{"want": n, "packs": [(adj, prims), ...]}]
    adj, prims = None, None

    def fl(t, k):
        return tuple(float(x) for x in t[1:1 + k])

    for line in open(path, "r", encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        k = t[0]
        if k == "v" and len(t) >= 4:
            V.append(fl(t, 3))
        elif k == "n" and len(t) >= 4:
            N.append(fl(t, 3))
        elif k == "c" and len(t) >= 5:
            C.append(fl(t, 4))
        elif k == "t1" and len(t) >= 3:
            T1.append(fl(t, 2))
        elif k == "mtl":
            mats.append({"want": 0, "packs": []})
        elif k == "packets:" and mats:
            mats[-1]["want"] = int(t[1])
        elif k == "packet":
            if not any(len(x["packs"]) < x["want"] for x in mats):
                # A packet nobody is owed: give it its own group rather than
                # dropping the geometry.
                mats.append({"want": 1, "packs": []})
            adj, prims = [], []
        elif k == "adj" and adj is not None:
            adj.append(tuple(int(x) for x in t[1:7]))
        elif k == "tri" and prims is not None:
            # `tri` carries no count: it is exactly one triangle per line,
            # `tri i0 i1 i2`. Only the strips are counted (`str 4 a b c d`).
            prims.append((k, [int(x) for x in t[1:4]]))
        elif k in MOD_PRIM_PARITY and prims is not None:
            prims.append((k, [int(x) for x in t[2:2 + int(t[1])]]))
        elif k == "}" and adj is not None:
            for x in mats:
                if len(x["packs"]) < x["want"]:
                    x["packs"].append((adj, prims))
                    break
            adj, prims = None, None

    if scale is None:
        scale = _fit_scale(V)
    inv = 1.0 / scale

    m = Mesh()
    m.scale = scale
    m.source = "mod-text"
    for mat in mats:
        faces = []
        for (adjs, plist) in mat["packs"]:
            for kind, idx in plist:
                refs = [adjs[i] for i in idx if i < len(adjs)]
                if kind == "tri":
                    faces += [tuple(refs[i:i+3]) for i in range(0, len(refs) - 2, 3)]
                    continue
                par = MOD_PRIM_PARITY[kind]
                for i in range(2, len(refs)):
                    a, b = (i-2, i-1) if (i % 2 == 0) == (par == 0) else (i-1, i-2)
                    faces.append((refs[a], refs[b], refs[i]))
        if not faces:
            continue

        verts, index, tris = [], {}, []
        for f in faces:
            fn = None
            tri = []
            for r in f:
                j = index.get(r)
                if j is None:
                    p = V[r[0]] if r[0] < len(V) else (0.0, 0.0, 0.0)
                    if 0 <= r[1] < len(N):
                        nrm = N[r[1]]
                    else:
                        if fn is None:
                            fn = _face_normal(*[V[x[0]] if x[0] < len(V) else (0.0, 0.0, 0.0)
                                                for x in f])
                        nrm = fn
                    uv = T1[r[3]] if 0 <= r[3] < len(T1) else (0.0, 0.0)
                    cpv = DEFAULT_CPV if not (0 <= r[2] < len(C)) else \
                        tuple(_clampi(x * 255.0, 0, 255) for x in C[r[2]])
                    j = len(verts)
                    index[r] = j
                    verts.append(Vtx(tuple(_clampi(x * inv, -32768, 32767) for x in p),
                                     uv, nrm, False, cpv=cpv))
                tri.append(j)
            tris.append(tuple(tri))
        m.groups.append({"material": 0,
                         "strips": [[verts[i] for i in st] for st in _stripify(tris)]})
    return m


def write_mesh_text(mesh, path):
    """Write the Angel Studios .mesh text format. Pos/Nrm/Tex0 are de-duplicated
    and referenced through adjuncts, which is how the originals are built.

    The strips go through clean_strips() first: the .mesh format has no ADC flag,
    so a PS2 strip written verbatim would draw the bridge triangles the console
    hides (a 128-triangle hood came out with 146). Rebuilding from the triangles
    actually drawn keeps the geometry identical.
    """
    pos, nrm, uv0, cpv, adj = [], [], [], [], []
    ipos, inrm, iuv, icpv, iadj = {}, {}, {}, {}, {}

    def intern(store, index, key):
        j = index.get(key)
        if j is None:
            j = len(store)
            index[key] = j
            store.append(key)
        return j

    out_groups = []
    for g in mesh.groups:
        strips = []
        for strip in clean_strips(g):
            idx = []
            for v in strip:
                P = tuple(round(c * mesh.scale, 6) for c in v.pos)
                N = tuple(round(c, 6) for c in v.nrm)
                T = tuple(round(c, 6) for c in v.uv)
                C = tuple(round(c / 255.0, 6) for c in v.cpv)
                key = (P, N, T, C)
                a = iadj.get(key)
                if a is None:
                    a = len(adj)
                    iadj[key] = a
                    adj.append((intern(pos, ipos, P), intern(nrm, inrm, N),
                                intern(cpv, icpv, C), intern(uv0, iuv, T)))
                idx.append(a)
            if len(idx) >= 3:
                strips.append(idx)
        if strips:
            out_groups.append((g["material"], strips))

    L = []
    L.append("{")
    L.append("\tSkinned 0 ")
    L.append("\tPosSkin 0 ")

    def block(name, items, fmt):
        if not items:
            L.append("\t%s 0 " % name)
            return
        L.append("\t%s %d { " % (name, len(items)))
        for it in items:
            L.append("\t\t" + fmt(it) + " ")
        L.append("\t}")
        L.append("")

    block("Pos", pos, lambda v: "%.6f\t%.6f\t%.6f" % v)
    block("Nrm", nrm, lambda v: "%.6f\t%.6f\t%.6f" % v)
    block("Cpv", cpv, lambda c: "%.6f\t%.6f\t%.6f\t%.6f" % c)
    block("Tex0", uv0, lambda v: "%.6f\t%.6f" % v)
    L.append("\tTex1 0 ")

    if adj:
        L.append("\tAdj %d { " % len(adj))
        for (p, nn, c, t) in adj:
            L.append("\t\tP %d " % p)
            L.append("\t\tN %d " % nn)
            L.append("\t\tC0 %d " % c)
            L.append("\t\tT0 %d " % t)
        L.append("\t}")
        L.append("")
    else:
        L.append("\tAdj 0 ")

    L.append("\tMtl %d { " % len(out_groups))
    for (mat, strips) in out_groups:
        L.append("\t\t{")
        L.append('\t\t\tName "#%d" ' % mat)
        L.append("\t\t\tPriority 32 ")
        L.append("\t\t\tPrim %d { " % len(strips))
        for idx in strips:
            L.append("\t\t\t\t{")
            L.append("\t\t\t\t\tType TRISTRIP")
            L.append("\t\t\t\t\tPriority 32 ")
            L.append("\t\t\t\t\tIdx %d { %s }" % (len(idx), " ".join(str(x) for x in idx)))
            L.append("\t\t\t\t}")
        L.append("\t\t\t}")
        L.append("\t\t}")
    L.append("\t}")
    L.append("")
    L.append("\tOffset 0 ")
    L.append("}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return os.path.getsize(path)


def clean_strips(group, rescale=1.0):
    """Rebuild a group's strips from the triangles it actually draws.

    Normalizes between formats: PS2 hides bridge triangles with the ADC flag,
    PSP hides them with degenerate vertices. Rebuilding from the drawn triangle
    list makes the output correct either way. `rescale` converts raw positions
    when the target format uses a different fixed scale.
    """
    verts, index, faces = [], {}, []
    for strip in group["strips"]:
        for (a, b, c) in tris_of_strip(strip):
            tri = []
            for i in (a, b, c):
                v = strip[i]
                pos = v.pos if rescale == 1.0 else tuple(_clampi(p*rescale, -32768, 32767) for p in v.pos)
                key = (pos, v.uv, v.nrm, v.cpv)
                j = index.get(key)
                if j is None:
                    j = len(verts)
                    index[key] = j
                    verts.append(Vtx(pos, v.uv, v.nrm, False, cpv=v.cpv))
                tri.append(j)
            faces.append(tuple(tri))
    if not faces:
        return []
    return [[verts[i] for i in st] for st in _stripify(faces)]


def _face_normal(a, b, c):
    ux, uy, uz = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
    vx, vy, vz = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
    n = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
    l = math.sqrt(n[0]**2 + n[1]**2 + n[2]**2)
    return (0.0, 0.0, 1.0) if l == 0 else (n[0]/l, n[1]/l, n[2]/l)


def _stripify(faces):
    """Greedy stripification preserving winding (engine global parity)."""
    edge = {}
    for fi, (a, b, c) in enumerate(faces):
        edge.setdefault((a, b), []).append((fi, c))
        edge.setdefault((b, c), []).append((fi, a))
        edge.setdefault((c, a), []).append((fi, b))
    used = [False]*len(faces)
    strips = []
    for s in range(len(faces)):
        if used[s]:
            continue
        used[s] = True
        a, b, c = faces[s]
        strip = [a, b, c]
        while len(strip) < MAX_BLOCK_VERTS:
            n = len(strip)
            e = (strip[-2], strip[-1]) if n % 2 == 0 else (strip[-1], strip[-2])
            nxt = None
            for fi, w in edge.get(e, ()):
                if not used[fi]:
                    nxt = (fi, w)
                    break
            if nxt is None:
                break
            used[nxt[0]] = True
            strip.append(nxt[1])
        strips.append(strip)
    return strips


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _pad(data, align, byte=b"\x00"):
    return data + byte * ((align - (len(data) % align)) % align)


def write_obj(mesh, path):
    lines_v, lines_vt, lines_vn, body = [], [], [], []
    n = 0
    for gi, g in enumerate(mesh.groups):
        body.append("usemtl %02X_group%d" % (g["material"], gi))
        body.append("g group%d" % gi)
        for strip in g["strips"]:
            base = n
            for v in strip:
                lines_v.append("v %.6f %.6f %.6f" % (v.pos[0]*mesh.scale, v.pos[1]*mesh.scale, v.pos[2]*mesh.scale))
                lines_vt.append("vt %.6f %.6f" % (v.uv[0], v.uv[1]))
                lines_vn.append("vn %.6f %.6f %.6f" % v.nrm)
                n += 1
            for (a, b, c) in tris_of_strip(strip):
                ia, ib, ic = base+a+1, base+b+1, base+c+1
                body.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (ia, ia, ia, ib, ib, ib, ic, ic, ic))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# exported by mc3_mesh_convert (source: %s)\n" % mesh.source)
        f.write("# mc3_scale %.12g\n# mc3_id %d\n" % (mesh.scale, mesh.piece_id))
        f.write("\n".join(lines_v) + "\n")
        f.write("\n".join(lines_vt) + "\n")
        f.write("\n".join(lines_vn) + "\n")
        f.write("\n".join(body) + "\n")
    return os.path.getsize(path)


def write_psppck(mesh, path, flavor="mcla", virtual_base=0x01A9F300):
    """Build a .psppck. Layout: header(0x80) | magic+ptrs(0x80..0xA0) | material
    table | mesh offset list | [damage] | mesh data (14-byte records)."""
    magic = MAGIC_MC3 if flavor == "mc3" else MAGIC_MCLA
    groups = mesh.groups
    num = len(groups)

    # mesh data: all strips back to back; remember (offset,len) per strip
    recs = bytearray()
    subs_per_group = []
    for g in groups:
        subs = []
        # PSP strips are stored as ONE continuous run per group: join the strips
        # with degenerate (zero-area) transition vertices and keep each strip
        # starting on an even index so the engine parity rebuilds it correctly.
        rs = mesh.scale / DEFAULT_SCALE
        # A PSP source has no ADC flags, so its strips are already "clean":
        # reuse them as-is (keeps the file as compact as the original). Only
        # rebuild when the source hides bridges with ADC (PS2).
        if any(v.skip for st in g["strips"] for v in st):
            src_strips = clean_strips(g, rescale=rs)
        elif rs == 1.0:
            src_strips = g["strips"]
        else:
            src_strips = [[Vtx(tuple(_clampi(p*rs, -32768, 32767) for p in v.pos), v.uv, v.nrm, False,
                               cpv=v.cpv)
                           for v in st] for st in g["strips"]]
        combined = []
        for strip in src_strips:
            if not strip:
                continue
            if combined:
                combined.append(combined[-1])
                combined.append(strip[0])
                if len(combined) % 2 == 1:
                    combined.append(strip[0])
            combined.extend(strip)
        for strip in ([combined] if combined else []):
            start = len(recs) // 14
            for v in strip:
                ru = _clampi(v.uv[0]*32767.0 - 32768, -32768, 32767)
                rv = _clampi(v.uv[1]*32767.0 - 32768, -32768, 32767)
                recs += struct.pack("<hh", ru, rv)
                nb = [_clampi(v.nrm[k]*127.0, -128, 127) & 0xFF for k in range(3)]
                nb[0] &= 0xFE                      # PSP: keep it even (data, not flag)
                recs += bytes(nb) + b"\x00"        # +1 skipped byte (damage id)
                recs += struct.pack("<hhh", *[_clampi(c, -32768, 32767) for c in v.pos])
            subs.append((start, len(recs)//14 - start))
        subs_per_group.append(subs)
    nverts = len(recs) // 14

    # Layout mirrors the retail files:
    #   0x00 header | 0x80 magic+pointers | 0xA0 extra fields | material table
    #   | u32 num_pointers | mesh list | sub-offset table | [damage] | mesh data
    # Padding is 0xCD (as the game's allocator leaves it), including the tail.
    mat_tbl = _pad(b"".join(struct.pack("<H", g["material"] & 0xFFFF) for g in groups), 16, b"\xCD")
    # Sub-offset table layout differs per game:
    #   MC3   -> every group starts 16-byte aligned (MC3 never points at +8/+12)
    #            and is followed by a 16-byte terminator "04 00 00 00 FE FF FF FF"
    #            padded with CD. That is where the odd FE FF FF FF filler comes from.
    #   MC:LA -> groups are packed tight, 4 bytes per sub-offset, no alignment.
    subtbl = bytearray()
    sub_offsets = []
    for gi, subs in enumerate(subs_per_group):
        if flavor == "mc3":
            subtbl = bytearray(_pad(bytes(subtbl), 16, b"\xCD"))
        sub_offsets.append(len(subtbl))
        for (o, l) in subs:
            subtbl += struct.pack("<HH", o, l)
        if flavor == "mc3":
            subtbl = bytearray(_pad(bytes(subtbl), 16, b"\xCD"))
            if gi != len(subs_per_group) - 1:
                subtbl += _pad(struct.pack("<iI", 4, 0xFFFFFFFE), 16, b"\xCD")
    subtbl = _pad(bytes(subtbl), 16, b"\xCD")
    dmg = b"" if flavor == "mc3" else _pad(b"\x00\x00" * nverts, 16, b"\x00")

    off_mat = 0xC0                              # after header + extra fields
    off_list = _len16(off_mat + len(mat_tbl) + 4)   # u32 count sits at list-4
    list_size = _len16(num * 0xC)
    off_sub = off_list + list_size
    off_dmg = off_sub + len(subtbl)
    off_data = _len16(off_dmg + len(dmg))

    va = mesh.virtual_addr if (mesh.virtual_addr and mesh.flavor == flavor) else \
         (0x0A030680 if flavor == "mc3" else 0x01A9F380)
    base = va - 0x80                            # file -> virt: virt = file + base

    def V(fileoff):
        return base + fileoff

    out = bytearray(b"\x00" * 0x80)
    struct.pack_into("<IIII", out, 0, va, 0x16, 1, 0)   # length patched at the end

    h = bytearray()
    h += magic
    h += struct.pack("<IIII", V(off_list), V(off_mat), V(off_dmg) if dmg else 0, V(off_data))
    h += b"\xFF\xFF\xFF\xFF"
    h += bytes([0x00, mesh.piece_id & 0xFF, 0x00, num & 0xFF])
    h += struct.pack("<I", 0x00012200)
    # extra fields seen in every retail mesh: vertex stride, total vertex count
    # and three constants (8.0, 1.0, 1.0)
    h += struct.pack("<HBB", 14, 0xC8, 0xCD)
    h += struct.pack("<HBB", nverts & 0xFFFF, 0xC0, 0xCD)
    h += struct.pack("<fff", 8.0, 1.0, 1.0)
    out += _pad(bytes(h), off_mat - 0x80, b"\xCD")

    out += mat_tbl
    out += b"\xCD" * (off_list - 4 - len(out))
    out += struct.pack("<I", num)               # num_pointers, read at list-4

    lst = bytearray()
    for gi in range(num):
        lst += struct.pack("<I", V(off_sub + sub_offsets[gi]))
        lst += struct.pack("<H", len(subs_per_group[gi]))
        lst += b"\xCD\xCD" + b"\x00\x00\x00\x00"    # retail: 2x CD then 4 zeros
    out += _pad(bytes(lst), list_size, b"\xCD")

    out += subtbl + dmg
    out += b"\xCD" * (off_data - len(out))
    out += recs
    out = bytearray(_pad(bytes(out), 16, b"\xCD"))
    struct.pack_into("<I", out, 0x0C, len(out) - 0x80)
    open(path, "wb").write(bytes(out))
    return len(out)


def _len16(n):
    return (n + 15) & ~15


def write_pck(mesh, path, virtual_offset=0x01717300, block_style="vehicle", restrip=True):
    """Build a PS2 single mesh.pck with independent, parity-safe packet blocks.

    `block_style` picks the packet header layout:
      "vehicle"  tag 9800026C, then scale, then the 3 culling floats;
      "traffic"  tag 98000260, then scale, straight to the vertex count.
    Traffic meshes carry no per-packet culling bounds -- measured across
    the four city containers, all 161 blocks use 98000260 and put the
    vertex count at +0x08, where a vehicle block (all 315 of them use
    9800026C) has the first culling float and puts the count at +0x14.
    Retail traffic never uses the odd-parity variant either, so this
    style always emits the even tag.
    """
    groups = mesh.groups
    num = len(groups)

    def blocks_for(group):
        # Rebuild the visible faces before packetizing.  This normalizes PSP
        # transition degenerates and PS2 ADC restarts, and is intentionally used
        # for PSP trim meshes whose faces cross the serialized subrange seams.
        # Each rebuilt strip is capped at MAX_BLOCK_VERTS by _stripify and becomes
        # one independent packet with its first two vertices ADC-flagged.
        packed = []
        if restrip:
            for strip in clean_strips(group):
                if len(strip) < 3:
                    continue
                packed.append([(v, k < 2) for k, v in enumerate(strip)])
            return packed
        # restrip=False keeps the source topology, for sources that already ship
        # console-ready strips (MC2's PMD0 batches are exactly that). Cut every
        # MAX_BLOCK_VERTS vertices and re-seed the continuation with the two
        # before the cut, which is the retail continuation packet. The cut MUST
        # land on an even index or the first triangle of the continuation flips
        # winding -- MAX_BLOCK_VERTS is even, so stepping by it keeps that true.
        for strip in group["strips"]:
            start = 0
            while start < len(strip):
                head = max(0, start - 2)
                seg = strip[head:min(len(strip), start + MAX_BLOCK_VERTS)]
                start += MAX_BLOCK_VERTS
                if len(seg) < 3:
                    break
                packed.append([(v, k < 2 or v.skip) for k, v in enumerate(seg)])
        return packed

    scale = mesh.scale or DEFAULT_SCALE
    all_blocks, counts = [], []
    for g in groups:
        bl = blocks_for(g)
        all_blocks.append(bl)
        counts.append(sum(len(b) for b in bl))

    traffic_style = (block_style == "traffic")

    def encode(recs, parity):
        cnt = len(recs)
        even = True if traffic_style else (parity % 2 == 0)
        # Per-packet camera/frustum culling bounds, stored at +0x08/+0x0C/+0x10.
        # The game culls a packet by these; leaving them at 0 makes parts blink
        # out at certain camera angles. Same formula the retail files use:
        #   CenterY/CenterZ = AABB midpoint, Radius = half the AABB diagonal.
        xs = [v.pos[0]*scale for v, _ in recs]
        ys = [v.pos[1]*scale for v, _ in recs]
        zs = [v.pos[2]*scale for v, _ in recs]
        center_y = (min(ys) + max(ys)) * 0.5
        center_z = (min(zs) + max(zs)) * 0.5
        radius = 0.5 * math.sqrt((max(xs)-min(xs))**2 + (max(ys)-min(ys))**2 + (max(zs)-min(zs))**2)
        if traffic_style:
            c = [bytearray.fromhex("98000260")]
            c.append(struct.pack("<f", scale))
            c.append(struct.pack("<I", cnt))
        else:
            c = [bytearray.fromhex("9800026C" if even else "C501026C")]
            c.append(struct.pack("<f", scale))
            c.append(struct.pack("<fff", center_y, center_z, radius))
            c.append(struct.pack("<I", cnt))
            c.append(struct.pack("<ff", 0, 0))
            c.append(struct.pack("<I", cnt))
        c.append(bytearray.fromhex("EE 00" if even else "1B 02"))
        c.append(struct.pack("B", cnt)); c.append(b"\x69")
        for v, sk in recs:
            c.append(struct.pack("<hhh", *[_clampi(x, -32768, 32767) for x in v.pos]))
        vchunk = _pad(b"".join(bytes(x) for x in c), 4)

        c = [bytearray.fromhex("C4 00" if even else "F1 01"), struct.pack("B", cnt), b"\x65"]
        for v, sk in recs:
            c.append(struct.pack("<HH", _clampi(v.uv[0]*4096, 0, 65535), _clampi(v.uv[1]*4096, 0, 65535)))
        uchunk = _pad(b"".join(bytes(x) for x in c), 4)

        # Diffuse channel (the .mesh `Cpv`), one RGBA byte quad per vertex.
        c = [bytearray.fromhex("18 41" if even else "45 42"), struct.pack("B", cnt), b"\x6E"]
        for v, sk in recs:
            c.append(bytes(_clampi(x, 0, 255) for x in v.cpv))
        dchunk = _pad(b"".join(bytes(x) for x in c), 4)

        c = [bytearray.fromhex("9A 00" if even else "C7 01"), struct.pack("B", cnt), b"\x6A"]
        for v, sk in recs:
            nb = [_clampi(v.nrm[k]*127.0, -128, 127) & 0xFF for k in range(3)]
            nb[0] = (nb[0] | 1) if sk else (nb[0] & 0xFE)
            c.append(bytes(nb))
        nchunk = _pad(b"".join(bytes(x) for x in c), 4)

        foot = bytearray.fromhex("98 00 00 04 06 00 00 14" if even else "C5 01 00 04 06 00 00 14")
        return _pad(vchunk + uchunk + dchunk + nchunk + foot, 16)

    mat_size = _len16(2*num)
    numptr_size = 16
    mainptr_size = _len16(8*num)
    skip = 32 + mat_size + numptr_size + mainptr_size

    body, mains, filesize = [], [], 0
    for gi in range(num):
        bp_size = 16 + _len16(8*1)
        blk = b"".join(encode(b, i) for i, b in enumerate(all_blocks[gi]))
        off = skip + bp_size + filesize
        bptr = struct.pack("<I", virtual_offset+off) + struct.pack("<H", len(blk)//16) + struct.pack("<H", counts[gi])
        body.append(_pad(struct.pack("<I", 1), 16, b"\xCD") + _pad(bptr, 16, b"\xCD") + blk)
        mains.append(virtual_offset + off - bp_size + 16)
        filesize += len(blk) + bp_size

    h = [TAG_PS2, struct.pack("<HH", 0, mesh.piece_id & 0xFFFF), struct.pack("<I", num),
         struct.pack("<I", virtual_offset+32),
         struct.pack("<I", virtual_offset+32+mat_size+numptr_size), struct.pack("<I", 0)]
    head = _pad(b"".join(h), 16, b"\xCD")
    mt = _pad(b"".join(struct.pack("<H", g["material"] & 0xFFFF) for g in groups), 16, b"\xCD")
    npt = _pad(struct.pack("<I", num), 16, b"\xCD")
    mp = _pad(b"".join(_pad(struct.pack("<I", mains[i]) + struct.pack("<H", 1), 8, b"\xCD") for i in range(num)), 16, b"\xCD")
    mesh_bin = head + mt + npt + mp + b"".join(body)
    out = _pad(struct.pack("<IIII", virtual_offset, 22, 1, len(mesh_bin)), 128) + mesh_bin
    open(path, "wb").write(out)
    return len(out)


# ---------------------------------------------------------------------------

def load(path, scale=DEFAULT_SCALE):
    low = path.lower()
    if low.endswith(".psppck"):
        return read_psppck(path)
    if low.endswith(".obj"):
        return read_obj(path, scale)
    # ".mesh" is the text source; ".mesh.pck" is the compiled binary, so test
    # for the binary extension first
    if low.endswith(".mesh"):
        return read_mesh_text(path, scale)
    if low.endswith(".mod") or low.endswith(".xmod"):
        # New path, so it can have the better default: no --scale means fit the
        # exponent to the data, the way rmcModelGeom::Load (0x2A8FA0) does when
        # the game itself loads the text. MC2 city meshes sit in world
        # coordinates and would saturate instantly at the fixed 1/4096.
        return read_mod_text(path, None if scale == DEFAULT_SCALE else scale)
    if low.endswith(".xbck"):
        return read_xbck(path, scale)
    return read_pck(path)


def save(mesh, path, flavor=None, restrip=True):
    low = path.lower()
    if low.endswith(".psppck"):
        return write_psppck(mesh, path, flavor or (mesh.flavor or "mcla"))
    if low.endswith(".obj"):
        return write_obj(mesh, path)
    if low.endswith(".mesh"):
        return write_mesh_text(mesh, path)
    if low.endswith(".xbck"):
        return write_xbck(mesh, path)
    return write_pck(mesh, path, restrip=restrip)


def main():
    # Both spellings work: `--id=92` and `--id 92`. The split-on-"=" version
    # this used to have ignored the spaced form without a word, and `--scale 0.5
    # in.mesh out.pck` was worse than a no-op: 0.5 was not a flag, so it landed
    # in the positional list and became the source file.
    argv = sys.argv[1:]
    flavor, scale, piece, mats, files = None, DEFAULT_SCALE, None, None, []
    restrip = True
    i = 0

    def value(a, i):
        if "=" in a:
            return a.split("=", 1)[1], i
        if i < len(argv):
            return argv[i], i + 1
        raise SystemExit("mc3_mesh_convert: %s needs a value" % a)

    while i < len(argv):
        a = argv[i]
        i += 1
        name = a.split("=", 1)[0]
        if a in ("--mc3", "--mcla"):
            flavor = a[2:]
        elif a in ("-v", "--verbose"):
            pass                      # accepted and ignored, as it always was
        elif a == "--keep-strips":
            restrip = False
        elif name == "--scale":
            v, i = value(a, i); scale = float(v)
        elif name == "--id":
            v, i = value(a, i); piece = int(v, 0)
        elif name == "--materials":
            v, i = value(a, i); mats = [int(x, 0) for x in v.split(",")]
        elif a.startswith("-"):
            raise SystemExit("mc3_mesh_convert: unknown option %s" % a)
        else:
            files.append(a)

    if len(files) < 2:
        print(__doc__)
        return
    src, dst = files[0], files[1]

    m = load(src, scale)
    if piece is not None:
        m.piece_id = piece
    if mats:
        for i, g in enumerate(m.groups):
            if i < len(mats):
                g["material"] = mats[i]
    ng, ns, nv, nt = m.stats()
    print("in : %s  [%s]  %d groups, %d strips, %d verts, %d tris" % (os.path.basename(src), m.source, ng, ns, nv, nt))
    size = save(m, dst, flavor, restrip=restrip)
    print("out: %s  %d bytes" % (os.path.basename(dst), size))


if __name__ == "__main__":
    main()
