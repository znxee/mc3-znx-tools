fn = 0x2FD868
lines = str(ida_hexrays.decompile(fn)).splitlines()
for hit, line in enumerate(lines):
    if '14456' not in line and '14460' not in line and '14464' not in line and '14468' not in line:
        continue
    emit('%08X %s hit line %d' % (fn, idc.get_func_name(fn), hit + 1))
    for i in range(max(0, hit - 45), min(len(lines), hit + 55)):
        emit('%5d  %s' % (i + 1, lines[i]))
