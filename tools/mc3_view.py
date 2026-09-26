"""
mc3_view.py - 3D viewer for MC3 meshes (PS2 .pck and PSP .psppck).

Easiest use: DRAG a .pck/.psppck file onto this .py -> opens a native 3D window
(matplotlib) directly. Or from the command line:
    python mc3_view.py "path/file.pck"             # opens in the browser
    python mc3_view.py "path/file.pck" --native    # matplotlib window instead
    python mc3_view.py "path/file.pck" --limit=400 # cap how many meshes load

Native window controls:
    - drag mouse = rotate   |   scroll = zoom (matplotlib)
    - <-  ->  = step through meshes   |   A = show all
    - B = flipped faces in RED (the ones you "see through" in game)
    - G = color by group / flat color  |   W = wireframe  |  Q/E = roll

Red faces have inverted winding (they disagree with the smoothed surface normal)
= exactly the ones that vanish / show-through in game. If matplotlib is not
available, it falls back to the HTML/WebGL version in the browser.
"""
import json
import os
import re
import struct
import sys
import importlib.util as _iu
_SP = _iu.spec_from_file_location(
    'mc3_output', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'mc3_output.py'))
_out_path = _iu.module_from_spec(_SP)
_SP.loader.exec_module(_out_path)
_VP_SPEC = _iu.spec_from_file_location(
    'mc3_vifpos', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'mc3_vifpos.py'))
_vifpos = _iu.module_from_spec(_VP_SPEC)
_VP_SPEC.loader.exec_module(_vifpos)



# ---------------------------------------------------------------------------
# Parsers -> list of groups: each group = {name, material, verts:[[x,y,z]...],
#            tris:[[i,j,k]...]}  (i,j,k indices into verts, order = winding)
# ---------------------------------------------------------------------------

def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def parse_psppck(path):
    d = open(path, "rb").read()
    base = struct.unpack_from("<I", d, 0)[0] - 0x80
    mesh_list = struct.unpack_from("<I", d, 0x84)[0] - base
    mat_off = struct.unpack_from("<I", d, 0x88)[0] - base
    mesh_ptr = struct.unpack_from("<I", d, 0x90)[0] - base
    num = struct.unpack_from("<I", d, mesh_list - 4)[0]

    material_indices = [struct.unpack_from("<H", d, mat_off + 2*i)[0] for i in range(num)]

    groups = []
    off = mesh_list
    for g in range(num):
        p = struct.unpack_from("<I", d, off)[0] - base
        cnt = struct.unpack_from("<H", d, off + 4)[0]
        off += 0xC
        # the group's continuous range (from the 1st sub-offset to the last), gaps included
        starts, ends = [], []
        po = p
        for k in range(cnt):
            rp = struct.unpack_from("<H", d, po)[0]
            rl = struct.unpack_from("<H", d, po + 2)[0]
            starts.append(rp); ends.append(rp + rl)
            po += 4
        if not starts:
            continue
        g0, g1 = min(starts), max(ends)
        if g1 * 14 + mesh_ptr > len(d):
            continue
        verts = [list(struct.unpack_from("<hhh", d, mesh_ptr + i*14 + 8)) for i in range(g0, g1)]
        # contiguous strip with parity local to the group; skip degenerates
        tris = []
        for i in range(2, g1 - g0):
            a, b, c = (i-2, i-1, i) if i % 2 == 0 else (i-1, i-2, i)
            A, B, C = verts[a], verts[b], verts[c]
            if A == B or B == C or A == C:
                continue
            tris.append([a, b, c])
        groups.append({"name": f"group{g}", "material": material_indices[g], "verts": verts, "tris": tris})
    return groups


def parse_ps2_pck(path):
    d = open(path, "rb").read()
    N = len(d)
    vbase = struct.unpack_from("<I", d, 0)[0]

    def toff(v):
        return v - vbase + 0x80

    def in_file(off):
        return 0 <= off <= N - 2

    # accept any mesh tag ?? ?? 7A 00 (98 0F=vp, 18 1E=traffic, etc.)
    if d[0x82:0x84] != b"\x7a\x00":
        raise ValueError("not a mesh.pck (missing ?? ?? 7A 00 tag at 0x80)")

    # try reading the header (num_ptr / material table / main pointers). If the
    # pointers fall outside the file (e.g. a slice extracted from a container),
    # ignore it and treat everything as a single group.
    material_indices = []
    group_starts = []
    try:
        num = struct.unpack_from("<I", d, 0x88)[0]
        mat_off = toff(struct.unpack_from("<I", d, 0x8c)[0])
        main_off = toff(struct.unpack_from("<I", d, 0x90)[0])
        if 0 < num <= 4096 and in_file(mat_off) and in_file(main_off) and mat_off + 2*num <= N and main_off + 8*num <= N:
            material_indices = [struct.unpack_from("<H", d, mat_off + 2*i)[0] for i in range(num)]
            main_pointers = [struct.unpack_from("<I", d, main_off + 8*i)[0] for i in range(num)]
            gs = [toff(mp) for mp in main_pointers]
            if all(in_file(g) for g in gs):
                group_starts = sorted(gs)
    except Exception:
        pass

    # every packet block (vp uses ...6C, traffic uses ...60)
    block_offs = [m.start() for m in re.finditer(
        rb"(\x98\x00\x02[\x6c\x60]|\xc5\x01\x02[\x6c\x60])", d)]

    def group_of(off):
        gi = 0
        for j, gs in enumerate(group_starts):
            if off >= gs:
                gi = j
        return gi

    # decode each block into (verts, tris) and accumulate per group
    per_group = {}
    for bo in block_offs:
        # vertex scale = float @ +4
        scale = struct.unpack_from("<f", d, bo + 4)[0] or (1/256.0)
        m = re.search(rb"(\xee\x00|\x1b\x02)(.)\x69", d[bo:bo+0x40], re.DOTALL)
        if not m:
            continue
        vc = m.group(2)[0]
        vd = bo + m.start() + 4
        verts = []
        for i in range(vc):
            x, y, z = struct.unpack_from("<hhh", d, vd + 6*i)
            verts.append([x*scale, y*scale, z*scale])
        nm = re.search(rb"(\x9a\x00|\xc7\x01)(.)\x6a", d[bo:bo+0x800], re.DOTALL)
        skip = [False]*vc
        if nm:
            nd = bo + nm.start() + 4
            skip = [(d[nd+3*i] & 1) == 1 for i in range(vc)]
        gi = group_of(bo)
        gg = per_group.setdefault(gi, {"verts": [], "tris": []})
        base_i = len(gg["verts"])
        gg["verts"].extend(verts)
        for i in range(2, vc):
            if skip[i]:
                continue
            a, b, c = (i-2, i-1, i) if i % 2 == 0 else (i-1, i-2, i)
            A, B, C = verts[a], verts[b], verts[c]
            if A == B or B == C or A == C:
                continue
            gg["tris"].append([base_i+a, base_i+b, base_i+c])

    groups = []
    for gi in sorted(per_group):
        g = per_group[gi]
        mat = material_indices[gi] if gi < len(material_indices) else -1
        groups.append({"name": f"group{gi}", "material": mat, "verts": g["verts"], "tris": g["tris"]})
    return groups


