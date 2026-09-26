"""
mc3_tex.py - Midnight Club 3 PS2 textures (.pck) and the .tex source format

Logic ported from the work of AlgumCorrupto/Thiekus/ZNX
(github.com/AlgumCorrupto/RAGE-SWF-research + swf-image-tools),
which covers the flash/ folder. The same swizzle routines apply to the rest of
the game, so here they are kept separate from the container parser.

Two sides:
  * SOURCE   .tex  -> 14-byte header + palette + mips (not swizzled)
  * COMPILED .pck  -> ps2Texture/ps2IndexedColors structs (swizzled)
"""
from __future__ import annotations
import re
import string
import struct, os
import importlib.util
from dataclasses import dataclass, field

# reverse pointer lookup helper (same directory) -- optional, only used as a
# fallback to size a texture of unknown format
try:
    _pp = importlib.util.spec_from_file_location(
        "mc3_pointers", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "mc3_pointers.py"))
    mc3_pointers = importlib.util.module_from_spec(_pp)
    _pp.loader.exec_module(mc3_pointers)
    PointerGraph = mc3_pointers.PointerGraph
except Exception:
    mc3_pointers = None
    PointerGraph = None

# --------------------------------------------------------------------------
# formats
# --------------------------------------------------------------------------
# .tex format code (offset 4). The bpp is PROVEN: the size formula matches
# exactly on all 1718 clean .tex files. 1/14 = 8bpp+256 colors, 15/16 =
# 4bpp+16 colors. The 1-vs-14 pair (and 15-vs-16) is the alpha channel flag, but
# it is an exporter choice, not derived from the content: fmt 1 never shows up
# with alpha (163/163 opaque) and the proxy inherits from the parent (1->15,
# 14->16), yet 14 and 16 are also used on opaque textures.
TEX_FMT_BPP = {1: 8, 14: 8, 15: 4, 16: 4}
TEX_FMT_NAME = {
    1:  "8bpp/256 colors, no alpha",
    14: "8bpp/256 colors, with alpha",
    15: "4bpp/16 colors, no alpha",
    16: "4bpp/16 colors, with alpha",
}

# ps2Texture.texture_type inside the .pck
BPP8_BOTH_SWIZZLED = 0x1   # indices and palette swizzled
BPP8_INDEX_SWIZZLED = 0x5  # only the palette swizzled, texture linear
BPP4_UNSWIZZLED = 0x6      # 4bpp linear, palette linear

TEXTYPE_NAME = {
    BPP8_BOTH_SWIZZLED: "8bpp (indices+palette swizzled)",
    BPP8_INDEX_SWIZZLED: "8bpp (palette swizzled only)",
    BPP4_UNSWIZZLED: "4bpp (linear)",
}


