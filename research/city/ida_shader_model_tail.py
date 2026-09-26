TARGETS = (0x84, 0x88, 0x8C)
for fn_ea in idautils.Functions():
    name = idc.get_func_name(fn_ea)
    if 'drwShaderModel' not in name:
        continue
    hits = []
    for ea in idautils.FuncItems(fn_ea):
        for opn in range(4):
            if idc.get_operand_type(ea, opn) == 0:
                continue
            value = idc.get_operand_value(ea, opn) & 0xFFFFFFFF
            if value in TARGETS:
                hits.append((ea, value, idc.GetDisasm(ea)))
    if hits:
        emit('%08X %s' % (fn_ea, name))
        for ea, value, dis in hits:
            emit('  %08X  +%02X  %s' % (ea, value, dis))
