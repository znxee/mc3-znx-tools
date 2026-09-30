"""Python twin of losangeles_bound.mod: builds the same MC3 _bnd body from MC2's
losangeles.rsc (BND0) + the tokyo_bnd.pck skeleton. Usage: build_reference.py OUT.pck
(Region order differs from the mod: here each tree keeps polys/verts/idx/blocks
together; the mod puts all geometry first and both trees last. Same content.)"""
import os
import struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
import mc3_bound as MB

RSC = os.path.join(os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets'), 'resource/losangeles/losangeles.rsc')
TOKYO = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/tokyo_bnd.pck')
VBASE = 0x06800000
OCT_DEPTH, OCT_PER_LEAF = 8, 48
QT_DEPTH, QT_PER_LEAF = 3, 15

rsc = open(RSC, 'rb').read()
tk = open(TOKYO, 'rb').read()
u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]
f32 = lambda b, o: struct.unpack_from('<f', b, o)[0]

count = u32(rsc, 4)
dirent = [(u32(rsc, 8 + 8 * i), rsc[12 + 8 * i:16 + 8 * i]) for i in range(count)]
def entry(i): return dirent[i][0]

grid = quad = None
for i, (off, fc) in enumerate(dirent):
    if fc == b'BND0':
        t = rsc[off + 4]
        if t == 0x0B: grid = off
        if t == 0x0A: quad = off
print('grid', hex(grid), 'quad', hex(quad))

def pmt_name(h):
    o = entry((h & 0x3FFF) - 1)
    assert rsc[o:o+4] == b'\x00\xff\xff\x0f'
    return rsc[o + 8:o + 40].split(b'\0')[0].decode()

def physical(name):
    return MB.physical_material_name(name)

# donor tables (tokyo offsets, file-relative)
MAT10, MAT9 = 0x1750, 0xAC750
mat10 = [u32(tk, MAT10 + 4 * i) for i in range(7)]
mat9 = [u32(tk, MAT9 + 4 * i) for i in range(15)]
def local(name, table):
    g = MB.GLOBAL_MATERIAL_IDS[physical(name)]
    return table.index(g) if g in table else 0