# ---------------------------------------------------------------------------
# Traffic container (multi-vehicle): *_traffic.pck
# ---------------------------------------------------------------------------

def _find_blocks(d):
    # flexible block start (vp uses ...6C, traffic ...60; tokyo likewise);
    # validated by requiring an EE00../1B02..69 vertex tag right after.
    return [m.start() for m in re.finditer(rb"(\x98\x00\x02.|\xc5\x01\x02.)", d, re.DOTALL)
            if re.search(rb"(\xee\x00|\x1b\x02).\x69", d[m.start():m.start()+0x30], re.DOTALL)]


def _decode_block(d, bo):
    N = len(d)
    scale = struct.unpack_from("<f", d, bo + 4)[0] or (1.0/256.0)
    m = re.search(rb"(\xee\x00|\x1b\x02)(.)\x69", d[bo:bo+0x40], re.DOTALL)
    if not m:
        return None
    vc = m.group(2)[0]
    vd = bo + m.start() + 4
    if vd + 6*vc > N:
        return None
    verts = [[struct.unpack_from("<h", d, vd + 6*i + 2*k)[0] * scale for k in range(3)] for i in range(vc)]
    nm = re.search(rb"(\x9a\x00|\xc7\x01)(.)\x6a", d[bo:bo+0x800], re.DOTALL)
    skip = [False]*vc
    if nm:
        nd = bo + nm.start() + 4
        if nd + 3*vc <= N:
            skip = [(d[nd+3*i] & 1) == 1 for i in range(vc)]
    return verts, skip


def _tris_from_blocks(blocks_data):
    verts, tris = [], []
    for bverts, skip in blocks_data:
        base = len(verts)
        verts.extend(bverts)
        vc = len(bverts)
        for i in range(2, vc):
            if skip[i]:
                continue
            a, b, c = (i-2, i-1, i) if i % 2 == 0 else (i-1, i-2, i)
            A, B, C = bverts[a], bverts[b], bverts[c]
            if A == B or B == C or A == C:
                continue
            tris.append([base+a, base+b, base+c])
    return verts, tris


def _name_before(d, off):
    # MC3 node names are lower case (body, whl0, headlight2, bndbone, glow_*).
    win = d[max(0, off-0x600):off]
    ns = re.findall(rb"[a-z_][a-z0-9_]{2,}", win)
    for s in reversed(ns):
        t = s.decode("latin1")
        if len(set(t)) > 1 and not t.endswith("bone"):  # skip repeats and the generic "bndbone"
            return t
    for s in reversed(ns):
        t = s.decode("latin1")
        if len(set(t)) > 1:
            return t
    return None


def parse_ps2_container(path):
    d = open(path, "rb").read()
    N = len(d)
    BASE = struct.unpack_from("<I", d, 0)[0] - 0x80   # virt -> file = virt - BASE
    vbase = BASE + 0x80

    def F(v):
        return v - BASE

    def U(o):
        return struct.unpack_from("<I", d, o)[0] if 0 <= o <= N-4 else 0

    def U16(o):
        return struct.unpack_from("<H", d, o)[0] if 0 <= o <= N-2 else 0

    def ok(v):
        return 0x06000000 <= v <= 0x07000000 and 0 <= F(v) <= N-4

    def is_mesh(fo):
        return 0 <= fo <= N-4 and d[fo+2:fo+4] in (b"\x7a\x00", b"\xbc\x00")

    # (1) directory: vehicle (name_list) -> mesh pointers.
    # name_list_pointers @ file 0xB0; follow audio[i] +0x2a4 -> root
    # +0x10 -> mesh_group (num u16@+2, array_ptr u32@+8) -> the mesh pointers.
    header_name = {}
    try:
        nlp = 0xB0          # the directory sits in the header, at a fixed file offset
        p_lop = U(nlp); n = U16(nlp+4); p_names = U(nlp+8)
        audio = [U(F(p_lop)+4*i) for i in range(n)]
        names = [d[F(p_names)+i*104+4:F(p_names)+i*104+64].split(b"\x00")[0].decode("latin1")
                 for i in range(n)]
        for i, a in enumerate(audio):
            if not ok(a):
                continue
            root = U(F(a)+0x2a4)
            if not ok(root):
                continue
            mg = U(F(root)+0x10)
            if not ok(mg):
                continue
            num = U16(F(mg)+2)
            arr = U(F(mg)+8)
            if not ok(arr) or num > 256:
                continue
            for k in range(num):
                mp = U(F(arr)+4*k)
                if ok(mp) and is_mesh(F(mp)):
                    header_name[F(mp)] = names[i]
    except Exception:
        pass

    # (2) also detect headers by structure (catches the unmapped ones, e.g. tram)
    headers = set(header_name)
    for o in range(0x80, N-0x14, 4):
        if not is_mesh(o):
            continue
        num = U(o+8); matp = F(U(o+0xc)); mainp = F(U(o+0x10))
        if 1 <= num <= 256 and 0 <= matp <= N-4 and 0 <= mainp <= N-4 and matp+2*num <= N and mainp+8*num <= N:
            headers.add(o)
    hs = sorted(headers)

    # (3) each mesh's blocks: follow the mesh header's own pointers
    #     (main pointers -> block pointers -> region with concatenated blocks).
    #     Each block pointer = u32 ptr + u16 size-in-qwords + u16 num verts.
    #     Only fall back to the tag scan if the pointers do not resolve.
    bymesh = {}
    for o in hs:
        n_g = U(o + 8)
        main = F(U(o + 0x10))
        regions = []
        if 1 <= n_g <= 256 and 0 <= main <= N - 8*n_g:
            for gi in range(n_g):
                mp = F(U(main + 8*gi))
                cnt = U16(main + 8*gi + 4)
                if not (0 <= mp <= N-8) or not (1 <= cnt <= 4096):
                    regions = []
                    break
                for k in range(cnt):
                    e = mp + 8*k
                    bptr = F(U(e))
                    qw = U16(e + 4)
                    if 0 <= bptr < N and qw:
                        regions.append((bptr, min(N, bptr + qw*16)))
        found = []
        for (lo, hi) in regions:
            for off in range(lo, hi, 16):
                if d[off:off+3] in (b"\x98\x00\x02", b"\xc5\x01\x02") and \
                   re.search(rb"(\xee\x00|\x1b\x02).\x69", d[off:off+0x30], re.DOTALL):
                    found.append(off)
        if found:
            bymesh[o] = sorted(set(found))

    if not bymesh:      # fallback: tag scan + nearest header
        import bisect
        for b in _find_blocks(d):
            i = bisect.bisect_right(hs, b) - 1
            if i >= 0:
                bymesh.setdefault(hs[i], []).append(b)

    # (4) build the groups
    groups = []
    for o in hs:
        if o not in bymesh:
            continue
        bdata = [r for r in (_decode_block(d, b) for b in sorted(bymesh[o])) if r]
        verts, tris = _tris_from_blocks(bdata)
        if not tris:
            continue
        matp = F(U(o+0xc))
        mat = U16(matp) if 0 <= matp <= N-2 else -1
        groups.append({"name": header_name.get(o, ""), "voff": vbase+(o-0x80),
                       "material": mat, "verts": verts, "tris": tris})
    return groups


