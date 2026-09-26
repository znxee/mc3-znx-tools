fn = 0x2FD868
lines = str(ida_hexrays.decompile(fn)).splitlines()
for needle in ('v285', 'v23 =', 'v295 =', 'DrawType'):
    emit('==== %s ====' % needle)
    for i, line in enumerate(lines):
        if needle in line:
            for j in range(max(0, i - 5), min(len(lines), i + 8)):
                emit('%5d  %s' % (j + 1, lines[j]))
