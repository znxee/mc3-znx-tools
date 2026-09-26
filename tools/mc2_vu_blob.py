"""
mc2_vu_blob.py - turn one MC2 PS2 PMD0 into a DMA chain the MC3 VU1 can draw

Builds the whole VIF1 chain for the `mc2_draw_probe` mod:
  * MPG upload of MC3's own `rv1_code_main` (VU 0x0000) and the body of `rv1_code`
    (VU 0x1F40, where rv1_code_main jumps back to for XGKICK), both dumped from the ELF
  * one UNPACK of the data-memory block the microcode expects before the first draw:
    the view-projection matrix, the viewport, an identity model matrix, the GIFtag
    template and the flat colour
  * the PMD0's own tags, with `call`/`ret` turned into `cnt`/`end` (the address of
    a `call` is a texture handle, not memory)

    python mc2_vu_blob.py <file.rsc> <pmd entry> <out.h> [--mesh-out X.png]
"""

import argparse
import math
import os
import struct
import sys

import mc2_rsc as R
import mc2_tex_gs as TG

VU_DIR = os.path.join(os.environ.get('MC3_WORK', '.'), 'output', 'vu_mc3')
MAIN_BIN = os.path.join(VU_DIR, 'vu_26387.bin')     # rv1_code_main
TAIL_BIN = os.path.join(VU_DIR, 'vu_12963.bin')     # rv1_code (chunk 0 = 256 empty bytes)
TAIL_VU = 0x1F40                                    # where the tail chunks live in VU memory

VIF_NOP, VIF_FLUSHE, VIF_STCYCL, VIF_STMOD = 0, 0x10000000, 0x01000101, 0x05000000


def vif_mpg(num, addr):
    return 0x4A000000 | ((num & 0xFF) << 16) | addr


def vif_unpack_v4_32(num, addr):
    return 0x6C000000 | (num << 16) | addr


def pcp_tags(rsc, cpvs, index):
    """A PCP0 as [(tagvif bytes, payload bytes)], with every `ref` resolved to the
    geometry it points at inside the city container (same walk as R.pcp_stream)."""
    pmap = R.payload_map(rsc)
    out = []
    for (p, did, qwc, addr, s) in R.pcp_chain(cpvs, index)[0]:
        if did == 'ref':
            src = ((addr >> 20) & 0xFFF) - 1
            want = addr & 0xFFFF0
            for cand in (src, src + 4096, src + 8192):
                if (want, qwc) in pmap.get(cand, ()):
                    o = rsc.entries[cand][0]
                    out.append((s[:8], rsc.data[o + want:o + want + qwc * 16], did, addr))
                    break
            else:
                raise ValueError('PCP0 %d: unresolved ref %#x' % (index, addr))
        else:
            out.append((s[:8], s[8:], did, addr))
    return out


def find_pcp(cpvs, pmd_index):
    """The first PCP0 whose refs read from PMD0 `pmd_index`."""
    for i, e in enumerate(cpvs.entries):
        if e[1] != 'PCP0':
            continue
        for (p, did, qwc, addr, s) in R.pcp_chain(cpvs, i)[0]:
            if did == 'ref' and ((addr >> 20) & 0xFFF) - 1 == pmd_index:
                return i
    raise ValueError('no PCP0 draws PMD0 %d' % pmd_index)


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def camera(points, aspect, direction=(0.35, 0.75, 0.55), fov_deg=42.0):
    """Row-vector view-projection matrix (clip = [x y z 1] * M) that frames `points`."""
    lo = [min(p[c] for p in points) for c in range(3)]
    hi = [max(p[c] for p in points) for c in range(3)]
    centre = [(lo[c] + hi[c]) / 2 for c in range(3)]
    radius = math.sqrt(sum((hi[c] - lo[c]) ** 2 for c in range(3))) / 2
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    dist = radius * 1.15 / math.sin(math.radians(fov_deg) / 2)
    d = norm(list(direction))
    eye = [centre[c] + d[c] * dist for c in range(3)]
    zax = norm([eye[c] - centre[c] for c in range(3)])
    xax = norm(cross([0, 1, 0], zax))
    yax = cross(zax, xax)
    view = [[xax[0], yax[0], zax[0], 0], [xax[1], yax[1], zax[1], 0], [xax[2], yax[2], zax[2], 0],
            [-dot(xax, eye), -dot(yax, eye), -dot(zax, eye), 1]]
    near, far = dist - radius * 1.2, dist + radius * 1.2
    proj = [[f / aspect, 0, 0, 0], [0, f, 0, 0],
            [0, 0, (far + near) / (near - far), -1], [0, 0, 2 * far * near / (near - far), 0]]
    return matmul(view, proj), dict(centre=centre, radius=radius, dist=dist, eye=eye)


