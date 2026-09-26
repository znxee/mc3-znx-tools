r"""Turn the `mesh_dump` heap dump into a mesh `.pck`.

THE WHOLE CHAIN, and every link was measured, not assumed:

  1. the mod creates a private heap (mcHeap::CreateMemTopHeap 0x004BD650) and
     asks the game to read a TEXT mesh into it;
  2. USE `.mesh`, NOT `.mod`. The `.mod` branch of rmcModel::Create builds the
     skeleton and stops: I measured packets with the pointer set and a size of
     ZERO, and the transitive closure of the root reached 390 bytes of a 148 KB
     heap. Through `.mesh` the same model reaches 46 KB in blocks that start
     with VIF (6002409E 3D800000 ...), because only that branch gets to
     rmcGeometry::Packet::Init;
  3. the mod dumps the block in hexadecimal and `mc3_heap_dump.py` rebuilds it;
  4. this script finds what the root reaches, COMPACTS and RELOCATES it.

WHY COMPACT. The game's allocator is a bump allocator: the parse buffer
(mshMesh) is allocated and freed inside the same heap and is NOT given back, so
the 169 KB dump has only 46 KB of live graph. A `.pck` is a memory image -
garbage inside it would be loaded along with it.

HOW THE RELOCATION IS MADE SAFE. Rewriting every word that falls in the heap
range is not enough: VIF data and floats would be caught too. Only a word that
points to the START of a reached payload, or to start+16, is rewritten - the
only two forms the engine uses, the second being the "count 16 bytes BEFORE the
array" idiom that shows up on all three levels (geometries, packets, cpv).

Header, checked on a retail `.pck`: [baseVA][kind][version=1][body bytes],
zeros up to 0x80, body from there on - so base VA <-> offset 0x80.

    python mc3_heap_to_pck.py output/heap_mesh.bin --base 01E51860 \
        --root 01E690D0 --out-path output/probe.mesh.pck
"""
import argparse
import bisect
import os
import struct

BASE_PCK = 0x06800000
MESH_KIND = 0x16
# A .pck keeps a class TOKEN at +0x00 of the root, not the vtable: the resource
# ctor swaps it for the vtable at load. The dump carries the runtime vtable
# (0x00626D98) because it was built by new, not relocated.
TOKEN_MESH = 0x007A0F98
# rmcTexturePS2 vtable, named by the C++ fork and confirmed on the 510 objects
# the game built for me: all with this word at +0x00.
VT_TEXTURE_PS2 = 0x00627280
BODY = 0x80


def heap_blocks(d):
    """List (header offset, flags, size) by walking the allocation list.

    16-byte header, read from the allocator itself (0x003B0130..): +0x00 link
    with 3 flag bits, +0x04 requested size, +0x0C owner. The step aligns to 16.
    """
    out, o = [], 0
    while o + 16 <= len(d):
        size_ = struct.unpack_from('<I', d, o + 4)[0]
        if size_ == 0 or o + 16 + size_ > len(d) + 16:
            break
        out.append((o, struct.unpack_from('<I', d, o)[0] & 7, size_))
        o += 16 + ((size_ + 15) & ~15)
    return out, o


