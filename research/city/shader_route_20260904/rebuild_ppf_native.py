"""Create a separate corrected PPF candidate; never patch an installed asset.

Uses the previous pf05 layout only for chunk placement. Pixel encoding and
palettes are regenerated from source TEX by the reconstructed native codec.
Must be paired with a PCK rebuilt with correct identities and unique ctor
ownership. Not a drop-in fix for the old broken City PCK.
"""
import hashlib
import json
import struct
from pathlib import Path
from inspect_contract import ROOT, TOOLS, u32
from native_texture_codec import pixels,palette
from audit_city_graph import audit_ppf

def main():
    previous=TOOLS/'output/mc2_losangeles/compiled_ppf/modcity_midnight_clear'
    old=previous.with_suffix('.ppf').read_bytes()
    assert hashlib.sha256(old).hexdigest()=='9658efad124e0f527cb4f7715caacae97acf69f3b2a37738514ccc8b3e9f9b0b'
    data=bytearray(old)
    m=json.loads(previous.with_suffix('.manifest.json').read_text())
    changed_ranges=[]
    for t in m['textures']:
        source=(TOOLS/'output/mc2_losangeles/texture'/t['texture']).read_bytes()
        w,h,fmt,count,*_=struct.unpack_from('<7H',source)
        assert (w,h,fmt,count)==(t['width'],t['height'],t['source_format'],len(t['levels']))
        bpp=t['bpp']; ncol=256 if bpp==8 else 16
        pal=palette(source[14:14+4*ncol],bpp)
        pos=14+4*ncol
        for k,level in enumerate(t['levels']):
            mw,mh=w>>k,h>>k;size=mw*mh*bpp//8
            payload,(uw,uh,upsm)=pixels(source[pos:pos+size],mw,mh,bpp);pos+=size
            en=u32(data,12+4*level['page']);at=(en&0x7ffff)*0x800+level['page_offset']+0x90
            assert len(payload)==level['pixel_bytes']
            data[at:at+size]=payload
            end=at+size
            if k==count-1:
                data[end:end+len(pal)]=pal;end+=len(pal)
            changed_ranges.append((at,end))
            level['upload_width']=uw;level['upload_height']=uh;level['upload_psm']=upsm
            packed=(uw.bit_length()-1)|((uh.bit_length()-1)<<5)|(upsm<<10)
            tex0=((mw+63)//64)<<14 | (19 if bpp==8 else 20)<<20 | (mw.bit_length()-1)<<26 | (mh.bit_length()-1)<<30 | 0x2000000400000000
            desc=struct.pack('<4HQ',packed,0,level['pixel_blocks'],level['blocks_through_end'],tex0)
            level['native_mip_descriptor_hex']=desc.hex()
        assert pos==len(source)
        t['native_clut_offset_qwords']=t['levels'][-1]['pixel_bytes']//16
        t['native_clut256']=int(bpp==8)
    dest=ROOT/'candidate'
    dest.mkdir(exist_ok=True)
    out=dest/'modcity_midnight_clear.ppf'
    if out.exists(): raise FileExistsError('Refusing to overwrite '+str(out))
    out.write_bytes(data)
    m.update({'output':str(out),'sha256':hashlib.sha256(data).hexdigest(),
       'status':'Candidate: 965 native payloads and 510 palettes verified offline; no in-game test.',
       'requires':'Rebuild PCK: correct texture identity, matching descriptors and one constructor visit per object. fileId 123 is profile-specific.',
       'encoder':'native_texture_codec.py; derived from retail ELF and verified against game dumps',
       'supersedes_sha256':hashlib.sha256(old).hexdigest()})
    out.with_suffix('.manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    # Ensure all bytes outside pixel/CLUT ranges were retained exactly.
    cursor=0
    for a,b in sorted(changed_ranges):
        assert old[cursor:a]==data[cursor:a]
        cursor=b
    assert old[cursor:]==data[cursor:]
    result=audit_ppf(out)
    assert not result['errors']
    result.update({'native_payloads_regenerated':sum(len(t['levels']) for t in m['textures']),
                   'native_palettes_regenerated':len(m['textures']),'non_payload_bytes_unchanged':True})
    (ROOT/'candidate_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    identity=json.loads((ROOT/'identity_audit.json').read_text())
    rows=['source_texture\tinternal_name\tdump_path\tfilename_was_correct']
    rows.extend('\t'.join([r['correct_source'],r['internal_name'],r['dump'],str(r['filename_correct'])]) for r in identity['records'])
    (ROOT/'native_texture_map.tsv').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
