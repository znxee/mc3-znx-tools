"""
mc2_tex_gs.py - MC2 PS2 textures (TEX0 + MIP0 + PAL0) as GS uploads

A TEX0 block (192 bytes) names a texture and points at two more blocks:
    +0x88 u16 width, u16 height
    +0x90 u32 handle of the PAL0     +0x94 u32 handle of the MIP0
    +0xA0 name, NUL-terminated
MIP0: 0x60-byte header, then the mip levels back to back; level sizes are u16
      counts of 256-byte units at +0x48. +0x28 low byte says 5 = 8bpp, 6 = 4bpp.
PAL0: 16-byte header, then 16 or 256 RGBA words (alpha 0x80 = opaque). The 256
      colour palette is stored in CLUT-swizzled order, i.e. ready to upload raw.

The texel data is the memory image of a PSMCT32 RECTANGLE upload, not a linear
image (measured: 8bpp reproduces mc3_tex.unswizzle8 exactly through the model
below; the stream is what a PSMCT32 rect of w/2 x h/2 words puts into PSMT8
memory). For 4bpp the rectangle is whatever covers GS blocks 0..n-1 of the
PSMT4 page: (across, down) blocks = 1:(1,1) 2:(2,1) 4:(2,2) 8:(4,2) 16:(4,4)
32:(8,4), each block 8x8 words.

This module decodes level 0 to one byte per texel and builds the GIF packets
that upload it (as PSMT8/PSMT4, letting the GS do the swizzle) plus its CLUT.
"""

import struct

blockTable32 = [[0,1,4,5,16,17,20,21],[2,3,6,7,18,19,22,23],[8,9,12,13,24,25,28,29],[10,11,14,15,26,27,30,31]]
blockTable4  = [[0,2,8,10],[1,3,9,11],[4,6,12,14],[5,7,13,15],[16,18,24,26],[17,19,25,27],[20,22,28,30],[21,23,29,31]]
columnTable32 = [[0,1,4,5,8,9,12,13],[2,3,6,7,10,11,14,15],[16,17,20,21,24,25,28,29],[18,19,22,23,26,27,30,31],
                 [32,33,36,37,40,41,44,45],[34,35,38,39,42,43,46,47],[48,49,52,53,56,57,60,61],[50,51,54,55,58,59,62,63]]
columnTable8 = [
 [0,4,16,20,32,36,48,52,2,6,18,22,34,38,50,54],[8,12,24,28,40,44,56,60,10,14,26,30,42,46,58,62],
 [33,37,49,53,1,5,17,21,35,39,51,55,3,7,19,23],[41,45,57,61,9,13,25,29,43,47,59,63,11,15,27,31],
 [96,100,112,116,64,68,80,84,98,102,114,118,66,70,82,86],[104,108,120,124,72,76,88,92,106,110,122,126,74,78,90,94],
 [65,69,81,85,97,101,113,117,67,71,83,87,99,103,115,119],[73,77,89,93,105,109,121,125,75,79,91,95,107,111,123,127],
 [128,132,144,148,160,164,176,180,130,134,146,150,162,166,178,182],[136,140,152,156,168,172,184,188,138,142,154,158,170,174,186,190],
 [161,165,177,181,129,133,145,149,163,167,179,183,131,135,147,151],[169,173,185,189,137,141,153,157,171,175,187,191,139,143,155,159],
 [224,228,240,244,192,196,208,212,226,230,242,246,194,198,210,214],[232,236,248,252,200,204,216,220,234,238,250,254,202,206,218,222],
 [193,197,209,213,225,229,241,245,195,199,211,215,227,231,243,247],[201,205,217,221,233,237,249,253,203,207,219,223,235,239,251,255]]
