"""
mc2_scene_blob.py - a whole patch of an MC2 PS2 city as one VIF1 DMA chain

Same idea as mc2_vu_blob.py, for many pieces at once: every PCP0 of a time of day
is the draw of one model, and an instance carries its own 3x4 matrix, which goes
into VU data memory 12..15 (rows of the rotation, then the translation - the
microcode multiplies it into the view-projection matrix at MSCAL 2).

    python mc2_scene_blob.py <city.rsc> --cpv midnight_clear --pieces 300 --bin OUT.bin

Pieces are the N nearest to the densest 300 m cell of the map, so a run always shows
one recognisable neighbourhood. A piece is dropped, not approximated, when its
textures no longer fit in VRAM.
"""

import argparse
import collections
import math
import struct

import mc2_rsc as R
import mc2_tex_gs as TG
import mc2_vu_blob as B

TEX_FIRST, TEX_LAST = 0x1500, 0x3800     # texture area in 256-byte blocks
CLUT_FIRST, CLUT_LAST = 0x3800, 0x4000
FLUSH = 0x11000000
NO_FAR = False                            # debugging: leave the far LODs out
GROUP = False                             # view mode: order pieces so neighbours share a texture (see mc2_view.cpp: off by
                                           # default - measured no draw-count change on PCSX2 and a real visual regression)


def direct(n):
    return 0x50000000 | (n & 0xFFFF)


def words(ws):
    return struct.pack('<%dI' % len(ws), *ws)


def texture_run(tags):
    """(first, last) texture handle a chain selects, in the order it selects them."""
    seq = []
    for tv, pl, did, addr in tags:
        if did == 'call' and (not seq or seq[-1] != addr):
            seq.append(addr)
    return (seq[0], seq[-1]) if seq else (None, None)


def group_by_texture(chosen):
    """Greedy chain: after a piece that ends on texture T, prefer one that starts on T, so
    the runtime can drop that piece's first texture switch. Pieces with no texture run go
    last. Nothing else about the pieces changes."""
    buckets = collections.defaultdict(list)
    tail = []
    for item in chosen:
        first, last = texture_run(item[1])
        (buckets[first] if first is not None else tail).append((item, last))
    out, cur = [], None
    while any(buckets.values()):
        if cur is not None and buckets.get(cur):
            item, last = buckets[cur].pop()
        else:
            key = max((k for k in buckets if buckets[k]), key=lambda k: len(buckets[k]))
            item, last = buckets[key].pop()
        out.append(item)
        cur = last
    return out + [i for i, _ in tail]


def pmd_tags(rsc, blocks):
    """PMD0 blocks drawn straight from the geometry (no colour layer): same tuples as
    B.pcp_tags. `blocks` are entry indices in the city container."""
    out = []
    for index in blocks:
        off, fcc, size = rsc.entries[index]
        tags, used = R.dma_chain(rsc.data[off:off + size])
        for t in tags:
            out.append((t.tagvif, t.payload, t.id, t.addr))
    return out


def referenced_pmds(rsc, cpvs):
    """PMD0 entries some main-pass PCP0 draws. Whatever is not in here has no colour
    layer, and is drawn from the geometry alone."""
    pmap = R.payload_map(rsc)
    seen = set()
    main = {(h & 0x3FFF) - 1 for h, (f, n) in cpvs.names.items() if f == 'PCP0' and n.endswith('_main')}
    for i, e in enumerate(cpvs.entries):
        if e[1] != 'PCP0' or i not in main:          # the 'refl' passes reuse the geometry
            continue
        for (p, did, qwc, addr, s) in R.pcp_chain(cpvs, i)[0]:
            if did != 'ref':
                continue
            src = ((addr >> 20) & 0xFFF) - 1
            for cand in (src, src + 4096, src + 8192):
                if (addr & 0xFFFF0, qwc) in pmap.get(cand, ()):
                    seen.add(cand)
                    break
    return seen


