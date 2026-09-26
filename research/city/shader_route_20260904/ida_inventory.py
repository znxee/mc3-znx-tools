import json
import os
import idaapi, idautils, idc, ida_funcs, ida_bytes, ida_auto, ida_pro
import ida_hexrays

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'research', 'city', 'shader_route_20260904')
ida_auto.auto_wait()
hex_ready = ida_hexrays.init_hexrays_plugin()
functions = []
for ea in idautils.Functions():
    f = ida_funcs.get_func(ea)
    functions.append({'address': ea, 'end': f.end_ea, 'name': idc.get_func_name(ea)})
with open(ROOT + '/functions.json', 'w') as stream:
    json.dump(functions, stream, indent=2)

targets = [0x2BA830, 0x2B7498, 0x2B8768, 0x42CCD0, 0x430BB8,
           0x2AF838, 0x595DE8, 0x2B9BD8, 0x526538, 0x2B7088]
results = []
for ea in targets:
    f = ida_funcs.get_func(ea)
    if not f:
        results.append({'address': ea, 'error': 'function not found'})
        continue
    name = idc.get_func_name(f.start_ea)
    try:
        pseudocode = str(ida_hexrays.decompile(f.start_ea)) if hex_ready else 'NO HEXRAYS'
    except Exception as exc:
        pseudocode = 'ERROR: ' + str(exc)
    assembly = ['%08X %s' % (a, idc.GetDisasm(a)) for a in idautils.FuncItems(f.start_ea)]
    record = {'address': f.start_ea, 'name': name, 'pseudocode': pseudocode,
              'assembly': assembly}
    results.append(record)
    with open(ROOT + '/initial_%08X.txt' % ea, 'w', encoding='utf-8') as stream:
        stream.write(name + '\n\n' + pseudocode + '\n\n' + '\n'.join(assembly))
with open(ROOT + '/initial.json', 'w') as stream:
    json.dump(results, stream, indent=2)
ida_pro.qexit(0)
