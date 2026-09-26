#!/usr/bin/env python3
"""Attach one safe 16x16 fallback texture to every Basic shader in a city PCK.

This is deliberately a visual boot probe, not the final Paris texture port.
Paris v1 has several independently-cloned texture objects in each shader group,
so the LA slot-indexed proxy tool cannot be used unchanged.  All Basic textures
receive proxy ID 1, whose thumbnail is a real MC2 Paris texture.  That makes a
missing streamed texture render visible geometry instead of the global black
texture, while leaving mesh, instance and PPF data untouched.
"""
from __future__ import annotations

import os
import argparse
import json
import struct
import sys
from pathlib import Path

TOOLS = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, str(TOOLS))
from mc3_tex import read_tex, tex_to_image, quantize, pack_4bpp

HEADER = 0x80
ROOT_PROXY = HEADER + 0x228
TEX1_PROXY_MASK = 0xFFE00000


def u16(data, off): return struct.unpack_from('<H', data, off)[0]
def u32(data, off): return struct.unpack_from('<I', data, off)[0]
def u64(data, off): return struct.unpack_from('<Q', data, off)[0]
def align(n, a): return (n + a - 1) & -a


class Pck:
    def __init__(self, path):
        self.data = bytearray(Path(path).read_bytes())
        self.base = u32(self.data, 0)
        if u32(self.data, 8) != 1 or u32(self.data, 0x0C) != len(self.data) - HEADER:
            raise ValueError('PCK is not a valid version 1')
    def off(self, va, size=1):
        out = va - self.base + HEADER
        if out < HEADER or out + size > len(self.data):
            raise ValueError(f'pointer outside the PCK: {va:08X}')
        return out
    def va(self, off): return self.base + off - HEADER


def thumbnail(path):
    image = tex_to_image(read_tex(str(path)), 0)
    rgba = image.to_rgba(fix_alpha=False)
    small = bytearray(16 * 16 * 4)
    for y in range(16):
        y0, y1 = y * image.height // 16, max(y * image.height // 16 + 1, (y + 1) * image.height // 16)
        for x in range(16):
            x0, x1 = x * image.width // 16, max(x * image.width // 16 + 1, (x + 1) * image.width // 16)
            count = r = g = b = a = 0
            for sy in range(y0, min(image.height, y1)):
                for sx in range(x0, min(image.width, x1)):
                    q = (sy * image.width + sx) * 4
                    rr, gg, bb, aa = rgba[q:q + 4]
                    r += rr; g += gg; b += bb; a += aa; count += 1
            q = (y * 16 + x) * 4
            small[q:q + 4] = bytes((round(r/count), round(g/count), round(b/count), round(a/count)))
    work = bytearray(small)
    for q in range(3, len(work), 4):
        if work[q] == 0: work[q] = 1
    palette, indices = quantize(work, 16, 16, colors=16, alpha_max=128)
    packed = bytes(pack_4bpp(indices, 256))
    if len(packed) != 128 or len(palette) != 64: raise AssertionError('invalid proxy')
    return packed, bytes(palette)


def patch(source, texture, output, audit):
    city = Pck(source)
    count = u32(city.data, HEADER + 8)
    group_va = u32(city.data, HEADER + 0x0C)
    textures = set()
    for group in range(count):
        desc = city.off(group_va + group * 16, 16)
        slots = u16(city.data, desc + 8)
        if not slots: continue
        array = city.off(u32(city.data, desc + 4), slots * 4)
        for slot in range(slots):
            shader = u32(city.data, array + slot * 4)
            if not shader: continue
            shader = city.off(shader, 12)
            if (u32(city.data, shader + 4) & 0x7F) != 0: continue
            textures.add(city.off(u32(city.data, shader + 8), 0x90))
    if not textures: raise ValueError('no Basic texture found')
    for tex in textures:
        old = u64(city.data, tex + 0x88)
        struct.pack_into('<Q', city.data, tex + 0x88, (old & ~TEX1_PROXY_MASK) | (1 << 21))
    index, palette = thumbnail(texture)
    start = align(len(city.data), 16)
    city.data += bytes([0xCD]) * (start - len(city.data))
    proxy, indices, palettes = start, start + 0x20, start + 0x20 + 128
    cache32, cache16 = palettes + 64, palettes + 64 + 4
    header = bytearray([0xCD]) * 0x20
    struct.pack_into('<HHIIII', header, 0, 1, 1, city.va(indices), city.va(palettes), city.va(cache32), city.va(cache16))
    city.data += header + index + palette + bytes([0xCD]) * 6
    city.data += bytes([0xCD]) * (align(len(city.data), HEADER) - len(city.data))
    struct.pack_into('<I', city.data, ROOT_PROXY, city.va(proxy))
    struct.pack_into('<I', city.data, 0x0C, len(city.data) - HEADER)
    Path(output).write_bytes(city.data)
    report = {'source': str(source), 'output': str(output), 'basic_texture_objects': len(textures), 'proxy_count': 1, 'size': len(city.data)}
    Path(audit).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=Path); ap.add_argument('texture', type=Path); ap.add_argument('output', type=Path); ap.add_argument('audit', type=Path)
    args = ap.parse_args()
    print(json.dumps(patch(args.source, args.texture, args.output, args.audit), indent=2))