def parse_psp_container(path):
    """PSP vehicle container (vp_*.psppck): holds several embedded meshes, each
    starting with the game's mesh magic. Header fields are the same as in a
    standalone mesh.psppck, just relative to the magic offset."""
    d = open(path, "rb").read()
    N = len(d)
    base = struct.unpack_from("<I", d, 0)[0] - 0x80

    def U(o):
        return struct.unpack_from("<I", d, o)[0] if 0 <= o <= N-4 else 0

    heads = []
    for magic in (b"\xcc\x99\x16\x09", b"\x38\x54\x5a\x00"):
        heads += [m.start() for m in re.finditer(re.escape(magic), d)]
    heads.sort()

    groups = []
    for X in heads:
        mesh_list = U(X+4) - base
        mat_off = U(X+8) - base
        mesh_data = U(X+16) - base
        piece_id, num = d[X+0x19], d[X+0x1b]
        if not (0 <= mesh_list < N and 0 <= mat_off < N and 0 <= mesh_data < N) or not (1 <= num <= 64):
            continue
        for g in range(num):
            e = mesh_list + g*0xC
            if e + 6 > N:
                break
            p = U(e) - base
            cnt = struct.unpack_from("<H", d, e+4)[0] if e+6 <= N else 0
            if not (0 <= p < N) or not (1 <= cnt <= 4096):
                continue
            subs = []
            for k in range(cnt):
                po = p + 4*k
                if po + 4 > N:
                    break
                subs.append((struct.unpack_from("<H", d, po)[0], struct.unpack_from("<H", d, po+2)[0]))
            if not subs:
                continue
            g0 = min(s[0] for s in subs)
            g1 = max(s[0]+s[1] for s in subs)
            if mesh_data + g1*14 > N or g1 <= g0:
                continue
            verts = [list(struct.unpack_from("<hhh", d, mesh_data + i*14 + 8)) for i in range(g0, g1)]
            tris = []
            for i in range(2, g1-g0):
                a, b, c = (i-2, i-1, i) if i % 2 == 0 else (i-1, i-2, i)
                if verts[a] == verts[b] or verts[b] == verts[c] or verts[a] == verts[c]:
                    continue
                tris.append([a, b, c])
            if tris:
                mat = struct.unpack_from("<H", d, mat_off + 2*g)[0] if mat_off + 2*g + 2 <= N else 0
                groups.append({"name": "id%d" % piece_id, "voff": base + 0x80 + X - 0x80,
                               "material": mat, "verts": verts, "tris": tris})
    return groups


