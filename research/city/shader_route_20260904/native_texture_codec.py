"""Executable reconstruction of indexed TEX -> MC3 PS2 upload payload.

Address evidence: ReadPalette 524878; Init 2B7498; swizzle wrappers 522DB8 /
522F00; GS-memory write 522990 / 522B60; PSMCT32 read 5227F0. Tables are
read from the private IDA export of the retail ELF, not invented constants.
Scope: power-of-two 4/8-bpp source images with the aspect ratios accepted by
the texture constructor; no resampling, direct color or mip generation.
"""
from functools import lru_cache
import json
import struct
from pathlib import Path

ROOT=Path(__file__).resolve().parent

@lru_cache(None)
def table(address,count):
    raw=bytes.fromhex(json.loads((ROOT/'route_index.json').read_text())['data']['0061B000'])
    return struct.unpack_from('<%dI'%count,raw,address-0x61b000)

def palette(raw,bpp):
    count=256 if bpp==8 else 16
    assert len(raw)==4*count
    out=bytearray(len(raw))
    for i in range(count):
        j=(i&~0x18)|((i>>1)&8)|((i<<1)&16) if bpp==8 else i
        b,g,r,a=raw[4*i:4*i+4]
        out[4*j:4*j+4]=bytes((r,g,b,a*128//255))
    return bytes(out)

@lru_cache(None)
def mapping(w,h,bpp):
    """Return output geometry and a source pixel -> destination bit mapping."""
    assert bpp in (4,8) and w>0 and h>0 and w&(w-1)==0 and h&(h-1)==0
    if w*h*bpp//8<256: return w,h,19 if bpp==8 else 20,None
    uw,uh=(w//2,h//2) if bpp==8 else ((w//2,h//4) if w==h else (w//4,h//2))
    block32=table(0x61b220,32); column32=table(0x61b2a0,16)
    mem_to_output={}
    for y in range(uh):
        py,ly=divmod(y,32);by,ry=divmod(ly,8)
        for x in range(uw):
            px,lx=divmod(x,64);bx,rx=divmod(lx,8)
            word=(px+py*((uw+63)//64))*2048+block32[by*8+bx]*64+(ry//2)*16+column32[(ry%2)*8+rx]
            for b in range(4): mem_to_output[4*word+b]=(y*uw+x)*4+b
    if bpp==8:
        block=table(0x61b460,32); col=table(0x61b4e0,128); byte=table(0x61b6e0,64)
    else:
        block=table(0x61b7e0,32); col=table(0x61b860,256); nibble=table(0x61bc60,128)
    destinations=[]
    for y in range(h):
        py,ly=divmod(y,64 if bpp==8 else 128);by,ry=divmod(ly,16);cy,iy=divmod(ry,4)
        for x in range(w):
            px,lx=divmod(x,128);bx,rx=divmod(lx,16 if bpp==8 else 32)
            q=rx+(16 if bpp==8 else 32)*iy
            word=(px+py*(((w+63)//64)//2))*2048+block[(8 if bpp==8 else 4)*by+bx]*64+16*cy+col[(64 if bpp==8 else 128)*(cy&1)+q]
            sub=byte[q] if bpp==8 else nibble[q]//2
            shift=0 if bpp==8 else (nibble[q]&1)*4
            destinations.append((mem_to_output[4*word+sub],shift))
    # Mapping must be a bijection over pixels/nibbles.
    assert len(set(destinations))==w*h
    return uw,uh,0,tuple(destinations)

def pixels(raw,w,h,bpp):
    assert len(raw)==w*h*bpp//8
    uw,uh,psm,m=mapping(w,h,bpp)
    if m is None: return raw,(uw,uh,psm)
    out=bytearray(len(raw))
    if bpp==8:
        for value,(index,_) in zip(raw,m): out[index]=value
    else:
        for pixel,(index,shift) in enumerate(m):
            value=(raw[pixel//2]>>((pixel&1)*4))&15
            out[index]|=value<<shift
    return bytes(out),(uw,uh,psm)

def verify_native_corpus():
    from inspect_contract import TOOLS,native_texture
    identity=json.loads((ROOT/'identity_audit.json').read_text())
    result={'textures':0,'mips':0,'palette_matches':0,'pixel_matches':0,'geometry_matches':0,'failures':[]}
    for row in identity['records']:
        source=(TOOLS/'output/mc2_losangeles/texture'/row['correct_source']).read_bytes()
        w,h,fmt,levels,*_=struct.unpack_from('<7H',source)
        bpp=8 if fmt in (1,14) else 4
        ncol=256 if bpp==8 else 16
        native=native_texture(Path(row['dump']))
        pal=palette(source[14:14+4*ncol],bpp)
        result['textures']+=1
        result['palette_matches']+=pal==native['palette']
        at=14+4*ncol
        for k in range(levels):
            mw,mh=max(1,w>>k),max(1,h>>k);size=mw*mh*bpp//8
            payload,transfer=pixels(source[at:at+size],mw,mh,bpp);at+=size
            expected=native['levels'][k]
            result['mips']+=1
            same=payload==expected['pixels']
            result['pixel_matches']+=same
            dimensions=expected['transfer']
            result['geometry_matches']+=transfer==(dimensions['width'],dimensions['height'],dimensions['psm'])
            if not same: result['failures'].append([row['correct_source'],k])
    (ROOT/'codec_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    assert result['pixel_matches']==result['mips'] and result['palette_matches']==result['textures'] and result['geometry_matches']==result['mips']

if __name__=='__main__': verify_native_corpus()
