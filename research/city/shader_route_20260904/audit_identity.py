"""Audit native dumps using their embedded tNNN name, never filename order."""
import collections
import json
import struct
from pathlib import Path
from inspect_contract import ROOT, TOOLS, native_texture, off, u32, source_palette_to_native

def main():
    mapping={int(k):v for k,v in (line.split('\t') for line in (TOOLS/'output/la_tex_mapa.tsv').read_text().splitlines())}
    comp=TOOLS/'output/mc2_losangeles/compiled_ppf/losangeles_midnight_clear'
    ppf=comp.with_suffix('.ppf').read_bytes()
    manifest=json.loads(comp.with_suffix('.manifest.json').read_text())
    byname={m['texture'].lower():m for m in manifest['textures']}
    counts=collections.Counter()
    records=[]
    for path in sorted((TOOLS/'output/la_tex_pck').glob('*.tex.pck')):
        d=path.read_bytes()
        p=off(d,u32(d,0x80+0x78))
        internal=d[p:d.index(0,p)].decode('ascii')
        assert internal.startswith('t') and internal[1:].isdigit(), internal
        original=mapping[int(internal[1:])]+'.tex'
        m=byname[original.lower()]
        native=native_texture(path)
        source=(TOOLS/'output/mc2_losangeles/texture'/original).read_bytes()
        colors=256 if m['bpp']==8 else 16
        expected=source_palette_to_native(source[14:14+4*colors],colors)
        palette_ok=native['palette']==expected
        counts['textures']+=1
        counts['filename_matches_internal_identity']+=path.name.lower()==(original+'.pck').lower()
        counts['palette_matches_correct_source']+=palette_ok
        counts['mip_count_matches_correct_source']+=native['mips']==len(m['levels'])
        counts['dimensions_match_correct_source']+=native['levels'][0]['sample']=={'width':m['width'],'height':m['height'],'psm':19 if m['bpp']==8 else 20,'tbw':(m['width']+63)//64}
        rec={'dump':str(path),'internal_name':internal,'correct_source':original,
             'filename_correct':path.name.lower()==(original+'.pck').lower(),
             'palette_matches_correct_source':palette_ok,'levels':[]}
        for k,l in enumerate(native['levels']):
            ml=m['levels'][k]
            en=u32(ppf,12+4*ml['page'])
            at=(en&0x7ffff)*0x800+ml['page_offset']+0x90
            pix=ppf[at:at+ml['pixel_bytes']]
            same=pix==l['pixels']
            counts['mips']+=1
            counts['pixel_size_matches']+=ml['pixel_bytes']==l['transfer_bytes']
            counts['pixel_match_bpp'+str(m['bpp'])]+=same
            counts['pixel_differ_bpp'+str(m['bpp'])]+=not same
            if k==native['mips']-1:
                oldpal=ppf[at+ml['pixel_bytes']:at+ml['pixel_bytes']+4*colors]
                counts['old_ppf_palette_matches']+=oldpal==native['palette']
                rec['old_ppf_palette_matches']=oldpal==native['palette']
            rec['levels'].append({'level':k,'sample':l['sample'],'transfer':l['transfer'],
                                  'bytes':l['transfer_bytes'],'old_ppf_pixel_matches':same})
        records.append(rec)
    assert len(set(r['internal_name'] for r in records))==510
    (ROOT/'identity_audit.json').write_text(json.dumps({'counts':dict(counts),'records':records},indent=2),encoding='utf-8')
    print(json.dumps(counts,indent=2))
    print('First five mismatches:',[(Path(r['dump']).name,r['correct_source']) for r in records if not r['filename_correct']][:5])

if __name__=='__main__': main()
