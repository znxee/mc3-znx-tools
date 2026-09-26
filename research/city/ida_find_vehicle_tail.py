TARGETS = [0x31C0, 0x3640, *range(0x3644, 0x36DC, 4), 0x36DC, 0x3720, 0x3810, 0x3814, 0x3818, 0x381C,
           0x3820, 0x3824, 0x3844, 0x3864, 0x3868, 0x386C, 0x3870,
           0x3874, 0x3878, 0x387C, 0x3880, 0x3884, 0x3888, 0x388C]

for fn_ea in idautils.Functions(0x2C0000, 0x310000):
    fn = ida_funcs.get_func(fn_ea)
    if not fn:
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
        emit('%08X %s' % (fn_ea, idc.get_func_name(fn_ea)))
        for ea, value, dis in hits:
            emit('  %08X  +%04X  %s' % (ea, value, dis))