def fbits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


GIF_PRIM_SHIFT = 15          # PRIM field in the GIFtag's second word (bits 47..57)
GIF_PRIM_TYPE = 7 << GIF_PRIM_SHIFT
GIF_PRIM_FAN = 5


def clip_template(tag):
    """The data[35] GIFtag with its primitive type changed to triangle fan (IIP/TME kept)."""
    t = list(tag)
    t[1] = (t[1] & ~GIF_PRIM_TYPE) | (GIF_PRIM_FAN << GIF_PRIM_SHIFT)
    return t


def data_block(m, half_w, half_h, colour, tme=False):
    """64 quadwords of VU1 data memory, addresses 0..63."""
    q = [[0, 0, 0, 0] for _ in range(64)]
    for i in range(4):
        q[4 + i] = [fbits(v) for v in m[i]]
    q[8] = [fbits(half_w), fbits(-half_h), 0, 0]                 # viewport scale
    q[9] = [fbits(2048.0), fbits(2048.0), fbits(1000.0), 0]      # viewport offset (GS units)
    q[12] = [fbits(1.0), 0, 0, 0]                                # identity model matrix, 4x3
    q[13] = [0, fbits(1.0), 0, 0]
    q[14] = [0, 0, fbits(1.0), 0]
    q[15] = [0, 0, 0, fbits(1.0)]
    # GIFtag template: NLOOP is patched by the microcode; PRE=1, PRIM = tristrip|IIP,
    # PACKED, NREG=3, REGS = ST, RGBAQ, XYZ2
    # the low bits of the tag's second word are padding to the GS; the clipper (rv1_code +0x608)
    # reads them as the primitive kind: 4 = triangle strip
    # EOP is set here too (word x = 0x8000, NLOOP 0): the trivial-reject path of the microcode
    # XGKICKs this very quadword as an EMPTY packet, and without EOP the GIF keeps reading
    # past the end of VU memory ("GS packet size exceeded VU memory size") and writes whatever
    # it finds into the GS registers. The normal path overwrites the whole x word anyway.
    q[35] = [0x8000, 0x4000 | ((0xC | (0x10 if tme else 0)) << 15) | (3 << 28) | 4, 0x512, 0]
    # the clipper (rv1_code +0x608) builds its output packet on a copy of one of these
    # GIFtag templates, picked by bit 0x4000 of data[34].x. It emits each clipped polygon
    # as a FAN (v0 v1 v2 .. vn), so the template must say fan, not the strip of data[35]:
    # native MC3 VU1 memory (freeroam savestate) has PRIM 5 in 36/37 against 4 in 35.
    # With a strip the second triangle of every clipped quad became (v1 v2 v3) instead of
    # (v0 v2 v3) - the dark triangular cuts along the screen edges in the MC2 scene.
    q[36] = clip_template(q[35])
    q[37] = clip_template(q[35])
    q[42] = [fbits(c) for c in colour]                           # flat colour, 0..1 (x128 in the VU)
    return q