# ---- nodes of the MC2 block: list of (verts, polys) in MC2 node layout
def node_geom(base, node, table, vbase, pbase):
    nv, npo = u32(rsc, node + 0x4C), u32(rsc, node + 0x50)
    vo, po = base + u32(rsc, node + 0x74), base + u32(rsc, node + 0x78)
    mo, mc = base + u32(rsc, node + 0x80), u32(rsc, node + 0x84)
    lut = [local(pmt_name(u32(rsc, mo + 4 * k)), table) for k in range(mc)]
    verts = [struct.unpack_from('<3f', rsc, vo + 12 * i) for i in range(nv)]
    polys = []
    for k in range(npo):
        raw = bytearray(rsc[po + 32 * k:po + 32 * k + 32])
        m = raw[12]
        raw[12] = lut[m] if m < len(lut) else 0
        idx = list(struct.unpack_from('<4H', raw, 16))
        tri = idx[3] == 0            # MC2: 4th index 0 = triangle (its n3 is 0, not 0xFFFF)
        for j in range(3 if tri else 4):
            idx[j] += vbase
        struct.pack_into('<4H', raw, 16, *idx)
        struct.pack_into('<4H', raw, 24, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
        polys.append(bytes(raw))
    return verts, polys

# type 9 from the grid cells
ncell = 42
V9, P9 = [], []
for c in range(ncell):
    node = grid + 0x74 + 0x90 * c
    if u32(rsc, node + 0x84) == 0xFFFFFFFF or not u32(rsc, node + 0x4C):
        continue
    v, p = node_geom(grid, node, mat9, len(V9), len(P9))
    V9 += v; P9 += p
V10, P10 = node_geom(quad, quad, mat10, 0, 0)
print('type9 %d verts %d polys; type10 %d verts %d polys' % (len(V9), len(P9), len(V10), len(P10)))

def poly_box(V, raw):
    idx = struct.unpack_from('<4H', raw, 16)
    tri = idx[3] == 0 and u32(raw, 28) >> 16 == 0xFFFF
    pts = [V[i] for i in idx[:3 if tri else 4]]
    return [min(q[a] for q in pts) for a in range(3)], [max(q[a] for q in pts) for a in range(3)]

def build_tree(V, P, fan, depth, per_leaf):
    """returns (blocks: list of lists of slots, root, idxlist). slot = ('leaf',start,count) or ('blk',i)"""
    boxes = [poly_box(V, p) for p in P]
    lo = [min(q[a] for q in V) for a in range(3)]
    hi = [max(q[a] for q in V) for a in range(3)]
    side = max(hi[a] - lo[a] for a in range(3))
    ctr = [(lo[a] + hi[a]) / 2 for a in range(3)]
    clo = [ctr[a] - side / 2 for a in range(3)]
    chi = [ctr[a] + side / 2 for a in range(3)]
    blocks, idx, deep = [], [], [0]
    def rec(ids, blo, bhi, d):
        deep[0] = max(deep[0], d)
        if d >= depth or len(ids) <= per_leaf:
            s = ('leaf', len(idx), len(ids)); idx.extend(ids); return s
        mid = [(blo[a] + bhi[a]) / 2 for a in range(3)]
        bi = len(blocks); blocks.append(None)
        kids = []
        for o in range(fan):
            bx, bz, by = o & 1, (o >> 1) & 1, (o >> 2) & 1
            flo = [mid[0] if bx else blo[0], (mid[1] if by else blo[1]) if fan == 8 else blo[1], mid[2] if bz else blo[2]]
            fhi = [bhi[0] if bx else mid[0], (bhi[1] if by else mid[1]) if fan == 8 else bhi[1], bhi[2] if bz else mid[2]]
            sub = [i for i in ids if all(boxes[i][0][a] <= fhi[a] and boxes[i][1][a] >= flo[a] for a in range(3))]
            kids.append(rec(sub, flo, fhi, d + 1))
        blocks[bi] = kids
        return ('blk', bi)
    root = rec(list(range(len(P))), clo, chi, 0)
    cell = side / float(2 ** deep[0])
    return blocks, root, idx, lo, hi, clo, chi, cell

t9 = build_tree(V9, P9, 8, OCT_DEPTH, OCT_PER_LEAF)
t10 = build_tree(V10, P10, 4, QT_DEPTH, QT_PER_LEAF)
print('tree9: %d blocks %d refs; tree10: %d blocks %d refs' % (len(t9[0]), len(t9[2]), len(t10[0]), len(t10[2])))

# ---- layout: skeleton pieces (tokyo file offsets) -> body
PIECES = [(0x80, 0x150), (0x1750, 0x17C0), (0x1AC0, 0x1BC0), (0xAC750, 0xAC7E0), (0xCB160, 0xCB1C0)]
out = bytearray()
newpos = []
for a, z in PIECES:
    newpos.append(len(out)); out += tk[a:z]
def remap(fo):
    for (a, z), n in zip(PIECES, newpos):
        if a <= fo < z: return n + fo - a
    return None
def va(bo): return VBASE + bo
for (a, z), n in zip(PIECES, newpos):
    for o in range(n, n + z - a, 4):
        w = u32(out, o)
        if VBASE <= w < VBASE + len(tk) - 0x80:
            r = remap(w - VBASE + 0x80)
            struct.pack_into('<I', out, o, va(r) if r is not None else 0xDEADBEEF)
B = lambda fo: remap(fo)             # tokyo file offset -> body offset
NODE10, NODE9 = B(0xD0), B(0x1B20)
PAY10, PAY9 = B(0x1770), B(0xAC790)
ROOT10, ROOT9 = B(0x14C), B(0x176C)

def append_blob(blob):
    while len(out) % 16: out.append(0xCD)
    o = len(out); out.extend(blob); return o

def emit(node, pay, rootslot, V, P, t, fan, payroot_is9):
    blocks, root, idx, lo, hi, clo, chi, cell = t
    op = append_blob(b''.join(P))
    ov = append_blob(b''.join(struct.pack('<3f', *q) for q in V))
    oi = append_blob(struct.pack('<%dH' % len(idx), *idx))
    ob = []
    for _ in blocks:
        append_blob(struct.pack('<I', fan) + b'\xCD' * 12)
        ob.append(append_blob(bytes(4 * fan)))
    def val(s):
        if s[0] == 'leaf': return (s[1] << 16) | ((s[2] * 2) << 1) | 1
        return va(ob[s[1]])
    for bi, kids in enumerate(blocks):
        for j, s in enumerate(kids):
            struct.pack_into('<I', out, ob[bi] + 4 * j, val(s))
    struct.pack_into('<I', out, rootslot, val(root))
    ctr = [(lo[a] + hi[a]) / 2 for a in range(3)]
    r1 = max(sum((q[a] - ctr[a]) ** 2 for a in range(3)) for q in V) ** 0.5
    r2 = max(sum(q[a] ** 2 for a in range(3)) for q in V) ** 0.5
    struct.pack_into('<3f', out, node + 0x08, *lo)
    struct.pack_into('<3f', out, node + 0x14, *hi)
    struct.pack_into('<3f', out, node + 0x20, *ctr)
    struct.pack_into('<3f', out, node + 0x2C, *ctr)
    struct.pack_into('<2f', out, node + 0x38, r1, r2)
    struct.pack_into('<I', out, node + 0x4C, len(V))
    struct.pack_into('<I', out, node + 0x50, len(P))
    struct.pack_into('<I', out, node + 0x68, va(ov))
    struct.pack_into('<I', out, node + 0x6C, va(op))
    if fan == 4:
        struct.pack_into('<I', out, node + 0x7C, val(root))
    struct.pack_into('<I', out, pay + 0x08, len(P))
    struct.pack_into('<I', out, pay + 0x0C, len(V))
    struct.pack_into('<I', out, pay + 0x10, len(idx))
    struct.pack_into('<I', out, pay + 0x14, va(oi))
    struct.pack_into('<f', out, pay + 0x18, cell)
    struct.pack_into('<3f', out, pay + 0x1C, *clo)
    struct.pack_into('<3f', out, pay + 0x28, *chi)

emit(NODE10, PAY10, ROOT10, V10, P10, t10, 4, False)
emit(NODE9, PAY9, ROOT9, V9, P9, t9, 8, True)
struct.pack_into('<2I', out, 0, len(V9), len(P9))
while len(out) % 16: out.append(0xCD)
hdr = bytearray(tk[:0x80]); struct.pack_into('<I', hdr, 0x0C, len(out))
data = bytes(hdr) + bytes(out)
open(sys.argv[1] if len(sys.argv) > 1 else 'proto_bnd.pck', 'wb').write(data)
print('body %d bytes (0x%X)' % (len(out), len(out)))