r0=[0,8,32,40,64,72,96,104,2,10,34,42,66,74,98,106,4,12,36,44,68,76,100,108,6,14,38,46,70,78,102,110]
r1=[16,24,48,56,80,88,112,120,18,26,50,58,82,90,114,122,20,28,52,60,84,92,116,124,22,30,54,62,86,94,118,126]
r2=[65,73,97,105,1,9,33,41,67,75,99,107,3,11,35,43,69,77,101,109,5,13,37,45,71,79,103,111,7,15,39,47]
r3=[81,89,113,121,17,25,49,57,83,91,115,123,19,27,51,59,85,93,117,125,21,29,53,61,87,95,119,127,23,31,55,63]
def _rot(r): return [r[g*8+((k+4)&7)] for g in range(4) for k in range(8)]
columnTable4=[r0,r1,r2,r3]+[[v+128 for v in _rot(r)] for r in (r0,r1,r2,r3)]
columnTable4=columnTable4+[[v+256 for v in r] for r in columnTable4]   # 16 rows x 32



def addr32(wx, wy, dbw):
    page = (wy >> 5) * dbw + (wx >> 6)
    blk = blockTable32[(wy >> 3) & 3][(wx >> 3) & 7]
    return page * 2048 + blk * 64 + columnTable32[wy & 7][wx & 7]


_RECT4 = {1: (1, 1), 2: (2, 1), 4: (2, 2), 8: (4, 2), 16: (4, 4), 32: (8, 4)}