def _try_converter(path, container=False):
    """Use mc3_mesh_convert's readers when available: they handle MC3 PSP and
    MC:LA psppck, PS2 pck and Xbox xbck, locating blocks through the real
    pointers. `container=True` reads every mesh embedded in an Xbox container."""
    try:
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc3_mesh_convert.py")
        spec = importlib.util.spec_from_file_location("mc3_mesh_convert", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return None
    try:
        m = mod.read_xbck_container(path) if container else mod.load(path)
    except Exception:
        return None
    groups = []
    for gi, g in enumerate(m.groups):
        verts, tris = [], []
        for st in g["strips"]:
            base = len(verts)
            verts.extend([[v.pos[0]*m.scale, v.pos[1]*m.scale, v.pos[2]*m.scale] for v in st])
            for (a, b, c) in mod.tris_of_strip(st):
                tris.append([base+a, base+b, base+c])
        if tris:
            groups.append({"name": "group%d" % gi, "material": g["material"],
                           "verts": verts, "tris": tris})
    plat = {"pck": "PS2"}.get(m.source, m.source.replace("psppck:", "PSP ").upper())
    return (groups, plat) if groups else None


# Finding the vertex batches inside a packet.
#
# Walking the VIF stream properly would need STCYCL/STMASK semantics - the walk
# desynchronises on a masked UNPACK and lands 4 bytes short of the next command.
# Scanning for the 0x69 command byte alone is the other extreme: vertex and
# colour data hit that value by chance and a false match yields coordinates that
# look like permutations of the same few numbers.
#
# So: scan, but validate. A real UNPACK V3-16 has a small VU1 address in its
# immediate, a sane count, data that fits, and does not overlap the batch before
# it. The set is only trusted when the counts add up to what the list entry
# declared - otherwise the common single-batch layout is used.
_VIF_UNPACK = {0x60: ("S", 32), 0x61: ("S", 16), 0x62: ("S", 8),
               0x64: ("V2", 32), 0x65: ("V2", 16), 0x66: ("V2", 8),
               0x68: ("V3", 32), 0x69: ("V3", 16), 0x6A: ("V3", 8),
               0x6C: ("V4", 32), 0x6D: ("V4", 16), 0x6E: ("V4", 8),
               0x6F: ("V4", 5)}
_VIF_COMPS = {"S": 1, "V2": 2, "V3": 3, "V4": 4}


def _vif_data_size(cmd, num, mask, cl):
    """Bytes of data an UNPACK actually consumes.

    Three things have to be right or the walk desyncs and every command after it
    is read out of vertex data:

    * the data is padded to FOUR bytes, not sixteen;
    * a MASKED unpack only reads the components whose two mask bits are 00 - the
      others come from ROW or COL or are not written. City blocks issue a V4-8
      masked with STMASK = 0x55555555, every field ROW, which reads NOTHING and
      just fills a constant colour from STROW. Assuming num*4 bytes there skips
      144 bytes that do not exist;
    * STMASK carries 4 bytes of data of its own and STROW/STCOL carry 16.
    """
    kind, bits = _VIF_UNPACK[cmd & 0x6F]
    n = num if num else 256
    comps = _VIF_COMPS[kind]
    if not (cmd & 0x10):
        total = n * comps
    else:
        rows = cl if cl else 1
        per = []
        for c in range(min(rows, 4)):
            fields = [(mask >> (8 * c + 2 * k)) & 3 for k in range(4)]
            per.append(sum(1 for k in range(comps) if fields[k] == 0))
        total = sum(per[k % len(per)] for k in range(n))
    return ((total * bits + 7) // 8 + 3) // 4 * 4


def _vif_walk(d, base, size):
    """[[(rel_addr, cmd, data_offset, count)]] - one list per MSCAL.

    A block is a VIF command stream and each batch inside it ends with its own
    ITOP + MSCAL pair, so the batches are DELIMITED, not guessed. Relative to
    the ITOP the city lays out a 2-qword header at +0, the normal at +2, the UV
    at +50 and the position at +98 (measured over 3003 blocks: V3-16 lands on
    +98 in 3131 of 3147 streams).
    """
    end = base + size
    q = base + 0x0C
    mask, cl, itop = 0, 1, None
    out, pend = [], []
    while q < end - 3:
        cmd, num = d[q + 3], d[q + 2]
        imm = d[q] | (d[q + 1] << 8)
        if cmd == 0x20:                    # STMASK + 4 bytes
            mask = struct.unpack_from("<I", d, q + 4)[0]
            q += 8
            continue
        if cmd in (0x30, 0x31):            # STROW / STCOL + 16 bytes
            q += 20
            continue
        if cmd == 0x01:                    # STCYCL
            cl = imm & 0xFF
            q += 4
            continue
        if cmd == 0x04:                    # ITOP
            itop = imm
            q += 4
            continue
        if cmd in (0x14, 0x15, 0x17):      # MSCAL / MSCALF / MSCNT ends a batch
            if pend:
                out.append([(a - itop if itop is not None else a, c, o, n)
                            for a, c, o, n in pend])
            pend = []
            q += 4
            continue
        if (cmd & 0x60) == 0x60 and (cmd & 0x6F) in _VIF_UNPACK:
            n = num if num else 256
            if q + 4 + _vif_data_size(cmd, num, mask, cl) > end:
                return []                  # desynced; caller falls back
            pend.append((imm & 0x3FFF, cmd, q + 4, n))
            q += 4 + _vif_data_size(cmd, num, mask, cl)
            continue
        q += 4
    if pend:
        out.append([(a - itop if itop is not None else a, c, o, n)
                    for a, c, o, n in pend])
    return out


def _vif_batches(d, base, size, nv):
    """LEGACY offset guess; do not use this to decode city positions.

    This takes the first V3-16/V3-32 stream. That is not unambiguous: 2462
    chains in the LA port store position as V3-8 differential and also carry a
    V3-16 attribute stream. The counts still add up while the AABB becomes
    false. Current city readers use `mc3_vifpos.decode_batches`, selected by the
    short-packet header's VU address. Kept only for old third-party callers.

    Falls back to the old validated scan when the walk does not add up to the
    vertex count the block list declared, which keeps the parser working on
    anything that does not follow the city layout.
    """
    batches_ = _vif_walk(d, base, size)
    got = []
    for batch in batches_:
        pos = adc = None
        for rel, cmd, off, n in batch:
            if (cmd & 0x6F) in (0x68, 0x69) and pos is None:
                pos = (off, n)
            elif rel == 2 and (cmd & 0x6F) in (0x6A, 0x6E):
                # the ITOP+2 stream is the normal in s8, and bit 0 of the x byte is the
                # ADC: suppresses the triangle ending at that vertex. Only 7.3%
                # of city blocks have that stream, but in those that do the game
                # draws less than the strip suggests.
                adc = (off, 3 if (cmd & 0x6F) == 0x6A else 4)
        if pos:
            got.append(pos + (adc,))
    if got and sum(n for _o, n, _a in got) == nv:
        return got

    end = base + size
    cand = []
    for at in range(base + 0x0C, end - 3, 4):
        if d[at + 3] != 0x69:
            continue
        num = d[at + 2]
        imm = d[at] | (d[at + 1] << 8)
        if not (1 <= num <= 128) or (imm & 0x3FF) > 700 or at + 4 + 6 * num > end:
            continue
        if cand and at < cand[-1][0] + 6 * cand[-1][1]:
            continue                       # inside the previous batch's data
        cand.append((at + 4, num, None))
    return (cand if cand and sum(n for _o, n, _a in cand) == nv
            else [(base + 0x10, nv, None)])


def _place(M, x, y, z):
    """Model space to world through the instance's 3x4, rows then translation."""
    return [M[3][a] + x * M[0][a] + y * M[1][a] + z * M[2][a] for a in range(3)]


def parse_ps2_city(path, limit=0):
    """City container (header type 67, or 4 for the _fog variants).

    City packets are PLANAR - one UNPACK for the positions and a separate one
    for the UVs - where a vehicle packet interleaves them, so they need their
    own decoder rather than the shared `_decode_block`.

    The 16-bit value that looks like a per-strip parity flag is the immediate of
    a VIF **ITOP** command, sitting right before the MSCAL that ends each batch.
    The VU program starts with `XITOP` and reads everything relative to it, so
    that number is the base of the batch, not a flag: atlanta uses 158 and 453,
    and relative to it the block lays out a 2-qword header at +0, the normal at
    +2, the UV at +50 and the position at +98. The winding rule is fixed -
    triangle i is (i-2, i-1, i) when i is even, (i-1, i-2, i) when odd.

    The city DOES have a normal stream, contrary to what this file used to say.
    It only appears on 7.3% of blocks (759 of atlanta's 10338) and the earlier
    walk never reached it, because a masked UNPACK under STMASK = 0x55555555
    reads no data at all and skipping num*4 bytes there desyncs the rest of the
    packet. Bit 0 of its x byte is the ADC flag, and honouring it drops 3.5% of
    atlanta's triangles and 6.1% of tokyo's - all of them faces the game never
    draws.

    One group per model, named from the object that owns the mesh.
    """
    d = open(path, "rb").read()
    N = len(d)
    va, kind, version, size = struct.unpack_from("<4I", d, 0)
    if kind not in (67, 4) or version != 1:
        raise ValueError("not a city container (type %d version %d)" % (kind, version))
    BASE = va - 0x80

    def F(v):
        return v - BASE

    def U(o):
        return struct.unpack_from("<I", d, o)[0] if 0 <= o <= N - 4 else 0

    def U16(o):
        return struct.unpack_from("<H", d, o)[0] if 0 <= o <= N - 2 else 0

    def ok(v):
        return bool(v) and 0x80 <= v - BASE <= N - 4

    # The mesh tag is a vtable left over from the build, so it is NOT the same
    # value in every city: atlanta, detroit and san diego use 0x007A1E18 while
    # tokyo uses 0x007A22A0. Detect it instead of hard-coding - take the value
    # that most often sits in front of a usable group table.
    #
    # The accepted range covers BOTH forms the value takes. A retail file stores
    # a TOKEN (0x007A....); a file we generate stores the address the token
    # translates to at load (0x0062....), because an object we create never
    # enters the list the game translates. Restricting to the token range made
    # this reader blind to our own output - the structural test below is what
    # actually validates a candidate anyway.
    votes = {}
    for o in range(0x80, N - 0x40, 4):
        v = U(o)
        if not (0x600000 <= v < 0x7C0000):
            continue
        ng, gt = U16(o + 8), U(o + 0x10)
        if not (1 <= ng <= 64) or not ok(gt):
            continue
        lp, cnt, _ = struct.unpack_from("<IHH", d, F(gt))
        if ok(lp) and 1 <= cnt <= 4096:
            votes[v] = votes.get(v, 0) + 1
    if not votes:
        return []
    MESH_TAG = max(votes, key=votes.get)

    # mesh virtual address -> (name, exact model-to-world matrix).
    #
    # Walk the real graph instead of guessing: MapRoot -> hoods -> the two
    # component arrays -> each component's data (+0x0C) -> buffers[0][0]
    # (+0x3C) -> the mesh. Looking for one ComponentData vtable only finds a
    # fraction of them - san diego has 5008 instance components on 0x7A0090 but
    # another 628 unique ones on 0x79FF88, and going by vtable left most meshes
    # ownerless, piled up on the origin.
    #
    # Vertices are local to the model. Instances carry a 3x4 at +0x20; unique
    # components have no matrix, and use the exact world translation stored in
    # ComponentData+0x2C. The previous grid fit ignored that field and forced
    # y=0, so OBJ/culling audits displaced otherwise correct components.
    info = {}
    R = 0x80                              # MapRoot sits at the start of the body
    nh, hp = U(R + 0x14), U(R + 0x18)
    if ok(hp):
        hb = F(hp)
        for h in range(nh if nh < 64 else 0):
            e = hb + 20 * h
            for cnt, arr, stride, placed in ((U(e + 0x04), U(e + 0x08), 28, False),
                                             (U(e + 0x0C), U(e + 0x10), 96, True)):
                if not ok(arr) or cnt > 20000:
                    continue
                ab = F(arr)
                for k in range(cnt):
                    comp = ab + stride * k
                    dp = U(comp + 0x0C)
                    if not ok(dp):
                        continue
                    cd = F(dp)
                    nm = bytes(d[cd + 0x70:cd + 0xB0]).split(b"\x00")[0]
                    nm = nm.decode("latin1") if nm and all(32 <= c < 127 for c in nm) else ""
                    # The instance carries a 3x4 transform, not just a
                    # position: three unit rows of rotation at +0x20 and the
                    # translation at +0x44. Using the translation alone draws
                    # every rotated building facing the wrong way - and it also
                    # produced a collision box 90 degrees out when one was
                    # generated from these numbers. The row-major reading
                    # (world = lx*r0 + ly*r1 + lz*r2 + t) is the right one:
                    # solving the same model's local centre through two
                    # differently rotated instances of it gives (19.90, 10.0,
                    # -22.41) from both.
                    mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                           (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
                    if placed:
                        try:
                            r = [struct.unpack_from("<3f", d, comp + 0x20 + 12 * k)
                                 for k in range(4)]
                            if all(-1e5 < c < 1e5 for c in r[3]):
                                mat = tuple(tuple(x) for x in r)
                        except Exception:
                            pass
                    else:
                        try:
                            c = struct.unpack_from('<3f', d, cd + 0x2C)
                            if all(-1e5 < x < 1e5 for x in c):
                                mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                       (0.0, 0.0, 1.0), tuple(c))
                        except Exception:
                            pass
                    # buffers[2][5] lives at +0x3C - read all ten slots, not
                    # just [0][0]. The extra ones hold the LOD variants of the
                    # same model, so they share its name and its place in the
                    # world. Taking only the first slot is what left two thirds
                    # of the meshes ownerless, heaped on the origin.
                    for bslot in range(10):
                        mp = U(cd + 0x3C + 4 * bslot)
                        if ok(mp) and mp not in info:
                            info[mp] = (nm if bslot == 0 else "%s#%d" % (nm, bslot), mat)

    groups, orphans = [], []
    for mo in range(0x80, N - 0x20, 4):
        if U(mo) != MESH_TAG:
            continue
        ngrp = U16(mo + 8)
        gt, mt = U(mo + 0x10), U(mo + 0x0C)
        if not (1 <= ngrp <= 64) or not ok(gt):
            continue
        verts, tris = [], []
        mat = U16(F(mt)) if ok(mt) else 0
        nm, M = info.get(mo + BASE, (None, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                            (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))))
        for gi in range(ngrp):
            lp, cnt, _ = struct.unpack_from("<IHH", d, F(gt) + 8 * gi)
            if not ok(lp) or cnt > 4096:
                break
            for bi in range(cnt):
                bp, qw, nv = struct.unpack_from("<IHH", d, F(lp) + 8 * bi)
                if not ok(bp) or not (3 <= nv <= 256):
                    continue
                b = F(bp)
                # Select positions from the VU address declared by each short
                # packet header. Taking the first V3 stream is wrong: 2462
                # chains use V3-8 difference positions and also contain a
                # V3-16 attribute stream. The old reader chose that attribute,
                # kept the expected count, and invented ~4096-unit AABBs.
                try:
                    pos_batches = _vifpos.decode_batches(bytes(d[b:b + qw * 16]))
                except ValueError:
                    continue
                if sum(len(x) for x in pos_batches) != nv:
                    continue
                walked = _vif_walk(d, b, qw * 16)
                adcs = []
                for batch in walked:
                    adc = None
                    for rel, cmd, off, n in batch:
                        if rel == 2 and (cmd & 0x6F) in (0x6A, 0x6E):
                            adc = (off, 3 if (cmd & 0x6F) == 0x6A else 4)
                            break
                    adcs.append(adc)
                # Each batch is its OWN strip - they go to different halves of
                # the VU1 buffer, so joining them would invent two triangles at
                # every seam, bridging parts of the mesh that never touch. That
                # is the same trap the vehicle converter avoids with --no-merge.
                for li, positions in enumerate(pos_batches):
                    vn = len(positions)
                    adc = adcs[li] if li < len(adcs) else None
                    base = len(verts)
                    verts.extend([_place(M, p[0], p[1], p[2]) for p in positions])
                    skip_ = [False] * vn
                    if adc and adc[0] + adc[1] * vn <= N:
                        no, st = adc
                        skip_ = [(d[no + st * i] & 1) == 1 for i in range(vn)]
                    for i in range(2, vn):
                        if skip_[i]:
                            continue
                        a, bb, c = (i - 2, i - 1, i) if i % 2 == 0 else (i - 1, i - 2, i)
                        A, B, C = verts[base + a], verts[base + bb], verts[base + c]
                        if A == B or B == C or A == C:      # degenerate bridge
                            continue
                        tris.append([base + a, base + bb, base + c])
        if tris:
            if nm is None:
                orphans.append(len(groups))
            groups.append({"name": nm or ("mesh@%06X" % mo),
                           "voff": mo + BASE, "material": mat,
                           "verts": verts, "tris": tris})
            if limit and len(groups) >= limit:
                break

    # Only about 45% of the meshes are reachable through
    # hood -> component -> data -> buffers[0][0]; the rest are referenced from
    # somewhere not yet identified (LOD variants, most likely) and would sit on
    # the origin in a heap. Serialisation is contiguous per model, so an orphan
    # inherits the position of the nearest placed mesh by file offset. That is a
    # HEURISTIC, not something read out of the file - good enough to lay a city
    # out, not something to trust for anything else.
    placed = [(g["voff"], g) for g in groups
              if g["voff"] in info and any(info[g["voff"]][1])]
    if placed and orphans:
        placed.sort()
        import bisect
        keys = [k for k, _g in placed]
        for gi in orphans:
            g = groups[gi]
            j = bisect.bisect_left(keys, g["voff"])
            best = None
            for cand in (j - 1, j):
                if 0 <= cand < len(placed):
                    if best is None or abs(placed[cand][0] - g["voff"]) < abs(best[0] - g["voff"]):
                        best = placed[cand]
            if best is None:
                continue
            _nm, (ox, oy, oz) = (info[best[0]][0], info[best[0]][1][3])
            if not (ox or oy or oz):
                continue
            g["verts"] = [[v[0] + ox, v[1] + oy, v[2] + oz] for v in g["verts"]]
            g["name"] += " ~"          # marks an inherited position
    return groups


CITY_LIMIT = 0          # --limit N: caps how many city meshes are loaded


def parse_any(path):
    low = os.path.basename(path).lower()
    # city container: the header type tells it apart from everything else
    try:
        if struct.unpack_from("<I", open(path, "rb").read(16), 4)[0] in (67, 4):
            g = parse_ps2_city(path, limit=CITY_LIMIT)
            if g:
                return g, "PS2 city"
    except Exception:
        pass
    if "traffic" in low or "_peds" in low:
        try:
            g = parse_ps2_container(path)
        except Exception:
            g = []
        if g:
            return g, "PS2 traffic"
        # slice cut out of a container: falls through to the single-mesh readers
    # Xbox container: the embedded meshes come from the LOD slot pointers
    if low.endswith(".xbck") and not low.endswith(".mesh.xbck"):
        r = _try_converter(path, container=True)
        if r:
            return r[0], "Xbox container"
    # single mesh (psppck MC3/MC:LA, PS2 pck, Xbox xbck) -> shared readers
    r = _try_converter(path)
    if r:
        return r
    if low.endswith(".psppck"):
        g = parse_psp_container(path)      # vp_*.psppck holds embedded meshes
        if g:
            return g, "PSP container"
        return parse_psppck(path), "PSP"
    try:
        return parse_ps2_pck(path), "PS2"
    except Exception:
        return parse_ps2_container(path), "PS2"


# ---------------------------------------------------------------------------
# HTML generator (self-contained WebGL2)
# ---------------------------------------------------------------------------

def _hsv(h, s, v):
    import colorsys
    return list(colorsys.hsv_to_rgb(h, s, v))


def build_html(groups, title, platform):
    # global bounding box -> centre and scale to fit in ~2 units
    allv = [v for g in groups for v in g["verts"]]
    if not allv:
        raise ValueError("empty mesh")
    mn = [min(v[i] for v in allv) for i in range(3)]
    mx = [max(v[i] for v in allv) for i in range(3)]
    cen = [(mn[i]+mx[i])/2 for i in range(3)]
    ext = max(mx[i]-mn[i] for i in range(3)) or 1.0
    sc = 2.0/ext

    ng = len(groups)
    data = []  # pos3 + color3 + bary3 per vertex, non-indexed triangles
    bary = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    group_info = []
    for gi, g in enumerate(groups):
        col = _hsv((gi/max(ng, 1)) % 1.0, 0.55, 0.95)
        gstart = len(data)
        vs = g["verts"]
        for tri in g["tris"]:
            for k in range(3):
                p = vs[tri[k]]
                data.append([(p[0]-cen[0])*sc, (p[1]-cen[1])*sc, (p[2]-cen[2])*sc,
                             col[0], col[1], col[2], bary[k][0], bary[k][1], bary[k][2]])
        gv = g["verts"]
        gmn = [min(v[i] for v in gv) for i in range(3)] if gv else [0, 0, 0]
        gmx = [max(v[i] for v in gv) for i in range(3)] if gv else [0, 0, 0]
        gc = [((gmn[i] + gmx[i]) / 2 - cen[i]) * sc for i in range(3)]
        gr = max((gmx[i] - gmn[i]) * sc for i in range(3)) / 2 or 0.2
        group_info.append({"name": g["name"], "material": g["material"], "color": col,
                           "c": gc, "r": gr,
                           "tris": len(g["tris"]), "start": gstart, "count": len(data)-gstart,
                           "voff": g.get("voff")})
    flat = [round(x, 5) for row in data for x in row]
    payload = {"verts": flat, "groups": group_info, "title": title, "platform": platform,
               "ntri": len(data)//3}
    return _HTML_TEMPLATE.replace("/*DATA*/", json.dumps(payload))


_HTML_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>MC3 mesh viewer</title>
<style>
 html,body{margin:0;height:100%;background:#15171c;color:#cdd3de;font:13px system-ui,monospace;overflow:hidden}
 #c{display:block;width:100vw;height:100vh;cursor:grab}
 #c:active{cursor:grabbing}
 #hud{position:fixed;top:8px;left:8px;background:#0009;padding:8px 10px;border-radius:6px;line-height:1.5;pointer-events:none;white-space:pre}
 #leg{position:fixed;top:8px;right:8px;background:#0009;padding:8px 10px;border-radius:6px;
      width:300px;max-height:calc(100vh - 16px);overflow-y:auto;overflow-x:hidden}
 #leg .row{display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #leg .row:hover{background:#ffffff14}
 .sw{width:12px;height:12px;border-radius:2px;display:inline-block}
 b{color:#8ecbff}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"></div>
<div id="leg"></div>
<script>
const P = /*DATA*/;
const cv = document.getElementById('c');
const gl = cv.getContext('webgl2', {antialias:true});
let cull=false, wire=false, colorGroup=true, backRed=true, selGroup=-1;

const vs=`#version 300 es
in vec3 aPos; in vec3 aColor; in vec3 aBary;
uniform mat4 uMVP;
out vec3 vW; out vec3 vC; out vec3 vB;
void main(){ vW=aPos; vC=aColor; vB=aBary; gl_Position=uMVP*vec4(aPos,1.0); }`;
const fs=`#version 300 es
precision highp float;
in vec3 vW; in vec3 vC; in vec3 vB;
uniform int uWire,uBackRed,uColorGroup;
out vec4 o;
void main(){
 vec3 n=normalize(cross(dFdx(vW),dFdy(vW)));
 vec3 L=normalize(vec3(0.35,0.75,0.55));
 float dif=abs(dot(n,L))*0.75+0.28;
 vec3 base = uColorGroup==1? vC : vec3(0.78,0.80,0.85);
 if(!gl_FrontFacing && uBackRed==1) base=vec3(1.0,0.12,0.12);
 vec3 col=base*dif;
 if(uWire==1){ vec3 d=fwidth(vB); vec3 a=smoothstep(vec3(0.0),d*1.4,vB);
   float e=min(min(a.x,a.y),a.z); col=mix(vec3(0.02,0.02,0.03),col,e); }
 o=vec4(col,1.0);
}`;
function sh(t,s){const o=gl.createShader(t);gl.shaderSource(o,s);gl.compileShader(o);
 if(!gl.getShaderParameter(o,gl.COMPILE_STATUS))console.error(gl.getShaderInfoLog(o));return o;}
const prog=gl.createProgram();
gl.attachShader(prog,sh(gl.VERTEX_SHADER,vs));gl.attachShader(prog,sh(gl.FRAGMENT_SHADER,fs));
gl.linkProgram(prog);gl.useProgram(prog);
const buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);
gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(P.verts),gl.STATIC_DRAW);
const stride=9*4;
function attr(name,size,off){const l=gl.getAttribLocation(prog,name);gl.enableVertexAttribArray(l);
 gl.vertexAttribPointer(l,size,gl.FLOAT,false,stride,off);}
attr('aPos',3,0);attr('aColor',3,12);attr('aBary',3,24);
const uMVP=gl.getUniformLocation(prog,'uMVP');
const uWire=gl.getUniformLocation(prog,'uWire');
const uBackRed=gl.getUniformLocation(prog,'uBackRed');
const uColorGroup=gl.getUniformLocation(prog,'uColorGroup');
gl.enable(gl.DEPTH_TEST);

// ---- mat4 ----
function persp(f,a,n,fa){const t=1/Math.tan(f/2);return[t/a,0,0,0, 0,t,0,0, 0,0,(fa+n)/(n-fa),-1, 0,0,2*fa*n/(n-fa),0];}
function mul(a,b){const o=new Array(16);for(let i=0;i<4;i++)for(let j=0;j<4;j++){let s=0;for(let k=0;k<4;k++)s+=a[k*4+j]*b[i*4+k];o[i*4+j]=s;}return o;}
function look(e,c,u){let z=nrm(sub(e,c)),x=nrm(cr(u,z)),y=cr(z,x);
 return[x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0, -dot(x,e),-dot(y,e),-dot(z,e),1];}
function sub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
function cr(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
function nrm(a){let l=Math.hypot(a[0],a[1],a[2])||1;return[a[0]/l,a[1]/l,a[2]/l];}

let yaw=0.7,pitch=0.5,dist=4.0,panx=0,pany=0,tgt=[0,0,0];
// frames the selected mesh: the camera orbits its centre instead of the origin
function focus(i){const g=P.groups[i];if(!g||!g.c)return;
 tgt=[g.c[0],g.c[1],g.c[2]];panx=0;pany=0;dist=Math.max(g.r*3.2,0.35);}
function unfocus(){tgt=[0,0,0];panx=0;pany=0;dist=4.0;}
function render(){
 const w=cv.clientWidth,h=cv.clientHeight;
 if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;}
 gl.viewport(0,0,w,h);gl.clearColor(0.08,0.09,0.11,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
 if(cull){gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);}else gl.disable(gl.CULL_FACE);
 const tx=tgt[0]+panx,ty=tgt[1]+pany,tz=tgt[2];
 const eye=[tx+dist*Math.cos(pitch)*Math.cos(yaw),ty+dist*Math.sin(pitch),tz+dist*Math.cos(pitch)*Math.sin(yaw)];
 const V=look(eye,[tx,ty,tz],[0,1,0]);
 const Pm=persp(1.0,w/h,0.05,100);
 gl.uniformMatrix4fv(uMVP,false,mul(Pm,V));
 gl.uniform1i(uWire,wire?1:0);gl.uniform1i(uBackRed,backRed?1:0);gl.uniform1i(uColorGroup,colorGroup?1:0);
 if(selGroup>=0 && P.groups[selGroup]){const g=P.groups[selGroup];gl.drawArrays(gl.TRIANGLES,g.start,g.count);}
 else gl.drawArrays(gl.TRIANGLES,0,P.ntri*3);
 hud();
}
function loop(){render();requestAnimationFrame(loop);}
// ---- interacao ----
let drag=null,lx=0,ly=0;
cv.addEventListener('mousedown',e=>{drag=e.button;lx=e.clientX;ly=e.clientY;e.preventDefault();});
window.addEventListener('mouseup',()=>drag=null);
window.addEventListener('mousemove',e=>{if(drag===null)return;const dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;
 if(drag===0){yaw+=dx*0.01;pitch=Math.max(-1.5,Math.min(1.5,pitch+dy*0.01));}
 else{panx-=dx*0.004*dist;pany+=dy*0.004*dist;}});
cv.addEventListener('wheel',e=>{dist*=Math.exp(e.deltaY*0.001);e.preventDefault();},{passive:false});
cv.addEventListener('contextmenu',e=>e.preventDefault());
window.addEventListener('keydown',e=>{const k=e.key.toLowerCase();
 if(k==='c')cull=!cull; else if(k==='w')wire=!wire; else if(k==='g')colorGroup=!colorGroup; else if(k==='b')backRed=!backRed;});
function hud(){document.getElementById('hud').textContent=
`${P.platform}  ${P.title}
${P.groups.length} groups  ${P.ntri} triangles
[C] culling: ${cull?'ON (like the game)':'off'}
[B] back-face red: ${backRed?'ON':'off'}
[W] wireframe: ${wire?'ON':'off'}
[G] color by group: ${colorGroup?'ON':'off'}
drag=rotate  scroll=zoom  right=pan`;}
// legend + mesh selector
function lbl(g,i){
 const off = g.voff!=null ? ('@0x'+(g.voff>>>0).toString(16)) : (g.name||('mesh'+i));
 const nm  = (g.voff!=null && g.name) ? (' '+g.name) : '';
 return i+': '+off+nm+'  m'+g.material+' ('+g.tris+'t)';
}
const leg=document.getElementById('leg');
const sel=document.createElement('select');
sel.innerHTML='<option value="-1">all ('+P.groups.length+' meshes, '+P.ntri+'t)</option>';
P.groups.forEach((g,i)=>{sel.innerHTML+='<option value="'+i+'">'+lbl(g,i)+'</option>';});
sel.onchange=e=>{selGroup=parseInt(e.target.value);
 if(selGroup>=0)focus(selGroup);else unfocus();};
sel.style.cssText='width:250px;margin:4px 0;background:#222;color:#cdd3de;border:1px solid #444';
const title=document.createElement('b');title.textContent='meshes (virtual offset | m=shader idx)';
leg.appendChild(title);leg.appendChild(sel);
const glist=document.createElement('div');leg.appendChild(glist);
P.groups.forEach((g,i)=>{const c=g.color.map(x=>Math.round(x*255));
 const div=document.createElement('div');div.className='row';div.style.cursor='pointer';
 div.innerHTML='<span class="sw" style="background:rgb('+c[0]+','+c[1]+','+c[2]+')"></span>'+lbl(g,i);
 div.onclick=()=>{selGroup=i;sel.value=i;focus(i);};
 glist.appendChild(div);});
loop();
</script></body></html>"""


def write_html(path):
    groups, plat = parse_any(path)
    html = build_html(groups, os.path.basename(path), plat)
    out = _out_path.path(os.path.basename(path) + ".html")
    open(out, "w", encoding="utf-8").write(html)
    return out, groups, plat


# ---------------------------------------------------------------------------
# Native viewer (matplotlib) - opens a window directly, no HTML
# ---------------------------------------------------------------------------

def _group_faces(g):
    """Precompute per face: the 3 vertices, the geometric normal and whether it
    is FLIPPED (winding disagrees with the smooth normal = a 'see-through' face)."""
    import numpy as np
    V = np.asarray(g["verts"], dtype=float)
    T = np.asarray(g["tris"], dtype=int)
    if len(T) == 0:
        return None
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(fn, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    fn_unit = fn / ln
    # smooth per-vertex normal (average of the face normals)
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, T[:, k], fn_unit)
    vln = np.linalg.norm(vn, axis=1, keepdims=True)
    vln[vln == 0] = 1.0
    vn /= vln
    favg = (vn[T[:, 0]] + vn[T[:, 1]] + vn[T[:, 2]])
    inverted = np.einsum("ij,ij->i", fn_unit, favg) < 0
    tris_xyz = np.stack([p0, p1, p2], axis=1)  # (F,3,3)
    return tris_xyz, fn_unit, inverted


def show_native(groups, title, platform):
    import numpy as np
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    # remove the viewer's keys from matplotlib's default shortcuts
    # (q=quit, g=grid, arrows=back/forward, s=save, etc.) so they do not clash
    mine = {"left", "right", "a", "b", "g", "w", "q", "e"}
    for km in [k for k in mpl.rcParams if k.startswith("keymap.")]:
        try:
            mpl.rcParams[km] = [x for x in mpl.rcParams[km] if x not in mine]
        except Exception:
            pass

    face_data = [_group_faces(g) for g in groups]
    ng = len(groups)
    gcol = [plt.cm.hsv((i / max(ng, 1)) % 1.0)[:3] for i in range(ng)]

    allv = np.concatenate([g["verts"] for g in groups if g["verts"]], axis=0)
    cen = (allv.min(0) + allv.max(0)) / 2
    rad = (allv.max(0) - allv.min(0)).max() / 2 or 1.0
    light = np.array([0.4, 0.7, 0.6]); light /= np.linalg.norm(light)

    st = {"sel": -1, "backred": True, "wire": False, "group": True, "roll": 0.0, "init": True}
    fig = plt.figure(f"MC3 viewer - {title}", figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")

    def _apply_view(el, az, roll):
        try:
            ax.view_init(elev=el, azim=az, roll=roll)
        except TypeError:      # matplotlib < 3.6 has no roll
            ax.view_init(elev=el, azim=az)

    def redraw():
        if st["init"]:
            el, az = -90.0, 90.0
            st["init"] = False
        else:
            el, az = ax.elev, ax.azim
        ax.clear()
        polys, colors = [], []
        for gi, g in enumerate(groups):
            if st["sel"] >= 0 and gi != st["sel"]:
                continue
            fd = face_data[gi]
            if fd is None:
                continue
            txyz, fn, inv = fd
            base = np.array(gcol[gi]) if st["group"] else np.array([0.78, 0.80, 0.85])
            shade = (np.abs(fn @ light) * 0.7 + 0.3)
            for i in range(len(txyz)):
                c = np.array([0.95, 0.12, 0.12]) if (st["backred"] and inv[i]) else base
                colors.append(np.clip(c * shade[i], 0, 1))
                polys.append(txyz[i])
        pc = Poly3DCollection(polys, facecolors=colors,
                              edgecolors=("k" if st["wire"] else "none"),
                              linewidths=0.15)
        ax.add_collection3d(pc)
        # frame whatever is on screen: the whole city when showing all, the
        # selected mesh when stepping through them - otherwise a single building
        # is a speck in the middle of a 2 km box
        c, r = cen, rad
        if st["sel"] >= 0 and groups[st["sel"]]["verts"]:
            gv = np.asarray(groups[st["sel"]]["verts"], dtype=float)
            c = (gv.min(0) + gv.max(0)) / 2
            r = max((gv.max(0) - gv.min(0)).max() / 2, 1e-3) * 1.25
        ax.set_xlim(c[0]-r, c[0]+r); ax.set_ylim(c[1]-r, c[1]+r); ax.set_zlim(c[2]-r, c[2]+r)
        try:
            ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        ax.set_axis_off()
        _apply_view(el, az, st["roll"])
        if st["sel"] >= 0:
            g = groups[st["sel"]]
            lab = "%d: %s @0x%x  m%s (%dt)" % (st["sel"], g.get("name") or "", g.get("voff", 0), g["material"], len(g["tris"]))
        else:
            lab = "ALL (%d meshes, %d tris)" % (ng, sum(len(g["tris"]) for g in groups))
        ax.set_title("%s | %s\n[<- ->]=mesh  A=all  B=flipped-red:%s  G=group-color:%s  W=wire:%s  Q/E=roll(%d)"
                     % (platform, lab, st["backred"], st["group"], st["wire"], int(st["roll"])), fontsize=9)
        fig.canvas.draw_idle()

    def onkey(e):
        k = (e.key or "").lower()
        if k == "right":
            st["sel"] = min(ng-1, st["sel"]+1)
        elif k == "left":
            st["sel"] = max(-1, st["sel"]-1)
        elif k == "a":
            st["sel"] = -1
        elif k == "b":
            st["backred"] = not st["backred"]
        elif k == "g":
            st["group"] = not st["group"]
        elif k == "w":
            st["wire"] = not st["wire"]
        elif k == "q":
            st["roll"] = (st["roll"] - 15) % 360
        elif k == "e":
            st["roll"] = (st["roll"] + 15) % 360
        else:
            return
        redraw()

    fig.canvas.mpl_connect("key_press_event", onkey)
    redraw()
    plt.show()


# ---------------------------------------------------------------------------
def _open_html(p, groups, plat):
    out = _out_path.path(os.path.basename(p) + ".html")
    open(out, "w", encoding="utf-8").write(build_html(groups, os.path.basename(p), plat))
    import webbrowser
    webbrowser.open("file:///" + os.path.abspath(out).replace("\\", "/"))
    print("opened in browser:", out)


if __name__ == "__main__":
    args = sys.argv[1:]
    # The browser is the default: it handles a whole city (300k triangles, ~50 MB
    # of HTML) where matplotlib crawls. --native asks for the old window.
    want_native = "--native" in args
    for a in args:
        if a.startswith("--limit="):    # cap the mesh count if the page is too big
            CITY_LIMIT = int(a.split("=", 1)[1])
    files = [a for a in args if not a.startswith("-")]
    if not files:
        print("usage: python mc3_view.py <file.pck|.psppck> [--native] [--limit=N]")
        print("  (or drag a .pck/.psppck onto this .py to open it in the browser)")
        sys.exit(0)
    p = files[0]
    groups, plat = parse_any(p)
    print("[%s] %s: %d meshes, %d triangles" % (plat, os.path.basename(p), len(groups),
                                                sum(len(g["tris"]) for g in groups)))
    if not groups:
        print("no geometry found."); sys.exit(0)
    if not want_native:
        _open_html(p, groups, plat)
    else:
        try:
            show_native(groups, os.path.basename(p), plat)
        except Exception as e:
            print("native viewer unavailable (%s); using browser..." % e)
            _open_html(p, groups, plat)
