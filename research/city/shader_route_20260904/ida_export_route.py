import json, os, re
import idaapi, idautils, idc, ida_funcs, ida_bytes, ida_auto, ida_pro, ida_hexrays

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'research', 'city', 'shader_route_20260904')
OUT = ROOT + '/decompiled'
os.makedirs(OUT, exist_ok=True)
ida_auto.auto_wait()
HEX = ida_hexrays.init_hexrays_plugin()
RANGES = [(0x2AE800, 0x2BB030), (0x42A700, 0x42D230),
          (0x521A00, 0x528100), (0x595DE8, 0x598300)]
EXTRA = [0x2A8980,0x2A8AF0,0x2A8A18,0x2B8768,0x2B8F30,0x2B7038,
         0x3B4800,0x3B34C0,0x430BB8,0x430EA8,0x4320E0,0x432260,
         0x3993A8,0x398AD0,0x398B68,0x1A9080]
VTRANGES = [(0x626E70,0x627480), (0x635AB8,0x635D80)]

# Some R5900 leaf/assembly functions are absent from IDA autoanalysis.
# Only create at explicit call/vtable targets in this private copy.
extra = set(EXTRA)
for lo, hi in VTRANGES:
    for at in range(lo,hi,4):
        target=idc.get_wide_dword(at)
        if any(a<=target<b for a,b in RANGES):
            extra.add(target)
created=[]
for ea in sorted(extra):
    if not ida_funcs.get_func(ea):
        if ida_funcs.add_func(ea):
            created.append(ea)
ida_auto.auto_wait()
starts=set()
for ea in idautils.Functions():
    if any(lo<=ea<hi for lo,hi in RANGES):
        starts.add(ea)
for ea in extra:
    f=ida_funcs.get_func(ea)
    if f: starts.add(f.start_ea)

aliases={}
for name in ['names.tsv','names_graph.tsv','names_token.tsv','names_manual.tsv']:
    path=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'symbols/')+name
    for line in open(path,encoding='utf-8',errors='replace'):
        fields=line.strip().split('\t')
        try: address=int(fields[0],16)
        except ValueError: continue
        if len(fields)>1: aliases.setdefault(address,[]).append(fields[1])

records=[]
for number,ea in enumerate(sorted(starts)):
    func=ida_funcs.get_func(ea)
    name=idc.get_func_name(ea)
    error=None
    try: code=str(ida_hexrays.decompile(ea)) if HEX else 'NO HEXRAYS'
    except Exception as exc: code='DECOMPILATION FAILED: '+str(exc); error=str(exc)
    items=list(idautils.FuncItems(ea))
    calls=[]
    for a in items:
        for target in idautils.CodeRefsFrom(a,0):
            tf=ida_funcs.get_func(target)
            if tf and tf.start_ea!=ea:
                calls.append({'from':a,'to':target,'function':tf.start_ea,'name':idc.get_func_name(tf.start_ea)})
    xrefs=[{'from':x.frm,'type':x.type,'function':idc.get_func_name(x.frm)} for x in idautils.XrefsTo(ea)]
    record={'address':ea,'end':func.end_ea,'name':name,'aliases':aliases.get(ea,[]),
            'error':error,'calls':calls,'xrefs':xrefs,'code_file':'%08X.c'%ea,'asm_file':'%08X.asm'%ea}
    records.append(record)
    with open(OUT+'/%08X.c'%ea,'w',encoding='utf-8') as stream:
        stream.write('// %08X %s\n// Candidate aliases: %s\n\n%s\n'%(ea,name,'; '.join(aliases.get(ea,[])),code))
    with open(OUT+'/%08X.asm'%ea,'w',encoding='utf-8') as stream:
        for a in items:
            stream.write('%08X %08X %s\n'%(a,idc.get_wide_dword(a),idc.GetDisasm(a)))
    with open(ROOT+'/progress.txt','w') as stream:
        stream.write('%d/%d %08X\n'%(number+1,len(starts),ea))

vtables={}
for lo,hi in VTRANGES:
    vtables['%08X'%lo]=[{'address':a,'target':idc.get_wide_dword(a),
                         'name':idc.get_func_name(idc.get_wide_dword(a))} for a in range(lo,hi,4)]
raw={}
for lo,hi in [(0x615D00,0x616020),(0x618C00,0x619000),(0x626D00,0x627500),
              (0x635A00,0x635E00),(0x61C200,0x61C400)]:
    raw['%08X'%lo]=ida_bytes.get_bytes(lo,hi-lo).hex()
with open(ROOT+'/route_index.json','w') as stream:
    json.dump({'input':idaapi.get_input_file_path(),'hexrays':HEX,'created_functions':created,
               'ranges':RANGES,'functions':records,'vtables':vtables,'data':raw},stream,indent=2)
ida_pro.qexit(0)