# --------------------------------------------------------------------------
# swizzle
# --------------------------------------------------------------------------
def unswizzle_palette(src: bytes, color_count: int = 256) -> bytearray:
    """PS2 CLUT: inside every block of 32 colors, the two middle groups of 8
    swap places. 16-color palettes do not go through this."""
    dst = bytearray(len(src))
    i = 0
    for part in range(color_count // 32):
        for block in range(2):
            for stripe in range(2):
                for color in range(8):
                    p = part * 32 + block * 8 + stripe * 16 + color
                    dst[i * 4:i * 4 + 4] = src[p * 4:p * 4 + 4]
                    i += 1
    return dst


def swizzle_palette(src: bytes, color_count: int = 256) -> bytearray:
    dst = bytearray(len(src))
    i = 0
    for part in range(color_count // 32):
        for block in range(2):
            for stripe in range(2):
                for color in range(8):
                    p = part * 32 + block * 8 + stripe * 16 + color
                    dst[p * 4:p * 4 + 4] = src[i * 4:i * 4 + 4]
                    i += 1
    return dst


def _swizzle8_map(width: int, height: int):
    """Build the PSMT8 linear->swizzled mapping. Cached per (w,h) because this
    is the bottleneck: without a cache a 256x256 texture redoes the same math
    65k times."""
    key = (width, height)
    m = _SWZ_CACHE.get(key)
    if m is not None:
        return m
    m = [0] * (width * height)
    for y in range(height):
        blk_y = (y & ~0xF) * width
        swap = (((y + 2) >> 2) & 1) * 4
        pos_y = (((y & ~3) >> 1) + (y & 1)) & 7
        col_y = pos_y * width * 2
        byte_y = (y >> 1) & 1
        row = y * width
        for x in range(width):
            block = blk_y + (x & ~0xF) * 2
            column = col_y + ((x + swap) & 7) * 4
            m[row + x] = block + column + byte_y + ((x >> 2) & 2)
    _SWZ_CACHE[key] = m
    return m


_SWZ_CACHE: dict = {}


def unswizzle8(src: bytes, width: int, height: int) -> bytearray:
    m = _swizzle8_map(width, height)
    dst = bytearray(width * height)
    for i, s in enumerate(m):
        dst[i] = src[s]
    return dst


def swizzle8(src: bytes, width: int, height: int) -> bytearray:
    m = _swizzle8_map(width, height)
    dst = bytearray(width * height)
    for i, s in enumerate(m):
        dst[s] = src[i]
    return dst


def unpack_4bpp(src: bytes, pixels: int) -> bytearray:
    """low nibble first"""
    dst = bytearray(pixels)
    for i in range(0, pixels, 2):
        b = src[i >> 1]
        dst[i] = b & 0x0F
        if i + 1 < pixels:
            dst[i + 1] = b >> 4
    return dst


def pack_4bpp(src: bytes, pixels: int) -> bytearray:
    dst = bytearray((pixels + 1) // 2)
    for i in range(0, pixels, 2):
        p1 = (src[i + 1] & 0x0F) if i + 1 < pixels else 0
        dst[i >> 1] = (src[i] & 0x0F) | (p1 << 4)
    return dst


# --------------------------------------------------------------------------
# alpha: 0..128 on the PS2, 0..255 in PNG
# --------------------------------------------------------------------------
def alpha_to_pc(pal: bytearray) -> bytearray:
    out = bytearray(pal)
    for i in range(3, len(out), 4):
        if out[i]:
            out[i] = min(255, out[i] * 2 - 1)
    return out


def alpha_to_ps2(pal: bytearray) -> bytearray:
    out = bytearray(pal)
    for i in range(3, len(out), 4):
        # Inverse of alpha_to_pc's ``2*a - 1`` mapping.  A plain right shift
        # made fully opaque PC/PSP alpha 255 become 127, so the native GS
        # endpoint 0x80 was unreachable.  Round the odd endpoint upward:
        # 0 -> 0, 1 -> 1, 254 -> 127, 255 -> 128.
        out[i] = (out[i] + 1) >> 1
    return out


# --------------------------------------------------------------------------
# decoded image
# --------------------------------------------------------------------------
@dataclass
class Image:
    width: int
    height: int
    indexes: bytearray            # 1 byte por pixel
    palette: bytearray            # RGBA, already unswizzled, alpha still 0..128
    texture_type: int = -1
    name: str = ""
    addr: int = 0                 # virtual address of the owner struct
    mips: list = field(default_factory=list)

    @property
    def colors(self) -> int:
        return len(self.palette) // 4

    def to_rgba(self, fix_alpha: bool = True) -> bytearray:
        pal = alpha_to_pc(self.palette) if fix_alpha else self.palette
        out = bytearray(self.width * self.height * 4)
        for i, idx in enumerate(self.indexes):
            o = idx * 4
            out[i * 4:i * 4 + 4] = pal[o:o + 4]
        return out

    def save_png(self, path: str, fix_alpha: bool = True):
        write_png(path, self.width, self.height, self.to_rgba(fix_alpha))

    def __repr__(self):
        t = TEXTYPE_NAME.get(self.texture_type, f"type {self.texture_type}")
        return f"<Image {self.name or '?'} {self.width}x{self.height} {self.colors}c {t}>"


# --------------------------------------------------------------------------
# minimal PNG (no external dependency)
# --------------------------------------------------------------------------
def write_png(path: str, width: int, height: int, rgba: bytes):
    import zlib
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def read_png(path: str):
    """Read an 8-bit PNG (RGB/RGBA/palette/gray, non-interlaced) and return
    (width, height, rgba_bytearray). Covers what write_png produces and what
    common editors export; uses Pillow if installed, otherwise decodes by hand."""
    try:
        from PIL import Image as _PIL
        im = _PIL.open(path).convert("RGBA")
        return im.width, im.height, bytearray(im.tobytes())
    except ImportError:
        pass

    import zlib
    d = open(path, "rb").read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    o = 8
    w = h = bit = ctype = None
    idat, plte, trns = bytearray(), b"", b""
    while o < len(d):
        ln = struct.unpack_from(">I", d, o)[0]
        tag = d[o + 4:o + 8]
        chunk = d[o + 8:o + 8 + ln]
        if tag == b"IHDR":
            w, h, bit, ctype = struct.unpack_from(">IIBB", chunk, 0)
        elif tag == b"PLTE":
            plte = chunk
        elif tag == b"tRNS":
            trns = chunk
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
        o += 12 + ln
    if bit != 8:
        raise ValueError("only 8-bit/channel PNG is read here (install Pillow for the rest)")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = w * chans
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        for i in range(stride):
            a = line[i - chans] if i >= chans else 0
            b = prev[i]
            c = prev[i - chans] if i >= chans else 0
            x = line[i]
            if ft == 1:
                x += a
            elif ft == 2:
                x += b
            elif ft == 3:
                x += (a + b) >> 1
            elif ft == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                x += a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[i] = x & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    rgba = bytearray(w * h * 4)
    if ctype == 6:
        rgba[:] = out
    elif ctype == 2:
        for i in range(w * h):
            rgba[i * 4:i * 4 + 3] = out[i * 3:i * 3 + 3]
            rgba[i * 4 + 3] = 255
    elif ctype == 3:
        for i in range(w * h):
            e = out[i]
            rgba[i * 4:i * 4 + 3] = plte[e * 3:e * 3 + 3]
            rgba[i * 4 + 3] = trns[e] if e < len(trns) else 255
    else:  # 0 ou 4 (cinza)
        for i in range(w * h):
            g = out[i * chans]
            rgba[i * 4:i * 4 + 3] = bytes((g, g, g))
            rgba[i * 4 + 3] = out[i * chans + 1] if ctype == 4 else 255
    return w, h, rgba


# --------------------------------------------------------------------------
# .tex source
# --------------------------------------------------------------------------
@dataclass
class TexFile:
    width: int
    height: int
    fmt: int
    mipmaps: int
    f8: int
    f10: int
    f12: int
    palette: bytearray
    levels: list

    @property
    def bpp(self) -> int:
        return TEX_FMT_BPP[self.fmt]

    @property
    def colors(self) -> int:
        return 16 if self.bpp == 4 else 256


def read_tex(path: str) -> TexFile:
    """A .tex source from ASSETS/**/texture/. Validated on all 1718 clean files:
    the size matches the formula exactly on every one of them."""
    d = open(path, "rb").read()
    w, h, fmt, mips, f8, f10, f12 = struct.unpack_from("<7H", d, 0)
    if fmt not in TEX_FMT_BPP:
        raise ValueError(f"unknown .tex format: {fmt}")
    bpp = TEX_FMT_BPP[fmt]
    ncol = 16 if bpp == 4 else 256
    o = 14
    pal = bytearray(d[o:o + ncol * 4]); o += ncol * 4
    levels = []
    for m in range(mips):
        mw, mh = max(1, w >> m), max(1, h >> m)
        n = mw * mh * bpp // 8
        levels.append((mw, mh, bytearray(d[o:o + n])))
        o += n
    return TexFile(w, h, fmt, mips, f8, f10, f12, pal, levels)


def tex_to_image(t: TexFile, level: int = 0) -> Image:
    mw, mh, data = t.levels[level]
    idx = unpack_4bpp(data, mw * mh) if t.bpp == 4 else bytearray(data)
    return Image(mw, mh, idx, t.palette, texture_type=-1)


def write_tex(path: str, t: TexFile):
    with open(path, "wb") as f:
        f.write(struct.pack("<7H", t.width, t.height, t.fmt, t.mipmaps,
                            t.f8, t.f10, t.f12))
        f.write(t.palette)
        for _, _, data in t.levels:
            f.write(data)



# --------------------------------------------------------------------------
# PNG -> .tex
# --------------------------------------------------------------------------
def png_to_tex(png_path: str, fmt: int = 14) -> TexFile:
    """Build a .tex source from a PNG.

    The format is palettised - 256 colours at 8bpp, or 16 at 4bpp - so an RGBA
    image has to be reduced. Quantising RGB and alpha separately is the usual
    way and it is wrong here: a soft rim over transparency needs the PAIR, so
    the palette is built over whole RGBA tuples.

    Alpha is snapped to 4-value steps first. That is not a compromise: the GS
    stores alpha as 0..128, so a PNG's 256 levels are already twice the
    precision the hardware keeps, and snapping frees palette slots for colour.
    """
    from PIL import Image as PILImage
    src = PILImage.open(png_path).convert("RGBA")
    w, h = src.size
    if w & (w - 1) or h & (h - 1):
        raise ValueError("the PS2 wants a power of two; this is %dx%d" % (w, h))

    bpp = TEX_FMT_BPP[fmt]
    ncol = 16 if bpp == 4 else 256

    px = list(src.getdata())
    snapped = [(r, g, b, a & 0xFC) for (r, g, b, a) in px]
    from collections import Counter
    common = [c for c, _ in Counter(snapped).most_common(ncol)]
    if not common:
        common = [(0, 0, 0, 0)]
    # A fully transparent entry has to exist, and at index 0, or every pixel the
    # art left empty picks up whatever colour happens to be nearest.
    if (0, 0, 0, 0) in common:
        common.remove((0, 0, 0, 0))
    common = [(0, 0, 0, 0)] + common[:ncol - 1]

    def near_check(c):
        r, g, b, a = c
        best_, di = 0, None
        for i, (pr, pg, pb, pa) in enumerate(common):
            # Alpha is weighted heavily: a wrong alpha shows as a hard edge,
            # a wrong colour inside an opaque area does not.
            d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2 + 4 * (a - pa) ** 2
            if di is None or d < di:
                best_, di = i, d
        return best_

    cache = {}
    idx = bytearray(w * h)
    for i, c in enumerate(snapped):
        j = cache.get(c)
        if j is None:
            j = cache[c] = near_check(c)
        idx[i] = j

    pal = bytearray()
    for (r, g, b, a) in common:
        pal += bytes((r, g, b, a))
    pal += bytes(4 * (ncol - len(common)))
    # NOT alpha_to_ps2 here. Measured on a shipped file: arial12_00.tex has
    # palette alpha running to 255, so the .tex SOURCE keeps the PC range and
    # the 0..128 conversion happens further down, on the way into the .pck.
    # Converting here made a round-trip come back with 255 as 126.

    data = idx if bpp == 8 else pack_4bpp(idx)
    return TexFile(w, h, fmt, 1, 0, 0, 0, pal, [(w, h, bytearray(data))])


# --------------------------------------------------------------------------
# container .pck
# --------------------------------------------------------------------------
SWF_OBJ_FILE, SWF_OBJ_SHAPE, SWF_OBJ_SPRITE, SWF_OBJ_BUTTON = 0, 1, 2, 3
SWF_OBJ_BITMAP, SWF_OBJ_FONT, SWF_OBJ_TXT, SWF_OBJ_EDITTEXT = 4, 5, 6, 7


class Pck:
    """Raw .pck container. file_offset = pointer - base + 0x80."""

    def __init__(self, path: str):
        self.path = path
        self.data = bytearray(open(path, "rb").read())
        self.base, self.unk1, self.unk2, self.size = struct.unpack_from("<4I", self.data, 0)

    def off(self, va: int) -> int:
        return va - self.base + 0x80

    def va(self, off: int) -> int:
        return off - 0x80 + self.base

    def valid(self, va: int) -> bool:
        return self.base <= va < self.base + len(self.data) - 0x80

    def u8(self, o):  return self.data[o]
    def u16(self, o): return struct.unpack_from("<H", self.data, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.data, o)[0]

    def cstr(self, o: int) -> str:
        e = self.data.index(b"\0", o)
        return self.data[o:e].decode("latin1")

    def save(self, path: str = None):
        open(path or self.path, "wb").write(self.data)


def read_ps2_texture(p: Pck, tex_va: int, clut_va: int, width=None, height=None) -> Image:
    """Decode a ps2Texture + ps2IndexedColors pair."""
    t = p.off(tex_va)
    ptr_tex = p.u32(t + 0x08)
    tw, th = p.u16(t + 0x2C), p.u16(t + 0x2E)
    ttype = p.u8(t + 0x30)
    w, h = width or tw, height or th

    c = p.off(clut_va)
    ncol = p.u16(c + 0x02)
    pal_raw = bytes(p.data[c + 0x10:c + 0x10 + ncol * 4])

    raw_off = p.off(ptr_tex)

    if ttype == BPP4_UNSWIZZLED:
        idx = unpack_4bpp(p.data[raw_off:raw_off + (w * h + 1) // 2], w * h)
        pal = bytearray(pal_raw)
    elif ttype == BPP8_INDEX_SWIZZLED:
        idx = bytearray(p.data[raw_off:raw_off + w * h])
        pal = unswizzle_palette(pal_raw, ncol)
    elif ttype == BPP8_BOTH_SWIZZLED:
        idx = unswizzle8(p.data[raw_off:raw_off + w * h], w, h)
        pal = unswizzle_palette(pal_raw, ncol)
    else:
        raise ValueError(f"unsupported texture_type: {ttype}")

    return Image(w, h, idx, pal, texture_type=ttype, addr=tex_va)


def write_ps2_texture(p: Pck, tex_va: int, clut_va: int, img: Image):
    """Rewrite indices and palette in place. Does not change size nor relocate
    anything: the new image must have the same width/height and at most the same
    color count as the original."""
    t = p.off(tex_va)
    ptr_tex = p.u32(t + 0x08)
    tw, th = p.u16(t + 0x2C), p.u16(t + 0x2E)
    ttype = p.u8(t + 0x30)
    if (img.width, img.height) != (tw, th):
        raise ValueError(f"size mismatch: image {img.width}x{img.height}, slot {tw}x{th}")

    c = p.off(clut_va)
    ncol = p.u16(c + 0x02)
    if img.colors > ncol:
        raise ValueError(f"palette too large: {img.colors} > {ncol}")

    pal = bytearray(img.palette) + bytes(4 * (ncol - img.colors))
    raw_off = p.off(ptr_tex)

    if ttype == BPP4_UNSWIZZLED:
        packed = pack_4bpp(img.indexes, img.width * img.height)
        p.data[raw_off:raw_off + len(packed)] = packed
    elif ttype == BPP8_INDEX_SWIZZLED:
        p.data[raw_off:raw_off + tw * th] = img.indexes
        pal = swizzle_palette(pal, ncol)
    elif ttype == BPP8_BOTH_SWIZZLED:
        p.data[raw_off:raw_off + tw * th] = swizzle8(img.indexes, tw, th)
        pal = swizzle_palette(pal, ncol)
    else:
        raise ValueError(f"unsupported texture_type: {ttype}")

    p.data[c + 0x10:c + 0x10 + ncol * 4] = pal


# ---- the flash path (swfBITMAP -> bmpInfo1 -> ps2Texture) ------------------
def flash_images(p: Pck) -> list:
    """Walk the swfFILE object list and return the images."""
    root = 0x80
    obj_list_va = p.u32(root + 0x10)
    count = p.u16(root + 0x2A)
    out = []
    lst = p.off(obj_list_va)
    for i in range(1, count):
        va = p.u32(lst + i * 4)
        if not p.valid(va):
            continue
        o = p.off(va)
        if p.u8(o) != SWF_OBJ_BITMAP:
            continue
        info_va = p.u32(o + 0x08)
        if not p.valid(info_va):
            continue
        info = p.off(info_va)
        w, h = p.u16(info + 0x88), p.u16(info + 0x8A)
        clut_va = p.u32(info + 0x90)
        tex_va = p.u32(info + 0x94)
        if not (p.valid(clut_va) and p.valid(tex_va)):
            continue
        img = read_ps2_texture(p, tex_va, clut_va, w, h)
        img.name = f"bitmap_{i:03d}"
        img.addr = info_va
        out.append(img)
    return out


# ---- generic scan (vehicles, city, etc.) ----------------------------------
def scan_textures(p: Pck) -> list:
    """Find ps2Texture structs by signature, without relying on the object
    graph. Useful for the .pck files that are not flash."""
    d, out, seen = p.data, [], set()
    end = len(d) - 0x48
    for o in range(0x80, end, 4):
        ttype = d[o + 0x30]
        if ttype not in (BPP4_UNSWIZZLED, BPP8_INDEX_SWIZZLED, BPP8_BOTH_SWIZZLED):
            continue
        w, h = struct.unpack_from("<HH", d, o + 0x2C)
        if not (8 <= w <= 2048 and 8 <= h <= 2048):
            continue
        if w & (w - 1) or h & (h - 1):
            continue
        ptr_tex = struct.unpack_from("<I", d, o + 0x08)[0]
        if not p.valid(ptr_tex):
            continue
        # the 0xCD padding between +0x0C and +0x28 is what rules out false hits
        if d[o + 0x0C:o + 0x28] != b"\xcd" * 0x1C:
            continue
        va = p.va(o)
        if va in seen:
            continue
        seen.add(va)
        out.append((va, w, h, ttype, ptr_tex))
    return out


def find_cluts(p: Pck) -> list:
    """ps2IndexedColors: u16 unk3(=1), u16 color_count, 12 bytes, colors."""
    d, out = p.data, []
    for o in range(0x80, len(d) - 0x410, 4):
        if struct.unpack_from("<H", d, o)[0] != 1:
            continue
        n = struct.unpack_from("<H", d, o + 2)[0]
        if n not in (16, 256):
            continue
        alpha = d[o + 0x10 + 3: o + 0x10 + n * 4: 4]
        if len(alpha) == n and max(alpha) <= 0x80 and len(set(alpha)) > 1:
            out.append((p.va(o), n))
    return out


# --------------------------------------------------------------------------
# raw block with mips (the path of the user's mc3_texture_palette_menu)
#
# Vehicle .pck files do not use the flash ps2Texture/ps2IndexedColors structs;
# the texture is a raw block: the levels back to back, with padding between them,
# and the palette at the end. The layout below (256/128/64/32 + 0x100 padding) is
# the one the user mapped in ImHex. CAREFUL: it is not universal -- in
# vp_350z_04.pck there is a 256x64 texture in the middle, so check the dimensions
# before assuming mips.
# --------------------------------------------------------------------------
MIP_PAD = 0x100

def mip_layout(width: int, height: int, levels: int = 4, pad: int = MIP_PAD) -> list:
    """[(w, h, relative_offset, n_bytes)] + palette offset at the end."""
    out, o = [], 0
    for i in range(levels):
        w, h = max(1, width >> i), max(1, height >> i)
        n = w * h
        out.append((w, h, o, n))
        o += n + (pad if i < levels - 1 else 0)
    return out, o


# --------------------------------------------------------------------------
# texture descriptor (what sits right ABOVE the pixels in a vehicle .pck)
#
#   desc+0x00  00 00 00 00
#   desc+0x04  vtable ptr  (0x00953128 on the retail build; vp_d_* use another)
#   desc+0x08  ptr to the owner node -- and that node points BACK to the
#              descriptor at +0x00
#   desc+0x0C  format/size descriptor: 00 05 01 00 = 256x256,
#              00 09 00 00 = 32x32, 60 01 00 00 = external-palette texture
#   desc+0x10  cd cd cd cd            <- the 0x4 padding
#   desc+0x14  0e 00 ff ff            <- the 0x4 of data (0x0E = same code the
#                                        .tex uses for 8bpp with alpha)
#   desc+0x18  00 00 00 00
#   desc+0x1C  cd cd cd cd
#   desc+0x20  0x70 bytes of 0xCD     <- the big padding
#   desc+0x90  pixels
#
# Anchoring on the vtable ptr does NOT work: it varies per build (27 vp_d_* cars
# do not carry the same value). Anchoring on the data block at +0x10..+0x20 works
# on all 94 cars and is instant.
# --------------------------------------------------------------------------
DESC_ANCHOR = bytes.fromhex("cdcdcdcd0e00ffff00000000cdcdcdcd")
DESC_TO_PIXELS = 0x90
# Field desc+0x0C. Observed pattern: byte[1] looks like a log2 of the area
# (05->256^2, 45->128^2, 09->32^2, 06->16^2), but only these 4 values exist in the
# assets, so it is mapped directly instead of risking the formula.
DESC_FMT = {
    b"\x00\x05\x01\x00": (256, 256),   # main texture of the base and _g .pck
    b"\x00\x45\x00\x00": (128, 128),   # the same one in _o (opponent, half res)
    b"\x00\x25\x00\x00": (128, 64),    # traffic; 128x64 and not 64x128 because
    b"\x00\x15\x00\x00": (64, 64),     #   it decodes cleaner on 8 of 8 of them
    b"\x00\x09\x00\x00": (32, 32),
    b"\x00\x06\x00\x00": (16, 16),     # dark thumbnail of the vp_d_*_o opponents
}


def find_descriptors(data: bytes) -> list:
    """Find every texture through the descriptor. Instant and build
    independent. Returns [(desc, pixels, fmt_field, owner_ptr)]."""
    out, i = [], 0
    while True:
        i = data.find(DESC_ANCHOR, i)
        if i < 0:
            break
        px = i + 0x80
        desc = px - DESC_TO_PIXELS
        ptr = struct.unpack_from("<I", data, desc + 8)[0] if desc + 12 <= len(data) else 0
        out.append((desc, px, bytes(data[desc + 0x0C:desc + 0x10]), ptr))
        i += 4
    return out


def descriptor_size(fmt: bytes):
    """Size from the +0x0C field, or None for the external-palette case."""
    return DESC_FMT.get(fmt)

DESC_AREA_BIAS = 5


DESC_SIZE_BIAS = 0x100


def descriptor_bytes(fmt: bytes):
    """Size of the texture data in bytes, from the +0x0C field.

    The field is simply <b>data size + 0x100</b>. Checked against every size in
    the game: 256x256 -> 65536+1024+256 = 66816 (0x00010500), 128x128 -> 17664,
    128x64 -> 9472, 64x64 -> 5376, 32x32 -> 2304, 16x16 -> 1536, and the 8x8
    4bpp placeholder -> 32+64+256 = 352 (0x00000160).

    This replaced an earlier reading of the same field as "(w*h >> 8) + 5". That
    was an algebraic coincidence -- for an 8bpp texture size = w*h + 1024, so
    size + 0x100 shifted right by 8 does give w*h/256 + 5 -- but it broke on the
    4bpp placeholder, which this does not. The PSP .pspppf stores the same field
    WITHOUT the 0x100 bias (its page 0 reads 0x8080 = 32896, exactly the size
    its .pspppmap CSV declares).

    Still only a size, so 128x64 and 64x128 remain indistinguishable here; the
    shape comes from DESC_FMT or from looking at the pixels.
    """
    v = struct.unpack_from("<I", fmt, 0)[0]
    n = v - DESC_SIZE_BIAS
    return n if n > 0 else None


def descriptor_area(fmt: bytes):
    """Pixel count implied by the +0x0C field, assuming 8bpp + 256-colour CLUT."""
    n = descriptor_bytes(fmt)
    if n is None or n <= 1024:
        return None
    return n - 1024


def shapes_for_area(area: int):
    """Power-of-two (w, h) pairs with that area, widest first."""
    out = []
    w = 1
    while w <= area:
        h = area // w
        if w * h == area and 8 <= w <= 1024 and 8 <= h <= 1024:
            out.append((w, h))
        w *= 2
    out.sort(key=lambda wh: (abs(wh[0] - wh[1]), -wh[0]))
    return out



# The full chain, from the bottom up:
#   node (vtable 0x007A1260 on retail / 0x007A2568 on vp_d_*)
#        +0x08 -> string with the texture name
#        +0x0C -> info
#   info (vtable 0x007A14A0 retail / 0x007A27A8 on vp_d_*)
#        +0x48 -> descriptor    (and the descriptor +0x08 points back here)
#   descriptor +0x90 -> pixels
# Several nodes point at the same info: one per material that uses the texture.
INFO_TO_DESC = 0x48
NODE_NAME = 0x08
NODE_INFO = 0x0C


# A resource name is plain ASCII. Using str.isalnum() here was a trap: it is
# Unicode-aware, so a latin1-decoded 0xEB reads as a letter and garbage like
# "0\xebz" passed as a name.
_NAME_HEAD = frozenset(string.ascii_letters + string.digits + chr(95))  # __envmap__ starts with _
_NAME_OK = frozenset(string.ascii_letters + string.digits + "_.-")


def texture_names(data: bytes, base: int = 0x06800000) -> dict:
    """{info_offset: name}. Scans the nodes looking for whoever points at an
    info and reads the string. Does not depend on the vtable value, only on the
    node layout."""
    infos = {}
    for desc, px, fmt, ptr in find_descriptors(data):
        o = ptr - base + 0x80 - INFO_TO_DESC
        if 0 <= o < len(data):
            infos[o] = None
    if not infos:
        return {}

    n = len(data)
    for o in range(0x80, n - 0x10, 4):
        v = struct.unpack_from("<I", data, o + NODE_INFO)[0]
        t = v - base + 0x80
        if t not in infos or infos[t] is not None:
            continue
        s = struct.unpack_from("<I", data, o + NODE_NAME)[0] - base + 0x80
        if not (0 <= s < n) or not (32 <= data[s] < 127):
            continue
        e = data.find(b"\0", s)
        if e < 0 or e - s > 64:
            continue
        try:
            name = data[s:e].decode("latin1")
        except Exception:
            continue
        # a resource name is [A-Za-z0-9_.-]; without this the scan matches any
        # node-looking data and makes up a name (e.g. "h%z"). The dot matters:
        # traffic containers name their textures "va_whl_truck_drk.tex", and
        # rejecting it left every traffic texture anonymous.
        if len(name) > 2 and name[0] in _NAME_HEAD and all(c in _NAME_OK for c in name):
            infos[t] = name
    return infos


TEX_NAME_WINDOW = 0x90
_TEX_NAME_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.-]{2,}\.tex\0")


def name_before_descriptor(data: bytes, desc: int, window: int = TEX_NAME_WINDOW):
    """Last "*.tex" string in the `window` bytes before a descriptor, or None.

    A FALLBACK ONLY, for textures whose info nothing references through a node.
    Those exist: sd_traffic has va_whl_truck_drk.tex sitting right there in the
    file, but no node links it to the info, so the node walk cannot find it.

    Adjacency is normally a trap in this format -- the plain "nearest preceding
    string" agrees with the node-resolved name only 15 times against 69. Two
    restrictions, measured together over every vehicle and traffic file, make
    this version safe: requiring the .tex suffix (every miss was a bone name
    like "glow_side1") and bounding the window.

    Swept: 0x60 scores 20 agreements, 0x90 through 0xC0 score 30, all with ZERO
    disagreements. The first one appears at 0xE0, where sd_traffic starts
    handing the __envmap__ placeholder the name of the bus. 0x90 sits at the
    start of the plateau, so it gets the full 30 with room to spare.
    """
    lo = max(0, desc - window)
    hits = list(_TEX_NAME_RE.finditer(data[lo:desc]))
    return hits[-1].group()[:-1].decode("latin1") if hits else None



# ---------------------------------------------------------------------------
# pf05 texture packs: .ppf (PS2) and .pspppf (PSP)
# ---------------------------------------------------------------------------
PF_MAGIC = b"pf05"
PF_BLOCK = 0x800


def pf_pages(data: bytes):
    """[(index, offset, size)] of a pf05 pack.

    Header is magic, u32 count, u32 page size. Then `count` entries of two u16:
    a block index (offset = index * 0x800) and the page size in units of 0x80.

    The skeleton of this came from offlbruno's ImHex pattern, which had the
    block index and the *0x800 already; what the cross-check added is that the
    second u16 is a size, not flags -- page_bytes = field * 0x80. Verified
    against hudmap.pspppmap, the plain-text CSV the PSP ships next to its pack:
    all 10 pages hold their CSV contents exactly, and the one short page reads
    0x0030 -> 0x1800 bytes, which is the only value its 0x1440 of content fits.
    """
    if data[:4] != PF_MAGIC:
        return []
    count, page = struct.unpack_from("<II", data, 4)
    out = []
    for i in range(count):
        bi, sz = struct.unpack_from("<HH", data, 0x0C + 4 * i)
        out.append((i, bi * PF_BLOCK, sz * 0x80))
    return out


def pf_textures(data: bytes):
    """[(name, pixels_offset, (w, h) or None, descriptor)] inside a pf05 pack.

    A pack page carries the SAME descriptor as a .pck -- same anchor, same
    +0x0C field -- so the vehicle reader works on it unchanged. atlanta's city
    pack holds 3145 of them.
    """
    out = []
    for desc, px, fmt, ptr in find_descriptors(data):
        dim = descriptor_size(fmt)
        if dim is None:
            a = descriptor_area(fmt)
            shapes = shapes_for_area(a) if a else []
            dim = shapes[0] if shapes else None
        out.append((None, px, dim, desc))
    return out

def decode_texture(data: bytes, px: int, w: int, h: int) -> Image:
    """Decode a vehicle texture from an already resolved size.
    8x8 is the flat placeholder (4bpp, 16-color palette right after, no swizzle);
    every other size is 8bpp both-swizzled with the palette right after the
    pixels."""
    if (w, h) == (8, 8):
        pal = bytearray(data[px + 32:px + 32 + 64])
        idx = unpack_4bpp(data[px:px + 32], 64)
        im = Image(w, h, idx, pal, texture_type=BPP4_UNSWIZZLED, addr=px)
        return im
    return read_block(data, px, w, h, levels=1)


def replace_texture(data: bytearray, px: int, w: int, h: int, rgba,
                    src_w: int, src_h: int):
    """Replace an 8bpp both-swizzled texture (256/128/32/16) in place with an
    RGBA image (alpha 0..255). Rescales with nearest neighbour if the source size
    differs. Does not touch file size nor pointers. Raises for the 8x8 placeholder
    (not worth editing) and for a colorless source."""
    if (w, h) == (8, 8):
        raise ValueError("8x8 texture is a flat placeholder; cannot be edited")
    if (src_w, src_h) != (w, h):
        rgba = _nearest_resize(rgba, src_w, src_h, w, h)
    pal, idx = quantize(rgba, w, h, 256, alpha_max=128)
    img = Image(w, h, idx, pal)
    write_block(data, px, [img], pal, colors=256, swizzled=True)


def _nearest_resize(rgba, sw, sh, dw, dh):
    out = bytearray(dw * dh * 4)
    for y in range(dh):
        sy = y * sh // dh
        for x in range(dw):
            sx = x * sw // dw
            s = (sy * sw + sx) * 4
            out[(y * dw + x) * 4:(y * dw + x) * 4 + 4] = rgba[s:s + 4]
    return out


def block_end_by_pointer(data: bytes, px: int, base: int = 0x06800000):
    """End of the texture block through the pointer graph: the next address
    somebody points at, after the pixels, is the start of the next structure. Use
    it to size a texture of unknown format without any padding heuristic (this is
    how the dark 16x16 ones in vp_d_*_o were solved). Returns the offset or
    None."""
    if PointerGraph is None:
        return None
    g = PointerGraph(data, base - 0x80)
    return g.next_target(px)


def trace_texture(pck_or_data, px: int, base: int = 0x06800000):
    """Walk up the owner chain of a texture (pixels at `px`) to the root, using
    the reverse lookup method. Returns the PointerGraph step list."""
    if PointerGraph is None:
        raise RuntimeError("mc3_pointers.py not found")
    data = pck_or_data.data if hasattr(pck_or_data, "data") else pck_or_data
    g = PointerGraph(data, base - 0x80)
    return g.trace_up(px)


def region_end_cdscan(data: bytes, px: int) -> int:
    """End of the useful region, scanning from the start to the first 0xCD.
    FRAGILE: if the
    pixels themselves contain 0xCD it stops in the middle of the image (which is
    what happened with the dark 16x16 ones in vp_d_*_o). Use only as fallback."""
    e = px
    while e < len(data) and data[e] != 0xCD:
        e += 1
    return e


def region_end(data: bytes, px: int, base: int = 0x06800000, graph=None):
    """End of the useful region of a block, preferring the POINTER method: the
    next pointed-to address marks the start of the next struct, and only the
    TRAILING padding is stripped from there. Falls back to the CD-scan when there
    is no graph/target.

    CAREFUL: pixel data forms u32 that fall inside the VA window, so for a LARGE
    block the next "target" is usually a false positive inside the image itself
    (on a 256x256 it gives ~7 KB instead of 66 KB). That is why the caller must
    validate the size; this is only reliable for small blocks."""
    if graph is None and PointerGraph is not None:
        graph = PointerGraph(data, base - 0x80)
    if graph is not None:
        _s, e, _n = graph.block_bounds(px)
        if e is not None and e > px:
            return e
    return region_end_cdscan(data, px)


def probe_small(data: bytes, px: int, end: int = None):
    """Flat placeholder texture: 32 bytes of 4bpp pixels (64 pixels, 8x8)
    followed by a 16-color palette, then 0xCD padding up to the next struct.

    Across the 94 cars this covers 501 of the 783 textures (all of them with
    `desc+0x0C = 60 01 00 00`), always with a palette of ONE single color
    repeated 16x: `ffffff00` (transparent white) or `ffffff80` (opaque white).
    They are placeholders for a material that resolves in the shader, like
    `window`.

    `end` is the end of the useful region; without it, falls back to the
    CD-scan. Returns (w, h, bpp, color) or None."""
    e = end if end is not None else region_end_cdscan(data, px)
    if e - px != 96:
        return None
    pal = data[e - 64:e]
    color = bytes(pal[:4])
    if any(bytes(pal[i * 4:i * 4 + 4]) != color for i in range(16)):
        return None
    return (8, 8, 4, color)


def pf_image(data: bytes, px: int, dim) -> "Image":
    """Decode one texture of a pf05 pack from its descriptor.

    `descriptor_bytes` already documents the layout: the field is
    `pixels + CLUT + 0x100`, so a 256x256 reads 65536+1024+256. The palette
    therefore sits immediately AFTER the index block, and is swizzled the same
    way the flash .pck palettes are.
    """
    w, h = dim
    # The shape can come from `shapes_for_area`, which offers whatever factors
    # the pixel count allows - including 32x34 and 64x17. The GS swizzle is
    # defined on power-of-two pages only, so anything else is a wrong guess and
    # would walk off the end of the block.
    if w & (w - 1) or h & (h - 1) or w < 16 or h < 16:
        return None                       # PSMT8 swizzles on a 16x16 page
    idx_raw = data[px:px + w * h]
    pal_raw = bytes(data[px + w * h:px + w * h + 1024])
    if len(idx_raw) < w * h or len(pal_raw) < 1024:
        return None
    return Image(w, h, unswizzle8(idx_raw, w, h),
                 unswizzle_palette(pal_raw, 256),
                 texture_type=BPP8_BOTH_SWIZZLED, addr=px)


def pf_list(data: bytes) -> list:
    """[(page_index, pixels_offset, (w, h), descriptor)] with the page each
    texture belongs to, so a name can be built even though pf05 stores none."""
    pages = pf_pages(data)
    out = []
    for _nm, px, dim, desc in pf_textures(data):
        pg = None
        for i, off, sz in pages:
            if off <= px < off + sz:
                pg = i
                break
        out.append((pg, px, dim, desc))
    return out


def textures(data: bytes, base: int = 0x06800000, use_pointers: bool = True) -> list:
    """Full list: [(name, pixel_offset, (w,h) or None, desc_offset)].

    When the descriptor code does not give the size, the region is measured
    through the pointer graph (the `next_target` method) and only falls back to
    the CD-scan if that does not add up."""
    names = texture_names(data, base)
    graph = None
    out = []
    for desc, px, fmt, ptr in find_descriptors(data):
        info = ptr - base + 0x80 - INFO_TO_DESC
        dim = descriptor_size(fmt)
        if dim is None:
            s = None
            if use_pointers and PointerGraph is not None:
                if graph is None:                  # build once per file
                    graph = PointerGraph(data, base - 0x80)
                _st, e, _n = graph.block_bounds(px)
                if e is not None:
                    s = probe_small(data, px, end=e)
            if s is None:                          # fallback
                s = probe_small(data, px)
            if s:
                dim = (s[0], s[1])
        nm = names.get(info) or name_before_descriptor(data, desc)
        out.append((nm, px, dim, desc))
    return out


def _coherence(data: bytes, off: int, w: int, h: int, pal: bytes,
               step: int = 4):
    """(mean difference between neighbouring pixels, number of distinct indices).

    A real texture has similar neighbours -- but so does a flat region, which is
    why that alone is not a criterion: something with a single color scores 0 and
    would beat any real image. Hence the variety is returned too."""
    m = _swizzle8_map(w, h)
    tot = cnt = 0
    seen = set()
    for y in range(0, h, step):
        row = y * w
        prev = None
        for x in range(0, w, 2):
            s = off + m[row + x]
            if s >= len(data):
                return 1e9, 0
            e = data[s]
            seen.add(e)
            c = (pal[e * 4], pal[e * 4 + 1], pal[e * 4 + 2])
            if prev is not None:
                tot += abs(c[0] - prev[0]) + abs(c[1] - prev[1]) + abs(c[2] - prev[2])
                cnt += 1
            prev = c
    return (tot / cnt if cnt else 1e9), len(seen)


def find_blocks(data: bytes, sizes=((256, 256), (256, 128), (128, 128),
                                    (128, 64), (64, 64)),
                colors: int = 256, max_cd: float = 0.5,
                min_variety: int = 24) -> list:
    """Find texture blocks without needing the ImHex offset.

    Locates 256-color CLUTs (alpha <= 0x80) and steps back `w*h` bytes -- the
    'MipMap count 0' case of Console Texture Explorer, which is what vehicle .pck
    files use. Since a texture region can also "look like" a palette, every
    candidate is scored by the spatial coherence of the decoded image and
    overlapping ones are resolved by the best score.

    Returns [(graphics_offset, w, h, palette_offset, score)], best first.
    """
    cands, o, n = [], 0, len(data)
    while o < n - colors * 4:
        A = data[o + 3: o + colors * 4: 4]
        if len(A) == colors and max(A) <= 0x80 and len(set(A)) > 2:
            pal = unswizzle_palette(bytes(data[o:o + colors * 4]), colors)
            for w, h in sizes:
                start = o - w * h
                if start < 0x80:
                    continue
                blk = data[start:o]
                if blk.count(0xCD) / len(blk) > max_cd:
                    continue
                coh, variety = _coherence(data, start, w, h, pal)
                if variety < min_variety:
                    continue          # too flat to be the texture
                # area first (the car texture is always the biggest), coherence
                # only to break ties between candidates of the same size
                cands.append((-(w * h), coh, start, w, h, o))
            o += 16
        else:
            o += 16

    cands.sort()
    out, taken = [], []
    for _area, score, start, w, h, po in cands:
        end = po + colors * 4
        if any(start < b and a < end for a, b in taken):
            continue
        taken.append((start, end))
        out.append((start, w, h, po, round(score, 2)))
    return out


def read_block(data: bytes, offset: int, width: int = 256, height: int = 256,
               levels: int = 4, pad: int = MIP_PAD, colors: int = 256,
               swizzled: bool = True) -> Image:
    """Read a raw block. Returns level 0 with the rest in .mips."""
    lv, pal_rel = mip_layout(width, height, levels, pad)
    pal_raw = bytes(data[offset + pal_rel: offset + pal_rel + colors * 4])
    pal = unswizzle_palette(pal_raw, colors) if colors == 256 else bytearray(pal_raw)

    imgs = []
    for w, h, rel, n in lv:
        raw = data[offset + rel: offset + rel + n]
        idx = unswizzle8(raw, w, h) if swizzled else bytearray(raw)
        imgs.append(Image(w, h, idx, pal, texture_type=BPP8_BOTH_SWIZZLED if swizzled
                          else BPP8_INDEX_SWIZZLED, addr=offset + rel))
    top = imgs[0]
    top.mips = imgs[1:]
    return top


def write_block(data: bytearray, offset: int, mips: list, palette: bytes,
                pad: int = MIP_PAD, colors: int = 256, swizzled: bool = True):
    """Write in place. `mips` = list of Image (level 0 first), same palette."""
    w0, h0 = mips[0].width, mips[0].height
    lv, pal_rel = mip_layout(w0, h0, len(mips), pad)
    for im, (w, h, rel, n) in zip(mips, lv):
        if (im.width, im.height) != (w, h):
            raise ValueError("level %dx%d does not match the expected %dx%d"
                             % (im.width, im.height, w, h))
        buf = swizzle8(im.indexes, w, h) if swizzled else bytearray(im.indexes)
        data[offset + rel: offset + rel + n] = buf
    pal = bytearray(palette) + bytes(4 * (colors - len(palette) // 4))
    if colors == 256:
        pal = swizzle_palette(pal, colors)
    data[offset + pal_rel: offset + pal_rel + colors * 4] = pal
    return offset + pal_rel + colors * 4


def block_size(width: int = 256, height: int = 256, levels: int = 4,
               pad: int = MIP_PAD, colors: int = 256) -> int:
    _, pal_rel = mip_layout(width, height, levels, pad)
    return pal_rel + colors * 4


# --------------------------------------------------------------------------
# quantisation / remapping
# --------------------------------------------------------------------------
def quantize(rgba: bytes, width: int, height: int, colors: int = 256,
             alpha_max: int = 128):
    """RGBA (alpha 0..255) -> (PS2 palette with alpha 0..alpha_max, indices).

    Uses Pillow if available; without it, falls back to a simple median-cut.
    Works on RGB pre-multiplied by alpha so that a nearly invisible pixel does
    not end up mapped to a bright opaque color (the white speckle on the edges).
    """
    try:
        from PIL import Image as PILImage
        src = PILImage.frombytes("RGBA", (width, height), bytes(rgba))
        q = src.convert("RGB").quantize(colors=colors, method=2, dither=0)
        idx = bytearray(q.tobytes())
        rp = (q.getpalette() or [])[: colors * 3]
        rp += [0] * (colors * 3 - len(rp))
        # alpha of each entry = average of the alphas of the pixels landing on it
        acc = [0] * colors
        cnt = [0] * colors
        for i, e in enumerate(idx):
            acc[e] += rgba[i * 4 + 3]
            cnt[e] += 1
        pal = bytearray()
        for i in range(colors):
            a = round(acc[i] / cnt[i] * alpha_max / 255) if cnt[i] else 0
            r, g, b = rp[i * 3], rp[i * 3 + 1], rp[i * 3 + 2]
            if a == 0:
                r = g = b = 0
            pal += bytes((r, g, b, a))
        return pal, idx
    except ImportError:
        pass

    # fallback: median-cut over RGBA
    px = [(rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3])
          for i in range(0, len(rgba), 4)]
    boxes = [list(range(len(px)))]
    while len(boxes) < colors:
        big = max(boxes, key=len)
        if len(big) <= 1:
            break
        boxes.remove(big)
        ch = max(range(4), key=lambda c: max(px[i][c] for i in big)
                 - min(px[i][c] for i in big))
        big.sort(key=lambda i: px[i][ch])
        boxes += [big[:len(big) // 2], big[len(big) // 2:]]
    pal = bytearray()
    for bx in boxes:
        if not bx:
            pal += b"\0\0\0\0"
            continue
        n = len(bx)
        r = sum(px[i][0] for i in bx) // n
        g = sum(px[i][1] for i in bx) // n
        b = sum(px[i][2] for i in bx) // n
        a = round(sum(px[i][3] for i in bx) / n * alpha_max / 255)
        pal += bytes((r if a else 0, g if a else 0, b if a else 0, a))
    pal += b"\0" * (colors * 4 - len(pal))
    return pal, remap(rgba, pal, colors, alpha_max)


def remap(rgba: bytes, palette: bytes, colors: int = 256, alpha_max: int = 128):
    """Match every pixel to the nearest entry. Compares RGB pre-multiplied by
    alpha, otherwise a nearly transparent pixel matches opaque white."""
    ents = []
    for i in range(colors):
        r, g, b, a = palette[i * 4:i * 4 + 4]
        f = a / alpha_max
        ents.append((r * f, g * f, b * f, a))
    cache = {}
    out = bytearray(len(rgba) // 4)
    for n in range(len(out)):
        key = rgba[n * 4:n * 4 + 4]
        hit = cache.get(bytes(key))
        if hit is None:
            r, g, b, a8 = key
            a = a8 * alpha_max / 255
            f = a / alpha_max
            rr, gg, bb = r * f, g * f, b * f
            best, bd = 0, 1e30
            for i, (pr, pg, pb, pa) in enumerate(ents):
                dr, dg, db, da = rr - pr, gg - pg, bb - pb, a - pa
                d = dr * dr + dg * dg + db * db + da * da * 16
                if d < bd:
                    bd, best = d, i
            hit = best
            cache[bytes(key)] = hit
        out[n] = hit
    return out


def pck_slots(p: Pck) -> list:
    """(name, info_va, clut_va, texture_va, img) of every image."""
    out = []
    for im in flash_images(p):
        o = p.off(im.addr)
        out.append((im.name, im.addr, p.u32(o + 0x90), p.u32(o + 0x94), im))
    return out


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        prog="mc3_tex",
        description="Midnight Club 3 PS2 textures: flash folder .pck and .tex sources")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list", help="list the textures of a file")
    a.add_argument("file")

    b = sub.add_parser("extract", help="extract everything to PNG")
    b.add_argument("file")
    b.add_argument("-o", "--outdir", default=".")

    c = sub.add_parser("replace", help="swap an image of a .pck with a PNG")
    c.add_argument("pck")
    c.add_argument("name", help="image name (see 'list')")
    c.add_argument("png")
    c.add_argument("-o", "--out", help="destination (default: overwrite)")

    e = sub.add_parser("make", help="build a .tex from a PNG (power of two)")
    e.add_argument("png")
    e.add_argument("-o", "--out", help="destination .tex")
    e.add_argument("--fmt", type=int, default=14,
                   help="14 = 8bpp/256 with alpha (default), 16 = 4bpp/16")

    ns = ap.parse_args(argv)

    if ns.cmd == "make":
        t = png_to_tex(ns.png, ns.fmt)
        out = ns.out or os.path.splitext(ns.png)[0] + ".tex"
        write_tex(out, t)
        used_set = sum(1 for i in range(0, len(t.palette), 4)
                     if t.palette[i:i + 4] != bytes(4))
        print("%s  %dx%d  fmt %d (%s)  %d colour(s) used  %d bytes"
              % (out, t.width, t.height, t.fmt, TEX_FMT_NAME.get(t.fmt, "?"),
                 used_set, os.path.getsize(out)))
        return 0

    f = ns.file if ns.cmd != "replace" else ns.pck

    if f.lower().endswith(".tex"):
        t = read_tex(f)
        if ns.cmd == "list":
            print("%s  %dx%d  fmt %d (%s)  %d mip(s)"
                  % (os.path.basename(f), t.width, t.height, t.fmt,
                     TEX_FMT_NAME.get(t.fmt, "?"), t.mipmaps))
            for i, (w, h, d) in enumerate(t.levels):
                print("    mip %d: %dx%d  %d bytes" % (i, w, h, len(d)))
        elif ns.cmd == "extract":
            os.makedirs(ns.outdir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(f))[0]
            for i in range(t.mipmaps):
                img = tex_to_image(t, i)
                # the .tex already stores alpha 0..255, so do not rescale
                name = stem if i == 0 else "%s_mip%d" % (stem, i)
                img.save_png(os.path.join(ns.outdir, name + ".png"), fix_alpha=False)
                print("  ->", name + ".png")
        else:
            ap.error("'replace' only applies to .pck")
        return 0

    with open(f, "rb") as fh:
        if fh.read(4) == PF_MAGIC:
            data = open(f, "rb").read()
            entries_ = pf_list(data)
            if ns.cmd == "replace":
                ap.error("'replace' does not work for pf05 yet")
            print("%s  pf05  %d page(s), %d texture(s)"
                  % (os.path.basename(f), len(pf_pages(data)), len(entries_)))
            if ns.cmd == "list":
                for pg, px, dim, desc in entries_[:200]:
                    print("    page %-4s @0x%-8X %s"
                          % (pg, px, ("%dx%d" % dim) if dim else "size ?"))
                if len(entries_) > 200:
                    print("    ... and %d more" % (len(entries_) - 200))
            else:
                os.makedirs(ns.outdir, exist_ok=True)
                n = 0
                for pg, px, dim, desc in entries_:
                    if not dim:
                        continue
                    img = pf_image(data, px, dim)
                    if img is None:
                        continue
                    item_name = "p%03d_%08X_%dx%d" % (pg or 0, px, dim[0], dim[1])
                    img.save_png(os.path.join(ns.outdir, item_name + ".png"))
                    n += 1
                print("  %d PNG(s) em %s" % (n, ns.outdir))
            return 0

    p = Pck(f)
    slots = pck_slots(p)
    if ns.cmd == "list":
        print("%s  base=%08x  %d image(s)" % (os.path.basename(f), p.base, len(slots)))
        for name, info, clut, tex, im in slots:
            print("  %-12s %4dx%-4d %3dc  %-34s info=%08x"
                  % (name, im.width, im.height, im.colors,
                     TEXTYPE_NAME.get(im.texture_type, "?"), info))
    elif ns.cmd == "extract":
        os.makedirs(ns.outdir, exist_ok=True)
        for name, info, clut, tex, im in slots:
            im.save_png(os.path.join(ns.outdir, name + ".png"))
        print("%d PNG(s) em %s" % (len(slots), ns.outdir))
    elif ns.cmd == "replace":
        ap.error("replace needs a palette quantizer; use the GUI")
    return 0


__all__ = [
    "Image", "TexFile", "Pck",
    "read_tex", "write_tex", "tex_to_image",
    "read_ps2_texture", "write_ps2_texture",
    "flash_images", "scan_textures", "find_cluts",
    "swizzle8", "unswizzle8", "swizzle_palette", "unswizzle_palette",
    "pack_4bpp", "unpack_4bpp", "write_png", "read_png",
    "BPP4_UNSWIZZLED", "BPP8_INDEX_SWIZZLED", "BPP8_BOTH_SWIZZLED", "TEXTYPE_NAME",
    "TEX_FMT_BPP", "TEX_FMT_NAME", "pck_slots",
    "find_descriptors", "descriptor_size", "texture_names", "textures",
    "read_block", "write_block", "block_size", "mip_layout", "probe_small",
    "decode_texture", "replace_texture", "quantize", "remap",
    "trace_texture", "block_end_by_pointer", "region_end", "region_end_cdscan",
]


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
