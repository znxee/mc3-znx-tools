"""Independent check of candidate PPF and manifest against native heap dumps."""
import json
from pathlib import Path
from inspect_contract import ROOT, native_texture, u32
from audit_city_graph import audit_ppf

def main():
    candidate=ROOT/'candidate/losangeles_midnight_clear.ppf'
    data=candidate.read_bytes()
    manifest=json.loads(candidate.with_suffix('.manifest.json').read_text())
    byname={x['texture'].lower():x for x in manifest['textures']}
    identity=json.loads((ROOT/'identity_audit.json').read_text())
    count=0;palettes=0
    for r in identity['records']:
        n=native_texture(Path(r['dump']))
        m=byname[r['correct_source'].lower()]
        assert n['mips']==len(m['levels'])
        assert n['clut_qwords']==m['native_clut_offset_qwords']
        assert n['clut256']==m['native_clut256']
        for k,l in enumerate(n['levels']):
            c=m['levels'][k]; e=u32(data,12+4*c['page'])
            at=(e&0x7ffff)*0x800+c['page_offset']+0x90
            assert data[at:at+c['pixel_bytes']]==l['pixels'],(r['correct_source'],k,'pixels')
            assert c['native_mip_descriptor_hex']==l['descriptor'],(r['correct_source'],k,'descriptor')
            if k==n['mips']-1:
                assert data[at+c['pixel_bytes']:at+c['pixel_bytes']+len(n['palette'])]==n['palette']
                palettes+=1
            count+=1
    result=audit_ppf(candidate)
    assert not result['errors'] and result['sha256']==manifest['sha256']
    result.update({'native_pixel_payloads_match':count,'native_descriptors_match':count,
                   'native_palettes_match':palettes,'runtime_tested':False})
    (ROOT/'candidate_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
