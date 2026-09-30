"""Validate every native VIF payload and forbid changes outside those payloads."""
import os
import collections, hashlib, itertools, json, math, struct, sys
from pathlib import Path
from city_vif_recenter import pck_packets, decode_positions
T=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'));W=Path(os.path.join(os.environ.get('MC3_WORK', '.'), 'output'))
sys.path.insert(0,str(T));import mc3_city_build as B
u=lambda d,o:struct.unpack_from('<I',d,o)[0]
h=lambda d,o:struct.unpack_from('<H',d,o)[0]
def main():
    old=(T/'output/losangeles_la_v6_alpha2004.pck').read_bytes()
    new=(W/'losangeles_la_v7_vif_local.pck').read_bytes();assert len(old)==len(new)
    base=u(new,0)
    def f(va,n=4):
        o=va-base+128;assert 128<=o<=len(new)-n;return o
    rows,_=B.read_place(str(T/'output/la729_place.tsv'))
    name=lambda r:('%s_%s_%s'%(r['name'],r['lod'],r['part'])).lower()
    source={p.name.lower():p for p in (T/'output/mc2_losangeles/model').glob('*.mod')}
    prepared={p.name.lower():p for p in (W/'la729_local_components_20260906').glob('*.mesh.pck')}
    rawdir={p.name.lower():p for p in (W/'la729_vif_restored_20260906').glob('*.mesh.pck')}
    allow=bytearray(len(new));count=collections.Counter();models=[];max_world_error=0
    for root,kind in ((0x194,'comp'),(0x198,'inst')):
        rr=[r for r in rows if r['kind']==kind]
        if kind=='inst':rr=list({name(r):r for r in rr}.values())
        chain=[];va=u(new,f(u(new,128+root))+4)
        while va:
            assert va not in chain;chain.append(va);va=u(new,f(va)+12)
        chain.reverse();assert len(chain)==len(rr)
        for va,r in zip(chain,rr):
            key=name(r);cd=f(va);mesh=f(u(new,cd+60));geo=f(u(new,mesh+16));ng=h(new,mesh+8)
            centre=struct.unpack_from('<3f',new,cd+44)
            pp=prepared[key+'.mesh.pck'].read_bytes();orig=rawdir[key+'.mesh.pck'].read_bytes()
            native=list(pck_packets(pp));heap_native=list(pck_packets(orig));ni=0
            vertices=[];maximum=0
            for g in range(ng):
                slots=f(u(new,geo+g*8));np=h(new,geo+g*8+4)
                for k in range(np):
                    ptr,qw,nv=struct.unpack_from('<IHH',new,slots+k*8);o=f(ptr,qw*16)
                    sg,sk,so,sz,sn=native[ni];_,_,ho,hs,hn=heap_native[ni];ni+=1
                    assert (g,k,qw*16,nv)==(sg,sk,sz,sn)
                    payload=new[o:o+sz];assert payload==pp[so:so+sz],(key,g,k,'builder changed VIF')
                    allow[o:o+sz]=b'\x01'*sz
                    pos,_=decode_positions(payload);rawpos,_=decode_positions(orig[ho:ho+hs])
                    assert len(pos)==nv and len(rawpos)==nv
                    for (s,q),(rs,rq) in zip(pos,rawpos):
                        assert s==rs
                        world=tuple(s*q[j]+(centre[j] if kind=='comp' else 0) for j in range(3))
                        err=max(abs(world[j]-rs*rq[j]) for j in range(3))
                        assert err<=s/2+0.0002,(key,err,s)
                        maximum=max(maximum,err)
                    vertices.extend(rawpos)
                    count['packets']+=1;count['vertices']+=nv
            assert ni==len(native)
            # Check the restored positions against actual MOD vertices, not a bounding-box guess.
            pts=[tuple(map(float,line.split()[1:4])) for line in source[key+'.mod'].read_text().splitlines() if line.startswith('v\t') or line.startswith('v ')]
            assert pts,key
            failures=[];max_source_error=0
            for scale in set(s for s,q in vertices):
                bins=collections.defaultdict(list)
                for p in pts:bins[tuple(math.floor(x/scale) for x in p)].append(p)
                for q in set(q for s,q in vertices if s==scale):
                    p=tuple(x*scale for x in q);best=math.inf
                    for delta in itertools.product((-1,0,1),repeat=3):
                        for ref in bins.get(tuple(q[j]+delta[j] for j in range(3)),()):
                            best=min(best,max(abs(ref[j]-p[j]) for j in range(3)))
                    if best>scale+0.00002:failures.append((q,best))
                    else:max_source_error=max(max_source_error,best)
            assert not failures,(key,len(failures),failures[:3])
            if kind=='inst':
                iv=u(new,cd+24);seen=set()
                while iv:
                    assert iv not in seen;seen.add(iv);io=f(iv,92)
                    assert old[io+32:io+92]==new[io+32:io+92]
                    count['instance_matrices_and_bounds_unchanged']+=1;iv=u(new,io)
            count[kind+'_types']+=1;count['groups']+=ng
            max_world_error=max(max_world_error,maximum)
            models.append({'name':key,'kind':kind,'vertices':len(vertices),'world_reconstruction_error':maximum,'source_vertex_max_error':max_source_error})
    actual=[i for i,(a,b) in enumerate(zip(old,new)) if a!=b]
    outside=[i for i in actual if not allow[i]];assert not outside,outside[:20]
    report={'passed':True,'counts':dict(count),'actual_changed_bytes':len(actual),'changed_bytes_outside_vif':len(outside),'max_world_reconstruction_error':max_world_error,'sha256':hashlib.sha256(new).hexdigest(),'models':models}
    (W/'losangeles_la_v7_validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='models'},indent=2))
if __name__=='__main__':main()