def emit(d, BASE, root_va, out_path, kind=MESH_KIND, token=TOKEN_MESH, quiet=False):
    """A .pck from an already loaded dump. It exists for BATCH MODE: with 298
    models in a 3.2 MB dump, calling the script once per model would reread the
    file 298 times."""
    blocks, stop = heap_blocks(d)

    def owner_(pp):
        lo, hi = 0, len(blocks) - 1
        while lo <= hi:
            m = (lo + hi) // 2
            off, _, size_ = blocks[m]
            if pp < off + 16:
                hi = m - 1
            elif pp >= off + 16 + size_:
                lo = m + 1
            else:
                return m
        return None

    root = owner_(root_va - BASE)
    if root is None:
        return None
    seen, queue = {root}, [root]
    while queue:
        off, _, size_ = blocks[queue.pop()]
        for k in range(0, size_ & ~3, 4):
            v = struct.unpack_from('<I', d, off + 16 + k)[0]
            if BASE <= v < BASE + len(d):
                t = owner_(v - BASE)
                if t is not None and t not in seen:
                    seen.add(t); queue.append(t)
    live = sorted(seen)
    order = [root] + [b for b in live if b != root]
    new_map, body = {}, bytearray()
    for b in order:
        off, _, size_ = blocks[b]
        new_map[b] = BASE_PCK + len(body)
        body += d[off + 16:off + 16 + size_]
        body += bytes((-len(body)) % 16)
    # RELOCATION THAT KEEPS THE OFFSET INSIDE THE BLOCK. The old version only
    # knew payload start and start+16, and that was enough for meshes; for
    # TEXTURES it is not - the .ppf manifest says the pixels sit at +144 inside
    # the chunk, and 838 of the 965 datChunkRef pointed into the middle of a
    # block, did not match, and were left dangling. Now any pointer INTO a
    # reached block is translated keeping the displacement.
    # TWO RULES, and the difference between them has already cost me both ways.
    #
    # DEFAULT (strict): only applies when the target is the START of a reached
    # payload, or start+16 (the "count 16 bytes before the array" idiom). I tried
    # replacing this with an offset-preserving translation and the regression was
    # immediate: the [u16 qw][u16 nv] pairs of the packet list are read as a
    # 32-bit word, matched as a pointer and were REWRITTEN - 19 meshes with an
    # absurd qw (50935, 33855, 47617). A count field is not a pointer.
    #
    # OFFSET-PRESERVING: only in a field I KNOW is a pointer. Today that is the
    # texture datChunkRef, which points to +144 inside the chunk (the chunk
    # header comes before the pixels) and so never matches a block start.
    dest = {}
    for b in order:
        off, _, size_ = blocks[b]
        dest[BASE + off + 16] = new_map[b]
        if size_ > 16:
            dest[BASE + off + 32] = new_map[b] + 16

    def translate(v):
        return dest.get(v)

    def translate_interior(v):
        b = owner_(v - BASE)
        if b is None or b not in new_map:
            return None
        off, _, _t = blocks[b]
        return new_map[b] + (v - BASE - (off + 16))
    # DO NOT RELOCATE INSIDE A VIF PAYLOAD. Requiring the target to be the
    # start of a reached payload already dropped almost everything, but not all:
    # in a measurement over LA's 729 models, 111 words in 41 models were VERTEX
    # OR UV COORDINATES that happened to equal a block address - and they were
    # rewritten, silently corrupting the geometry. Vertex data produces any
    # 32-bit pattern; there are only pointers where there is STRUCTURE. The
    # ranges come from the mesh itself: root -> group table -> packet list.
    range_list = []
    ro, _, _rt = blocks[root]

    # TWO KINDS OF ROOT. Mesh: the protected ranges are the VIF payloads,
    # reached through group table -> packet list. TEXTURE (rmcTexturePS2, vtable
    # 0x00627280): they are the PIXEL blocks, pointed to by the four 12-byte
    # datChunkRef from +0x48 on. Pixel data hits a pointer value even more
    # easily than vertices, so the protection matters even more here; and
    # reading +0x10 on a texture would grab a GS register thinking it is a
    # pointer.
    if struct.unpack_from('<I', d, ro + 16)[0] == VT_TEXTURE_PS2:
        for k in range(4):
            ptr = struct.unpack_from('<I', d, ro + 16 + 0x48 + 12 * k)[0]
            if not (BASE <= ptr < BASE + len(d)):
                continue
            b = owner_(ptr - BASE)
            if b is not None:
                off_b, _, size_b = blocks[b]
                range_list.append((off_b + 16, off_b + 16 + size_b))
        range_list.sort()

        def in_payload_tex(x, _f=range_list):
            i = bisect.bisect_right(_f, (x, 1 << 30)) - 1
            return i >= 0 and _f[i][0] <= x < _f[i][1]

        swapped = spared = 0
        for b in order:
            off, _, size_ = blocks[b]
            d0 = new_map[b] - BASE_PCK
            for k in range(0, size_ & ~3, 4):
                v = struct.unpack_from('<I', d, off + 16 + k)[0]
                if BASE <= v < BASE + len(d) and translate(v) is not None:
                    if in_payload_tex(off + 16 + k):
                        spared += 1
                        continue
                    struct.pack_into('<I', body, d0 + k, translate(v))
                    swapped += 1
        # the four datChunkRef, at a known position
        d0r = new_map[root] - BASE_PCK
        for k in range(4):
            v = struct.unpack_from('<I', d, ro + 16 + 0x48 + 12 * k)[0]
            if not (BASE <= v < BASE + len(d)):
                continue
            nv = translate_interior(v)
            if nv is not None:
                struct.pack_into('<I', body, d0r + 0x48 + 12 * k, nv)
                swapped += 1
        if token:
            struct.pack_into('<I', body, 0, token)
        hdr = struct.pack('<4I', BASE_PCK, kind, 1, len(body)) + bytes(BODY - 16)
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        open(out_path, 'wb').write(hdr + body)
        return {'blocks': len(live), 'bytes': sum(blocks[b][2] for b in live),
                'relocated': swapped, 'spared': spared,
                'size': BODY + len(body)}

    gt = struct.unpack_from('<I', d, ro + 16 + 0x10)[0]
    pg = gt - BASE
    # rmcModelGeom+8 is the number of material groups, NOT always one.
    # Protect every group's VIF and its [qw,nv] scalar word. Restricting
    # this walk to group zero still corrupted 219 words in 81 LA meshes.
    ng = struct.unpack_from('<H', d, ro + 16 + 8)[0]
    if not (1 <= ng <= 4096 and 0 <= pg <= len(d) - 8 * ng):
        raise ValueError('invalid mesh group table while protecting VIF')
    for gi in range(ng):
        ge = pg + 8 * gi
        items = struct.unpack_from('<I', d, ge)[0] - BASE
        nb = struct.unpack_from('<H', d, ge + 4)[0]
        range_list.append((ge + 4, ge + 8))
        if not (0 <= nb <= 4096 and 0 <= items <= len(d) - 8 * nb):
            raise ValueError('invalid packet table while protecting VIF')
        if nb:
            for k in range(nb):
                e = items + 8 * k
                range_list.append((e + 4, e + 8))
                dp = struct.unpack_from('<I', d, e)[0] - BASE
                qw = struct.unpack_from('<H', d, e + 4)[0]
                if qw:
                    if not (0 <= dp <= len(d) - 16 * qw):
                        raise ValueError('invalid VIF extent while protecting payload')
                    range_list.append((dp, dp + 16 * qw))
    range_list.sort()
    # Merge overlaps so bisect cannot miss a containing interval.
    merged = []
    for start, end in range_list:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    range_list = merged

    def in_payload(x, _f=range_list):
        i = bisect.bisect_right(_f, (x, 1 << 30)) - 1
        return i >= 0 and _f[i][0] <= x < _f[i][1]

    swapped = spared = 0
    for b in order:
        off, _, size_ = blocks[b]
        d0 = new_map[b] - BASE_PCK
        for k in range(0, size_ & ~3, 4):
            v = struct.unpack_from('<I', d, off + 16 + k)[0]
            if BASE <= v < BASE + len(d) and translate(v) is not None:
                if in_payload(off + 16 + k):
                    spared += 1
                    continue
                struct.pack_into('<I', body, d0 + k, translate(v))
                swapped += 1
    if token:
        struct.pack_into('<I', body, 0, token)
    hdr = struct.pack('<4I', BASE_PCK, kind, 1, len(body)) + bytes(BODY - 16)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    open(out_path, 'wb').write(hdr + body)
    return {'blocks': len(live), 'bytes': sum(blocks[b][2] for b in live),
            'relocated': swapped, 'spared': spared,
            'size': BODY + len(body)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dump')
    ap.add_argument('--base', required=True, type=lambda s: int(s, 16),
                    help='VA where the heap landed (the BLOB line)')
    ap.add_argument('--root', type=lambda s: int(s, 16),
                    help='VA of the rmcModel (the PRB line)')
    ap.add_argument('--out-path', default='output/mesh.mesh.pck')
    ap.add_argument('--kind', type=lambda s: int(s, 16), default=MESH_KIND)
    ap.add_argument('--batch', metavar='TSV',
                    help='lines "<root hex>\t<output path>"; reads the dump ONCE')
    ap.add_argument('--token', type=lambda s: int(s, 16), default=TOKEN_MESH,
                    help='word at +0x00 of the root; 0 keeps the runtime vtable')
    a = ap.parse_args()

    d = open(a.dump, 'rb').read()
    BASE = a.base

    if a.batch:
        ok = failed = 0
        for line in open(a.batch, encoding='utf-8'):
            line = line.rstrip()
            if not line or line.startswith('#'):
                continue
            hexr, out_path = line.split(chr(9))[:2]
            r = emit(d, BASE, int(hexr, 16), out_path, a.kind, a.token, quiet=True)
            if r:
                ok += 1
            else:
                failed += 1
                print('  root %s falls in no block' % hexr)
        print('  batch: %d generated, %d failed' % (ok, failed))
        return
    blocks, stop = heap_blocks(d)
    print('%s: %d bytes, %d blocks (the walk covered %.1f%%)'
          % (os.path.basename(a.dump), len(d), len(blocks), 100.0 * stop / len(d)))

    def owner_(p):
        lo, hi = 0, len(blocks) - 1
        while lo <= hi:
            m = (lo + hi) // 2
            off, _, size_ = blocks[m]
            if p < off + 16:
                hi = m - 1
            elif p >= off + 16 + size_:
                lo = m + 1
            else:
                return m
        return None

    root = owner_(a.root - BASE)
    if root is None:
        raise SystemExit('the root %08X falls in no block' % a.root)

    seen, queue = {root}, [root]
    while queue:
        off, _, size_ = blocks[queue.pop()]
        for k in range(0, size_ & ~3, 4):
            v = struct.unpack_from('<I', d, off + 16 + k)[0]
            if BASE <= v < BASE + len(d):
                t = owner_(v - BASE)
                if t is not None and t not in seen:
                    seen.add(t)
                    queue.append(t)
    live = sorted(seen)
    live_bytes = sum(blocks[b][2] for b in live)
    print('  reached from the root: %d blocks, %d bytes (%.1f%% of the dump)'
          % (len(live), live_bytes, 100.0 * live_bytes / len(d)))

    # THE ROOT GOES FIRST. Nothing points to it (checked: zero references), so
    # moving it to the start of the body is free - and the `.pck` requires the
    # root at 0x80.
    order = [root] + [b for b in live if b != root]
    new, body = {}, bytearray()
    for b in order:
        off, _, size_ = blocks[b]
        new[b] = BASE_PCK + len(body)
        body += d[off + 16:off + 16 + size_]
        body += bytes((-len(body)) % 16)

    # map of valid targets: payload start and start+16
    dest = {}
    for b in order:
        off, _, size_ = blocks[b]
        dest[BASE + off + 16] = new[b]
        if size_ > 16:
            dest[BASE + off + 32] = new[b] + 16

    swapped = ignored = 0
    for b in order:
        off, _, size_ = blocks[b]
        d0 = new[b] - BASE_PCK
        for k in range(0, size_ & ~3, 4):
            v = struct.unpack_from('<I', d, off + 16 + k)[0]
            if not (BASE <= v < BASE + len(d)):
                continue
            if v in dest:
                struct.pack_into('<I', body, d0 + k, translate(v))
                swapped += 1
            else:
                ignored += 1
    print('  pointers relocated: %d   words in range but not at a start: %d'
          % (swapped, ignored))

    if a.token:
        struct.pack_into('<I', body, 0, a.token)
        print('  root +0x00: runtime vtable -> token %08X' % a.token)

    hdr = struct.pack('<4I', BASE_PCK, a.kind, 1, len(body)) + bytes(BODY - 16)
    os.makedirs(os.path.dirname(a.out_path) or '.', exist_ok=True)
    open(a.out_path, 'wb').write(hdr + body)
    print('  -> %s  (%d bytes, body %d)' % (a.out_path, BODY + len(body), len(body)))
    r = struct.unpack_from('<8I', body, 0)
    print('  root: vtable %08X flags %08X groups %d table %08X geoms %08X'
          % (r[0], r[1], r[2], r[3], r[4]))


if __name__ == '__main__':
    main()
