FUNCTIONS = (0x2F6C48, 0x2F82D0, 0x2F85D0, 0x2FD868, 0x3042C8)
NEEDLES = tuple(str(x) for x in (14352, 14356, 14360, 14364, 14368,
                                 14372, 14404, 14436, 14440, 14444,
                                 14448, 14452, 14456, 14460, 14464,
                                 14468, 14472))

for fn in FUNCTIONS:
    lines = str(ida_hexrays.decompile(fn)).splitlines()
    hits = [i for i, line in enumerate(lines) if any(n in line for n in NEEDLES)]
    if not hits:
        continue
    emit('%08X %s' % (fn, idc.get_func_name(fn)))
    shown = set()
    for hit in hits:
        for i in range(max(0, hit - 30), min(len(lines), hit + 36)):
            if i not in shown:
                emit('%5d  %s' % (i + 1, lines[i]))
                shown.add(i)