def pieces_near_densest(rsc, cpvs, count, radius, lod=0, centre=None):
    comps, insts = R.read_hoods(R.city_folder(rsc.path))
    place = {(i.type.lower(), i.extension): i for i in insts}
    cbyname = {c.name.lower(): c for c in comps}
    cells = collections.Counter((int(i.pos[0] // 300), int(i.pos[2] // 300)) for i in insts)
    (cx, cz), _ = cells.most_common(1)[0]
    centre = centre or (cx * 300 + 150.0, cz * 300 + 150.0)
    out = []
    # the lowest LOD each model has: some (ground slabs, roads) exist only as LOD 1
    best = {}
    lods = collections.defaultdict(dict)
    for index, kind, key, level, which in R.cpv_entries(cpvs):
        if which == 'main':
            lods[(kind, key)][level] = index
            if (kind, key) not in best or level < best[(kind, key)][0]:
                best[(kind, key)] = (level, index)
    for (kind, key), (level, index) in best.items():
        far_index = lods[(kind, key)].get(1) if level == 0 else None
        if kind == 'inst':
            inst = place.get(key)
            if inst is None:
                continue
            lo, hi, rot, pos = inst.emin, inst.emax, inst.rot, inst.pos
        else:
            c = cbyname.get(key)
            if c is None:
                continue
            lo, hi, rot, pos = c.emin, c.emax, None, None
        # distance from the centre to the piece's box on the ground plane: a road or a
        # slab of ground that contains the centre counts as 0, however large it is
        dx = max(lo[0] - centre[0], 0.0, centre[0] - hi[0])
        dz = max(lo[2] - centre[1], 0.0, centre[1] - hi[2])
        out.append(dict(index=index, far_index=far_index, kind=kind, rot=rot, pos=pos, lo=lo, hi=hi,
                        dist=math.hypot(dx, dz)))
    # Blocks with no colour layer - roads, junctions, but also the 'ground' slot of models
    # that DO have a main PCP0 - are drawn from the PMD0 alone, flat colour, per placement.
    refd = referenced_pmds(rsc, cpvs)
    by = R.models_by_name(rsc)

    def bare_blocks(model):
        got = []
        for h in R.model_blocks(model, None):
            r = rsc.resolve(h)
            if r is not None and r[0] is rsc and rsc.entries[r[1]][1] == 'PMD0' and r[1] not in refd:
                got.append(r[1])
        return got

    def far_blocks(model, near):
        # only when there is a real LOD 0 to be far from
        if not [h for h in R.model_blocks(model, 0) if h]:
            return []
        got = []
        for h in R.model_blocks(model, 1):
            r = rsc.resolve(h) if h else None
            if (r is not None and r[0] is rsc and rsc.entries[r[1]][1] == 'PMD0'
                    and r[1] not in refd and r[1] not in near):
                got.append(r[1])
        return got

    extra = []
    for c in comps:
        m = by.get(c.name.lower())
        if m is not None and bare_blocks(m):
            b = bare_blocks(m)
            extra.append((m, b, far_blocks(m, b), None, None, c.emin, c.emax))
    for i in insts:
        m = by.get(i.type.lower())
        if m is not None and bare_blocks(m):
            b = bare_blocks(m)
            extra.append((m, b, far_blocks(m, b), i.rot, i.pos, i.emin, i.emax))
    for model, blocks, far, rot, pos, lo, hi in extra:
        dx = max(lo[0] - centre[0], 0.0, centre[0] - hi[0])
        dz = max(lo[2] - centre[1], 0.0, centre[1] - hi[2])
        out.append(dict(index=None, kind='pmd', model=model, blocks=blocks, far_blocks=far, rot=rot, pos=pos,
                        lo=lo, hi=hi, dist=math.hypot(dx, dz)))
    out = [p for p in out if p['dist'] <= radius]
    out.sort(key=lambda p: p['dist'])
    return out[:count * 2], centre          # spare ones: some get dropped


def build(rsc_path, cpv, count, radius=350.0, half_w=256.0, half_h=224.0, prefill=False, only=None, kind=None, centre=None, view=False, textured=True, depth_always=False):
    rsc = R.Rsc(rsc_path)
    cpvs = R.open_cpvs(rsc, cpv)
    pal = R.palette(cpvs)
    cand, centre = pieces_near_densest(rsc, cpvs, count, radius, centre=centre)
    if kind:
        cand = [p for p in cand if (p['kind'] == 'pmd') == (kind == 'pmd')]
    if only:
        # debugging: just the pieces whose colour-layer name contains `only`, framed on their own box
        keep = []
        for p in cand:
            nm = p['model'].name if p['kind'] == 'pmd' else next(
                (n for h, (f, n) in cpvs.names.items() if f == 'PCP0' and (h & 0x3FFF) - 1 == p['index']), '')
            if only.lower() in nm.lower():
                keep.append(p)
        cand = keep

    tex_next, clut_next = TEX_FIRST, CLUT_FIRST
    texs = {}                                # handle -> (Texture, tbp, cbp)
    # handle 0 = no texture: a white texture makes MODULATE return the vertex colour
    if textured:
        texs[0] = (TG.white(), tex_next, clut_next)
        tex_next += 4
        clut_next += 4
    chosen = []
    for p in cand:
        if len(chosen) >= count:
            break
        try:
            tags = pmd_tags(rsc, p['blocks']) if p['kind'] == 'pmd' else B.pcp_tags(rsc, cpvs, p['index'])
        except ValueError:
            continue
        if not tags:
            continue
        far = None
        if view and not NO_FAR:
            try:
                if p['kind'] == 'pmd':
                    far = pmd_tags(rsc, p['far_blocks']) if p.get('far_blocks') else None
                elif p.get('far_index') is not None:
                    far = B.pcp_tags(rsc, cpvs, p['far_index'])
            except ValueError:
                far = None
        if not textured:
            chosen.append((p, tags, far))
            continue
        # textures: try near + far; if the far LOD's do not fit, keep only the near one
        for attempt in (((tags, far), (tags, None)) if far else ((tags, None),)):
            need = []
            for tl in attempt:
                for tv, pl, did, addr in (tl or []):
                    if did == 'call' and addr and addr not in texs and addr not in need:
                        need.append(addr)
            loaded = []
            tn, cn, ok = tex_next, clut_next, True
            for h in need:
                try:
                    t = TG.load(rsc, h)
                except Exception:
                    ok = False
                    break
                size = (t.w * t.h * t.bpp // 8 + 1023) // 1024 * 4     # blocks of 256 B, in steps of 4
                if tn + size > TEX_LAST or cn + 4 > CLUT_LAST:
                    ok = False
                    break
                loaded.append((h, t, tn, cn))
                tn += size
                cn += 4
            if ok:
                break
        if not ok:
            continue
        for h, t, tbp, cbp in loaded:
            texs[h] = (t, tbp, cbp)
        tex_next, clut_next = tn, cn
        chosen.append((p, tags, attempt[1]))
    print('%d pieces chosen of %d candidates, %d textures, %d KB of texture VRAM'
          % (len(chosen), len(cand), len(texs), (tex_next - TEX_FIRST) * 256 // 1024))

    # frame the square the pieces were picked from, not the pieces themselves: a long
    # coastline or a road across the map would otherwise shrink the whole view
    if only:
        pts = [(x, y, z) for p in [c for c, _, _f in chosen] for x in (p['lo'][0], p['hi'][0])
               for y in (p['lo'][1], p['hi'][1]) for z in (p['lo'][2], p['hi'][2])]
    else:
        pts = [(centre[0] + sx * radius, y, centre[1] + sz * radius)
               for sx in (-1, 1) for sz in (-1, 1) for y in (0.0, 60.0)]
    m, info = B.camera(pts, half_w / half_h, direction=(0.0, 0.8, 0.6), fov_deg=40.0)

    stream = []

    def align():
        while len(stream) % 4:
            stream.append(B.VIF_NOP)

    stream += [B.VIF_NOP, B.VIF_FLUSHE, B.VIF_STCYCL, B.VIF_STMOD]
    main = open(B.MAIN_BIN, 'rb').read()
    tail = open(B.TAIL_BIN, 'rb').read()[0x100:]
    for blob, base in ((main, 0), (tail, B.TAIL_VU // 8)):
        n_instr = len(blob) // 8
        ws = struct.unpack('<%dI' % (len(blob) // 4), blob)
        done = 0
        while done < n_instr:
            n = min(255, n_instr - done)
            stream.append(B.vif_mpg(n, base + done))
            stream += ws[done * 2:(done + n) * 2]
            align()
            done += n
    block = B.data_block(m, half_w, half_h, (0.55, 0.55, 0.6, 1.0), tme=textured)   # data[42]: flat colour of the PMD0-only models
    # Match the live MC3 VU1 depth mapping. The older MC2-specific -2000/2048
    # range sorted MC2 surfaces internally but biased them against MC3 traffic.
    native_z = 2047.96875
    block[8] = [B.fbits(half_w), B.fbits(-half_h), B.fbits(-native_z), 0]
    block[9] = [B.fbits(2048.0), B.fbits(2048.0), B.fbits(native_z), 0]
    stream.append(B.vif_unpack_v4_32(64, 0))
    for row in block:
        stream += row
    align()
    if prefill:
        done = 100
        while done < 768:
            n = min(255, 768 - done)
            stream.append(B.vif_unpack_v4_32(n, done))
            stream += [0x45000000, 0x45000000, 0x45000000, 0] * n
            align()
            done += n
    stream.append(0x6E000000 | 768)                  # UNPACK V4-8u x256 -> the palette
    for c in pal:
        stream.append(c[0] | c[1] << 8 | c[2] << 16 | c[3] << 24)
    align()

    chain = [len(stream) // 4 | (1 << 28), 0, 0, 0] + stream
    pieces = []

    def cnt(vif, payload):
        pieces.append((struct.pack('<II', *vif), payload))

    first = TG.gif_tag(3, 0)                      # one-time GS state: TEX1, CLAMP, TEST(GEQUAL)
    TG.ad(first, 0x61, TG.TEX1_1)   # LCM=1: LOD fixed at K=0, linear
    TG.ad(first, 0, TG.CLAMP_1)
    # Diagnostic mode: allow every pixel through the GS depth comparison to
    # distinguish depth rejection from geometry/VU clipping artifacts.
    TG.ad(first, 0x30000 if depth_always else 0x50000, 0x47)
    cnt((FLUSH, direct(len(first) // 4)), words(first))
    for h, (t, tbp, cbp) in texs.items():
        up = TG.upload_words(t, tbp, cbp)
        cnt((FLUSH, direct(len(up) // 4)), words(up))

    current = None
    ranges = []                           # per piece: (near segment, far segment or None)

    def emit(tags, rot, pos):
        nonlocal current
        if view:
            current = None                # a culled neighbour must not leave the texture state stale
        first_tag = len(pieces)
        first_sw = None                   # index of the segment's first texture switch tag
        tex_first = tex_last = None
        mat = [B.fbits(rot[0][0]), B.fbits(rot[0][1]), B.fbits(rot[0][2]), 0,
               B.fbits(rot[1][0]), B.fbits(rot[1][1]), B.fbits(rot[1][2]), 0,
               B.fbits(rot[2][0]), B.fbits(rot[2][1]), B.fbits(rot[2][2]), 0,
               B.fbits(pos[0]), B.fbits(pos[1]), B.fbits(pos[2]), 0]
        cnt((FLUSH, B.vif_unpack_v4_32(4, 12)), words(mat))
        for tv, pl, did, addr in tags:
            cnt(struct.unpack('<II', tv), pl)
            if textured and did == 'call' and addr != current: # handle 0 = white (no texture)
                current = addr
                t, tbp, cbp = texs[addr]
                sw = TG.switch_words(t, tbp, cbp)
                if first_sw is None:
                    first_sw, tex_first = len(pieces), addr
                tex_last = addr
                cnt((FLUSH, direct(len(sw) // 4)), words(sw))
        return (first_tag, len(pieces) - 1, first_sw, tex_first, tex_last)

    if view and GROUP:
        chosen = group_by_texture(chosen)
    for p, tags, far in chosen:
        rot, pos = p['rot'], p['pos']
        if rot is None:
            rot, pos = [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0]
        near_seg = emit(tags, rot, pos)
        far_seg = emit(far, rot, pos) if far else None
        ranges.append((near_seg, far_seg))
    n_static = 1 + len(texs)              # the state packet and the texture uploads
    pieces_off = 0
    offs = []                             # word offset of every tag from the start of the chain
    for n, (tv, pl) in enumerate(pieces):
        if n == n_static:
            pieces_off = len(chain)       # words from the start of the chain to the first piece
        offs.append(len(chain))
        last = n == len(pieces) - 1 and not view
        lo = len(pl) // 16 | ((7 if last else 1) << 28)
        w2, w3 = struct.unpack('<II', tv)
        chain += [lo, 0, w2, w3]
        chain += list(struct.unpack('<%dI' % (len(pl) // 4), pl))
    if view:
        # An explicit end tag. The viewer strings the VISIBLE pieces together by rewriting
        # the last tag of each one into a `next` that points at the following piece, and
        # the last visible one at this tag.
        info['end_off'] = len(chain)
        chain += [7 << 28, 0, 0, 0]
        info['static_last'] = offs[n_static - 1]
        info['table'] = []
        for (p, _, _f), (near_seg, far_seg) in zip(chosen, ranges):
            fs = (offs[far_seg[0]], offs[far_seg[1]]) if far_seg else (0, 0)
            # the first switch can only be skipped when it is not also the segment's last tag
            # (the culler rewrites the last tag of a piece to chain to the next one)
            first_sw = near_seg[2]
            skippable = first_sw is not None and first_sw != near_seg[1]
            info['table'].append((offs[near_seg[0]], offs[near_seg[1]], p['lo'], p['hi'], fs[0], fs[1],
                                  offs[first_sw] if skippable else 0,
                                  near_seg[3] if skippable else 0, near_seg[4] if skippable else 0,
                                  1 if skippable else 0))
        info['far_count'] = sum(1 for r in ranges if r[1])
    info['pieces_off'] = pieces_off
    # camera basis for the viewer: the same look-at as B.camera()
    zax = B.norm([info['eye'][k] - info['centre'][k] for k in range(3)])
    xax = B.norm(B.cross([0, 1, 0], zax))
    yax = B.cross(zax, xax)
    info['basis'] = (xax, yax, zax)
    return chain, info, len(chosen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rsc')
    ap.add_argument('--cpv', default='midnight_clear')
    ap.add_argument('--pieces', type=int, default=300)
    ap.add_argument('--radius', type=float, default=350.0)
    ap.add_argument('--only', default=None, help='debug: only pieces whose name contains this')
    ap.add_argument('--kind', default=None, choices=('pmd', 'pcp'), help='debug: only PMD0-only or only colour-layer pieces')
    ap.add_argument('--centre', type=float, nargs=2, default=None, metavar=('X', 'Z'), help='world x z of the view centre (default: densest cell)')
    ap.add_argument('--prefill', action='store_true')
    ap.add_argument('--bin', required=True)
    ap.add_argument('--view', action='store_true', help='write the viewer format: a 128-byte camera header before the chain')
    ap.add_argument('--flat', action='store_true', help='debug: disable texture mapping and uploads while keeping CPVS vertex colours')
    ap.add_argument('--depth-always', action='store_true', help='debug: make the GS depth test always pass to isolate depth rejection')
    ap.add_argument('--no-far', action='store_true', help='debug: no far LOD segments')
    ap.add_argument('--group', action='store_true', help='order pieces by texture (measured: no draw-count change on PCSX2, and a visual regression from draw-order-dependent overlapping decals - off by default, see FORKS.md)')
    a = ap.parse_args()
    global NO_FAR, GROUP
    NO_FAR = a.no_far
    GROUP = a.group
    chain, info, n = build(a.rsc, a.cpv, a.pieces, a.radius, prefill=a.prefill, only=a.only, kind=a.kind, centre=tuple(a.centre) if a.centre else None, view=a.view, textured=not a.flat, depth_always=a.depth_always)
    with open(a.bin, 'wb') as f:
        if a.view:
            r, u, b = info['basis']
            fl = lambda x: struct.unpack('<I', struct.pack('<f', x))[0]
            hdr = [0x3253434D, info['pieces_off'], len(chain), info['static_last']]
            hdr += [fl(v) for v in info['eye']] + [fl(v) for v in r + u + b]
            hdr += [fl(1.0 / math.tan(math.radians(40.0) / 2)), fl(6.0), fl(4000.0), fl(6.0), fl(0.05)]
            hdr += [0] * (21 - len(hdr))
            table_off = 128 + len(chain) * 4
            hdr += [table_off, len(info['table']), info['end_off']]
            hdr += [0] * (32 - len(hdr))
            f.write(struct.pack('<32I', *hdr))
        f.write(struct.pack('<%dI' % len(chain), *chain))
        if a.view:
            for start, last, lo, hi, fstart, flast, sw_off, tex0, tex1, skippable in info['table']:
                # 16 words: start, last, box(6), far start, far last, 2 spare, first switch tag,
                # first texture, last texture, flags (1 = the first switch may be skipped)
                f.write(struct.pack('<2I6f2I2I4I', start, last, *lo, *hi, fstart, flast, 0, 0,
                                    sw_off, tex0, tex1, skippable))
    if a.view:
        print('%d of %d pieces also carry a far LOD' % (info['far_count'], len(info['table'])))
    print('chain: %d quadwords (%d KB), camera centre %s radius %.0f dist %.0f'
          % (len(chain) // 4, len(chain) * 4 // 1024, [round(v) for v in info['centre']],
             info['radius'], info['dist']))


if __name__ == '__main__':
    main()