def build(rsc_path, index, half_w, half_h, colour, keep=None, diag=False, cpv=None, cpv_index=None, textured=True):
    rsc = R.Rsc(rsc_path)
    off, fcc, size = rsc.entries[index]
    buf = rsc.data[off:off + size]
    tags, used = R.dma_chain(buf)
    assert used == size, 'chain did not consume the whole block'
    drawn, bias, params, _ = R.read_pmd(rsc, index)
    pts = [p for b in drawn for p in b.pos]
    m, info = camera(pts, half_w / half_h)

    stream = []

    def align():
        while len(stream) % 4:
            stream.append(VIF_NOP)

    stream += [VIF_NOP, VIF_FLUSHE, VIF_STCYCL, VIF_STMOD]
    main = open(MAIN_BIN, 'rb').read()
    tail = open(TAIL_BIN, 'rb').read()[0x100:]
    for blob, base in ((main, 0), (tail, TAIL_VU // 8)):
        n_instr = len(blob) // 8
        words = struct.unpack('<%dI' % (len(blob) // 4), blob)
        done = 0
        while done < n_instr:
            n = min(255, n_instr - done)
            stream.append(vif_mpg(n, base + done))
            stream += words[done * 2:(done + n) * 2]
            align()
            done += n
    if diag:
        # hijack the tail's entry (instruction 1022, 0x1FF0): jump to a program at 2016
        # that stores VI01..VI15 into data memory 900.. and ends
        stream.append(vif_mpg(2, 1022))
        stream += [0x8000033C, 0x400003E1 & 0xFFFFFFFF] if False else [0x400003E1, 0x000002FF, 0x8000033C, 0x000002FF]
        align()
        prog = []
        for k in range(1, 16):
            prog += [0x0B000000 | (k << 16) | (900 + k), 0x000002FF]
        prog += [0x8000033C, 0x400002FF, 0x8000033C, 0x000002FF, 0x8000033C, 0x000002FF]
        stream.append(vif_mpg(len(prog) // 2, 2016))
        stream += prog
        align()
    block = data_block(m, half_w, half_h, colour, tme=bool(cpv))
    stream.append(vif_unpack_v4_32(64, 0))
    for row in block:
        stream += row
    align()
    # The microcode's vertex loop is software-pipelined and reads one vertex PAST the
    # end of each batch, clip-testing it and folding the result into the batch's "needs
    # clipping" flag. In the game that slot holds leftovers of an earlier draw; here it
    # would be untouched (zero) memory, which puts the phantom vertex far outside the
    # frustum and sends every batch into the clipper. So fill the batch area with a
    # position that lands on the middle of the view: bias + centre.
    fill = [fbits(bias[c] + info['centre'][c]) for c in range(3)] + [0]
    done = 100
    while done < 768:
        n = min(255, 768 - done)              # the UNPACK count is 8 bits
        stream.append(vif_unpack_v4_32(n, done))
        for _ in range(n):
            stream += fill
        align()
        done += n

    if cpv:
        # the palette the microcode gathers from (mscal 0xA reads VU address 768 + index):
        # 256 x RGBA as four 32-bit words each, unpacked from the bytes of CPP0
        cp = R.open_cpvs(rsc, cpv)
        pal = R.palette(cp)
        assert len(pal) == 256, len(pal)
        stream.append(0x6E000000 | (0 << 16) | 768)        # UNPACK V4-8u, num 0 = 256
        for i in range(0, 256, 1):
            stream.append(pal[i][0] | (pal[i][1] << 8) | (pal[i][2] << 16) | (pal[i][3] << 24))
        align()
    chain = [len(stream) // 4 | (1 << 28), 0, 0, 0]     # cnt, then the VIF stream
    chain += stream
    current_tex = [None]
    if cpv:
        # the PCP0 is the real draw: it re-sends the geometry by reference and adds the
        # per-vertex palette indices (S-8u at B+3), then kicks with mscal 0xA
        pcp = R.open_cpvs(rsc, cpv)
        cpv_index = cpv_index if cpv_index is not None else find_pcp(pcp, index)
        raw = pcp_tags(rsc, pcp, cpv_index)
        pieces = []
        if textured:
            handles = []
            for tv, pl, did, addr in raw:
                if did == 'call' and addr not in handles:
                    handles.append(addr)
            texs = {}
            first = TG.gif_tag(3, 0)          # one-time GS state: TEX1 (linear), CLAMP (repeat), TEST
            TG.ad(first, 0x64, TG.TEX1_1)
            TG.ad(first, 0, TG.CLAMP_1)
            TG.ad(first, 0x30000, 0x47)
            pieces.append(dict(id='cnt', qwc=len(first) // 4, vif=struct.pack('<II', 0x11000000, 0x50000000 | (len(first) // 4)),
                               payload=struct.pack('<%dI' % len(first), *first)))
            for k, hd in enumerate(handles):
                t = TG.load(rsc, hd)
                tbp, cbp = 0x1000 + k * 0x100, 0x2000 + k * 4
                texs[hd] = (t, tbp, cbp)
                up = TG.upload_words(t, tbp, cbp)
                pieces.append(dict(id='cnt', qwc=len(up) // 4, vif=struct.pack('<II', 0x11000000, 0x50000000 | (len(up) // 4)),
                                   payload=struct.pack('<%dI' % len(up), *up)))
                print('texture %#x %-24s %dx%d %dbpp -> TBP %#x CBP %#x' % (hd, t.name, t.w, t.h, t.bpp, tbp, cbp))
        for tv, pl, did, addr in raw:
            pieces.append(dict(id='cnt', qwc=len(pl) // 16, vif=tv, payload=pl))
            if textured and did == 'call' and addr != current_tex[0]:
                current_tex[0] = addr
                t, tbp, cbp = texs[addr]
                sw = TG.switch_words(t, tbp, cbp)
                pieces.append(dict(id='cnt', qwc=len(sw) // 4, vif=struct.pack('<II', 0x11000000, 0x50000000 | (len(sw) // 4)),
                                   payload=struct.pack('<%dI' % len(sw), *sw)))
        tags = pieces
    for n, t in enumerate(tags):
        if isinstance(t, dict):
            t = type('T', (), dict(t))()
            t.id = 'ret' if n == len(tags) - 1 else 'cnt'
            t.tagvif = t.vif
        last = t.id == 'ret' or (keep is not None and n == keep - 1)
        lo = t.qwc | ((7 if last else 1) << 28)
        w2, w3 = struct.unpack('<II', t.tagvif)
        chain += [lo, 0, w2, w3]
        chain += list(struct.unpack('<%dI' % (len(t.payload) // 4), t.payload))
        if last:
            break
    return chain, m, info, pts, bias


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rsc')
    ap.add_argument('index', type=int)
    ap.add_argument('out')
    ap.add_argument('--tags', type=int, default=None, help='stop after this many tags (debugging)')
    ap.add_argument('--diag', action='store_true', help='hijack the tail entry to dump VI01..15')
    ap.add_argument('--cpv', default=None, help='draw through the PCP0 of this time of day, e.g. midnight_clear')
    ap.add_argument('--cpv-index', type=int, default=None, help='PCP0 entry index')
    ap.add_argument('--bin', default=None, help='also write the raw chain here (the mod reads it from host0:)')
    ap.add_argument('--half-w', type=float, default=256.0)
    ap.add_argument('--half-h', type=float, default=224.0)
    a = ap.parse_args()
    chain, m, info, pts, bias = build(a.rsc, a.index, a.half_w, a.half_h, (0.95, 0.65, 0.25, 1.0), a.tags, a.diag, a.cpv, a.cpv_index)
    assert len(chain) % 4 == 0

    # host-side sanity check: every vertex should land inside the viewport
    inside = 0
    for p in pts:
        c = [sum(([p[0], p[1], p[2], 1.0][k]) * m[k][j] for k in range(4)) for j in range(4)]
        if c[3] > 0 and all(abs(c[j]) <= c[3] for j in range(3)):
            inside += 1
    print('points %d, inside frustum %d, centre %s radius %.1f dist %.1f'
          % (len(pts), inside, [round(v, 1) for v in info['centre']], info['radius'], info['dist']))
    print('chain: %d quadwords (%d bytes)' % (len(chain) // 4, len(chain) * 4))

    if a.bin:
        with open(a.bin, 'wb') as f:
            f.write(struct.pack('<%dI' % len(chain), *chain))
    with open(a.out, 'w') as f:
        f.write('// generated by mc2_vu_blob.py - do not edit\n')
        f.write('enum { CHAIN_QW = %d };\n' % (len(chain) // 4))
        f.write('static const mc3_u32 CHAIN[] __attribute__((aligned(16))) = {\n')
        for i in range(0, len(chain), 8):
            f.write('    ' + ', '.join('0x%08Xu' % w for w in chain[i:i + 8]) + ',\n')
        f.write('};\n')


if __name__ == '__main__':
    main()
