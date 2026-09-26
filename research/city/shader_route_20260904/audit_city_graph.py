"""Read-only structural audit of City shader groups and pf05 chunk pages.

The shader walk models City ResourcePageIn 0x597E28, not a scan for tokens.
Runtime relocation is simulated at a nonzero delta; inputs are never patched.
Only basic shader texture closures are interpreted here. Other types are
reported explicitly and must be checked with their own constructor layout.
"""
import os
import argparse
import collections
import hashlib
import json
import struct
from pathlib import Path

ROOT=Path(__file__).resolve().parent
TOOLS=Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools'))
u16=lambda b,o:struct.unpack_from('<H',b,o)[0]
u32=lambda b,o:struct.unpack_from('<I',b,o)[0]

def audit_city(path):
    d=path.read_bytes(); base=u32(d,0)
    def f(p,n=4):
        o=p-base+0x80
        if not 0x80<=o<=len(d)-n: raise ValueError('pointer outside PCK: %08X'%p)
        return o
    group_count=u32(d,0x88); group_va=u32(d,0x8c)
    if group_count>1024: raise ValueError('unreasonable group count')
    descriptors=[(g,f(group_va+16*g,16)) for g in range(group_count)]
    if u32(d,0x90): descriptors.append(('secondary',f(u32(d,0x90),16)))
    slots=[]; shader_owners=collections.defaultdict(list); texture_owners=collections.defaultdict(set)
    chunk_owners=collections.defaultdict(set); errors=[]; types=collections.Counter()
    textures=set(); texture_types=collections.Counter(); simulated_tex_fields={}; relocation_failures=[]
    delta=0x01000000-base
    for g,desc in descriptors:
        count=u16(d,desc+8); arr=f(u32(d,desc+4),count*4) if count else 0
        for k in range(count):
            ptr=u32(d,arr+4*k)
            if not ptr: continue # City dispatcher explicitly skips nulls.
            loc={'group':g,'slot':k,'shader_va':'%08X'%ptr}
            shader_owners[ptr].append(loc)
            try:
                so=f(ptr,12); typ=u32(d,so+4)&127
                types[typ]+=1
                if typ!=0: continue
                serialized_tex=u32(d,so+8)
                # Field is mutated by TextureFactory::ResourcePageIn each call.
                previous=simulated_tex_fields.get(so+8,serialized_tex)
                relocated=(previous+delta)&0xffffffff
                simulated_tex_fields[so+8]=relocated
                if not 0x01000000<=relocated<=0x01000000+len(d)-0x80-0xa0:
                    if len(relocation_failures)<12:
                        relocation_failures.append(dict(loc,field_file_offset=hex(so+8),
                            original_texture=hex(serialized_tex),value_after_ctor=hex(relocated)))
                to=f(serialized_tex,0xa0)
                texture_owners[serialized_tex].add(ptr)
                if serialized_tex in textures: continue
                textures.add(serialized_tex)
                texture_types[d[to+4]]+=1
                if d[to+4] in (1,2,3): continue
                if d[to+4]!=0: raise ValueError('invalid texture class: '+str(d[to+4]))
                mips=d[to+0x86]
                if not 1<=mips<=4: raise ValueError('mip count outside 1..4: '+str(mips))
                for level in range(mips):
                    ref=to+0x48+12*level
                    p,offset,word=struct.unpack_from('<III',d,ref)
                    file_id=word>>23
                    if file_id:
                        chunk_owners[(file_id,word&0x7fffff,offset)].add(ref)
                    elif p:
                        co=f(p,0x90)
                        size=u32(d,co+12)
                        if size<0x90 or co+size>len(d):
                            raise ValueError('resident chunk extent invalid at '+hex(co))
                    else:
                        raise ValueError('active mip has no resident chunk or pageFile')
            except (ValueError,IndexError,struct.error) as e:
                if len(errors)<40: errors.append(dict(loc,error=str(e)))
    repeated=[(p,ls) for p,ls in shader_owners.items() if len(ls)>1]
    return {'path':str(path),'sha256':hashlib.sha256(d).hexdigest(),'base':hex(base),
        'groups':len(descriptors),'non_null_slots':sum(map(len,shader_owners.values())),
        'unique_shaders':len(shader_owners),'types':dict(types),'unique_basic_textures':len(textures),
        'shaders_referenced_multiple_times':len(repeated),
        'extra_shader_visits':sum(len(ls)-1 for _,ls in repeated),
        'textures_shared_by_distinct_basic_shaders':sum(len(v)>1 for v in texture_owners.values()),
        'texture_types':dict(texture_types),'unique_stream_chunks':len(chunk_owners),
        'stream_chunks_with_multiple_reference_owners':sum(len(v)>1 for v in chunk_owners.values()),
        'first_duplicate_shaders':[{'shader':hex(p),'visits':len(ls),'first_visits':ls[:3]} for p,ls in repeated[:4]],
        'simulated_bad_relocations_examples':relocation_failures,'errors_examples':errors,
        'scope':'All City slots; texture closure only for type 0. Counts of owners are unique ref fields, not repeated visits.'}

def audit_ppf(path):
    data=path.read_bytes()
    if data[:4]!=b'pf05': raise ValueError('not pf05')
    count,stride=struct.unpack_from('<II',data,4)
    errors=[]; chunks=0; lengths=collections.Counter()
    if 12+4*count>0x3000: errors.append('header table exceeds mount read of 0x3000')
    for page in range(count):
        entry=u32(data,12+4*page)
        start=(entry&0x7ffff)*0x800; length=(entry>>20)*0x800
        lengths[length]+=1
        if length>stride or length<0x20 or start+length>len(data):
            errors.append({'page':page,'bad_extent':[start,length]}); continue
        at=0
        while at+0x20<=length:
            size=u32(data,start+at+12)
            if not size: break
            if size<0x20 or size%16 or at+size>length:
                errors.append({'page':page,'bad_chunk':[at,size]}); break
            chunks+=1;at+=size
        else: errors.append({'page':page,'missing_terminator':True})
    return {'path':str(path),'sha256':hashlib.sha256(data).hexdigest(),'pages':count,'stride':stride,
            'read_lengths':dict(lengths),'chunks':chunks,'errors':errors,
            'scope':'pf05 extents and chunk chain, not pixel identity or GS descriptors'}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--city',type=Path,action='append',default=[])
    ap.add_argument('--ppf',type=Path,action='append',default=[])
    ap.add_argument('--output',type=Path,default=ROOT/'city_graph_audit.json')
    a=ap.parse_args()
    results={'cities':[audit_city(p) for p in a.city],'ppf':[audit_ppf(p) for p in a.ppf]}
    a.output.write_text(json.dumps(results,indent=2),encoding='utf-8')
    for r in results['cities']:
        print(Path(r['path']).name, {k:r[k] for k in ('non_null_slots','unique_shaders','extra_shader_visits','unique_basic_textures','stream_chunks_with_multiple_reference_owners')},'errors',len(r['errors_examples']))
    for r in results['ppf']: print(Path(r['path']).name,'pages',r['pages'],'chunks',r['chunks'],'errors',len(r['errors']))

if __name__=='__main__': main()
