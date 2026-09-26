# -*- coding: utf-8 -*-
"""City packet round trip: decode and re-encode, compare byte for byte.

It is the test that says whether I can WRITE a city block, not only read one.
"""
import os
import struct
import math
import re
import collections

PATH = os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources/city/atlanta_midnight_clear.pck')
d = open(PATH, 'rb').read()
N = len(d)
TAG = re.compile(rb'..\x02\x60', re.DOTALL)


def parse(off):
    """Return everything the block is made of, including the bytes I do not understand yet."""
    if off + 0x10 > N:
        return None
    scale = struct.unpack_from('<f', d, off + 4)[0]
    cnt = struct.unpack_from('<I', d, off + 8)[0]
    if not (1 <= cnt <= 4096) or not math.isfinite(scale) or not (1e-6 < abs(scale) < 1e4):
        return None
    if d[off + 0x0F] != 0x69 or d[off + 0x0E] != (cnt & 0xFF):
        return None
    p0 = off + 0x10
    if p0 + 6 * cnt + 4 > N:
        return None
    u0 = p0 + 6 * cnt
    while u0 % 4:
        u0 += 1
    if d[u0 + 3] != 0x65 or u0 + 4 + 4 * cnt > N:
        return None
    return dict(
        off=off,
        tag=d[off:off + 4],
        scale=scale,
        cnt=cnt,
        sub_pos=d[off + 0x0C:off + 0x10],
        pos=[struct.unpack_from('<hhh', d, p0 + 6 * i) for i in range(cnt)],
        pad_pos=d[p0 + 6 * cnt:u0],
        sub_uv=d[u0:u0 + 4],
        uv=[struct.unpack_from('<hh', d, u0 + 4 + 4 * i) for i in range(cnt)],
        end=u0 + 4 + 4 * cnt,
    )


def encode(b):
    """Re-encode from the decoded fields."""
    o = bytearray()
    o += b['tag']
    o += struct.pack('<f', b['scale'])
    o += struct.pack('<I', b['cnt'])
    o += b['sub_pos']
    for p in b['pos']:
        o += struct.pack('<hhh', *p)
    o += b['pad_pos']
    o += b['sub_uv']
    for u in b['uv']:
        o += struct.pack('<hh', *u)
    return bytes(o)


blocks = []
for m in TAG.finditer(d):
    o = m.start()
    if o % 16:
        continue
    b = parse(o)
    if b:
        blocks.append(b)

equal = dif = 0
examples = []
for b in blocks:
    orig = d[b['off']:b['end']]
    new = encode(b)
    if orig == new:
        equal += 1
    else:
        dif += 1
        if len(examples) < 3:
            j = next((i for i in range(min(len(orig), len(new))) if orig[i] != new[i]), -1)
            examples.append((hex(b['off']), len(orig), len(new), j))

print('blocks: %d' % len(blocks))
print('round trip byte-for-byte IDENTICAL: %d | differs: %d' % (equal, dif))
for e in examples:
    print('   differs at %s  orig %d B, new %d B, 1st difference at byte %d' % e)

# what is in the fields I treat as opaque
subp = collections.Counter(b['sub_pos'].hex() for b in blocks)
subu = collections.Counter(b['sub_uv'].hex() for b in blocks)
padp = collections.Counter(len(b['pad_pos']) for b in blocks)
print()
print('sub_pos (+0x0C, 4 bytes): %d distinct values' % len(subp))
print('   most common: %s' % dict(subp.most_common(4)))
print('sub_uv  (4 bytes): %d distinct values' % len(subu))
print('   most common: %s' % dict(subu.most_common(4)))
print('padding between positions and uv: %s' % dict(padp))

# can sub_pos and sub_uv be derived from the count?
ok_p = sum(1 for b in blocks
           if b['sub_pos'] == bytes([b['sub_pos'][0], b['sub_pos'][1], b['cnt'] & 0xFF, 0x69]))
ok_u = sum(1 for b in blocks
           if b['sub_uv'] == bytes([b['sub_uv'][0], b['sub_uv'][1], b['cnt'] & 0xFF, 0x65]))
print()
print('sub_pos = [?, ?, cnt, 0x69] in %d/%d' % (ok_p, len(blocks)))
print('sub_uv  = [?, ?, cnt, 0x65] in %d/%d' % (ok_u, len(blocks)))
# and the first 2 bytes of the tag?
tg = collections.Counter(b['tag'][:2].hex() for b in blocks)
print('first 2 bytes of the tag: %d distinct, most common %s' % (len(tg), dict(tg.most_common(4))))
