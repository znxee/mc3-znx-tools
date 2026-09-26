"""Restore graph-addressed VIF from the original immutable heap, never infer pointers from values."""
import argparse, hashlib, json, struct
from pathlib import Path
from city_vif_recenter import pck_packets, decode_positions

def raw_packets(d, base, root):
    def u(o): return struct.unpack_from('<I', d, o)[0]
    def h(o): return struct.unpack_from('<H', d, o)[0]
    def off(va, size):
        o=va-base
        assert 0 <= o <= len(d)-size, (hex(va),size)
        return o
    ro=off(root,24); ng=h(ro+8); geo=off(u(ro+16),ng*8)
    for g in range(ng):
        n=h(geo+8*g+4); slots=off(u(geo+8*g),n*8)
        for k in range(n):
            va,qw,nv=struct.unpack_from('<IHH',d,slots+8*k)
            if qw and nv:
                yield g,k,off(va,qw*16),qw*16,nv

def main():
    a=argparse.ArgumentParser();a.add_argument('--toolkit',type=Path,required=True);a.add_argument('--out',type=Path,required=True);args=a.parse_args()
    t=args.toolkit; heap=(t/'output/heap_la729.bin').read_bytes();base=0x017BC680
    # All allocation backlinks corroborate this base, not only the first one.
    prev=None;o=0;blocks=0
    while o+16<=len(heap):
        link,size=struct.unpack_from('<II',heap,o)
        if not size: break
        if prev is not None: assert (link&~7)==base+prev,(hex(o),hex(link),hex(base+prev))
        prev=o;o+=16+((size+15)&~15);blocks+=1
    assert o==len(heap),(o,len(heap))
    args.out.mkdir(parents=True,exist_ok=False)
    report={'heap_sha256':hashlib.sha256(heap).hexdigest(),'heap_base':hex(base),'allocation_blocks':blocks,'models':[]}
    for line in (t/'output/la729_lote.tsv').read_text().splitlines():
        root,path=line.split('\t');src=t/path;data=src.read_bytes();out=bytearray(data)
        old=list(pck_packets(data));raw=list(raw_packets(heap,base,int(root,16)))
        assert len(old)==len(raw),src.name
        changes=[];verts=0
        for (g,k,po,sz,nv),(hg,hk,ho,hs,hn) in zip(old,raw):
            assert (g,k,sz,nv)==(hg,hk,hs,hn),src.name
            original=heap[ho:ho+sz];current=data[po:po+sz]
            decoded,_=decode_positions(original);assert len(decoded)==nv,(src.name,g,k,len(decoded),nv)
            verts+=nv
            for w in range(0,sz,4):
                if original[w:w+4]!=current[w:w+4]:
                    changes.append({'group':g,'packet':k,'packet_offset':w,'old':current[w:w+4].hex(),'restored':original[w:w+4].hex()})
            out[po:po+sz]=original
        (args.out/src.name).write_bytes(out)
        report['models'].append({'name':src.name,'packets':len(old),'vertices':verts,'changes':changes,'source_sha256':hashlib.sha256(data).hexdigest(),'restored_sha256':hashlib.sha256(out).hexdigest()})
    report['changed_models']=sum(bool(x['changes']) for x in report['models'])
    report['changed_words']=sum(len(x['changes']) for x in report['models'])
    report['vertices']=sum(x['vertices'] for x in report['models'])
    (args.out/'restore_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='models'},indent=2))
if __name__=='__main__':main()