def decode(stream, w, h, bpp):
    """Level-0 stream -> bytearray of w*h palette indices."""
    if bpp == 4 and w == 16 and h == 16:
        # The MC2 16x16 light/office textures store 128 packed bytes in
        # coherent row order; treating them as a 256-byte GS block scrambles
        # the image and reads beyond MIP0.
        if len(stream) < 128:
            raise ValueError('short 16x16 PSMT4 mip')
        return bytearray((stream[i >> 1] >> (4 * (i & 1))) & 15
                         for i in range(256))
    if bpp == 8:
        wr, hr = w // 2, h // 2
    else:
        across, down = _RECT4[w * h // 512]
        wr, hr = across * 8, down * 8
    dbw = max(1, (wr + 63) // 64)
    words = struct.unpack('<%dI' % (len(stream) // 4), stream)
    mem = {}
    for wy in range(hr):
        for wx in range(wr):
            mem[addr32(wx, wy, dbw)] = words[wy * wr + wx]
    out = bytearray(w * h)
    pagesx = max(1, (w // 64) // 2)
    for y in range(h):
        for x in range(w):
            if bpp == 8:
                page = (y >> 6) * pagesx + (x >> 7)
                blk = blockTable32[(y >> 4) & 3][(x >> 4) & 7]
                off = page * 8192 + blk * 256 + columnTable8[y & 15][x & 15]
                out[y * w + x] = (mem.get(off // 4, 0) >> (8 * (off & 3))) & 255
            else:
                page = (y >> 7) * pagesx + (x >> 7)
                blk = blockTable4[(y >> 4) & 7][(x >> 5) & 3]
                nib = page * 16384 + blk * 512 + columnTable4[y & 15][x & 31]
                out[y * w + x] = (mem.get(nib // 8, 0) >> (4 * (nib & 7))) & 15
    return out


class Texture(object):
    __slots__ = ('name', 'w', 'h', 'bpp', 'pixels', 'clut')


def white():
    """A 16x16 texture whose only colour is 0x80 white: TFX=MODULATE then gives back the
    vertex colour, i.e. "no texture". Handle 0 in a PMD0/PCP0 call means exactly that."""
    t = Texture()
    t.name, t.w, t.h, t.bpp = 'white', 16, 16, 8
    t.pixels = bytearray(16 * 16)
    t.clut = struct.pack('<I', 0x80808080) + bytes(1020)
    return t


def load(rsc, handle):
    got = rsc.resolve(handle)
    if got is None or got[0].entries[got[1]][1] != 'TEX0':
        raise ValueError('handle %#x is not a TEX0' % handle)
    box, index = got
    b = box.block(index)
    t = Texture()
    t.name = b[0xA0:0xE0].split(bytes(1))[0].decode('ascii', 'replace')
    t.w, t.h = struct.unpack_from('<HH', b, 0x88)
    hpal, hmip = struct.unpack_from('<II', b, 0x90)
    mip = box.resolve(hmip)
    pal = box.resolve(hpal)
    mb = mip[0].block(mip[1])
    pb = pal[0].block(pal[1])
    t.bpp = 8 if (mb[0x28] & 15) == 5 else 4
    n = t.w * t.h * t.bpp // 8
    t.pixels = decode(mb[0x60:0x60 + n], t.w, t.h, t.bpp)
    t.clut = pb[0x10:0x10 + (256 if t.bpp == 8 else 16) * 4]
    return t


# ---------------------------------------------------------------------------
# GIF packets
# ---------------------------------------------------------------------------

BITBLTBUF, TRXPOS, TRXREG, TRXDIR, TEXFLUSH = 0x50, 0x51, 0x52, 0x53, 0x3F
TEX0_1, TEX1_1, CLAMP_1 = 0x06, 0x14, 0x08
PSMCT32, PSMT8, PSMT4 = 0x00, 0x13, 0x14


def ad(words, value, reg):
    words += [value & 0xFFFFFFFF, value >> 32, reg, 0]


def gif_tag(nloop, flg, nreg=1, regs=0xE, eop=1):
    return [nloop | (eop << 15), (flg << 26) | (nreg << 28), regs, 0]


def upload(words, dbp, dbw, psm, w, h, payload):
    """Append one host->local transfer of `payload` (bytes, 16-byte multiple)."""
    words += gif_tag(4, 0, eop=0)
    ad(words, dbp << 32 | dbw << 48 | psm << 56, BITBLTBUF)
    ad(words, 0, TRXPOS)
    ad(words, w | h << 32, TRXREG)
    ad(words, 0, TRXDIR)
    words += gif_tag(len(payload) // 16, 2)
    words += list(struct.unpack('<%dI' % (len(payload) // 4), payload))


def pack_pixels(t):
    if t.bpp == 8:
        data = bytes(t.pixels)
    else:
        data = bytes(t.pixels[i] | (t.pixels[i + 1] << 4) for i in range(0, len(t.pixels), 2))
    return data + bytes(1) * (-len(data) % 16)


def upload_words(t, tbp, cbp):
    """The GIF packet that puts texel data at `tbp` and the CLUT at `cbp`."""
    words = []
    tbw = max(1, t.w // 64)
    upload(words, tbp, tbw, PSMT8 if t.bpp == 8 else PSMT4, t.w, t.h, pack_pixels(t))
    if t.bpp == 8:
        upload(words, cbp, 1, PSMCT32, 16, 16, t.clut)          # 256 words as a 16x16 rect
    else:
        upload(words, cbp, 1, PSMCT32, 8, 2, t.clut)            # 16 words as an 8x2 rect
    return words


def log2(n):
    return n.bit_length() - 1


def tex0(t, tbp, cbp):
    tbw = max(1, t.w // 64)
    psm = PSMT8 if t.bpp == 8 else PSMT4
    return (tbp | tbw << 14 | psm << 20 | log2(t.w) << 26 | log2(t.h) << 30 |
            1 << 34 |                    # TCC: RGBA
            0 << 35 |                    # TFX: MODULATE
            cbp << 37 | PSMCT32 << 51 | 0 << 55 | 0 << 56 | 1 << 61)   # CSM1, CSA 0, CLD 1


def switch_words(t, tbp, cbp):
    """A one-shot packet, meant for VIF DIRECT: select this texture."""
    words = gif_tag(2, 0)
    ad(words, tex0(t, tbp, cbp), TEX0_1)
    ad(words, 0, TEXFLUSH)
    return words
