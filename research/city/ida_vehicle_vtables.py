vtables = [
    (0x0062E4C0, 'Vehicle root'),
    (0x00634A50, 'VehInput'),
    (0x006277F0, 'VehDamage'),
    (0x00627F40, 'VehModel'),
    (0x00627770, 'VehAudio'),
    (0x006278C8, 'VehSim'),
    (0x00627820, 'VehGyro'),
    (0x00627978, 'VehZone'),
    (0x006281B0, 'VehMods'),
    (0x00626D98, 'Mesh'),
]
for vt, hint in vtables:
    emit('\n%08X %s' % (vt, hint))
    seen = set()
    for x in idautils.XrefsTo(vt):
        f = ida_funcs.get_func(x.frm)
        start = f.start_ea if f else x.frm
        if (x.frm, start) in seen:
            continue
        seen.add((x.frm, start))
        emit('  xref %08X in %08X %-48s %s' % (
            x.frm, start, idc.get_func_name(start) or '?', idc.GetDisasm(x.frm)))
    for i in range(0, 6):
        p = idc.get_wide_dword(vt + 4*i)
        emit('  [%d] %08X %s' % (i, p, idc.get_func_name(p) or '?'))
