"""Read-only comparison: source TEX, engine-produced TexturePS2 and compiled PPF."""
import os
import collections
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools'))
u16 = lambda b, o: struct.unpack_from('<H', b, o)[0]
u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]
u64 = lambda b, o: struct.unpack_from('<Q', b, o)[0]

def off(data, ptr):
    return ptr - u32(data, 0) + 0x80

def native_texture(path):
    d = path.read_bytes()
    t = d[0x80:0x120]
    levels = []
    for k in range(t[0x86]):
        desc = t[8+16*k:24+16*k]
        packed = u16(desc, 0)
        tex0 = u64(desc, 8)
        ref = struct.unpack_from('<III', t, 0x48+12*k)
        chunk = off(d, ref[0])
        transfer = {'width':1 << (packed & 31), 'height':1 << ((packed >> 5)&31), 'psm':packed >> 10}
        count = transfer['width']*transfer['height']*{0:32,2:16,19:8,20:4}[transfer['psm']]//8
        levels.append({'ref':ref, 'chunk_offset':chunk,'chunk_size':u32(d,chunk+12),
                       'descriptor':desc.hex(), 'transfer':transfer, 'transfer_bytes':count,
                       'sample': {'width':1 << ((tex0>>26)&15),'height':1 << ((tex0>>30)&15),
                                  'psm':(tex0>>20)&63, 'tbw':(tex0>>14)&63},
                       'blocks':u16(desc,4),'cumulative_blocks':u16(desc,6),
                       'pixels':d[chunk+0x90:chunk+0x90+count]})
    palette_start = levels[-1]['chunk_offset']+0x90+16*u16(t,0x80)
    palette = d[palette_start:palette_start+(1024 if t[0x84] else 64)]
    return {'name':path.name,'base':u32(d,0),'mips':t[0x86],'clut_qwords':u16(t,0x80),
            'clut256':t[0x84],'clamp':t[0x85], 'levels':levels,'palette':palette,
            'sha256':hashlib.sha256(d).hexdigest()}

def source_palette_to_native(raw, count, scale=128):
    out = bytearray(len(raw))
    for i in range(count):
        dest = (i & ~0x18) | ((i >> 1)&8) | ((i << 1)&16) if count==256 else i
        b,g,r,a = raw[4*i:4*i+4]
        out[4*dest:4*dest+4] = bytes((r,g,b,a*scale//255))
    return bytes(out)

def main():
    compiled=TOOLS/'output/mc2_losangeles/compiled_ppf/losangeles_midnight_clear'
    manifest=json.loads(compiled.with_suffix('.manifest.json').read_text())
    ppf=compiled.with_suffix('.ppf').read_bytes()
    counts=collections.Counter()
    records=[]
    for m in manifest['textures']:
        source=(TOOLS/'output/mc2_losangeles/texture'/m['texture']).read_bytes()
        n=native_texture(TOOLS/'output/la_tex_pck'/(m['texture']+'.pck'))
        color_count=256 if m['bpp']==8 else 16
        expected_palette=source_palette_to_native(source[14:14+4*color_count],color_count)
        n['source_format']=m['source_format']
        n['native_palette_matches_BGRA_scale128']=n['palette']==expected_palette
        counts['textures']+=1
        counts['palette_native_matches_BGRA_scale128']+=n['palette']==expected_palette
        counts['mip_count_matches']+=n['mips']==len(m['levels'])
        for k,l in enumerate(n['levels']):
            if k>=len(m['levels']):
                counts['missing_ppf_mips']+=1
                l['pixels_sha256']=hashlib.sha256(l.pop('pixels')).hexdigest()
                continue
            ml=m['levels'][k]
            entry=u32(ppf,12+4*ml['page'])
            page_file_off=(entry&0x7ffff)*0x800
            pc=page_file_off+ml['page_offset']
            old_pixels=ppf[pc+0x90:pc+0x90+ml['pixel_bytes']]
            l['old_ppf_pixels_match_native']=old_pixels==l['pixels']
            l['old_ppf_size_matches_transfer']=ml['pixel_bytes']==l['transfer_bytes']
            counts['mips']+=1
            counts['pixels_match_native']+=old_pixels==l['pixels']
            counts['pixels_differ_bpp'+str(m['bpp'])]+=old_pixels!=l['pixels']
            counts['transfer_size_matches']+=ml['pixel_bytes']==l['transfer_bytes']
            l['pixels_sha256']=hashlib.sha256(l.pop('pixels')).hexdigest()
            if k==n['mips']-1:
                old_pal=ppf[pc+0x90+ml['pixel_bytes']:pc+0x90+ml['pixel_bytes']+4*color_count]
                n['old_ppf_palette_matches_native']=old_pal==n['palette']
                n['old_ppf_clut_position_matches_native']=ml['pixel_bytes']==16*n['clut_qwords']
                counts['old_ppf_palette_matches_native']+=old_pal==n['palette']
                counts['clut_position_matches']+=ml['pixel_bytes']==16*n['clut_qwords']
        n['palette_sha256']=hashlib.sha256(n.pop('palette')).hexdigest()
        records.append(n)
    index=json.loads((ROOT/'route_index.json').read_text())
    result={'counts':dict(counts),'ppf_sha256':hashlib.sha256(ppf).hexdigest(),'records':records,
            'decompilation':{'functions':len(index['functions']),
                'errors':[r for r in index['functions'] if r['error']]},
            'global_initial_bytes':{'%08X'%a:bytes.fromhex(index['data'][base])[a-int(base,16)]
                 for a,base in [(0x61c294,'0061C200'),(0x615fe1,'00615D00'),(0x615ffc,'00615D00')]}}
    (ROOT/'texture_contract_audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2))
    print('First example:',json.dumps(records[0],indent=2))

if __name__=='__main__': main()
